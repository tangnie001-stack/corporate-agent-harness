## MODIFIED Requirements

### Requirement: 多机器人配置以注册表声明并在启动期校验

系统 SHALL 从环境变量 `WECOM_BOTS`（JSON 数组，**必须写在一行**）读取多台智能机器人配置，每台包含 `key`（业务线标识）、`bot_id`、`secret`，以及可选的 `name`（人类可读名称，仅用于日志）与 `profile`（该机器人使用的 Agent profile，缺省为空 = 通用人设）。系统 SHALL 在启动期（`WECOM_BOT_ENABLED=true` 时）解析并校验：值为单行且 JSON 合法、每台 `key`/`bot_id`/`secret` 非空、`key` 匹配 `^[a-z0-9_-]+$`、`key` 唯一、`bot_id` 唯一、`key` 不为保留字 `callback`、`name` 若存在必须为字符串、`profile` 若存在必须为**已注册预设名**（不要求该预设具备专属工具差异）；任一不满足 SHALL 立即失败（fail-fast）。当 `WECOM_BOT_ENABLED=false` 时，系统 SHALL 不解析该配置、SHALL NOT 因该配置非法而启动失败。

`profile` 的存在性校验 SHALL 在启动企微驱动**之前**完成，且 SHALL 直接依据预设目录的加载结果判定，SHALL NOT 依赖 AgentService 实例（其预设注册表在构造期建立，且受 skills 目录门控）。

校验失败时，错误信息 SHALL 只标识到**下标或 `key`**（如 `WECOM_BOTS[1].secret 为空`），SHALL NOT 包含 `WECOM_BOTS` 的原值或任何 `secret` 内容。

#### Scenario: 配置合法

- **WHEN** `WECOM_BOT_ENABLED=true` 且 `WECOM_BOTS` 为单行合法 JSON、每台 `key`/`bot_id`/`secret` 非空、`key` 符合字符集且唯一、`bot_id` 唯一
- **THEN** 系统接受该配置并为每台创建驱动

#### Scenario: 配置非法

- **WHEN** `WECOM_BOT_ENABLED=true` 且 `WECOM_BOTS` 为非法 JSON、跨多行、或某台缺少/为空 `key`/`bot_id`/`secret`、或 `key` 不符合字符集、或 `key`/`bot_id` 重复、或 `key` 为 `callback`
- **THEN** 应用启动失败并给出可定位的错误信息

#### Scenario: profile 引用不存在

- **WHEN** 某台机器人声明了未注册的 `profile`
- **THEN** 启动失败并指出该机器人的 `key`

#### Scenario: 预设目录缺失时无法判定

- **WHEN** `agents/` 目录缺失（预设整体不可用）
- **THEN** 跳过 profile 存在性校验并记 warning，不因此启动失败

#### Scenario: profile 只提供人设

- **WHEN** 某台机器人声明的 profile 在注册表中但不具备专属工具差异
- **THEN** 启动成功，该机器人以基础能力档运行，人设取自该预设

#### Scenario: 未声明 profile

- **WHEN** 某台机器人未声明 `profile`
- **THEN** 该机器人以通用人设运行，不报错

#### Scenario: 错误信息不泄露凭证

- **WHEN** 校验失败并产生错误信息/日志
- **THEN** 该信息只含下标或 `key` 级定位，不含 `WECOM_BOTS` 原值或任何 `secret` 内容

#### Scenario: 长连接模式但未配置机器人

- **WHEN** `WECOM_BOT_ENABLED=true` 且 `WECOM_BOT_MODE=long_connection` 且 `WECOM_BOTS` 为空
- **THEN** 应用启动失败（而非静默无机器人工作）

#### Scenario: 通道关闭时容忍坏配置

- **WHEN** `WECOM_BOT_ENABLED=false`
- **THEN** 系统不解析 `WECOM_BOTS`，任何取值的该变量都不导致启动失败

### Requirement: 入站消息按机器人身份分发并携带业务线标识

系统 SHALL 以入站报文的 `aibotid` 映射到对应 `bot_key`（`aibotid` 即 BotID），并 SHALL 在处理日志中以 `bot_key=<key>` 携带该标识；长连接驱动在其自身错误日志中 SHALL 携带机器人身份，且该值 SHALL 按日志规约编码（token 不安全时转义，不得裸写产生畸形日志行）。

系统 SHALL 在同一处按 `bot_key` 解析该机器人配置的 `profile`，并 SHALL 将其作为本轮生成入口的 agent 参数；该解析 SHALL 为查表，SHALL NOT 依赖模型判断。日志 SHALL 携带 `profile=<name>` 维度。

#### Scenario: 收到某台机器人的消息

- **WHEN** 某台已配置机器人推送入站消息
- **THEN** 处理时能确定其 `bot_key`，且日志包含 `bot_key=<key>` 维度

#### Scenario: 按机器人分配 profile

- **WHEN** 某台声明了 `profile: bot-ops` 的机器人收到消息
- **THEN** 该轮生成入口收到 `agent=bot-ops`，日志包含 `profile=bot-ops`

#### Scenario: 未知机器人

- **WHEN** 入站报文的 `aibotid` 不在配置注册表中（长连接路径）
- **THEN** 系统记 warning 并安全返回，不抛异常、不影响其他机器人

#### Scenario: 驱动侧解析或处理失败

- **WHEN** 长连接驱动解析入站报文或调用业务 handler 失败
- **THEN** 其错误日志包含该连接对应的机器人标识，且该值经日志值编码输出
