# Corporate Agent Harness

**企业智能助手 harness** — 聊天底座 + 可插拔工具 / 知识库；RAG 知识库是其中一个能力模块。

上传 PDF / DOCX / TXT 文档即可用自然语言提问：系统从知识库检索相关片段、按需联网补充，
由大语言模型生成带引用来源的回答；领域长任务可委派给 fork 子代理执行。

## 系统架构

```
用户 → Nginx (:80) → /api/* → FastAPI (:8000) → PostgreSQL 15 (+pgvector) / Redis 7 / MinIO
                   → /* → 静态 HTML/CSS/JS 前端（无构建步骤）

Langfuse 2.x (:3000) → 与 app 复用同一 PostgreSQL 实例（独立 database）
模型：三类模型（LLM / Embedding / Rerank）经 provider 兼容层**直连 provider**（默认阿里云百炼，可切 DeepSeek）；
      **不启用** LiteLLM 网关（compose profile `disabled`，保留备查；决策见 `docs/adr/0020-no-model-gateway-direct-provider.md`）
```

- **数据流与管理链路**（上传→入库、问答→检索→生成、KB/会话/认证管理等）统一以
  `docs/agents/data-flow.md` 为准，本 README 只保留索引，不复制细节。
- **技术栈与版本**见下方「技术栈」；**代码结构与模块职责**见 `docs/agents/code-map.md`。

## 链路索引

| 链路 | 一句话 | 入口 |
|---|---|---|
| 1. 文档上传 → 解析 → 入库 | MinIO 存原件 → 解析 → 策略路由分块 → 质量校验 → 向量与全文索引落 PostgreSQL | `POST /api/kbs/documents/upload` |
| 2. 用户问答 → 检索 → 生成 | KB 检索（dense + 全文，RRF 融合）→ 精排 → 流式生成 → 引用高亮；必要时联网兜底 / 委派子代理 | `POST /api/chat/stream` |
| 3. 知识库管理 | 创建（名称去重）/ 列表 / 删除（软删文档 + 清理向量） | `POST /api/kbs/list`、`POST /api/kbs`、`POST /api/kbs/delete` |
| 4. 会话管理 | 列表 / 消息历史与过程回放 / 删除 / 取消在途生成 | `POST /api/sessions/list`、`POST /api/sessions/messages`、`POST /api/sessions/delete` |
| 5. 认证 | 登录（首次自动注册）/ 校验 / 登出；token 存 Redis | `POST /api/auth/login`、`POST /api/auth/verify`、`POST /api/auth/logout` |
| 6. RAGAS 质量评估（CLI） | 测试集生成 + 评估 + 可选质量门禁 | `python -m src.cli.eval_ragas` |
| 7. 分块质量评估 | 嵌入在链路 1 中，由 `CHUNK_EVAL_ENABLED` 开关控制 | `src/chunking/scorer.py` |

> 端点与事件流的完整契约见 `docs/agents/api_contract.md`。

## 功能特性

- **文档格式**：PDF / DOCX / TXT；表格与跨页做兼容处理（不做 OCR）
- **知识库管理**：多知识库创建 / 切换 / 删除
- **混合检索**：pgvector 稠密检索 + PostgreSQL 全文检索（jieba 预分词），RRF 融合后精排
- **流式输出**：回答逐 token 推送；生成与 SSE 连接解耦，断线可按 `seq` 续传
- **引用溯源**：回答附来源文档与页码，编号全局唯一；来源按权威度分级（KB=T0，web T1–T4）
- **智能体预设**：`agents/<name>.md` 指定人设与工具面，会话内 bind-once
- **技能与委派**：`skills/<name>/SKILL.md` 声明式能力 —— 短正文 inline 注入主 agent，
  超预算正文自动改 fork、由主 agent 经 `delegate_task` 委派子代理执行；也支持 `/xxx` 直出
- **澄清交互**：材料不足时经 `ask_user` 向用户追问（会话内已确认则不重复问）
- **联网兜底**：知识库检索不达标时经 Tavily 补充，结果进同一引用池
- **深度思考开关**：按会话开启模型思考模式
- **任务看板**：`task` 工具集 + 会话任务注册表，经 SSE 推送任务事件
- **对话历史**：Redis 缓存最近多轮（自动降级到内存）+ PostgreSQL 持久化与过程回放
- **可观测**：一次生成 = 一条 Langfuse trace，含嵌套的模型轮次与工具 span
- **部署**：Docker Compose 一键起

## 技术栈

| 层 | 技术 |
|-----|--------|
| 前端 | Nginx + 原生 HTML/CSS/JS（无构建步骤） |
| API | FastAPI + Uvicorn（含 SSE 流式） |
| 后端 | Python 3.11 / LangChain 1.x + LangGraph 1.x |
| Agent 循环 | LangChain `create_agent` + 四件套 middleware；唯一装配入口 `src/agents/graph/agent_factory.py` |
| 关系库 | PostgreSQL 15（`pgvector/pgvector:pg15`）+ SQLAlchemy 2.x / asyncpg；迁移由 Alembic 管理 |
| 检索 | pgvector（`vector(1024)`）稠密路 + PostgreSQL 全文检索（`tsvector('simple')` + jieba 预分词）词法路，应用层 RRF 融合 |
| 对象存储 | MinIO（dev）/ 阿里云 OSS（prod） |
| 缓存 | Redis 7（会话、登录 token、生成任务） |
| 模型 | **provider 可切换**（默认阿里云百炼 DashScope 兼容端点，可切 DeepSeek）：provider 差异收敛到单一层 `src/infra/llm/provider/`，切换只改 `.env`（见「切换大模型」） |
| Tracing | Langfuse 2.x 自托管，与 app 复用同一 PostgreSQL 实例（独立 database） |
| 文档解析 | PyMuPDF（PDF）、python-docx（DOCX）、chardet（编码嗅探） |
| 评价 | RAGAS（CLI 侧：faithfulness / answer_relevancy / context_recall / context_precision） |
| 日志 | loguru（分层前缀 + 英文 k=v，见 `docs/agents/logging-rules.md`） |
| 模型网关 | **不启用** LiteLLM Proxy（compose profile `disabled`，保留备查；决策见 ADR-0020） |
| 部署 | Docker Compose |

## 快速启动

### 前置条件

- Docker & Docker Compose
- 模型服务 Key：默认直连阿里云百炼 DashScope 兼容端点；可按 `.env` 切到 DeepSeek（见「切换大模型」）。**不启用** LiteLLM 网关（ADR-0020）
- 可选 Tavily API Key：联网兜底；留空时不注册 `search_web`，检索不达标走纯拒答

### 启动步骤

```bash
# 1. 配置环境变量
cp .env.template .env
# 编辑 .env：至少填默认 provider 的密钥（<PROVIDER>_API_KEY，如 DASHSCOPE_API_KEY）
#   provider 选择用 LLM_PROVIDER（缺省 dashscope；切 DeepSeek 见「切换大模型」）

# 2. 启动所有服务（dev 经 COMPOSE_PROFILES 默认带 langfuse profile）
docker compose up --build -d

# 3. 访问
#   前端界面： http://localhost        （对话 + 知识库管理）
#     登录测试：账号 admin / 密码 admin123（首次输入自动注册）
#   API 文档： http://localhost/docs   （Swagger UI）
#   Langfuse： http://localhost:3000   （管理员见 .env 的 LANGFUSE_INIT_USER_*）

# 4. Langfuse 不需要手工初始化
#   首次在空库上启动时，由 .env 的 LANGFUSE_INIT_* 自动播种 org / project / 管理员与 API Key。
#   两个静默坑：PROJECT_ID 留空则 project 与 key 都不建（不报错）；管理员邮箱/密码缺任一则 UI 登录不了。
```

> ⚠️ 容器 env 在**创建时**固化：改完 `.env` 后 `docker compose restart app` **不生效**，
> 必须 `docker compose up -d --force-recreate app`（只改依赖才需要 `--build`）。

### 使用流程

1. 创建知识库（如 "2024年年报"）
2. 上传文档（PDF / DOCX / TXT）
3. 在对话框中输入问题（可选：开启「深度思考」、选择知识库 / 智能体 / 技能）
4. 查看回答、引用来源与（若发生委派的）子代理分析过程

## 部署指南

### 1. Docker Compose 完整部署（推荐）

一键启动全部服务（`litellm-proxy` 属 `disabled` profile 且**不启用**，见 ADR-0020；`langfuse-web` 由 `COMPOSE_PROFILES` 决定）：

```bash
docker compose up -d --build
```

| 服务 | 容器名 | 端口 | 说明 |
|------|--------|------|------|
| Nginx | `corporate-rag-nginx` | `:80` | 反向代理 + 静态文件服务 |
| FastAPI | `corporate-rag-app` | `:8000` | REST API + SSE 流式 |
| PostgreSQL | `corporate-rag-postgres` | `:5432` | 应用关系库 + pgvector 向量索引；Langfuse 复用同一实例 |
| Redis | `corporate-rag-redis` | `:6379` | 会话 / 登录 token / 生成任务 |
| MinIO | `corporate-rag-minio` | `:9000` / `:9001` | 文档对象存储（API / 控制台） |
| Langfuse | `corporate-rag-langfuse-web` | `:3000` | Tracing 面板（profile `langfuse`） |
| LiteLLM Proxy | `corporate-rag-litellm-proxy` | `:4000` | **不启用**（profile `disabled`，保留备查；ADR-0020） |

启动后访问：

- **前端页面** → http://localhost （对话主界面；`/Knowledgebase` 为知识库管理页）
- **API 文档** → http://localhost/docs （Swagger UI；OpenAPI 规范在 `/openapi.json`）
- **Langfuse** → http://localhost:3000

### 2. 本地开发模式（热重载 + 前端反代）

本地跑：**后端用宿主 `.venv` 起 uvicorn，前端用一个一次性 nginx 容器**反代静态文件与 `/api`。
端口按 slot 分配，**多个 worktree 可并存**（本仓只有一套 compose 容器，worktree 内不跑 compose —— 原因与边界见 `docs/agents/cookbook.md`「并行会话（worktree）」）。

```bash
scripts/dev-worktree.sh up              # 默认 slot 1：后端 8001 / 前端 8080
scripts/dev-worktree.sh up --slot 2     # 第二个 worktree 换 slot：后端 8002 / 前端 8081
scripts/dev-worktree.sh status          # 查看当前端口与进程
scripts/dev-worktree.sh down            # 停止
```

- 前端 → `http://localhost:8080`（`/api/` 经反代打到本地 8001）
- 接口 → `http://localhost:8001/docs`
- 依赖服务（`postgres` / `redis` / `minio` / `langfuse-web`）仍用容器那套，无需另起；脚本已内置宿主侧必需的 `POSTGRES_HOST=localhost`。
- 首次冷启动在 ext4（`/root/code/corporate_rag`）上只需数秒；若仓库落在 `/mnt/d`（9p）上则约需 1 分钟。脚本会等到健康检查通过再返回。
- 反代配置模板：`deploy/nginx/nginx.dev.conf.template`（与生产 `nginx.conf` 只差 `proxy_pass` 目标）。

> ⚠️ **前端不要只用一个静态服务器**（如 `python3 -m http.server 8080`）：前端全部用相对路径 `fetch('/api/...')`，
> 没有反代时 `/api/*` 会打到静态服务器上、全部 404。纯样式预览见下一节。
> 也不要用容器 nginx（`:80`）来访问本地代码——它反代的是**容器里的 app**，与本地进程无关。

### 3. 纯前端预览（无需后端）

只想看页面样式效果，不需要 API：

```bash
python3 -m http.server 8080 --directory deploy/nginx/html/
# 浏览器打开 http://localhost:8080
```

### 4. 单独管理各服务

```bash
# 构建并启动所有服务
docker compose up -d --build

# 查看所有服务状态
docker compose ps

# 查看应用日志
docker compose logs -f app
docker compose logs -f nginx

# 只重启某个服务
docker compose restart app

# 停止所有服务
docker compose down

# 停止并清除所有数据（慎用）
docker compose down -v

# 查看容器网络
docker network inspect corporate_rag_network

# 改了 .env / compose（env、volumes、command）后生效：必须重创容器
docker compose up -d --force-recreate app
```

## Nginx 路由说明

```
http://localhost/              → deploy/nginx/html/ 静态文件（默认 chat.html）
http://localhost/Knowledgebase → 知识库管理页（index.html）
http://localhost/login.html    → 登录页（未登录访问 / 会 302 到这里）
http://localhost/api/*         → 反向代理到 app:8000
http://localhost/docs          → FastAPI Swagger 文档
http://localhost/openapi.json  → FastAPI OpenAPI 规范
```

Nginx 已预配 SSE 支持（`proxy_buffering off` + 300s 读超时），确保流式问答不卡顿。
路由定义见 `deploy/nginx/nginx.conf`（唯一归属）。

---

## 项目结构

```
├── src/                   后端（分层 api/ → services/ → agents/ + rag/ + chat/ + chunking/ + infra/）
├── tests/                 单元测试（与 src/ 模块一一对应）
├── deploy/                nginx 配置与前端静态文件、postgres 初始化脚本
│   └── nginx/html/        前端页面（chat.html / index.html / login.html，无构建步骤）
├── skills/                运行时 skill 内容库（<name>/SKILL.md，业务侧管理，随镜像发布）
├── agents/                智能体预设内容库（<name>.md，业务侧管理，随镜像发布）
├── alembic/               数据库迁移（唯一链）
├── scripts/               运维脚本（清库、重建 KB 数据、重写 content_seg、本地起服务）
├── litellm/               LiteLLM 网关配置（**不启用**，备查；ADR-0020）
├── docker-compose.yml / .override.yml / .prod.yml / .image.yml（生产部署用）
├── Dockerfile
├── pyproject.toml         依赖与工具配置（ruff / pyright / pytest / doc_anchors）
└── data/ logs/            运行期数据与日志挂载点（gitignore）
```

> 各模块职责、常见改动落点速查、以及后端 `src/` 的完整分层说明见
> `docs/agents/code-map.md`（结构的唯一归属文档），本 README 不复制其内容。

## 配置说明

通过 `.env` 配置（模板与逐项注释见 `.env.template`），主要配置项：

| 配置项 | 说明 |
|---------|------|
| `LLM_PROVIDER` / `<ROLE>_PROVIDER` | **选模型 provider**（缺省 `dashscope`）；角色级覆盖见下方「切换大模型」 |
| `<PROVIDER>_API_KEY` | 各 provider 的密钥（如 `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY`） |
| `LLM_MODEL` / `CLASSIFY_MODEL` / `SUMMARY_MODEL` / `RAGAS_LLM_MODEL` | **可选覆盖**模型名；不填则取 `registry.yaml` 的角色默认 |
| `LLM_BASE_URL` / `LLM_API_KEY` | ⚠️ **已废弃**（旧"单 provider 覆盖"键；切换 provider 时必须删除，见 `deploy-runbook.md` §2.7） |
| `EMBEDDING_MODEL` | 向量化模型（可选覆盖；**维度由 `registry.yaml` 声明**，启动期与 `chunks.embedding` 列校验） |
| `RERANK_MODEL` | 精排模型（走 DashScope） |
| `DASHSCOPE_API_KEY` | 百炼 provider 的密钥（即 `dashscope` 的 `<PROVIDER>_API_KEY`） |
| `PROVIDER_VALIDATE` | 启动期 provider 配置校验开关（缺省 `on`；`off` 跳过） |
| `TAVILY_API_KEY` / `WEB_SEARCH_ENABLED` / `WEB_SEARCH_PER_TURN_LIMIT` | 联网兜底（Key 为空时不注册 `search_web`） |
| `POSTGRES_*` | 应用关系库与向量索引 |
| `REDIS_*` | 会话 / token / 生成任务 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 文档对象存储（compose 侧 root 凭据与 app 侧**同源**，无需手动保持一致） |
| `LANGFUSE_*` / `LANGFUSE_INIT_*` | 自托管 Langfuse（应用侧 Key 必须与播种 Key 是同一对） |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 分块参数 |
| `TOP_K_RETRIEVAL` / `TOP_K_RERANK` | 检索召回数与精排保留数 |
| `MEMORY_WINDOW` | 对话历史窗口 |

## 切换大模型（provider）

LLM provider 的差异（思考参数形状 / reasoning 字段 / 端点 / 密钥 / 定价 / 窗口）已收敛到**单一层**
`src/infra/llm/provider/`（数据面 `registry.yaml` 随镜像发布；决策见 `docs/adr/0021-provider-compat-layer.md`）。
**切换 provider 不改代码**：

| provider | 说明 |
|---|---|
| `dashscope`（默认） | 阿里云百炼兼容端点 |
| `deepseek` | DeepSeek（`deepseek-flash` / `deepseek-v4-pro`）；**无 embedding / rerank 端点** |

- **怎么切**：改 `.env` 的 `LLM_PROVIDER`（+ `<PROVIDER>_API_KEY`）→ 同步目标机 `.env` → 重跑部署。
  **完整步骤与坑（要删哪些旧键）见 `docs/agents/deploy-runbook.md` §2.7** —— 本 README 不复述步骤。
- **角色级覆盖**：`<ROLE>_PROVIDER`（`main` / `classify` / `summary` / `judge` / `embedding` / `rerank`）。
  `main`/`classify`/`summary` 跟随 `LLM_PROVIDER`；**`embedding` / `rerank` / `judge` 独立缺省 `dashscope`**
  —— 因此切 DeepSeek 时，**向量化与精排仍留百炼**（否则启动期校验拒绝）。
- **模型名 / 定价 / 窗口**均缺省取 `registry.yaml`，`.env` 不必写；定价在**启动期自动 ensure**（无需手动 seed）。
- 回退：把 `LLM_PROVIDER` 改回旧值即可（`.env` 可逆）；**跨版本回滚须镜像与 `.env` 一起回**。

## RAGAS 评估

```bash

# 生成测试集（需先创建知识库并上传测试文档）
python -m src.cli.eval_ragas --kb-id <KB_ID> --generate --size 20

# 运行评估（加 --gate 启用质量门禁，不达标退出码为 1）
python -m src.cli.eval_ragas --kb-id <KB_ID>

# 列出可用知识库
python -m src.cli.eval_ragas --list-kbs
```

> 宿主侧执行需加 `POSTGRES_HOST=localhost` 前缀（`.env` 里写的是 compose 服务名）。

## 已知限制

- 扫描件 PDF 不支持（无 OCR 能力）
- 表格 / 数字可能因分块被切断（只检测不保护）
- RAGAS 评估的 judge 角色**独立缺省 `dashscope`**（可用 `RAGAS_PROVIDER` / `RAGAS_LLM_MODEL` 与生成模型分离以降低 self-bias）；若与生成模型同源，仍存在 self-bias 风险
- 单 worker 部署：SSE 帧缓冲与生成任务注册表在进程内存中，多副本需另做共享层
- MCP 工具接入**尚未实现**（`src/agents/tools/registry.py` 只预留了扩展位）
- 生产已上线**单机 demo**（ECS + 全镜像 `docker-compose.image.yml`，见 `docs/agents/deploy-runbook.md`）；**多机 + 托管中间件**（RDS / Redis / OSS / SLB）尚未闭环

## 许可证

MIT

## 运行
### 创建虚拟环境
python -m venv .venv

### 激活虚拟环境
source .venv/bin/activate

### 安装项目依赖
pip install -e .

### 本地开发（启动依赖服务）
docker compose up -d redis postgres minio langfuse-web

### 运行 API 服务（宿主侧热重载）
```bash
# 宿主进程解析不了 .env 里的服务名，必须用 localhost 覆盖
POSTGRES_HOST=localhost uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### 运行测试
```bash
# 宿主侧必加该前缀；容器内不加（用 .env 的服务名）
POSTGRES_HOST=localhost pytest tests/ -v
```

### 代码检查（提交前由 pre-commit 自动跑）
```bash
ruff format . && ruff check . --fix       # 格式化 + lint
.venv/bin/pyright src/                    # 类型检查
.venv/bin/python -m src.cli.check_docs    # 文档防腐（路径/路由/符号/禁用词）
.venv/bin/python -m src.cli.check_adr     # ADR 头部与索引一致性
```

### 工作流程
大需求流程与 skill 路由表见 `docs/agents/dev-flow.md`（唯一归属，本 README 不复制）


## 大模型写代码的坑
### 技术细节上面幻觉非常多，一定要详细问，并且强制要求网上搜相应的文章进行结合后再回答
### 前后端搭配写代码时，一定要求大模型生成接口契约，否则生成的代码，前后端接口字段对不上
### 链接数据库，中间件等的方法，一定注意异步同步，大模型都是默认同步的， 其他情况下一定要询问同步/异步的问题，还有接口统一用post方式
### 大模型的知识盲区，一定要多问，多问，多问， 不会直接给你最好的方案，只会贴合你当前代码的给你方案，导致没有架构性，前瞻性，只是为了解决当前的问题。
### 大模型对工程化，对可用性，对性能的理解较弱，做项目的时候一定要自己关注这块
目前具体工程化的问题有，链路统一的traceid，同步/异步调用，线程池的使用，上下文contextvars的使用，添加日志系统给大模型查日志，添加异常统一管理，添加metrics,span完善监控，工程化的框架需要形成rule。代码的治理很麻烦，包装的方法要通过rule指定使用。
### 领域深化不够，不能针对领域特殊的case做出应对，必须要挑出来，单独处理。
文档分块，非正常格式的文档，需要做兼容。 文档表格跨页
### 针对项目结构有盲点，比如给前端对象，数据库对象，中间件对象，等都不涉及提出优化。
### 大模型不会泛型的概念，然后设计模式只能出常用的，理解程度不深
