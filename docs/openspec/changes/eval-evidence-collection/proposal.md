## Why

当前评测只能给出**聚合分数**：`faithfulness` / `context_recall` / `context_precision` 低分时，无法从现有产物判断事实是在**解析/分块、query rewrite、向量召回、rerank、prompt 组装、生成、工具路由还是裁判**哪一步丢的。

现有证据缺口：

- `detail_json.retrieval_details` 只有 `source / score / kind`，**没有 chunk_id、没有 rerank 前后的候选集**；
- `trajectory` 只有工具名 / 参数 / 迭代数，**没有结果数、耗时、检索阶段快照**；
- 题集有 `reference_contexts` 文本与 `source_file`，**没有 gold chunk 归属**；
- 裁判只落 `error` 摘要，**没有 reason / 逐条 verdict**。

结果就是：Q21 这种“参考答案的 80,348 在题集 reference_contexts 里有、实际检索 contexts 没有”的题，只能推断是检索/排序，**无法区分 query rewrite、embedding、lexical、RRF、rerank、top-k 哪一段**；Q15/Q19 这种题集错误也会和检索漏召混在一起。

**只落证据还不够**：证据包仍然要人/Agent 读。本 change 在证据包之上加一层**确定性归因摘要**：按指标输出 `root_cause`、`confidence`、`evidence`，无法判定时明确写 `inconclusive`；不调用 LLM、不改评分口径。

## What Changes

- 新增 `eval_deepeval --evidence`（可选 `--evidence-all`）调试开关：默认关闭，关闭时零额外开销。
- 逐题采集并落盘证据 JSON，默认只写**低分/NaN/失败/工具未完全命中/行为分低于阈值**的题；`--evidence-all` 写全部。
- 证据包覆盖：
  - **题集**：`reference` / `reference_contexts` / `source_file` / `expected_tools` / 可选的 `source_chunk_ids`；
  - **query 路径**：原始问题、实际 `retrieve_kb.args.query`、可用的 rewritten / sub_queries；
  - **检索链**：dense / lexical / RRF 候选（`chunk_id` + score）、rerank 前后（chunk_id / rank / score）、最终 contexts（chunk_id / source / page / score / order）；
  - **chunk 探针**：在 KB 全量分块中定位参考答案的数值/文本片段，产出 `gold_chunk_ids`、`fact_in_corpus` 与命中位置；
  - **工具链**：工具名、参数、结果数、迭代；`delegate_task` 只记顶层，不展开子代理；
  - **生成/裁判**：最终 answer、每个指标的 score / error / reason（有 verdict 时一并记录）、行为轴分数与 reason；
  - **trace 关联**：`run_trace_id` / `sut_trace_id` / `judge_measures[]`（measure 级汇总）/ `judge_calls[]`（每次 HTTP 调用）/ 行为轴 `trace_id` 映射；这些 ID 由 `eval-trace-correlation` 提供，本 change 只消费、不生成。行为轴 judge 的内部调用不在 v1 范围内，证据只记行为分与 `trace_id` 映射。
- **确定性归因摘要**：
  - 每题按指标输出 `attribution`：`root_cause` / `confidence` / `evidence`；
  - 根因枚举固定：`judge_failure` / `testset_error` / `parsing_or_chunking` / `retrieval_miss` / `rerank_or_topk` / `prompt_or_generation` / `tool_routing` / `agent_orchestration` / `inconclusive`；
  - 规则基于 `gold_chunk_ids`、`fact_in_corpus`、`fact_in_candidates`、`fact_in_reranked`、`fact_in_final`、`answer_mentions_fact`、`testset_conflict`、`judge_failed`、`tool_called`；
  - `summary.json` 输出 `root_cause_counts`（按指标与总计）与 `inconclusive_count`；
  - 不调用 LLM；证据不足时写 `inconclusive`，不强行贴标签。
- `gold_chunk_ids` 优先取题集可选字段 `source_chunk_ids`；缺失时由 chunk 探针生成，并记 `gold_chunk_source`（`testset` / `probe` / `null`）与 `gold_chunk_confidence`。
- 证据只落本地 `data/deepeval/evidence/<run_id>/`（gitignored），不新增 API、不进 CSV / DB / Langfuse。
- 证据采集/归因失败只记 warning，SHALL NOT 影响评测主流程、主分或门禁。

## Capabilities

### New Capabilities

- `eval-evidence`：评测逐题证据采集 + 确定性根因归因（opt-in、非评分）。

### Modified Capabilities

（无）

> 实施前置：`eval-trace-correlation`（提供 `run_trace_id` / `trace_id` / `judge_call_id`）与 `tool-correctness-diagnostic`（提供 `tool_correctness` / `trajectory` / `expected_tools` 链路）。本 change 只消费 trace ID，不重复生成。

## Impact

- **代码**：`src/cli/eval_deepeval.py`（CLI 开关、编排、归因、落盘）、`src/rag/retrieval.py`（dense/lexical/RRF/rerank 证据）、`src/agents/tools/rag_tools.py`（retrieve_kb 证据）、`src/core/eval_evidence.py`（采集器 + 归因器）。
- **测试**：证据 schema、低分选择、非评分副作用、失败降级、chunk 探针、gold chunk 匹配、归因规则、summary 计数、CLI 开关。
- **数据**：`data/deepeval/evidence/`（新增、gitignored）；不改 `eval_report` / CSV / Markdown 结构；不扩测试集。
- **文档**：`src/cli/README.md`、`docs/agents/eval-metrics-troubleshooting.md`、`docs/agents/api_contract.md`（只登记证据路径/字段与归因枚举，不改评分契约）。
- **依赖/API**：无新增依赖，无 API 变更。
