# eval-evidence Specification

## Purpose
TBD - created by archiving change eval-evidence-collection. Update Purpose after archive.
## Requirements
### Requirement: Evidence collection is opt-in and never changes scoring

评测 SHALL 提供可选的逐题证据采集，由 CLI `--evidence` 开启；默认关闭时 SHALL NOT 增加检索、评分或文件 IO。

- `--evidence` SHALL 只写低分/异常题；`--evidence-all` SHALL 写全部题。
- 证据采集/归因 SHALL NOT 改变任何指标分数、主分、门禁判据、CSV、Markdown、`eval_report` 或 Langfuse。
- 证据采集/归因/写入失败 SHALL 只记 warning，SHALL NOT 中断评测、改变退出码或改变任何分数。
- `--evidence` SHALL 可与 `--limit`、`--gate`、`--run-type`、`--deepeval-only` 组合；SHALL NOT 与任何现有参数互斥。
- `--deepeval-only` 下没有四主分，`--evidence` SHALL 只按行为分 `< 0.5` 或缺失选题，SHALL NOT 把“主分缺失”当成低分。
- 证据写盘 SHALL 发生在 `check_gate` 之前；`--gate` 的 `sys.exit` SHALL NOT 导致已选中的证据丢失。

#### Scenario: Default is off

- **WHEN** 用户不传 `--evidence`
- **THEN** 评测 SHALL NOT 创建 `data/deepeval/evidence/` 下的任何文件，且分数/门禁/报告 SHALL 与未实现该能力时一致

#### Scenario: Evidence failure does not fail evaluation

- **WHEN** 证据写入或归因抛异常（磁盘满、权限不足等）
- **THEN** 评测 SHALL 继续完成并输出正常报告，只记 warning，退出码 SHALL 只由原评测逻辑决定

#### Scenario: Low-score selection

- **WHEN** 用户传 `--evidence` 且某题四主分低于门禁阈值/NaN、`tool_correctness` 未完全命中或失败、行为分低于 0.5 或缺失
- **THEN** 该题 SHALL 生成证据文件；其余题 SHALL NOT 生成

#### Scenario: Evidence-all writes every question

- **WHEN** 用户传 `--evidence-all`
- **THEN** 每个评测题 SHALL 生成证据文件，不论分数高低

#### Scenario: DeepEval-only has no main-score filter

- **WHEN** 用户同时传 `--deepeval-only` 与 `--evidence`
- **THEN** 选题 SHALL 只使用行为分；SHALL NOT 因四主分不存在而写全部题或写 0 分

#### Scenario: Gate exits after evidence is written

- **WHEN** 用户传 `--gate` 且门禁失败
- **THEN** 已选中的证据文件 SHALL 已完整落盘；`sys.exit(1)` SHALL NOT 留下半截文件

### Requirement: Evidence collection is scoped to the main agent and one run

证据采集 SHALL 绑定同一轮的 `EvidenceRun`，且 SHALL NOT 把子代理的检索混入父题证据。

- `run_evaluation` / `score_rows` SHALL 通过显式 `evidence_run` 参数消费同一轮 collectors；SHALL NOT 依赖第二个全局 run ContextVar。
- `score_rows` 每行 SHALL 按 `q_index` 选中对应 collector，并把 `HookEvidence.measures` 拷入该题；不得归到别的题。`evidence_run=None` 或 collector 不存在时 SHALL 跳过采集，不中断评分。
- empty/failed 的 early-continue 分支 SHALL 在 `continue` 前写 `metric_attempted=false` + `skipped_reason`；empty 的 `tool_correctness` 为 true，failed 的 `tool_correctness` 为 false（`_score_tool_correctness` 直接返回，未调 measure）。
- 子代理执行（`SkillExecutor._run_fork`）SHALL 临时将 `current_evidence` 置为 null，并在 `finally` 恢复；子代理的 `retrieve_kb` SHALL NOT 写入父题证据。
- `sample_class=web` 的题 SHALL 记 `retrieval.status="not_applicable"`，`fact_in_corpus/candidates/reranked/final=null`；`judge_failed` SHALL 优先为 `judge_failure`，裁判成功但低分 SHALL 为 `inconclusive`；`tool_correctness` 缺 `retrieve_kb` SHALL 为 `inconclusive`（web fallback），SHALL NOT 判 `tool_routing`。
- `--evidence` 对 web 题 SHALL 只在 `tool_correctness < 1.0` 或行为分 `< 0.5`/缺失时选；`--evidence-all` SHALL 写全部 web 题。
- 低分选择 SHALL 使用**掩码前的逐题 rows**（含 `sample_class`），SHALL NOT 使用 `masked_df`；web 的四主分 SHALL NOT 作为低分触发。

#### Scenario: Subagent retrieval is not captured

- **WHEN** 某题调用 `delegate_task`，子代理内部再调用 `retrieve_kb`
- **THEN** 父题证据 SHALL 只记录顶层 `delegate_task`；子代理的 `retrieve_kb` / contexts SHALL NOT 出现在父题 `retrieval` 或 `tool_calls` 中

#### Scenario: Scoring attaches judge measures to the right question

- **WHEN** 同一轮连续评分两题
- **THEN** 第 1 题的 `HookEvidence.measures` SHALL 只进入 `collectors[0]`，第 2 题进入 `collectors[1]`

#### Scenario: Web sample is not KB attribution

- **WHEN** `sample_class=web` 且裁判成功但分数低
- **THEN** `retrieval.status` SHALL 为 `not_applicable`，`fact_in_*` SHALL 为 null，主分 `root_cause` SHALL 为 `inconclusive`

#### Scenario: Web judge failure still wins

- **WHEN** `sample_class=web` 且 `judge_failed=true`
- **THEN** 该指标 `root_cause` SHALL 为 `judge_failure`，SHALL NOT 因 web 身份降成 `inconclusive`

#### Scenario: Web fallback is not tool routing

- **WHEN** `sample_class=web` 且顶层只有 `search_web`、没有 `retrieve_kb`
- **THEN** `tool_correctness` 的 `root_cause` SHALL 为 `inconclusive`（web fallback），SHALL NOT 为 `tool_routing`

### Requirement: Each evidence file covers the attribution chain

每题证据 SHALL 是一个带 `schema_version` 的 JSON，覆盖从题集到裁判的链路；取不到的值 SHALL 写 `null`，SHALL NOT 省略键或伪造值。

- `testset` SHALL 含 `reference`、`reference_contexts`、`source_file`、`expected_tools`、`numeric_risk`，可选 `source_chunk_ids` / `match_scope` / `chunking_version`。
- `query` SHALL 含 `original` 与 `tool_queries`；可用的 `rewritten` / `sub_queries` / `intent_route` 有则记录，无则 null。
- `retrieval` SHALL 含 dense / lexical / RRF / rerank before / rerank after / dedup / top-k / final contexts 的快照与各阶段 `count`、`total_count`、`failed`、`truncated`。
- 多次 `retrieve_kb` 时，`tool_calls[i].final_contexts` SHALL 记录该次调用的 contexts，`retrieval.final_contexts` SHALL 记录合并后的全局 prompt 顺序；两者都带 `order_in_prompt`（1-based）。
- `tool_calls` SHALL 含工具名、参数、结果数、迭代数、`call_index`；`delegate_task` 只记顶层。
- `judge` SHALL 含每个指标的 `score`、`error`、`reason`；行为轴 SHALL 含逐题分数与 reason。
- `trace` SHALL 含 `run_trace_id`、`sut_trace_id`、`judge_measures[]`（`judge_measure_id` / metric / attempt / model / latency_ms / request_count / response_count / statuses / tokens_in / tokens_out / reason / error）、嵌套 `judge_calls[]`（`judge_call_id` / call_index / status / finish_reasons / tokens / latency / json_ok / error）与行为轴 `trace_id`/`link_method`；`judge_calls` SHALL 能通过 `judge_measure_id` 归到对应 measure。
- `trace` 的 ID 与 measure 记录 SHALL 直接消费 `eval-trace-correlation` 的进程内接口（`HookEvidence.measures` / `EvalTraceContext` / `link_behavior_traces`），SHALL NOT 重解析日志或重新生成；缺失时写 null。
- 行为轴 judge 的内部调用不在 v1 范围：SHALL NOT 伪造缺失的 `judge_calls`；只记行为分与 `trace_id` 映射。
- `facts` SHALL 含 `fact_in_corpus`、`fact_in_candidates`、`fact_in_reranked`、`fact_in_final`、`fact_tokens`、`fact_hit_tokens`、`fact_coverage`、`missing_fact_tokens`、`answer_mentions_fact`、`testset_conflict`、`judge_failed`、`metric_attempted`、`skipped_reason`、`tool_called`；无法判定时写 null。
- `attribution` SHALL 按指标输出 `root_cause` / `confidence` / `evidence`。

#### Scenario: Evidence includes retrieval stage snapshots

- **WHEN** 某题生成证据文件
- **THEN** 文件 SHALL 同时含 dense、lexical、RRF、rerank 前、rerank 后、dedup、top-k 与最终 contexts 的条目；某路失败时 SHALL 记 `failed=true` 且该阶段列表为空

#### Scenario: Missing values are null

- **WHEN** 评测路径未执行 query rewrite，或指标未暴露 reason
- **THEN** 对应字段 SHALL 为 `null`，SHALL NOT 用工具 query 冒充改写结果

#### Scenario: Trace ids are absent

- **WHEN** 运行时 trace 字段不可用（change 未部署、`evidence=None`、或 measure 记录为空）
- **THEN** `trace` 段 SHALL 写 null 字段，证据与归因 SHALL 仍照常生成，SHALL NOT 伪造或重新生成 trace ID

#### Scenario: Delegated retrieval is not fabricated

- **WHEN** 顶层轨迹只有 `delegate_task`
- **THEN** 证据 SHALL 记录该顶层调用，子代理内部工具 SHALL NOT 被伪造为顶层调用

### Requirement: Retrieval evidence exposes candidate identity, score and rank

检索证据 SHALL 使用 `chunk_id` 与 `parent_key` 作为候选身份，并记录各阶段分数、分数类型与排名，使“候选里有但 rerank 后没有”与“候选里没有”可区分。

- 每阶段 SHALL 记录 `chunk_id`、`parent_key`、`doc_id`、`source`、`page`、`score`、`score_kind`、`rank`（0-based）。
- `score_kind` SHALL 取 `cosine_similarity` / `lexical` / `rrf` / `rerank` / `fallback`。
- dense 条目 SHALL 同时记录 `distance`；lexical 条目的 `distance` SHALL 为 null。
- RRF 条目 SHALL 记录 `rrf_score`（来自 `ChunkResult.rrf_score`），SHALL NOT 只保留融合后的顺序。
- 最终 contexts SHALL 记录 `order_in_prompt`（1-based，全局引用编号）。
- 每阶段候选 SHALL 有上限（默认 50）；截断时 SHALL 记 `truncated=true` 与 `total_count`。
- dedup 与 top-k 截断 SHALL 记录被丢弃的 `chunk_id`，不得只记数量。
- `retrieve_kb` 的 outer rerank timeout 分支 SHALL 记录 `rerank.failed=true` / `reason="timeout"` / `score_kind="fallback"`，并记录该分支的 dedup 与 top-k 丢弃 ID；SHALL NOT 让证据链缺段。
- 候选 ID/分数 SHALL 来自运行期采集；SHALL NOT 事后用重跑结果替代。

#### Scenario: RRF score is carried into evidence

- **WHEN** 混合检索产出 RRF 融合结果
- **THEN** 每条 RRF 证据 SHALL 带 `rrf_score` 与 `score_kind="rrf"`，而不是只有排名

#### Scenario: Candidate survives dense but disappears after rerank

- **WHEN** 某 chunk 在 dense 结果中存在、在 rerank 后不存在
- **THEN** 证据 SHALL 同时包含该 chunk 的 dense 条目与 rerank 前条目，且 final contexts 不含它

#### Scenario: Candidate is absent from every stage

- **WHEN** 某 chunk 不在 dense/lexical/RRF/rerank/final 任一阶段
- **THEN** 证据 SHALL 明确显示各阶段均未命中（而不是字段缺失）

#### Scenario: Dedup and top-k record dropped ids

- **WHEN** 某候选被 parent 去重或 top-k 截断丢弃
- **THEN** 证据 SHALL 在 `dropped_chunk_ids` 中列出它，并保留 `kept_chunk_ids`

### Requirement: Chunk probe and gold chunk IDs distinguish corpus absence from retrieval miss

评测 SHALL 提供确定性 chunk 探针：用参考答案的数值/文本片段在当前 KB 全量分块中定位命中 chunk，并生成 gold chunk 归属。

- 探针 SHALL 记录命中的 `doc_id`、`chunk_id`、`parent_key`、`source`、`page`，并给出 `fact_in_corpus` 的 `true`/`false`/`null`。
- `gold_chunk_ids` SHALL 优先取题集 `source_chunk_ids`；缺失时 SHALL 用探针命中结果；都没有时 SHALL 为 null。
- `gold_chunk_source` SHALL 记 `testset` / `probe` / `null`；`gold_chunk_confidence` SHALL 记 `high` / `medium` / `null`。
- 题集 `match_scope="exact"` 时 `gold_chunk_confidence` SHALL 为 `high`；`match_scope="group"` / `"partial"` 或缺失时 SHALL NOT 为 `high`（最高 medium）。
- `source_chunk_ids` 中每个 id SHALL 使用 `ChunkResult.id` 的空间（`{doc_id}:{chunk_index}`），且能在本次 `corpus_chunks` 中找到、属于同一 `doc_id`；缺失/跨 doc 时 SHALL 记 warning 并降级为探针或 null，SHALL NOT 假 high。
- 题集可选的 `chunking_version` / `index_build_id` SHALL 随证据记录；缺失时写 null。
- `gold_parent_keys` SHALL 记录命中的 parent 键；`fact_in_candidates` / `fact_in_reranked` / `fact_in_final` SHALL 用 `chunk_id OR parent_key` 判交集。`parent_content` 为空/None 时 `parent_key` SHALL 为 `(doc_id, chunk_id)`，与 `_dedup_by_parent` 的 passthrough 口径一致。
- 探针与 `answer_mentions_fact` SHALL 使用同一归一化：全角→半角、去千分位逗号/空白、统一负号、数值键=数字+单位、文本去 `【公司 · 报告期】` 头并压缩空白。
- 探针 SHALL NOT 调用 LLM；匹配 SHALL 为归一化字符串/数值匹配。
- 全量分块 SHALL 复用 `main()` 空库检查已读取的 chunks；一次评测 run 内 SHALL NOT 二次读库。
- 命中数 SHALL 有上限（默认 20），截断时记 `truncated=true`。
- `numeric_conflict` SHALL 为 `true` 当参考答案有数值 token、语料中完全找不到、且题集 `numeric_risk=true`；找到时为 `false`；无数值 token 或语料不可用时为 `null`。
- `unit_missing` SHALL 检查数值 token 后 4 个字符内是否有单位（元/万元/亿元/%/百分点）；有单位 `false`、无单位 `true`、无数值/语料不可用 `null`。`unit_missing` 只做信号，SHALL NOT 单独触发 `testset_error`；`testset_conflict` SHALL 只由 `numeric_conflict` 触发。`period_conflict` 在 v1 SHALL 恒为 `null`。

#### Scenario: Fact exists in corpus but was not retrieved

- **WHEN** 参考答案中的数值/文本能在某个 chunk 中命中，但该 chunk 不在最终 contexts
- **THEN** 证据 SHALL 记 `fact_in_corpus=true` 并列出命中 chunk，`gold_chunk_source=probe`

#### Scenario: Testset provides exact source chunk ids

- **WHEN** 题集样本带 `source_chunk_ids` 且 `match_scope="exact"`
- **THEN** 证据 SHALL 使用该字段作为 `gold_chunk_ids`，记 `gold_chunk_source=testset`、`gold_chunk_confidence=high`，SHALL NOT 用探针覆盖

#### Scenario: Testset provides group-scope ids

- **WHEN** 题集样本带 `source_chunk_ids` 但 `match_scope="group"` 或 `"partial"`
- **THEN** `gold_chunk_source=testset`，`gold_chunk_confidence` SHALL 为 `medium`，SHALL NOT 为 `high`

#### Scenario: Stale source chunk ids are downgraded

- **WHEN** `source_chunk_ids` 中的 id 在本次 `corpus_chunks` 中不存在或属于不同 `doc_id`
- **THEN** 证据 SHALL 记 warning，`gold_chunk_source` SHALL 降级为 `probe` 或 `null`，SHALL NOT 继续标记 `high`

#### Scenario: Fact is absent from corpus

- **WHEN** 参考答案的数值/文本在所有 chunk 中都不命中
- **THEN** 证据 SHALL 记 `fact_in_corpus=false`，供判断题集/解析/分块问题

#### Scenario: Numeric risk and no corpus match

- **WHEN** 参考答案含数值 token、语料中完全找不到、且题集 `numeric_risk=true`
- **THEN** `testset_conflict.numeric_conflict` SHALL 为 `true`，供 `testset_error` 优先于 `parsing_or_chunking`

#### Scenario: Probe cannot decide

- **WHEN** 参考答案没有可提取的数值/文本片段，或 KB 读取失败
- **THEN** 证据 SHALL 记 `fact_in_corpus=null`，SHALL NOT 猜测 true/false

#### Scenario: Parent-child equivalence

- **WHEN** gold 命中 child A，最终 context 保留的是同 parent 的 child B
- **THEN** `fact_in_final` SHALL 通过 `parent_key` 判为 true，SHALL NOT 因 `chunk_id` 不同而误判为 false

### Requirement: Deterministic attribution assigns a root cause per metric

评测 SHALL 在证据文件内输出**逐指标**的确定性归因；SHALL NOT 调用 LLM 判断根因，SHALL NOT 修改评分。

- `attribution` SHALL 以指标名为键，每项含 `root_cause`、`confidence`、`evidence`。
- `root_cause` SHALL 取自固定枚举：`judge_failure`、`testset_error`、`parsing_or_chunking`、`retrieval_miss`、`rerank_or_topk`、`prompt_or_generation`、`tool_routing`、`agent_orchestration`、`inconclusive`。
- 归因规则 SHALL 先处理 `metric_attempted=false`（空样本/失败样本）与 `judge_failed`、`testset_conflict`，再按事实阶段（corpus → candidates → rerank → final）定位；无法判定时 SHALL 写 `inconclusive`。
- `metric_attempted=false` 的主分/裁判诊断指标 SHALL 归为 `inconclusive`，`evidence` 写 `skipped:<sample_class=empty|failed>`；SHALL NOT 归为 `judge_failure`。
- `judge_failed` SHALL 仅在 `metric_attempted=true` 且（score 为 NaN 或 error 非空）时为 true；`metric_attempted=false` 时 SHALL 为 false。
- 事实类指标 SHALL 要求对应阶段 `fact_coverage == 1.0` 才给具体根因；`0 < fact_coverage < 1.0` 时 SHALL 写 `inconclusive`，`evidence` 含 `partial_fact_coverage` 与 `missing_fact_tokens`，SHALL NOT 用部分命中断言 `retrieval_miss` / `rerank_or_topk`。
- `fact_in_* is None` SHALL 先短路为 `inconclusive`，SHALL NOT 进入 `is False` 的事实阶段分支。
- `faithfulness` SHALL NOT 接入 gold 事实链：`judge_failed` → `judge_failure`；answer 为空/failed → `prompt_or_generation`；其余 SHALL 为 `inconclusive`（v1 不做 claim 级幻觉诊断）。
- `tool_correctness` 的 `reason=="failed"`（跑图失败）SHALL 归为 `inconclusive`，SHALL NOT 归为 `tool_routing`；web 样本缺 `retrieve_kb` SHALL 归为 `inconclusive`（web fallback）；只有跑图成功、非 web、有标签、期望工具缺失才 SHALL 归为 `tool_routing`。
- `contextual_relevancy` 在 `fact_in_corpus=false` 时 SHALL 优先归为 `parsing_or_chunking` 或 `testset_error`，SHALL NOT 归为 `retrieval_miss`。
- `confidence` SHALL 取 `high` / `medium` / `low`；`inconclusive` 时 SHALL 为 `inconclusive`。
- `evidence` SHALL 是单行字符串，指出触发规则的证据（如 `fact_in_corpus=true, fact_in_candidates=false`），SHALL NOT 只写“低分”。
- `root_cause` SHALL 表示“最可能丢在哪一层”，SHALL NOT 被描述为因果证明；`confidence` SHALL 反映证据质量。
- `period_conflict` 在 v1 SHALL 恒为 null；归因 SHALL NOT 仅凭期间差异断言 `testset_error`。

#### Scenario: Null fact markers are not false

- **WHEN** `fact_in_corpus` / `fact_in_candidates` / `fact_in_final` 为 `null`（web、无 token、语料不可用）
- **THEN** 归因 SHALL 为 `inconclusive`，SHALL NOT 进入 `is False` 的 `parsing_or_chunking` / `retrieval_miss` 分支

#### Scenario: Faithfulness does not use the gold fact chain

- **WHEN** `faithfulness` 非空、裁判成功但分数低
- **THEN** `root_cause` SHALL 为 `inconclusive`，SHALL NOT 因 gold 不在 final 断言 `parsing_or_chunking` / `retrieval_miss`

#### Scenario: Fact exists but is missing from candidates

- **WHEN** `fact_in_corpus=true`、`fact_coverage.corpus=1.0` 且 `fact_in_candidates=false`
- **THEN** 相关指标的 `root_cause` SHALL 为 `retrieval_miss`，`confidence` SHALL 为 `high`（gold 来自 testset 且 `match_scope="exact"`）或 `medium`（其余情况）

#### Scenario: Partial fact coverage is inconclusive

- **WHEN** `0 < fact_coverage.<stage> < 1.0`（只命中部分参考数值）
- **THEN** 相关指标 SHALL 为 `inconclusive`，`evidence` SHALL 含 `partial_fact_coverage` 与 `missing_fact_tokens`，SHALL NOT 断言具体层

#### Scenario: Candidate exists but is dropped before final

- **WHEN** `fact_in_candidates=true` 且 `fact_in_final=false`
- **THEN** 相关指标的 `root_cause` SHALL 为 `rerank_or_topk`，并区分 rerank/dedup/top-k 证据

#### Scenario: Testset numeric conflict

- **WHEN** 参考答案的数值在语料中不存在且 `numeric_risk=true`
- **THEN** `context_recall` 的 `root_cause` SHALL 优先为 `testset_error`，`confidence` SHALL 为 `medium`（`numeric_risk` 只是先验，不排除分块/解析丢失），SHALL NOT 先归因为检索

#### Scenario: Period conflict is not guessed

- **WHEN** 参考答案的期间/季度可能与语料不一致，但 v1 没有结构化期间抽取
- **THEN** `period_conflict` SHALL 为 null，归因 SHALL 依据其他事实或写 `inconclusive`；SHALL NOT 仅凭期间差异断言 `testset_error`

#### Scenario: Judge failed

- **WHEN** `metric_attempted=true` 且指标 score 为 NaN 或 error 非空
- **THEN** 该指标的 `root_cause` SHALL 为 `judge_failure`，`confidence=high`

#### Scenario: Empty sample is not a judge failure

- **WHEN** `sample_class=empty` 或跑图失败导致主分/裁判诊断未调用（`metric_attempted=false`）
- **THEN** 该指标 SHALL 为 `inconclusive`，`evidence` SHALL 写 `skipped:<reason>`；SHALL NOT 为 `judge_failure`

#### Scenario: Failed run is not tool routing

- **WHEN** `tool_correctness_reason == "failed"`
- **THEN** `tool_correctness` 的 `root_cause` SHALL 为 `inconclusive`，SHALL NOT 为 `tool_routing`

#### Scenario: Abstract question cannot be matched

- **WHEN** 参考答案抽不出可匹配的数值/文本 token，关键事实标记为 null
- **THEN** 归因 SHALL 为 `inconclusive`，SHALL NOT 猜测 `retrieval_miss` 或 `prompt_or_generation`

### Requirement: Attribution summary reports root-cause distribution

`summary.json` SHALL 汇总本次 run 的根因分布，供回答“这一轮整体最该修哪一层”。

- SHALL 含 `run_id`、`run_trace_id`、`qa_count`、`low_score_questions`、`evidence_files`、`elapsed_ms`。
- SHALL 含 `root_cause_counts.by_metric` 与 `root_cause_counts.total`。
- SHALL 含 `scored_metric_count`、`skipped_metric_count`、`inconclusive_count`。
- `root_cause_counts` SHALL 只统计 `metric_attempted=true` 的指标；`skipped_metric_count` SHALL 单独计数，SHALL NOT 混入 `inconclusive_count`。
- `low_score_questions` SHALL 为 0-based `q_index`；`evidence_files` SHALL 为相对目录的文件名。
- 没有低分题时 SHALL 仍写 `summary.json`（空 `evidence_files`、零计数），SHALL NOT 静默跳过。
- 计数 SHALL 与逐题 `attribution` 一致；同一题不同指标分别计数。
- `summary.json` SHALL 与逐题证据同目录、同样的原子写语义。

#### Scenario: Summary counts match per-question attribution

- **WHEN** 一次证据采集完成
- **THEN** `summary.json` 的每个根因计数 SHALL 等于逐题 `attribution` 中对应根因的出现次数

#### Scenario: Inconclusive is counted explicitly

- **WHEN** 若干指标无法归因
- **THEN** `inconclusive_count` SHALL 明确计数，SHALL NOT 把它们计入任何具体根因

#### Scenario: No low-score question still writes summary

- **WHEN** `--evidence` 开启但没有任何题命中低分选择
- **THEN** SHALL 仍写 `summary.json`，`evidence_files=[]` 且计数全 0，SHALL NOT 静默不写

### Requirement: Evidence is local, private and atomically written

证据 SHALL 只写入本地 `data/deepeval/evidence/<run_id>/`，SHALL NOT 写入 `eval_report`、CSV、Markdown、Langfuse 或任何 API 响应。

- 目录 SHALL 被 `.gitignore` 忽略；证据 SHALL NOT 提交到 git。
- 每题证据与 `summary.json` SHALL 以临时文件 + 原子替换写入，避免半截文件。
- 证据文件名 SHALL 只含 `q_index`，SHALL NOT 含用户输入。
- 证据手册 SHALL 提示“含完整上下文与回答，勿外发”。

#### Scenario: Evidence is not exposed through API

- **WHEN** 调用 `kb_eval` API
- **THEN** 响应 SHALL NOT 含证据路径、证据内容或 `root_cause`

#### Scenario: Partial write does not leave a corrupt file

- **WHEN** 写入过程中进程异常
- **THEN** 目标路径 SHALL 只可能是完整旧文件或完整新文件，SHALL NOT 出现半截 JSON

### Requirement: Evidence collection and attribution degrade gracefully

证据采集/归因 SHALL 是观测旁路；任一阶段不可用时，其余阶段 SHALL 继续采集并落盘。

- dense/lexical 单路失败 SHALL 记 `failed=true`，不得丢弃另一路证据。
- rerank 降级（fallback）SHALL 记 `score_kind="fallback"`。
- 裁判/行为分缺失 SHALL 记 null + reason，SHALL NOT 阻断证据文件生成。
- 指标实现差异（RAGAS 桥 / DeepEval 原生）SHALL NOT 导致证据采集失败；不支持的字段记 null，归因写 `inconclusive`。
- 归因器抛异常 SHALL 只影响该题 `attribution`，SHALL NOT 阻断证据写入或评测。
- 题集样本读取失败 SHALL 只让 `testset` 字段为 null，SHALL NOT 阻断采集。

#### Scenario: Dense path fails but lexical succeeds

- **WHEN** dense 检索抛错、lexical 返回结果
- **THEN** 证据 SHALL 记 dense `failed=true`，并保留 lexical/RRF/final 证据

#### Scenario: Judge reason unavailable

- **WHEN** 某指标实例不暴露 reason
- **THEN** 证据 SHALL 写 `reason=null`，其余字段照常落盘，相关归因 SHALL 为 `inconclusive` 或按其他规则判定

#### Scenario: Attribution failure is isolated

- **WHEN** 某题归因器抛异常
- **THEN** 该题证据 SHALL 仍落盘，`attribution` SHALL 写全 `inconclusive`，评测 SHALL 继续
