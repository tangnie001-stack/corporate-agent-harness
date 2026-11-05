## Context

当前应用只有两类入口：正式站（`www`，需登录）与企微长连接。`chat.html` 打开即调用 `/api/kbs/list`，该接口在 `src/middleware/auth.py` 中强制要求登录 token；而 `/api/chat/stream` 与 `/api/sessions/*` 已支持匿名 `user_id` Cookie 兜底。

关键事实（已核实）：

- `src/api/chat.py` 直接使用请求体里的 `kb_id` 发起检索，**不校验归属**——这是 demo 最大的数据出口风险。
- `src/api/auth.py` 的登录接口对不存在的账号会先自动注册；demo 身份绝不能走这条路径。
- 应用为单 worker，流式任务表在进程内；因此配额不能放内存。
- 应用机 2C8G，已跑 app / postgres / redis / minio / langfuse；demo 与正式站共享这些资源。
- 现有知识库 `test` 混有 4 家公司的财报（东软、灿坤、腾讯），不适合直接当演示库。

## Goals / Non-Goals

**Goals:**

- 提供一个"发出去别人自己跑"的 demo 入口：免登录、可直接对话、能讲清演示范围。
- 保证数据边界：演示身份只能访问演示知识库，不能读写其他任何知识库。
- 保证成本可控：轮数、IP 频率、全站 token 三层上限，且重启不清零。
- 关停可秒级生效，不影响正式站。

**Non-Goals:**

- 不做独立部署/独立数据库（无真实数据阶段不划算；毕业条件见 D1）。
- 不做访客上传文档（放 A2，需要临时 KB + TTL 清理）。
- 不做人机验证（Turnstile/阿里云验证码放 A2，先靠限流与蜜罐）。
- 不做会话分享、多租户、demo 看板（后续阶段）。

## Decisions

### D1 同实例 + `DEMO_MODE` 开关，不拆独立实例

当前没有交付、没有真实流量，拆独立实例要多一套 compose/库/key/域名，收益为零。风险由"演示库 + 强制 kb 覆盖 + 配额"三项兜住。

**毕业条件（硬性）**：一旦有真实客户数据入库，demo 必须迁到独立实例（不同库、不同模型 key）。这条写进 runbook，不靠记忆。

**Alternatives**：独立实例（更干净但成本高，暂不需要）；只读回放（说服力弱）。

### D2 入口用子域名 `demo.tangnie.cn`，不用路径 `/demo`

- 子域名天然隔离 Cookie：访客的匿名身份与正式站登录 token 不会互相污染。
- 不需要把前端所有 `fetch("/api/...")` 改前缀。
- nginx 只需新增一个 server 块，仍走 80 端口，不新开端口（新端口只增加暴露面，不带来隔离）。

### D3 demo 身份由网关 Header（Host）判定，落在认证中间件

在 `auth_middleware` 入口判定 `DEMO_MODE && Host 命中 demo 域名`，随后：

- 忽略 `token` Cookie（防止正式账号在 demo 域提权）；
- 复用现有 `user_id` Cookie 机制，但值统一为 `demo_<uuid>`；
- `request.state.is_demo = True`，供后续守卫与日志读取。

**为什么不用独立的登录接口**：demo 不需要账号体系，任何"注册/登录"都会把演示变成可被滥用的账号池。

### D4 知识库强制覆盖放在 `turn` 入口的唯一一处

覆盖点选在 `src/api/chat.py` 取出 `kb_id` 之后、`start_turn` 之前：`if demo: kb_id = settings.DEMO_KB_ID`。

multi-query 重写、检索工具、会话回放都从同一条链路拿 kb，因此只需一处覆盖；若 `DEMO_KB_ID` 为空则直接拒绝请求（fail-closed），不允许退化成"任意库"。

**Alternatives**：在每个检索工具里判断（分散、易漏）；改前端（可被绕过，不算边界）。

### D5 配额三层，计数全部落 Redis

| 层 | 键 | 阈值（默认） | 超限行为 |
|---|---|---|---|
| 会话轮数 | `demo:turn:<session_id>` | 20 | 409 + 可读提示 |
| 访客日轮数 | `demo:user:<uid>:<yyyymmdd>` | 50 | 同上 |
| 全站日 token | `demo:tokens:<yyyymmdd>` | 300000 | 统一"今日额度已用完" |
| IP 频率 | nginx `limit_req` | 60 次/10 分钟 | 429/503（网关层） |

**令牌日界按 `Asia/Shanghai` 计算**；计数使用 `INCRBY` + 当日过期，重启后自然保持。

token 统计落在 provider 层（`src/infra/llm/`）统一累加，避免"流式结束后才算账"导致漏计。

### D6 限流在网关、配额在应用，两层职责不重叠

nginx 负责"挡住明显的脚本/爬虫"（按 IP，成本低）；应用层负责"业务语义的量"（轮数、token）。两者都做，但阈值独立配置，避免一处失效即全失效。

### D7 紧急关停走 nginx，不走配置开关

`DEMO_MODE` 在启动时读取（settings 是模块级常量），改它需要重启容器；紧急场景要秒级。方案：runbook 里给一段"把 demo server 的 `location /` 改为 `return 503` 后 `nginx -s reload`"的操作，恢复时反向执行。

### D8 演示语料：独立 KB + 单文档（腾讯 2024 年报）

- 新建独立 KB（UUID 写进 `DEMO_KB_ID`），从 `data/test_docs/tencent_2024_annual.pdf` 灌入；
- 演示库不参与任何业务统计；重置脚本只按该 KB 清理文档、chunks 与 MinIO 对象；
- 6 个示例问题必须逐条跑通后才允许上线（门禁，不做"看起来能答"）。

**Alternatives**：复用现有 `test` 库（混了 4 家公司，检索会串）；自编语料（成本高，当前无必要）。

### D9 观测：`channel=demo` + `demo_miss`

`Channel` 增加 `demo` 取值，在 chat 入口按 `is_demo` 设置，日志第 5 段与 Langfuse metadata 自动带上；检索零命中时额外打一条 `demo_miss`（脱敏 query + trace_id），供后续按周聚合补语料。

### D10 人机验证推迟到 A2

Turnstile 的挑战脚本来自 Cloudflare 域名，国内可达性未验证；先用 IP 限流 + 蜜罐字段兜底，验证可达后再接。设计上预留一个"首条消息前校验"的钩子位置，不预先引入依赖。

## Risks / Trade-offs

- **demo 流量挤占正式站与企微 bot** → 限制 demo 并发流数（建议 3），并在 runbook 写明"demo 异常时先关停 demo"。
- **kb 覆盖只做在 chat 链路，其他入口遗漏** → 会话回放、任务状态等接口不触发检索，风险可控；验收用例覆盖"传其他 kb_id"场景。
- **访客换无痕浏览器绕过轮数** → IP 限流兜底；接受"多开会被限流"这一体验损失。
- **重置脚本误删业务数据** → 只接受 `DEMO_KB_ID`，脚本内二次校验该 KB 名称以 `demo-` 开头。
- **演示问题答不上导致首印象差** → 示例问题作为上线门禁；`demo_miss` 提供持续改进闭环。
- **微信内置浏览器 SSE 兼容性未知** → 上线前在微信内实测一次，失败则给"在浏览器中打开"的提示。

## Migration Plan

1. 建演示 KB 并灌入腾讯年报（种子脚本），拿到 UUID；
2. 配置 `DEMO_MODE=false` 与 `DEMO_KB_ID`，先不改 nginx（无入口，零影响）；
3. nginx 增加 demo server（先返回 503），验证证书与 DNS；
4. 切到正常转发，`DEMO_MODE=true` 并重启 app；
5. 按验收清单（范围内正确率、范围外拒答、乱问稳定性）跑一遍；
6. 关停演练：按 runbook 执行一次 503 再恢复。

**回滚**：`DEMO_MODE=false` + 重启（约 10–30 秒），或直接执行关停步骤。

## Open Questions

- 配额阈值是否采用默认值（20 轮 / 50 轮日 / 30 万 token 日 / 60 次 10 分钟）——需按实际模型单价核算一次成本。
- `demo_miss` 是否随 A1 一起上（工作量小，建议上）。
- 演示页是否需要「留邮箱换更多额度」的转化入口（涉及表单与隐私声明）。
- 微信内置浏览器的 SSE 表现需实测后再定是否给降级提示。
