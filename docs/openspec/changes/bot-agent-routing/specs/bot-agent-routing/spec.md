## ADDED Requirements

### Requirement: 入口决定 Agent，路由不经过模型

系统 SHALL 在进入生成之前，按「渠道 + 渠道内身份」确定本轮 profile，并由 profile 决定人设、工具面与轮次预算。路由 SHALL 是确定性查表，SHALL NOT 由模型判断或二次分发。

映射规则：站点取请求中的 `agent`（缺省 `chat`）；企微取 `bot_key` 对应的配置项；告警轮固定标签 `alert`。

`chat` SHALL 视为**逻辑 profile 名**（通用人设），其对应的 agent 取值为空串；系统 SHALL NOT 要求存在名为 `chat` 的预设文件。

告警轮的 `alert` SHALL 视为**路由标签**，复用通用人设与无澄清图，SHALL NOT 要求装配独立的 Agent 实例。

#### Scenario: 企微按机器人分配

- **WHEN** 某台已配置 `profile` 的机器人收到 @ 消息
- **THEN** 该轮使用该 profile 对应的 Agent，且在下一次相同上下文中结果一致

#### Scenario: 站点未选择时使用默认

- **WHEN** 站点请求未携带 `agent`
- **THEN** 该轮使用 `chat` profile，行为与改造前一致

#### Scenario: 告警轮无独立 Agent

- **WHEN** 告警 webhook 触发一轮分析
- **THEN** 该轮使用 `alert` 路由标签与通用人设，不要求存在名为 `alert` 的 Agent 实例

#### Scenario: 未配置 profile 的机器人

- **WHEN** `WECOM_BOTS` 中某台机器人没有 `profile` 字段
- **THEN** 该轮回落到 `chat`，不报错、不阻断消息处理

### Requirement: profile 映射到预装配的能力档实例

系统 SHALL 在启动期为每个**能力档**装配一个 Agent 实例（工具面 + 轮次预算），并在轮次开始时按 profile 查表选中档位。能力档 SHALL 至少区分「基础」与「含只读日志工具」两档；未在注册表中的 profile 引用的档位 SHALL NOT 被装配。

人设 SHALL 由 profile 对应的预设正文在运行期施加，SHALL NOT 固化在 Agent 实例中——因此同档的不同预设共用同一实例。

工具面的判据 SHALL 是承载该档的装配产物（其工具名集合同时驱动提示词工具段与 verify 判据），SHALL NOT 使用忽略档位的全局工具列表。

外层编排图 SHALL 复用，SHALL NOT 为每个 profile 复制一份外图；校验、格式化与引用提取等编排逻辑对所有 profile 一致。

#### Scenario: 多个 profile 共用同一外图

- **WHEN** 两个 profile 分别处理一轮请求
- **THEN** 两者经过相同的编排节点序列，差异只在所选能力档的工具与预算，以及运行期施加的人设

#### Scenario: 无专属工具差异的 profile 可直接使用

- **WHEN** 某台机器人声明了只有人设差异、无工具差异的 profile（如客服、财务）
- **THEN** 该 profile 使用基础档，启动成功，不要求为它单独装配实例

#### Scenario: 日志工具只出现在含日志档

- **WHEN** 分别以基础档与含日志档处理一轮请求
- **THEN** 只有后者装配了只读日志工具，前者的提示词工具段与可用工具中都不含它们

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

关闭开关时，系统 SHALL 忽略会话已绑定的 agent 值（不沿用 `sessions.agent`），本轮按通用人设解析；该忽略 SHALL 限定在**企微来源的轮次**，站点与告警轮次的绑定解析 SHALL 不受该开关影响。

忽略 SHALL 只在内存中生效，SHALL NOT 改写会话的绑定记录；因此开关重新开启后，原绑定 SHALL 仍然生效。

#### Scenario: 关闭开关

- **WHEN** `WECOM_AGENT_ROUTING_ENABLED=false`
- **THEN** 所有企微轮次使用通用人设，不加载 `bot-` 预设的 profile

#### Scenario: 已绑定会话在关闭开关后回落

- **WHEN** 某群会话此前已绑定 `bot-ops`，随后开关被关闭并重建容器
- **THEN** 该群下一轮使用通用人设，不携带日志工具，且无需清理 `sessions.agent`

#### Scenario: 站点绑定不受回滚开关影响

- **WHEN** 开关关闭，站点会话已绑定某个站点预设后发起新一轮
- **THEN** 该轮仍使用该预设（企微专用开关不改变站点行为）

#### Scenario: 回滚不破坏原绑定

- **WHEN** 关闭开关跑过若干轮后再重新开启并重建
- **THEN** 此前绑定 `bot-ops` 的群会话恢复使用 `bot-ops`，无需人工重绑
