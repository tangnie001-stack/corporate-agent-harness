## Context

当前企微 4 台机器人共用一条生成管线：handler 不传 agent，`start_turn` 以空 agent 进入，prompt 落到 `general` 人设；工具面由编译期 `build_graph(tools=...)` 固定，与预设无关。

告警链路是第四个编排入口，它先取数（MCP span）后生成，但 trace 由 `_run_generation` 的 `@observe` 在生成开始时创建，导致 trace 起点晚于其子 span（实测 3.2s）。

约束：

- 图在启动期编译，工具列表编译期固定；每轮换工具需重建 Agent，成本高且不可回归
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

按「渠道 + 渠道内身份」查表：站点取请求 `agent`（缺省 `chat`），企微取 `bot_key` 的配置，告警固定 `alert`。

**弃**：加一层调度 Agent 判断该找谁。理由：意图在入口已确定（用户 @ 哪个机器人就是哪个角色），多一层只增加 0.5–2s 延迟、路由错误与权限扩散。

### D2 Agent 实例按 profile 预装配，外图复用

启动期为每个被引用的 profile 装配一个 Agent（人设 + 工具面 + 轮次预算），外图只有「交互 / 非交互」两张（差异是 `ask_user`）。

**弃**：每个 profile 一张完整图。理由：外图节点（finalize/verify/format）与角色无关，复制只会让校验逻辑改 N 处；图数量会随 profile 线性增长。

### D3 工具清单启动期固定，不做每轮发现

启动时连 MCP、`tools/list`、校验期望工具存在，然后编译进 Agent。

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

### D12 标签收敛为三类

`kb|no_kb`、渠道、`pf:<profile>`。**弃**：`mcp|no_mcp`（与 profile 高度重合，且只能表达"能力"不能表达"实际调用"）；**弃**：把工具名做成标签（高基数、组合爆炸，行为看 span 与 `tools_used`）。

### D13 回滚开关默认开启

`WECOM_AGENT_ROUTING_ENABLED=true`；关闭后所有企微轮次回落通用人设，不依赖清库。理由：bind-once 使"改配置"无法解绑已有会话，开关是唯一能一分钟止血的手段。

## Risks / Trade-offs

- **老会话绑定后无法换角色** → 接受；需要时用开关整体回退，或新会话。
- **日志工具对群内所有人开放** → 依赖企微机器人可见范围（已确认是唯一闸门）；uri 白名单与审计日志降低影响面。
- **白名单过窄** → 模型频繁回答"该接口不在可查询范围"；白名单从 4 个起步，按需扩。
- **工具输出占用上下文** → 20 行上限 + 摘要；工具结果不落跨轮历史。
- **模型滥用工具** → 工具描述写清"何时用"，预算 8 轮兜底。
- **trace name 变更影响既有筛选** → 文档登记 `chat_turn` → `alert_turn`；历史数据保留。
- **agent 节点注册表化引入状态字段** → 未接 checkpointer，无迁移；新增字段有默认值。

## Migration Plan

1. 发布代码（开关默认 true，但 `WECOM_BOTS` 尚无 `profile`）→ 行为等价于现状（全部回落 `chat`）。
2. 给 `ops` 加 `profile: bot-ops`，重建容器；观察一轮真实 @ 的 trace（`pf:ops`、`alert_turn` 不涉及）。
3. 再接 `dev`；确认两台的回答风格与工具调用。
4. 出现异常时：`WECOM_AGENT_ROUTING_ENABLED=false` + 重建，全部回落通用人设。

## Open Questions

- 无。群/人级权限已明确依赖企微可见范围，不在本期。
