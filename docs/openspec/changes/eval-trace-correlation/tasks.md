## 1. Run trace、对象生命周期与报告元数据

- [x] 1.1 `src/cli/eval_deepeval.py` 在解析出 `kb_id` 后立即生成 `run_id`（时间戳）与 `run_trace_id = eval_run_<timestamp>_<8hex>`；`EVALUATION_RUN` 在 testset 加载**之前**记录 `kb_id` / `run_id` / `run_trace_id`。
- [x] 1.2 `EVALUATION_START`、`QA_ANSWER_START`、`QA_ANSWER_DONE` 增加 `run_trace_id`；更新 `src/core/log_events.py` / `log_event_specs.py` 的 EventSpec 字段。
- [x] 1.3 `judge_evidence = HookEvidence()` 后立即 `set_run(run_trace_id)`；`EvalTraceContext` 在 `build_bridge_judge` 之后创建，`judge_model=bridge_judge.model_name`。
- [x] 1.4 `build_report_meta` 增加 `run_trace_id`；`save_markdown_report` 报告头增加 `**Run trace:** <run_trace_id>`。
- [x] 1.5 不改 CSV 列与 `eval_report` schema；CSV 保留现有 `trace_id`，不新增 `run_trace_id`。
- [x] 1.6 单测：`run_trace_id` 格式/唯一；题集加载失败仍有 `EVALUATION_RUN`；报告头含 Run trace；Langfuse 关闭时仍生成；CSV 列集合不变；`EVALUATION_START` / QA 事件含 run_trace_id。

## 2. 评分阶段 trace context

- [x] 2.1 `score_rows` 每题评分前取 `trace_id = row.get("trace_id")`；有值才 `current_trace_id.set(...)`，`finally` reset；无 trace_id 时不得 KeyError。
- [x] 2.2 `HookEvidence` 的 `reset/mark/begin_measure/end_measure` 仅在 `evidence is not None` 时调用；所有 trace 操作包 try/except。
- [x] 2.3 单测：连续两题评分不串；异常路径恢复；无 trace_id 的 row 不报错；`evidence=None` 评分正常。

## 3. 两级裁判 ID、JUDGE_CALL 与 JUDGE_HTTP

- [x] 3.1 `src/core/log_events.py` / `log_event_specs.py` 登记 `JUDGE_HTTP`（`[cli]` / info）；`JUDGE_CALL` 字段改为：`trace_id` / `run_trace_id` / `judge_measure_id` / `component` / `metric` / `attempt` / `q_index` / `model` / `latency_ms` / `ok` / `request_count` / `response_count` / `statuses` / `tokens_in` / `tokens_out` / `error_count` / `error` / `reason_len` / `reason_hash`；**移除** `seconds` / `note`，**不保留别名**。
- [x] 3.2 `src/infra/llm/provider/http_observability.py`：`HookEvidence.set_run(run_trace_id, judge_model=None)`；`reset()` 清 `events/_trace_id/_measure/_call_index/measures`，保留 `_run_trace_id`；`begin_measure(measure_id, metric, attempt, trace_id, q_index)`；`end_measure(reason, error) -> dict`（schema 见 design D5），并把结果 append 到 `measures`。
- [x] 3.3 `on_request`：有 active measure 时自增 `call_index`（1 起）、生成 `judge_call_id`、把 measure/call/开始时间写 `request.extensions["cb_judge"]`；无 active measure 时只 append request 事件，不分配 ID、不写 extensions。
- [x] 3.4 `on_response`：有 active measure 时解析 `usage`/`choices[].finish_reason`、计算 `latency_ms`、append response 事件并 `log_event(JUDGE_HTTP, ...)`（含 `trace_id` / `q_index` / `call_index`）；无 active measure 时只 append 事件，不产 `JUDGE_HTTP`。
- [x] 3.5 **异常隔离**：`on_request` / `on_response` 整个函数体包 `try/except Exception`，永不向 httpx 抛出；`log_event` 单独再包；`end_measure` / reason 读取 / `JUDGE_CALL` 拼装同样隔离。
- [x] 3.6 `_score_metric_with_retry`：每次 attempt `begin_measure(...)`，`finally` `end_measure(...)`；measure 成功后读 `metric.reason`，异常分支不读；`JUDGE_CALL` 写 `latency_ms` / `request_count` / `response_count` / `statuses`（按 `call_index` 排序、无 response 位补 null）/ `error_count` / `error` / `reason_len` / `reason_hash`。
- [x] 3.7 **无 response 记录**：`end_measure()` 对“有 request、无 response”的 `call_index` 生成 `JUDGE_HTTP(status=null, tokens=null, latency_ms=null, error="no_response")`，保证每个 `judge_call_id` 都有落点。
- [x] 3.8 **真 hook 接线测试**（`httpx.MockTransport`，不触网）：经 `build_bridge_judge(evidence=...)` 发 200+usage、429、`ConnectError`、非 JSON；断言 `JUDGE_HTTP` 条数/字段、`JUDGE_CALL` 汇总、无 response 的 `status=null` 失败记录。
- [x] 3.9 **并发乱序测试**：`asyncio.gather` 让两个请求以相反顺序完成，断言 `statuses` 按 `call_index` 对齐（缺失位 null）。
- [x] 3.10 **异常隔离测试**：注入会抛异常的 logger/encoder，断言评分、重试、门禁与无 trace 时一致。
- [x] 3.11 **无 active measure 测试**：先发一次 behavior-style 请求，断言 events 有 response、`JUDGE_HTTP` 为 0、不抛错。
- [x] 3.12 **reason 不复用测试**：第一题成功、第二题异常，第二题 `reason_len` / `reason_hash` 为空。

## 4. 行为轴 trace 关联

- [x] 4.1 `read_behaviour_scores` 保留 `testCase.order`；校验 order 为 `[0, n)` 内唯一整数；校验通过后令 `q_index=order` 并按 order 排序返回；缺失/非整数/越界/重复抛 `RuntimeError`。
- [x] 4.2 新增 `link_behavior_traces(behaviour_rows, trace_ids)`：按 `q_index` 映射；成功 `link_method="order"`；防御性越界 → `trace_id=null` / `link_method="unmapped"`。
- [x] 4.3 确认 `fill_missing_behaviour_rows`、`_build_behaviour_section`、`_write_langfuse_scores` 都按归一化后的 `q_index` 顺序消费；不修改 `test_run_*.json`；Markdown 行为段不新增 trace 列。
- [x] 4.4 单测：正常映射；乱序 testCases 归一化后 `q_index==order`；缺失/越界/重复 order fail loud；下游三者对齐同一题号。

## 5. Langfuse metadata

- [x] 5.1 `_answer_one_question` 的 `langfuse_context.update_current_trace` metadata 增加 `run_trace_id` 与 `component="sut"`。
- [x] 5.2 不新建 run trace、不改 session_id、不伪造 judge span。
- [x] 5.3 单测/手动：Langfuse 开启时 metadata 可见；关闭时不调用客户端。

## 6. 文档与验收

- [x] 6.1 `src/cli/README.md` 说明四组 ID（run / trace / measure / call）、`JUDGE_CALL` 与 `JUDGE_HTTP` 的分工、`note`→`error` / `seconds`→`latency_ms` 的字段变更、报告头与 v1 边界。
- [x] 6.2 `docs/agents/troubleshooting.md` 增加“评测 trace”入口：run_trace_id → 题 trace_id → judge_measure_id → judge_call_id；同步更新 `note` / `seconds` 的旧引用。
- [x] 6.3 `docs/agents/logging-rules.md` 登记 `JUDGE_CALL` / `JUDGE_HTTP` 字段、`[cli]` 前缀、取值与“不记 prompt/response 原文”的边界；同步更新 `note` / `seconds` 旧口径。
- [x] 6.4 `docs/agents/eval-metrics-troubleshooting.md` 增加“裁判/被测者 trace 怎么看”，并写明行为轴 judge 的 v1 盲区（无 active measure、不产 JUDGE_HTTP）。
- [x] 6.5 `src/cli/trace_logs.py` 的模块文档同步“hooks 成功路径也产日志”的现状。
- [x] 6.6 全量 `pytest tests/` + `ruff check src/cli src/infra src/core` 通过。
- [x] 6.7 活体冒烟：跑 `--limit 1`，确认同一题 `trace_id` 下能看到 `run_trace_id`、`judge_measure_id`、多条 `judge_call_id`、model/status/tokens（或 null）；报告头有 Run trace；`trace_logs <run_trace_id>` 能拿到题级锚点。
