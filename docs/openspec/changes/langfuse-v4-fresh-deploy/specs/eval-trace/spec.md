## MODIFIED Requirements

### Requirement: Every evaluation run has a run trace and every question keeps its trace

评测 SHALL 为每次运行生成 `run_trace_id`，并保留每题 `trace_id`；两者 SHALL 同时出现在可检索的日志与报告元数据中。两者 SHALL 均为 **32 位小写十六进制字符串**（Langfuse v4 / OTLP 对 trace-id 的硬约束），SHALL NOT 携带前缀或非十六进制字符。

- `run_trace_id` SHALL 为 32hex，在一次运行内唯一。
- `run_trace_id` SHALL 在解析出 `kb_id` 后立即生成；`EVALUATION_RUN` SHALL 在 testset 加载之前记录，含 `kb_id` / `run_id` / `run_trace_id`，使 testset 缺失或加载失败仍有 run 锚点。
- 每题 `trace_id` SHALL 为 32hex，且在一次运行内每题唯一；SHALL NOT 复用 `run_trace_id`。
- Markdown 报告头 SHALL 含 `Run trace: <run_trace_id>`。
- CSV SHALL 保留每题 `trace_id` 列；SHALL NOT 因本 change 新增 `run_trace_id` 列。
- `EVALUATION_START`、`QA_ANSWER_START`、`QA_ANSWER_DONE` SHALL 携带 `run_trace_id`，使 `trace_logs <run_trace_id>` 能枚举题级锚点。

#### Scenario: Run trace is present without Langfuse

- **WHEN** 评测在 `LANGFUSE_ENABLE=false` 下运行
- **THEN** `EVALUATION_RUN` 与报告头 SHALL 仍有 `run_trace_id`，每题 CSV 仍有 `trace_id`
- **AND** 两者均匹配 `^[0-9a-f]{32}$`

#### Scenario: Testset failure still has a run anchor

- **WHEN** 题集缺失或加载失败
- **THEN** 退出前 SHALL 已记录含 `run_trace_id` 的 `EVALUATION_RUN`

#### Scenario: Question anchors are discoverable by run trace

- **WHEN** 用 `run_trace_id` 检索日志
- **THEN** SHALL 能拿到该轮 `EVALUATION_START` 与每题 `QA_ANSWER_START/DONE` 的锚点

#### Scenario: 后端正视题级 trace id

- **WHEN** 单题评测的 trace 被上报到 Langfuse v4
- **THEN** 该 trace 的 id 等于日志中的 `trace_id`
- **AND** SDK 日志中不出现 "not a valid 32 lowercase hex char ... Ignoring trace ID"

## ADDED Requirements

### Requirement: 诊断与评测读取 trace 的字段契约

读取单条 trace SHALL 返回**属性式对象**（支持 `trace.metadata`、`trace.output`、`trace.session_id` 等属性访问），SHALL NOT 返回需要改调用方访问方式的 dict。

**必需字段集合**为 `id`、`metadata`、`observations`（含名称、类型、起止时间与 metadata）。其余字段（`input`、`output`、`session_id`、`user_id`、`latency`、`usage`）SHALL 以属性形式提供，但允许为空值——字段缺失 SHALL 只记 warning，SHALL NOT 使读取失败。

必需字段缺失时 SHALL 显式失败（抛出既有 `SourceFailedError` 或等价的可诊断错误），SHALL NOT 静默返回空值让上层退化为 `no_retrieval_snapshot`。

#### Scenario: 真实 trace 的检索事实可读

- **WHEN** 用一条真实对话的 trace_id 调用诊断
- **THEN** `TraceSource` 读到的 `retrieval_contexts` 条数 > 0
- **AND** 报告中的检索证据与 trace 中记录的一致

#### Scenario: 字段缺失显式失败

- **WHEN** 读取的 trace 缺少 `metadata` 或 `observations`
- **THEN** 读取层抛出可诊断错误
- **AND** 不把"读到但为空"伪装成"没有检索快照"
