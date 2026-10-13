# wecom-channel Specification

## Purpose
企业微信智能机器人通道：多机器人长连接的配置声明与启动期校验、逐台装配/降级/关停、入站消息按机器人身份分发；回调模式保留为单机器人 legacy。官方 `aibot` SDK 的用法约束与部署影响见 `docs/agents/code-map.md` / `docs/agents/deploy-runbook.md`。
## Requirements
### Requirement: 多机器人配置以注册表声明并在启动期校验

系统 SHALL 从环境变量 `WECOM_BOTS`（JSON 数组，**必须写在一行**）读取多台智能机器人配置，每台包含 `key`（业务线标识）、`bot_id`、`secret`。系统 SHALL 在启动期（`WECOM_BOT_ENABLED=true` 时）解析并校验：值为单行且 JSON 合法、每台 `key`/`bot_id`/`secret` 非空、`key` 匹配 `^[a-z0-9_-]+$`、`key` 唯一、`bot_id` 唯一、`key` 不为保留字 `callback`；任一不满足 SHALL 立即失败（fail-fast）。当 `WECOM_BOT_ENABLED=false` 时，系统 SHALL 不解析该配置、SHALL NOT 因该配置非法而启动失败。

校验失败时，错误信息 SHALL 只标识到**下标或 `key`**（如 `WECOM_BOTS[1].secret 为空`），SHALL NOT 包含 `WECOM_BOTS` 的原值或任何 `secret` 内容。

#### Scenario: 配置合法

- **WHEN** `WECOM_BOT_ENABLED=true` 且 `WECOM_BOTS` 为单行合法 JSON、每台 `key`/`bot_id`/`secret` 非空、`key` 符合字符集且唯一、`bot_id` 唯一
- **THEN** 系统接受该配置并为每台创建驱动

#### Scenario: 配置非法

- **WHEN** `WECOM_BOT_ENABLED=true` 且 `WECOM_BOTS` 为非法 JSON、跨多行、或某台缺少/为空 `key`/`bot_id`/`secret`、或 `key` 不符合字符集、或 `key`/`bot_id` 重复、或 `key` 为 `callback`
- **THEN** 应用启动失败并给出可定位的错误信息

#### Scenario: 错误信息不泄露凭证

- **WHEN** 校验失败并产生错误信息/日志
- **THEN** 该信息只含下标或 `key` 级定位，不含 `WECOM_BOTS` 原值或任何 `secret` 内容

#### Scenario: 长连接模式但未配置机器人

- **WHEN** `WECOM_BOT_ENABLED=true` 且 `WECOM_BOT_MODE=long_connection` 且 `WECOM_BOTS` 为空
- **THEN** 应用启动失败（而非静默无机器人工作）

#### Scenario: 通道关闭时容忍坏配置

- **WHEN** `WECOM_BOT_ENABLED=false`
- **THEN** 系统不解析 `WECOM_BOTS`，任何取值的该变量都不导致启动失败

### Requirement: 每台机器人一条长连接驱动，逐台装配、降级与关停

系统 SHALL 为每台机器人构造一个长连接驱动并各自建立连接；某台连接失败时 SHALL 只记 warning 并跳过该台，SHALL NOT 影响其余机器人、SHALL NOT 阻塞应用启动。驱动 SHALL 集中存放于 `wecom_service` 的注册表（键为 `bot_key`）。`stop()` SHALL 逐台断开，单台断开失败 SHALL 记 warning 并继续处理其余台，且 SHALL 幂等（重复调用安全）。启动完成时系统 SHALL 记录一条 INFO 锚点日志，给出**成功连接台数与总台数**。

#### Scenario: 全部连接成功

- **WHEN** `WECOM_BOT_MODE=long_connection` 且配置合法、企微端点可达
- **THEN** 每台机器人各建立一条长连接，且记录锚点日志显示成功台数等于总台数

#### Scenario: 单台连接失败

- **WHEN** 某台机器人建立连接时抛错
- **THEN** 该台被跳过并记 warning，其余机器人仍建立连接，应用启动成功，且锚点日志反映成功台数小于总台数

#### Scenario: 关停时单台断开失败

- **WHEN** 关停过程中某台 `disconnect()` 抛错
- **THEN** 其余台仍被断开，失败台记 warning；重复调用 `stop()` 不报错

### Requirement: 入站消息按机器人身份分发并携带业务线标识

系统 SHALL 以入站报文的 `aibotid` 映射到对应 `bot_key`（`aibotid` 即 BotID），并 SHALL 在处理日志中以 `bot_key=<key>` 携带该标识；长连接驱动在其自身错误日志中 SHALL 携带机器人身份，且该值 SHALL 按日志规约编码（token 不安全时转义，不得裸写产生畸形日志行）。

#### Scenario: 收到某台机器人的消息

- **WHEN** 某台已配置机器人推送入站消息
- **THEN** 处理时能确定其 `bot_key`，且日志包含 `bot_key=<key>` 维度

#### Scenario: 未知机器人

- **WHEN** 入站报文的 `aibotid` 不在配置注册表中（长连接路径）
- **THEN** 系统记 warning 并安全返回，不抛异常、不影响其他机器人

#### Scenario: 驱动侧解析或处理失败

- **WHEN** 长连接驱动解析入站报文或调用业务 handler 失败
- **THEN** 其错误日志包含该连接对应的机器人标识，且该值经日志值编码输出

### Requirement: 长连接驱动使用官方 SDK 且仅用其公开接口

长连接驱动 SHALL 基于官方 `wecom-aibot-python-sdk`（导入名 `aibot`）实现，SHALL 仅使用 `connect()`、`disconnect()`、`on()`、`reply_stream()` 等公开接口，SHALL NOT 调用 `run()`（其自建事件循环，与 FastAPI 冲突）。该禁令 SHALL 有一处自动化守卫（对驱动源码的静态断言），而非仅文档约定。

#### Scenario: 建立连接

- **WHEN** 驱动启动
- **THEN** 使用 SDK 的 `connect()` 建立连接并注册消息/事件处理器，未调用 `run()`

#### Scenario: 守卫拦截误用

- **WHEN** 有人在长连接驱动中调用 SDK 的 `run()`
- **THEN** 自动化守卫（静态断言）失败，而不是等到运行时才暴露

### Requirement: 官方 SDK 的用法与部署影响有归属文档

仓库 SHALL 在 `docs/agents/code-map.md` 记录官方 `aibot` SDK 的用法与约束（包名/版本/导入名、SDK 承担的职责、允许使用的接口、**禁止 `run()`**），并 SHALL 在 `docs/agents/deploy-runbook.md` 记录其依赖与部署影响（传递依赖 `websockets`/`aiohttp`/`pyee`/`cryptography`/`certifi` ⇒ 需重建 app 镜像、预热云效 PyPI 代理仓 `repo-okxha`）。

#### Scenario: 查阅 SDK 用法

- **WHEN** 开发者要修改长连接驱动
- **THEN** 能在 `code-map.md` 找到 SDK 用法与约束（含禁止 `run()`）

#### Scenario: 评估部署影响

- **WHEN** 发布含该依赖的版本
- **THEN** `deploy-runbook.md` 已写明需重建镜像并预热代理仓

### Requirement: 配置形状变更后不残留误导性的旧变量

长连接专用凭证 SHALL 只通过 `WECOM_BOTS` 提供；扁平的单机器人长连接变量 `WECOM_BOT_ID` / `WECOM_BOT_SECRET` SHALL 被移除；回调专用变量（`WECOM_BOT_TOKEN` / `WECOM_BOT_ENCODING_AES_KEY` / `WECOM_BOT_RECEIVE_ID`）SHALL 保留并标注仅 `callback` 模式使用。

#### Scenario: 查阅 .env 模板

- **WHEN** 运维人员填写 `.env`
- **THEN** 长连接凭证只在 `WECOM_BOTS` 一处填写（单行），不再有与之重复或冲突的扁平长连接变量

### Requirement: 回调模式保留为单机器人 legacy

系统 SHALL 在 `WECOM_BOT_MODE=callback` 时按既有扁平变量装配单台回调驱动，其驱动 SHALL 存入注册表的**保留键 `callback`**，且该模式下入站消息的 `bot_key` SHALL 固定为 `callback`（不参与长连接注册表的反查）；当模式非 `callback` 时，`/api/wecom/callback` SHALL 返回 404。

#### Scenario: 非回调模式下访问回调端点

- **WHEN** `WECOM_BOT_MODE=long_connection` 且请求 `/api/wecom/callback`
- **THEN** 返回 404

#### Scenario: 回调模式的机器人标识

- **WHEN** `WECOM_BOT_MODE=callback` 且收到回调消息
- **THEN** 处理日志中的 `bot_key` 为 `callback`，不因该机器人不在 `WECOM_BOTS` 中而产生"未知机器人"告警

### Requirement: 主动推送消息到指定会话

系统 SHALL 提供**不依赖入站帧**的主动发送能力：向指定会话推送一条消息，会话由配置显式给出。寻址 SHALL 遵循 SDK `send_message(chatid, body)` 的语义 —— 单聊填用户 `userid`、群聊填群 `chatid`；系统 SHALL NOT 以"最近一次入站会话"或其他隐式默认充当目标。

该能力 SHALL 为**一次性发送**：不依赖入站 `req_id`，因而**没有流式更新语义**，也不支持更新已发出的消息（多帧快照与消息更新均要求入站帧的 `req_id`）。需要"先回执再结论"的调用方 SHALL 分两次发送。

该出口 SHALL 通过一个**稳定的、可被上层依赖的接口**暴露（现有通道驱动协议只有启动/停止，**不承载发送**）；接口 SHALL 接收目标机器人、目标会话与内容，SHALL 返回成功/失败，并使调用方无需感知 SDK 细节。

发送结果 SHALL 以成功/失败返回给调用方；失败时 SHALL 记含**目标会话标识与错误码**的日志。单次发送失败 SHALL NOT 影响入站消息处理，也 SHALL NOT 影响其他机器人的连接。目标会话标识 SHALL 按日志规约编码后输出，SHALL NOT 裸写。

#### Scenario: 推送到群

- **WHEN** 给定目标群 `chatid` 与消息内容，且该机器人连接已就绪
- **THEN** 系统经 SDK 主动发送，群内出现该消息

#### Scenario: 目标未配置

- **WHEN** 未配置目标会话，或配置取值为空
- **THEN** 系统不发送并记 warning，SHALL NOT 回落到任意默认会话

#### Scenario: 发送失败

- **WHEN** SDK 发送返回错误（连接未就绪、凭证失效等）
- **THEN** 系统记录含目标会话标识与错误码的日志并返回失败，入站处理与其余机器人连接不受影响
