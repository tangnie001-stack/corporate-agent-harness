## Context

评测报告目前只有逐题分数、最终 contexts（`source/score/kind`）、工具名/参数/迭代与错误摘要。用它无法回答“事实在哪一步丢了”：

- `retrieval_details` 没有 `chunk_id`，也没有 dense/lexical/RRF 候选与 rerank 前后快照；
- `trajectory` 没有工具结果数/耗时，也没有检索阶段快照；
- 题集只有 `reference_contexts` 文本，没有 gold chunk 归属（v6 全量无 `source_chunk_ids`）；
- 裁判只有 `error` 摘要，没有 reason/逐条 verdict；
- `trace_id` 能串起请求与裁判调用，但不能说明事实在哪一层丢失。

只落证据仍然需要人/Agent 读。本 change 在证据包之上加一层**确定性归因摘要**：按指标输出 `root_cause` + `confidence` + `evidence`，证据不足时写 `inconclusive`。它不调用 LLM、不改评分口径、不做跨题因果推断。

## Goals / Non-Goals

**Goals:**

- `--evidence` 开启后，为低分/NaN/失败/工具或行为异常的题生成逐题证据 JSON。
- 证据覆盖：题集出处、gold chunk、query 路径、dense/lexical/RRF 候选、rerank 前后、dedup、top-k、最终 contexts、工具调用、answer、裁判 score/error/reason、行为分/reason。
- 证据带 **trace 关联**：`run_trace_id` / `sut_trace_id` / `judge_measures` / `judge_calls` / 行为轴 `trace_id` 映射；ID 来自 `eval-trace-correlation`，本 change 只消费。
- 产出**逐指标**的确定性归因：`root_cause` / `confidence` / `evidence`；无法判定写 `inconclusive`。
- `summary.json` 给出根因分布（按指标与总计），回答“这一轮整体最该修哪一层”。
- 证据采集/归因失败不影响评测主流程、主分、门禁、CSV、DB、Langfuse。
- 默认关闭时零额外开销；证据只落本地 gitignored 目录。

**Non-Goals:**

- 不调用 LLM 做根因判断；归因只基于确定性匹配与规则。
- 不做跨题/全局因果推断；只回答“这一题、这个指标，最可能丢在哪一步”。
- 不做“参考答案与语料谁对”的语义判断；`numeric_conflict` 只做数值存在性硬匹配，`period_conflict` v1 恒为 null。
- 不改评分口径、指标实现、门禁、报告结构、DB schema。
- 不扩测试集、不生成新题、不改出题器；`source_chunk_ids` 可选，缺失时用探针。
- 不把证据上传 Langfuse、不新增 API。
- 不展开子代理内部轨迹（顶层工具调用之外不可观测的部分记 null/unknown）。
- 不生成 trace ID：`run_trace_id` / `trace_id` / `judge_measure_id` / `judge_call_id` 由 `eval-trace-correlation` 提供；本 change 只读取并写入证据。

## Decisions

### D1：opt-in + 非评分，默认零开销

- 新增 CLI `--evidence`；`--evidence-all` 写全部题，否则只写低分题。
- 采集器/归因器在关闭时 no-op；检索/评分路径只多一次 `current_evidence.get() is None` 判断。
- 证据写入与归因包在 `try/except` 内：失败只 `warning`，不得改变退出码、分数、CSV/DB。
- 不把证据放进 `detail_json`（避免 DB 膨胀与隐私面扩大）。
- 证据写盘必须排在 `--gate` 之前：`check_gate` 会 `sys.exit`，晚写等于丢证据。

### D2：EvidenceRun + 每题 QuestionEvidence + ContextVar

新增 `src/core/eval_evidence.py`（不 import `rag`/`infra`）：

```python
@dataclass(slots=True)
class QuestionEvidence:
    q_index: int
    trace_id: str | None
    testset: dict
    query: dict
    retrieval: dict
    tool_calls: list[dict]
    judge: dict
    behavior: dict
    facts: dict
    attribution: dict

@dataclass(slots=True)
class EvidenceRun:
    run_id: str
    run_trace_id: str | None
    enabled: bool
    collectors: list[QuestionEvidence | None]
    corpus_chunks: list | None       # ChunkResult，仅类型上不 import
    captured_at: str
```

- `current_evidence: ContextVar[QuestionEvidence | None]`，默认 `None`。
- `EvidenceRun.new_question(q_index)` 创建 collector；生成阶段每题 set/reset。
- **评分阶段显式传参**：`run_evaluation(..., evidence_run=None)` → `score_rows(..., evidence_run=None)`；`score_rows` 每行按 `row["q_index"]` 从 `evidence_run.collectors` 取 collector，`current_evidence.set(collector)`，评分结束把 `HookEvidence.measures` 拷进去。不新增第二个全局 `current_evidence_run`。`evidence_run=None` 或 `collectors[q_index] is None` 时 SHALL 跳过采集，不中断评分。
- **子代理隔离**：`SkillExecutor._run_fork` 切到子 `current_request_ctx` 时，同时 `current_evidence.set(None)`，`finally` 恢复；子代理的 `retrieve_kb` 不得写进父题证据。
- 采集器只接受 dict/list/基本类型；`ChunkResult` 由调用方转成 dict 后写入，避免 core 依赖 infra。
- 归因/探针/写盘放在 `src/cli/eval_evidence_report.py`（CLI 专用，可 import `ChunkResult`/`VectorStore`）。
- 理由：`rag` 只能 import `core`，不能反向依赖 `cli`；把“采集”和“报告”拆开，避免循环与职责混杂。

### D3：检索链记录“三张快照 + 去重 + 截断 + 最终上下文”

在 `retrieval.search` / `rerank_results` / `retrieve_kb` 记录：

1. **dense**：`chunk_id` / `parent_key` / `doc_id` / `source` / `page` / `rank` / `score` / `distance` / `score_kind="cosine_similarity"`
2. **lexical**：同上，`score_kind="lexical"`，`distance=null`
3. **RRF**：`chunk_id` / `parent_key` / `rank` / `score` / `score_kind="rrf"`；`score` 取 `ChunkResult.rrf_score`
4. **rerank 前**：融合结果顺序的 `chunk_id` / `rank_before` / `dense_rank` / `sparse_rank` / `score_kind="rrf"`
5. **rerank 后（dedup 前）**：`chunk_id` / `rank_after` / `score` / `score_kind="rerank"`（降级时 `"fallback"`）
6. **dedup**：`dropped_chunk_ids` / `kept_chunk_ids`，两者都按 `parent_key` 去重口径
7. **top-k 截断**：`dropped_chunk_ids` / `kept_chunk_ids` / `rank_before_topk` / `rank_after_topk`
8. **每次调用的最终 contexts**：`tool_calls[i].final_contexts`（该次 `retrieve_kb` 的 `chunk_id` / `parent_key` / `source` / `page` / `score` / `order_in_prompt`，1-based 全局引用编号）
9. **顶层合并 contexts**：`retrieval.final_contexts` 为多次 `retrieve_kb` 合并后的 prompt 顺序视图；与每 call 视图同时存在，分别回答“哪次找到的”和“模型实际看到什么”

每阶段记录 `count` / `total_count` / `truncated` / `failed`；dense/lexical 单路失败必须可见。候选列表上限默认 50 条/阶段。

**web 样本**：`sample_class=web` 没有 KB 检索链；`retrieval.status="not_applicable"`，dense/lexical/RRF/rerank 列表为空，`fact_in_corpus/candidates/reranked/final=null`。不得把 web contexts 当 KB 候选做归因。

**RRF 分数来源**：`rrf_fusion` 当前只返回 `ChunkResult`，分数在局部变量里丢失。给 `ChunkResult` 增加 `rrf_score: float | None = None`，`rrf_fusion` / `rrf_fusion_multi` 回填；`row_to_chunk_result` 默认 None，不影响现有消费者。

**dedup / top-k 丢弃**：`_dedup_by_parent` 只返回 kept；在 `rerank_results` 内用 dedup 前后列表的 `parent_key` 集合差计算 dropped，不改 `_dedup_by_parent` 签名。

### D4：Query 路径记录“原始 → 实际工具 query → 可用的改写”

- `original_query`：题集 `user_input`。
- `tool_queries`：每次 `retrieve_kb` 的实际 `query`（含 `call_index`）。
- `rewritten_query` / `sub_queries` / `intent_route` / `history_len`：评测路径实际执行了改写才记录；当前 eval 的 `retrieve_kb` 不调用 `rewrite_query`，因此写 `null`，不伪造。
- `trajectory` 里的工具 args 只作为交叉核对，不作为 query 路径的唯一来源。

### D5：Gold chunk 与 Chunk 探针

**gold_chunk_ids 来源优先级**：

1. 题集可选字段 `source_chunk_ids`：
   - `match_scope="exact"` → `gold_chunk_source="testset"`、`gold_chunk_confidence="high"`；
   - `match_scope="group"` / `"partial"` → `gold_chunk_source="testset"`、`gold_chunk_confidence="medium"`（组级/部分匹配不是精确 gold）；
   - 每个 id 必须能在本次 `corpus_chunks` 中找到；缺失/不属于同 doc → 记 warning，降级为探针或 null，不得假 high；
2. 否则用 chunk 探针匹配 `reference` 的数值/文本片段 → `gold_chunk_source="probe"`、`gold_chunk_confidence="medium"`；
3. 都不可用 → `gold_chunk_ids=null`、`gold_chunk_source=null`、`gold_chunk_confidence=null`。

题集可选字段还包括 `match_scope`（`exact` / `group` / `partial`）与 `chunking_version`（或 `index_build_id`）；缺失时按 `match_scope=null` 处理，置信度不得超过 medium。

**探针**：

1. 取 `reference` 中的数值 token（含负号、千分位、百分号、万元/亿元等单位）与归一化文本片段（优先“数字 + 稀有词”窗口，不只用前 40 字）。
2. 从 `EvidenceRun.corpus_chunks` 取全量分块；该列表由 `main()` 的空库检查 `get_all_chunks` 复用，**不再二次读库**。
3. 在 `content` 与 `parent_content` 中做归一化子串/数值匹配，记录命中的 `doc_id/chunk_id/source/page/parent_key`。
4. 输出：
   - `gold_chunk_ids`：命中 chunk（上限 20）；
   - `gold_parent_keys`：命中 parent 键（上限 20）；
   - `fact_in_corpus`：`true` / `false` / `null`；
   - `probe_tokens`：实际用的数值/文本 token；
   - `truncated`：命中超过上限时为 `true`。

**token 归一化**（探针与 `answer_mentions_fact` 共用）：

- 全角数字/符号 → 半角；
- 去掉数值中的千分位逗号与空白；
- 统一负号（`−` / `－` → `-`）；
- 数值键 = `规范化数字 + 单位`；单位缺失时只比数字，并在 `unit_missing` 标记；
- 文本片段：去掉 `【公司 · 报告期】` 头、压缩空白、转小写后做子串匹配；
- 匹配不到时写 `null`，不得猜 `false`。

**parent/child 等价**：`parent_key = (doc_id, md5(parent_content))`，与 `_dedup_by_parent` 同口径。`fact_in_candidates` / `fact_in_reranked` / `fact_in_final` 用 `chunk_id OR parent_key` 判交集，避免“同 parent 的另一个 child 在 final”被误判为未命中。

**testset_conflict**：

- `numeric_conflict`：`true` 当参考答案有数值 token、语料中完全找不到、且题集 `numeric_risk=true`；`false` 当找到；`null` 当没有数值 token 或语料不可用。它是“数值存在性”判断，不是“谁对”的语义判断。
- `unit_missing`：数值 token 后 4 个字符内没有单位（元/万元/亿元/%/百分点）→ `true`；有单位 → `false`；无数值 token 或语料不可用 → `null`。**`unit_missing` 只做信号，不触发 `testset_error`**：表格单位常在表头，参考答案省略单位是正常表述；只有 `numeric_conflict` 进入 `testset_conflict`。
- `period_conflict`：v1 **恒为 null**；不做期间/季度语义判断（见 Non-Goals）。

### D6：裁判、评分与行为证据

- 每个指标：`score`、`error`、`reason`、`metric_attempted`、`skipped_reason`。
- `metric_attempted`：该指标是否真的调用了 `measure`。`sample_class=empty` / 跑图失败时，四主分与两项裁判诊断未调用 → `false`；`tool_correctness` 仍按轨迹评分 → `true`。
- `skipped_reason`：`sample_class=empty` / `failed` / `null`；仅当 `metric_attempted=false` 时有值。
- `reason` 只在 measure 正常返回后读取，异常分支不读 `metric.reason`。
- `score_rows` 每题评分结束、下一次 `evidence.reset()` 前，把 `HookEvidence.measures` 拷进当前 `QuestionEvidence.judge`；不重解析日志。
- 行为轴：从 `behaviour_rows` 记 `metrics` / `reasons`；`failed` 题记 `score=null` / `reason=failed`；用 `link_behavior_traces` 得到 `trace_id` / `link_method`。
- 不把裁判 raw prompt 或完整 raw output 落盘；只记错误摘要与 reason。

### D6.1：Trace 关联只消费，不生成

- 证据 `trace` 段从 `eval-trace-correlation` 的进程内接口读取：
  - `run_trace_id`（整轮，`EvalTraceContext`）；
  - `sut_trace_id`（该题 `trace_id`）；
  - `judge_measures[]`：直接取 `HookEvidence.measures`（`judge_measure_id` / metric / attempt / model / latency_ms / request_count / response_count / statuses / tokens_in / tokens_out / reason / error），其中 `calls[]` 即嵌套 `judge_calls[]`；
  - 行为轴映射（`trace_id` / `link_method`）。
- `JUDGE_CALL` 日志只记 `reason_len` / `reason_hash`；证据文件 MAY 记录完整 `reason`（仅本地 gitignored），直接取 measure 记录里的 `reason`。
- 取不到时写 null；SHALL NOT 在本 change 里重新生成/猜测 trace ID。
- 行为轴 judge 的内部调用不在 v1 范围：证据只记行为分与 `trace_id` 映射，不伪造缺失的 judge call。
- 行为行以 `read_behaviour_scores` 归一化后的 `q_index`（已校验等于 `order`）为准，与 trace / Langfuse 使用同一题号。

### D7：低分选择、落盘顺序与 `--deepeval-only`

低分判定（满足任一）：

- 四主分任一 `< GATE_THRESHOLDS[...]` 或 NaN；
- `tool_correctness` 有标签且 `< 1.0`，或 `tool_correctness_reason` 是 `failed` / `metric_unavailable` / `tool_correctness:*`；
- `sample_class=empty` 或 `failed`；
- 行为轴任一分 `< 0.5` 或缺失。

`--evidence` 只写这些题；`--evidence-all` 写全部。

**web 样本**：`--evidence` 仅在 `tool_correctness < 1.0` 或行为分 `< 0.5`/缺失时选；`--evidence-all` 写全部。web 题的主分归因写 `inconclusive`，`retrieval.status="not_applicable"`，`fact_in_* = null`，不得按 KB 链归因。

**`--deepeval-only`**：没有四主分与 `masked_df`，只按行为分 `< 0.5` 或缺失选；不把“主分缺失”当成低分。

**落盘**：

```
data/deepeval/evidence/<run_id>/
  summary.json
  q_<q_index>.json
```

`run_id` 用报告时间戳（与 CSV/MD 同源）。写入用临时文件 + `os.replace` 原子落盘。写盘位置在 `_save_eval_report` / Langfuse 回写之后、`check_gate` 之前。

`summary.json` 记录：`run_id`、`qa_count`、`low_score_questions`、各题证据路径、采集耗时、`root_cause_counts.by_metric` / `total`、`inconclusive_count`。

### D8：根因枚举（固定，不自由文本）

| 枚举 | 含义 |
|---|---|
| `judge_failure` | 裁判失败/NaN/JSON 解析失败 |
| `testset_error` | 参考答案的数值在语料中不存在（硬匹配；`unit_missing` 单独不触发） |
| `parsing_or_chunking` | 语料里没有该事实：解析/分块/文档缺失 |
| `retrieval_miss` | chunk 存在，但没进 dense/lexical/RRF 候选 |
| `rerank_or_topk` | 进了候选，但被 rerank/dedup/top-k 挤掉或排序差 |
| `prompt_or_generation` | 进了最终上下文，但回答没用/用错/跑题/给建议 |
| `tool_routing` | 跑图成功、有标签，但期望工具没在顶层调用 |
| `agent_orchestration` | 重复检索、打转、冗余步骤 |
| `inconclusive` | 证据不足（抽象题/匹配不到事实/运行失败） |

`root_cause` 按**指标**输出，不强行一题一个；同一题不同指标可以有不同根因。

### D9：归因规则（metric-scoped）

先算事实标记：

- `fact_tokens`：参考答案归一化后的数值 token 列表；
- `fact_hit_tokens`：`{corpus, candidates, reranked, final}` 各阶段命中的 token；
- `fact_coverage`：`{corpus, candidates, reranked, final}` 各阶段的 `命中数 / fact_tokens 总数`；
- `missing_fact_tokens`：未在 final 命中的 token；
- `fact_in_corpus` / `fact_in_candidates` / `fact_in_reranked` / `fact_in_final`：各阶段是否**至少命中一个** token（bool）；具体根因要求 `fact_coverage.<stage> == 1.0`，部分命中写 `inconclusive`；
- `answer_mentions_fact`：answer 中出现 gold 的数值/文本 token（同一归一化规则）；无 token 时 null；
- `metric_attempted` / `skipped_reason`：指标是否真的调用过 measure；空样本/跑图失败为 false；
- `judge_failed`：`metric_attempted=true` 且（score 为 NaN 或 `error` 非空）；`metric_attempted=false` 时恒为 false；
- `testset_conflict`：仅 `numeric_conflict` 为 true；`unit_missing` 只做信号；`period_conflict` v1 恒 null；
- `tool_called` / `call_count`：顶层轨迹。

规则（按顺序，首个命中即停）：

| 指标 | 规则 |
|---|---|
| 任意主分/裁判诊断 | `metric_attempted=false` → `inconclusive`，`evidence` 写 `skipped:<sample_class=empty|failed>`；SHALL NOT 归 `judge_failure` |
| 事实类指标 | `testset_conflict` → `testset_error`；`fact_coverage.<stage> < 1.0` → `inconclusive`，`evidence` 写 `partial_fact_coverage` + `missing_fact_tokens`；SHALL NOT 用部分覆盖断言具体层 |
| `faithfulness` | judge_failed → `judge_failure`；`fact_in_final=false` → 事实阶段根因（从 `fact_in_corpus` 起，不用 testset_conflict）；`fact_in_final=true` 且 `answer_mentions_fact=false` → `prompt_or_generation`；否则 `inconclusive` |
| `answer_relevancy` | judge_failed → `judge_failure`；answer 为空/failed → `prompt_or_generation`；否则 `inconclusive` |
| `context_precision` | judge_failed → `judge_failure`；gold 不在 candidates → `retrieval_miss`；gold 在 candidates 但不在 final → `rerank_or_topk`；gold 在 final 但分数低 → `rerank_or_topk`（排序/噪声）；否则 `inconclusive` |
| `context_recall` | judge_failed → `judge_failure`；testset_conflict → `testset_error`；`fact_in_corpus=false` → `parsing_or_chunking`；`fact_in_final=false` → 事实阶段根因；否则 `inconclusive` |
| `contextual_relevancy` | judge_failed → `judge_failure`；`fact_in_corpus=false` → `parsing_or_chunking` / `testset_error`；gold 不在 candidates → `retrieval_miss`；gold 在 candidates 但不在 final → `rerank_or_topk`；否则 `inconclusive` |
| `non_advice` | judge_failed → `judge_failure`；低分 → `prompt_or_generation`（合规建议） |
| `tool_correctness` | `tool_correctness_reason=="failed"`（跑图失败）→ `inconclusive`；只有 `delegate_task` → `inconclusive`；跑图成功且期望工具缺失 → `tool_routing` |
| `TaskCompletion` | failed/空回答 → `prompt_or_generation`；低分 → `inconclusive` |
| `StepEfficiency` | 低分 → `agent_orchestration` |
| `AgentLoopDetection` | 低分 → `agent_orchestration` |

**事实阶段根因**（供 F/CR/CREL 使用；`testset_conflict → testset_error` 仅用于 CR/CREL，F 从 `fact_in_corpus` 起）：

```
testset_conflict           → testset_error
!fact_in_corpus            → parsing_or_chunking
!fact_in_candidates        → retrieval_miss
!fact_in_reranked          → rerank_or_topk
!fact_in_final             → rerank_or_topk（dedup/top-k）
否则                        → prompt_or_generation / inconclusive
```

### D10：置信度规则

| 置信度 | 条件 |
|---|---|
| `high` | judge_failed 明确；或 gold 来自 testset 且 `match_scope="exact"`、阶段 `fact_coverage=1.0`；或 testset 数值硬冲突 |
| `medium` | gold 来自探针；或 `match_scope="group"/"partial"`；或事实阶段只能推断到层；或 `unit_missing` 仅作信号 |
| `low` | 只有间接证据（如 answer 未出现 token 但 reference 是抽象表述） |
| `inconclusive` | 无规则命中，或关键事实标记为 null，或 `fact_coverage < 1.0` |

不把 `low` 自动升级为 `high`；`inconclusive` 是合法结果。

### D11：summary 根因分布

`summary.json` 至少包含：

```json
{
  "schema_version": 1,
  "run_id": "20261018_041404",
  "run_trace_id": "eval_run_20261018_041404_ab12cd34",
  "qa_count": 22,
  "low_score_questions": [3, 15, 16, 19, 20, 21, 22],
  "evidence_files": ["q_3.json", "..."],
  "elapsed_ms": 1234,
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
  "scored_metric_count": 12,
  "skipped_metric_count": 2,
  "inconclusive_count": 1
}
```

- `scored_metric_count`：`metric_attempted=true` 且被归因的指标条目数；
- `skipped_metric_count`：`metric_attempted=false` 的指标条目数（空样本/失败样本）；
- `inconclusive_count`：`scored_metric_count` 中 `root_cause="inconclusive"` 的条目数；
- `root_cause_counts` 只统计 `scored_metric_count`，不含 skipped；
- `by_metric` 的分母是该指标被归因的条目数；`total` 是各指标 attribution 的条目总数（同一题不同指标分别计数）；
- 没有低分题时仍写 `summary.json`：`evidence_files=[]`、计数全 0，不静默跳过；
- `low_score_questions` 为 0-based `q_index`；`evidence_files` 为相对目录的文件名。

### D12：隐私、原子写与保留

- 证据包含完整 answer / contexts / 题集 reference；目录必须加入 `.gitignore`。
- 不上传 Langfuse、不写 DB、不暴露 API。
- 每题证据用临时文件 + `os.replace`；`summary.json` 同。
- 保留策略：手动清理；README/排障手册提示“含原文，勿提交/外发”。
- 证据文件名不含用户输入，只含 `q_index`。
- 每阶段候选上限 50、gold 命中上限 20；`truncated` 必须记录。

### D13：schema 版本与向后兼容

- 证据 JSON 顶层 `schema_version: 1`。
- 字段缺失统一写 `null`，不省略键；`attribution` 也是全量键。
- 未来加字段只增不改；删除/改名必须升 `schema_version`。
- 证据/归因不依赖某个指标实现（RAGAS/DeepEval 都可）；取不到的值写 null + `inconclusive`。

### D14：能回答什么、不能回答什么（验收口径）

**能回答（分层定位）**：

| 问题 | 证据 | root_cause | 置信度 |
|---|---|---|---|
| 事实在语料里吗 | `fact_in_corpus` / gold chunks | `parsing_or_chunking` / `testset_error` | 探针 medium；source_chunk_ids + match_scope=exact 才 high，group/partial medium |
| 进了召回候选吗 | dense/lexical/RRF | `retrieval_miss` | medium（v6 无 gold ids） |
| 进了 rerank 后吗 | rerank 前/后 + dedup/top-k | `rerank_or_topk` | medium |
| 进了最终上下文吗 | final contexts（含 parent_key） | `rerank_or_topk` / `prompt_or_generation` | medium |
| 回答用了吗 | `answer_mentions_fact` | `prompt_or_generation` | low/medium |
| 裁判是否挂了 | `judge_failed` + trace measure/calls | `judge_failure` | high |
| 工具是否该调没调 | `tool_correctness` + trajectory | `tool_routing` | high（跑图成功时） |
| 是否重复检索/打转 | StepEfficiency / LoopDetection | `agent_orchestration` | medium |

**不能回答（v1 明写）**：

- 参考答案的数值在**语义上**是否正确（42% vs 53% 谁对）——只做存在性匹配；
- 期间/季度是否错标——`period_conflict` 恒 null，Q15 类可能落到 `inconclusive` 或被其他事实带偏；
- query rewrite 改了什么——评测路径不执行改写，`rewritten` 为 null；
- 子代理内部检索——只看顶层 `delegate_task`；
- 裁判为什么给某个具体 verdict——只有 score/reason，没有逐条 verdict；
- 多因一果的因果证明——`root_cause` 是“最可能丢在哪一层”，不是因果推断。

**因此**：score 告诉你“有问题”，trace 告诉你“哪次请求/哪次裁判”，证据回答“事实在哪一层”，归因给出“最可能的层”。三者合起来能定位大多数单层问题；跨层/语义问题必须保留 `inconclusive` 或人工复核。

### D15：测试策略

- 单测：schema、低分选择（含 `--deepeval-only`）、no-op 关闭路径、写入失败不影响主流程、候选截断、RRF 分数、dedup/top-k 丢弃 ID。
- 检索单测：dense 有/rerank 后无；dense 失败 lexical 成功；parent/child 等价；final 顺序与全局 `order_in_prompt`。
- 探针单测：Q21 类“语料有但没召回” → true；臆造数值 + `numeric_risk` → `numeric_conflict=true`；无 token → null；`source_chunk_ids` 优先；KB 不可用 → null；命中上限。
- 归因单测：
  - Q21 类：corpus 有、candidates 无 → `retrieval_miss`；
  - Q20 类：candidates 有、final 无 → `rerank_or_topk`；
  - Q19 类：数值存在性冲突 → `testset_error`；
  - Q16/Q22 类：error/NaN → `judge_failure`；
  - `tool_correctness_reason="failed"` → `inconclusive`，不是 `tool_routing`；
  - CREL：`fact_in_corpus=false` → `parsing_or_chunking`/`testset_error`，不是 `retrieval_miss`；
  - 抽象题无 token → `inconclusive`。
- 集成：固定 fake retrieval/scorer 造“候选有但 rerank 后没有”，跑 `--evidence`，断言 retrieval 证据与 `attribution.context_precision.root_cause=="rerank_or_topk"`。
- CLI：`--evidence` 与 `--deepeval-only` / `--gate` 组合不改变退出码；`--evidence-all` 写全部；gate 退出前证据已落盘。
- 性能：关闭时无额外 DB/文件 IO；开启时全量 chunk 只读一次（复用空库检查结果）。

## Risks / Trade-offs

- **归因会错**：抽象题/改写题的事实无法用 token 匹配 → `inconclusive`，不强行给标签；`low` 置信度必须保留。
- **parent/child 等价**：用 `parent_key` 降低“同 parent 的另一个 child”误判，但仍可能把同 parent 下不同事实混在一起；证据保留 `chunk_id` 与 `parent_key`，便于人工核对。
- **RRF 字段扩散**：给 `ChunkResult` 加 `rrf_score` 是共享类型变更；默认 None，现有消费者不受影响，但需要测试锁住。
- **探针误判**：数值格式化（千分位/单位/负号）可能漏匹配；v1 只做可解释的硬匹配，匹配不到时 `fact_in_corpus=null/false`，不升级为高置信结论。
- **`numeric_conflict` 启发式**：依赖 `numeric_risk`，只做存在性判断；不判断“42% vs 53% 谁对”，避免误伤。
- **证据体积**：候选集/上下文可能很大 → 每阶段上限 50、最终 contexts 全量、chunk 命中上限 20；截断记 `truncated`。
- **隐私**：证据含原文 → gitignore、仅本地、文档提示；不写 DB/Langfuse。
- **性能开销**：开启时多一次题集 samples 读取 + 文件写；chunk 全量读取复用空库检查，不额外读库。
- **子代理盲区**：`delegate_task` 只记顶层；子代理内部工具调用记 null/unknown，归因 `inconclusive`。
- **gold chunk 缺失**：v6 没有 `source_chunk_ids`，全部走探针，置信度只到 medium；要 high 需题集侧补字段。
- **`--deepeval-only`**：没有主分，只按行为分选；文档必须写清，避免误以为“没写就是没问题”。
- **schema 漂移**：用 `schema_version` 与字段全量 null 约束。

## Migration Plan

1. 先合并 `eval-trace-correlation`，让 `run_trace_id` / `judge_measure_id` / `HookEvidence.measures` / `link_behavior_traces` 可用。
2. `tool-correctness-diagnostic` 已合并/归档，`tool_correctness` / `trajectory` / `expected_tools` 链路已在。
3. 新增证据模块、CLI 开关与归因器；默认关闭，无需迁移。
4. 新增 `data/deepeval/evidence/` 到 `.gitignore`；历史报告不受影响。
5. 回滚：删除开关/采集器/归因器即可；已落盘证据可手动清理。

## Open Questions

- `source_chunk_ids` 是否要在出题侧强制化？本 change 先读可选字段、缺失时用探针；题集 schema 的强制化留给出题侧 change。
- 证据保留期？暂定手动清理；若未来自动化，再定天数与清理命令。
- `period_conflict` 是否值得做？v1 先不做；若 Q15 类题集错误反复出现，再单独设计期间抽取与对齐。
