## Context

评测报告目前只有逐题分数、最终 contexts（`source/score/kind`）、工具名/参数/迭代与错误摘要。用它无法回答“事实在哪一步丢了”：

- `retrieval_details` 没有 `chunk_id`，也没有 rerank 前后的候选集；
- `trajectory` 没有工具结果数/耗时，也没有检索阶段快照；
- 题集只有 `reference_contexts` 文本，没有 gold chunk 归属；
- 裁判只有 `error` 摘要，没有 reason/verdict。

只落证据仍然需要人/Agent 读。本 change 在证据包之上加一层**确定性归因摘要**：按指标输出 `root_cause` + `confidence` + `evidence`，证据不足时写 `inconclusive`。它不调用 LLM、不改评分口径、不做跨题因果推断。

## Goals / Non-Goals

**Goals:**

- `--evidence` 开启后，为低分/NaN/失败/工具或行为异常的题生成逐题证据 JSON。
- 证据覆盖：题集出处、gold chunk、query 路径、dense/lexical/RRF 候选、rerank 前后、最终 contexts、工具调用、answer、裁判 score/error/reason、行为分/reason。
- 证据带 **trace 关联**：`run_trace_id` / `sut_trace_id` / `judge_measures` / `judge_calls` / 行为轴 `trace_id` 映射；ID 来自 `eval-trace-correlation`，本 change 只消费。
- 产出**逐指标**的确定性归因：`root_cause` / `confidence` / `evidence`；无法判定写 `inconclusive`。
- `summary.json` 给出根因分布（按指标与总计），回答“这一轮整体最该修哪一层”。
- 证据采集/归因失败不影响评测主流程、主分、门禁、CSV、DB、Langfuse。
- 默认关闭时零额外开销；证据只落本地 gitignored 目录。

**Non-Goals:**

- 不调用 LLM 做根因判断；归因只基于确定性匹配与规则。
- 不做跨题/全局因果推断；只回答“这一题、这个指标，最可能丢在哪一步”。
- 不改评分口径、指标实现、门禁、报告结构、DB schema。
- 不扩测试集、不生成新题、不改出题器；`source_chunk_ids` 可选，缺失时用探针。
- 不把证据上传 Langfuse、不新增 API。
- 不展开子代理内部轨迹（顶层工具调用之外不可观测的部分记 null/unknown）。
- 不生成 trace ID：`run_trace_id` / `trace_id` / `judge_call_id` 由 `eval-trace-correlation` 提供；本 change 只读取并写入证据。

## Decisions

### D1：opt-in + 非评分，默认零开销

- 新增 CLI `--evidence`；`--evidence-all`（可选）写全部题，否则只写低分题。
- 采集器/归因器在关闭时 no-op；检索/评分路径只多一次 `None` 判断。
- 证据写入与归因包在 `try/except` 内：失败只 `warning`，不得改变退出码、分数、CSV/DB。
- 不把证据放进 `detail_json`（避免 DB 膨胀与隐私面扩大）。

### D2：用 ContextVar 采集器，不穿透业务签名

新增 `src/core/eval_evidence.py`：

- `EvidenceCollector`：每轮一个实例，记录 query/retrieval/rerank/tool/context/judge/behavior 快照。
- `current_evidence: ContextVar[EvidenceCollector | None]`；默认 `None`。
- 检索/工具/评分层通过 `current_evidence.get()` 取收集器；没有就跳过。
- 不改 `retrieve_kb` / `search` / `rerank_results` 的公共签名，也不影响生产链路。

理由：证据只服务评测 CLI；用 ContextVar 与现有 `RequestContext`/`current_trace_id` 的传播方式一致。

### D3：检索链记录“三张快照 + 去重 + 最终上下文”

在 `retrieval.search` / `rerank_results` / `retrieve_kb` 记录：

1. **dense**：`[{"chunk_id","doc_id","source","page","score"}]`
2. **lexical**：同上
3. **RRF**：融合后的 `chunk_id + rrf_score + rank`
4. **rerank 前**：每条 `chunk_id + rank_before + 原始 score`
5. **rerank 后**：`chunk_id + rank_after + rerank_score`
6. **dedup**：`dropped_chunk_ids`、`kept_chunk_ids`
7. **top-k 截断**：`rank_before_topk`、`rank_after_topk`
8. **最终 contexts**：`chunk_id + source + page + score + order_in_prompt`

每阶段记录 `count`、`failed`、`truncated`；dense/lexical 单路失败必须可见。候选列表上限默认 50 条/阶段。

### D4：Query 路径记录“原始 → 实际工具 query → 可用的改写”

- `original_query`：题集 `user_input`。
- `tool_query`：`trajectory.tool_calls[].args.query`（现有数据即可）。
- `rewritten_query` / `sub_queries`：如果评测路径实际执行了改写，记录其输出；没有就 `null`，不伪造。
- `intent_route` / `history_len`：有则记，无则 null。

### D5：Gold chunk 与 Chunk 探针

**gold_chunk_ids 来源优先级**：

1. 题集可选字段 `source_chunk_ids` → `gold_chunk_source="testset"`，置信度高；
2. 否则用 chunk 探针匹配 `reference` 的数值/文本片段 → `gold_chunk_source="probe"`，置信度中；
3. 都不可用 → `gold_chunk_ids=null`，`gold_chunk_source=null`。

**探针**：

1. 取 `reference` 中的数值 token（含千分位/百分号/单位附近的数字）与归一化文本片段（前 40 个有效字符）。
2. 从 `VectorStore.get_all_chunks(kb_id)` 一次性取回全量分块（本次 run 只读一次）。
3. 在 `content` 与 `parent_content` 中做归一化子串/数值匹配，记录命中的 `doc_id/chunk_id/source/page`。
4. 输出：
   - `matched_chunks`：命中 chunk 列表（上限 20）；
   - `fact_in_corpus`：`true` / `false` / `null`；
   - `probe_tokens`：实际用的数值/文本 token。

### D6：裁判与行为证据只记现有可观测值

- 每个指标：`score`、`error`、`reason`；DeepEval 指标实例暴露 `reason` 就记，没有就 null。
- `reason` 只在 measure 正常返回后读取；异常分支不得读 `metric.reason`（指标实例跨题复用，可能是上一题残留）。
- 不解析/持久化 `verbose_logs` 全文；需要时用 `trace_id` 查 Langfuse/日志。
- 行为轴：从 `behaviour_rows` 记 `metrics` 与 `reasons`；`failed` 题记 `score=null` / `reason=failed`。
- 不把裁判 raw prompt 或完整 raw output 落盘；只记错误摘要与 reason。

### D6.1：Trace 关联只消费，不生成

- 证据 `trace` 段从 `eval-trace-correlation` 读取，分两级：
  - `run_trace_id`（整轮）；
  - `sut_trace_id`（该题 `trace_id`）；
  - `judge_measures[]`：`judge_measure_id` / metric / attempt / model / latency_ms / request_count / response_count / statuses / tokens_in / tokens_out / reason / error；
  - `judge_calls[]`：`judge_call_id` / `call_index` / status / finish_reason / tokens_in / tokens_out / latency_ms / json_ok；SHALL 通过 `judge_measure_id` 归到对应 measure；
  - 行为轴映射（`trace_id` + `link_method`）。
- `JUDGE_CALL` 日志只记 `reason_len` / `reason_hash`；证据文件 MAY 记录完整 `reason`（仅本地 gitignored），由 measure 结果回填。
- 取不到时写 null；SHALL NOT 在本 change 里重新生成/猜测 trace ID。
- 行为轴 judge 的内部调用不在 v1 范围：证据只记行为分与 `trace_id` 映射，不伪造缺失的 judge call。
- 证据写入 SHALL NOT 改变 trace ID 的生成/日志行为；trace change 关闭/失败时，证据仍可落盘，trace 字段为 null。

### D7：低分选择与落盘

低分判定（满足任一）：

- 四主分任一 `< GATE_THRESHOLDS[...]` 或 NaN；
- `tool_correctness` 有标签且 `< 1.0`，或 `tool_correctness_reason` 是 `failed` / `metric_unavailable` / `tool_correctness:*`；
- `sample_class=empty` 或 `failed`；
- 行为轴任一分 `< 0.5` 或缺失。

`--evidence` 只写这些题；`--evidence-all` 写全部。落盘：

```
data/deepeval/evidence/<run_id>/
  summary.json
  q_<q_index>.json
```

`run_id` 用报告时间戳（与 CSV/MD 同源）。`summary.json` 记录：run_id、题数、低分题号、各题证据路径、采集耗时、`root_cause_counts`、`inconclusive_count`。写入用临时文件 + `os.replace` 原子落盘。

### D8：根因枚举（固定，不自由文本）

| 枚举 | 含义 |
|---|---|
| `judge_failure` | 裁判失败/NaN/JSON 解析失败 |
| `testset_error` | 参考答案期间/数值/单位/口径错误 |
| `parsing_or_chunking` | 语料里没有该事实：解析/分块/文档缺失 |
| `retrieval_miss` | chunk 存在，但没进 dense/lexical/RRF 候选 |
| `rerank_or_topk` | 进了候选，但被 rerank/dedup/top-k 挤掉 |
| `prompt_or_generation` | 进了最终上下文，但回答没用/用错/跑题/给建议 |
| `tool_routing` | 该调的工具没调，或调错工具 |
| `agent_orchestration` | 重复检索、打转、冗余步骤 |
| `inconclusive` | 证据不足（抽象题/匹配不到事实） |

`root_cause` 按**指标**输出，不强行一题一个；同一题不同指标可以有不同根因。

### D9：归因规则（metric-scoped）

先算事实标记：

- `fact_in_corpus`：gold chunk 在探针/题集中存在；
- `fact_in_candidates`：gold chunk 与 dense/lexical/RRF 候选有交集；
- `fact_in_reranked`：gold chunk 与 rerank 后（dedup 前）有交集；
- `fact_in_final`：gold chunk 与最终 contexts 有交集；
- `answer_mentions_fact`：answer 中出现 gold 的数值/实体 token；无 token 时 null；
- `judge_failed`：score 为 NaN 或 `error` 非空；
- `testset_conflict`：`period_conflict` / `numeric_conflict` / `unit_missing` 任一为 true；
- `tool_called` / `call_count`：顶层轨迹。

规则（按顺序，首个命中即停）：

| 指标 | 规则 |
|---|---|
| `faithfulness` | judge_failed → `judge_failure`；`fact_in_final=false` → 事实阶段根因；`fact_in_final=true` 且 `answer_mentions_fact=false` → `prompt_or_generation`；否则 `inconclusive` |
| `answer_relevancy` | judge_failed → `judge_failure`；answer 为空/failed → `prompt_or_generation`；否则 `inconclusive` |
| `context_precision` | judge_failed → `judge_failure`；gold 不在 candidates → `retrieval_miss`；gold 不在 final → `rerank_or_topk`；gold 在 final 但分数低 → `rerank_or_topk`（排序/噪声）；否则 `inconclusive` |
| `context_recall` | judge_failed → `judge_failure`；testset_conflict → `testset_error`；`fact_in_corpus=false` → `parsing_or_chunking`；`fact_in_final=false` → 事实阶段根因；否则 `inconclusive` |
| `contextual_relevancy` | judge_failed → `judge_failure`；gold 在 candidates 但不在 final → `rerank_or_topk`；否则 `retrieval_miss`；否则 `inconclusive` |
| `non_advice` | judge_failed → `judge_failure`；低分 → `prompt_or_generation`（合规建议） |
| `tool_correctness` | failed → `tool_routing`；只有 delegate_task → `inconclusive`；期望工具缺失 → `tool_routing` |
| `TaskCompletion` | failed/空回答 → `prompt_or_generation`；低分 → `inconclusive` |
| `StepEfficiency` | 低分 → `agent_orchestration` |
| `AgentLoopDetection` | 低分 → `agent_orchestration` |

**事实阶段根因**（供 F/CR 使用）：

```
!fact_in_corpus      → parsing_or_chunking
!fact_in_candidates  → retrieval_miss
!fact_in_reranked    → rerank_or_topk
!fact_in_final       → rerank_or_topk（dedup/top-k）
否则                  → prompt_or_generation / inconclusive
```

### D10：置信度规则

| 置信度 | 条件 |
|---|---|
| `high` | judge_failed 明确；或 gold chunk 来自 testset 且阶段交集明确；或 testset 数值/期间硬冲突 |
| `medium` | gold 来自探针文本匹配；或事实阶段只能推断到层；或 testset 单位缺失 |
| `low` | 只有间接证据（如 answer 未出现 token 但 reference 是抽象表述） |
| `inconclusive` | 无规则命中，或关键事实标记为 null |

不把 `low` 自动升级为 `high`；`inconclusive` 是合法结果。

### D11：summary 根因分布

`summary.json` 至少包含：

```json
{
  "schema_version": 1,
  "run_id": "20261018_041404",
  "qa_count": 22,
  "low_score_questions": [3, 15, 16, 19, 20, 21, 22],
  "root_cause_counts": {
    "by_metric": {
      "context_recall": {"testset_error": 2, "retrieval_miss": 1},
      "context_precision": {"rerank_or_topk": 2, "inconclusive": 1},
      "faithfulness": {"judge_failure": 1, "prompt_or_generation": 1}
    },
    "total": {
      "testset_error": 2,
      "retrieval_miss": 1,
      "rerank_or_topk": 2,
      "judge_failure": 1,
      "prompt_or_generation": 1,
      "agent_orchestration": 1,
      "inconclusive": 0
    }
  },
  "inconclusive_count": 0
}
```

### D12：隐私、原子写与保留

- 证据包含完整 answer / contexts / 题集 reference；目录必须加入 `.gitignore`。
- 不上传 Langfuse、不写 DB、不暴露 API。
- 每题证据用临时文件 + `os.replace`；`summary.json` 同。
- 保留策略：手动清理；README/排障手册提示“含原文，勿提交/外发”。
- 证据文件名不含用户输入，只含 `q_index`。

### D13：schema 版本与向后兼容

- 证据 JSON 顶层 `schema_version: 1`。
- 字段缺失统一写 `null`，不省略键；`attribution` 也是全量键。
- 未来加字段只增不改；删除/改名必须升 `schema_version`。
- 证据/归因不依赖某个指标实现（RAGAS/DeepEval 都可）；取不到的值写 null + `inconclusive`。

### D14：测试策略

- 单测：schema、低分选择、no-op 关闭路径、写入失败不影响主流程、chunk 探针命中/未命中、候选截断。
- 归因单测：
  - Q21 类：gold 在 corpus、不在 candidates → `retrieval_miss`；
  - Q20 类：gold 在 candidates、不在 final → `rerank_or_topk`；
  - Q19 类：参考数值与语料冲突 → `testset_error`；
  - Q16/Q22 类：error/NaN → `judge_failure`；
  - 抽象题无 token → `inconclusive`；
  - `source_chunk_ids` 存在时优先于探针。
- 集成：固定 fake retrieval/scorer 造“候选有但 rerank 后没有”，跑 `--evidence`，断言 `attribution.context_precision.root_cause=="rerank_or_topk"`。
- CLI：`--evidence` 与 `--deepeval-only` / `--gate` 组合不改变退出码；`--evidence-all` 写全部。
- 性能：关闭时无额外 DB/文件 IO；开启时全量 chunk 只读一次。

## Risks / Trade-offs

- **归因会错**：抽象题/改写题的事实无法用 token 匹配 → `inconclusive`，不强行给标签；`low` 置信度必须保留。
- **证据体积**：候选集/上下文可能很大 → 每阶段上限 50、最终 contexts 全量、chunk 命中上限 20；截断记 `truncated`。
- **隐私**：证据含原文 → gitignore、仅本地、文档提示；不写 DB/Langfuse。
- **性能开销**：开启时多一次全量 chunk 读取 + 文件写；只用于调试，默认关闭。
- **query rewrite 不可见**：评测路径可能不经 `QueryRouter`；取不到就写 null，不用工具 query 冒充改写结果。
- **候选集重建不确定性**：必须运行期采集；事后重跑可能因 embedding/rerank 波动不同。证据文件记录 `captured_at` 与配置快照。
- **子代理盲区**：`delegate_task` 只记顶层；子代理内部工具调用记 null/unknown，归因 `inconclusive`。
- **gold chunk 缺失**：题集没有 `source_chunk_ids` 时用探针，置信度只到 medium；要 high 需题集侧补字段。
- **schema 漂移**：用 `schema_version` 与字段全量 null 约束。

## Migration Plan

1. 先合并 `tool-correctness-diagnostic`，让 `tool_correctness` / `trajectory` / `expected_tools` 链路可用。
2. 新增证据模块、CLI 开关与归因器；默认关闭，无需迁移。
3. 新增 `data/deepeval/evidence/` 到 `.gitignore`；历史报告不受影响。
4. 回滚：删除开关/采集器/归因器即可；已落盘证据可手动清理。

## Open Questions

- 低分阈值是否要包含 `contextual_relevancy`？默认不包含（它是诊断，低分常态）；如需纳入，用常量扩展，不改 schema。
- 是否要把 `source_chunk_ids` 写进题集？本 change 先读可选字段、缺失时用探针；题集 schema 的强制化留给出题侧 change。
- 证据保留期？暂定手动清理；若未来自动化，再定天数与清理命令。
