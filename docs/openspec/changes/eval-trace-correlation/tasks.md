## 1. Run trace 与报告元数据

- [ ] 1.1 `src/cli/eval_deepeval.py` 在 `main()` 生成 `run_id`（时间戳）与 `run_trace_id = eval_run_<timestamp>_<8hex>`；`EVALUATION_RUN` 事件写 `run_id` / `run_trace_id`。
- [ ] 1.2 `build_report_meta` 增加 `run_trace_id`；`save_markdown_report` 报告头增加 `**Run trace:** <run_trace_id>`。
- [ ] 1.3 不改 CSV 列与 `eval_report` schema；CSV 保留现有 `trace_id`，不新增 `run_trace_id`。
- [ ] 1.4 单测：`run_trace_id` 格式/唯一；报告头含 Run trace；Langfuse 关闭时仍生成；CSV 列集合不变。

## 2. 评分阶段 trace context

- [ ] 2.1 `src/cli/eval_deepeval.py` 的 `score_rows` 每题评分前 `current_trace_id.set(row["trace_id"])`，`finally` reset；异常路径也必须恢复。
- [ ] 2.2 确认 `_score_metric_with_retry`、诊断指标、`_score_tool_correctness` 都在该上下文内执行。
- [ ] 2.3 单测：连续两题评分，第二题开始时 trace 为第二题；第一题结束后已恢复；异常路径不泄漏。

## 3. 两级裁判 ID、JUDGE_CALL 与 JUDGE_HTTP

- [ ] 3.1 `src/core/log_events.py` 登记 `JUDGE_HTTP` 事件；`JUDGE_CALL` 增加 `run_trace_id` / `judge_measure_id` / `component` / `model` / `latency_ms` / `request_count` / `response_count` / `http_call_count` / `statuses` / `finish_reasons` / `tokens_in` / `tokens_out` / `error_count` / `error` / `reason_len` / `reason_hash`。
- [ ] 3.2 `src/infra/llm/provider/http_observability.py`：`HookEvidence.set_run(run_trace_id)` / `begin_measure(measure_id, metric, attempt)` / `end_measure()`；`on_request` 自增 `call_index`（从 1 起）、生成 `judge_call_id`、把 measure/call/开始时间写入 `request.extensions`；`on_response` 计算 `latency_ms`、解析 `usage`/`finish_reason`、追加事件并 `log_event(Event.JUDGE_HTTP, ...)`。
- [ ] 3.3 `_score_metric_with_retry`：每次 attempt 调用 `evidence.begin_measure(...)`，`finally` 调 `end_measure()`；生成 `judge_measure_id = f"{trace_id}:{metric_name}:{attempt}"`；日志写汇总 `request_count/response_count/statuses/finish_reasons/tokens`。
- [ ] 3.4 `reason` 只在 `metric.measure` 正常返回后读取：`reason_len=len(reason)`、`reason_hash=sha256(reason)[:16]`；异常分支不读 `metric.reason`；日志不写完整 reason。
- [ ] 3.5 汇总规则：本次 measure 内所有 response 都有 `usage` 才求和，否则 `tokens_in/tokens_out=null`；无 response 时 `statuses=[]`、`request_count>response_count`、`error` 非空。
- [ ] 3.6 单测：同一 measure 3 次 HTTP → 3 条 `JUDGE_HTTP` + 1 条 `JUDGE_CALL(http_call_count=3)`；两次 attempt 的 measure ID 不同且 `call_index` 重置；200+usage；429；连接失败无 response；非 JSON body；第一题成功/第二题异常的 reason 不复用。

## 4. 行为轴 trace 关联

- [ ] 4.1 `src/cli/eval_scoring.py` 的 `read_behaviour_scores` 保留 `testCase.order`，输出行增加 `order` 字段。
- [ ] 4.2 新增 `link_behavior_traces(behaviour_rows, trace_ids)`：只按 `row["order"]` 映射；order 缺失/非整数/越界 → `trace_id=null`、`link_method="unmapped"`；成功 → `link_method="order"`；不得回退到列表下标。
- [ ] 4.3 证据与 trace 查询使用该映射；不修改 `test_run_*.json`；Markdown 行为段不新增 trace 列。
- [ ] 4.4 单测：正常映射；乱序 rows 仍按 order；order 缺失/越界写 null；不按列表位置硬猜。

## 5. Langfuse metadata

- [ ] 5.1 `_answer_one_question` 的 `langfuse_context.update_current_trace` metadata 增加 `run_trace_id` 与 `component="sut"`。
- [ ] 5.2 不新建 run trace、不改 session_id、不伪造 judge span。
- [ ] 5.3 单测/手动：Langfuse 开启时 metadata 可见；关闭时不调用客户端。

## 6. 文档与验收

- [ ] 6.1 `src/cli/README.md` 说明四组 ID（run / trace / measure / call）、`JUDGE_CALL` 与 `JUDGE_HTTP` 的分工、报告头与 v1 边界。
- [ ] 6.2 `docs/agents/troubleshooting.md` 增加“评测 trace”入口：run_trace_id → 题 trace_id → judge_measure_id → judge_call_id。
- [ ] 6.3 `docs/agents/logging-rules.md` 登记 `JUDGE_CALL` / `JUDGE_HTTP` 字段、取值与“不记 prompt/response 原文”的边界。
- [ ] 6.4 `docs/agents/eval-metrics-troubleshooting.md` 增加“裁判/被测者 trace 怎么看”，并写明行为轴 judge 的 v1 盲区。
- [ ] 6.5 全量 `pytest tests/` + `ruff check src/cli src/infra src/core` 通过。
- [ ] 6.6 活体冒烟：跑 `--limit 1`，确认同一题 `trace_id` 下能看到 `run_trace_id`、`judge_measure_id`、多条 `judge_call_id`、model/status/tokens（或 null）；报告头有 Run trace。
