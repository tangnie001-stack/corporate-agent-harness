# trace-diagnosis P2b-1（D3 追溯）实施记录

**范围：** 只做 D3 有界追溯。RetrievalProbe（2.3）、窄协议（2.4）、`cited` 判定与原文展开不在本批。

## 设计要点

| 点 | 决定 |
|---|---|
| 触发 | `context budget high` / `history budget exceeded`；或零召回（含"完全没有 retrieve_kb 观测"）且问题含指代 |
| 数据源 | 同 session 的前序 trace（Langfuse 只读，`fetch_traces(session_id=…, to_timestamp=…)`）；预算标记读本地应用日志 |
| 取数上限 | `DIAG_TRACE_LOOKBACK_ROUNDS`（默认 2），按时间升序，最近一轮 `offset=-1` |
| 只取什么 | 该轮最后一次检索的 `result_count` / `context_count` / `score_kind`、metadata 里的模型名（缺失记 `N/A`，不猜） |
| 结论影响 | 仅一条：本轮未检索 + 上一轮被截 → `context_truncated`；其余只补 `H*` 证据与 `basis_rounds` |
| 失败处理 | 追溯异常 → warning 日志 + `rounds=()`，主结论不降级 |

## 落点

- `src/rca/diagnose/history.py`：`HistoryRound` / `should_lookback` / `HistorySource`
- `src/rca/diagnose/logs.py`：`BudgetFlags` + `flags_for_trace`（抽出 `_iter_window`，错误行与预算标记共用扫描）
- `src/rca/diagnose/sources.py`：抽 `parse_retrieval_calls`（当前 trace 与历史轮次同一解析）
- `src/cli/eval_trace_io.py`：新增 `fetch_session_traces`
- `src/rca/diagnose/{judge,pipeline,render}.py`：`prior` 判定、`H*` 证据与历史断言、卡片"结论基于前 N 轮"
- `src/agents/tools/diagnose_tools.py`：工具新增 `question` 入参（只用于指代判定，不交给模型决定路径）

## 验证

- `pytest` 2480 passed / 2 skipped；`ruff check .` 0；`pyright src/` 0 error
- 活体：`trace_afe90f74…` + `question="那它今年呢"` 跑通（该 session 只有这一条 trace，按设计不追溯）

## 已知边界

1. 模型名取 trace metadata 的 `model` 键；当前 trace metadata 没有该键，历史轮次也会显示 `N/A`——补字段属 turn-provenance 变更。
2. 预算标记只扫本地日志（7 天）；超窗口的历史轮次 `truncated=False`，属"未判定"而非"未发生"。
3. ~~`cited` 仍未知~~ —— 已补：`TraceSource` 从 trace 输出按 `\[\d+\]` 判定引用（与 format 节点同一规则），输出为空时仍记 `None` 不猜。


## 补充：cited 判定（2026-11-08）

- `TraceSource.fetch` 返回 `TraceFacts(calls, evidence, cited)`：`cited` 由 trace 输出的 `[n]` 标记判定；`output` 为空或非文本 → `None`（不猜）。
- `diagnose(cited=None)` 时取 `facts.cited`；显式传参优先（测试与批量回放可覆盖）。
- 影响：高分上下文 + 回答无 `[n]` → 判 `not_cited`，不再落"无法判断回答是否引用"。
- 活体验证：`trace_afe90f74…` 输出含 `[3]` → `cited=True`，结论由"无法判断"变为"证据不足以归入已定义根因"（该轮实际答对）。
