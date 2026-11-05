## ADDED Requirements

### Requirement: demo 入口与开关
系统 SHALL 通过 `DEMO_MODE` 开关控制演示入口；关闭时所有请求按正式站行为处理，不产生演示身份、不施加演示配额。

#### Scenario: 开关关闭时行为不变
- **WHEN** `DEMO_MODE=false` 且访客访问 demo 域名
- **THEN** 系统按正式站处理该请求（要求登录或按现有匿名规则），不启用任何演示降权与配额

#### Scenario: 开关打开时 demo 域名进入演示模式
- **WHEN** `DEMO_MODE=true` 且请求 Host 为 `demo.tangnie.cn`
- **THEN** 请求被标记为 demo 请求，后续按本节其余要求处理

### Requirement: demo 访客身份
演示请求 SHALL 使用独立的演示身份（形如 `demo_<uuid>`），该身份 MUST NOT 写入正式用户表，也 MUST NOT 复用登录接口的自动注册路径。

#### Scenario: 首次访问分配演示身份
- **WHEN** 访客首次打开 demo 页面且没有身份 Cookie
- **THEN** 系统分配新的 `demo_<uuid>` 并写入 Cookie，后续请求复用该身份

#### Scenario: 正式登录态不得在 demo 域名提权
- **WHEN** 浏览器同时持有正式站登录 token 并访问 demo 域名
- **THEN** 系统忽略该 token，仍按演示身份处理请求

### Requirement: 演示知识库强制绑定
演示请求的检索目标 SHALL 固定为配置项 `DEMO_KB_ID`；请求体中的 `kb_id` MUST 被忽略或覆盖，演示身份 MUST NOT 能检索其他知识库。

#### Scenario: 客户端传入其他 kb_id
- **WHEN** 演示身份发起 `/api/chat/stream` 且请求体 `kb_id` 指向非演示库
- **THEN** 系统以 `DEMO_KB_ID` 执行检索，并在响应中不暴露目标库被替换这一内部细节

#### Scenario: 演示库未配置时拒绝服务
- **WHEN** `DEMO_MODE=true` 但 `DEMO_KB_ID` 为空
- **THEN** 系统拒绝演示请求并返回可读错误，而不是退化为检索任意知识库

### Requirement: 演示只读边界
演示身份 SHALL 只能建会话、发消息、读演示库；以下写操作 MUST 返回 403：上传文档、删除文档、创建/删除知识库、修改知识库领域、触发评测、生成评测题集、修改模型与配置类接口。

#### Scenario: 演示身份调用上传接口
- **WHEN** 演示身份 POST `/api/kbs/documents/upload`
- **THEN** 返回 403，且不产生任何文档或对象存储写入

#### Scenario: 演示身份可读知识库列表
- **WHEN** 演示身份 POST `/api/kbs/list`
- **THEN** 只返回 `DEMO_KB_ID` 对应的知识库（只读），不返回其他知识库

### Requirement: 演示配额与预算
系统 SHALL 对演示流量施加三层限制：单会话轮数、单 IP 请求频率、全站每日 token 预算；计数 MUST 存于共享存储（Redis），MUST NOT 依赖进程内状态。

#### Scenario: 单会话轮数耗尽
- **WHEN** 同一演示会话的问答轮数达到 `DEMO_SESSION_MAX_TURNS`
- **THEN** 后续提问被拒绝并提示可新建会话或额度已用完

#### Scenario: 全站日预算耗尽
- **WHEN** 当日累计 token 达到 `DEMO_DAILY_TOKEN_BUDGET`
- **THEN** 所有演示问答统一返回"今日演示额度已用完"，且不再调用模型

#### Scenario: 预算计数在重启后保持
- **WHEN** app 容器重启后访客继续提问
- **THEN** 当日已消耗额度仍然生效，不因重启清零

### Requirement: 演示界面形态
演示页面 SHALL 在首屏显示演示范围声明与示例问题，并隐藏知识库管理、上传、评测等入口；额度耗尽时 MUST 给出明确提示而不是静默失败。

#### Scenario: 首屏展示示例问题
- **WHEN** 访客打开演示页
- **THEN** 页面展示范围声明与固定的示例问题列表，点击示例问题直接发起一次提问

#### Scenario: 额度耗尽时提示
- **WHEN** 演示额度（轮数或全站预算）耗尽
- **THEN** 输入区给出"额度已用完"的可读提示，禁用发送且不产生请求

### Requirement: 演示紧急关停
系统 SHALL 提供不依赖应用重启的关停手段：在网关层将 demo 域名切换为维护响应，使演示立即不可用，且不影响正式站。

#### Scenario: 关停演练
- **WHEN** 运维按 runbook 执行 demo 关停
- **THEN** demo 域名返回维护页/503，正式站域名行为不变

### Requirement: 演示观测口径
演示请求 SHALL 在日志与追踪中以 `channel=demo` 标识；检索未命中的演示提问 MUST 记录一条可聚合的 `demo_miss` 事件（含脱敏 query 与 trace_id），用于补语料。

#### Scenario: 演示流量可与正式流量区分
- **WHEN** 访客在 demo 域名发起一轮问答
- **THEN** 该轮日志第 5 段与 Langfuse metadata 的 channel 为 `demo`

#### Scenario: 检索落空被记录
- **WHEN** 演示提问未检索到任何片段并给出拒答
- **THEN** 产生一条 `demo_miss` 事件，可用于后续聚合分析
