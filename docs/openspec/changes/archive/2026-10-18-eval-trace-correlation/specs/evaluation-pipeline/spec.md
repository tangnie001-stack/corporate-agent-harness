## MODIFIED Requirements

### Requirement: Framework-internal LLM calls are observable by trace id

评测 SHALL 使**框架内部的 LLM 调用**（原生指标 `a_measure` 内部的多次裁判调用）在日志中可按该题的 `trace_id` 追溯。

**行为轴判官调用的归因边界（明写，避免误期待）**：DeepEval 行为指标在 `evals_iterator` **耗尽之后**才评分，届时循环上下文已结束 ⇒ 其判官调用**不**经 `http_async_client` 的 hooks 做逐题 `trace_id` 归因（该层 `trace_id` 记为空）；行为轴的逐题失败证据 SHALL 由其 `test_run_*.json` 的 `testCases[].metricsData[].reason` 承载，并在报告「Agent 行为轴」段呈现。行为轴请求没有 active measure 时，hook SHALL 照常记录 request/response 事件（保留 hooks 已触发的 smoke 断言），但 SHALL NOT 分配 `judge_call_id`、SHALL NOT 产 `JUDGE_HTTP`。

实现手段 SHALL 为：在裁判的 **`http_async_client`**（`httpx.AsyncClient`）上挂**请求级日志**（httpx event hooks）。**成功与失败路径都产日志行**：每次收到 response SHALL 产一条 `JUDGE_HTTP`（`status` / `finish_reasons` / `tokens_in` / `tokens_out` / `latency_ms` / `json_ok`）；传输失败（无 response）SHALL 由 `end_measure()` 补一条 `status=null` / `error="no_response"` 的记录。**SHALL 挂在 `http_async_client` 而非 `http_client`**。

**hook SHALL 为 `async def` 且以 `await response.aread()` 读体**。**hook 的整个函数体 SHALL 包 `try/except`；任何观测异常 SHALL NOT 向 httpx 抛出**，只记 warning。

**落地现状**：`JUDGE_HTTP` 覆盖成功与失败路径；`JUDGE_CALL` 记录 measure 级汇总（`request_count` / `response_count` / `statuses` / `tokens` / `reason_len` / `reason_hash` / `error`），`seconds` / `note` 已更名为 `latency_ms` / `error`。**请求体摘要 / 响应体摘要仍不记录**；`trace_id` 与 token 足以定位“哪次调用、哪个模型、什么状态、是否被截断”。

**该日志只做留证**：SHALL NOT 被当作 `NaN` 子分类的依据。

`trace_id` SHALL 经**显式闭包**绑定进 event hook，SHALL NOT 依赖 contextvar 传递；裁判 client 跨样本复用 ⇒ 闭包 SHALL 绑到**可变证据桶**（题级串行，安全）。`JUDGE_HTTP` SHALL 显式携带 `trace_id` / `q_index`；`current_trace_id` 只服务日志 patcher 与桥内部 warning，SHALL NOT 作为 hook 归因的唯一来源。

**验收范围（明写）**：本条只覆盖**经我们提供的 `http_async_client` 发出的 LLM 请求**；行为指标的 **DeepEval span 树**仍不进日志。

#### Scenario: Internal calls carry the question's trace id

- **WHEN** 某题的裁判调用在指标内部发生（含重试与多次调用）
- **THEN** 对应日志行 SHALL 携带该题的 `trace_id`

#### Scenario: The logging hook rides on the async client used for the timeout

- **WHEN** 审阅裁判客户端构造处
- **THEN** 请求级日志 SHALL 挂在用于细粒度超时的**同一个 `http_async_client`** 上，且 hook SHALL 为 `async def`

#### Scenario: Hook wiring is proven by a smoke assertion

- **WHEN** 实施期跑 1 题真调裁判
- **THEN** SHALL 断言响应 hook 触发 **> 0** 次，且能取到状态码与 `finish_reasons`

#### Scenario: Successful responses are logged

- **WHEN** 某次裁判 HTTP 调用返回 200 且 body 可解析
- **THEN** SHALL 产出一条 `JUDGE_HTTP`，含 status / finish_reasons / tokens / latency；SHALL NOT 只在失败时记录

#### Scenario: Transport failure is recorded with a null status

- **WHEN** 某次裁判调用已发出 request 但连接失败或超时
- **THEN** SHALL 产出一条 `JUDGE_HTTP(status=null, error="no_response")`，且该次 `judge_call_id` 可被检索

#### Scenario: Observability failure does not fail the request

- **WHEN** `JUDGE_HTTP` 的字段序列化或 `log_event` 抛异常
- **THEN** 裁判 HTTP 调用 SHALL 正常返回，评分/重试/门禁与无 trace 时一致，只记 warning
