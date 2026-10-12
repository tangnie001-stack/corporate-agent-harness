# 企微智能机器人：官方 aibot SDK 行为事实表

> **归属**：企微通道层的 **SDK 实测行为**唯一来源（结论 + 证据 + 对设计的影响）。
> 用途：企微通道/投影层的实现与排障、`wecom-agent-bridge` change 的 design 输入。
> 维护：结论随 SDK 升级或实测重跑而更新；**每条结论必须带证据**（原始帧/日志/人眼观察）。

**实测环境**：`wecom-aibot-python-sdk==1.0.2`（导入名 `aibot`），机器人 `dev`，单 worker 进程内探针，
脚本 `scripts/spike/wecom_sdk_probe.py`（一次性，跑完即删），日期 **2026-10-08**。

---

## 一、快照语义（E1）✅ 已确认

**结论**：企微**每次刷新整段替换**气泡 ⇒ 每帧内容必须是**截至该帧的累积全文**，不是增量。

**证据**：同一 `stream_id` 依次发 `甲` → `甲乙` → `甲乙丙` → `甲乙丙`(finish)；4 帧全部 ack（0.25~0.45s）；
人眼确认气泡最终为 **`甲乙丙`**，且过程是**整段替换**（不是 `甲甲乙甲乙丙` 那样拼接）。

**影响**：投影层的「快照式流式投影」需求成立，**无需修订投影 spec**；`WeComPresenter` 的累积实现正确。

---

## 二、重复帧与空白帧（E2）✅ 已确认

**结论**：内容与上一帧**完全相同**的帧、以及**仅含零宽字符**的帧，平台**都接受（ack 成功）**，且**不产生可见瑕疵**（无空气泡、无空行）。

**证据**：帧序列 `甲` → `甲`(同内容) → `\u200b` → `甲`(finish)，全部 ack；人眼确认"只有一个 `甲`，没有多余气泡"。

**影响**：实现里的「内容未变跳过 / 空白不发」是**优化而非必需**——即便发出去也不会坏（这降低了节流实现的正确性压力）。

---

## 三、认证等待与失败可见性（E3）✅ 已确认

**结论**：
1. **`connect()` 不等认证就返回**。
2. 认证失败**不抛异常**（`connect()` 正常返回），只发 **`error` 事件**，内容含 `errcode=853000` / `errmsg=invalid bot_id or secret`。
3. 认证失败后 SDK **不断开、不重连**；60 秒观察窗内**服务端也没有关闭**该连接 ⇒ 会留下一个"未认证但 TCP 挂着"的连接。

**证据**：
```
+0.309s connect() 返回（尚未认证）
+0.477s 事件 authenticated（距 connect() 返回 +0.168s）
—— 错 secret ——
+0.195s connect() 返回（无异常）
事件序列：connected → error('Authentication failed: invalid bot_id or secret ... (code: 853000)')
60s 内无 disconnected / reconnecting；服务端未关闭
```

**影响**：
- 驱动的"逐台 try/except `await connect()`"若只看返回值是**死代码** ⇒ 必须**等 `authenticated` 事件**判定就绪。
- 凭证类失败的**判据**：`errcode=853000`（或 `errmsg=invalid bot_id or secret`），**不是**普通 `on_error`。
- "认证失败 → 主动 `disconnect()` + 停重连"仍应做：避免留下一个挂着的未认证连接。

---

## 四、被顶号（E4）✅ 已确认 —— **确实会互踢，周期不固定**

**结论（三步机制）**：
1. **"被顶"由业务事件告知**：新连接认证后，服务端**立刻**给**旧连接**推 `event.disconnected_event`
   （`body.event.eventtype == "disconnected_event"`）。此刻**旧连接的 WS 毫无异常**（无 `disconnected`、心跳照常）。
2. **服务端随后才关闭旧连接，延迟不确定**：实测两次为 **313 秒** 与 **2.2 秒**（关闭原因 `no close frame received or sent`）。
3. 该关闭触发 SDK 的 `disconnected` → `reconnecting`（1s 退避）→ **重连成功**；而**重连成功的一方会立刻顶掉另一方**
   ⇒ **互踢循环自我维持**。

**证据**（三次独立运行）：
```
E4#1（只观察 90s）：A 收到 disconnected_event；A 无可感知异常（观察窗太短，误判为"不会互踢"）
E4#2（观察 405s）：
  A: connected → authenticated → disconnected('no close frame received or sent') → reconnecting(1) → connected → authenticated
  B: connected → authenticated
E4#3（专门观测 420s）：
  t=0.61s   A 收 disconnected_event（被顶）
  t=313.2s  A 被服务端关闭 → reconnecting(1) → t=314.5s 重连成功 → t=314.7s **B 收 disconnected_event**
  t=316.9s  B 被服务端关闭（距被顶仅 2.2s）→ reconnecting(1) → t=318.1s 重连成功 → t=318.3s **A 收 disconnected_event**
```

**影响**：
- 设计里担心的「**无限互踢**」**确实存在**（此前基于 90 秒窗的结论作废）；互踢间隔**不固定**（313s / 2.2s），可能很快。
- **修法**：订阅 `disconnected_event` → 记 warning + 标记该台不就绪 + **主动 `disconnect()` 且不再抢回**。
  只"停重连"不够——必须在收到该事件时**就断开自己**，否则服务端仍会在它自己的时机关闭并触发 SDK 重连。
- ⚠️ **当前生产驱动未订阅该事件**（`src/channels/wecom/long_connection.py` 的 `_EVENT_EVENTS` 只有
  `enter_chat` / `template_card_event` / `feedback_event`）⇒ 一旦发生（滚动重启、双进程、探针与生产并存）
  会**静默地无限互踢**，日志里看不出原因。
- **归属跟着"最新连接"走**：A 重连成功后 B 立刻被顶 ⇒ 消息归属随最新认证的连接转移。

---

## 五、帧节奏与 ack（E6）✅ 已确认

**结论**：单帧 ack 往返 **0.235~0.451s**（均值约 0.26s）；同一 `req_id` **串行**发送；
连发 30 帧 + 终态共 **8.37s**，**无 ack 超时**（SDK 的 5s 超时未触发）。

**证据**：30 帧逐帧 ack 日志，`总耗时 8.371s`；日志无 `Reply ack timeout`。

**影响**：实际发送节奏被 **ack RTT（~250ms/帧）天然压住** ⇒ 100ms 的最小间隔不会成为瓶颈；
30 帧远未触及 SDK 队列上限（`_max_reply_queue_size=100`）。

---

## 六、Markdown 渲染能力（E8）✅ 已确认

**结论**：企微气泡**全量渲染** Markdown：一级/二级标题、无序列表、有序列表、粗体、斜体、行内代码、
**代码块**、**表格**、链接、分隔线（`---`）。

**证据**：一条混排消息（各类语法 + 表格 + 代码块 + 分隔线 + 末尾「参考来源」段）**全部按预期渲染**。

**影响**：「参考来源」可用 Markdown 列表（当前实现）甚至表格；正文可用代码块。

---

## 七、trace_id 闭环（E10）✅ 已确认

**结论**：
1. **`feedback` 承载可用**：首帧带 `feedback={"id": ...}` → 该条消息出现 👍/👎 按钮；用户点赞后**回执帧到达**，字段路径 = **`body.event.feedback_event.id`**，值**原样回传**。
2. **回执帧同时带 `msgid` 与 `msgtype`** ⇒ **不会被 SDK 的 `msgtype` 门禁丢弃，也不会被 `parse_inbound` 的 `msgid` 必填丢弃**。
3. 终态 footer **可见**（`trace_id: ...` 随终态帧出现；需等终态帧发出才看得到）。

**证据**：
```json
{ "msgid": "76a963c5...", "aibotid": "aib2...", "chattype": "single", "msgtype": "event",
  "event": { "eventtype": "feedback_event",
             "feedback_event": { "id": "trace_spike_e10", "type": 1 } } }
```
（`type: 1` = 点赞）；人眼确认 👍/👎 按钮出现、footer 行可见。

**影响**：
- trace_id **三路全部可用**（首帧 feedback.id + 日志 + 终态 footer）⇒ footer **不必强制开启**，按配置开关即可。
- **阶段 3 的「事件帧解析分流」（tasks 3.9）不必做** —— 反馈回执不缺必填字段。
- 桥接 handler 只需**显式处理** `event_type == "feedback_event"`（取 `feedback_event.id` 落日志）。

### 七之二、`feedback.id` 的取值约束（E10b，2026-10-08 补测）

**结论**：`feedback.id` **必须以 `trace_` 开头**；否则平台**静默忽略**（帧照常被 ack、**不报错**、但**不显示 👍/👎 按钮**）。长度无关。

**证据**（同一机器人、同一入站 req_id，两条独立 stream）：

| id | 长度 | 前缀 | 按钮 | ack |
|---|---|---|---|---|
| `trace_spike_e10`（E10） | 15 | `trace_` | ✅ 有 | ✅ |
| `spike_short`（E10b-A） | 15 | `spike_` | ❌ 无 | ✅（**无错误**） |
| `trace_<uuid>`（E10b-B） | 42 | `trace_` | ✅ 有 | ✅ |

**影响**：
- 我们的 `new_trace_id()` 产物形如 `trace_<uuid>` ⇒ **天然满足该约束**，无需改造。
- 若将来改用别的 id 形状（如裸 uuid、`span_…`）会**静默失去反馈按钮**——这条约束因此必须留在事实表里。
- 排障启示：反馈按钮不出现时，**平台不会报错**，只能从"id 前缀"与"是否挂在首帧"两处查。

**E10c（2026-10-08 已结案）**：活体验收中"真实回复未出现按钮"经**插桩复跑**判定为**观察误差**——投影层帧日志实测首帧 `index=1 finish=False chars=7 feedback_id=trace_5e4caf1c-…`（即**本轮** trace），按钮正常出现；同轮 8 帧（`finish=False` ×7 + 终态 `finish=True`）与快照累积均符合 spec。
⇒ **反馈链路（首帧带 `feedback.id` = 本轮 trace_id）实测正确**，无需改造。
（副产品：插桩坐实了"投影层此前无任何发送日志"这个可观测性缺口，已在 `channels/wecom/presenter.py` 补 `[wecom] reply frame sent index=… finish=… chars=… feedback_id=…` 常驻 INFO 日志。）

---

## 八、首帧前时限（E11）◐ 部分结论

**结论**：收到入站后**空等 120 秒**再发第一帧，**发送成功** ⇒ 至少 2 分钟级**没有**"首帧前时限"。
（6 分钟级的静默未测。）

**影响**：占位首帧仍是好实践（用户体验 + 规避未知时限），但不是"必须抢时限"的紧迫项。

---

## 八之补、卡片点击回调（E5）✅ 已确认 —— 可用（超出预期）

**结论**：`button_interaction` 卡片发送成功；用户点击后回调到达，**按钮 `key` 与 `task_id` 都原样回传**。

**证据**（用户点"选项乙"）：
```json
{ "msgid": "f95549ab...", "chattype": "single", "from": {"userid": "…"},
  "msgtype": "event",
  "response_url": "https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=...",
  "event": { "eventtype": "template_card_event",
             "template_card_event": { "card_type": "button_interaction",
                                      "event_key": "spike:opt:2",
                                      "task_id": "spike_e5" } } }
```
可用的最小卡片 schema：
```python
{"card_type": "button_interaction", "task_id": "<关联令牌>",
 "main_title": {"title": "...", "desc": "..."},
 "button_list": [{"text": "选项甲", "style": 1, "key": "spike:opt:1"}, ...]}
```

**影响**：澄清卡片（二期）**可直接落地**：
- 字段路径 = **`body.event.template_card_event.event_key`**（选项）+ **`.task_id`**（关联令牌）
- `task_id` 原样回传 ⇒ 天然充当"卡片 ↔ 挂起请求"的关联键（不必自建身份编码）
- 回调帧带 `from.userid` / `chattype` / `msgid` / `msgtype` ⇒ 可辨触发者，且过得了 SDK 门禁与 `parse_inbound`

---

## 九、长流保活（E7）✅ 已确认

**结论**：每 ≤4 分钟发一次非终态帧，流可**存活到 8.5 分钟以上**；6 分钟关口**没有**切断它。

**证据**：
```
t=0        「E7 开始（t=0）」      ack 0.461s
t≈4min     「E7 保活 #1」          ack 0.295s
t≈8min     「E7 保活 #2」          ack 0.460s
t≈8.5min   「E7 结束 —— …」终态    ack 0.397s   ← 越过 6 分钟仍能发
全程 0 条 ack 超时 / 0 条 invalid req_id / 无断连 / 无重连
```

**影响**：4 分钟保活策略**有效**（阶段 2 配置的 240s 可用）；6 分钟时限在本 SDK + 平台上**未表现为硬断**。
保守起见仍按"保活续命"实现（不依赖"没有时限"这一结论）。

**未测**：真正的 6 分钟静默（完全不发帧）是否被断 —— 未做（耗时长且非必要，因为我们的实现总会发保活帧）。

---

## 十、其他实测事实（非实验项但有用）

| 事实 | 证据 | 用途 |
|---|---|---|
| 入站**消息**帧带 `response_url`（`https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=...`） | 文本消息原始帧 | WS 之外的一次性主动补发兜底（CowAgent 同款） |
| **同一 `req_id` 可反复多轮流式回复**（含多次 `finish=true`） | 5 个实验共用同一 req_id，全部 ack | 不必"一条入站只能回一次" |
| **事件帧也带 `msgid`** | `enter_chat` / `feedback_event` / `disconnected_event` 帧 | 去重与解析可用 |
| **`enter_chat` 的 req_id 不能用于 `reply_stream`** | 实测 `errcode=846605 invalid req_id` | 欢迎语必须走 `reply_welcome`；桥接 handler 必须跳过事件帧 |
| 发送失败**以 `RuntimeError` 从 `reply_stream` 抛出** | `Reply ack error: errcode=846605 ...` | 投影层的降级路径（捕异常 → 一次性收尾）设计吻合 |
| 同进程多 client 时 SDK 自带日志**无法区分连接**（前缀同为 `AiBotSDK`） | E4 两连接日志交织 | 多连接排障需自行加标签 |
| SDK 常量 | 读源码 `aibot/ws.py` | `_reply_ack_timeout=5.0s`、`_max_reply_queue_size=100`、`heartbeat_interval=30000ms`、`max_reconnect_attempts=10`、`reconnect_interval=1000ms` |

---

## 十之二、主动推送 `send_message`（E12）✅ 已确认

**结论**：主动推送**可用**，但**不支持流式** ⇒ "先回执、后结论"只能做成**两条独立消息**。

**证据**（2026-10-12，机器人 `dev`，目标群 `wrXoYADQAAe1pspykyEgdg5BdrmN9Q5A`；探针 `scripts/spike/wecom_send_message_probe.py`，出口 IP `114.92.159.22` 即本机**非**生产机）：

```
E1  {"msgtype":"markdown","markdown":{"content":"…"}}   → 272ms  errcode=0 errmsg=ok
E3  {"msgtype":"stream","stream":{…}} ×3                → 99/99/90ms  errcode=40008 invalid message type
E4  chatid=SPIKE_INVALID_CHATID                         → 121ms  errcode=93006 invalid chatid
E5  连发 10 条 markdown                                  → 成功 10 / 失败 0，总耗时 3023ms
```

人眼确认：群里收到 11 条（E1 一条 + E5 十条）。

1. **body 形状** = `{"msgtype": "markdown", "markdown": {"content": "…"}}` —— 与 `reply_stream` 的
   `{"msgtype":"stream","stream":{…}}` 同款约定（`msgtype` + 同名子对象）。SDK 会把它与 `chatid` 合并后发送。
2. **无流式/无刷新**：`msgtype=stream` 被拒（`40008`）⇒ 主动推送**不能**像 `reply_stream` 那样更新同一气泡
   （`reply_stream` 的多帧快照、`update_template_card` 都要求**入站帧的 `req_id`**）。
3. **错误判据**：失败以 `RuntimeError`（`Reply ack error: errcode=…`）从 `send_message` 抛出；
   **目标非法 = `93006`**，**消息类型非法 = `40008`**。单次往返 261~377ms。
4. **节奏**：连发 10 条无失败、无超时，≈300ms/条（含 ack）；未触及频率上限。

**未交互也能发**（E12b，2026-10-12 补测）：用 **`support`**（该群从未与它交互过 —— 库里只有
`[企微·dev] 群聊 wrXoYA…` 这一条会话）向 `dev` 取得**同一个 chatid** 发送 → `errcode=0`（303ms），
**人眼确认群里出现该消息**。

⇒ `chatid` 是**群维度、与机器人无关**（这也解释了会话 key 为何要额外带 `bot_key` 来区分）；
**发送不要求该机器人先与群交互过**。但**获取** chatid 仍只能靠一次入站帧（企微无"列群"API）
⇒ "写死群 id"的方案仍需先 @ 一次**播种**，播种一次后**三台机器人共用**。

**影响**：
- 「受理占位 + 结论」**必须分两次 `send_message`**，不能做成同一气泡刷新（见 change `alibaba-alert-notify` 的 design D15）。
- 排障时按 `93006` / `40008` 区分"目标不存在"与"消息体形状错"。
- 报警群无需"先 @ 一次才能发" ⇒ 落地时只要把 chatid 配进去即可（chatid 本身仍需采集一次）。

---

## 十一、对设计与计划的回写清单

| 项 | 处理 |
|---|---|
| design **D5**（快照语义） | ✅ 实测确认，**无需修订**投影 spec |
| design **D6**（节流/上限/保活） | 保活 240s **有效**（E7：8.5 分钟以上不断）；100ms 节流不是瓶颈（E6：ack RTT ~250ms 已是天然下限） |
| design **D9**（trace_id 三路） | 三路均可用；footer 按配置开关（**不必**"反馈不可用时强制开启"）；接线字段 `body.event.feedback_event.id` |
| design **D11**（驱动可靠性） | **改写**：① 认证——必须等 `authenticated` 事件；凭证类失败判据 = `errcode=853000`；认证失败后 SDK 不断连 ⇒ 需主动 `disconnect`；② 被顶——**订阅 `disconnected_event`，收到即主动断开且不再抢回**（否则无限互踢；实测周期 313s / 2.2s，不固定） |
| design **D14**（澄清） | 二期卡片路**已验证可用**：`task_id` + `event_key` 天然构成"关联令牌 + 选项"编码 |
| tasks **0.x（组 0）** | 全部完成（E1–E4、E6–E8、E10、E11 已确认；E5 意外可用） |
| tasks **3.8**（消费 feedback_event） | 保留；字段名已确定 |
| tasks **3.9**（事件帧解析分流） | **删除** —— 反馈回执同时带 `msgid` 与 `msgtype`，不会在 SDK 门禁或 `parse_inbound` 被丢弃 |
| tasks **3.4/3.5/3.6** | 桥接 handler **必须跳过事件帧**：`enter_chat` 的 req_id 不能用于 `reply_stream`（实测 `errcode=846605 invalid req_id`） |
| 驱动 `_EVENT_EVENTS` | **补** `event.disconnected_event`（当前缺失 ⇒ 无限互踢且静默） |
| 阶段 4 驱动补丁清单 | 以 E3 + E4 的结论替换原清单（`authenticated` 判定 + `853000` 判据 + `disconnected_event` 处置） |
| 阈值（阶段 2 配置模块） | 100ms / 85 / 200000 / 240s 均可保留 |
