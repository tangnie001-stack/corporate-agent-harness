## MODIFIED Requirements

### Requirement: 一轮生成一个 trace，id 与请求 trace_id 一致

应用层 SHALL 为每一轮对话生成恰好一个 Langfuse trace。该 trace 的 id SHALL 与请求级 `trace_id` **逐字相同**，使日志行、响应头、SSE `done` 事件与 Langfuse 四方指向同一条链路。

`trace_id` SHALL 为 **32 位小写十六进制字符串**（W3C Trace Context / OTLP 的 trace-id 形态），SHALL NOT 携带前缀或其他字符。生成顺序 SHALL 为：前端生成 → nginx 兜底（内建 `$request_id`）→ 后端兜底（`uuid4().hex`）；三者产出的形态一致。

**入站 id 是不可信输入**：`X-Trace-ID` / `?trace_id` 完全由客户端提供。系统 SHALL 在使用前按 `^[0-9a-f]{32}$` 校验；**不合法时 SHALL 静默丢弃该值并服务端重新生成**（与缺失时的行为一致），**SHALL NOT 因此拒绝请求**。

校验与重生成 SHALL 在 **id 被写入请求上下文之前**完成（即任何下游读取该 id 之前），使响应头、日志、SSE 事件与 trace 从源头取到同一个值。

#### Scenario: 正常一轮对话的 id 对齐

- **WHEN** 客户端发起一轮对话并收到完整 SSE 响应
- **THEN** Langfuse 中存在且仅存在一条 trace，其 id 等于响应头 `X-Trace-ID` 的值
- **AND** 该 id 同时等于本轮全部日志行注入的 `trace_id`，以及 SSE `done` 事件携带的 `trace_id`
- **AND** 该 id 匹配 `^[0-9a-f]{32}$`

#### Scenario: 由日志反查 Langfuse

- **WHEN** 从日志中取得某个 32 位十六进制 `trace_id`
- **THEN** 可在 Langfuse 中按该 id **原样**检索到对应的 trace（无需任何格式转换或映射）

#### Scenario: 三级生成链

- **WHEN** 前端携带 `X-Trace-ID` 发起请求
- **THEN** 后端沿用该值，不重新生成
- **AND** 当客户端未携带时，后端使用 nginx 注入的 `X-Trace-ID`（`$request_id`）
- **AND** 当前两者都缺失时，后端生成 `uuid4().hex`

#### Scenario: 非法入站 id 被拒且不污染观测库

- **WHEN** 请求携带非 32hex 的 `X-Trace-ID`（例如 `trace_<uuid>`、含空格、含短横线、或长度不符）
- **THEN** 该值 SHALL NOT 成为 Langfuse 的 trace id（既不写入也不报错失败）
- **AND** 服务端重新生成一个合法 id，响应的 `X-Trace-ID`、日志行与该 trace 的 id 三者仍一致
- **AND** 请求本身**照常成功处理**（不因 id 非法而返回错误状态）

#### Scenario: 校验发生在 id 生效之前

- **WHEN** 请求携带既非空、又合法的 `X-Trace-ID`
- **THEN** 响应头 `X-Trace-ID`、该请求全部日志行的 `trace_id`、SSE `done` 事件与 trace id 四者相同

### Requirement: 开关语义与故障隔离

`LANGFUSE_ENABLE` SHALL 作为应用侧 trace 产出的总开关，默认值为 `true`，且 SHALL 保留置为 `false` 时完全不产出 trace 的能力。关闭时 SHALL 在**函数体首行与每个子 observation 创建点**短路：不创建子 observation、不触碰 SDK 客户端、不发送任何上报请求；根 observation 若由装饰器产生，允许其存在但 SHALL 为空且不上报。SDK 侧 SHALL 同时以 `LANGFUSE_TRACING_ENABLED=false` 兜底。无论开关取值或 Langfuse 后端是否可达，对话功能 SHALL NOT 因 tracing 受到影响。

#### Scenario: 开关关闭时不产出

- **WHEN** `LANGFUSE_ENABLE=false` 且应用完成一轮对话
- **THEN** Langfuse 侧不新增任何 trace
- **AND** 对话正常完成，SSE 事件序列与开启时一致

#### Scenario: 关闭时不产生任何上报请求

- **WHEN** `LANGFUSE_ENABLE=false` 时完成一轮对话
- **THEN** 进程内不产生任何指向 Langfuse 的 HTTP 请求
- **AND** 不创建任何子 observation；装饰器产生的根 observation 为空且不上报

#### Scenario: CLI 链路同样受控

- **WHEN** 以 `LANGFUSE_ENABLE=false` 运行 `eval_deepeval` 评估
- **THEN** Langfuse 侧不新增任何 trace
- **AND** 评估流程照常完成并产出 CSV（不因开关关闭而中断）

#### Scenario: 后端不可达时对话不受影响

- **WHEN** `LANGFUSE_ENABLE=true` 但 Langfuse 后端不可达
- **THEN** 对话仍正常完成，不产生面向用户的错误
- **AND** 故障仅以日志形式暴露

#### Scenario: 缺省配置采用默认开关

- **WHEN** 环境变量未显式提供 `LANGFUSE_ENABLE`
- **THEN** 应用按 `true` 处理

### Requirement: trace 的用户、标签与业务元数据

trace SHALL 记录触发该轮对话的用户标识；SHALL 记录一组**低基数且稳定**的标签用于筛选；SHALL 以元数据形式记录本轮的运行事实（生效智能体、本轮加载的技能、知识库标识与领域、深思考开关等）。

标签 SHALL 仅限低基数取值；**高基数或易变的取值（如具体知识库标识、智能体名、技能名）SHALL 写入元数据而非标签** —— 标签一经写入无法通过 API 删除，写错或改口径会永久残留。

**迟到字段的承载**：v5 的 `propagate_attributes` 只在上下文建立时生效，无法回填；轮末才确定的运行事实（如检索快照、工具使用）SHALL 记录在**根 observation 的 metadata**，读取层 SHALL 将其合并进 trace 视图（`trace.metadata`），使上层消费方无需改访问方式。

#### Scenario: 用户标识落库

- **WHEN** 一轮对话完成
- **THEN** 该 trace 携带触发用户的标识，可据此筛选与聚合

#### Scenario: 低基数标签

- **WHEN** 一轮对话完成
- **THEN** 该 trace 的标签仅包含低基数的稳定取值（如是否绑定知识库）
- **AND** 具体知识库标识、生效智能体名与技能名出现在元数据中，而非标签中

#### Scenario: 业务元数据可读

- **WHEN** 在 Langfuse 中打开一条 trace
- **THEN** 可从其元数据读到本轮的生效智能体、加载的技能与知识库上下文，无需回查日志

#### Scenario: 轮末字段仍可读

- **WHEN** 一轮对话结束后读取该 trace 的检索快照与工具使用
- **THEN** 通过读取层得到的 `metadata` 含 `retrieval_contexts` 与 `tools_used`
- **AND** 即使这些字段是在轮末才写入，也不丢失

### Requirement: 评测结果回写为 trace 分数

评测 SHALL 把每题的各指标分数回写到其对应的 trace 上，使评测结果可在 Langfuse 中按 trace 查看与筛选。

回写 SHALL 显式按**每题的 trace id** 定位 trace（评分发生在**批量评测之后**、逐题的观测上下文已关闭，故 SHALL NOT 依赖「当前 observation / 当前 trace」的隐式上下文）。

回写 SHALL 使用 `get_client()` 取得的共享客户端与 `create_score()` API，**SHALL NOT** 新建客户端实例；当 `LANGFUSE_ENABLE` 关闭时，回写 SHALL 被短路且不产生任何出网写入。

#### Scenario: Scores readable on the trace

- **WHEN** 一次评测运行完成
- **THEN** 每题对应的 trace 上出现以指标名命名的分数

#### Scenario: No writes when tracing disabled

- **WHEN** `LANGFUSE_ENABLE` 为关闭状态
- **THEN** 评测不向 Langfuse 写入任何分数，也不产生出网请求

## ADDED Requirements

### Requirement: 观测上报的 SDK 与 API 形态

应用 SHALL 使用 Langfuse Python SDK **5.x**，观测上报 SHALL 采用 OTel 语义的 API（`start_observation` 与 `create_score`），SHALL NOT 依赖已移除的 `langfuse.decorators`、低层 `client.span()` / `client.generation()` / `client.score()`。trace 级属性（user/session/tags/metadata）SHALL 通过 `propagate_attributes` 或根 observation 设置。

#### Scenario: 显式 trace 上下文可用

- **WHEN** 应用在请求外部（如事件流消费、委派子图）创建 span
- **THEN** 通过 `trace_context=TraceContext(trace_id=..., parent_span_id=...)` 将其挂到当前请求的 trace 上
- **AND** 该 span 与请求内创建的 span 处于同一 trace

#### Scenario: 后台任务的根 observation 使用传入 id

- **WHEN** 对话在后台任务中执行（`turn_runner`），且请求上下文已有 trace_id
- **THEN** 该任务的根 observation 通过 `trace_context=TraceContext(trace_id=...)` 建立
- **AND** 其 `trace_id` 与请求的 trace_id 逐字相同（observation 自身的 id 是 16hex span id，不是 trace id）
- **AND** 按该 trace_id 能从后端检索到该 trace

#### Scenario: 线程池内创建 span 不改变归属

- **WHEN** 在 `asyncio.to_thread` 中创建 observation
- **THEN** 该 observation 归属外层请求的 trace（而非新建 trace）
- **AND** 若线程内无法取得上下文，则该 observation 不产出，而不是产出到错误 trace
