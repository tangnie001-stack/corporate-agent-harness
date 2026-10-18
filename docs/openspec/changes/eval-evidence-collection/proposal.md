## Why

当前评测只能给出**聚合分数**：`faithfulness` / `context_recall` / `context_precision` 低分时，无法从现有产物判断事实是在**解析/分块、query rewrite、向量召回、lexical、RRF、rerank、dedup、top-k、prompt 组装、生成、工具路由还是裁判**哪一步丢的。

现有证据缺口：

- `detail_json.retrieval_details` 只有 `source / score / kind`，**没有 chunk_id、没有候选集、没有 rerank 前后快照**；
- `trajectory` 只有工具名 / 参数 / 迭代数，**没有结果数、耗时、检索阶段快照**；
- 题集有 `reference_contexts` 文本与 `source_file`，**没有 gold chunk 归属**（v6 全量无 `source_chunk_ids`）；
- 裁判只落 `error` 摘要，**没有 reason / 逐条 verdict**；
- `trace_id` 能串起“哪次请求、哪次裁判调用”，但**不告诉你事实在哪一步丢了**。

结果就是：Q21 这种“参考答案的 80,348 在题集 reference_contexts 里有、实际检索 contexts 没有”的题，只能推断是检索/排序；Q15/Q19 这种题集错误也会和检索漏召混在一起。

本 change 在 trace 关联之上补一层**运行期证据 + 确定性归因**：

- 证据回答“事实在不在语料、在不在候选、在不在 rerank 后、在不在最终上下文、回答有没有用”；
- 归因把这些事实映射成固定的 `root_cause` + `confidence` + `evidence`；
- 无法判定时明确写 `inconclusive`，不调用 LLM、不改评分。

## What Changes

- 新增 `eval_deepeval --evidence`（可选 `--evidence-all`）调试开关：默认关闭，关闭时零额外开销。
- **采集架构**：每轮一个 `EvidenceRun`，内含按 `q_index` 的 `QuestionEvidence`；生成阶段每题 set/reset `current_evidence`，评分阶段把 `HookEvidence.measures` 归到同一题；评分结束后再决定写哪些题。
- **全量分块复用**：`main()` 空库检查已经读了一次 `get_all_chunks`；开启 evidence 时把结果交给 `EvidenceRun`，探针不再二次读库。
- **题集样本单独加载**：evidence 模式额外调用 `load_testset_samples` 并按 `--limit` 切片，拿到 `reference_contexts` / `source_file` / 可选 `source_chunk_ids` / `match_scope` / `chunking_version`；默认评测路径的三元组加载不动。
- 证据包覆盖：
  - **题集**：`reference` / `reference_contexts` / `source_file` / `expected_tools` / 可选 `source_chunk_ids` / `match_scope` / `chunking_version` / `numeric_risk`；
  - **query 路径**：`original` / `tool_queries`（实际 `retrieve_kb.args.query`）/ `rewritten` / `sub_queries` / `intent_route`；评测路径不执行改写时写 null；
  - **检索链**：dense / lexical / RRF / rerank 前 / rerank 后 / dedup / top-k / final contexts；每条候选带 `chunk_id`、`parent_key`、`doc_id`、`source`、`page`、`rank`、`score`、`score_kind`；
  - **工具链**：工具名、参数、结果数、耗时、迭代、`call_index`；`delegate_task` 只记顶层；
  - **生成/裁判**：answer、每个指标的 score / error / reason；行为轴分数与 reason；
  - **trace 关联**：`run_trace_id` / `sut_trace_id` / `judge_measures[]` / 嵌套 `judge_calls[]` / 行为轴 `trace_id` + `link_method`；这些 ID 由 `eval-trace-correlation` 提供，本 change 只消费。
- **确定性归因**：
  - 每题按指标输出 `attribution`：`root_cause` / `confidence` / `evidence`；
  - 根因枚举固定：`judge_failure` / `testset_error` / `parsing_or_chunking` / `retrieval_miss` / `rerank_or_topk` / `prompt_or_generation` / `tool_routing` / `agent_orchestration` / `inconclusive`；
  - `summary.json` 输出 `root_cause_counts.by_metric` / `total`、`scored_metric_count` / `skipped_metric_count` / `inconclusive_count`；
  - 空样本/失败样本未调用指标 → `metric_attempted=false`，归 `inconclusive`，不归 `judge_failure`；
  - **faithfulness v1 不接入 gold 事实链**：只判 `judge_failure` / 空回答，其余 `inconclusive`（F 的幻觉诊断需要 claim 级证据）；
  - web 样本的 `judge_failed` 优先为 `judge_failure`；裁判成功但低分 → `inconclusive`；web fallback 的 `tool_correctness` 不判 `tool_routing`；
  - 规则只使用本次运行期证据，不跨题推断、不调用 LLM。
- **v1 明确边界**：
  - `period_conflict` 只记 null；不在 v1 做“期间/季度是否一致”的语义判断（避免把题集期间错标误判成检索问题）；
  - `numeric_conflict` 只做“参考答案数值在语料中是否存在”的硬匹配，置信度最多 medium；不做“42% vs 53% 谁对”的口径判断；`unit_missing` 只做信号；
  - 多值参考答案在 `fact_coverage < 1.0` 时只给整体 `inconclusive`，不做逐 fact 定位；
  - 子代理内部轨迹不展开；`delegate_task` 只记顶层，归因 `inconclusive`；
  - 不扩测试集、不生成新题、不改出题器。
- **低分选择**：
  - `--evidence` 只写低分/NaN/失败/工具未完全命中/行为分低于 0.5 的题；
  - `--evidence-all` 写全部；
  - `--deepeval-only` 下没有主分，按行为分 < 0.5 或缺失选，不假装有主分筛选。
- **落盘顺序**：证据写入必须在 `--gate` 之前；否则门禁 `sys.exit` 会吞掉证据。
- 证据只落本地 `data/deepeval/evidence/<run_id>/`（gitignored），不新增 API、不进 CSV / DB / Langfuse。
- 证据采集/归因/写入失败只记 warning，SHALL NOT 影响评测主流程、主分、门禁或退出码。

## Capabilities

### New Capabilities

- `eval-evidence`：评测逐题证据采集 + 确定性根因归因（opt-in、非评分）。

### Modified Capabilities

（无）

> 实施前置：`eval-trace-correlation`（提供 `run_trace_id` / `trace_id` / `judge_measure_id` / `judge_call_id` / `HookEvidence.measures` / `link_behavior_traces`）与 `tool-correctness-diagnostic`（提供 `tool_correctness` / `trajectory` / `expected_tools` 链路）。本 change 只消费 trace ID，不重复生成。

## Impact

- **代码**：
  - `src/infra/db/vector_store/types.py`（`ChunkResult.rrf_score`）；
  - `src/rag/fusion.py`（RRF 分数回填）；
  - `src/rag/retrieval.py`（dense/lexical/RRF/rerank/dedup/top-k 证据）；
  - `src/agents/tools/rag_tools.py`（工具调用与最终 contexts 证据）；
  - `src/agents/skills/executor.py`（子代理执行时清空 `current_evidence`，父题不采集子代理检索）；
  - `src/core/eval_evidence.py`（`EvidenceRun` / `QuestionEvidence` / `current_evidence`，不 import rag）；
  - `src/cli/eval_evidence_report.py`（探针 / 归因 / 原子写）；
  - `src/cli/eval_deepeval.py`（CLI 开关、编排、评分后 drain、门禁前写盘）；
  - `src/cli/testset_io.py`（不动；evidence 模式单独 `load_testset_samples`）。
- **测试**：证据 schema、低分选择、no-op、失败降级、RRF 分数、dedup/top-k 丢弃 ID、chunk 探针、parent/child 等价、归因规则、summary 计数、CLI 组合、写盘顺序。
- **数据**：`data/deepeval/evidence/`（新增、gitignored）；不改 `eval_report` / CSV / Markdown 结构；不扩测试集。
- **文档**：`src/cli/README.md`、`docs/agents/eval-metrics-troubleshooting.md`、`docs/agents/api_contract.md`（只登记证据路径/字段与归因枚举，不改评分契约）。
- **依赖/API**：无新增依赖，无 API 变更。
