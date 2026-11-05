# streaming-run Specification

## Purpose
TBD - created by archiving change streaming-decouple. Update Purpose after archive.
## Requirements
### Requirement: 后台生成任务与连接解耦

系统 SHALL 将 agent 循环置于后台 asyncio 任务中运行，与 SSE 连接解耦；StreamingRunManager SHALL 持有任务强引用（`_session_tasks`），任务完成/取消/异常后自动注销。客户端断开 SSE 时，系统 SHALL 仅停止推送事件，不得取消后台生成任务。

#### Scenario: 断连后生成继续
- **WHEN** 客户端断开 SSE 而任务正在生成
- **THEN** 后台任务继续执行，事件持续写入缓冲，不触发中止

#### Scenario: 任务自动注销
- **WHEN** 后台任务正常完成、被取消或抛异常
- **THEN** 任务从 `_session_tasks` 注册表注销，不留残留引用

### Requirement: 事件带 seq 写入缓冲

系统 SHALL 为每个流式事件分配递增 seq（per-session），并写入该 session 的事件缓冲；done/error 终态事件同样入缓冲。

#### Scenario: 事件按序入缓冲
- **WHEN** 任务产出 status / token / citation / ask_user / done 事件
- **THEN** 缓冲中事件按 seq 递增排列，done 事件标记当前轮结束

### Requirement: 缓冲生命周期按当前轮管理

系统 SHALL 在新一轮生成（`POST /api/chat/stream`）时清空该 session 的上一轮缓冲；缓冲容量上限 2000 条，终态后 5 分钟 TTL 惰性清理。

#### Scenario: 新一轮清空旧缓冲
- **WHEN** 新一轮 `POST /api/chat/stream` 启动
- **THEN** 该 session 缓冲被清空，仅保留当前轮事件

#### Scenario: 缓冲超上限丢弃最旧
- **WHEN** 缓冲条目超过 2000 条
- **THEN** 丢弃最旧条目，保留最近的 1950 条（超长回答丢头为已知限制）

### Requirement: 断点续接接口

系统 SHALL 提供 `GET /api/sessions/events`（SSE）：按 `after_seq` 回放缓冲中 seq 大于该值的事件，并持续推送实时新事件，直到遇到 done/error 终态；无缓冲时立即返回 done。回放事件的事件名与 payload 格式 SHALL 与实时流完全一致（含 done 事件的 trace_id），前端复用同一渲染路径。

#### Scenario: 刷新后从断点续接
- **WHEN** 前端以 `after_seq=N` 请求续接且缓冲存在
- **THEN** 服务端回放 seq>N 的事件并持续推送新事件，遇 done 收尾

#### Scenario: 无缓冲兜底
- **WHEN** 请求续接而该 session 无缓冲
- **THEN** 立即返回 done，前端转历史加载

#### Scenario: tail 空闲超时
- **WHEN** 回放后超过 180s 无新事件（如任务等待澄清或异常卡住）
- **THEN** 返回 error（"续传超时，请刷新页面"）并结束连接，前端提示刷新

### Requirement: 历史响应携带消息状态

历史接口 `POST /api/sessions/messages` 返回的 MessageItem SHALL 携带 `status` 字段（`complete` / `interrupted`），前端据此在历史中标记被中断的回答。

#### Scenario: 中断回答带状态返回
- **WHEN** 历史中某条 assistant 消息为中断的部分回答
- **THEN** 该消息的 status SHALL 为 `interrupted`，完整回答为 `complete`

### Requirement: 任务状态查询

系统 SHALL 提供 `GET /api/sessions/task-status`：缓冲存在且无终态 → `generating`（含可续接标记与当前缓冲 seq）；缓冲有终态或关系型库存在 assistant 消息 → `completed`；无缓冲且无 assistant → `idle`（不得返回 generating）。

#### Scenario: 生成中可续接
- **WHEN** 缓冲存在且无 done/error 终态
- **THEN** 返回 `status=generating` 与当前缓冲 seq

#### Scenario: 进程死亡兜底
- **WHEN** 无缓冲且无 assistant 消息
- **THEN** 返回 `status=idle`，前端不得持续轮询或显示生成中

### Requirement: 显式取消接口

系统 SHALL 提供 `POST /api/sessions/cancel`：StreamingRunManager SHALL 维护 `session_id → abort_signal` 映射（任务启动时登记、完成时注销），cancel 端点从映射取信号置位；agent 循环在步骤边界检查并中止；任务中止时已产出 token 则写部分回答（`status=interrupted`），并写入带 cancelled 标记的 done 事件。

#### Scenario: 点停止中止生成
- **WHEN** 用户调用 cancel 而任务正在生成
- **THEN** abort_signal 置位，任务在下一边界中止，停止 LLM 调用与 token 消耗

#### Scenario: 无活跃任务取消
- **WHEN** 调用 cancel 而该 session 无活跃任务
- **THEN** 返回未取消（no_active_task），不产生副作用

### Requirement: 新接口鉴权与归属校验

resume/status/cancel 接口 SHALL 复用现有 cookie 鉴权中间件，并对 session_id 做归属校验：会话存在且属于当前用户才放行，否则返回 404。

#### Scenario: 越权访问被拒
- **WHEN** 用户请求不属于自己的 session 的 resume/status/cancel
- **THEN** 返回 404，不泄露会话状态

### Requirement: 来源声明复用 status 事件的流契约

系统 SHALL 复用既有 `status` 事件承载每轮来源声明（生效智能体 / 本轮技能动作），并遵守以下流契约：

- **stage 取值专用**：来源声明 SHALL 使用专用于来源语义的 `stage` 取值（`turn_agent` / `turn_skill`），SHALL NOT 复用会改变正文/旁白判定的工具态取值（如检索、联网）。
- **逐轮各至多一次**：同一轮生成内，每类来源声明 SHALL 至多产出一条；不随 agent 多轮迭代或验证重生成重复。
- **顺序与时机**：产出顺序 SHALL 为「智能体声明 → 技能声明 → 既有节点状态行」，且两条声明 SHALL 先于同一轮的澄清（ask_user）与委派（delegate）事件进入缓冲。
- **条件不满足不产出**：未绑定智能体、本轮无技能动作、或命令失败时，对应声明 SHALL NOT 产出（不得输出空行或占位）。
- **结构化真源不变**：`agent_used` SHALL 继续作为生效智能体的结构化来源；消费端 SHALL NOT 从来源声明的文案文本反解结构化值。
- **向后兼容**：消费端 SHALL 容忍未知 `stage` 取值（忽略或以通用状态行渲染），不得因新增 stage 报错。

#### Scenario: 复用 status 承载来源声明
- **WHEN** 一轮生成声明了生效智能体与本轮技能
- **THEN** 该轮缓冲中出现两条 `status` 事件，`stage` 为来源专用取值，`message` 为用户可见文案

#### Scenario: 每轮至多一次
- **WHEN** 一轮生成内 agent 迭代多次并触发工具调用
- **THEN** 每类来源声明仍只出现一次

#### Scenario: 先于澄清与委派事件
- **WHEN** 同一轮随后产生 ask_user 或 delegate 事件
- **THEN** 两条来源声明在缓冲中的顺序早于这些事件

#### Scenario: 条件不满足不产出
- **WHEN** 本轮未绑定智能体且无技能动作
- **THEN** 缓冲中不出现来源声明事件，也不出现空文案的 `status`

#### Scenario: 消费端容忍未知 stage
- **WHEN** 消费端收到来源专用 stage 的状态事件
- **THEN** 它按通用状态行正常渲染，不报错；且不因该文案改变正文与旁白的判定口径

#### Scenario: 文案不作为结构化真源
- **WHEN** 消费端需要本轮的生效智能体值
- **THEN** 它从 `agent_used`（结构化）取值，而非解析来源声明的文本
