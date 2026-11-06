## Why

企微 4 台机器人当前**共用同一个通用 Agent**：handler 不传 agent，所有群轮次落到 general 人设（模型自称“企业知识库问答助手”），工具面也只有检索/联网/任务——运维和开发在群里问“今天 createOrder 正常吗”，只能回答“我查不到”。

同时告警链路的标签体系只有渠道与 kb 两维，无法按“哪个 Agent / 是否具备日志能力”筛选。

本次把「入口 → Agent」的分配做成显式配置，并让 ops/dev 具备只读日志查询能力。

## What Changes

- 新增 **入口到 Agent 的路由**：按（渠道, 身份）确定 profile，profile 决定人设、工具面与轮次预算；路由在模型之前完成。
- 新增 **4 个机器人预设**：bot-ops / bot-dev / bot-support / bot-finance；人设各自独立，工具面只在 ops/dev 分档（含日志工具）。
- 预设 schema 增加 **可见性与渠道约束**：hidden / channel；bot- 前缀强制校验，站点选择器不可见且站点请求不可用。
- 新增 **两个只读日志工具**（仅 ops/dev）：sls_query_latency / sls_query_requests，复用现有 MCP 客户端；带接口白名单、时间窗、行数上限与脱敏。
- 外图 **按 profile 查能力档、选对应已装配的 Agent 实例**（基础 / 含日志两档），不按 Agent 复制外图。
- trace 约定收敛：**三个标签**（kb|no_kb / 渠道 / pf:<profile>）+ metadata 补齐（profile、tools_used、rca_*）。
- 新增 **回滚开关** WECOM_AGENT_ROUTING_ENABLED（默认 true），关闭后全部回落通用人设。

**BREAKING**：chat 标签不再写入（历史数据保留）。

## Capabilities

### New Capabilities

- bot-agent-routing: 入口到 Agent 的分配规则（渠道/身份 → profile）、Agent 注册表、可见性与渠道约束、回滚开关。
- ops-log-tools: 面向对话的两个只读日志工具契约（参数、白名单、时间窗、输出裁剪、脱敏、空结果与失败语义）。

### Modified Capabilities

- agent-preset: 预设文件结构增加 hidden / channel 字段与加载期校验。
- wecom-channel: 机器人注册表增加 profile 字段；入站分发时确定该轮 profile。
- llm-tracing: 标签由 3 类构成；metadata 增加 profile / tools_used / rca_*。

## Impact

**代码**

- src/channels/wecom/handler.py：按 bot_key 取 profile 与 kb_id，start_turn(agent=...)
- src/agents/presets/：schema 增加字段与校验
- src/services/agent_service.py：agent 节点按 profile 取已装配实例
- src/agents/tools/：新增两个日志工具（复用 src/rca/sources/mcp.py）
- agents/：4 个新预设文件

**配置**

- WECOM_BOTS 每条增加 profile
- 新增 WECOM_AGENT_ROUTING_ENABLED、RCA_TOOL_URI_WHITELIST

**文档**

- docs/agents/logging-rules.md：新增 [tool] 前缀
- docs/agents/troubleshooting.md：trace 维度约定（tag 值域 + metadata 字段）
- docs/agents/glossary.md：Agent 实例 / profile / preset 三个词

**不在本期**

- 群/人级日志权限（依赖企微机器人可见范围）
- support/finance 的专属能力
- KB per bot
- 渐进式 MCP / call_tool 代理 / 调度 Agent
