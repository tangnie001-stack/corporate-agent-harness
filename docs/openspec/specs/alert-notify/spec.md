# alert-notify Specification

## Purpose
TBD - created by archiving change alibaba-alert-notify. Update Purpose after archive.
## Requirements
### Requirement: 告警接收端点以 URL 路径 token 鉴权

系统 SHALL 提供告警接收端点（HTTP POST），路径形如 `/api/alerts/webhook/{token}` —— token 取自
**URL 路径**，因为上游是 Grafana 的 contact point、**不支持自定义请求头**（2026-10-13 实测）。
token SHALL 以**常数时间**方式与配置值比较。

token 不匹配、或该端点**未配置 token** 时，系统 SHALL 返回 **401**；URL **缺少 token 段**时
SHALL 返回 **404**（路由不匹配）—— 两者语义分离，便于排障（不再用 404 同时表达"鉴权失败"）。
该端点 SHALL NOT 依赖登录态，且应用在未配置 token 时 SHALL 正常启动。

凭据 SHALL NOT 进入日志：nginx 访问日志 SHALL 对该路径段脱敏，应用侧打印请求路径前 SHALL 先掩码。

#### Scenario: token 正确

- **WHEN** URL 路径里的 token 与配置一致
- **THEN** 系统接受该请求并进入告警处理流程

#### Scenario: token 错误或缺失

- **WHEN** URL 路径里的 token 与配置不一致
- **THEN** 返回 401

#### Scenario: 未配置 token

- **WHEN** 该端点未配置 token
- **THEN** 应用正常启动，且对该端点的任何请求都返回 401

#### Scenario: URL 缺少 token 段

- **WHEN** 请求路径不是 `/api/alerts/webhook/{token}` 形式（例如只打到 `/api/alerts/webhook`）
- **THEN** 返回 404，与鉴权失败（401）可区分

#### Scenario: 凭据不进日志

- **WHEN** 任一请求到达该端点（无论成败）
- **THEN** nginx 访问日志与应用日志里的路径均为掩码形态（形如 `.../webhook/***`），不含 token 原文

### Requirement: 端点立即应答，不等待分析

端点 SHALL 在校验与去重通过后**立即**返回成功应答，SHALL NOT 等待分析完成 —— 上游 Webhook 有 5 秒硬超时，等待会导致上游判定失败并重试。

端点应答 SHALL NOT 表达分析结果：分析失败时端点仍返回"已受理"，失败通过群消息兜底体现。

#### Scenario: 分析耗时长

- **WHEN** 分析耗时超过上游超时阈值
- **THEN** 端点已在上游超时之前返回成功应答，分析在后台继续

#### Scenario: 分析失败不影响应答

- **WHEN** 后台分析抛错
- **THEN** 端点应答仍为"已受理"（不因分析失败改为错误码）

### Requirement: 告警载荷解析与必填校验

系统 SHALL 按 **Grafana 直连推送的原生 Alertmanager 载荷**解析（实测确认**无需任何自定义模板**），取出：
**报警身份**（顶层 `alarmId`；缺失时回退**首条告警的 `fingerprint`**）、每条告警的**指纹**
（`alerts[].fingerprint`）、**告警名称**（`commonLabels.alertname`）、**告警等级**（按 `level` →
`commonLabels.severity` → `commonLabels._aliyun_arms_alert_level` 取首个可用，都无则 `-`）、
**告警状态**（顶层 `status`，缺省按 `firing`）以及每条告警的描述（按 `annotations.message` →
`summary` → `description` 取首个非空；都没有则退回顶层 `message`）。

必填字段缺失或载荷非法（含 JSON 不可解析）时，系统 SHALL 记 warning、返回可识别的错误响应，**并把原始
载荷记入日志**（供与上游核对形状），SHALL NOT 以未处理异常结束。**缺 `alarmId` 不属错误**（按上回退到指纹）。

#### Scenario: 真实 Grafana 载荷

- **WHEN** 收到 Grafana contact point 推送的原生载荷（含 `status`、`alerts[].fingerprint`、`commonLabels.alertname`）
- **THEN** 系统解析出上述全部字段，**上游无需做任何模板定制**

#### Scenario: 缺 alarmId

- **WHEN** 载荷没有顶层 `alarmId`
- **THEN** 系统以首条告警的 `fingerprint` 充当报警身份并继续处理，不报错

#### Scenario: 缺告警名称 / 无指纹 / JSON 非法

- **WHEN** 载荷缺 `commonLabels.alertname`、或没有任何有效 `fingerprint`、或 JSON 不可解析
- **THEN** 系统记 warning、返回 400、把原始载荷记入日志，且不进入分析流程

#### Scenario: 载荷含多条告警

- **WHEN** 载荷含多条告警元素
- **THEN** 系统逐条读取其指纹（去重键）与描述（多条以换行合并），不因多条而报错

### Requirement: 入口按告警指纹去重

系统 SHALL 为载荷中**每一条** alert 生成去重键 **`<fingerprint>|<status>`**，配合**可配置的时间窗**判重；
仅当**全部**键都命中窗口时，该请求 SHALL 被丢弃并记 info 日志，SHALL NOT 触发分析。去重记录 SHALL 使用带
过期时间与容量上限的**有界**结构（不得为无界增长的裸映射）。

**状态必须参与去重键**：恢复通知与触发通知携带**同一个** `fingerprint`，键里不含状态的话，恢复会在
窗口内被当成"重复"直接丢掉（实测隐患）。

被去重丢弃时，端点 SHALL 返回**与受理相同的成功应答**（不得为错误码）—— 否则上游会按发送失败重试，形成无效重试噪声。

#### Scenario: 全部键命中窗口

- **WHEN** 请求中所有 alert 的指纹与状态组合都在去重窗口内出现过
- **THEN** 该请求被丢弃，不触发分析

#### Scenario: 存在未命中的键

- **WHEN** 请求中至少一条 alert 的键不在窗口内
- **THEN** 系统正常处理该请求

#### Scenario: 恢复不被触发那条吃掉

- **WHEN** 同一指纹先收到 `firing`、随后（去重窗口内）收到 `resolved`
- **THEN** 两条都被正常处理（键不同），恢复不会被丢弃

### Requirement: 报警会话与问答会话隔离，且按报警标识归并

系统 SHALL 为报警分析派生**独立的会话标识** —— 与企微群聊/单聊的问答会话相互隔离、不复用，标识来源为
**报警身份**（顶层 `alarmId`；缺失时为**首条告警的 `fingerprint`**）；**同一身份的多次请求 SHALL 归入同一
会话，不同身份 SHALL 各自独立**（即"一个报警身份一个会话"）。

该会话 SHALL 使用**固定**的 `user_id`，SHALL NOT 挂知识库、SHALL NOT 绑定智能体预设。分析 SHALL 复用站点同一条生成管线，不另建旁路，且 SHALL 在本轮进入生成**之前**设置**渠道上下文**（不得落默认渠道）。

喂给 Agent 的输入 SHALL 由固定模板构造（含告警名称、等级与各条告警的描述），模板 SHALL 位于 `src/config/`。

#### Scenario: 与群问答会话隔离

- **WHEN** 某群的报警被处理
- **THEN** 其会话标识与"该群 @ 机器人"的问答会话标识不同，两侧历史互不可见

#### Scenario: 固定归属

- **WHEN** 报警会话被创建
- **THEN** 其 `user_id` 为配置的固定值，普通用户的会话列表不包含该会话

#### Scenario: 输入由模板构造

- **WHEN** 构造本轮输入
- **THEN** 输入文本按固定模板由载荷字段渲染，而非直接使用不可预期的原文

#### Scenario: 同一报警标识复用会话

- **WHEN** 两次请求携带相同的报警标识（且未被去重丢弃）
- **THEN** 二者归入同一会话，后一轮能看到前一轮的历史

#### Scenario: 渠道上下文已设置

- **WHEN** 报警轮进入生成
- **THEN** 该轮日志的渠道段与追踪元数据标记为报警专用渠道，而非默认渠道

### Requirement: 报警轮不接受澄清追问

报警轮为**非交互**轮次：该轮 SHALL NOT 使用澄清追问能力 —— 不得向用户发起问题等待回答。信息不足时 SHALL 基于现有信息作答。

#### Scenario: 信息不足

- **WHEN** 模型判断输入缺少关键信息
- **THEN** 本轮不产生澄清问题（无 `ask_user` 可用），直接基于现有信息作答

### Requirement: 分析轮次有超时

报警分析轮次 SHALL 有**可配置的整轮超时**；超时后系统 SHALL **中止该轮生成**（而不仅是停止等待），并转入兜底转发，SHALL NOT 让该次报警既无结论也无兜底。

被超时中止的轮次 SHALL **不作为结论**：不得把它推送为结论，其落库状态 SHALL 反映"被取消"。

#### Scenario: 分析超时

- **WHEN** 分析轮次超过配置的超时阈值仍未结束
- **THEN** 该轮生成被中止并执行兜底转发，且该轮不作为结论被推送

#### Scenario: 挂死也能收口

- **WHEN** 生成侧因模型或出网问题长时间不产生终态
- **THEN** 超时机制仍能收口（不依赖生成侧自行结束、不依赖事件检查点到达）

### Requirement: 失败、空回答或繁忙时兜底转发

当分析轮次抛错、超时、**结论为空**（终态文本去空白后为空）或**并发繁忙**时，系统 SHALL 把该次告警的原始内容按模板渲染后转发到目标群，确保群侧至少收到该次报警。

兜底消息 SHALL 带前缀标记以与正常结论区分，SHALL 限制长度，SHALL NOT 整包 dump 原始载荷，且 SHALL NOT 写入报警会话历史。

#### Scenario: 分析抛错

- **WHEN** 分析轮次抛错
- **THEN** 目标群收到带前缀标记的兜底消息

#### Scenario: 结论为空

- **WHEN** 本轮终态文本为空或仅含空白
- **THEN** 系统按失败处理并兜底转发，不推送空气泡

#### Scenario: 并发繁忙

- **WHEN** 该会话已有轮次在跑而被拒绝
- **THEN** 系统按失败处理并兜底转发，不返回 5xx、不静默丢失

#### Scenario: 兜底不落历史

- **WHEN** 走了兜底转发
- **THEN** 该兜底内容不出现在报警会话的历史中

### Requirement: 主动推送分两段

系统 SHALL 在入口去重通过后**立即**推送一条**受理占位**到目标群，并在分析完成后推送**结论** —— 两条为**相互独立的消息**（主动推送无入站帧，因而不支持流式更新同一气泡）。

**受理占位 SHALL 在后台执行流中发送，SHALL NOT 位于 HTTP 响应路径上** —— 主动推送等待回执的超时常量与上游 5 秒硬超时同量级，同步等待会把响应拖过上游超时。

占位消息 SHALL NOT 写入报警会话历史；占位发送失败 SHALL 只记 warning 并继续分析，SHALL NOT 阻断后续流程。占位文案 SHALL 位于 `src/config/` 的文案类。

目标群 SHALL 来自配置，SHALL NOT 由告警内容动态决定；未配置目标群时 SHALL 不发送并记 warning。

推送给群的**结论文本** SHALL 取该轮的**净化正文**（与站内落库同源的分拣结果），SHALL NOT 由事件流 token 自行累积（会混入工具调用前的旁白），SHALL NOT 回读数据库（与收尾写入存在竞态）。

#### Scenario: 两段顺序

- **WHEN** 一次报警被受理并完成分析
- **THEN** 目标群先收到受理占位、后收到分析结论

#### Scenario: 占位发送失败

- **WHEN** 受理占位发送失败
- **THEN** 系统记 warning，分析继续执行，结论仍会推送

#### Scenario: 未配置目标群

- **WHEN** 未配置目标群
- **THEN** 系统不发送并记 warning，流程其余部分不失败

#### Scenario: 结论不含旁白

- **WHEN** 本轮生成中发生过工具调用并带有过程性叙述
- **THEN** 推送到群的结论与站内落库的正文一致（不含工具调用前的旁白）

#### Scenario: 端点不被占位拖慢

- **WHEN** 主动推送的等待耗时接近其超时上限
- **THEN** 端点应答不受影响（占位在后台发送）

### Requirement: 恢复通知直接推模板消息，不跑分析轮次

当载荷顶层 `status` **非** `firing`（即恢复）时，系统 SHALL **不发起分析轮次**，改为直接向目标群推送一条
**恢复文案**：`[已恢复] <告警名>（<等级>）`，有描述时另起一行附上，无描述时只出标题行。恢复文案 SHALL
位于 `src/config/` 的文案类。

恢复通知 SHALL 使用与触发**相同的报警身份**（即同一会话维度），并 SHALL 参与去重（键含状态，见上）。
未配置目标群时 SHALL 不发送并记 warning。

#### Scenario: 收到恢复通知

- **WHEN** 载荷 `status` 为 `resolved`
- **THEN** 目标群收到**一条** `[已恢复] …` 消息，且**没有**受理占位、**没有**分析轮次（不消耗模型）

#### Scenario: 同一告警的触发与恢复

- **WHEN** 同一指纹的告警先触发、后恢复
- **THEN** 触发走"占位 + 结论/兜底"，恢复走单条 `[已恢复] …`，二者互不影响

#### Scenario: 恢复但未配置目标群

- **WHEN** 未配置目标群
- **THEN** 系统不发送并记 warning，流程其余部分不失败
