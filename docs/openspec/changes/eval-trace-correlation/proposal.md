## Why

评测已经有**每题一个** `trace_id`（`eval_<12hex>`）：站点/CLI 日志的 trace 段、CSV 的 `trace_id` 列、Langfuse 的每题 trace 都用它。但这只覆盖了“被测者（SUT）”的一半，仍然无法回答“这一轮评测里，裁判是怎么跑的”：

- **没有 run 级 trace**：一轮评测的 22 个 trace 只能靠时间戳/会话名猜，无法把整轮串起来；
- **裁判调用没有独立标识**：`JUDGE_CALL` 只有 `metric/attempt/seconds/ok/note`，没有 `judge_call_id`、model、status、finish_reason、tokens；
- **评分阶段没有设置 `current_trace_id`**：`score_rows` 只给 `HookEvidence` mark 了 trace；`BridgeDeepEvalLLM` / httpx hook / DeepEval 内部的 warning 日志可能拿不到当前题 trace；
- **行为轴 judge 只能靠 order 关联**：`test_run_*.json` 没有显式 `trace_id`，排查时无法直接跳到对应题目 trace；
- **SUT 与 judge 混在一条 trace 里**：没有 `component=sut/judge` 标签，无法区分“这是检索/生成”还是“这是裁判”；
- **没有可检索的 judge 调用记录**：一次裁判失败后，只能看到 `JUDGE_CALL ok=false`，看不到模型、HTTP 状态、token、结束原因。

本 change 只做**trace 关联与裁判调用观测**：不改变评分、不改门禁、不建议把完整 prompt/response 落日志。它让每次评测、每个问题、每次裁判调用都有一个可追的 ID。

## What Changes

- **每次评测一个 `run_trace_id`**：`eval_run_<timestamp>_<8hex>`；写入 `EVALUATION_RUN` 日志、Markdown 报告头、Langfuse trace metadata（每题）。`run_id` 仍用时间戳。
- **保留每题 `trace_id`**：现有 `eval_<12hex>` 不变；CSV / 日志 / Langfuse 继续使用。
- **每次裁判调用一个 `judge_call_id`**：`<trace_id>:<metric>:<attempt>`；与 `trace_id`、`metric`、`attempt` 一起写入 `JUDGE_CALL`。
- **评分阶段设置 `current_trace_id`**：`score_rows` 每题评分前 set/reset 为该题 `trace_id`，让裁判桥、httpx hook、DeepEval 内部日志都带上当前题 trace。
- **扩展 `JUDGE_CALL` 字段**：`run_trace_id` / `judge_call_id` / `component=judge` / `model` / `status` / `finish_reason` / `tokens_in` / `tokens_out` / `latency_ms` / `prompt_len` / `prompt_truncated` / `reason` / `error`。不记录 prompt/response 原文。
- **HookEvidence 补 usage/状态**：从裁判 HTTP response 解析 `usage`、`finish_reason`，附在证据事件上；`JUDGE_CALL` 从本次 attempt 的事件窗口汇总这些字段。
- **行为轴 trace 关联**：证据/报告按 `q_index` + `test_run.order` 关联 `trace_id`，并在关联处标注 `link_method="order"`；不伪造 `test_run` 里不存在的 trace 字段。
- **Langfuse metadata**：每题 trace 增加 `run_trace_id` / `component`；不要求新增 judge span（日志已可追；span 化留后续）。
- **always-on**：trace ID 与 judge 日志默认开启；不依赖 `--evidence` 开关；采集/日志失败只 warning，不影响评分。

## Capabilities

### New Capabilities

- `eval-trace`：评测 run / question / judge call 的 trace 关联与裁判调用观测。

### Modified Capabilities

（无）

> 与 `eval-evidence-collection` 的关系：本 change 先打通 trace ID 与 judge 调用记录；evidence change 直接消费 `run_trace_id` / `trace_id` / `judge_call_id`，不再自己生成。

## Impact

- **代码**：`src/cli/eval_deepeval.py`（run_trace_id、score_rows trace context、JUDGE_CALL 字段、报告头）、`src/infra/llm/provider/http_observability.py`（usage/status 解析）、`src/core/log_events.py`（事件字段登记）、可能的 `src/infra/llm/trace_context.py`（run trace contextvar）。
- **测试**：run/judge trace ID、JUDGE_CALL 字段、评分阶段 current_trace_id 恢复、HTTP 非 2xx/usage 解析、行为 order 关联、关闭 Langfuse 时仍可用。
- **数据/API**：不改 `eval_report` schema、不改评分；CSV 的 `trace_id` 列保留；Markdown 报告头新增 `run_trace_id`；不新增 API。
- **文档**：`src/cli/README.md`、`docs/agents/eval-metrics-troubleshooting.md`、`docs/agents/troubleshooting.md`（trace-first 的评测分支）、`docs/agents/logging-rules.md`（JUDGE_CALL 字段）。
- **依赖**：无新增依赖。
