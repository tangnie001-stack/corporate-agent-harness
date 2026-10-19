# eval-trace Specification

## Purpose
TBD - created by archiving change eval-trace-correlation. Update Purpose after archive.
## Requirements
### Requirement: Every evaluation run has a run trace and every question keeps its trace

评测 SHALL 为每次运行生成 `run_trace_id`，并保留每题现有 `trace_id`；两者 SHALL 同时出现在可检索的日志与报告元数据中。

- `run_trace_id` SHALL 格式为 `eval_run_<timestamp>_<8hex>`，在一次运行内唯一。
- `run_trace_id` SHALL 在解析出 `kb_id` 后立即生成；`EVALUATION_RUN` SHALL 在 testset 加载之前记录，含 `kb_id` / `run_id` / `run_trace_id`，使 testset 缺失或加载失败仍有 run 锚点。
- 每题 `trace_id` SHALL 继续使用现有 `eval_<12hex>` 格式，SHALL NOT 被重命名或复用。
- Markdown 报告头 SHALL 含 `Run trace: <run_trace_id>`。
- CSV SHALL 保留每题 `trace_id` 列；SHALL NOT 因本 change 新增 `run_trace_id` 列。
- `EVALUATION_START`、`QA_ANSWER_START`、`QA_ANSWER_DONE` SHALL 携带 `run_trace_id`，使 `trace_logs <run_trace_id>` 能枚举题级锚点。

#### Scenario: Run trace is present without Langfuse

- **WHEN** 评测在 `LANGFUSE_ENABLE=false` 下运行
- **THEN** `EVALUATION_RUN` 与报告头 SHALL 仍有 `run_trace_id`，每题 CSV 仍有 `trace_id`

#### Scenario: Testset failure still has a run anchor

- **WHEN** 题集缺失或加载失败
- **THEN** 退出前 SHALL 已记录含 `run_trace_id` 的 `EVALUATION_RUN`

#### Scenario: Question anchors are discoverable by run trace

- **WHEN** 用 `run_trace_id` 检索日志
- **THEN** SHALL 能拿到该轮 `EVALUATION_START` 与每题 `QA_ANSWER_START/DONE` 的锚点

### Requirement: Judge work is identified at measure and HTTP-call granularity

评测 SHALL 为每次裁判指标打分和每次裁判 HTTP 调用分配可关联的 ID，使一次 measure 内部的多次裁判调用可区分。

- `judge_measure_id` SHALL 为 `<trace_id>:<metric>:<attempt>`。
- `judge_call_id` SHALL 为 `<judge_measure_id>:<call_index>`，`call_index` 从 1 起、在单次 measure 内自增。
- 一次 `metric.measure` SHALL 只产生一条 `JUDGE_CALL`（measure 汇总）；其内部每次**分配出去的** HTTP 调用 SHALL 产生一条 `JUDGE_HTTP`，包括传输失败（无 response）的调用。
- 同一指标不同 attempt SHALL 使用不同的 `judge_measure_id`，且 `call_index` SHALL 从 1 重新开始。
- 传输失败（request 已发出、无 response）SHALL 由 `end_measure()` 补一条 `JUDGE_HTTP`：`status=null`、`tokens_in/tokens_out=null`、`latency_ms=null`、`error="no_response"`。
- 无 active measure 的请求（行为轴）SHALL 照常记录 request/response 事件，但 SHALL NOT 分配 `call_index`、SHALL NOT 产 `JUDGE_HTTP`、SHALL NOT 伪造 ID。
- 本要求 SHALL 覆盖四主分与裁判诊断（`contextual_relevancy`）；`tool_correctness` 不调用裁判，SHALL NOT 产生 `JUDGE_CALL` / `JUDGE_HTTP`；行为轴（`TaskCompletion` / `StepEfficiency`）SHALL NOT 被本 change 展开到 per-call。

#### Scenario: Multi-call metric produces multiple HTTP records

- **WHEN** 某次 `metric.measure` 内部发生 3 次裁判 HTTP 调用且都收到 response
- **THEN** SHALL 产生 3 条 `JUDGE_HTTP`（`call_index=1..3`）与 1 条 `JUDGE_CALL`（`request_count=3` / `response_count=3`），四者共享同一 `judge_measure_id`

#### Scenario: Second attempt restarts call index

- **WHEN** 某题某指标第 2 次 attempt 发生 2 次 HTTP 调用
- **THEN** `judge_measure_id` SHALL 以 `:2` 结尾，`judge_call_id` SHALL 为 `<measure_id>:1` 与 `<measure_id>:2`，SHALL NOT 延续第 1 次 attempt 的序号

#### Scenario: Transport failure has an id and a record

- **WHEN** 某次裁判调用已发出 request 但连接失败/超时
- **THEN** SHALL 产生一条 `JUDGE_HTTP`，含该次 `judge_call_id`、`status=null`、`error="no_response"`；`JUDGE_CALL` 的 `request_count` SHALL 大于 `response_count`

#### Scenario: Behaviour judge has no active measure

- **WHEN** 行为轴 judge 请求经过同一个 `HookEvidence`，但没有 `begin_measure`
- **THEN** request/response 事件 SHALL 仍被记录（现有 hooks 断言可用），但 SHALL NOT 产生 `JUDGE_HTTP`，SHALL NOT 分配 `judge_call_id`

### Requirement: Judge execution carries the question trace and call metadata

评测 SHALL 在评分阶段把 `current_trace_id` 设为当前题 `trace_id`，并在评分结束后恢复；`JUDGE_CALL` 与 `JUDGE_HTTP` SHALL 记录可定位裁判执行的字段。

- `score_rows` 每题评分前 SHALL set `current_trace_id`，`finally` SHALL reset；不得泄漏到下一题；`row` 没有 `trace_id` 时 SHALL 跳过 set，SHALL NOT `KeyError`。
- `JUDGE_CALL` SHALL 含 `trace_id`、`run_trace_id`、`judge_measure_id`、`component=judge`、`metric`、`attempt`、`model`、`latency_ms`、`ok`、`request_count`、`response_count`、`statuses`、`tokens_in`、`tokens_out`、`error_count`、`error`、`reason_len`、`reason_hash`。
- `JUDGE_HTTP` SHALL 含 `run_trace_id`、`trace_id`、`q_index`、`judge_measure_id`、`judge_call_id`、`component=judge`、`metric`、`attempt`、`call_index`、`status`、`finish_reasons`、`tokens_in`、`tokens_out`、`latency_ms`、`json_ok`、`error`。
- `JUDGE_CALL.statuses` SHALL 按 `call_index` 排序、长度等于 `request_count`；无 response 的位置 SHALL 为 `null`；SHALL NOT 只按响应完成顺序排列。
- `reason_len` / `reason_hash` SHALL 只在 `metric.measure` 正常返回后读取；异常分支 SHALL NOT 读取 `metric.reason`（可能为上一题残留）。
- SHALL NOT 记录完整 prompt、response 或完整 `reason` 原文；SHALL NOT 记录 `prompt_len` / `prompt_truncated`。
- 本次 measure 内任一 response 缺 `usage` 时，`JUDGE_CALL` 的 `tokens_in/tokens_out` SHALL 为 null；SHALL NOT 用部分和冒充总量。
- 非 2xx SHALL 在 `JUDGE_HTTP.status` 保留真实状态码，SHALL NOT 伪装成 200 或 null。
- 本要求 SHALL 将原 `JUDGE_CALL` 的 `note` 更名为 `error`、`seconds` 更名为 `latency_ms`；SHALL NOT 同时保留两个别名。

#### Scenario: Judge warnings carry the question trace

- **WHEN** 裁判桥/HTTP hook/DeepEval 在评分阶段输出 warning
- **THEN** 该日志 SHALL 带当前题 `trace_id`（由 `current_trace_id` 注入），且 `JUDGE_CALL` 显式带同一 `trace_id`

#### Scenario: Judge HTTP failure is visible

- **WHEN** 某次 judge 调用返回 429 或连接失败
- **THEN** 429 SHALL 产生 `JUDGE_HTTP(status=429)` 并计入 `JUDGE_CALL.statuses`；连接失败 SHALL 产生 `JUDGE_HTTP(status=null, error="no_response")`，且 `JUDGE_CALL.request_count > response_count`

#### Scenario: Token usage is recorded when available

- **WHEN** 裁判 HTTP 200 body 含 `usage.prompt_tokens/completion_tokens`
- **THEN** `JUDGE_HTTP` 与 `JUDGE_CALL` SHALL 记录对应 tokens；任一 response 缺失时 `JUDGE_CALL` SHALL 写 null

#### Scenario: Statuses align with call index

- **WHEN** 同一 measure 内两个请求并发且响应顺序与调用顺序相反
- **THEN** `JUDGE_CALL.statuses` SHALL 仍按 `call_index` 排列，SHALL NOT 按响应完成顺序排列

#### Scenario: Reason is not reused across questions

- **WHEN** 第一题 measure 成功、第二题 measure 抛异常
- **THEN** 第二题的 `JUDGE_CALL` SHALL NOT 带第一题的 `reason_len` / `reason_hash`

#### Scenario: Trace context does not leak across questions

- **WHEN** 连续评分两题
- **THEN** 第二题评分开始前 `current_trace_id` SHALL 为第二题 `trace_id`；第一题结束后 SHALL 已恢复

### Requirement: Observation failures never change scoring

trace / judge 观测 SHALL 只做留证；任何观测逻辑异常 SHALL NOT 传播到 httpx 或评分路径。

- httpx `on_request` / `on_response` 的整个函数体 SHALL 包 `try/except`；异常 SHALL 只记 warning，SHALL NOT 向 httpx 抛出。
- `JUDGE_CALL` / `JUDGE_HTTP` 的字段拼装与 `log_event` SHALL 单独包 `try/except`；失败 SHALL NOT 改变 measure 返回值、重试、分数、门禁或退出码。
- `begin_measure` / `end_measure` / `reason` 读取 SHALL 隔离；`end_measure` 异常 SHALL NOT 覆盖原始 measure 异常。
- `evidence is None`（测试/逃生门路径）SHALL 跳过所有 trace 调用，SHALL NOT `AttributeError`。

#### Scenario: Hook logging failure does not fail the judge call

- **WHEN** `JUDGE_HTTP` 的字段序列化或 `log_event` 抛异常
- **THEN** 裁判 HTTP 调用 SHALL 正常返回；分数、重试次数、门禁与无 trace 时一致，只记 warning

#### Scenario: Missing evidence bucket is a no-op

- **WHEN** `run_evaluation` / `score_rows` 以 `evidence=None` 调用
- **THEN** 评分 SHALL 正常完成，SHALL NOT 调用 `begin_measure` / `end_measure` / `mark`

### Requirement: Behaviour scores are linked to question traces without fabricating ids

行为轴分数 SHALL 先按 `test_run.testCases[].order`（0-based）校验并归一化题号，再映射到每题 `trace_id`。

- `read_behaviour_scores` SHALL 保留 `testCase.order`，并要求它是 `[0, n)` 内唯一整数；校验通过后 SHALL 令 `q_index = order` 并按 `order` 排序返回。
- order 缺失/非整数/越界/重复 SHALL 抛 `RuntimeError`（fail loud），SHALL NOT 按列表位置兜底。
- `fill_missing_behaviour_rows`、`_build_behaviour_section`、`_write_langfuse_scores` SHALL 使用同一 `q_index` 顺序，使行为分、trajectories、trace_ids 三者对齐。
- `link_behavior_traces` SHALL 按 `q_index` 映射 `trace_ids[q_index]`；成功记 `link_method="order"`；防御性地对越界/缺失写 `trace_id=null` / `link_method="unmapped"`。
- 映射 SHALL NOT 修改 `test_run_*.json`；SHALL NOT 因映射失败改变行为分。

#### Scenario: Behaviour row links by order

- **WHEN** `test_run` 中某 testCase 的 `order=2`
- **THEN** 该行 `q_index` SHALL 为 2，映射结果 SHALL 带 `trace_ids[2]` 与 `link_method="order"`

#### Scenario: Shuffled test cases are normalized once

- **WHEN** `test_run` 的 testCases 列表顺序与 `order` 不一致
- **THEN** `read_behaviour_scores` SHALL 按 `order` 排序并令 `q_index=order`；Langfuse 与证据 SHALL 使用同一题号

#### Scenario: Invalid order fails loud

- **WHEN** 某行为行的 order 缺失、越界或重复
- **THEN** `read_behaviour_scores` SHALL 抛 `RuntimeError`，SHALL NOT 回退到列表位置硬猜

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
