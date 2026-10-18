## Context

评测的 trace 现状是“每题有一半，裁判那半缺失”：

- **SUT**：`generate_answers_and_contexts[_with_behaviour]` 给每题生成 `trace_id = eval_<12hex>`，set `current_trace_id`，日志 patcher 与 Langfuse 每题 trace 都用它；CSV 有 `trace_id` 列。
- **Judge**：`JUDGE_CALL` 日志显式带 `trace_id`，`HookEvidence` 也按 trace 归集 HTTP 事件；但 `score_rows` **没有 set `current_trace_id`**，所以 `BridgeDeepEvalLLM`、httpx hook、DeepEval 内部的 warning 日志可能丢失当前题 trace。
- **Run**：只有 `session_id` 与时间戳，没有 run 级 trace；22 个题的 trace 无法直接归属到同一轮。
- **Behavior**：行为分从 `test_run_*.json` 回读，只有 `order/input`，没有显式 `trace_id`；只能按 order 推断。
- **Judge 字段**：`JUDGE_CALL` 只有 `metric/attempt/seconds/ok/note`，没有 `judge_call_id/model/status/finish_reason/tokens`；失败后无法回答“裁判用了哪个模型、HTTP 是否 2xx、是否超 token”。

本 change 只补 trace 关联与裁判调用观测，不改评分口径、不把完整 prompt/response 写日志。

## Goals / Non-Goals

**Goals:**

- 每次评测有 `run_trace_id`；每题有 `trace_id`；每次 judge 调用有 `judge_call_id`。
- 评分阶段设置 `current_trace_id`，让裁判内部日志与 SUT 使用同一题 trace。
- `JUDGE_CALL` 记录 model / status / finish_reason / tokens / latency / reason / error。
- 行为分按 `q_index + order` 关联到 `trace_id`，明确标 `link_method="order"`。
- Trace 默认 always-on；失败只 warning，不改变分数/门禁/退出码。
- 证据 change 可直接消费这些 ID，不再自己生成。

**Non-Goals:**

- 不改变任何评分、主分、门禁、CSV 列、`eval_report` schema。
- 不记录完整 judge prompt / response（隐私/体积）；只记长度、模型、token、状态、原因。
- 不新建 judge 的 Langfuse span（v1 先保证日志/ID 可追；span 化留后续）。
- 不把行为分的 `trace_id` 写进 `test_run_*.json`（DeepEval 产物不改）；用外部映射。
- 不依赖 `--evidence` 开关；trace 是 always-on。

## Decisions

### D1：三组 ID 的命名与生命周期

| ID | 格式 | 范围 | 用途 |
|---|---|---|---|
| `run_trace_id` | `eval_run_<timestamp>_<8hex>` | 一次评测 | 串起整轮日志/报告/Langfuse metadata |
| `trace_id` | `eval_<12hex>`（现有） | 一题 | SUT 日志、CSV、Langfuse 每题 trace |
| `judge_call_id` | `<trace_id>:<metric>:<attempt>` | 一次指标尝试 | 区分同一题的多次裁判调用 |

不重命名现有 `trace_id`，避免破坏 CSV/日志/Langfuse 既有消费方。`run_id` 仍是时间戳，`run_trace_id` 是新增的 trace 键。

新增 `EvalTraceContext`（dataclass：`run_id` / `run_trace_id` / `judge_model`）与 `current_eval_trace` ContextVar：`main()` 设置，`score_rows` / `JUDGE_CALL` / Langfuse metadata 读取；不改现有 `current_trace_id` 的语义。

### D2：always-on、非评分、失败降级

- `run_trace_id` 在 `main()` 开始生成，写入 `EVALUATION_RUN` 日志与报告头。
- trace 设置/日志字段拼装失败只 `warning`；不得改变指标分数、门禁、退出码、CSV/DB。
- 不依赖 `--evidence`；evidence change 只负责消费。

### D3：评分阶段恢复每题 trace

`score_rows` 每题评分前：

```python
token = current_trace_id.set(row["trace_id"])
try:
    ... 评分 ...
finally:
    current_trace_id.reset(token)
```

这样 `BridgeDeepEvalLLM`、httpx hook、DeepEval 内部 warning 都带当前题 `trace_id`；`JUDGE_CALL` 仍显式写 `trace_id`。不得只在 `JUDGE_CALL` 里写 trace 而不设置 contextvar。

### D4：JUDGE_CALL 字段

在现有 `JUDGE_CALL` 上增加：

| 字段 | 来源 | 说明 |
|---|---|---|
| `run_trace_id` | `EvalTraceContext` | 整轮 ID |
| `judge_call_id` | `<trace_id>:<metric>:<attempt>` | 本次调用 ID |
| `component` | 固定 `judge` | 区分 SUT / judge |
| `model` | `EvalTraceContext.judge_model` | 裁判模型名 |
| `status` | `HookEvidence` response | HTTP 状态；无 response 时 null |
| `finish_reason` | `HookEvidence` response | 如 stop/length；无则 null |
| `tokens_in` / `tokens_out` | response `usage` | 无则 null |
| `latency_ms` | `time.monotonic()` | 本次 attempt 墙钟 |
| `reason` | `metric.reason` 或错误摘要 | 截断单行，不落全文 |
| `error` | 异常链摘要 | 无错为空串 |

`prompt_len` / `prompt_truncated` 若 bridge 能提供则记录，否则 null；不记录 prompt 原文。

### D5：HookEvidence 补 usage/状态

`http_observability.py` 的 `on_response` 解析 body 后增加：

- `usage` → `prompt_tokens` / `completion_tokens`；
- `status` / `finish_reason`（已有）；
- 可选 `model`（body 里的 model 字段）。

`_score_metric_with_retry` 用 `event_start` 取本次 attempt 的事件窗口，取最后一个 `phase=response` 事件汇总到 `JUDGE_CALL`；无 response 时 status/tokens 为 null、error 保留异常摘要。不要为日志重放/重试。

### D6：行为轴关联

`read_behaviour_scores` 已按 `testCase.order` 输出逐题行；新增纯函数：

```python
def link_behavior_traces(behaviour_rows, trace_ids) -> list[dict]:
    # 按 order/q_index 映射；返回带 trace_id / link_method="order" 的行副本
```

报告/证据用该映射；不修改 `test_run_*.json`。若 order 越界/缺失，trace_id 写 null，不猜。

### D7：Langfuse metadata

每题 `langfuse_context.update_current_trace(...)` 增加 metadata：

```python
{"run_trace_id": run_trace_id, "component": "sut"}
```

不新建 run trace、不改 `session_id` 语义；judge 不作为独立 span（日志已有 `judge_call_id`）。如果未来要 judge span，另立 change。

### D8：报告与 CSV

- Markdown 报告头增加 `**Run trace:** <run_trace_id>`。
- CSV 保留现有 `trace_id` 列；不新增 `run_trace_id` 列（同一文件内恒定，避免 schema 变更）。
- `build_report_meta` 增加 `run_trace_id`；`save_markdown_report` 渲染。
- 不改 `detail_json` schema；evidence change 会把 trace 写进证据包。

### D9：失败/回滚

- 所有 trace 字段拼装包在 `try/except`，失败只 warning。
- 回滚：删除 run_trace_id 生成、JUDGE_CALL 新字段、score_rows contextvar 设置；不影响评分。
- 不新增 DB 迁移。

### D10：测试策略

- 单测：run_trace_id 格式/唯一；judge_call_id 格式；`score_rows` 评分后 `current_trace_id` 恢复；JUDGE_CALL 字段（有/无 response）；HTTP usage 解析；非 2xx；行为 order 映射；Langfuse 关闭时仍生成 ID/日志。
- 集成：fake judge + fake HookEvidence，跑一题，断言 `JUDGE_CALL` 同时含 `trace_id`、`run_trace_id`、`judge_call_id`、`component=judge`。
- 回归：`trace_id` 格式、CSV 列、Langfuse session 行为不变。

## Risks / Trade-offs

- **日志膨胀**：JUDGE_CALL 字段变多，但每指标每次 attempt 一条，量可控；不落 prompt/response。
- **token 解析失败**：response 非 JSON/无 usage 时写 null，不阻断；与现有 `json_ok` 口径一致。
- **contextvar 泄漏**：score_rows 用 try/finally 恢复；异常路径也恢复；用单测锁住。
- **行为 order 不可靠**：order 缺失/越界写 null，不猜；link_method 明确。
- **Langfuse v2**：只加 metadata，不假设可嵌套 trace/span；judge span 留后续。
- **与 evidence change 重复**：本 change 只产出 ID/日志；evidence change 只消费，不重复生成。

## Migration Plan

1. 在 `eval_deepeval` 生成 run_trace_id，写日志/报告头。
2. score_rows 设置/恢复 current_trace_id。
3. 扩展 JUDGE_CALL/HookEvidence；更新事件字段登记。
4. 行为 order 映射与 Langfuse metadata。
5. 更新文档/测试；默认 always-on，无需数据迁移。

## Open Questions

- 是否要把 `run_trace_id` 写进 CSV？暂不，避免 schema 变更；报告头/日志/证据已够。
- 是否要给 judge 建 Langfuse span？v1 不做；日志 + judge_call_id 已可追。
- 行为分是否需要 `trace_id` 写回 `test_run`？不改 DeepEval 产物；用外部映射。
