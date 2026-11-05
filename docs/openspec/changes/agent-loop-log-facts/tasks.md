## 1. 事件登记

- [ ] 1.1 `src/core/log_events.py`（`Event`）与 `src/core/log_event_specs.py`（`EVENT_SPECS`）**双处同名登记** 5 个事件：`agent resolved`、`prompt assembled`、`prompt messages`、`skill injected`、`skill dispatch`；并给 `model turn` 的 `fields` 增 `temperature`、`temp_source`、`kb_bound`（**不改** `iteration done`）
- [ ] 1.2 `tests/core/test_log_events.py`：断言新事件在两处同名同集（import 期校验之外补一条显式用例）

## 2. 生成侧事实

- [ ] 2.1 `src/agents/graph/agent_node.py`：在温度分档处（`:158-167`）提取**单一真源**的局部 `temperature`（未绑 KB 用 `NON_KB_MAIN_TEMPERATURE`、绑 KB 用 `LLM_TEMPERATURE`），同一变量既传给 LLM 调用也进日志；`model turn`（`:194-203`）追加 `temperature` / `temp_source` / `kb_bound`
- [ ] 2.2 `agent_node._initial_messages`（`:62`）：在 `build_prompt` 返回并插入注入消息之后统计并记 `prompt messages`（system / 注入 / 历史三类条数）；**不为此改 `prompt.py` 函数签名**
- [ ] 2.3 `src/rag/prompt.py`：补模块级 logger（当前未 import logging），在 `build_system_prompt` 内记 `prompt assembled`（`persona_source`、`kb_bound`、`has_skills`、`discipline_injected`、`delegate_injected`）

## 3. 入口侧事实

- [ ] 3.1 `src/services/agent_service.py`：`_resolve_session_agent`（`:876-912`）改为返回结构化结果（生效值 + `source ∈ bound|new_bound|ignored|unregistered|none`）；调用方不再重推五分支
- [ ] 3.2 `agent resolved`：消费 3.1 的结果记 `requested / bound / effective / source / persona_applied`；请求值与绑定值都为空时**不记**
- [ ] 3.3 `_preload_skills_text`（`:704`）/ `_preload_if_first_round`（`:731`）返回类型改为 `tuple[str, list[str]]`（正文, 去重保序的技能名；解析失败的不入名单），同步更新调用点与既有断言
- [ ] 3.4 `skill injected`：两个注入点（`:845` inline / `:865` preload）记 `skill`、`mode(inline|preload)`、`chars`、`source(command|preset)`
- [ ] 3.5 `skill dispatch`：`parse_prefix` 之后记 `kind(plain|known|unknown)`、`skill`、`context(inline|fork|none)`、`direct_skill`；`kind=plain` 不记

## 4. 测试

- [ ] 4.1 `tests/agents/graph/test_agent_node_observability.py`：温度单一真源（日志值 == 传给模型的值，用 mock LLM 捕获）、`temp_source` 两态、`prompt messages` 三类条数之和等于实发条数
- [ ] 4.2 `tests/services/test_agent_resolved.py`：来源五分支各一例，空值时不记
- [ ] 4.3 `tests/services/test_preset_skill_preload.py`：预加载返回 `(text, names)`——多技能、保序、去重、查不到的不入名单（改造既有 6 处断言）
- [ ] 4.4 `tests/services/test_skill_observability.py`：`skill injected` 两来源、`skill dispatch` 三态且 plain 不记
- [ ] 4.5 `tests/rag/test_prompt_observability.py`：`prompt assembled` 五字段（人设来源、kb、skills、纪律、委派）

## 5. 文档与验收

- [ ] 5.1 `docs/agents/logging-rules.md`：登记 5 个事件（开放登记制）与 `model turn` 的三个新字段
- [ ] 5.2 活体验收：拿一条真实 `trace_id`，**只读日志**回答 proposal「验收判据」的四个问题，并把命令与输出贴进 PR 说明

## 6. 门禁

- [ ] 6.1 `POSTGRES_HOST=localhost pytest tests/ -q` 全绿；`ruff check .` 无新增；`pyright src/` 不引入新 error
- [ ] 6.2 确认无 `print()` / TODO / 调试代码残留
