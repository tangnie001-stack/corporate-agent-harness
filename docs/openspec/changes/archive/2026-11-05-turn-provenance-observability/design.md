## Context

现状（已实机核对）：

- `agent_used` 事件在 `_run_generation` 进入图循环前**直接写缓冲**（`manager.add_event(...)`），**没有经 `_record_event(capture, ...)`** → 不进 `capture.events_log` → 不进 `process` 列 → **历史回放看不到**；前端只用它纠正顶栏。
- skill 的 inline 注入、首轮预加载、`/xxx` 三态分派**在日志与流里都没有信号**（仅失败有 `skill preload skip` / `skill direct skip`）。
- `[agent] model turn` 无 `temperature`；无"生效智能体""prompt 组成""skill 注入/分派"事件；`iteration done` 只有 `msgs=N` 总数。
- fork 轮已有可见反馈：`executor` 经共享 `ctx.clarify_channel` 投 `{"type":"delegate","skill":…}`，`_convert_event`（`agent_service.py:220` 附近）转 `SSEDelegateEvent`，前端渲染「`{skill} · 领域专家分析`」折叠区。
- 实测 `trace_38a02de6-…` 时无法从日志回答"温度/是否用了财务专家人设/skill 是否生效"，只能反查 `conversation_history` 里的 `[[skill-injection]]` 行。
- `process` 列持久化与回放由已归档能力 `session-process-replay` 承载（口径已同步到主 spec `specs/session-process-replay/spec.md`；实现为 `_EXCLUDED_TYPES` + `rebuildProcessFromEvents`），本 change 依赖其现状。

**实现落点核实（决定方案能否照原样实现）**：

- `direct_skill` 非空**无法区分** fork / unknown / 禁用 / 非 FORK 失败 → 需要显式模式承载。
- `_preload_skills_text()`（`agent_service.py:704`）与 `_preload_if_first_round()`（`:731`）**只返回拼接正文、不返回技能名** → 列名单需改返回值。
- `discipline_injected` / `delegate_injected` 是 `build_system_prompt`（`src/rag/prompt.py`）**内部**决定的，调用方只能靠字符串包含反推 → 日志落点需放在该函数内或改其返回值。
- `ITERATION_DONE` 在 `agent_node.py:144` 打印；第 ≥2 轮 `messages` 是累积的 `state.messages`，且组装后注入的 `HumanMessage` 与历史 `HumanMessage` **同类型不可区分** → 消息构成只能在组装点（`_initial_messages` / `build_prompt`）统计，不能挂在 `iteration done` 上。
- 历史消息已带 `model_name` 列（`src/api/sessions.py:144` 填充、`src/api/model/response.py:185` 字段，存量消息可为 null）→ 模型标注可**只靠既有列**在回放重建，无需动 `process` 口径。
- `attachHistoryCitations`（`chat.html:1476`）已是"从既有列重建回放同级元素"的现成模式（`sources` 列 → `insertAdjacentElement('afterend')`，`:1467`）→ 模型标注照此实现。

## Goals / Non-Goals

**Goals:**

- 每轮开头用 **SSE `status`**（复用既有事件类型）声明两条事实：生效智能体、本轮加载/使用的技能。
- 这两条事实**随 `process` 持久化并在历史回放中重现**。
- 历史回放的**模型标注**一并重建（读既有 `model_name` 列），使同一轮的来源信息在实时与回放两条路径上观感一致。
- 补齐日志缺口，使同一批事实**也能从日志直接判定**（诊断/成本/排障闭环）。

**Non-Goals:**

- 不新增 SSE 事件类型（复用 `status`）；不改 `agent_used` 的语义与用途。
- **温度不进 SSE**（属运维信息，只进日志）——这是有意决定，非遗漏。
- 不为 skill 状态行引入"跨轮仍在上下文"的隐式状态上报。
- 不做前端样式重设计（未知 stage 已被 UI 忽略；新行会与"正在思考..."相邻成组，是否强调样式见 Open Questions）。

## Decisions

### D1 复用 `status` 事件，不新增事件类型

- **理由**：`status` 已被前端实时渲染（`renderStatusTag`）**且**已被历史回放重建（`rebuildProcessFromEvents` 的 `status` 分支）、且不在 `_EXCLUDED_TYPES` 内 → 复用即自动获得"进 process + 可回放"，改动面最小。
- **备选**：新增 `skill_used` / `turn_provenance` 事件类型 → 需新增 dataclass + union + `to_sse` + `from_payload` + 前端 handler + 回放分支，收益仅是"结构化"，不值当。

### D2 `stage` 用**新专用值** `turn_agent` / `turn_skill`

- **理由**：`renderStatusTag()` 对 `stage === 'retrieve' || 'web_search'` 会触发"已见工具调用 → 待定正文归为旁白"的重分类（`toolCallSeen=true; flushPendingAsPreamble(); reclassifyNarrationAsPreamble()`）。新行若借用这两个 stage 会**把正文误判成旁白**。
- **备选**：复用 `STAGE_AGENT='agent'` → 放弃，会与既有"正在思考..."同组、无法区分、未来不能单独样式化。
- 回放分支只读 `message/detail`，不读 `stage` → 新增 stage 对回放无影响。

### D3 agent 文案用中文 `display_name`

- **理由**：`ctx.agent` 是 slug，用户要看到「财务专家」。`stream_chat` 已解析出 `session_preset`，顺手把显示名挂 `ctx`（与 `ctx.agent` 同路）。
- **边界**：未绑定（`effective_agent == ''`）→ **不发该行**；`display_name` 缺失 → 回落 `name`（slug）；registry 缺失不报错。

### D4 skill 一条列全 + 标点口径

- 文案：`成功加载 skills：{列表}`；列表按"命令顺序 / 预设声明顺序"去重。
- **标点定案**：全角冒号 `：` + **顿号 `、`** 分隔（与既有 `UNKNOWN_SKILL_PREFIX` 的「可用技能：a、b、c」风格一致，避免同一产品内两种列表标点）。可逆，如需半角逗号改一处模板即可。
- 一次调用可加载多个（首轮预加载可多个）；inline `/xxx` 通常 1 个。**两者互斥**：本轮有 inline 注入时 history 非空 → 预加载必然跳过，故一轮最多一组。

### D5 fork 轮发，但用**专门措辞**

- 文案：`使用技能：/{name}（子代理执行）`。
- **理由**：fork 不注入上下文，不是"加载"；同时保证"每轮开头统一声明"的完整性。
- **口径：声明表意图，结果另述**：fork 的 `skill_action` 在 `stream_chat`（`agent_service.py:851-852`）置位，早于子代理实际运行；子代理可能超时/中断，executor 未装配时还会被 `unavailable_skill_direct` 兜底成"不可直接执行"。此时**声明不撤回、不改写**——它表达"本轮触发了该技能"，结果由委派区（`delegate` 折叠区）或既有兜底文案呈现。（inline 无此问题：置位发生在同步注入成功之后。）
- **代价**：与 `delegate` 折叠区轻微重复（前者"用了什么"、后者"子代理怎么跑"）；接受。
- **备选**：fork 不发（靠 delegate 区）→ 放弃。

### D6 两条 status 必须**同时** `_record_event`（进 process / 可回放）

- **理由**：仅 `manager.add_event` 会重蹈 `agent_used` 覆辙（刷新即消失）；多写一行 `_record_event(capture, ev)` 即进 `process.events`，回放由既有 `status` 分支重建，**这两条声明本身前端零改动**（模型标注另有前端改动，见 D16）。
- 并以**回归断言**锁住（见 Risks），防后续改动 `_EXCLUDED_TYPES` 时静默失效。

### D7 顺序、时序与单次性

- 顺序：`agent_used`（结构化，顶栏）→ `[status] 当前使用了 X` → `[status] 成功加载 skills：…`（或 fork 的"使用技能"）→ 既有首个"正在思考..."。
- **时序约束**：两条 status 必须在**创建 clarify drain 任务之前**写入缓冲（与现有 `agent_used` 同点），否则并发的 drain 任务可能把 ask_user/delegate 事件插到前面，破坏 D7 顺序。
- 两条 status **每次请求各只发一次**（图循环之前），不随多轮迭代或 verify 重生成重复。

### D8 只声明"本轮动作"（含失败分支不发）

- 覆盖：本轮 inline `/xxx` 命中注入成功、首轮预加载解析成功、本轮 fork `/xxx`（措辞不同）。
- **不覆盖（必须显式不发）**：`unknown`（未注册）；`user-invocable:false` 禁用技能（`parse_prefix` 视作 unknown，`skill_direct.py:71` 出 SKILL_USER_DISABLED 文案）；`plain` 轮无技能；上一轮注入、本轮仍在上下文。
- `known 但非 FORK`（`SKILL_DIRECT_UNAVAILABLE`）**在当前枚举下不可达**：`SkillContext` 只有 inline/fork（`models.py:19-20`，非法值回落 inline），且 `user_visible()` 恰等于 `user_invocable`（`registry.py:74-78`），故"用户可调且已注册"的技能要么走 inline 注入（`direct_skill=""`）、要么是 fork（置 `direct_skill`）。该分支仅由 executor 未装配时的 `unavailable_skill_direct` 兜底覆盖；按"不发"处理即可。
- 这些分支已由既有兜底文案承载，再报一次是噪声。

### D9 用户可见文案入 `SSEInteractionTexts`；stage 常量入 const

- `AGENT_IN_USE_TMPL = "当前使用了 {agent}"`
- `SKILLS_LOADED_TMPL = "成功加载 skills：{skills}"`
- `SKILL_IN_USE_TMPL = "使用技能：/{skill}（子代理执行）"`
- `STAGE_TURN_AGENT = "turn_agent"`、`STAGE_TURN_SKILL = "turn_skill"`

### D10 双通道分工；文案行不得被解析回结构化值

| 通道 | 面向 | 内容 |
|---|---|---|
| SSE `status` | 用户 / UI（可回放） | 生效智能体、本轮技能（人类可读文本） |
| 日志 | 运维 / 诊断 | 温度、生效智能体及来源、prompt 组成、skill 注入/分派、消息构成 |

- `agent_used` 仍是**顶栏的唯一结构化来源**；前端 SHALL NOT 从文案行反解结构化值（防未来偷懒解析）。

### D11 日志补全（事件名英文空格分隔；新事件需两处同名登记）

| # | 事件 | prefix / level | 字段 |
|---|---|---|---|
| 1 | `model turn`（扩） | agent / info | +`temperature`、+`temp_source(explicit\|default)`、+`kb_bound` |
| 2 | `agent resolved`（新） | session / info（**仅 requested 或 bound 非空时记录**；两者全空不记） | `requested, bound, effective, source(bound\|new_bound\|ignored\|unregistered\|none), persona_applied` |
| 3 | `prompt assembled`（新） | llm / info | `persona_source(preset\|base), kb_bound, has_skills, discipline_injected, delegate_injected, system_msgs` |
| 4 | `prompt messages`（新） | agent / info | `system_msgs, injected_msgs, history_msgs` |
| 5 | `skill injected`（新） | session / info | `skill, mode(inline\|preload), chars, source(command\|preset)` |
| 6 | `skill dispatch`（新） | session / info（**仅 `kind=known`/`unknown` 记录**；`kind=plain` 不记） | `kind(plain\|known\|unknown), skill, context(inline\|fork\|none), direct_skill` |
| — | ~~`iteration done` 增消息数拆分~~ | **删除** | 见 Context 落点核实：迭代点拿不到构成，且注入/历史同为 HumanMessage；其意图并入 #4 |

- **为什么把 prompt 拆成两条事件**：`discipline_injected`/`delegate_injected` 只有 `build_system_prompt` 内部知道（注入事实），而 `injected_msgs`/`history_msgs` 只有 `_initial_messages` 在 `build_prompt` 返回并插入注入消息之后才知道（`agent_node.py:106-121`）。若合成一条，要么让 `build_system_prompt`/`build_prompt` 回传事实对象（改 3 个函数签名 + 6 处既有测试调用点），要么在调用方用字符串包含反推（脆弱）。故**拆两条**：`prompt assembled`（组成，组装点）+ `prompt messages`（条数，`_initial_messages`），各司其职、零签名改动。
- **噪声控制用调用点守卫，不用 debug**：`log_event(event, **fields)` 的事件级别由 `EventSpec` 固定（`src/core/logging.py:193-213`），**调用点无法逐次降级**；且 `_ALLOWED_LEVELS` 只允许 `info/warning/error`（`src/core/log_events.py:25`，import 期 `_validate_registry` 硬断言，`tests/core/test_log_events.py:14` 亦锁定）——故"降 debug"**不可实现**，改为**条件下不记**（`agent resolved` 全空不记、`skill dispatch` 的 plain 不记），噪声控制效果与"debug 默认关"等价，且不必改公共注册表。
- 只记长度/枚举/标识，**不记提示词正文**（日志脱敏约定）。
- `agent resolved` 的 `source` 必须由**解析函数本人**给出：`_resolve_session_agent`（`agent_service.py:876-912`）现只返回 `str`，改造为返回结构化结果（生效值 + 来源枚举），避免调用方重推五分支逻辑（漂移风险；符合"结构化数据优先于散落判断"）。
- `temperature` 必须**单一真源**：分档在 `agent_node.py:158-167`（绑 KB 不传参、沿用构造默认 `LLM_TEMPERATURE`；未绑 KB 内联 `NON_KB_MAIN_TEMPERATURE`），故在该分档处提取一个局部 `temperature` 变量，**同一变量既传给 LLM 调用也进日志**，并附 `temp_source` 区分"显式传参 / 沿用默认"——否则日志点的数值无法回答"实际传值是多少"。

### D12 发出位置

- `stream_chat`：把"本轮技能模式/名单"与"agent 显示名"挂到 `RequestContext`（与 `ctx.agent` 同路，graph 层不持有 registry）。
- `_run_generation`：在 `agent_used` 同点（drain 创建前）发两条 `SSEStatusEvent`，各自 `_record_event` + `manager.add_event`。

### D13 技能模式必须显式承载（新增字段）

- `RequestContext.skill_action: str = "none"`，取值 `none | inline | preload | fork | unknown`，由 `stream_chat` 依据 `parsed.kind` 与 `record.context` 决定：
  - `kind=known` 且 `context=INLINE` → `inline`
  - `kind=known` 且 `context=FORK` → `fork`
  - `kind=unknown`（含 `user-invocable:false` 禁用）→ `unknown`
  - `kind=plain` → `none`
  - 首轮预加载**实际注入且回传名单非空** → `preload`（返回空则保持 `none`，否则会发出空名单声明"成功加载 skills："）
- **理由**：`direct_skill` 非空无法区分 fork / unknown / 禁用 / 非 FORK 失败（见 Context）。`_run_generation` 只依据 `skill_action` 决定发不发、发哪条文案，**不自行判断 fork**。
- **名单承载（谁填 `loaded_skills`）**：由 `stream_chat` 与本轮动作**同点**填充——
  - `inline` → 命中记录的**规范名**（registry 的 `record.name`，非用户输入原文）；
  - `preload` → 预加载回传的名单（D14）；
  - `fork` / `unknown` / `none` → 留空（fork 的文案用 `direct_skill`，不经名单）。

### D14 预加载需回传"成功解析的技能名列表"

- `_preload_skills_text()` / `_preload_if_first_round()` 现只返回拼接正文；**定案**：改为返回 `tuple[str, list[str]]`（正文, 成功解析的技能名列表——去重、保序）。
- **理由**：D4 的"一条列全"与 spec 的"多技能列全"场景无数据来源。
- **代价**：调用点（`agent_service.py:750`、`:863-864`）与既有测试（`tests/services/test_preset_skill_preload.py` 6 处断言）需同批更新。

### D15 prompt 相关日志的落点（两条事件）

- `discipline_injected` / `delegate_injected` 只有 `build_system_prompt`（`src/rag/prompt.py:24`）自己知道 → `prompt assembled` 打在**该函数内**（注入事实最准、改动最小）。该模块目前是纯函数、**无 logger**（未 import logging）→ 需补模块级 logger，这是本 change 对它的唯一改动。
- `injected_msgs` / `history_msgs` 只有 `_initial_messages`（`src/agents/graph/agent_node.py:62`）在 `build_prompt` 返回**并插入注入消息之后**才知道 → `prompt messages` 打在**该函数内**。
- **不合并为一条**：合并需让 `build_system_prompt` / `build_prompt` 回传事实对象（改 3 个函数签名 + 6 处既有测试调用点），收益仅是"少一个事件名"。
- **备选**：调用方用字符串包含反推注入事实 → 放弃（脆弱、改文案即失效）。

### D16 历史回放重建模型标注（并入本 change，采用"读既有列"）

- 现状：`renderModelInfo`（"由 X 回答"）只从实时 `model_info` handler 调用（`chat.html:2277-2280`）；`model_info` 在 `_EXCLUDED_TYPES` 内（`src/chat/process_log.py:16`）→ `rebuildProcessFromEvents` 无该分支（`chat.html:3303`），历史回放不重建。
- 若不处理：本 change 后历史里"来源声明回放、模型标注消失"，同一批来源信息观感不一致。
- **决定**：并入本 change——历史渲染时用 `m.model_name` 在 AI 气泡行后追加模型标注，照 `attachHistoryCitations`（`chat.html:1476`）的 `insertAdjacentElement('afterend')`（`:1467`）模式，新增 `attachHistoryModelNote`，在 `loadSessionMessages`（`:3332`）内与引用栏同点调用。
- **为什么不是"把模型名并入来源声明行"**：来源声明在轮首发出（`agent_service.py:533`），实际模型名要等 LLM 返回才知道（`agent_node.py:187-196`）→ 只能填配置值、填不出实际值与 `is_fallback`；且 D10 已定"文案非结构化真源"。
- **不触碰**：`process` 口径、`_EXCLUDED_TYPES`、已归档能力 `session-process-replay` 的契约。
- **残余限制**：`is_fallback` 未持久化 → 回放标注不含 `fallback` 标记；`model_name` 为空/缺失（存量消息，`response.py:185` 注释明示 null）→ 不渲染、不补占位。

## Risks / Trade-offs

- **status 行与 delegate 折叠区重复（fork）** → 用不同措辞区分；接受轻微重复。
- **fork 声明与结果不一致（超时/中断/降级）** → D5 已定口径：声明表"本轮触发"、不撤回不改写；结果由委派区/兜底文案呈现。
- **误用 `retrieve`/`web_search` stage 会把正文判成旁白** → D2 固定专用值；测试断言 stage 值。
- **并发 drain 抢顺序** → D7 的时序约束（drain 创建前写入）。
- **依赖 `session-process-replay` 的 `_EXCLUDED_TYPES`** → 加**回归断言**（"两条来源声明必须出现在 `process.events`"），把它变成会红的安全网。
- **D14 改函数签名**会波及既有预加载测试 → 在任务里点名同批更新。
- **`process` JSON 增大** → 每轮仅 +2 帧，可忽略。
- **双通道重复（agent/skill 各出现一次）** → 有意为之（不同受众），文档写明防误删。
- **日志噪声** → D11 的调用点守卫（`skill dispatch` 的 plain 轮不记；`agent resolved` 无请求无绑定时不记）。
- **未绑定 agent / 无技能轮的"不发"口径** → spec 场景显式写明，防实现随手发空行。
- **回放模型标注的空值** → `model_name` 为空/缺失（存量消息）不渲染、不补占位；由 `chat-harness-ui` spec 场景锁定。

## Migration Plan

- 纯增量：无 DB 迁移（复用既有 `process` 列）；无 API 破坏（新增 stage 取值与消息文本，既有消费者忽略未知 stage）。
- 回滚：移除两条 status 的发出与日志埋点、移除 `deploy/nginx/html/chat.html` 的 `attachHistoryModelNote` 及其调用即可；无 DB/契约回滚。

## Open Questions

- 是否为新 stage 加专属样式（否则两条来源行会与"正在思考..."相邻成组、可能被淹没）—— 默认不做，留待观察后单独决定；已在 `chat-harness-ui` spec 请求"过程区最前"的位置约束。
- `unknown` / 非 FORK 失败轮是否要在流里给一行"未生效"提示 —— 默认不发（已有兜底文案）。
