## 1. Run trace 与报告元数据

- [ ] 1.1 `src/cli/eval_deepeval.py` 在 `main()` 生成 `run_id`（时间戳）与 `run_trace_id = eval_run_<timestamp>_<8hex>`；`EVALUATION_RUN` 事件写 `run_id` / `run_trace_id`。
- [ ] 1.2 `build_report_meta` 增加 `run_trace_id`；`save_markdown_report` 报告头增加 `**Run trace:** <run_trace_id>`。
- [ ] 1.3 不改 CSV 列与 `eval_report` schema；CSV 保留现有 `trace_id`。
- [ ] 1.4 单测：`run_trace_id` 格式/唯一；报告头含 Run trace；Langfuse 关闭时仍生成。

## 2. 评分阶段 trace context

- [ ] 2.1 `src/cli/eval_deepeval.py` 的 `score_rows` 每题评分前 `current_trace_id.set(row["trace_id"])`，`finally` reset；异常路径也必须恢复。
- [ ] 2.2 确认 `_score_metric_with_retry`、诊断指标、`_score_tool_correctness` 都在该上下文内执行。
- [ ] 2.3 单测：连续两题评分，第二题开始时 trace 为第二题；第一题结束后已恢复；异常路径不泄漏。

## 3. JUDGE_CALL 与裁判 HTTP 观测

- [ ] 3.1 `src/core/log_events.py` 登记 `JUDGE_CALL` 新字段（run_trace_id / judge_call_id / component / model / status / finish_reason / tokens_in / tokens_out / latency_ms / reason / error）。
- [ ] 3.2 `src/cli/eval_deepeval.py` 在 `_score_metric_with_retry` 生成 `judge_call_id = f"{trace_id}:{metric_name}:{attempt}"`；填 model（来自 EvalTraceContext）、latency、ok、reason/error。
- [ ] 3.3 `src/infra/llm/provider/http_observability.py` 的 `on_response` 解析 `usage.prompt_tokens/completion_tokens` 与 body `model`，写入 evidence 事件；不记录 prompt/response 原文。
- [ ] 3.4 `_score_metric_with_retry` 用 `event_start` 取本次 attempt 事件窗口，汇总最后一个 response 的 status/finish_reason/tokens；无 response 写 null，异常摘要进 error。
- [ ] 3.5 单测：200 + usage；429；连接失败无 response；非 JSON body；多 attempt 的 judge_call_id 区分。

## 4. 行为轴 trace 关联

- [ ] 4.1 `src/cli/eval_scoring.py` 新增 `link_behavior_traces(behaviour_rows, trace_ids)`：按 order/q_index 映射，返回带 `trace_id` / `link_method="order"` 的副本；越界/缺失写 null。
- [ ] 4.2 报告/证据使用映射；不修改 `test_run_*.json`。
- [ ] 4.3 单测：正常映射；order 缺失/越界；不按列表位置硬猜。

## 5. Langfuse metadata

- [ ] 5.1 `_answer_one_question` 的 `langfuse_context.update_current_trace` metadata 增加 `run_trace_id` 与 `component="sut"`。
- [ ] 5.2 不新建 run trace、不改 session_id、不伪造 judge span。
- [ ] 5.3 单测/手动：Langfuse 开启时 metadata 可见；关闭时不调用客户端。

## 6. 文档与验收

- [ ] 6.1 `src/cli/README.md` 说明 run_trace_id / trace_id / judge_call_id 与报告头。
- [ ] 6.2 `docs/agents/troubleshooting.md` 增加“评测 trace”入口：run_trace_id → 题 trace_id → judge_call_id。
- [ ] 6.3 `docs/agents/logging-rules.md` 登记 JUDGE_CALL 新字段与取值。
- [ ] 6.4 `docs/agents/eval-metrics-troubleshooting.md` 增加“裁判/被测者 trace 怎么看”。
- [ ] 6.5 全量 `pytest tests/` + `ruff check src/cli src/infra src/core` 通过。
- [ ] 6.6 活体冒烟：跑 `--limit 1`，确认日志中同一题 `trace_id` 下能看到 `run_trace_id`、`judge_call_id`、model/status/tokens（或 null）；报告头有 Run trace。
