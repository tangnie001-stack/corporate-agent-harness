## Context

当前自托管 Langfuse **v2.95.11**：单容器 + 复用应用 PostgreSQL（独立 database `langfuse`），无 ClickHouse / worker / 对象存储。应用侧用 Python SDK **2.60.10**，通过 `langfuse.decorators.langfuse_context` 与低层 `client.span()` / `client.generation()` 上报；trace_id 为 `trace_<uuid4>`（`CLAUDE.md:87`、`glossary.md:13`、`api_contract.md:858` 三处为契约）。

实施前实测基线（2026-11-09）：

| 事实 | 数值 / 结论 |
|---|---|
| Langfuse 库大小 | 28 MB |
| traces / observations / scores / datasets | 336 / 1151 / 668 / 0 |
| 668 条 score 的归属 | **全部挂在 `eval_question` trace 上**（5 个指标），线上 `chat_turn` 一条都没有 |
| `_prisma_migrations` | 271 行（v2 迁移历史）→ **不能复用该库给 v4**，否则迁移只跑增量导致 schema 错位 |
| 本机资源 | 7.8 GiB / 8 核 / 4 GiB swap；现有 6 个容器合计占 3.6 GiB |
| SDK 硬约束 | v3+ 对 trace_id 校验 **32 位小写 hex**，非法即忽略并自行生成（`langfuse/_client/client.py:1793`） |
| v4 读路径 | `GET /api/public/traces/:id` 返回 404，改 Observations API v2 |
| v4 写入 | `POST /api/public/otel/v1/traces`（OTLP）；legacy ingestion 仅保留 `score-create` |
| v4 授权 | OSS 计划下 `annotation-queue-count` / `model-based-evaluations-count-evaluators` 均为 `false`（不限量） |

关联进行中变更：`trace-diagnosis`（35/47），其 `TraceSource` 复用 `src/cli/eval_trace_io.py::fetch_trace`；本变更会改写该函数，故两者存在硬依赖。

## Goals / Non-Goals

**Goals:**

- 用全新部署把 Langfuse 升到 v4，能力上支持 LLM-as-a-Judge、人工标注队列、Dataset 版本化
- 应用侧在不改变**观测语义**（trace 树、session/user/tag/metadata、token 与成本）的前提下完成 SDK 5.x 迁移
- 保证「日志里的 trace_id = Langfuse 的 trace id」这条性质继续成立
- 保证升级当天 `diagnose_trace` 与评测 CLI 可用

**Non-Goals:**

- 不迁移历史数据、不保留 v2 库、不做 v2→v3→v4 版本跳转
- 不配置任何 evaluator / 标注队列业务规则
- 不改题库来源、不引入 `dataset_run_id` 回填、不做 CI 门禁
- 不接 purge 定时任务（只保证手动 CLI）

## Decisions

### D1 全新部署 + 新 database，不做任何迁移

旧库含 271 条 v2 迁移记录，v4 会在其上只跑增量，schema 必错；且历史仅 336 条 trace、668 条 score（score 真源在 `eval_report`）。

- **选择**：新建 `langfuse_v4` 空库，v4 迁移从零执行；旧库与旧容器在验收后删除
- **备选**：三跳迁移（v2.95 → v3.29 并行 → 最新 v3 → v4 + backfill）。被否：三跳 + 并行期 + 4 个后台迁移 + 3 倍磁盘，换 336 条 trace 的浏览能力，不成立

### D2 SDK 取 5.x，不取 3.15

两者都兼容 v4（v4 只拒 Python SDK ≤2）。实测差异只有 `update_current_trace`：3.15 保留、5.x 改为 `propagate_attributes` + 根 span `update()`；`get_current_trace_id` / `get_current_observation_id` / `score_current_trace` / `flush` 在 5.x 均保留。

- **选择**：5.x（当前 5.2.0，要求服务端 ≥4.59）
- **理由**：多改 4 处换一次迁完；官方文档与技能均按 v5 书写
- **备选**：3.15（改动最小，但三年内还要再迁同一批 18 个文件）

### D3 trace_id 改 32hex，三级生成链

约束来自 OTel/W3C（trace-id 16 字节 = 32 小写 hex），由 SDK 客户端先拦、协议层再拦。保留前缀的方案（对外 `trace_<uuid>`、内部 sha256 派生）会让「日志里的 id 不是 trace id」，长期有害。

- **选择**：三层统一产 32hex——前端 `crypto.randomUUID().replace(/-/g,'')`；nginx `map` 判空用内建 `$request_id`（本身就是 32hex）；后端 `uuid4().hex`
- **校验**：入站 `^[0-9a-f]{32}$` 才沿用，否则丢弃重生成并 warning；`eval_run_<ts>_<8hex>` 同步改 32hex
- **备选**：保留前缀 + 映射（契约不变但多一层心智负担）；只在 SDK 侧转换（日志与 trace 分叉）

### D4 「关闭观测」拆成两层

`langfuse_context.configure(enabled=)` 在 5.x 不存在；现有测试依赖运行时切换，且 1184 条用例不能承担每次约 2s 的 SDK reset 开销。

约束：根 observation 由 `@observe` 装饰的 `_run_generation` 产生（`agent_service.py:770`），**装饰器先于函数体执行**，因此"最外层短路"不可达——守卫只能放在函数体首行与所有子 span 创建点。

- **选择**：应用层守卫为主（`settings.LANGFUSE_ENABLE=False` 时，根函数体首行直接返回、所有子 observation 创建点前置判断，不触碰 SDK 客户端），SDK env `LANGFUSE_TRACING_ENABLED=false` 为兜底（须早于首次 `get_client()`）。关闭时的可测判据是"零子 observation + 零上报请求"，而非"零 `@observe` 调用"
- **备选**：纯 env（启动期定死，测试 monkeypatch 失效）；纯运行时（无 API 可用）

### D5 读路径只实现 v4 一套

- **依据**：官方 v3→v4 迁移文档的读接口表明确列出 `GET /api/public/traces` 与 `/traces/:id` 返回 404、由 Observations API v2 取代；该结论仍须在 spike ③ 用实际 v4 实例复验（若端点可用，本决策重新评估，可省掉整块改造）
- **选择**：`eval_trace_io` 改用 Observations API v2，向上层返回**属性式对象**（沿用 `getattr` 访问习惯），字段清单写进 spec；`metadata` SHALL 合并根 observation 的 metadata（迟到字段的唯一承载）；不写版本分支、不做 fallback
- **理由**：数据不迁、实例不并存，双兼容是纯负担
- **风险**：若返回 dict，`getattr(trace, "metadata", None)` 不报错但得到空值，诊断会静默退化为 `no_retrieval_snapshot`；故必须以"真实 trace 的 retrieval 调用数 > 0"作为验收
- **备选**：先降级、另立 change（会让升级当天失去按 trace 排查能力，并卡住 `trace-diagnosis`）

### D6 清理改用官方删除 API

- **选择**：`DELETE /api/public/traces/{id}`（v4 OSS 自带 `trace-deletion`）；保留 CLI 的 dry-run、保留期下界、单次上限与双重确认
- **备选**：直连 ClickHouse 写 SQL（自建级联、与官方 schema 耦合，且删除语义受 mutation 影响）

### D7 部署形态细节

- web 与 worker **必须同一 tag**（同一次构建，schema 版本一致）
- ClickHouse 取最新 LTS 且 ≥25.12；`CLUSTER_ENABLED=false`；**时区 UTC**；**命名卷**；healthcheck；内存上限 1.5g
- MinIO 新建事件桶，S3 配置开启 `FORCE_PATH_STYLE`
- `langfuse-web` 的 `mem_limit` 由 256m 提到 ≥1g，两个 Node 容器设 `NODE_OPTIONS`
- 沿用现有 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY/SECRET`，app 配置零改动

### D8 测试策略

- 三层：应用层守卫（主）+ `LANGFUSE_TRACING_ENABLED=false`（兜底）+ 单测断言「关闭时零 span」
- 真 Langfuse 契约测试标 `@pytest.mark.integration`，默认跳过；单测禁止联网
- 新增 `test_trace_context_otel.py` 固化 D9 的线程池结论

### D9 验证顺序：trace-diagnosis 全部放到 v4

`trace-diagnosis` 的 8.5（15~20 条真实 trace + 人工根因）与 8.6（活体端到端）均在 v4 执行，不在 v2 预跑；其归档推迟到本变更验收之后。

- **理由**：v2 数据即将清空，在 v2 上验等于给一个即将消失的环境做一次性证明
- **代价**：`trace-diagnosis` 长期挂在 in-progress；两个 change 并行期需要文件边界约定（本变更只改 `eval_trace_io` 实现，不动 `TraceSource` 接口；`src/rca/` 下由 trace-diagnosis 主导）
- **变量隔离**：先用 spike ③ 的字段对照证明读路径正确，再跑 8.5；否则失败无法区分是诊断逻辑还是字段映射

### D10 切换方式：dev 测试床 + 生产停机窗口

- dev：先用 3001 端口 + 独立库起一套 v4 当测试床，SDK 与读路径在那里验完
- 生产：单独停机窗口；顺序为「关观测 → 停 app → 换栈 → 起 → 验证 → 开观测」
- **不保留** v2 容器与库；不设回滚路径（已确认可停机、可清数据）

### D11 后台任务的根 span 显式绑定 trace id

`src/services/turn_runner.py:373` 与 `src/cli/eval_deepeval.py:329` 现有 `langfuse_observation_id=` 是 v2 装饰器专用 kwarg（由 `@observe` 从 kwargs 摘走）。5.x 没有该机制：参数会落进业务函数（TypeError）或被忽略（根 span 由服务端生成 id）。

- **选择**：删除该 kwarg；在后台任务入口用 `start_as_current_observation(trace_context=TraceContext(trace_id=...))` 显式建立根 observation，其余 span 挂其下
- **验收**：一条回归测试断言「根 observation 的 trace_id == 传入 id，且按该 id 能查到 trace」（observation 自身的 id 是 16hex span id，不是 trace id）

### D12 迟到字段改由根 observation 承载

v5 的 span.update(metadata=...) 写入的是 **observation** 级元数据（langfuse.observation.metadata.*），不是 trace 级；propagate_attributes 又只在上下文建立时生效。因此轮末才产生的 retrieval_contexts / tools_used 无法像 v2 的 update_current_trace 那样补写到 trace 上。

- **选择**：迟到字段写根 observation 的 metadata；读取层（eval_trace_io）把「trace metadata + 根 observation metadata」合并成对外的 .metadata，消费方（诊断/评测）无需改动
- **验收**：判据与 tasks 1.3（spike ③）一致——用 5.1 的读接口回读轮末写入的 retrieval_contexts / tools_used；7.10 断言二者可读
- **备选**：只写 trace 级并接受丢失（会让诊断恒为 no_retrieval_snapshot，不可接受）

## Spike 结论（2026-11-09，SDK 5.2.0 实测）

- **spike① 关闭观测**：`LANGFUSE_TRACING_ENABLED=false` 时 `get_client()._tracing_enabled=False`；`start_as_current_observation` 返回的 span 全零（`trace_id` 32 个 0、`id` 16 个 0）；`get_current_trace_id()` 为 None；指向本地监听端口时**零连接**。结论：SDK 侧兜底可靠；应用层守卫仍保留（省对象开销 + 支持运行时 `settings` 切换）。
- **spike② 线程池上下文**：`asyncio.to_thread` 内 `get_current_trace_id()` 与新建 span 的 `trace_id` 均等于外层 trace；`span.id` 为 16hex、`span.trace_id` 为 32hex。结论：OTel 上下文可穿线程池，后台任务根绑定按 `trace_id` 断言。
- **版本**：Python SDK **5.2.0**；Langfuse 服务端 **4.76.0**；ClickHouse **26.3.62.3**（官方 v4.76 compose 钉的版本）。
- **顺带观察**：后端不可达时 SDK 在后台批量导出器里重试（日志 `Transient error ... retrying`），不阻塞调用方——与「后端不可达不影响对话」的既有要求一致。
- **未完成**：spike③（读 API 字段对照）依赖 v4 实例，安排在 2.9 测试床起来后执行。

## Risks / Trade-offs

- **[trace_id 契约破坏]** 外部系统传自定义 id（如 `order-123`）将被丢弃 → 业务单号改放 `metadata.biz_id`；前端以 SSE `done` 事件的 trace_id 为准，老页面会自愈
- **[OTel 上下文穿不过线程池]** `asyncio.to_thread` 里创建的 span 可能挂到新 trace，比不记录更难发现 → spike ② 实测并固化为回归测试
- **[读 API 字段缺口]** Observations API v2 可能缺 latency/usage/children 之一 → spike ③ 先做字段对照；缺字段则调整 `eval_trace_io` 的返回映射，不改上层
- **[成本静默 NULL]** 模型定价 seed 若在 v4 失效，成本字段为空而门禁仍通过 → 验收项强制断言「一次对话的 calculated cost > 0」
- **[容器重启丢 trace]** CH/MinIO 使用匿名卷 → 必须命名卷；验收里加一次 `docker compose restart` 后数据仍在
- **[v4 登录失败]** 认证栈与反代配置变化 → dev 先验，验收含「控制台登录成功」
- **[8G 内存]** 峰值约 5.5 GiB（现 3.6 + CH 1.5 + worker 0.5）→ 设 `mem_limit` 与 Node heap 上限；保留 4 GiB swap
- **[trace-diagnosis 被卡]** v4 若延期，其归档一直挂起 → 设定最迟点，超期则以 v2 跑一次 8.6 先行收口（成本为一条 trace）
- **[两 change 并行冲突]** 同改 `src/rca/` → 约定合并顺序：trace-diagnosis 的 `src/rca/` 实现先落地，本变更的读路径改造排在其后
- **[诊断入口正则过时]** `diagnose_tools.py:35` 的 `^trace_[0-9a-fA-F-]{36}$` 会让所有 32hex id 被判非法 → 删除本地正则，复用 `tracing.TRACE_ID_PATTERN`，并补正例测试
- **[purge 列举失效]** 保留期清理的"列超期"仍读 PG `traces` 表，v4 下恒为 0 条，dry-run 会被误读成"没有超期数据" → 列举改 `GET /api/public/traces` 分页 + 时间过滤；验收要求"命中 > 0 且删除后 UI 查不到"
- **[trace 级字段时序]** `propagate_attributes` 在上下文创建时生效不能回填；`span.update(metadata=)` 只写 observation 级 → 迟到字段落根 observation、读取层合并（见 D12）；验收断言「经读接口能读到」而非「Langfuse 原始 trace 上有」
- **[新库建不出来]** `POSTGRES_DB` 与 init 脚本只在空卷首启生效，已有卷上不会创建 `langfuse_v4` → 必须显式 `CREATE DATABASE` 并做存在性检查

## Migration Plan

1. **准备**：三份 compose、两个 env 模板、新库与新桶、镜像入 ACR
2. **dev 测试床**：3001 端口 + `langfuse_v4` 起 v4；完成 spike ①②③
3. **代码改造**：trace_id 三层 → SDK 5.x → 读路径 → purge
4. **dev 验收**：8 条清单 + 一次 `restart` 数据不丢 + 控制台登录 + annotate 冒烟
5. **生产停机窗口**：关观测 → 停 app → 换栈（新库、新镜像）→ 起 → 冒烟 → 开观测
6. **收尾**：删除 v2 容器与旧库；`trace-diagnosis` 在 v4 上完成 8.5/8.6 并归档

**回滚**：无。失败即在窗口内修复或退回「无观测运行」，不恢复 v2 数据。

## Open Questions

- v4 具体镜像 tag、SDK 5.x 具体版本、ClickHouse LTS 版本（按实施当日 latest 记录）
- `LANGFUSE_INIT_*` 在 v4 的播种行为与登录（`NEXTAUTH_URL` / trustHost）——dev 实测后回填
- Observations API v2 的字段清单，以及 `GET /api/public/traces/{id}` 在 v4 是否真为 404（spike ③ 同时验这两点）
- `LANGFUSE_TRACING_ENABLED=false` 时 SDK 是否完全 no-op（spike ①）
- `propagate_attributes` 与 `update_current_trace` 的等价边界：哪些字段可在轮末补写（spike ③ 一并验，判据是「用读接口回读」）
