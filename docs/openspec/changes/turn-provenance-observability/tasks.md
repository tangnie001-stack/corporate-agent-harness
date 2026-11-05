## 1. 常量、文案与事件登记

- [ ] 1.1 `src/config/const.py` 新增来源声明专用 `stage` 常量：`STAGE_TURN_AGENT = "turn_agent"`、`STAGE_TURN_SKILL = "turn_skill"`（行内注释写明"**不得复用 retrieve/web_search**，否则前端会触发正文/旁白重分类"）
- [ ] 1.2 `SSEInteractionTexts` 新增三条模板：`AGENT_IN_USE_TMPL = "当前使用了 {agent}"`、`SKILLS_LOADED_TMPL = "成功加载 skills：{skills}"`、`SKILL_IN_USE_TMPL = "使用技能：/{skill}（子代理执行）"`（**全角冒号 + 顿号 `、`**，与既有 `UNKNOWN_SKILL_PREFIX`「可用技能：a、b、c」及 `skill_direct.py:59` 的 `"、".join()` 一致）
- [ ] 1.3 `src/core/log_events.py`（`Event`）与 `src/core/log_event_specs.py`（`EVENT_SPECS`）**两处同名登记** 5 个新事件：`agent resolved`、`prompt assembled`、`prompt messages`、`skill injected`、`skill dispatch`；并给 `model turn` 的 `fields` 增 `temperature`、`temp_source`、`kb_bound`（**不改** `iteration done`）
- [ ] 1.4 `tests/core/test_log_events.py` 断言新事件在两处同名同集（import 期校验覆盖即可，补一条显式用例）

## 2. 来源声明的数据承载

- [ ] 2.1 `src/infra/llm/request_context.py` 新增三字段（含行内注释）：`agent_display_name: str = ""`、`loaded_skills: list[str] = field(default_factory=list)`、`skill_action: str = "none"`（枚举：`none|inline|preload|fork|unknown`）
- [ ] 2.2 `AgentService.stream_chat` 填 `skill_action` 与 `loaded_skills`（同一处，保证二者一致）：
  - `kind=known` + `context=INLINE` → `skill_action="inline"`，`loaded_skills=[命中记录的规范名 record.name]`（**非用户输入原文**）
  - `kind=known` + `context=FORK` → `skill_action="fork"`，`loaded_skills=[]`（文案用 `direct_skill`）
  - `kind=unknown`（含 `user-invocable:false` 禁用与未注册）→ `skill_action="unknown"`，名单留空
  - `kind=plain` → `skill_action="none"`，名单留空
  - 首轮预加载**实际注入且回传名单非空** → `skill_action="preload"`，`loaded_skills=` 预加载回传名单（见 2.3）；**返回空则不置位（保持 `none`）**，否则会发出空名单声明"成功加载 skills："
- [ ] 2.3 `_preload_skills_text()` / `_preload_if_first_round()` 返回类型改为 `tuple[str, list[str]]`（正文, 成功解析的技能名列表——去重、保序；查不到的不入名单）；同步更新调用点 `agent_service.py:750`、`:863-864` 与 `tests/services/test_preset_skill_preload.py` 的 6 处既有断言
- [ ] 2.4 `stream_chat` 填 `agent_display_name`：取 `session_preset.display_name`（`:827-831` 已解析），缺失回落 `name`；未绑定留空（registry 缺失不报错）
- [ ] 2.5 `_resolve_session_agent`（`:876-912`）改造为返回**结构化结果**（生效值 + `source ∈ bound|new_bound|ignored|unregistered|none`），调用方不再重推五分支；同步更新受影响单测

## 3. 每轮来源声明发出（复用 SSE `status`）

- [ ] 3.1 `_run_generation` 在既有 `agent_used` 同点（`agent_service.py:533-534`，**创建 clarify drain 任务 `:537` 之前**）发智能体声明：`SSEStatusEvent(stage=STAGE_TURN_AGENT, message=AGENT_IN_USE_TMPL.format(agent=ctx.agent_display_name))`；`agent_display_name` 为空时不发
- [ ] 3.2 紧随其后发技能声明（每轮至多一条，依 `ctx.skill_action`）：`inline`/`preload` → `stage=STAGE_TURN_SKILL` + `SKILLS_LOADED_TMPL.format(skills="、".join(ctx.loaded_skills))`；`fork` → `SKILL_IN_USE_TMPL.format(skill=direct_skill)`；`none`/`unknown` → 不发
- [ ] 3.3 两条声明**各自** `_record_event(capture, ev)`（只写 `capture.events_log`）+ `manager.add_event(session_id, ev.type, ev.payload_for_buffer())`（只写 SSE 缓冲）——两 sink 并列、非重复写入（核实依据：`_record_event` 定义 `:354-365` 仅 append `capture.events_log`；既有双 sink 先例 `:544-545`）；使其进入 `capture.events_log` → 随 `process` 列持久化 → 历史回放重建

## 4. 补全日志

- [ ] 4.1 `agent_node`：在温度分档处（`:158-167`）提取**单一真源**的局部 `temperature` 变量（未绑 KB 用 `NON_KB_MAIN_TEMPERATURE`、绑 KB 沿用构造默认 `LLM_TEMPERATURE`），同一变量既传给 LLM 调用也进日志；`model turn`（`:194-203`）追加 `temperature`、`temp_source(explicit|default)`、`kb_bound`
- [ ] 4.2 新增 `agent resolved`：消费 2.5 的结构化结果，记 `requested/bound/effective/source/persona_applied`；**请求值与绑定值都为空时用调用点守卫「不记」**（避免每轮噪声；`log_event` 级别由 `EventSpec` 固定、调用点不能逐次降级，故不用 debug，见 design D11）
- [ ] 4.3 新增 `prompt assembled`：落点 **`build_system_prompt`（`src/rag/prompt.py:24`）函数内**——先为该模块补模块级 logger（现**未 import logging**）；字段 `persona_source(preset|base)`、`kb_bound`、`has_skills`、`discipline_injected`、`delegate_injected`、`system_msgs`（注入/历史条数此处不可知，见 4.4）
- [ ] 4.4 新增 `prompt messages`：落点 **`_initial_messages`（`src/agents/graph/agent_node.py:62`）**，在 `build_prompt` 返回**并插入注入消息之后**统计 `system_msgs`、`injected_msgs`、`history_msgs`（**不为此改 `prompt.py` 函数签名**，否则波及 6 处既有测试调用点）
- [ ] 4.5 新增 `skill injected`：在 `_inject_skill_message` 两个调用点（`agent_service.py:845` inline / `:865` preload）记录 `skill`、`mode(inline|preload)`、`chars`、`source(command|preset)`；preload 多技能时 `skill` 取顿号连接的名列表
- [ ] 4.6 新增 `skill dispatch`：在 `parse_prefix` 之后记录 `kind(plain|known|unknown)`、`skill`、`context(inline|fork|none)`、`direct_skill`；**仅 `kind=known`/`unknown` 调用 `log_event`，`kind=plain`（普通文本轮）用调用点守卫不记**（同 4.2 之因）
- [ ] 4.7 不实现原「`iteration done` 消息数拆分」（迭代点拿不到构成、注入与历史同为 `HumanMessage`）；其意图由 4.4 的 `prompt messages` 承载

## 5. 测试（按落点点名）

- [ ] 5.1 `tests/services/test_agent_service.py`：智能体声明（已绑定→文案=display_name；未绑定→不发）；技能声明（inline 单、fork 措辞、unknown/禁用技能/无技能→不发；`非FORK` 分支当前不可达，见 design D8，不为其造用例）；顺序（`agent_used`→`turn_agent`→`turn_skill`→首个节点状态行）；多轮迭代不重复
- [ ] 5.2 `tests/services/test_preset_skill_preload.py`：预加载返回 `(text, names)`（多技能、声明顺序、去重；查不到的不入名单）
- [ ] 5.3 `tests/agents/graph/test_agent_node.py` + `tests/rag/test_prompt_layers.py`：`prompt assembled` 字段（人设来源、条件注入）；`prompt messages` 三类消息条数；`model turn` 的 `temperature`/`temp_source`/`kb_bound`（未绑 KB 与绑 KB 两态各自的值与来源）
- [ ] 5.4 `tests/chat/test_process_log.py`：两条来源声明出现在 `process.events`（回归断言，锁住"可回放"不被 `_EXCLUDED_TYPES` 变化破坏）
- [ ] 5.5 `tests/core/test_log_events.py`：新事件两处登记一致
- [ ] 5.6 **后端**（`tests/api/test_sessions.py` 或 `tests/chat/test_process_log.py`）：`process` 缺失/存量消息不报错、不补占位（**只覆盖后端**；前端回放行为由 6.3 与 8.2 的 playwright 覆盖）
- [ ] 5.7 单测 `_resolve_session_agent` 的 `source` 五分支（bound / new_bound / ignored / unregistered / none）

## 6. 前端：历史回放模型标注

- [ ] 6.1 `deploy/nginx/html/chat.html` 新增 `attachHistoryModelNote(row, modelName)`：照 `attachHistoryCitations`（`:1476`）的 `insertAdjacentElement('afterend', …)`（`:1467`）模式，在 AI 气泡行后追加 `.model-note`「由 <b>{model}</b> 回答」；`modelName` 为空/缺失则**直接返回、不渲染**；**不重建 `fallback` 标记**（未持久化，D16 残余限制，函数内注释写明）
- [ ] 6.2 在 `loadSessionMessages`（`:3332`）内、与 `attachHistoryCitations`（调用点 `:3357`）同点接线：`attachHistoryModelNote(lastAiRow(), m.model_name)`（用 `escapeHtml` 转义）
- [ ] 6.3 手动校验：切换到一个历史会话 → 气泡行后出现模型标注；服务端返回空 `model_name` 时不出现空行；多个历史轮各自的标注落在各自气泡之后（不串轮）

## 7. 文档同步

- [ ] 7.1 `docs/agents/api_contract.md`：登记 `status` 的新 `stage` 取值（`turn_agent`/`turn_skill`）、逐轮语义、顺序与时机（早于澄清/委派）、"文案非结构化真源"，以及"失败/无技能不发""声明表意图、结果另述"的口径；登记历史消息 `model_name` 被前端回放消费（模型标注）；并写明**评审约束**：前端顶栏生效智能体必须继续取自 `agent_used`（结构化真源），**不得**从来源声明文案反解
- [ ] 7.2 `docs/agents/logging-rules.md`：登记 5 个新事件与 `model turn` 扩字段（含 `temp_source`）；注明**不再扩 `iteration done`** 的原因（避免后人再提）
- [ ] 7.3 `docs/agents/data-flow.md`：如新增/变化了链路则同步（否则跳过并说明）
- [ ] 7.4 如为新 `stage` 增加专属样式，则同步 `docs/design/pages/chat-agent-skill-selector-2026-09-11.md` 与 `MASTER.md`（默认不做）

## 8. 门禁与实机验证

- [ ] 8.1 `pytest tests/ -q` 全绿、`ruff check .` 无错、`pyright src/` 不新增 error、`python -m src.cli.check_docs` 0 error
- [ ] 8.2 `playwright-cli` 实机：选「财务专家」发一轮（或 inline `/xxx`）→ 过程区最前出现两条来源声明；**刷新/切会话后**两条声明与**模型标注**均可回放；fork 轮显示"使用技能…（子代理执行）"；**未知 stage 容忍**（两条声明作为普通状态行正常渲染、不改变正文/旁白判定）、顶栏仍显示生效智能体（取自 `agent_used`）；控制台无新增 error
- [ ] 8.3 用一条真实 trace（含 agent + skill）复核：日志可直接读出温度及其来源、生效智能体与来源、prompt 组成与消息构成、技能注入与分派
