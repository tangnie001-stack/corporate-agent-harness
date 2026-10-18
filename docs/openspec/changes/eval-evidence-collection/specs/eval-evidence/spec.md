## ADDED Requirements

### Requirement: Evidence collection is opt-in and never changes scoring

评测 SHALL 提供可选的逐题证据采集，由 CLI `--evidence` 开启；默认关闭时 SHALL NOT 增加检索、评分或文件 IO。

- `--evidence` SHALL 只写低分/异常题；`--evidence-all` SHALL 写全部题。
- 证据采集/归因 SHALL NOT 改变任何指标分数、主分、门禁判据、CSV、Markdown、`eval_report` 或 Langfuse。
- 证据采集/归因/写入失败 SHALL 只记 warning，SHALL NOT 中断评测、改变退出码或改变任何分数。
- `--evidence` SHALL 可与 `--limit`、`--gate`、`--run-type` 组合；SHALL NOT 与 `--deepeval-only` 互斥。

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

### Requirement: Each evidence file covers the attribution chain

每题证据 SHALL 是一个带 `schema_version` 的 JSON，覆盖从题集到裁判的链路；取不到的值 SHALL 写 `null`，SHALL NOT 省略键或伪造值。

- `testset` SHALL 含 `reference`、`reference_contexts`、`source_file`、`expected_tools`，可选 `source_chunk_ids`。
- `gold_chunk_ids` SHALL 优先取题集 `source_chunk_ids`；缺失时由 chunk 探针生成，并记 `gold_chunk_source` 与 `gold_chunk_confidence`。
- `query` SHALL 含 `original` 与 `tool_queries`；可用的 `rewritten` / `sub_queries` / `intent_route` 有则记录，无则 null。
- `retrieval` SHALL 含 dense / lexical / RRF / rerank before / rerank after / dedup / top-k / final contexts 的快照与各阶段 `count`、`failed`、`truncated`。
- `tool_calls` SHALL 含工具名、参数、结果数、迭代数；`delegate_task` 只记顶层。
- `judge` SHALL 含每个指标的 `score`、`error`、`reason`；行为轴 SHALL 含逐题分数与 reason。
- `facts` SHALL 含 `fact_in_corpus`、`fact_in_candidates`、`fact_in_reranked`、`fact_in_final`、`answer_mentions_fact`、`testset_conflict`、`judge_failed`、`tool_called`；无法判定时写 null。
- `attribution` SHALL 按指标输出 `root_cause` / `confidence` / `evidence`。

#### Scenario: Evidence includes retrieval stage snapshots

- **WHEN** 某题生成证据文件
- **THEN** 文件 SHALL 同时含 dense、lexical、RRF、rerank 前、rerank 后、dedup、top-k 与最终 contexts 的条目；某路失败时 SHALL 记 `failed=true` 且该阶段列表为空

#### Scenario: Missing values are null

- **WHEN** 评测路径未执行 query rewrite，或指标未暴露 reason
- **THEN** 对应字段 SHALL 为 `null`，SHALL NOT 用工具 query 冒充改写结果

#### Scenario: Delegated retrieval is not fabricated

- **WHEN** 顶层轨迹只有 `delegate_task`
- **THEN** 证据 SHALL 记录该顶层调用，子代理内部工具 SHALL NOT 被伪造为顶层调用

### Requirement: Retrieval evidence exposes candidate identity, score and rank

检索证据 SHALL 使用 `chunk_id` 作为候选身份，并记录各阶段分数与排名，使“候选里有但 rerank 后没有”与“候选里没有”可区分。

- 每阶段 SHALL 记录 `chunk_id`、`doc_id`、`source`、`page`、`score`（该阶段适用时）与 `rank`。
- 最终 contexts SHALL 记录 `order_in_prompt`，与最终 prompt 顺序一致。
- 每阶段候选 SHALL 有上限（默认 50）；截断时 SHALL 记 `truncated=true` 与 `total_count`。
- 候选 ID/分数 SHALL 来自运行期采集；SHALL NOT 事后用重跑结果替代。
- dedup 与 top-k 截断 SHALL 记录被丢弃的 `chunk_id`，不得只记数量。

#### Scenario: Candidate survives dense but disappears after rerank

- **WHEN** 某 chunk 在 dense 结果中存在、在 rerank 后不存在
- **THEN** 证据 SHALL 同时包含该 chunk 的 dense 条目与 rerank 前条目，且 final contexts 不含它

#### Scenario: Candidate is absent from every stage

- **WHEN** 某 chunk 不在 dense/lexical/RRF/rerank/final 任一阶段
- **THEN** 证据 SHALL 明确显示各阶段均未命中（而不是字段缺失）

### Requirement: Chunk probe and gold chunk IDs distinguish corpus absence from retrieval miss

评测 SHALL 提供确定性 chunk 探针：用参考答案的数值/文本片段在当前 KB 全量分块中定位命中 chunk，并生成 gold chunk 归属。

- 探针 SHALL 记录命中的 `doc_id`、`chunk_id`、`source`、`page`，并给出 `fact_in_corpus` 的 `true`/`false`/`null`。
- `gold_chunk_ids` SHALL 优先取题集 `source_chunk_ids`；缺失时 SHALL 用探针命中结果；都没有时 SHALL 为 null。
- `gold_chunk_source` SHALL 记 `testset` / `probe` / `null`；`gold_chunk_confidence` SHALL 记 `high` / `medium` / `null`。
- 探针 SHALL NOT 调用 LLM；匹配 SHALL 为归一化字符串/数值匹配。
- 每个 KB 的全量分块在一次评测 run 内 SHALL 最多读取一次。
- 命中数 SHALL 有上限（默认 20），截断时记 `truncated=true`。

#### Scenario: Fact exists in corpus but was not retrieved

- **WHEN** 参考答案中的数值/文本能在某个 chunk 中命中，但该 chunk 不在最终 contexts
- **THEN** 证据 SHALL 记 `fact_in_corpus=true` 并列出命中 chunk，`gold_chunk_source=probe`

#### Scenario: Testset provides source chunk ids

- **WHEN** 题集样本带 `source_chunk_ids`
- **THEN** 证据 SHALL 使用该字段作为 `gold_chunk_ids`，记 `gold_chunk_source=testset`、`gold_chunk_confidence=high`，SHALL NOT 用探针覆盖

#### Scenario: Fact is absent from corpus

- **WHEN** 参考答案的数值/文本在所有 chunk 中都不命中
- **THEN** 证据 SHALL 记 `fact_in_corpus=false`，供判断题集/解析/分块问题

#### Scenario: Probe cannot decide

- **WHEN** 参考答案没有可提取的数值/文本片段，或 KB 读取失败
- **THEN** 证据 SHALL 记 `fact_in_corpus=null`，SHALL NOT 猜测 true/false

### Requirement: Deterministic attribution assigns a root cause per metric

评测 SHALL 在证据文件内输出**逐指标**的确定性归因；SHALL NOT 调用 LLM 判断根因，SHALL NOT 修改评分。

- `attribution` SHALL 以指标名为键，每项含 `root_cause`、`confidence`、`evidence`。
- `root_cause` SHALL 取自固定枚举：`judge_failure`、`testset_error`、`parsing_or_chunking`、`retrieval_miss`、`rerank_or_topk`、`prompt_or_generation`、`tool_routing`、`agent_orchestration`、`inconclusive`。
- 归因规则 SHALL 先处理 `judge_failed` 与 `testset_conflict`，再按事实阶段（corpus → candidates → rerank → final）定位；无法判定时 SHALL 写 `inconclusive`。
- `confidence` SHALL 取 `high` / `medium` / `low`；`inconclusive` 时 SHALL 为 `inconclusive`。
- `evidence` SHALL 是单行字符串，指出触发规则的证据（如 `fact_in_corpus=true, fact_in_candidates=false`），SHALL NOT 只写“低分”。

#### Scenario: Fact exists but is missing from candidates

- **WHEN** `fact_in_corpus=true` 且 `fact_in_candidates=false`
- **THEN** 相关指标的 `root_cause` SHALL 为 `retrieval_miss`，`confidence` SHALL 为 `high`（gold 来自 testset）或 `medium`（gold 来自探针）

#### Scenario: Candidate exists but is dropped before final

- **WHEN** `fact_in_candidates=true` 且 `fact_in_final=false`
- **THEN** 相关指标的 `root_cause` SHALL 为 `rerank_or_topk`，并区分 rerank/dedup/top-k 证据

#### Scenario: Testset period or numeric conflict

- **WHEN** 参考答案的期间/数值/单位与语料冲突
- **THEN** `context_recall` 的 `root_cause` SHALL 优先为 `testset_error`，SHALL NOT 先归因为检索

#### Scenario: Judge failed

- **WHEN** 指标 score 为 NaN 或 error 非空
- **THEN** 该指标的 `root_cause` SHALL 为 `judge_failure`，`confidence=high`

#### Scenario: Abstract question cannot be matched

- **WHEN** 参考答案抽不出可匹配的数值/文本 token，关键事实标记为 null
- **THEN** 归因 SHALL 为 `inconclusive`，SHALL NOT 猜测 `retrieval_miss` 或 `prompt_or_generation`

#### Scenario: Tool was not called

- **WHEN** 期望工具不在顶层轨迹，且标签存在
- **THEN** `tool_correctness` 的 `root_cause` SHALL 为 `tool_routing`

### Requirement: Attribution summary reports root-cause distribution

`summary.json` SHALL 汇总本次 run 的根因分布，供回答“这一轮整体最该修哪一层”。

- SHALL 含 `root_cause_counts.by_metric` 与 `root_cause_counts.total`。
- SHALL 含 `inconclusive_count` 与 `low_score_questions`。
- 计数 SHALL 与逐题 `attribution` 一致；同一题不同指标分别计数。
- `summary.json` SHALL 与逐题证据同目录、同样的原子写语义。

#### Scenario: Summary counts match per-question attribution

- **WHEN** 一次证据采集完成
- **THEN** `summary.json` 的每个根因计数 SHALL 等于逐题 `attribution` 中对应根因的出现次数

#### Scenario: Inconclusive is counted explicitly

- **WHEN** 若干指标无法归因
- **THEN** `inconclusive_count` SHALL 明确计数，SHALL NOT 把它们计入任何具体根因

### Requirement: Evidence is local, private and atomically written

证据 SHALL 只写入本地 `data/deepeval/evidence/<run_id>/`，SHALL NOT 写入 `eval_report`、CSV、Markdown、Langfuse 或任何 API 响应。

- 目录 SHALL 被 `.gitignore` 忽略；证据 SHALL NOT 提交到 git。
- 每题证据与 `summary.json` SHALL 以临时文件 + 原子替换写入，避免半截文件。
- `summary.json` SHALL 记录 run_id、题数、低分题号、每题证据路径与采集耗时。
- 证据 README/手册 SHALL 提示“含完整上下文与回答，勿外发”。

#### Scenario: Evidence is not exposed through API

- **WHEN** 调用 `kb_eval` API
- **THEN** 响应 SHALL NOT 含证据路径、证据内容或 `root_cause`

#### Scenario: Partial write does not leave a corrupt file

- **WHEN** 写入过程中进程异常
- **THEN** 目标路径 SHALL 只可能是完整旧文件或完整新文件，SHALL NOT 出现半截 JSON

### Requirement: Evidence collection and attribution degrade gracefully

证据采集/归因 SHALL 是观测旁路；任一阶段不可用时，其余阶段 SHALL 继续采集并落盘。

- dense/lexical 单路失败 SHALL 记 `failed=true`，不得丢弃另一路证据。
- rerank 降级（fallback）SHALL 记 `scored="fallback"` 或等价标记。
- 裁判/行为分缺失 SHALL 记 null + reason，SHALL NOT 阻断证据文件生成。
- 指标实现差异（RAGAS 桥 / DeepEval 原生）SHALL NOT 导致证据采集失败；不支持的字段记 null，归因写 `inconclusive`。
- 归因器抛异常 SHALL 只影响该题 `attribution`，SHALL NOT 阻断证据写入或评测。

#### Scenario: Dense path fails but lexical succeeds

- **WHEN** dense 检索抛错、lexical 返回结果
- **THEN** 证据 SHALL 记 dense `failed=true`，并保留 lexical/RRF/final 证据

#### Scenario: Judge reason unavailable

- **WHEN** 某指标实例不暴露 reason
- **THEN** 证据 SHALL 写 `reason=null`，其余字段照常落盘，相关归因 SHALL 为 `inconclusive` 或按其他规则判定

#### Scenario: Attribution failure is isolated

- **WHEN** 某题归因器抛异常
- **THEN** 该题证据 SHALL 仍落盘，`attribution` SHALL 写全 `inconclusive`，评测 SHALL 继续
