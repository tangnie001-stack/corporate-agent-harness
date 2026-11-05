## Context

当前企微 4 台机器人共用一条生成管线：handler 不传 agent，`start_turn` 以空 agent 进入，prompt 落到 `general` 人设；工具面由编译期 `build_graph(tools=...)` 固定，与预设无关。

告警链路是第四个编排入口，它先取数（MCP span）后生成，但 trace 由 `_run_generation` 的 `@observe` 在生成开始时创建，导致 trace 起点晚于其子 span（实测 3.2s）。

约束：

- 图在启动期编译，工具列表编译期固定；每轮换工具需重建 Agent，成本高且不可回归
- **工具面与轮次预算在编译期固化**（`AgentLoopBundle.tool_names`，`workflow.py:117`），**人设不固化**（运行期经 `ctx.persona` + `SystemMessagesMiddleware` 施加）；因此 profile 换的是实例与 bundle，不是重编 prompt
- 会话 agent 为 bind-once（`sessions.agent`），换角色只能新会话
- 企微侧已用「机器人可见范围」控制谁能使用，应用层不再重复做群/人级白名单
- 落库只写 user 消息与净化后的 assistant 回答，工具结果不跨轮

## Goals / Non-Goals

**Goals:**

- 入口到 Agent 的分配显式化、可配置、可回滚
- ops/dev 能查只读日志，且工具不出现在其他 Agent 的能力面里
- 渠道专属 Agent 对站点不可见、不可调用
- trace 起点、标签、metadata 三者一致且可筛选

**Non-Goals:**

- 调度 Agent / supervisor：路由在入口处确定，不需要模型分发
- 每个 bot 一张外图：外图编排与角色无关
- KB per bot、KB Agent as Tool
- 渐进式 MCP / call_tool 代理
- 群/人级日志权限（依赖企微可见范围）

## Decisions

### D1 路由在入口处确定性完成

按「渠道 + 渠道内身份」查表：站点取请求 `agent`（缺省 `chat`），企微取 `bot_key` 的配置，告警固定标签 `alert`。

`alert` 只是路由标签（用于 trace 与日志），复用通用人设与无澄清图，**不装配独立 Agent 实例**——告警轮的差异在编排（无 ask_user + rca 取证），不在能力面。

**弃**：加一层调度 Agent 判断该找谁。理由：意图在入口已确定（用户 @ 哪个机器人就是哪个角色），多一层只增加 0.5–2s 延迟、路由错误与权限扩散。

### D2 Agent 实例按**能力档**预装配，外图复用

启动期把 profile 映射为**能力档**，每个档装配一个 Agent（工具面 + 轮次预算）。外图只有「交互 / 非交互」两张（差异是 `ask_user`）。

- 档位：`default`（基础工具、5 轮）与 `log`（基础工具 + 两个日志工具、6 轮）
- 映射：`bot-ops` / `bot-dev` → `log`；其余（含站点预设、`bot-support`、`bot-finance`、通用人设）→ `default`
- 人设**不在档上**：同档的不同预设共用同一实例，人设由 `ctx.persona` 运行期施加
- 外图的 `agent` 节点持有 `{(能力档, clarify): AgentLoopBundle}`，运行期按 `state.profile` 查档后选 bundle

**工具面的唯一判据是 `bundle.tool_names`**：prompt 工具段取它（`agent_node.py:282` → `build_prompt`），因为它在编译期就固定在 bundle 上；`ctx.tool_names` 只喂 verify / regen 判据（`regen_decision.py:72`）。两者都必须按本轮档位取值，但改 prompt 只认前者。

**弃**：每个 profile 一个实例。理由：人设不进实例，`bot-support`/`bot-finance` 与 `chat` 的实例完全相同，纯属重复编译；而按名字硬编码装配清单（只装 chat/bot-ops/bot-dev）会让声明了 `profile: bot-support` 的机器人在启动期失败。

**弃**：每个 profile 一张完整图。理由：外图节点（finalize/verify/format）与角色无关，复制只会让校验逻辑改 N 处；图数量会随 profile 线性增长。

### D3 工具清单启动期固定，不做每轮发现

启动时连 MCP、`tools/list`、校验期望工具存在，然后编译进 Agent。

**前置条件**：只有当日志类 profile（`bot-ops` / `bot-dev`）**被 `WECOM_BOTS` 引用**时才做这件事。没有机器人引用时**完全不连 MCP、不校验白名单**——否则所有未用日志能力的部署（含当前生产 compose 与 `.env.example`）都会因 MCP 不可达而起不来，Migration Plan 第 1 步的"等价现状"也不成立。

**失败语义分两种**：

- 连接失败 / 超时 → **不阻断启动**，日志档仍装配工具，工具返回「查询失败：日志服务不可用」（本决策的核心：工具存在并结构化失败，优于工具消失）
- 连接成功但 `tools/list` 缺少期望工具 → **启动失败**（契约不符，属确定性配置错误，不静默降级）

**弃**：每轮 `list_tools`（OpenAI SDK 风格）。理由：多一次 RTT；工具集变化破坏可回归性；MCP 不可用时工具"消失"会让模型说"我没有这个能力"，而预编译下工具存在并返回结构化失败，语义更准确。

### D4 profile 配置放 `WECOM_BOTS` 条目

与 `key`/`name` 同级增加 `profile`。**弃**：新增独立配置文件或 DB 表。理由：只有 4 台机器人，引入新配置源会增加部署与排障成本。

### D5 可见性两层校验

预设新增 `hidden` / `channel`；`/api/agents` 过滤 `hidden`，生成入口校验渠道。`bot-` 前缀强制要求两字段，加载期失败。

**弃**：只在前端隐藏。理由：手搓请求仍可指定 `agent=bot-ops`，等于绕过。

### D6 profile 通过图状态传递

`AgentState` 增加 `profile` 字段，`agent` 节点按它取实例。**弃**：用 contextvar。理由：state 可被 trace metadata、重生成路径与测试直接读到，且本项目未接 checkpointer，加字段无迁移成本。

### D7 日志工具手写，不用 MCPAdapter 自动转换

两个 `@tool` 包装现有 `McpLogSource`。**弃**：`langchain.mcp.MCPAdapter` 自动发现。理由：自动转换无法控制描述文案、参数白名单、输出裁剪与脱敏；服务端新增工具会自动暴露给模型。

### D8 参数语义：minutes 而非 epoch，uri 精确白名单

模型说"最近 30 分钟"，代码换算时间戳；`uri` 必须精确命中白名单，否则返回错误并列出可选值。

**弃**：让模型算 epoch；**弃**：允许任意 uri 或前缀匹配。理由：前者是高频错误源（时区/单位），后者等于允许查询任意接口。

### D9 输出为紧凑文本而非 JSON

聚合 5 行、明细最多 20 行 6 字段，节点用 `node-N` 别名。**弃**：返回结构化 JSON。理由：模型读文本更稳，且可直接贴进回答；JSON 还要二次裁剪。

### D10 单次超时 8 秒、不自动重试

**弃**：工具内重试。理由：重试让用户多等一个超时周期；把重试权交给模型（换查询）更可控。

### D11 trace 起点前移到告警入口

`_analyze_turn` 加 `@observe(name="alert_turn")` 并传 `langfuse_observation_id`，使取数 span 隶属同一条 trace；metadata 补 `rca_*`。

**弃**：用 `client.trace(timestamp=...)` 事后修正。理由：v2 的 `client.trace()` 会覆盖 timestamp，行为不确定。

**实现前置（spike，见 tasks 6.0）**：`_run_generation` 自身也传 `langfuse_observation_id`（`turn_runner.py:373`），嵌套时父子可能撞同一个 observation id。实施前先用本地一条假 webhook 验证：是否仍是**一条** trace、根是否早于首个取数 span。

**回退分支必须整组收口**，不是只取消改代码：spike 不通过时，连带把 llm-tracing 的「告警轮根开在告警入口」delta 条款、`6.4` 的「起点不晚于首个 span」断言、`8.2`/`8.3` 的对应验收一并删除或改写为「trace 名仍为 `chat_turn`，起点为生成开始」，并同步 Risks 中的同名条目；否则按 8.2 验收必挂。

### D12 标签收敛为三类

`kb|no_kb`、渠道、`pf:<profile>`。**弃**：`mcp|no_mcp`（与 profile 高度重合，且只能表达"能力"不能表达"实际调用"）；**弃**：把工具名做成标签（高基数、组合爆炸，行为看 span 与 `tools_used`）。

### D13 回滚开关默认开启

`WECOM_AGENT_ROUTING_ENABLED=true`；关闭后所有企微轮次回落通用人设，不依赖清库。

**关键实现点**：`_resolve_session_agent` 只要 `bound` 非空就直接返回绑定值（`agent_service.py:1393`），因此关闭开关时必须**显式忽略 bound**，否则老会话仍带 `bot-ops` 的人设与工具，开关形同虚设。范围与语义写死如下：

- **作用域只到企微**：该方法是站点 / 企微共用的单点（`agent_service.py:1250`），而开关是全局 settings，照字面实现会连站点绑定一起忽略。override 的条件是 `WECOM_AGENT_ROUTING_ENABLED=false` **且** `current_channel.get() == Channel.WECOM`；站点（`Channel.WEB`，`chat.py:54` 在 `start_turn` 之前写入）与告警（`Channel.ALERT`）不受影响。
- **只在内存里忽略，不写库**：override 分支把 `bound` 与 `requested` 都视为空，直接返回 `effective=""`（本项目的"通用人设"即空 agent 值，**没有**名为 `chat` 的预设文件），**不调用** `bind_session_agent_async`。
- **回滚可逆**：因为不改 `sessions.agent`，把开关改回 true 并重建后，老会话立即恢复 `bot-ops` 绑定；反过来若在 override 里"重新绑定 chat"，`agents/` 无 `chat.md` 会走 unregistered（每轮 warning、不写库），真写库则开关重开后会话永久 chat，回滚不可逆。

## Risks / Trade-offs

- **老会话绑定后无法换角色** → 接受；需要时用开关整体回退，或新会话。
- **日志工具对群内所有人开放** → 依赖企微机器人可见范围（已确认是唯一闸门）；uri 白名单与审计日志降低影响面。
- **白名单过窄** → 模型频繁回答"该接口不在可查询范围"；白名单从 4 个起步，按需扩。
- **工具输出占用上下文** → 20 行上限 + 摘要；工具结果不落跨轮历史。
- **模型滥用工具** → 工具描述写清"何时用"，预算 6 轮兜底（`bot-ops`/`bot-dev`；其余仍 5 轮）。
- **trace name 变更影响既有筛选** → 文档登记 `chat_turn` → `alert_turn`；历史数据保留。
- **alert_turn 与 chat_turn 撞 observation id** → 先 spike 验证（tasks 6.0）；不通过则放弃改代码，只保留文档说明。
- **工具白名单未配置导致全拒** → 仅在日志类 profile 被引用时于启动期 fail-fast（tasks 5.3）；未引用时该变量缺省不影响启动。
- **启动多编译一张子图** → 只多 `log` 档一张（`default` 档即现有那张），与预设数量无关；预设再多也不会增加编译量。
- **agent 节点注册表化引入状态字段** → 未接 checkpointer，无迁移；新增字段有默认值。

## Migration Plan

1. 发布代码（开关默认 true，但 `WECOM_BOTS` 尚无 `profile`）→ 行为等价于现状：全部回落通用人设，且因无机器人引用日志档，启动期**不连 MCP、不校验 `RCA_TOOL_URI_WHITELIST`**。
2. 给 `ops` 加 `profile: bot-ops`，重建容器；观察一轮真实 @ 的 trace（`pf:ops`、`alert_turn` 不涉及）。
3. 再接 `dev`；确认两台的回答风格与工具调用。
4. 出现异常时：`WECOM_AGENT_ROUTING_ENABLED=false` + 重建，全部回落通用人设。

## Open Questions

- 无。群/人级权限已明确依赖企微可见范围，不在本期。
