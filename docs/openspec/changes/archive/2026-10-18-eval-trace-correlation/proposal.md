## Why

评测已经有**每题一个** `trace_id`（`eval_<12hex>`）：站点/CLI 日志的 trace 段、CSV 的 `trace_id` 列、Langfuse 的每题 trace 都用它。但这只覆盖了“被测者（SUT）”的一半，仍然无法回答“这一轮评测里，裁判是怎么跑的”：

- **没有 run 级 trace**：一轮评测的 22 个 trace 只能靠时间戳/会话名猜，无法把整轮串起来；题级日志本身也没有 run ID；
- **裁判调用没有独立标识**：`JUDGE_CALL` 只有 `metric/attempt/seconds/ok/note`，没有 `judge_measure_id` / `judge_call_id`、model、status、finish_reason、tokens；
- **一次指标打分里有多达 2–9 次裁判调用**：Faithfulness 4 次（truths/claims/verdicts/reason），AnswerRelevancy 3 次（statements/verdicts/reason），ContextualPrecision / ContextualRecall 各 2 次，ContextualRelevancy 每条 context 一次加 reason（≤9），NonAdvice 3 次。现在每个 attempt 只记一条汇总，**内部哪一步失败看不到**；传输失败甚至连 ID 都没有；
- **评分阶段没有设置 `current_trace_id`**：`score_rows` 只给 `HookEvidence` mark 了 trace；`BridgeDeepEvalLLM` / httpx hook / DeepEval 内部的 warning 日志可能拿不到当前题 trace；
- **行为轴 judge 只能靠 order 关联**：`test_run_*.json` 没有显式 `trace_id`，且 `read_behaviour_scores` 当前丢弃了 `order`；
- **SUT 与 judge 混在一条 trace 里**：没有 `component=sut/judge` 标签，无法区分“这是检索/生成”还是“这是裁判”；
- **没有可检索的 judge 调用记录**：一次裁判失败后，只能看到 `JUDGE_CALL ok=false`，看不到模型、HTTP 状态、token、结束原因。

本 change 只做**trace 关联与裁判调用观测**：不改变评分、不改门禁、不建议把完整 prompt/response 落日志。它让每次评测、每个问题、每次裁判指标打分、每次裁判 HTTP 调用都有一个可追的 ID。

## What Changes

- **每次评测一个 `run_trace_id`**：`eval_run_<timestamp>_<8hex>`；在解析出 `kb_id` 后立即生成，`EVALUATION_RUN` 在 testset 加载**之前**记录；写入 Markdown 报告头、Langfuse trace metadata（每题），并附加到 `EVALUATION_START` / `QA_ANSWER_START` / `QA_ANSWER_DONE`。`run_id` 仍用时间戳。
- **保留每题 `trace_id`**：现有 `eval_<12hex>` 不变；CSV / 日志 / Langfuse 继续使用。
- **裁判采用两级 ID**：
  - `judge_measure_id = <trace_id>:<metric>:<attempt>`：一次 `metric.measure`（一个指标、一次 attempt）；
  - `judge_call_id = <judge_measure_id>:<call_index>`：该次 measure 内第 `call_index` 次 HTTP 裁判调用（1 起）；
  - 只覆盖走裁判的四主分与 `contextual_relevancy` / `non_advice`；`tool_correctness` 不调裁判，不产生 measure。
- **两级日志**：
  - `JUDGE_CALL`：measure 级汇总（`request_count` / `response_count` / `statuses`（按 `call_index` 排序、缺失位 null）/ `tokens_in` / `tokens_out` / `error_count` / `error` / `reason_len` / `reason_hash`）；
  - `JUDGE_HTTP`：每次分配出去的调用一条（`trace_id` / `q_index` / `call_index` / `status` / `finish_reasons` / `tokens_in` / `tokens_out` / `latency_ms` / `json_ok` / `error`）；
  - 传输失败（有 request、无 response）也产 `JUDGE_HTTP(status=null, error="no_response")`，保证每个 `judge_call_id` 都有落点。
- **评分阶段设置 `current_trace_id`**：`score_rows` 每题评分前 set/reset 为该题 `trace_id`（`row.get`，缺失时跳过），让裁判桥内部 warning 带上当前题 trace。
- **HookEvidence 承载 measure 上下文**：新增 `set_run()` / `begin_measure()` / `end_measure()`，用桶内状态（而不是跨 sync/async 的 ContextVar）给每次请求分配 `call_index`、记录请求开始时间；`end_measure()` 返回完整 measure 记录（含 `calls[]` / 完整 `reason` / `error`），供证据侧消费。`reset()` 保留 run ID。
- **无 active measure 的分支**：行为轴请求照常记录 request/response 事件（保留现有 hooks 断言），但不分配 `call_index`、不产 `JUDGE_HTTP`，避免伪 ID。
- **失败隔离**：hook 整个函数体、`JUDGE_CALL` / `JUDGE_HTTP` 字段拼装与 `log_event`、`end_measure`、reason 读取全部包 `try/except`；观测异常永不传给 httpx 或评分路径。
- **字段迁移**：`JUDGE_CALL` 的 `note` 更名为 `error`、`seconds` 更名为 `latency_ms`，不保留别名；同批更新 EventSpec、logging-rules、troubleshooting 与 `trace_logs` 文档。
- **HookEvidence 补 usage/状态**：从裁判 HTTP response 解析 `usage.prompt_tokens/completion_tokens`、`choices[].finish_reason`；不记录 prompt/response 原文。
- **行为轴 order 归一化**：`read_behaviour_scores` 校验 `order` 为 `[0, n)` 内唯一整数，令 `q_index=order` 并排序返回；缺失/越界/重复直接 fail loud。`fill_missing` / 报告 / Langfuse / 证据统一按该 `q_index` 消费，避免“order 映射到 A 题、Langfuse 写到 B 题”。
- **Langfuse metadata**：每题 trace 增加 `run_trace_id` / `component="sut"`；**不新建 judge span**（log-only，v1 明确边界）。
- **always-on**：trace ID 与 judge 日志默认开启；不依赖 `--evidence` 开关；采集/日志失败只 warning，不影响评分。

## Capabilities

### New Capabilities

- `eval-trace`：评测 run / question / judge measure / judge HTTP call 的 trace 关联与裁判调用观测。

### Modified Capabilities

- `evaluation-pipeline`：改写 `Framework-internal LLM calls are observable by trace id` —— 成功与失败路径都产请求级日志；传输失败补 `status=null` 记录；hook 全程异常隔离；`trace_id` 仍走显式证据桶（不依赖 contextvar 做 hook 归因）；行为轴无 active measure 时不产 `JUDGE_HTTP`；`note`/`seconds` 更名为 `error`/`latency_ms`。

> 与 `eval-evidence-collection` 的关系：本 change 先打通 trace ID、judge 调用记录与 measure 结果；evidence change 直接消费 `run_trace_id` / `trace_id` / `judge_measure_id` / `judge_call_id` / `HookEvidence.measures`，不再自己生成或重解析日志。

## Impact

- **代码**：`src/cli/eval_deepeval.py`（run_trace_id、score_rows trace context、JUDGE_CALL 汇总、报告头）、`src/infra/llm/provider/http_observability.py`（set_run / measure 上下文 / usage 解析 / JUDGE_HTTP / 异常隔离）、`src/cli/eval_scoring.py`（`read_behaviour_scores` 校验 order、`link_behavior_traces`）、`src/core/log_events.py` / `log_event_specs.py`（事件与字段登记）、可能的 `src/infra/llm/trace_context.py`（EvalTraceContext）。
- **测试**：run/judge ID、measure↔HTTP 两级关联、多 HTTP 调用/多 attempt、MockTransport 真实 hook（200/429/ConnectError/非 JSON）、乱序响应、hook 异常隔离、无 active measure、无 response 记录、评分阶段 current_trace_id 恢复、行为 order 归一化（乱序/缺失/重复）、关闭 Langfuse 时仍可用、`reason` 不跨题复用。
- **数据/API**：不改 `eval_report` schema、不改评分；CSV 的 `trace_id` 列保留，**不新增 `run_trace_id` 列**；Markdown 报告头新增 `Run trace`；不新增 API。
- **文档**：`src/cli/README.md`、`docs/agents/eval-metrics-troubleshooting.md`、`docs/agents/troubleshooting.md`（trace-first 的评测分支）、`docs/agents/logging-rules.md`（JUDGE_CALL / JUDGE_HTTP 字段与边界）、`src/cli/trace_logs.py` 模块文档。
- **依赖**：无新增依赖。

## Boundaries（v1 明确不做）

- **行为轴 judge 不做到 per-call**：`TaskCompletion` / `StepEfficiency` 由 DeepEval `evals_iterator` 内部调用，不经过 `_score_metric_with_retry`；v1 只把**行为分**按归一化后的 order 映射到 `trace_id`，不追其内部裁判调用。
- **不建 judge Langfuse span**：只保证日志/证据可追。
- **不记录 prompt 原文、response 原文、prompt 长度/截断标记**：`prompt_len` / `prompt_truncated` 若未来由 bridge 提供再另立字段。
- **不改 CSV schema**：`run_trace_id` 只进报告头/日志/证据。
