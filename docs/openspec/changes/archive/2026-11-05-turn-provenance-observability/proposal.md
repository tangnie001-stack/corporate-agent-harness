## Why

每轮回答"由谁（智能体）、按哪本手册（skill）产生"这件事，目前对运维和用户都不可见：

- SSE 只回传 `agent_used`（会话绑定值），且它**直接写缓冲、不经 `events_log`**，所以既**不进口语流**（只驱动顶栏）、也**不随历史回放保留**；
- skill 的加载、`/xxx` 分派、注入在**日志与流里都是静默的**（仅在失败时才有 `skill preload skip` / `skill direct skip`）；
- 排障所需的关键事实（采样温度、人设是否真的生效、system prompt 由谁组成、本轮注入了哪些 skill）**日志里全缺**。

实测 `trace_38a02de6-1984-4184-89bc-03a9729ca956` 时，无法从日志判定温度、是否用了「财务专家」人设、以及 skill 是否生效——只能反查数据库里的 `[[skill-injection]]` 行。补上这些事实，既让用户"看得见"，也让排障"查得到"。

## What Changes

- **复用 SSE `status` 事件（不新增事件类型）** 在每轮开头声明两条事实：
  - 生效智能体：`当前使用了 财务专家`（采用预设 `display_name`；未绑定时不发）
  - 本轮加载的技能：`成功加载 skills：a、b、c`（一条列全、顿号分隔；无则不发出）
- **fork 轮单独措辞**：`使用技能：/financial-statement-analyzer（子代理执行）`——fork 不是"加载"（不注入上下文），故用"使用/执行"措辞，避免误导。声明表达**本轮触发**（意图）；fork 的实际结果（超时/中断/降级）由委派区或既有兜底文案呈现，**不改写、不撤回**该声明。
- **新增专用 `stage` 取值** `turn_agent` / `turn_skill`：**必须避开 `retrieve` / `web_search`**，因为前端 `renderStatusTag()` 对这两个 stage 会触发"已见工具调用 → 待定正文归为旁白"的重分类，用错会把正文误判成旁白。
- **两条 status 同时写入 `capture.events_log`**（`_record_event`），随 `process` 列持久化 → 历史回放自动重建（这两条声明本身前端零改动，回放已有 `status` 分支；模型标注的回放另有前端改动）。
- **只声明本轮动作**：inline `/xxx` 与首轮预加载；上一轮注入、本轮仍在上下文的不重复声明。
- **历史回放一并重建模型标注**：采用"读既有列"方案——历史渲染用 `m.model_name`（`api/sessions.py:144` 已返回）在气泡行后追加「由 X 回答」。**不触碰 `process` 口径、不改 `_EXCLUDED_TYPES`**，即不动已归档能力 `session-process-replay` 的契约；`is_fallback` 未持久化 → 回放标注不含 `fallback` 标记（登记为残余限制）。
- **补全可观测性日志缺口**（原 6 项，第 6 项经核实不可按原样实现、并入第 3 项）：
  1. `model turn` 增 `temperature`、`temp_source`（显式传参/沿用默认）、`kb_bound` 字段
  2. 新增 `agent resolved`（requested/bound/effective/来源/persona 是否生效；两者全空时不记）
  3. 新增 `prompt assembled`（人设来源 preset|base、kb_bound、has_skills、纪律/委派是否注入）与 `prompt messages`（system / 注入 / 历史三类消息条数）——拆两条：注入事实只有组装点可知、消息条数只有 `_initial_messages` 可知，合并需改 `build_system_prompt`/`build_prompt` 签名与 6 处既有测试
  4. 新增 `skill injected`（skill、mode inline|preload、字符数、来源 command|preset）
  5. 新增 `skill dispatch`（`kind` 命中/未注册两态的 skill、context inline|fork、direct_skill；**普通文本轮不记**）
  6. 原「`iteration done` 增消息数拆分」→ **删除**：`ITERATION_DONE` 在迭代点打印（第 ≥2 轮 `messages` 为累积态），且组装后注入消息与历史消息同为 `HumanMessage` 不可区分；其意图（消息构成）改由第 3 项的 `prompt messages` 在组装点承载
- **契约与文档同步**：`api_contract.md`（`status` 新 stage 取值与逐轮语义）、`logging-rules.md`（新事件登记）、`data-flow.md`（如需）。

## Capabilities

### New Capabilities

- `turn-provenance`: 每轮回答来源（生效智能体 + 本轮技能动作）的可声明与可回放——定义 SSE `status` 行的 stage 取值、文案模板、发出顺序与"只报本轮动作"的口径，以及该事实必须随 `process` 持久化并在历史回放中重现。

### Modified Capabilities

- `agent-loop-observability`: 补齐日志缺口（温度、生效智能体、prompt 组成与消息构成、skill 注入、skill 分派），使上述事实可从日志直接判定。
- `chat-harness-ui`: 消息流在每轮过程区开头新增两条"本轮来源"状态行（智能体 / 技能），含 fork 的专用措辞与无技能时不发出的约定；并**覆写既有 `消息流样式`**（主 spec 已要求"末尾标注模型名"，但历史回放未实现）——明确"实时与历史回放两条路径均重建、历史取自 `model_name` 列、空值不渲染占位、不含 `fallback` 标记"。
- `streaming-run`: 新增来源声明所用的 `status` 事件 `stage` 取值（`turn_agent` / `turn_skill`）、逐轮一次、顺序与"仅本轮动作"的流契约。

## Impact

- **代码**：`src/config/const.py`（stage 常量 + `SSEInteractionTexts` 三条模板）、`src/services/agent_service.py`（`stream_chat` 写入本轮技能模式/名单/显示名；`_preload_skills_text`/`_preload_if_first_round` **改返回 `tuple[str, list[str]]`**；`_resolve_session_agent` **改造为返回结构化（生效值 + source）**；`_run_generation` 发 status + 双 sink 写入）、`src/agents/graph/agent_node.py`（温度分档处提取单一真源变量；`model turn` 扩 `temperature`/`temp_source`/`kb_bound`）、`src/rag/prompt.py`（`prompt assembled` 落在 `build_system_prompt` 内，需补模块级 logger）、`src/infra/llm/request_context.py`（承载本轮技能模式/名单/显示名）、`src/core/log_events.py` + `src/core/log_event_specs.py`（新事件两处同名登记）。
- **前端**：`deploy/nginx/html/chat.html` — ① 来源声明本身**零改动**（`status` 已有实时渲染与回放分支）；② 新增 `attachHistoryModelNote`：历史回放时读既有 `model_name` 列在 AI 气泡行后追加「由 X 回答」，空值跳过，照 `attachHistoryCitations`（`:1476` → `insertAdjacentElement('afterend')`）的现成模式；③ 如为新 stage 加专属样式则改 CSS（默认不做）。
- **文档**：`docs/agents/api_contract.md`、`docs/agents/logging-rules.md`、必要时 `docs/agents/data-flow.md`。
- **依赖**：`process` 列的持久化与回放由已归档能力 `session-process-replay` 承载（口径已同步到主 spec `specs/session-process-replay/spec.md`）；本 change 依赖其 `_EXCLUDED_TYPES` 与 `rebuildProcessFromEvents` 现状，并以回归断言锁住。
- **测试**：`tests/services/test_agent_service.py`（两条 status 的文案/顺序/幂等）、`tests/services/test_preset_skill_preload.py`（返回 `(text, names)`）、`tests/agents/graph/test_agent_node.py`（`model turn` 温度三字段）、`tests/rag/test_prompt_layers.py`（`prompt assembled` 字段）、`tests/chat/test_process_log.py`（来源声明进入 `process.events`）、`tests/api/test_sessions.py`（无 `process` 存量消息回放不报错）、`tests/core/test_log_events.py`（两处登记一致）、`_resolve_session_agent` 五分支单测。
- **残余限制**：历史回放的模型标注只含模型名（取自 `model_name` 列），**不含 `fallback` 标记**——`is_fallback` 随 `model_info` 帧而来、不入 `process`；本 change 不为其新增持久化（会触碰已归档能力 `session-process-replay` 的口径）。
