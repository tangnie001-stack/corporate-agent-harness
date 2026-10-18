## ADDED Requirements

### Requirement: Every evaluation run has a run trace and every question keeps its trace

评测 SHALL 为每次运行生成 `run_trace_id`，并保留每题现有 `trace_id`；两者 SHALL 同时出现在可检索的日志与报告元数据中。

- `run_trace_id` SHALL 格式为 `eval_run_<timestamp>_<8hex>`，在一次运行内唯一。
- 每题 `trace_id` SHALL 继续使用现有 `eval_<12hex>` 格式，SHALL NOT 被重命名或复用。
- `EVALUATION_RUN` 日志 SHALL 含 `run_id` 与 `run_trace_id`。
- Markdown 报告头 SHALL 含 `Run trace: <run_trace_id>`。
- CSV SHALL 保留每题 `trace_id` 列；SHALL NOT 因本 change 新增 `run_trace_id` 列。
- 每次 judge 调用 SHALL 使用 `<trace_id>:<metric>:<attempt>` 作为 `judge_call_id`。

#### Scenario: Run trace is present without Langfuse

- **WHEN** 评测在 `LANGFUSE_ENABLE=false` 下运行
- **THEN** `EVALUATION_RUN` 与报告头 SHALL 仍有 `run_trace_id`，每题 CSV 仍有 `trace_id`

#### Scenario: Judge call id is deterministic

- **WHEN** 某题某指标第 2 次 attempt
- **THEN** `judge_call_id` SHALL 为 `<trace_id>:<metric>:2`，且同一次 attempt 的日志可据此关联

### Requirement: Judge execution carries the question trace and call metadata

评测 SHALL 在评分阶段把 `current_trace_id` 设为当前题 `trace_id`，并在评分结束后恢复；`JUDGE_CALL` SHALL 记录可定位裁判执行的字段。

- `score_rows` 每题评分前 SHALL set `current_trace_id`，`finally` SHALL reset；不得泄漏到下一题。
- `JUDGE_CALL` SHALL 含 `trace_id`、`run_trace_id`、`judge_call_id`、`component=judge`、`metric`、`attempt`、`model`、`status`、`finish_reason`、`tokens_in`、`tokens_out`、`latency_ms`、`reason`、`error`。
- `reason` / `error` SHALL 为单行截断摘要；SHALL NOT 记录完整 prompt 或 response 原文。
- 无 HTTP response（连接失败/超时）时 `status`/`finish_reason`/tokens SHALL 为 null，异常摘要 SHALL 保留在 `error`。
- 非 2xx SHALL 保留真实 `status`，不得伪装成 200 或 null。

#### Scenario: Judge warnings carry the question trace

- **WHEN** 裁判桥/HTTP hook/DeepEval 在评分阶段输出 warning
- **THEN** 该日志 SHALL 带当前题 `trace_id`（由 `current_trace_id` 注入），且 `JUDGE_CALL` 显式带同一 `trace_id`

#### Scenario: Judge HTTP failure is visible

- **WHEN** 某次 judge 调用返回 429 或连接失败
- **THEN** `JUDGE_CALL` SHALL 含 `status=429` 或 `status=null`、`error` 摘要、`judge_call_id` 与 `trace_id`

#### Scenario: Token usage is recorded when available

- **WHEN** 裁判 HTTP 200 body 含 `usage.prompt_tokens/completion_tokens`
- **THEN** `JUDGE_CALL` SHALL 记录对应 `tokens_in/tokens_out`；缺失时写 null

#### Scenario: Trace context does not leak across questions

- **WHEN** 连续评分两题
- **THEN** 第二题评分开始前 `current_trace_id` SHALL 为第二题 `trace_id`；第一题结束后 SHALL 已恢复

### Requirement: Behaviour scores are linked to question traces without fabricating ids

行为轴分数 SHALL 通过 `q_index + test_run.order` 映射到每题 `trace_id`，并标注关联方式。

- 映射 SHALL 由纯函数完成，SHALL NOT 修改 `test_run_*.json`。
- 成功映射的行 SHALL 含 `trace_id` 与 `link_method="order"`。
- order 缺失/越界时 `trace_id` SHALL 为 null，`link_method` SHALL 记 `null` 或 `unmapped`；SHALL NOT 猜测。
- 报告/证据使用该映射；SHALL NOT 因映射失败改变行为分。

#### Scenario: Behaviour row links by order

- **WHEN** `test_run` 第 3 个 testCase 对应 `q_index=2`
- **THEN** 映射结果 SHALL 带 `trace_ids[2]` 与 `link_method="order"`

#### Scenario: Missing order is not guessed

- **WHEN** 某行为行缺少 order 或 order 越界
- **THEN** 其 `trace_id` SHALL 为 null，SHALL NOT 回退到列表位置硬猜

### Requirement: Trace correlation is always on, non-scoring and failure-tolerant

trace ID 与 judge 调用的可观测字段 SHALL 默认开启，且 SHALL NOT 改变评测结果。

- 本能力 SHALL NOT 依赖 `--evidence`；无该开关时 SHALL 仍生成 ID 与 JUDGE_CALL 字段。
- trace/日志字段拼装失败 SHALL 只记 warning，SHALL NOT 改变分数、门禁、退出码、CSV/DB。
- `run_trace_id` / `trace_id` / `judge_call_id` SHALL NOT 进入主分、门禁或 Langfuse score。
- 关闭 Langfuse 时 ID 与日志 SHALL 仍可用。

#### Scenario: Trace failure does not change scores

- **WHEN** run_trace_id 生成或 JUDGE_CALL 字段拼装抛异常
- **THEN** 评测 SHALL 继续，分数/门禁/CSV 与无 trace 时一致，只记 warning

#### Scenario: Trace ids are not scores

- **WHEN** 审阅主分聚合、门禁与 Langfuse score
- **THEN** trace ID / judge_call_id SHALL NOT 作为评分输入或 score 名称

### Requirement: Langfuse question traces carry run metadata

当 Langfuse 开启时，每题 trace 的 metadata SHALL 含 `run_trace_id` 与 `component="sut"`；SHALL NOT 改变 session 语义或要求 judge 独立 span。

- 不新建 run 级 Langfuse trace。
- judge 调用 SHALL NOT 被伪造为独立 trace；其关联由 `judge_call_id` + 日志承载。
- Langfuse 关闭时 SHALL NOT 调用 Langfuse API。

#### Scenario: Metadata appears on question trace

- **WHEN** Langfuse 开启并完成一题
- **THEN** 该题 trace metadata SHALL 含 `run_trace_id` 与 `component=sut`

#### Scenario: Langfuse disabled means no client calls

- **WHEN** `LANGFUSE_ENABLE=false`
- **THEN** 评测 SHALL NOT 为 trace metadata 构造/调用 Langfuse 客户端
