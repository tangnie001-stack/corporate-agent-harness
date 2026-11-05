## ADDED Requirements

### Requirement: 入口决定 Agent，路由不经过模型

系统 SHALL 在进入生成之前，按「渠道 + 渠道内身份」确定本轮 profile，并由 profile 决定人设、工具面与轮次预算。路由 SHALL 是确定性查表，SHALL NOT 由模型判断或二次分发。

映射规则：站点取请求中的 `agent`（缺省 `chat`）；企微取 `bot_key` 对应的配置项；告警固定 `alert`。

#### Scenario: 企微按机器人分配

- **WHEN** 某台已配置 `profile` 的机器人收到 @ 消息
- **THEN** 该轮使用该 profile 对应的 Agent，且在下一次相同上下文中结果一致

#### Scenario: 站点未选择时使用默认

- **WHEN** 站点请求未携带 `agent`
- **THEN** 该轮使用 `chat` profile，行为与改造前一致

#### Scenario: 未配置 profile 的机器人

- **WHEN** `WECOM_BOTS` 中某台机器人没有 `profile` 字段
- **THEN** 该轮回落到 `chat`，不报错、不阻断消息处理

### Requirement: profile 映射到预装配的 Agent 实例

系统 SHALL 为每个被引用的 profile 在启动期装配一个 Agent 实例（人设 + 工具面 + 轮次预算），轮次开始时按 profile 选择实例。

外层编排图 SHALL 复用，SHALL NOT 为每个 profile 复制一份外图；校验、格式化与引用提取等编排逻辑对所有 profile 一致。

#### Scenario: 多个 profile 共用同一外图

- **WHEN** 两个 profile 分别处理一轮请求
- **THEN** 两者经过相同的编排节点序列，差异只在 Agent 实例的人设、工具与预算

#### Scenario: 未知 profile 降级

- **WHEN** 配置引用了未装配的 profile
- **THEN** 该轮降级到 `chat` 并记 warning，不阻断该轮

### Requirement: 渠道专属 Agent 对站点不可见且不可调用

智能体预设 SHALL 支持 `hidden`（是否出现在 `/api/agents` 清单）与 `channel`（允许使用的渠道）两个可选字段。

名称以 `bot-` 开头的预设 MUST 声明 `hidden: true` 与 `channel: wecom`，否则加载期 SHALL 失败。

`GET /api/agents` SHALL NOT 返回 `hidden: true` 的预设。非该预设声明渠道的请求指定该预设时，系统 SHALL 忽略该值并记录告警日志，SHALL NOT 因此拒绝请求。

#### Scenario: 站点清单不含渠道专属预设

- **WHEN** 站点请求 `GET /api/agents`
- **THEN** 返回结果中不含任何 `hidden: true` 的预设

#### Scenario: 站点请求指定渠道专属预设

- **WHEN** 非企微来源的请求携带 `agent=bot-ops`
- **THEN** 该值被忽略，本轮使用默认 profile，并记录一条「绑定被忽略」日志

#### Scenario: bot- 前缀缺少声明

- **WHEN** `agents/bot-xxx.md` 未声明 `hidden: true` 或 `channel: wecom`
- **THEN** 加载期失败并指出该文件，不注册该预设

### Requirement: 路由可整体回滚

系统 SHALL 提供开关 `WECOM_AGENT_ROUTING_ENABLED`（默认 true）。关闭时，所有企微轮次 SHALL 回落到通用人设，且回滚 SHALL 不依赖修改数据库或回滚镜像。

#### Scenario: 关闭开关

- **WHEN** `WECOM_AGENT_ROUTING_ENABLED=false`
- **THEN** 所有企微轮次使用通用人设，不加载 `bot-` 预设的 profile
