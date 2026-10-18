## Context

评测的 trace 现状是“每题有一半，裁判那半缺失”：

- **SUT**：`generate_answers_and_contexts[_with_behaviour]` 给每题生成 `trace_id = eval_<12hex>`，set `current_trace_id`，日志 patcher 与 Langfuse 每题 trace 都用它；CSV 有 `trace_id` 列。
- **Judge**：`JUDGE_CALL` 日志显式带 `trace_id`，`HookEvidence` 也按 trace 归集 HTTP 事件；但 `score_rows` **没有 set `current_trace_id`**，所以 `BridgeDeepEvalLLM`、httpx hook、DeepEval 内部的 warning 日志可能丢失当前题 trace。
- **Judge 粒度**：`JUDGE_CALL` 是**每个指标每次 attempt 一条**；而一次 `metric.measure()` 内部有多条 HTTP 调用。按当前 DeepEval 实现（`include_reason=True`）：

  | 指标 | HTTP 调用 |
  |---|---|
  | Faithfulness | 4（truths / claims / verdicts / reason） |
  | AnswerRelevancy | 3（statements / verdicts / reason） |
  | ContextualPrecision | 2（verdicts / reason） |
  | ContextualRecall | 2（truths / reason） |
  | ContextualRelevancy | N+1（每条 context 一次，N ≤ 8；另加 reason） |
  | NonAdvice | 3（advices / verdicts / reason） |

  因此现有记录看不到“同一指标内部哪一步失败”，也看不到单次 HTTP 的 token/状态。
- **Run**：只有 `session_id` 与时间戳，没有 run 级 trace；22 个题的 trace 无法直接归属到同一轮；SUT 的题级日志也没有 run ID。
- **Behavior**：行为分从 `test_run_*.json` 回读；文件里有 0-based `order`，但 `read_behaviour_scores` 目前丢弃了它，只保留 `enumerate` 推出的 `q_index`。
- **Judge 字段**：`JUDGE_CALL` 只有 `metric/attempt/seconds/ok/note`，没有 `judge_measure_id/judge_call_id/model/status/finish_reason/tokens`；失败后无法回答“裁判用了哪个模型、HTTP 是否 2xx、是否超 token”。

本 change 只补 trace 关联与裁判调用观测，不改评分口径、不把完整 prompt/response 写日志。

## Goals / Non-Goals

**Goals:**

- 每次评测有 `run_trace_id`；每题有 `trace_id`；每次裁判指标打分有 `judge_measure_id`；每次裁判 HTTP 调用有 `judge_call_id`。
- 评分阶段设置 `current_trace_id`，让裁判桥内部 warning 与 SUT 使用同一题 trace。
- `JUDGE_CALL` 记录 measure 级汇总；`JUDGE_HTTP` 记录每次 HTTP 调用的 status / finish_reasons / tokens / latency。
- 行为分按 `testCases[].order` 关联到 `trace_id`，明确标 `link_method="order"`。
- Trace 默认 always-on；任何观测逻辑失败只 warning，不改变分数/门禁/退出码。
- 证据 change 可直接消费这些 ID 与 measure 结果，不再自己生成。

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

`EvalTraceContext`（dataclass：`run_id` / `run_trace_id` / `judge_model`）与 `current_eval_trace` ContextVar：`main()` 在 `build_bridge_judge` 之后创建，`judge_model` 取 `bridge_judge.model_name`（不读 settings 猜）；`score_rows` / `JUDGE_CALL` / Langfuse metadata 读取。

### D2：always-on、非评分、失败降级与对象生命周期

- `run_trace_id` 在 `main()` 解析出 `kb_id` 后立即生成；`EVALUATION_RUN` 在 testset 加载**之前**记录（含 `kb_id` / `run_id` / `run_trace_id`），使 testset 缺失/加载失败也有 run 锚点。
- `HookEvidence` 创建后立即 `set_run(run_trace_id)`；`reset()` **只清** `events` / `_trace_id` / `_measure` / `_call_index` / `measures`，**保留** `_run_trace_id`；证据采集器必须在本行 `reset()` 之前读取 `measures`。
- trace 设置/日志字段拼装失败只 `warning`；不得改变指标分数、门禁、退出码、CSV/DB。**观测逻辑不得向 httpx 或评分路径抛异常**（见 D5）。
- 不依赖 `--evidence`；evidence change 只负责消费。
- `EVALUATION_START`、`QA_ANSWER_START/DONE` 增加 `run_trace_id`，使 `trace_logs <run_trace_id>` 能枚举题级锚点。

### D3：评分阶段恢复每题 trace

`score_rows` 每题评分前：

```python
trace_id = row.get("trace_id")
token = current_trace_id.set(trace_id) if trace_id else None
try:
    ... 评分 ...
finally:
    if token is not None:
        current_trace_id.reset(token)
```

- `row` 允许没有 `trace_id`（现有测试/逃生门路径）；缺失时不得 `KeyError`，只跳过 trace 设置。
- `HookEvidence` 的 `reset/mark/begin_measure/end_measure` 仅在 `evidence is not None` 时调用。
- 所有 trace 相关操作包 `try/except`，失败只 warning。

### D4：两级裁判 ID 与两条日志

**为什么必须两级**：一个 `metric.measure()` 可能发 2–9 次 HTTP 请求。只记 measure 级一条会掩盖前面的 429/超时/截断，也无法回答“是 truths 还是 verdicts 那一步失败”。

**`JUDGE_CALL`（measure 级，每次 `metric.measure` 一条）**：

| 字段 | 来源 | 说明 |
|---|---|---|
| `run_trace_id` | `EvalTraceContext` | 整轮 ID |
| `judge_measure_id` | `<trace_id>:<metric>:<attempt>` | measure ID |
| `component` | 固定 `judge` | 区分 SUT / judge |
| `metric` / `attempt` | 调用参数 | 现有字段保留 |
| `q_index` / `trace_id` | 当前行 | 现有字段保留；缺失时 null |
| `model` | `EvalTraceContext.judge_model` | 裁判模型名 |
| `latency_ms` | `time.monotonic()` | 整个 measure 的墙钟（替代原 `seconds`） |
| `ok` | 是否拿到非 NaN 分数 | 现有语义保留 |
| `request_count` | 本次 measure 的 request 事件数 | 已发出的 HTTP 调用数 |
| `response_count` | 本次 measure 的 response 事件数 | 收到响应的调用数 |
| `statuses` | 按 `call_index` 排序的 status 列表 | 无 response 的位置写 `null`，长度等于 `request_count` |
| `tokens_in` / `tokens_out` | 各 response usage 求和 | 任一缺失则写 null（见 D5） |
| `error_count` | `(request_count - response_count) + 非 2xx response 数 + json_ok=false 数` | HTTP 层失败计数 |
| `reason_len` / `reason_hash` | measure **成功**返回后读 `metric.reason` | 见 D6 |
| `error` | 异常链摘要（最多 3 条，`;` 连接） | 替代原 `note`；无错为空串 |

**`JUDGE_HTTP`（HTTP 级，每次分配出去的调用一条）**：

| 字段 | 来源 | 说明 |
|---|---|---|
| `run_trace_id` | 证据桶 | 整轮 ID |
| `trace_id` / `q_index` | 证据桶 / 当前行 | 显式题锚点，不依赖日志 patcher 的 contextvar |
| `judge_measure_id` | 证据桶当前 measure | measure ID |
| `judge_call_id` | `<judge_measure_id>:<call_index>` | 本次调用 ID |
| `component` | 固定 `judge` | 区分 SUT / judge |
| `metric` / `attempt` | 证据桶当前 measure | 便于单独过滤 |
| `call_index` | 证据桶自增 | 1 起 |
| `status` | response | 真实 HTTP 状态；**无 response 时为 null** |
| `finish_reasons` | response body `choices[].finish_reason` | 列表；无则 null |
| `tokens_in` / `tokens_out` | response body `usage` | 无则 null |
| `latency_ms` | request hook 记录的开始时间到 response | 单次 HTTP；无 response 时为 null |
| `json_ok` | body 是否合法 JSON | 无 response 时为 null |
| `error` | `no_response` / body 解析摘要 / null | 失败证据 |

**传输层失败**（httpx 只派发 request/response，无异常 hook）：`on_response` 不会触发；由 `end_measure()` 对“有 request、无 response”的 `call_index` 补一条 `JUDGE_HTTP(status=null, error="no_response", tokens=null, latency_ms=null)`。这样每个分配出去的 `judge_call_id` 都有落点。

**attempt 与 call_index**：每次 attempt 是新的 measure，`judge_measure_id` 不同、`call_index` 从 1 重新开始。

**前缀**：`JUDGE_HTTP` 与 `JUDGE_CALL` 同用 `[cli]`，一次 grep 可取两级日志。

### D5：HookEvidence 的 measure 上下文、返回结构与异常隔离

不把 measure 上下文放进 ContextVar：`metric.measure()` 是同步入口，内部可能 `asyncio.run`/跨线程，ContextVar 不保证传进 async hook。改为在 `HookEvidence` 桶内维护**单槽状态**（评分串行、并发=1，与现有 `mark(trace_id)` 的前提一致）。

```python
class HookEvidence:
    def set_run(self, run_trace_id: str | None) -> None: ...
    def reset(self) -> None: ...          # 清 events/_trace_id/_measure/_call_index，保留 run

    def begin_measure(
        self, *, measure_id: str, metric: str, attempt: int,
        trace_id: str | None, q_index: int | None,
    ) -> None: ...

    def end_measure(
        self, *, reason: str | None, error: str
    ) -> dict:
        """返回并追加一条 measure 记录到 self.measures。"""
```

`end_measure()` 返回（同时 append 到 `self.measures`）：

```python
{
  "judge_measure_id": str,
  "metric": str,
  "attempt": int,
  "model": str | None,
  "trace_id": str | None,
  "q_index": int | None,
  "request_count": int,
  "response_count": int,
  "tokens_in": int | None,
  "tokens_out": int | None,
  "error_count": int,
  "calls": [
    {"judge_call_id", "call_index", "status", "finish_reasons",
     "tokens_in", "tokens_out", "latency_ms", "json_ok", "error"}
  ],
  "reason": str | None,
  "error": str,
}
```

- `on_request`：`self._call_index += 1`；生成 `judge_call_id`；把 measure/call/开始时间写入 `request.extensions["cb_judge"]`；追加 request 事件。
- `on_response`：从 `response.request.extensions["cb_judge"]` 取信息；解析 body 的 `usage` 与 `choices[].finish_reason`；计算 `latency_ms`；追加 response 事件并产 `JUDGE_HTTP`。
- **无 active measure**（行为轴等）：照常 append request/response 事件（保住 `main()` 的 hooks 断言），但**不分配 `call_index`、不写 `extensions`、不产 `JUDGE_HTTP`**。用 `has_active_measure` 判定。
- **异常隔离**：`on_request` / `on_response` 整个函数体包 `try/except Exception`，失败只 `logger.warning`，**永不向 httpx 抛出**；`log_event` 调用单独再包一层。`end_measure()`、`reason` 读取、`JUDGE_CALL` 拼装同样隔离；`end_measure` 异常不得覆盖原始 measure 异常。
- **usage 求和规则**：本次 measure 内所有 response 都有 `usage` → 求和；任一缺失 → 汇总 `null`，不把部分和冒充总量。
- **reset 语义**：`reset()` 不得清 `_run_trace_id`；`measures` 只保留当前题窗口，证据采集器在本题评分后、下一题 `reset()` 前读取。

### D6：日志里的 reason 与错误

- `reason_len`：`len(reason)`；`reason_hash`：`sha256(reason)[:16]`；不写完整 reason、不写 excerpt。
- `error`：异常链摘要（沿用 `_exc_chain_summary`），单行；不写 traceback。
- `JUDGE_CALL` 的 `reason_len` / `reason_hash` 只在 `metric.measure()` 正常返回后读取；异常分支不读 `metric.reason`（指标实例跨题复用，可能是上一题残留）。
- 完整 `reason` 只放 `end_measure()` 的返回 / `HookEvidence.measures`，供本地 evidence 消费，不进日志。

### D7：行为轴关联（order 为权威题号）

`read_behaviour_scores` 增加保留 `order`，并**校验 + 归一化**：

1. 读取 `testCase.order`；必须是 0-based 整数，且在 `[0, n)` 内，且无重复。
2. 校验通过后：`q_index = order`，并按 `order` 排序返回；使列表位置与 `q_index` 一致。
3. 缺失/非整数/越界/重复 → 抛 `RuntimeError`（fail loud），不猜、不按列表位置兜底。

这样 `fill_missing_behaviour_rows`（按 `q_index`）、`_build_behaviour_section`（按列表位置取 `trajectories[i]`）、`_write_langfuse_scores`（按列表位置 zip `trace_ids`）三处消费的是同一题号，不会脑裂。

新增纯函数：

```python
def link_behavior_traces(behaviour_rows, trace_ids) -> list[dict]:
    # 行内 q_index 已是权威题号；映射 trace_ids[q_index]
    # 越界/缺失 → trace_id=null, link_method="unmapped"（防御性，正常路径不会出现）
```

报告/证据用该映射；不修改 `test_run_*.json`。Markdown 行为段不新增 trace 列。

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

- 所有 trace 字段拼装、hook、日志调用包 `try/except`，失败只 warning。
- 回滚：删除 run_trace_id 生成、JUDGE_CALL/JUDGE_HTTP 新字段、score_rows contextvar 设置；不影响评分。
- 不新增 DB 迁移。

### D11：v1 边界（写进规格，避免误解）

| 对象 | v1 覆盖 | 不覆盖 |
|---|---|---|
| SUT（检索/生成/工具） | 每题 `trace_id` + `run_trace_id` 锚点 + Langfuse metadata | — |
| 主分/诊断裁判 | measure 级 + 每次 HTTP 调用（含无 response 的失败记录） | prompt 原文/截断标记 |
| 行为轴 | 行为分按 order 归一化后映射 `trace_id` | 行为 judge 内部调用（无 active measure，不产 JUDGE_HTTP） |
| Langfuse | SUT 每题 trace metadata | judge 独立 span / run trace |

### D12：测试策略

- 单测：`run_trace_id` 格式/唯一；`judge_measure_id` / `judge_call_id` 格式与自增；同一 measure 4 次 HTTP → 4 条 `JUDGE_HTTP` + 1 条 `JUDGE_CALL(request_count=4)`；同一指标两次 attempt → measure ID 不同且 `call_index` 从 1 重置。
- `score_rows` 评分后 `current_trace_id` 恢复；异常路径也恢复；连续两题不串；无 `trace_id` 的 row 不 KeyError；`evidence=None` 不 AttributeError。
- **真实 hook 接线**：用 `httpx.MockTransport` 走 `build_bridge_judge(evidence=...)`，覆盖 200+usage、429、`ConnectError`、非 JSON；断言 `JUDGE_HTTP` 条数/字段、`JUDGE_CALL` 汇总、无 response 的 `status=null` 失败记录。
- **并发乱序**：用 `asyncio.gather` 让两个请求以相反顺序完成，断言 `statuses` 按 `call_index` 对齐（缺失位 null）。
- **异常隔离**：注入会抛异常的 logger/encoder，断言评分与无 trace 时一致。
- **无 active measure**：先发一次 behavior-style 请求，断言 events 有 response、`JUDGE_HTTP` 为 0、不抛错。
- **reason**：第一题成功、第二题抛异常时，第二题不得复用第一题 reason。
- **行为映射**：乱序 `testCases` 经 `read_behaviour_scores` 归一化后 `q_index==order`；缺失/越界/重复 order → fail loud。
- Langfuse 关闭时仍生成 ID/日志，且不调用客户端。
- 回归：`trace_id` 格式、CSV 列、Langfuse session 行为不变。

## Risks / Trade-offs

- **日志量**：按 4/3/2/2/(N+1)/3 次调用估算，22 题约 500–700 条 `JUDGE_HTTP`/轮，单条数百字节，日轮转可接受；不采样，采样会破坏“每次调用可追”。
- **token 解析失败**：任一 response 无 usage → 汇总 tokens 为 null，不阻断。
- **contextvar 泄漏**：score_rows 用 try/finally 恢复；异常路径也恢复；用单测锁住。
- **measure 上下文串台**：`HookEvidence` 单槽依赖“评分串行、并发=1”；若未来并发评分，必须改为按 task 隔离。
- **行为 order 不可靠**：`read_behaviour_scores` 对缺失/重复/越界 fail loud；不再保留“按列表位置猜”的路径。
- **Langfuse v2**：只加 metadata，不假设可嵌套 trace/span；judge span 留后续。
- **与 evidence change 重复**：本 change 只产出 ID/日志/measure 记录；evidence change 只消费，不重复生成。
- **行为 judge 盲区**：v1 不追行为轴内部裁判调用；其失败证据仍可从 `test_run` 的 reason 获得。

## Migration Plan

1. 在 `eval_deepeval` 解析 `kb_id` 后生成 run_trace_id，提前记录 `EVALUATION_RUN`。
2. score_rows 设置/恢复 current_trace_id（`row.get` + 空值保护）。
3. HookEvidence 增加 set_run/measure 上下文/JUDGE_HTTP，扩展 JUDGE_CALL；更新事件字段登记与旧字段文档。
4. 行为 order 校验/归一化与 Langfuse metadata。
5. 更新文档/测试；默认 always-on，无需数据迁移。

## Open Questions

- 是否要把 `run_trace_id` 写进 CSV？暂不，避免 schema 变更；报告头/日志/证据已够。
- 是否要给 judge 建 Langfuse span？v1 不做；日志 + 两级 ID 已可追。
- 行为分是否需要 `trace_id` 写回 `test_run`？不改 DeepEval 产物；用外部映射。
