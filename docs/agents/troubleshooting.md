# 以 trace_id 为中心的排障指导

> **归属**：把「查 Bug / 查问题」统一收敛到 **trace_id 中心**的查询方式 —— 环境（本地·生产）×
> 渠道（站点·企微·飞书）的矩阵、SOP、症状→事件映射，以及"查不到就往哪加日志"的落点清单。
> 本文件只给**流程与路由**；"查什么用什么事件"的清单与字段口径以 `docs/agents/logging-rules.md`
> 为唯一归属，此处一律指针引用，不复制其正文。

## 1. 原则：trace-first

一次问答的全过程由**同一个** `trace_id` 串起来，它同时出现在四处（取值与白名单口径见
`docs/agents/glossary.md` 的 `trace_id` 词条）：

| 出现处 | 怎么拿 |
|---|---|
| 响应头 `X-Trace-ID` | 任意 HTTP 响应 |
| 应用日志**第 3 段** | 本地/生产日志文件（段位口径见 `docs/agents/logging-rules.md`） |
| SSE `done` 事件 | 流式回答的结束帧（契约见 `docs/agents/api_contract.md`） |
| Langfuse trace 根 | 与 `trace_id` 是**同一个 id**，浏览器里直接粘进去即可打开 |

**排障第一步永远是先拿到 trace_id**，再按「环境 §3 起 / 渠道 §4 起」分流。没有 trace_id 就开始
翻日志，只能靠时间窗盲扫。

## 2. 查不到 = 观测缺口

既有事件与日志不足以定位根因时，**不要再猜**，就地补观测：

1. **定位到该补哪一层** —— 用 §9 的落点清单，按"你要回答的是什么问题"选层（检索 / agent /
   LLM / 会话 / 渠道 / 工具）。
2. **用 `log_event` 输出、并在 `EVENT_SPECS` 登记** —— 禁止在调用点手拼日志文本；事件命名、
   前缀、级别、值类型编码的规范见 `docs/agents/logging-rules.md`。
3. **重跑复现一次**，用新事件的数据验证假设，而不是改行为去"试试看"。

> 这是硬规约，见 `docs/agents/rules.md`「问题排查规则 › 以 trace_id 为中心（trace-first）」。

### 2.1 零命中时，先排除"请求没到后端"

站点前端**自己在浏览器里生成** `trace_id`（`login.html` / `index.html` / `chat.html` 的
`generateTraceId()`），作为 `X-Trace-ID` 请求头发上来 —— 后端只是**沿用**这个值。

因此当请求**根本没到达后端**时，该 id **只存在于浏览器**，日志与 Langfuse 里必然一条都查不到。
最典型就是 **nginx 502**（上游容器刚被重建、nginx 还缓存着旧 IP —— 见 `defensive-patterns.md`
「重建上游容器后必须让 nginx 重解析」）。

**判据**（`grep` 全量日志零命中时按序走）：

1. **经 nginx 复现一次看返回码**：`502/504` = 上游不可达，**不是应用问题**，此时不该继续往应用侧找。
2. **看 nginx 侧**：`docker logs corporate-rag-nginx`（访问日志与 `error_log` 都在 stdout；
   应用日志在容器内 `/data/logs/`，两者位置不同）。
3. 排除后再回到 §2 的"观测缺口"流程。

> 别把"查不到 trace_id"直接判成"日志缺失/观测缺口" —— 先确认这次请求**有没有进来**。

## 3. 环境 × 渠道矩阵

| | 站点（web） | 企业微信（wecom） | 飞书（feishu，预留） |
|---|---|---|---|
| **本地** | 日志优先：容器内 `docker exec corporate-rag-app grep` 打 `trace_id`；Langfuse UI 辅助 | 同上；渠道段必为 `wecom` | 未接入 |
| **生产** | 日志优先：**SSH 到目标机**后 `docker exec corporate-rag-app grep`（见 §6）；Langfuse 作第二视图（保留期更长） | 同上；企微另有 SDK 侧事实见 `docs/agents/wecom-sdk-facts.md` | 未接入 |
| 日志保留 | `app_*.log` **7 天**、`error_*.log` **30 天** | 同左 | — |
| Langfuse 保留 | 30 天（自建清理，口径见 `docs/agents/glossary.md` 的「trace 保留期」） | 同左 | — |

**本地与生产的取日志命令形状相同**，区别只是"先 SSH 到目标机"这一跳（部署与挂载形态见
`docs/agents/deploy-runbook.md`）。

## 4. 渠道标识

共享管线的日志（agent / llm / db / retrieval）**本身不含渠道信息**，靠渠道段区分：

- **日志行第 5 段 `channel`**：取值 `web` / `wecom` / `feishu`（定义见 `src/config/const.py` 的
  `Channel`）；未设置时以 `none` 占位。段位契约见 `docs/agents/logging-rules.md`。
- **Langfuse `metadata.channel`**：同一取值，写在 trace 根（`src/services/agent_service.py`），
  可在浏览器按渠道筛 trace。
- **写入口**：各渠道在跑一轮之前写 `current_channel`（`src/infra/llm/trace_context.py`）——
  站点 `src/api/chat.py`、企微 `src/channels/wecom/handler.py`，并由
  `src/services/turn_runner.py` 在后台任务入口重设（链路见 `docs/agents/data-flow.md`
  「trace_id 贯穿」）。

**注意**：渠道在"确认要跑一轮"时才写入——回合**之前**的渠道侧日志（如启动锚点、未知机器人、
去重命中）会是 `none`，这是预期而非缺陷。

## 5. 本地 SOP

1. **复现**，并从响应头 `X-Trace-ID` 或 SSE `done` 帧记下 trace_id（前端也在流里带上它）。
2. **按 trace_id 捞日志**（app 日志写在容器内 `LOG_DIR`）：
   `docker exec corporate-rag-app grep "<trace_id>" /data/logs/app_YYYY-MM-DD.log`
3. **看关键事件**：命中事件名的含义与字段以 `docs/agents/logging-rules.md` 为准。
4. **要 session 维度**：日志行第 4 段即 `session_id`；按会话回放 SSE 帧的配方见
   `docs/agents/cookbook.md`「调试 › SSE 帧级核对」（**该配方只在生成结束后 5 分钟内可用**，
   原因同上条文档）。
5. **要更全的视图**（prompt 原文 / 工具 span / token / 耗时）：本地 Langfuse UI 按 trace_id 查，
   辅助定位。开关与查看条件见 `docs/agents/cookbook.md`。

## 6. 生产 SOP

应用日志**只在容器内**，取法只有一条路：

```bash
# 先 SSH 到目标机（地址见 docs/agents/deploy-runbook.md）
docker exec corporate-rag-app grep "<trace_id>" /data/logs/app_YYYY-MM-DD.log
```

为什么不能更省事（决定了排障手段的上限）：

- 日志目录是 **named volume**（非 bind mount）⇒ **宿主目录下 grep 不到**；
- 应用日志**不落 stdout**（无 console sink）⇒ `docker logs` / `docker compose logs app` **没有内容**。

因此生产的常规姿势是：**能 SSH 就用 §6 的 grep 拿一手日志**；**不方便 SSH 时用 Langfuse**
（浏览器即可访问，保留期 30 天，且带 prompt / 工具 span / token / 耗时），代价是看不到应用侧
事件（如闸门、落库前置、检索信号）。

## 7. 渠道差异（站点 vs 企业微信）

| 维度 | 站点（web） | 企业微信（wecom） |
|---|---|---|
| 入口 | HTTP 流式接口 | 长连接推送（SDK 回调） |
| `trace_id` 来源 | 中间件（可被 `X-Trace-ID` / `?trace_id` 覆盖，过白名单） | 服务端每轮 `new_trace_id()` 自生成 |
| `session_id` 来源 | 前端生成并传参 | 服务端按机器人/会话派生 |
| 回复通道 | SSE 帧 | 企微消息帧（终态 footer 带 `trace_id`） |
| 专属事实 | — | `docs/agents/wecom-sdk-facts.md`（被顶号、ack 节奏、feedback 路径等） |

**分渠道筛日志**：直接按第 5 段过滤，例如 `awk -F'|' '$5 ~ /wecom/'` 或 grep 渠道 token。

## 8. 症状 → 该先看哪里

| 症状 | 先看 | 细节归属 |
|---|---|---|
| 回答为空 / 一直"思考中" | 该 trace 是否触顶、是否有完整性检查事件 | `docs/agents/logging-rules.md`（事件全集与指标口径） |
| 反复检索 / 盲重试 | 检索行为信号行 | 同左（「已知例外」） |
| 答非所问 / 引用缺失 | 检索与验证事件 + Langfuse 的 prompt/工具 span | 同左 + `docs/agents/data-flow.md` |
| 不知道是哪个渠道来的 | 日志第 5 段 `channel` | 本文件 §4 |
| 企微不回 / 被顶号 | 渠道侧日志 + SDK 实测事实 | `docs/agents/wecom-sdk-facts.md` |
| 部署/端口/发布异常 | 部署槽位日志与运行形态 | `docs/agents/deploy-runbook.md` |
| 评测指标出现 `error` / 评测总耗时异常 | 该题 trace_id 下的 judge call（失败时为异常链摘要）与 provider hooks 请求级证据（仅失败时产日志） | 本文件 §10 + **ADR-0026** |

## 9. "查不到 → 加日志"落点清单

| 想回答的问题 | 该层 | 落点 | 用什么 |
|---|---|---|---|
| 检索取了几条、为什么重试 | 检索 | 检索层 | `log_event` + 检索类事件（登记见 `docs/agents/logging-rules.md`） |
| agent 迭代/状态机怎么走的 | agent | agent 主循环层 | 同左（`[agent]` 前缀） |
| 模型收到了什么、花了多少 token | LLM | LLM 调用层 | 同左（`[llm]` 前缀）；也可直接看 Langfuse generation |
| 闸门/落库/并发怎么判的 | 会话 | 会话与持久化层 | 同左（`[session]` / `[db]` 前缀） |
| 消息从哪个渠道进来 | 渠道 | 渠道入口 | 已有渠道段（本文件 §4）；渠道侧细节用 `[wecom]` 前缀 |
| 工具调用与委派 | 工具/委派 | 工具与 fork 委派层 | 同左（`[delegate]` 前缀 / Langfuse span） |

**加完必须两层一起动**：调用点用 `log_event` + 事件在 `EVENT_SPECS` 登记（漏登记 import 期即报错），
口径见 `docs/agents/logging-rules.md`。若新增的是**层**，还要登记 `LOG_PREFIXES` 并加进前缀主表。

## 10. 案例：RAGAS 评测失败——不要凭结果反推，把原始响应接上 trace

> **历史记录（换桥前）**：本案的证据字段（`finish_reason` / `completion_tokens` / 原始输出片段）来自
> 现代栈（instructor）时代的检测器；当前桥路径**不产**这些字段，现行排查口径见上方 §8 与本页 `judge call` 说明。

> 决策与证据 → **ADR-0024**（裁判客户端策略）。本节只记**排查方法**与**走错的路**。

**症状**：21 题评测跑 **1h31m**（其中仅约 8 分钟在计算）；**5/21 行**指标带 `error`；异常类名随配置漂移
（`IncompleteOutputException` / `InstructorRetryException` / `RuntimeError`）。

**走错的两条路（务必别重复）**：

1. **凭结果反推「prompt 太长 / 输入撑爆」** —— 实测否决：KB 分块长度均值 **652** 字符、最大 1,998；
   失败题的上下文只有 **5 条**（正常值）；prompt 里 35% 是 RAGAS 官方模板自带的固定开销。
2. **凭结果反推「模型生成失控、撞满 max_tokens」** —— 抓**原始响应**后否决：失败调用
   `finish_reason=**stop**`、`completion_tokens` 仅 **148–215**、原始输出是 `{ "classifications": [ { }, { } ] }`
   ⇒ 真因是**模型偶发吐「空结构」**，与"输出爆量"无关。

**本次奏效的做法**（正是 §2「查不到 = 观测缺口」的落地）：

1. 给裁判**每次尝试**加事件 `judge call`（`metric` / `q_index` / `trace_id` / `attempt` / `seconds` / `ok` / `note`）；
2. `note` 落**异常链摘要**（`_exc_chain_summary`，`外层类<-根因类: 根因消息`）；请求级证据（状态码 / 响应体可读性 / `finish_reason`）由裁判 client 的 event hooks 收集在 `HookEvidence` 桶，仅失败时进日志（provider hooks）；
3. `run_evaluation` 把每题的 `trace_id` 写进行 ⇒ 报告那列 `trace_id` 直达裁判调用。

**结论**：同一指标上**两种独立失败模式并存**——「空结构」（`finish_reason=stop`、输出极短）与
「截断」（撞 `max_tokens`）。**只记异常类名会把两者混成一个 `InstructorRetryException`**，
这就是此前数日无法定案的原因。

**怎么查**：

```bash
TID=$(python -c "import csv,glob,os;print(next(csv.DictReader(open(sorted(glob.glob('data/ragas/reports/*.csv'))[-1],encoding='utf-8-sig')))['trace_id'])")
python -m src.cli.trace_logs "$TID" --grep "judge call"   # 该题每次裁判尝试 + 失败时的异常链摘要（请求级证据另见 provider hooks 日志）
python -m src.cli.trace_logs "$TID" --grep "metric retry" # 重试原因（异常链摘要）
```

**可迁移的教训**：包装层的异常类名（`XxxRetryException` / `RuntimeError`）几乎总是**丢根因**的；
排障时优先把**被调用方（模型 / 下游）的原始返回**落到日志，再用 `trace_id` 把链路串起来 —— 这比
反复调整参数"试试看"快一个量级。

## 11. 相关文档

- 日志格式 / 前缀主表 / 事件全集 / 值类型 / 级别 / 指标口径 → `docs/agents/logging-rules.md`
- 操作配方（SSE 帧回放、取证不截断等） → `docs/agents/cookbook.md`
- 端到端链路 → `docs/agents/data-flow.md`
- 代码落点与结构 → `docs/agents/code-map.md`
- 术语 → `docs/agents/glossary.md`
- 接口契约（`X-Trace-ID` 头、`done` 帧、feedback 回传） → `docs/agents/api_contract.md`
- 企微 SDK 实测事实 → `docs/agents/wecom-sdk-facts.md`
- 发布 / 部署 / 日志挂载形态 → `docs/agents/deploy-runbook.md`
- 可观测后端（Langfuse）与保留期 → `docs/agents/glossary.md`
