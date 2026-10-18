# 代码地图（前后端结构）

代码结构唯一归属文档：仓库顶层目录、后端 `src/` 分层、前端 `deploy/nginx/html`、
运行时内容库与测试的组织方式。**改动代码前先读这里定位文件**；层间调用规则与文件大小
红线是规约，见 `CLAUDE.md`「代码目录结构」与 `docs/agents/rules.md`。

> 本仓库**前后端同仓**：后端 Python（`src/`），前端静态 HTML/CSS/JS（`deploy/nginx/html/`，
> 由 Nginx 容器直接托管，无构建步骤）。

## 一、顶层目录

| 目录 / 文件 | 职责 |
|------|------|
| `src/` | 后端 Python 源码（分层见下） |
| `tests/` | 单元测试，与 `src/` 模块一一对应 |
| `deploy/` | 部署件：`nginx/`（反向代理 + 前端静态文件）、`postgres/init/` 建库脚本、`wait-for-it.sh` |
| `deploy/nginx/html/` | **前端页面静态文件**（chat.html / index.html / login.html 等） |
| `skills/` | 运行时 skill 内容库（`<name>/SKILL.md`，业务侧管理，**随 app 镜像发布** —— `Dockerfile` COPY 进 `/app/skills`） |
| `agents/` | **智能体预设内容库**（`<name>.md` 平坦文件，业务侧管理；见下方「三个 `agents` 的区别」） |
| `docs/` | 文档：`agents/`（本目录，规则/契约/排查）、`design/`（UI 设计规格与 HTML 预览）、`openspec/`（OpenSpec 主目录）、`superpowers/` |
| `openspec/` | **符号链接 → `docs/openspec`**；OpenSpec changes / specs |
| `alembic/` + `alembic.ini` | 数据库迁移（唯一链；`0001_pg_baseline` 从零建 8 张表，`0002_kb_domain` 加列） |
| `scripts/` | 运维脚本（清库、重建 KB 数据、重写 `content_seg`）。两个发布期子目录按**执行机器**分工：`scripts/ci/pack-deploy-artifact.sh` = **构建机**（云效流水线构建阶段的执行命令；白名单打包出部署制品，并把本次 `BUILD_NUMBER` 注入 compose 的 app 镜像 tag，暂存目录 `pack-staging/`）；`scripts/deploy/deploy.sh` = **目标机**（环境判定 → 依赖安装 → 前置检查 → ACR 登录校验 → 拉镜像 → 起栈 → 迁移 → 健康检查；**不走 git**，假定当前目录已是制品解压后的发布目录）。标准发布路径 `/opt/www/corporate_rag`（脚本内置 `EXPECTED_ROOT`，跑在别处会提醒） |
| `litellm/` | LiteLLM 代理配置（模型网关） |
| `data/`、`logs/` | 运行期数据与日志挂载点 |
| `docker-compose.yml` / `.override.yml` / `.prod.yml` | 编排（redis / postgres / minio / langfuse-web / nginx / litellm-proxy / app 共 7 个服务；prod 无 litellm-proxy）。`langfuse-web` 挂 `profiles: ["langfuse"]`，dev 经 `.env` 的 `COMPOSE_PROFILES=langfuse` 默认启用 |
| `docker-compose.image.yml` | **目标机部署档**：镜像全取阿里云 ACR、无 `build:`、对外只暴露 nginx:80（其余端口绑回环）；技能/预设/迁移/nginx 配置靠挂载。配套 `scripts/deploy/deploy.sh` |
| `Dockerfile`、`pyproject.toml` | 应用镜像与依赖 |

> **三个 `agents` 的区别（勿混淆）**：根 `agents/` = 智能体预设**内容**（`<name>.md` 平坦文件）；
> `src/agents/` = agent 运行时**代码**（graph / tools / skills / presets）；`docs/agents/` = **文档**（规则 / 契约 / 排查）。

## 二、后端 `src/` 分层

```
main.py            FastAPI 入口：app 工厂 + 异常处理器 + 中间件挂载 + 路由注册
api/               纯路由层：请求校验 → 调 service → 返回（不写业务逻辑）
  ├─ model/        Pydantic 请求体(request.py) / 响应体(response.py)
  ├─ schema.py     路由共用 schema；dependencies.py 依赖注入（服务单例 / 当前用户）
  ├─ auth.py       `api/auth/login`、`api/auth/verify`、`api/auth/logout`、`api/auth/anonymous`
  ├─ health.py     `api/health`、`api/config`
  ├─ knowledge_base.py  KB CRUD：`api/kbs/list`、`api/kbs`、`api/kbs/delete`、`api/kbs/domain`
  ├─ documents.py  文档上传 / 列表 / 状态 / 分块 / 删除（`api/kbs/documents/*`）
  ├─ kb_eval.py    最近一次评估报告 `api/kbs/eval/latest`
  ├─ chat.py       SSE 主入口 `api/chat/stream`
  ├─ clarify.py    澄清应答 `api/chat/clarify-answer`
  ├─ sessions.py   会话 list / messages / delete / cancel + 过程事件与任务（`api/sessions/*`）
  ├─ feedback.py   回答反馈 `api/feedback`
  ├─ llm_test.py   连通性自检 `api/llm/test`
  ├─ ragas_generate.py  已退役的出题端点 `api/ragas/generate`（保留路由，恒 410）
  ├─ alerts.py     阿里云告警接入 `api/alerts/webhook`（读请求头 token 后交 alert_service）
  └─ capabilities.py    能力清单 `api/skills`、`api/agents`（经 service 派生）
  ⚠ SSE 序列化不在本层：`src/utils/sse.py`（本层只调 `to_sse`，不得内联格式化）
  ⚠ 路由的权威清单（含方法与请求体）见 `src/api/README.md` 与 `api_contract.md`
services/          业务编排：app_service → kb / document / chat(agent)
  agent_service.py 图生命周期 + 一次生成的主循环（_run_generation）
  capability_service.py  能力清单：/api/skills、/api/agents 由 registry 派生（fail-open）
  summary_scheduler.py  跨轮历史摘要调度：触发判据 / best-effort 抢锁 / 后台生成（回合正常完成分支发起）
  summary_lock.py  跨轮摘要并发守卫（Redis SETNX，前缀 chat_summary_lock:，独立于轮次锁 chat_lock:）
  alert_service.py  阿里云告警接入：载荷解析 / 逐条指纹去重 / 编排（占位 → 跑 Agent（禁澄清）→ 结论或兜底转发）
agents/            LangGraph agent 循环
  ├─ graph/        workflow(建图) / state / agent_factory(唯一装配入口 build_agent) / middleware(循环四件套) / agent_node(首轮组装 + 循环包装节点) / message_payload(消息→trace 载荷纯函数) / history_window(历史窗口切分纯函数，裁剪与摘要触发判据同口径) / nodes / verify / skill_direct(命令行直出节点)
  ├─ tools/        retrieve_kb、ask_user、search_web、task、registry( + readonly 声明表)
  ├─ skills/       主从委派运行时：loader/registry/executor/delegate_task/models/invocation/prefix(/xxx 解析与清洗纯函数) + fork 执行层 fork_stream/fork_tools/delegate_run
  └─ presets/      智能体预设：models / loader / registry
rag/               检索与知识库路由：retrieval / fusion(RRF 纯函数) / context / prompt / temporal
chat/              对话管理：manager(Redis) / persistence(PostgreSQL) / streaming / task_registry / process_log / summary_store(摘要读写 mixin，Redis 优先内存降级，宿主 ChatManager) / history_summary(摘要核心：提示词组装 + 调用 + 四条不变量校验)
chunking/          分块：router(策略路由) / strategies(4 种) / validator / scorer
parsers/           文档解析：pdf / docx / txt + base / router
core/              日志：logging / log_events / log_event_specs
config/            settings(环境变量) / const(常量/文案/枚举) / response_codes / wecom_bots(多机器人配置解析/校验) / alert(告警配置解析/校验)
  └─ prompts/      prompt 模板包：__init__.py(7 条行为键常量 VERIFY_* / FORK_*) / loader.py(唯一读取点) / validation.py(启动期校验) / templates/*.yaml(22 条模板：段模板 `kind: section` 14 条 + 独立任务模板 `kind: task` 8 条)
                   —— 段模板(`kind: section`)与独立任务模板(`kind: task`)同处一包；改 prompt 文案改 YAML，改规则的挂载点改代码
infra/             基础设施：db(engine/DSN + transaction 事务边界 + models + repos + vector_store + lexical_query) / llm(tracing 为 Langfuse 开关/flush/trace id 校验唯一入口；langfuse_purge 为保留期删除的 SQL 后端；token_count 为统一 token 计数入口——tiktoken 近似，encoder 缺失时降级 `len(text)//2` 并记一次 warning；**provider/ 为 provider 兼容层，见下**) / search(tokenizer 为唯一 jieba 分词入口) / auth / redis_client
middleware/        auth / trace_id / response_processor（统一响应包装）
cli/               DeepEval 评测、v6 出题、检索对比、trace 回放/清理、症状指标、种子与防腐闸门（check_docs / check_adr）等命令行工具
models.py          模型门面（**薄委托** `src/infra/llm/provider/`）：get_llm / get_classify_llm / get_summary_llm / get_judge_llm / get_embeddings / get_rerank
utils/             sse 事件类型 / errors / desensitize / auth_crypto
tools/             工具基类（base.py）
channels/          接入通道：base(通用抽象) / wecom(parse 入站解析 / crypto 加解密 / callback 回调驱动 / long_connection(长连接驱动，封装官方 aibot SDK))
```

### CLI 出题模块（v6）

| 模块 | 职责 |
|------|------|
| `synth_contexts.py` | v6 出题上下文：分块读取、分组、公司/报告期锚点 |
| `synth_gates.py` | v6 出题质量门：语言/完整句/自足/去重/多跳 |
| `synth_writer.py` | v6 样本转换、元数据、原子写 |
| `testset_synth.py` | v6 出题编排与 CLI |

> **provider 兼容层** —— `src/infra/llm/provider/`：把各家 LLM provider 的差异（思考参数形状 / reasoning 字段名 / 端点 / 密钥 / 定价 / 窗口）**收敛到一层**，调用侧只认三个门面。**不是网关**（不做路由 / fallback / 限流，见 ADR-0020 / ADR-0021）。
> - `registry.yaml` —— **数据面**（随镜像发布）：每个 provider 的 `base_url` / `api_key_env` / `thinking_style` / `reasoning_field` / `capabilities` / `models`(角色→默认模型名) / `catalog`(模型→`context_window` / `pricing` / `tokens_per_cjk_char` / embedding 批大小与维度)。密钥不入库，只写 env 名。
> - `spec.py` —— `ProviderSpec` / `ThinkingStyle` / `load_registry()` / `resolve_role(role, registry, env)`（角色→provider+模型+端点+密钥，含 `.env` 覆盖与旧键软迁移）/ `validate_config()`（启动期 fail-fast）。
> - `thinking.py` —— `thinking_extra_body(spec, enabled)`：**唯一**产生思考参数处（`dashscope` 用 `enable_thinking`+`preserve_thinking`；`deepseek` 用 `thinking.type`）。构造期与请求期**同源**，避免互相覆盖丢字段。
> - `chat.py` —— `ReasoningPreservingChat`（流式提取思考文本 + 请求侧按 `reasoning_field` 回传）。
> - `factory.py` —— `build_chat` / `build_embeddings` / `build_rerank`（`src/models.py` 门面即薄委托到此）。
> - `pricing_seed.py` —— 启动期按 `catalog.pricing` **幂等 ensure** Langfuse 模型定义（无价记 WARNING，不阻断启动）。
> - 角色缺省分两族：`main`/`classify`/`summary` 跟随 `LLM_PROVIDER`；`embedding`/`rerank`/`judge` **独立缺省 `dashscope`**。**切换 provider 只改 `.env`**（操作见 `deploy-runbook.md` §2.7）。

> **官方 `aibot` SDK（长连接）用法与约束** —— 依赖 `wecom-aibot-python-sdk`（导入名 `aibot`）。
> SDK 承担：连接认证（`aibot_subscribe`）、心跳保活、断线重连、事件分发、流式回复、模板卡片、文件解密。
> 本仓库**只**使用其公开接口：`connect()` / `disconnect()` / `on()` / `reply_stream()`。
> **禁止调用 `run()`** —— 它自建事件循环，与 FastAPI 冲突（有守卫测试 `tests/channels/test_long_connection_no_run.py`）。
> 契约：每机器人同时只能一条连接（新连接踢旧连接）。依赖与部署影响见 `deploy-runbook.md`。
>
> 企微流式投影（接入站点同款 Agent 管线投影层）落在两处：
> - `src/channels/wecom/presenter.py`：企微流式投影层（事件流 → 企微帧；快照累积 / 节流 / 上限 / 保活）
> - `src/config/wecom_presenter.py`：企微投影层可调参数与文案
>
> 企微桥接通道层落点：
> - `src/channels/wecom/handler.py`：入站 → 站点同款 Agent 管线的桥接 handler（标识/去重/trace_id/事件分流）
> - `src/channels/wecom/session.py`：企微会话与用户标识派生（UUIDv5）
> - `src/channels/wecom/bounded_map.py`：有界 TTL 映射（msgid 去重与澄清触发者登记共用）
> - `src/config/wecom_channel.py`：企微通道层参数（去重/触发者登记的 TTL 与容量）与通道文案
> - `src/channels/wecom/long_connection.py`：长连接驱动；**就绪 = 收到 SDK `authenticated` 事件**（等待上界 5s，超时只降级不断开）；凭证类失败与"被顶"均置非就绪
> - `src/services/wecom_service.py`：驱动注册表（收全部已启动者，供 stop 关闭）+ `is_ready(bot_key)` 查询；锚点 `n=` 只计认证就绪台数
> - `src/services/clarify_service.py`：澄清答案**编排**（下沉目标）：定位并 resolve 挂起 Future + 落库用户答案 + 文本格式化
> - `src/channels/wecom/clarify_parse.py`：把企微**文本回复**解析成站点同构 `answers=[{id,selected,custom}]`（单问/编号多问/multi_select/无效判定）
> - 澄清在企微的链路：投影层**渲染问题**（不 finalize）→ 用户回复 → handler 校验**仅触发者** → 解析 → `resolve_clarify_answer` → **原回合续跑、同一气泡继续累积**

### prompt 组装与段模板归属

- `src/rag/prompt.py` —— **段组装器**：五段固定顺序（`SECTION_ORDER`）、逐条条件注入的
  **判据表**（`_SECTION_RULES`，判据住代码、YAML 只装正文）、`base` 三选一解析。
  逐条判据与文案的对照表在 `docs/agents/prompt-ownership.md` §3，两处必须同步增删。
- `src/config/prompts/templates/` —— 段模板（`kind: section`，参与 system 组装）与独立
  任务模板（`kind: task`，各自单独调用）；归属由 `kind` / `section` / `domain` 字段声明，
  不从文件名或 id 推断。

### 关系型存储（PostgreSQL）

- **引擎与 DSN 归属**：`src/infra/db/engine.py` 在模块级构造异步引擎与 `session_factory`；
  DSN 由 `src/config/settings.py:build_postgres_dsn()` 提供（`postgresql+asyncpg://` 形式，
  `POSTGRES_PASSWORD` 缺失时抛 `RuntimeError`）。连接池参数（`pool_size` / `max_overflow`）
  也在 `engine.py`，与 Langfuse 共享同一实例，见 `docs/agents/defensive-patterns.md`。
- **ORM 模型唯一来源**：`src/infra/db/models/`（`chat` / `chunk` / `document` / `eval_report` /
  `feedback` / `kb` / `user`），声明式基类与通用 Mixin 在 `src/infra/db/base.py`。
- **Repo 层**：`src/infra/db/repos/`（`chat_repo` / `chunk_repo` / `document_repo` /
  `eval_repo` / `kb_repo` / `user_repo`）。包名 `repos` 与内容一致：均为 PostgreSQL 各表的 SQL 访问层；
  原历史包名 mysql_db 的改名已完成（P4 收尾），见需求池 F-18（P1 遗留项 L4，已修）。<!-- doc-anchors-allow -->
- **事务边界原语**：`src/infra/db/transaction.py` 的 `session_scope` 是跨表原子提交的唯一入口，
  每个 Repo 以其为基础暴露 `transaction()`。**写路径的事务边界**：跨表原子操作须用
  `session_scope(...)` / `Repo.transaction()` 打开唯一事务，并把 `session=` 传给参与方法
  —— 参与者只执行语句、不提交，提交与回滚由边界那一层决定；不传 `session` 的老调用点行为不变
  （自开会话、出块提交）。规则与历史缺陷见 `docs/agents/defensive-patterns.md`「派生写操作跨事务」。
- **`chunks` 表由 `ChunkModel` 映射**（`src/infra/db/models/chunk.py`）：baseline 手写建表
  （`content_seg` 文本列 + `tsv` 生成列 + `embedding vector(1024)` + 3 个索引），SQL 访问层是
  `src/infra/db/repos/chunk_repo.py` 的 `ChunkRepo`（含 `Vector.cosine_distance` dense 检索与
  `search_lexical` 词法检索：`tsv @@ to_tsquery` + `ts_rank` 降序，词元全被滤掉时降级为 `content LIKE`）。
  `content_seg` 存 jieba 词项（空格连接，见 `src/infra/search/tokenizer.py`），`tsv = to_tsvector('simple', content_seg)`
  是它的持久化生成列；分词结果随之固化，**分词器变更必须跑 `scripts/rewrite_content_seg.py --apply`**
  （`--check` 退出码 1 = 存量过期）。ORM 属性名 `extra` 映射列名 `metadata`（避开 `Base.metadata` 命名冲突），列名不变。
- **向量存储**：`src/infra/db/vector_store/` —— `__init__.py`（公开入口 `VectorStore`，别名导出 PG 实现）、
  `pg_store.py`（`PgVectorStore` + `QueryEmbedder`；含 `dense_search` 与 `lexical_search` 两路取数，
  后者按 `ts_rank` 降序；两者都按 k 上限 `MAX_QUERY_K`（`src/config/const.py`）截断）、`mapping.py`（行↔`ChunkResult`
  映射与 metadata 回填）、`types.py`（`ChunkResult` / `ChunkQueryResult`）。后端为 PostgreSQL +
  pgvector，IO 方法全为 `async`；契约见 `docs/agents/api_contract.md` §4。
- **迁移唯一链**：根 `alembic/`（`alembic.ini` 的 `script_location` 指向它），当前两个
  revision：`alembic/versions/0001_pg_baseline.py`（从零建 8 张表 —— 7 张由 ORM metadata
  生成，`chunks` 为手写增补）与 `alembic/versions/0002_kb_domain.py`（给 `knowledge_base`
  加 `domain` 列，不改表数）。

**分层调用规则**（改代码前必守，详见 `CLAUDE.md`）：`api/` 不得直接调 `infra/`、`config/`，
必须经 `services/`；`api/chat.py` 不含 SSE 格式化函数。前端只经 Nginx `/api/*` 打到后端，
不直连 `app` 容器。

### 后端一次问答的主链路

```
浏览器 chat.html
  → POST /api/chat/stream            src/api/chat.py
  → ChatStreamRequest 校验            src/api/model/request.py
  → AppService → AgentService         src/services/app_service.py、agent_service.py
  → make_initial_state                src/agents/graph/state.py
  → LangGraph.astream_events          src/agents/graph/workflow.py（agent（内化循环）→ agent_finalize → verify → format）
       ├─ agent 节点：首轮组装 + invoke 装配产物   src/agents/graph/agent_node.py
       ├─ 循环装配入口 + 四件套 middleware        src/agents/graph/agent_factory.py、middleware.py
       ├─ tools: retrieve_kb 等        src/agents/tools/rag_tools.py、web_tools.py
       └─ delegate_task → fork 子代理  src/agents/skills/delegate_task.py、executor.py
  → SSE 事件转换 + 落缓冲             agent_service.py _convert_event / chat/streaming.py
  → StreamingResponse（SSE）          src/api/chat.py
```

关键入口：`src/main.py`（异常处理器 + 中间件顺序 `CORS → ResponseProcessor → auth → TraceID → router`）、
`src/api/dependencies.py`（`get_app_service` 依赖注入）、`src/services/agent_service.py:450`
（`_run_generation` 主循环）。数据流链路细节见 `docs/agents/data-flow.md`。

## 三、前端 `deploy/nginx/html/`

Nginx 容器把本目录挂到 `/usr/share/nginx/html` 直接托管，**无 npm / 构建步骤**，
改完刷新即可（详细部署见 `docs/agents/cookbook.md`「部署」）。

| 文件 | 行数 | 用途 | 依赖 |
|------|------|------|------|
| `chat.html` | ~3030 | **对话问答页**（主页面，Nginx 默认 index）| 自包含内联 CSS/JS；仅外链 `vendor/marked.min.js`、`vendor/purify.min.js`、Google Fonts |
| `index.html` | ~1269 | **知识库管理页**（KB/文档增删、上传、分块预览、评测徽标）| 自包含；外链 `js/api.js` |
| `login.html` | ~44 | 登录页 | — |
| `js/api.js` | 209 | REST API 请求封装（统一错误处理 / trace_id） | 被 `index.html` 引用 |
| `js/chat.js` | 809 | 早期 SSE 聊天控制器（外部 JS 版） | **当前页面未引用**（chat.html 已自包含），历史遗留 |
| `css/style.css` | 447 | 早期样式表 | **当前页面未引用**，历史遗留 |
| `vendor/` | — | `marked.min.js`（Markdown 渲染）、`purify.min.js`（XSS 消毒） | chat.html 引用 |
| `*-mockup.html` | — | UI 设计预览稿（chat-harness / eval / kb-harness-page），**非生产页面** | 独立打开 |

> ⚠️ 改前端前先确认改的是 `chat.html` 还是 `js/chat.js`：`js/chat.js` 与 `css/style.css`
> 当前无页面引用，但 e2e 测试与部分归档文档仍会提到它们。新逻辑写进自包含页面，
> 不要误改遗留文件。

### `chat.html` 内部结构（改前必读）

单文件内联 JS，按注释分块（`// ── 段名 ──`）：

| 段（起始行区域） | 内容 |
|------|------|
| State / Session / DOM refs（:840-） | STATE 状态机常量、state 全局（sessionId/kbId/deepThinking/abortController/lastSeq）、双输入框 composer（新对话页 / 历史对话页） |
| 页面切换（:956-） | `showNewPage` / `showHistoryPage` / 头部标题同步 |
| Markdown 渲染（:991-） | `renderMarkdown`（marked + purify）、流式半成品 markdown 归一化 |
| 渲染（:1059-） | 用户气泡、单气泡叙事（过程元素挂当前轮 assistant 气泡内）、状态标签、AI 回答流式/终态、引用横条 + 抽屉 |
| 任务看板（:1437-） | `upsertTask` / `appendDelegateActivity`（数据源 = SSE `task`/`delegate` 事件 + 快照接口） |
| 澄清 composer（:1662-） | `renderComposer` / `submitComposer` → `POST /api/chat/clarify-answer` |
| 反馈（:1810-） | `appendFeedback` / `sendFeedback` → `POST /api/feedback` |
| 思考过程（:1861-） | `renderReasoningDelta`（deep_thinking 的 reasoning 增量） |
| delegate 过程（:1919-） | `ensureDelegateSection` / `appendDelegateDelta` / `finalizeDelegateSection`（fork 子代理 start/delta/end） |
| **SSE 消费（:2036-2240）** | `fetchStream` / `parseSSE` / **`buildStreamHandlers`**（事件分发核心）/ `resumeStream`（断线续传） |
| 发送与停止（:2301-） | `startStream(query)`（POST `/api/chat/stream`，body: session_id/kb_id/query/deep_thinking）、`sendMessage`、`stopGeneration` |
| KB 选择（:2470-） | `loadKBs` / `renderKbMenu` → `GET /api/kbs/list` |
| 会话列表（:2588-） | `loadSessions` / `switchSession` / `newSession` → `/api/sessions/*` |
| 历史回放（:2641-2832） | `rebuildProcessFromEvents`（用事件流重建过程视图）、`loadSessionMessages` |
| 登录 / 用户区（:2862-） | `verifyLogin` / `handleLogout` → `/api/auth/*` |

**前端消费的后端接口（均在 `/api`）**：`chat/stream`、`chat/clarify-answer`、`sessions/{list,events,messages,cancel,task-status}`、
`kbs/list`、`feedback`、`auth/{verify,logout}`、`config`。接口字段语义与 SSE 事件结构见
`docs/agents/api_contract.md`。

## 四、Nginx 路由（`deploy/nginx/nginx.conf`）

| location | 去向 |
|------|------|
| `/` | `root /usr/share/nginx/html; index chat.html`（前端页面） |
| `= /Knowledgebase` | `try_files /index.html`（知识库管理页入口） |
| `/api/` | `proxy_pass http://app:8000`（后端） |
| `/docs`、`/openapi.json` | `proxy_pass http://app:8000`（Swagger） |

## 五、运行时内容库与部署

- `skills/<name>/SKILL.md`：声明式能力文件（frontmatter：name / description / context /
  model / allowed-tools / agent / user-invocable / disable-model-invocation）。**业务侧管理，改内容免改代码**
  （但需重建镜像 —— 无运行时自定义入口）；由 `Dockerfile` COPY 进容器 `/app/skills`。**`context` 未声明时默认 `inline`**（与上游一致）；
  但正文超过 `INLINE_PROMPT_MAX_CHARS`（500 字符）时**自动改用 fork** 承载——inline 会把正文写进
  主 agent 的会话历史并长期占用其预算（超限后静默被裁），故长文不必手写 `context: fork`。
  **显式声明永远优先**：显式 `inline` 且超预算只记 warning（由超限守卫测试承担），显式 `fork`
  与正文长度无关。（`src/agents/skills/loader.py`）
  术语与委派机制见 `docs/agents/glossary.md`「技能委派」。
- `agents/<name>.md`：智能体预设（frontmatter 驼峰键 display_name / description / tools /
  skills / maxTurns；正文为 system prompt 人设）。术语见 `docs/agents/glossary.md`「智能体预设」。
- 顶层 `skills/`（运行时内容）与 `.claude/skills/`（开发期工具链，如 openspec）语义不同，勿混淆。
- 容器日志在 `/data/logs/`，按天轮转；`trace_id` 见 `docs/agents/logging-rules.md`。

## 六、测试 `tests/`

与 `src/` 模块一一对应：`tests/agents`、`api`、`chat`、`chunking`、`cli`、`config`、`core`、
`infra`、`middleware`、`parsers`、`rag`、`services`、`utils`，公共 fixture 在 `tests/fixtures`、
`tests/conftest.py`。外部依赖 mock，不发真实网络请求。

## 七、文档与 OpenSpec

- `docs/agents/`：规则 / 契约 / 排查（本目录，归属见 `CLAUDE.md`「文档组织」表）。
- `docs/design/`：UI 设计系统与页面规格 + HTML 预览（流程见 `docs/agents/ui-design-flow.md`）。
- `openspec/`（→ `docs/openspec/`）：capability specs 与 changes；用 `openspec list` / `openspec status --change <name>` 查询。

## 八、常见改动落点速查

| 我要改… | 落点 |
|------|------|
| **定位文件前：查代码关系**（谁调用它 / 它依赖谁 / 改动波及面） | 先查 `.ua/knowledge-graph.json`（**勿整包读**，1.6 MB）：用 `/understand-chat <问题>`，或按 id grep 节点再取 1-hop 邻域。查询配方、新鲜度判据（hash 不等 ≠ 过期）、自动更新与已知坑见 `docs/agents/knowledge-graph.md` |
| 加/改一个 HTTP 接口 | `src/api/<模块>.py`（路由）+ `src/api/model/request.py`/`response.py`（契约）+ 对应 `services/` 编排；同步 `docs/agents/api_contract.md` + 测试断言 + **前端消费方 `deploy/nginx/html/`** |
| 改一次生成的编排 / 事件转换 | `src/services/agent_service.py`（`_run_generation` / `_convert_event`） |
| 改工具观测（工具 span 的字段 / 归组 / 过滤） | `src/infra/llm/tool_trace.py`（`ToolTraceCollector`）；消费点在 `src/services/agent_service.py::_run_generation` 的事件循环。口径见 ADR-0015 |
| 改模型定价 / 让 Langfuse 出成本 | `src/infra/llm/provider/registry.yaml` 的 `catalog.pricing`（**每 token**）；CLI 播种 `src/cli/seed_langfuse_models.py`；**启动期**由 `src/infra/llm/provider/pricing_seed.py` 幂等 ensure（`.env` 的 `MODEL_*_PRICE_PER_TOKEN` 已废弃） |
| **切/加 LLM provider（百炼 / DeepSeek / …）** | **数据面** `src/infra/llm/provider/registry.yaml`（加一条 provider 条目 ≈ 零代码）；**逻辑面** `spec.py` / `thinking.py`。**切换只改 `.env` 的 `LLM_PROVIDER`**；操作见 `deploy-runbook.md` §2.7，决策见 ADR-0021 |
| 改思考参数形状 / reasoning 回传 | `src/infra/llm/provider/thinking.py`（唯一产生 `extra_body`）+ `chat.py`（`_get_request_payload` 按 `reasoning_field` 回传） |
| 改 agent 循环 / 提示词 | 文案改 `src/config/prompts/templates/*.yaml`（经 `loader.py` 唯一读取）；循环装配与四件套 middleware 改 `src/agents/graph/agent_factory.py`、`middleware.py`；首轮组装与规则挂载点改 `src/agents/graph/agent_node.py`、`nodes.py`、`src/rag/prompt.py` |
| 改跨轮历史摘要（触发阈值 / 摘要提示词 / 调度） | 切分口径 `src/agents/graph/history_window.py`；摘要核心 `src/chat/history_summary.py` + 模板 `templates/task-summary-system.yaml`；调度 `src/services/summary_scheduler.py`；锁 `src/services/summary_lock.py`；存储 `src/chat/summary_store.py`；阈值在 `src/config/const.py` |
| 加/改工具 | `src/agents/tools/`（实现 + 在 `rag_tools.py` 注册）；工具描述文案入 `src/config/` |
| 加/改 skill 机制 | `src/agents/skills/`（loader/registry/executor/delegate_task）；内容放 `skills/<name>/SKILL.md` |
| 加/改智能体预设 | 内容放 `agents/<name>.md`；机制在 `src/agents/presets/`（loader/registry） |
| 改前端对话页 | `deploy/nginx/html/chat.html`（自包含）；**勿改 `js/chat.js`** |
| 改知识库管理页 | `deploy/nginx/html/index.html`（+ `js/api.js`） |
| 改 Nginx 路由 / 静态托管 | `deploy/nginx/nginx.conf`、`deploy/nginx/Dockerfile` |
| 改分块 | `src/chunking/`（`strategies/` 加策略 + `router.py` 挂路由）；排查见 `docs/agents/chunking-issues.md` |
| 改常量 / 文案 / 阈值 | `src/config/`（`settings.py` 环境变量、`prompts/` 提示词、`const.py` 常量与 SSE 文案） |
| 加日志事件 / 前缀 | `src/core/log_events.py`、`log_event_specs.py`；规范见 `docs/agents/logging-rules.md` |
| 改渠道标识 / 加新渠道 | 渠道常量 `src/config/const.py`（`Channel`）；contextvar `src/infra/llm/trace_context.py`；写入口 `src/api/chat.py`、`src/channels/wecom/handler.py`、`src/services/turn_runner.py`；日志段位见 `docs/agents/logging-rules.md`，排障见 `docs/agents/troubleshooting.md` |
| 改数据库 schema / 迁移 | `src/infra/db/models/`（ORM）+ `alembic/versions/`（迁移）；见本文「关系型存储（PostgreSQL）」 |
| 改部署 / 容器 | `docker-compose*.yml`、`Dockerfile`；操作见 `docs/agents/cookbook.md` |
| **加/改接入通道（企微回调/长连接）** | `src/channels/`（`base.py` 抽象 + `wecom/`）；路由 `src/api/wecom.py`；装配 `src/services/wecom_service.py`；配置 `src/config/wecom_bots.py` |
