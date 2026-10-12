## 1. Spike：验证主动推送（前置）

- [x] 1.1 写一次性探针脚本，用现有某台机器人凭证调 SDK `send_message(chatid, body)`，验证能否向**群**主动发出消息
- [x] 1.2 验证目标限制：未经交互的群/用户能否收到；记录失败错误码
- [x] 1.3 验证节奏与**频率上限**（两段式使消息数翻倍），记录连发表现
- [x] 1.4 在目标群 @ 一次机器人，从 `sessions.title`（`[企微·<bot_key>] 群聊 <chatid>`）读出 `chatid` 明文并登记（注意 `title` 仅首次落库生效）
- [x] 1.5 结论（可用性、失败码、频率上限）回写 `docs/agents/wecom-sdk-facts.md`

## 2. 配置

- [ ] 2.1 `src/config/` 新增告警配置：**token 用独立键**（`ALERT_WEBHOOK_TOKEN`，不塞进目标 JSON）、**目标群单目标 JSON**（`{bot_key, chatid}`，机器人复用 `dev`）、去重时间窗、整轮超时、固定 `user_id`（全零 UUID）、占位与兜底文案、兜底长度上限、query 模板
- [ ] 2.2 启动期校验（对齐 `src/config/wecom_bots.py` 的做法）：目标群 JSON 必填非空、非法即 fail-fast 且不回显敏感值；**token 为空视为"未配置"**，不阻断启动
- [ ] 2.3 同步 `.env.template` 与 `.env.example`（**两个文件都要改**）
- [ ] 2.4 渠道值 `ALERT` 进 `src/config/`（常量），文案类承载占位与兜底文本

## 3. 通道层：主动推送（wecom-channel）

- [ ] 3.1 先扩接口：现有通道驱动协议只有 `start/stop`，**不承载发送** ⇒ 新增稳定的发送出口（扩协议或新增服务方法），入参 = `bot_key` + 目标会话 + 内容、出参 = 成功/失败；docstring 写明**一次性发送、无流式、不可更新**
- [ ] 3.2 实现封装 SDK `send_message(chatid, body)`，body 用 `{"msgtype":"markdown","markdown":{"content":…}}`（实测形状，见 `wecom-sdk-facts.md` E12）
- [ ] 3.3 目标未配置 / 机器人不存在 / 连接未就绪 → 记 warning 并返回失败，**不回落到默认会话**
- [ ] 3.4 发送失败记含**目标会话标识与错误码**（`93006` / `40008` 等）的日志，值按日志规约编码
- [ ] 3.5 单测：成功 / 目标未配置 / 发送失败 三条路径

## 4. 告警接收端点

- [ ] 4.1 新增 `POST` 端点；token **从请求头读取**，用 `hmac.compare_digest` 做常数时间比较
- [ ] 4.2 token 不匹配或未配置 → 返回 **404**；端点不依赖登录态
- [ ] 4.3 **校验 + 去重通过后立即返回成功应答**（须在上游 5 秒硬超时内）；应答**不表达**分析结果；**去重丢弃时同样返回成功应答**（不得为错误码，否则上游会重试）
- [ ] 4.4 **层间规则**：`api/` 只做路由与转发；载荷解析、去重、编排一律落 `services/`（如新增 `services/alert_service.py`），`api/` 不得直接 import `config/`、`infra/`
- [ ] 4.5 载荷解析：`alarmId` / **逐条** `alerts[].fingerprint` / `commonLabels.alertname` / `level` / `alerts[].annotations.message`；缺必填或 JSON 非法 → 记 warning + 可识别错误响应（不得抛未处理异常）
- [ ] 4.6 单测：鉴权三态（正确/错误/未配置）；立即应答（模拟慢分析不拖住响应）；去重丢弃仍为 2xx；载荷正常 / 多条 alert / 缺字段 / 非法

## 5. 入口去重

- [ ] 5.1 在 `services/` 层对**每一条** alert 的 `fingerprint` 取键 + 可配置时间窗的**有界**结构（带 TTL 与容量上限）
- [ ] 5.2 **全部**指纹命中窗口才丢弃并记 info；存在未命中即正常处理
- [ ] 5.3 单测：全部命中 / 部分未命中 / 结构有界

## 6. 报警分析编排

- [ ] 6.1 派生报警会话标识 = `uuid5(NS, "wecom|<bot_key>|alert|<alarmId>")`：**同一报警标识复用同一会话、不同标识各自独立**（与群聊/单聊问答会话隔离）
- [ ] 6.2 装配 `start_turn`：固定 `user_id`（全零 UUID）、`kb_id=""`、`agent=""`，title 取 `[企微·<bot_key>] 报警 <alertname>`；**query 由 `src/config/` 的模板构造**
- [ ] 6.3 渠道上下文：在**后台协程内、`start_turn` 之前**设置 `ALERT`（`create_task` 会拷贝上下文；在 handler 里 set 晚了会落 `none`）
- [ ] 6.4 禁澄清：**构造期编译两套图**（含 / 不含 `ask_user`）并按轮次选择，同步 `ctx.tool_names`、段渲染判据与 fork 工具池。⚠️ 这不是"透传一个参数" —— 图在 `AgentService.__init__` 编译一次即固化；`CLARIFICATION_ENABLED` 是**死配置**、`ask_user` 目前**无条件注册**
- [ ] 6.5 整轮超时 + **真取消**：`wait_for` 包住事件消费**不够**（停不了生成）；需在进程内新开 cancel 口子（`StreamingRunManager` 无公开 `task.cancel`；`POST /api/sessions/cancel` 因归属校验对报警会话无效）⇒ 超时即中止生成，该轮按取消路径落 `interrupted`、**不作为结论**；阈值取 `src/config/`
- [ ] 6.6 去重通过后**立刻推受理占位**（文案取 config）——**必须在后台任务内发，不得位于响应路径**（等回执的超时为 5s，会撞上游硬超时）；失败只记 warning，不阻断分析、不落历史
- [ ] 6.7 正常完成 → 取该轮**净化正文**（复用生成侧同一套分拣逻辑，不得自行累积 token、不回读 DB）推**结论**到目标群
- [ ] 6.8 单测：会话标识复用/独立、固定归属、query 模板渲染、无澄清可用、超时中止生成、占位不阻塞响应、两段推送顺序与失败隔离

## 7. 兜底

- [ ] 7.1 抛错 / 超时 / **结论为空** / **`TurnBusy`** → 把原始告警按模板渲染后转发到目标群（**带 `[原始告警]` 前缀**、限长、**禁止整包 dump**）
- [ ] 7.2 兜底消息**不写入**报警会话历史
- [ ] 7.3 单测：四条触发路径各自生效；前缀与限长；不落历史

## 8. 契约与文档

- [ ] 8.1 `docs/agents/api_contract.md` 登记新端点（路径、**请求头鉴权**、请求/响应形状、5 秒应答约束、历史踩坑）
- [ ] 8.2 `docs/agents/code-map.md` 登记新增模块与改动落点
- [ ] 8.3 `docs/agents/logging-rules.md` 登记渠道值 `ALERT`
- [ ] 8.4 `docs/agents/deploy-runbook.md` 补：告警 token 与目标群随 `.env` 上机；ARMS 侧「通用 Webhook + 自定义 Header」配置步骤；**我们规定的自定义 body 模板正文（必需项，字段名即解析契约）**；首次取 `chatid` 的做法（含 `title` 仅首次生效）

## 9. 部署与验收

- [ ] 9.1 目标机 `.env` 补 token 与目标群（**先同步 `.env`，再上新制品**）
- [ ] 9.2 阿里云侧建「通用 Webhook」通知对象（**Header 带 token**，**按我们规定的模板正文填自定义 body 模板**）并在通知策略中引用；用「发送测试」抓一份真实 body 核对字段名
- [ ] 9.3 抓一份真实 payload 落日志（脱敏），确认字段齐备（与规定模板一致）与 `alarmId` 稳定性，校准 design 的 Open Questions
- [ ] 9.4 真实触发一次告警 → 确认群里**先收到受理占位、后收到结论**
- [ ] 9.5 制造一次分析失败 → 确认兜底转发生效且带 `[原始告警]` 前缀
- [ ] 9.6 确认端点在上游 5 秒内返回（用真实 ARMS 触发，观察有无超时重试）
- [ ] 9.7 确认报警会话不出现在普通用户的会话列表
- [ ] 9.8 端到端验收（9.4/9.5）**必须在能连企微通道的环境**做 —— 本地 `WECOM_BOT_ENABLED=false`、探针在非生产机跑过，本地验证替代不了
