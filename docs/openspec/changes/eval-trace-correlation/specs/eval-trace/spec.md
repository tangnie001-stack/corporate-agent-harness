## ADDED Requirements

### Requirement: Every evaluation run has a run trace and every question keeps its trace

评测 SHALL 为每次运行生成 `run_trace_id`，并保留每题现有 `trace_id`；两者 SHALL 同时出现在可检索的日志与报告元数据中。

- `run_trace_id` SHALL 格式为 `eval_run_<timestamp>_<8hex>`，在一次运行内唯一。
- 每题 `trace_id` SHALL 继续使用现有 `eval_<12hex>` 格式，SHALL NOT 被重命名或复用。
- `EVALUATION_RUN` 日志 SHALL 含 `run_id` 与 `run_trace_id`。
- Markdown 报告头 SHALL 含 `Run trace: <run_trace_id>`。
- CSV SHALL 保留每题 `trace_id` 列；SHALL NOT 因本 change 新增 `run_trace_id` 列。

#### Scenario: Run trace is present without Langfuse

- **WHEN** 评测在 `LANGFUSE_ENABLE=false` 下运行
- **THEN** `EVALUATION_RUN` 与报告头 SHALL 仍有 `run_trace_id`，每题 CSV 仍有 `trace_id`

### Requirement: Judge work is identified at measure and HTTP-call granularity

评测 SHALL 为每次裁判指标打分和每次裁判 HTTP 调用分配可关联的 ID，使一次 measure 内部的多次裁判调用可区分。

- `judge_measure_id` SHALL 为 `<trace_id>:<metric>:<attempt>`。
- `judge_call_id` SHALL 为 `<judge_measure_id>:<call_index>`，`call_index` 从 1 起、在单次 measure 内自增。
- 一次 `metric.measure` SHALL 只产生一条 `JUDGE_CALL`（measure 汇总），其内部每次收到 HTTP response SHALL 产生一条 `JUDGE_HTTP`。
- 同一指标不同 attempt SHALL 使用不同的 `judge_measure_id`，且 `call_index` SHALL 从 1 重新开始。
- 传输层失败（无 response）SHALL NOT 产生 `JUDGE_HTTP`；SHALL 由 `JUDGE_CALL` 的 `request_count > response_count` 与 `error` 体现。
- 本要求 SHALL 覆盖四主分与两项裁判诊断（`contextual_relevancy` / `non_advice`）；`tool_correctness` 不调用裁判，SHALL NOT 产生 `JUDGE_CALL` / `JUDGE_HTTP`；行为轴（`TaskCompletion` / `StepEfficiency`）SHALL NOT 被本 change 展开到 per-call。

#### Scenario: Multi-call metric produces multiple HTTP records

- **WHEN** 某次 `metric.measure` 内部发生 3 次裁判 HTTP 调用
- **THEN** SHALL 产生 3 条 `JUDGE_HTTP`（`call_index=1..3`）与 1 条 `JUDGE_CALL`（`http_call_count=3`），四者共享同一 `judge_measure_id`

#### Scenario: Second attempt restarts call index

- **WHEN** 某题某指标第 2 次 attempt 发生 2 次 HTTP 调用
- **THEN** `judge_measure_id` SHALL 以 `:2` 结尾，`judge_call_id` SHALL 为 `<measure_id>:1` 与 `<measure_id>:2`，SHALL NOT 延续第 1 次 attempt 的序号

#### Scenario: Behavior judge is explicitly out of scope

- **WHEN** 审阅行为轴 `TaskCompletion` / `StepEfficiency` 的分数
- **THEN** 其 trace 关联 SHALL 只有行为分到 `trace_id` 的映射，SHALL NOT 声称已记录其内部裁判调用

### Requirement: Judge execution carries the question trace and call metadata

评测 SHALL 在评分阶段把 `current_trace_id` 设为当前题 `trace_id`，并在评分结束后恢复；`JUDGE_CALL` 与 `JUDGE_HTTP` SHALL 记录可定位裁判执行的字段。

- `score_rows` 每题评分前 SHALL set `current_trace_id`，`finally` SHALL reset；不得泄漏到下一题。
- `JUDGE_CALL` SHALL 含 `trace_id`、`run_trace_id`、`judge_measure_id`、`component=judge`、`metric`、`attempt`、`model`、`latency_ms`、`ok`、`request_count`、`response_count`、`http_call_count`、`statuses`、`finish_reasons`、`tokens_in`、`tokens_out`、`error_count`、`error`、`reason_len`、`reason_hash`。
- `JUDGE_HTTP` SHALL 含 `run_trace_id`、`judge_measure_id`、`judge_call_id`、`component=judge`、`metric`、`attempt`、`call_index`、`status`、`finish_reason`、`tokens_in`、`tokens_out`、`latency_ms`、`json_ok`。
- `reason_len` / `reason_hash` SHALL 只在 `metric.measure` 正常返回后读取；异常分支 SHALL NOT 读取 `metric.reason`（可能为上一题残留）。
- SHALL NOT 记录完整 prompt、response 或完整 `reason` 原文；SHALL NOT 记录 `prompt_len` / `prompt_truncated`。
- 无 HTTP response（连接失败/超时）时 `JUDGE_CALL` 的 `statuses` SHALL 为空、tokens SHALL 为 null，异常摘要 SHALL 保留在 `error`。
- 非 2xx SHALL 在 `JUDGE_HTTP.status` 保留真实状态码，SHALL NOT 伪装成 200 或 null。
- 本次 measure 内任一 response 缺 `usage` 时，`JUDGE_CALL` 的 `tokens_in/tokens_out` SHALL 为 null；SHALL NOT 用部分和冒充总量。

#### Scenario: Judge warnings carry the question trace

- **WHEN** 裁判桥/HTTP hook/DeepEval 在评分阶段输出 warning
- **THEN** 该日志 SHALL 带当前题 `trace_id`（由 `current_trace_id` 注入），且 `JUDGE_CALL` 显式带同一 `trace_id`

#### Scenario: Judge HTTP failure is visible

- **WHEN** 某次 judge 调用返回 429 或连接失败
- **THEN** 429 SHALL 产生 `JUDGE_HTTP(status=429)` 并计入 `JUDGE_CALL.statuses`；连接失败 SHALL 使 `JUDGE_CALL.request_count > response_count`、`statuses=[]` 且 `error` 非空

#### Scenario: Token usage is recorded when available

- **WHEN** 裁判 HTTP 200 body 含 `usage.prompt_tokens/completion_tokens`
- **THEN** `JUDGE_HTTP` 与 `JUDGE_CALL` SHALL 记录对应 tokens；任一 response 缺失时 `JUDGE_CALL` SHALL 写 null

#### Scenario: Reason is not reused across questions

- **WHEN** 第一题 measure 成功、第二题 measure 抛异常
- **THEN** 第二题的 `JUDGE_CALL` SHALL NOT 带第一题的 `reason_len` / `reason_hash`

#### Scenario: Trace context does not leak across questions

- **WHEN** 连续评分两题
- **THEN** 第二题评分开始前 `current_trace_id` SHALL 为第二题 `trace_id`；第一题结束后 SHALL 已恢复

### Requirement: Behaviour scores are linked to question traces without fabricating ids

行为轴分数 SHALL 通过 `test_run.testCases[].order`（0-based）映射到每题 `trace_id`，并标注关联方式。

- `read_behaviour_scores` SHALL 保留 `testCase.order`；SHALL NOT 只保留 `enumerate` 推出的序号。
- 映射 SHALL 由纯函数完成，SHALL NOT 修改 `test_run_*.json`。
- 成功映射的行 SHALL 含 `trace_id` 与 `link_method="order"`。
- order 缺失/非整数/越界时 `trace_id` SHALL 为 null，`link_method` SHALL 为 `unmapped`；SHALL NOT 回退到列表位置猜。
- 证据与 trace 查询 SHALL 使用该映射；Markdown 行为段结构 SHALL NOT 因本 change 改变；SHALL NOT 因映射失败改变行为分。

#### Scenario: Behaviour row links by order

- **WHEN** `test_run` 中某 testCase 的 `order=2`
- **THEN** 映射结果 SHALL 带 `trace_ids[2]` 与 `link_method="order"`

#### Scenario: Shuffled rows still link by order

- **WHEN** `behaviour_rows` 的列表顺序与 `order` 不一致
- **THEN** 映射 SHALL 仍按各行 `order` 取 `trace_id`，SHALL NOT 按列表下标绑定

#### Scenario: Missing order is not guessed

- **WHEN** 某行为行缺少 order 或 order 越界
- **THEN** 其 `trace_id` SHALL 为 null、`link_method` SHALL 为 `unmapped`，SHALL NOT 回退到列表位置硬猜

### Requirement: Trace correlation is always on, non-scoring and failure-tolerant

trace ID 与 judge 调用的可观测字段 SHALL 默认开启，且 SHALL NOT 改变评测结果。

- 本能力 SHALL NOT 依赖 `--evidence`；无该开关时 SHALL 仍生成 ID、`JUDGE_CALL` 与 `JUDGE_HTTP`。
- trace/日志字段拼装失败 SHALL 只记 warning，SHALL NOT 改变分数、门禁、退出码、CSV/DB。
- `run_trace_id` / `trace_id` / `judge_measure_id` / `judge_call_id` SHALL NOT 进入主分、门禁或 Langfuse score。
- 关闭 Langfuse 时 ID 与日志 SHALL 仍可用。
- 评分串行（并发=1）SHALL 是本 change 的已知前提；若未来并发评分，measure 上下文必须另行隔离。

#### Scenario: Trace failure does not change scores

- **WHEN** run_trace_id 生成或 JUDGE_CALL/JUDGE_HTTP 字段拼装抛异常
- **THEN** 评测 SHALL 继续，分数/门禁/CSV 与无 trace 时一致，只记 warning

#### Scenario: Trace ids are not scores

- **WHEN** 审阅主分聚合、门禁与 Langfuse score
- **THEN** trace ID / judge ID SHALL NOT 作为评分输入或 score 名称

### Requirement: Langfuse question traces carry run metadata

当 Langfuse 开启时，每题 trace 的 metadata SHALL 含 `run_trace_id` 与 `component="sut"`；SHALL NOT 改变 session 语义或要求 judge 独立 span。

- 不新建 run 级 Langfuse trace。
- judge 调用 SHALL NOT 被伪造为独立 trace；其关联由 `judge_measure_id` / `judge_call_id` + 日志承载。
- Langfuse 关闭时 SHALL NOT 调用 Langfuse API。

#### Scenario: Metadata appears on question trace

- **WHEN** Langfuse 开启并完成一题
- **THEN** 该题 trace metadata SHALL 含 `run_trace_id` 与 `component=sut`

#### Scenario: Langfuse disabled means no client calls

- **WHEN** `LANGFUSE_ENABLE=false`
- **THEN** 评测 SHALL NOT 为 trace metadata 构造/调用 Langfuse 客户端
