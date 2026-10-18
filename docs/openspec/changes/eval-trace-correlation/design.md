## Context

评测的 trace 现状是“每题有一半，裁判那半缺失”：

- **SUT**：`generate_answers_and_contexts[_with_behaviour]` 给每题生成 `trace_id = eval_<12hex>`，set `current_trace_id`，日志 patcher 与 Langfuse 每题 trace 都用它；CSV 有 `trace_id` 列。
- **Judge**：`JUDGE_CALL` 日志显式带 `trace_id`，`HookEvidence` 也按 trace 归集 HTTP 事件；但 `score_rows` **没有 set `current_trace_id`**，所以 `BridgeDeepEvalLLM`、httpx hook、DeepEval 内部的 warning 日志可能丢失当前题 trace。
- **Judge 粒度**：`JUDGE_CALL` 是**每个指标每次 attempt 一条**；而一次 `metric.measure()` 内部通常有多次 HTTP 调用（实测：Faithfulness truths/claims/verdicts ≥3，AnswerRelevancy statements/verdicts=2，ContextualRelevancy 每条 context 一次最多 8，NonAdvice advices/verdicts=2）。因此现有记录看不到“同一指标内部哪一步失败”。
- **Run**：只有 `session_id` 与时间戳，没有 run 级 trace；22 个题的 trace 无法直接归属到同一轮。
- **Behavior**：行为分从 `test_run_*.json` 回读；文件里有 0-based `order`，但 `read_behaviour_scores` 目前丢弃了它，只保留 `enumerate` 推出的 `q_index`。
- **Judge 字段**：`JUDGE_CALL` 只有 `metric/attempt/seconds/ok/note`，没有 `judge_measure_id/judge_call_id/model/status/finish_reason/tokens`；失败后无法回答“裁判用了哪个模型、HTTP 是否 2xx、是否超 token”。

本 change 只补 trace 关联与裁判调用观测，不改评分口径、不把完整 prompt/response 写日志。

## Goals / Non-Goals

**Goals:**

- 每次评测有 `run_trace_id`；每题有 `trace_id`；每次裁判指标打分有 `judge_measure_id`；每次 HTTP 裁判调用有 `judge_call_id`。
- 评分阶段设置 `current_trace_id`，让裁判内部日志与 SUT 使用同一题 trace。
- `JUDGE_CALL` 记录 measure 级汇总；`JUDGE_HTTP` 记录每次 HTTP 调用的 status / finish_reason / tokens / latency。
- 行为分按 `testCases[].order` 关联到 `trace_id`，明确标 `link_method="order"`。
- Trace 默认 always-on；失败只 warning，不改变分数/门禁/退出码。
- 证据 change 可直接消费这些 ID，不再自己生成。

**Non-Goals:**

- 不改变任何评分、主分、门禁、CSV 列、`eval_report` schema。
- 不记录完整 judge prompt / response（隐私/体积）；日志只记长度与 hash，完整 reason 只进本地证据。
- 不新建 judge 的 Langfuse span（v1 log-only）。
- **行为轴 judge 不做到 per-call**：`TaskCompletion` / `StepEfficiency` 由 `evals_iterator` 内部评分，v1 只映射行为分到 `trace_id`。
- 不把行为分的 `trace_id` 写进 `test_run_*.json`（DeepEval 产物不改）；用外部映射。
- 不依赖 `--evidence` 开关；trace 是 always-on。
- 不记录 `prompt_len` / `prompt_truncated`（bridge 目前不提供；未来需要再另立字段）。

## Decisions

### D1：四组 ID 的命名与生命周期

| ID | 格式 | 范围 | 用途 |
|---|---|---|---|
| `run_trace_id` | `eval_run_<timestamp>_<8hex>` | 一次评测 | 串起整轮日志/报告/Langfuse metadata |
| `trace_id` | `eval_<12hex>`（现有） | 一题 | SUT 日志、CSV、Langfuse 每题 trace |
| `judge_measure_id` | `<trace_id>:<metric>:<attempt>` | 一次 `metric.measure` | 区分同一题、同指标的不同 attempt |
| `judge_call_id` | `<judge_measure_id>:<call_index>`（`call_index` 从 1 起） | 一次 HTTP 裁判调用 | 区分 measure 内部的多次裁判调用 |

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

### D4：两级裁判 ID 与两条日志

**为什么必须两级**：一个 `metric.measure()` 可能发 3–8 次 HTTP 请求。如果只记 measure 级一条，`status/tokens` 只能取最后一次，会掩盖前面的 429/超时/截断；也无法回答“是 truths 还是 verdicts 那一步失败”。

**`JUDGE_CALL`（measure 级，每次 `metric.measure` 一条）**：

| 字段 | 来源 | 说明 |
|---|---|---|
| `run_trace_id` | `EvalTraceContext` | 整轮 ID |
| `judge_measure_id` | `<trace_id>:<metric>:<attempt>` | measure ID |
| `component` | 固定 `judge` | 区分 SUT / judge |
| `metric` / `attempt` | 调用参数 | 现有字段保留 |
| `q_index` / `trace_id` | 当前行 | 现有字段保留 |
| `model` | `EvalTraceContext.judge_model` | 裁判模型名 |
| `latency_ms` | `time.monotonic()` | 整个 measure 的墙钟 |
| `ok` | 是否拿到非 NaN 分数 | 现有语义保留 |
| `request_count` / `response_count` | HookEvidence 本次 measure 窗口 | 传输失败时二者不等 |
| `http_call_count` | 本次 measure 的 response 事件数 | 等价 `response_count` |
| `statuses` | 各 response 的 status 列表 | 保留顺序，如 `[200, 200, 429]` |
| `finish_reasons` | 各 response 的 finish_reason 拍平 | 如 `[stop, stop, length]` |
| `tokens_in` / `tokens_out` | 各 response usage 求和；任一缺失则求和为 null 的规则见 D5 | 不伪造 |
| `error_count` | 非 2xx response 数 + 无 response 的 request 数 | HTTP 层失败计数 |
| `reason_len` / `reason_hash` | measure **成功**返回后读 `metric.reason` | 见 D6 |
| `error` | 异常链摘要（最多 3 条，`;` 连接） | 无错为空串 |

**`JUDGE_HTTP`（HTTP 级，每次收到 response 一条）**：

| 字段 | 来源 | 说明 |
|---|---|---|
| `run_trace_id` | 证据桶/上下文 | 整轮 ID |
| `judge_measure_id` | 证据桶当前 measure | measure ID |
| `judge_call_id` | `<judge_measure_id>:<call_index>` | 本次 HTTP 调用 ID |
| `component` | 固定 `judge` | 区分 SUT / judge |
| `metric` / `attempt` | 证据桶当前 measure | 便于单独过滤 |
| `call_index` | 证据桶自增 | 1 起 |
| `status` | response | 真实 HTTP 状态 |
| `finish_reason` | response body `choices[].finish_reason` | 无则 null |
| `tokens_in` / `tokens_out` | response body `usage` | 无则 null |
| `latency_ms` | request hook 记录的开始时间到 response | 单次 HTTP |
| `json_ok` | body 是否合法 JSON | 与现有 `HookEvidence` 一致 |
| `retry_after` | response header | 现有字段保留 |

**传输层失败没有 response hook**（httpx 只派发 request/response，无异常 hook）：这类调用不会产生 `JUDGE_HTTP`；由 `JUDGE_CALL` 的 `request_count > response_count` 与 `error` 体现。不得伪造 `status`。

**attempt 与 call_index 的关系**：每次 attempt 是一个新的 measure，`judge_measure_id` 不同、`call_index` 从 1 重新开始；同一题同一指标第 2 次 attempt 的 ID 必须与第 1 次不同。

### D5：HookEvidence 的 measure 上下文与 usage 解析

不把 measure 上下文放进 ContextVar：`metric.measure()` 是同步入口，内部可能 `asyncio.run` 或跨线程，ContextVar 不保证传进 async hook。改为在 `HookEvidence` 桶内维护**单槽状态**（评分串行、并发=1，与现有 `mark(trace_id)` 的前提一致）：

```python
class HookEvidence:
    def set_run(self, run_trace_id: str) -> None: ...

    def begin_measure(self, *, measure_id: str, metric: str, attempt: int) -> None:
        self._measure = {...}   # 清空本次窗口（不动 events 的全局 trace 窗口）
        self._call_index = 0

    def end_measure(self) -> dict:
        return {...}            # 返回 request/response 记录供 JUDGE_CALL 汇总
```

`on_request`：

- `self._call_index += 1`；
- 生成 `judge_call_id = f"{measure_id}:{call_index}"`；
- 把 `call_index` / `judge_call_id` / `time.monotonic()` 写入 `request.extensions["cb_judge"]`（随 request 进入 response hook）；
- 追加 `{"phase": "request", ...}` 到 events。

`on_response`：

- 从 `response.request.extensions["cb_judge"]` 取 measure/call 信息；
- 解析 body：`usage.prompt_tokens/completion_tokens`、`choices[].finish_reason`；
- 计算 `latency_ms`；
- 追加 response 事件，并直接 `log_event(Event.JUDGE_HTTP, ...)`。

**usage 求和规则**：

- 本次 measure 内所有 response 都有 `usage` → 求和；
- 任一 response 缺 `usage` 或解析失败 → 汇总写 `null`（不把部分和冒充总量），同时保留该条 `JUDGE_HTTP.tokens_*=null`。

**reason 读取时机**：`JUDGE_CALL` 的 `reason_len` / `reason_hash` 只在 `metric.measure()` 正常返回后读取；异常分支**不读** `metric.reason`（指标实例跨题复用，异常时可能是上一题残留）。完整 reason 不写日志，只由 evidence change 落本地文件。

### D6：日志里的 reason 与错误

- `reason_len`：`len(reason)`；`reason_hash`：`sha256(reason)[:16]`；可选 `reason_excerpt`：最多 120 字符、单行（`\n` 转义）。三选二即可，不得写完整 reason。
- `error`：异常链摘要（沿用 `_exc_chain_summary`），单行；不写 traceback。
- 不记录 prompt / response 原文；不记录 `prompt_len` / `prompt_truncated`（当前无可靠来源）。

### D7：行为轴关联

`read_behaviour_scores` 增加保留 `order`（来自 `testCase.order`，已核实 0-based、与 `q_index` 同源），输出行含 `q_index` / `order` / `input` / `metrics` / `reasons`。

新增纯函数：

```python
def link_behavior_traces(behaviour_rows, trace_ids) -> list[dict]:
    # 只按 row["order"] 映射 trace_ids[order]；order 缺失/非整数/越界 → trace_id=null,
    # link_method="unmapped"；不得回退到 enumerate 位置。
```

证据与 trace 查询用该映射；Markdown 行为段不新增 trace 列（保持现有报告形状）。不修改 `test_run_*.json`。若 order 越界/缺失，`trace_id` 写 null，不猜。

### D8：Langfuse metadata

每题 `langfuse_context.update_current_trace(...)` 增加 metadata：

```python
{"run_trace_id": run_trace_id, "component": "sut"}
```

不新建 run trace、不改 `session_id` 语义；judge 不作为独立 span（日志已有 `judge_measure_id` / `judge_call_id`）。如果未来要 judge span，另立 change。

### D9：报告与 CSV

- Markdown 报告头增加 `**Run trace:** <run_trace_id>`。
- CSV 保留现有 `trace_id` 列；不新增 `run_trace_id` 列（同一文件内恒定，避免 schema 变更）。
- `build_report_meta` 增加 `run_trace_id`；`save_markdown_report` 渲染。
- 不改 `detail_json` schema；evidence change 会把 trace 写进证据包。

### D10：失败/回滚

- 所有 trace 字段拼装包在 `try/except`，失败只 warning。
- 回滚：删除 run_trace_id 生成、JUDGE_CALL/JUDGE_HTTP 新字段、score_rows contextvar 设置；不影响评分。
- 不新增 DB 迁移。

### D11：v1 边界（写进规格，避免误解）

| 对象 | v1 覆盖 | 不覆盖 |
|---|---|---|
| SUT（检索/生成/工具） | 每题 `trace_id` + Langfuse metadata | — |
| 主分/诊断裁判 | measure 级 + 每次 HTTP 调用 | prompt 原文/截断标记 |
| 行为轴 | 行为分按 order 映射 `trace_id` | 行为 judge 内部调用（不经 `_score_metric_with_retry`） |
| Langfuse | SUT 每题 trace metadata | judge 独立 span / run trace |

### D12：测试策略

- 单测：`run_trace_id` 格式/唯一；`judge_measure_id` / `judge_call_id` 格式与自增；同一 measure 多次 HTTP → 多个 `JUDGE_HTTP`、一条 `JUDGE_CALL`；同一指标两次 attempt → measure ID 不同且 `call_index` 从 1 重置。
- `score_rows` 评分后 `current_trace_id` 恢复；异常路径也恢复；连续两题不串。
- `JUDGE_CALL` 字段（有/无 response）；HTTP 200 + usage；429；连接失败无 response（`request_count > response_count`、`statuses=[]`、`error` 非空）；非 JSON body。
- `reason`：第一题成功、第二题抛异常时，第二题不得复用第一题 reason（`reason_len=null`/`reason_hash=null`）。
- 行为映射：乱序 rows 仍按 `order` 映射；order 缺失/越界 → null + `unmapped`；不按位置猜。
- Langfuse 关闭时仍生成 ID/日志，且不调用客户端。
- 集成：fake judge + fake HookEvidence，跑一题，断言 `JUDGE_CALL` 与 `JUDGE_HTTP` 可通过 `judge_measure_id` 关联，且 `component=judge`。
- 回归：`trace_id` 格式、CSV 列、Langfuse session 行为不变。

## Risks / Trade-offs

- **日志量**：`JUDGE_HTTP` 每次裁判调用一条；22 题 × 4 指标 × 2–8 次 ≈ 数百条/轮，仍可控。只记状态/token，不落正文。
- **token 解析失败**：任一 response 无 usage → 汇总 tokens 为 null，不阻断；与现有 `json_ok` 口径一致。
- **contextvar 泄漏**：score_rows 用 try/finally 恢复；异常路径也恢复；用单测锁住。
- **measure 上下文串台**：`HookEvidence` 单槽依赖“评分串行、并发=1”；若未来并发评分，必须改为按 task 隔离（本 change 明确记录该前提）。
- **行为 order 不可靠**：order 缺失/越界写 null，不猜；link_method 明确。
- **Langfuse v2**：只加 metadata，不假设可嵌套 trace/span；judge span 留后续。
- **与 evidence change 重复**：本 change 只产出 ID/日志；evidence change 只消费，不重复生成。
- **行为 judge 盲区**：v1 不追行为轴内部裁判调用；其失败证据仍可从 `test_run` 的 reason 获得。

## Migration Plan

1. 在 `eval_deepeval` 生成 run_trace_id，写日志/报告头。
2. score_rows 设置/恢复 current_trace_id。
3. HookEvidence 增加 measure 上下文与 JUDGE_HTTP；扩展 JUDGE_CALL；更新事件字段登记。
4. 行为 order 映射与 Langfuse metadata。
5. 更新文档/测试；默认 always-on，无需数据迁移。

## Open Questions

- 是否要把 `run_trace_id` 写进 CSV？暂不，避免 schema 变更；报告头/日志/证据已够。
- 是否要给 judge 建 Langfuse span？v1 不做；日志 + 两级 ID 已可追。
- 行为分是否需要 `trace_id` 写回 `test_run`？不改 DeepEval 产物；用外部映射。
