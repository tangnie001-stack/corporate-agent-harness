# WeKnora 运行时依赖调研

> 调研对象：本地源码镜像 `/mnt/d/code/demo/AIAgent/github/WeKnora`
> 一手来源：仓库内的 `docker-compose*.yml` / `Dockerfile*` / `.env*.example` / `go.mod` / `config/` / `docs/LITE.md` 等（下文每条结论均标注来源文件与行号）。
> 说明：本仓库为 GitHub 克隆，作为本次调研的唯一依据；未在源码中找到证据的内容一律标注「未从源码确认」。
> 调研日期：2026-09-18

---

## 0. 一句话结论

WeKnora 有**两套运行形态**，依赖差异极大：

- **标准版（默认 `docker compose up`）**：编排 5 个常驻容器 —— `frontend` + `app` + `docreader` + `postgres(ParadeDB)` + `redis`，再外接**一个 LLM + 一个 Embedding 模型服务**（云 API 或自建 Ollama）才能真正问答。
- **Lite 版**：单个 Go 二进制，内置 SQLite + sqlite-vec + 内存流管理，**不依赖** Postgres / Redis / docreader 容器（`docs/LITE.md`、`.env.lite.example`），但仍需外接模型服务。

### 最小可运行依赖集

| 形态 | 必需组件 | 说明 |
|------|---------|------|
| 标准版（可问答） | `frontend` + `app` + `docreader` + `ParadeDB(pg17)` + `redis` + LLM 服务 + Embedding 服务 | 5 个容器 + 至少 2 个模型服务；向量检索默认复用 ParadeDB（pgvector + pg_search BM25） |
| Lite（可问答） | 单个 `WeKnora-lite` 二进制 + LLM 服务 + Embedding 服务 | 无容器中间件；SQLite 落盘，流管理走内存 |
| 只从源码跑后端（开发） | `docker-compose.dev.yml` 的 `postgres` + `redis`（+ 可选 `docreader`），本地 `go run` | `scripts/dev.sh`、`Makefile` `dev-start`/`dev-app` |

> 「必需」分两层：**进程层**（容器/二进制）与**功能层**（模型服务）。模型服务不是环境变量层面的必填项，但不配置则无法问答（见 §4）。

---

## 1. 组件总表

| 组件 | 类别 | 是否必需（标准版默认） | 用途 | 来源 |
|------|------|:---:|------|------|
| `frontend`（nginx 静态托管 Vue SPA） | 应用服务 | 是（默认启动） | Web UI，反代后端 API | `docker-compose.yml:2-34`；`frontend/Dockerfile:60,76` |
| `app`（Go） | 应用服务 | 是 | 主后端：REST API、RAG 编排、Agent、任务队列消费 | `docker-compose.yml:36-382`；`docker/Dockerfile.app:15,87,152` |
| `docreader`（Python gRPC） | 应用服务 | 是（默认启动），功能上可绕过 | 复杂文档解析（PDF/Office/OCR/多模态/ASR） | `docker-compose.yml:398-500`；`docker/Dockerfile.docreader:4,156` |
| `postgres`（ParadeDB v0.22.2-pg17） | 外部中间件 | 是（`DB_DRIVER=postgres` 默认） | 主数据库 + 默认向量库 + BM25 全文检索 | `docker-compose.yml:521-540`；`.env.example:126-140` |
| `redis`（redis:7.0-alpine） | 外部中间件 | 是（`STREAM_MANAGER_TYPE=redis` 默认） | 聊天流缓冲 + Asynq 异步任务队列 | `docker-compose.yml:542-548`；`.env.example:142-159` |
| 对象存储（local / MinIO / 云） | 存储 | 默认 `local` 即可 | 上传文件与解析产物 | `.env.example:186-261`；`docker-compose.yml:602-623` |
| LLM 服务 | 模型服务 | 功能必需 | 问答/改写/摘要/Agent 推理 | `config/config.yaml`；`README_CN.md:158` |
| Embedding 服务 | 模型服务 | 功能必需 | 向量化 | `README_CN.md:160` |
| Rerank 服务 | 模型服务 | 可选 | 精排（`enable_rerank` 默认 true，可用模型缺省降级） | `config/config.yaml:21`；`rerank_server_demo.py` |
| VLM / ASR 服务 | 模型服务 | 可选 | 图片多模态 / 扫描件 OCR / 音频转写 | `config/config.yaml:46-47`；`docreader/README.md` |
| Neo4j | 外部中间件 | 可选（`--profile neo4j`） | GraphRAG 知识图谱 | `docker-compose.yml:625-644`；`.env.example:330-338` |
| Qdrant / Milvus / Weaviate / Doris / OpenSearch / ES / 腾讯 VectorDB | 向量库替代 | 可选 | 替换默认 ParadeDB 向量检索 | `docker-compose.yml:646-765`；`.env.example:268-328` |
| MinIO | 对象存储 | 可选（`--profile minio`） | S3 兼容对象存储 | `docker-compose.yml:602-623` |
| SearXNG | 外部服务 | 可选（`--profile searxng`） | 自建网络搜索 | `docker-compose.yml:555-600` |
| Langfuse（web + worker + ClickHouse + MinIO） | 可观测性 | 可选（`--profile langfuse`） | LLM 调用链路追踪（唯一追踪后端） | `docker-compose.yml:779-996` |
| Dex | 外部服务 | 可选（`--profile dex`） | OIDC 测试 IdP | `docker-compose.yml:767-777` |
| MCP Server（Python） | 应用服务 | 可选（`--profile full`） | 对外暴露 MCP 工具 | `docker-compose.yml:998-1022`；`mcp-server/Dockerfile:1` |
| odl-hybrid（OpenDataLoader） | 应用服务 | 可选（`--profile odl-hybrid`） | 高精度 PDF 解析后端 | `docker-compose.yml:504-518`；`docker/Dockerfile.odl-hybrid:8,22` |
| sandbox（Docker/E2B/Cube） | 应用服务 | 可选（非常驻，按会话拉容器） | 技能沙箱执行 | `docker-compose.yml:386-396`；`.env.example:595-600` |

---

## 2. 外部中间件详述

### 2.1 PostgreSQL（默认即 ParadeDB，非原生 postgres）

- 镜像：`paradedb/paradedb:v0.22.2-pg17`（`docker-compose.yml:522`）。
- 它同时承担**三个角色**：主数据库、默认向量库（`RETRIEVE_DRIVER=postgres`）、BM25 全文索引。初始化脚本建了 `vector`(pgvector)、`pg_search`(ParadeDB BM25)、`pg_trgm`、`uuid-ossp` 扩展，并建了 `USING bm25 (...)` 索引（`migrations/paradedb/00-init-db.sql:1-5,205`）。
- **推论（重要）**：默认配置下不能直接用官方 `postgres` 镜像替换，必须是带 pgvector + ParadeDB pg_search 的镜像，否则默认检索链路不可用。
- `DB_DRIVER` 还支持 `mysql` / `sqlite`（`.env.example:126-140`；`go.mod` 引入 `gorm.io/driver/postgres`、`gorm.io/driver/sqlite`、`go-sql-driver/mysql`）。`migrations/` 下确有 `mysql` / `paradedb` / `sqlite` 三套脚本。
- 生产 compose **未把 5432 映射到宿主机**（`docker-compose.yml:521-540` 无 `ports:`）；dev compose 才映射（`docker-compose.dev.yml:7-8`）。

### 2.2 Redis

- 镜像 `redis:7.0-alpine`，启动参数 `--appendonly yes --requirepass ${REDIS_PASSWORD}`（`docker-compose.yml:543-545`）。
- 两个职责：聊天流处理（`STREAM_MANAGER_TYPE=redis`，默认）+ Asynq 异步任务队列（`.env.example:142-159`）。Langfuse 自建栈复用同一 Redis 的 DB 1（`docker-compose.yml:784`）。
- **可关闭**：设 `STREAM_MANAGER_TYPE=memory` 且 `REDIS_ADDR` 留空即不依赖 Redis（`.env.example:145-147`），Lite 即此模式。

### 2.3 对象存储

- 类型枚举：`local / minio / cos / tos / s3 / obs / oss / dummy`，默认 `STORAGE_TYPE=local`（`.env.example:186-188`）。
- MinIO 是 compose 的可选 profile（`--profile minio`，`docker-compose.yml:602-623`），默认**不启动**；默认走 `LOCAL_STORAGE_BASE_DIR=/data/files` + `data-files` volume（`docker-compose.yml:48,219`）。
- 云厂商支持腾讯 COS / 火山 TOS / AWS S3 / 华为 OBS / 阿里 OSS（`.env.example:200-261`）。

### 2.4 向量库（可替换）

`RETRIEVE_DRIVER` 支持（逗号分隔可多驱动）：`postgres`（默认）/ `elasticsearch_v7` / `elasticsearch_v8` / `opensearch` / `qdrant` / `milvus` / `weaviate` / `doris` / `tencent_vectordb`（`.env.example:268-271`）。对应 compose profile：

| 向量库 | profile | 镜像 | 端口（宿主机） | 来源 |
|--------|---------|------|---------------|------|
| ParadeDB(pgvector) | 默认 | `paradedb/paradedb:v0.22.2-pg17` | 内网 5432 | `docker-compose.yml:521-540` |
| Qdrant | `qdrant` | `qdrant/qdrant:v1.16.2` | 6333 / 6334 | `docker-compose.yml:646-659` |
| Milvus（standalone，内嵌 etcd） | `milvus` | `milvusdb/milvus:v2.6.11` | 19530 / 9091 | `docker-compose.yml:661-687` |
| Weaviate | `weaviate` | `semitechnologies/weaviate:1.28.4` | 9035→8080 / 50052→50051 | `docker-compose.yml:689-710` |
| Apache Doris（FE+BE） | `doris` | `apache/doris:fe-4.1.0` / `be-4.1.0` | 8030 / 9030 / 8040 | `docker-compose.yml:728-765` |
| OpenSearch | 仅 dev compose `opensearch` | `opensearchproject/opensearch:3.3.2` | 9200 | `docker-compose.dev.yml:126-154` |

> OpenSearch 只出现在 `docker-compose.dev.yml`，生产 `docker-compose.yml` 无对应服务，但代码/环境变量支持 `opensearch` 驱动（`.env.example:279-284`）。

**向量库有两条独立的启用路径（模型同理，见 §4.1）**：

| 路径 | 机制 | 证据 |
|------|------|------|
| env 驱动 | `RETRIEVE_DRIVER=milvus` → app 启动时按 `slices.Contains` 注册引擎，地址取 `MILVUS_ADDRESS`（默认 `localhost:19530`） | `.env.example:269-271`；`internal/container/container.go:1326-1342` |
| 前端 UI 添加 | Milvus 被注册为可在界面新增的向量库类型，带 `ConnectionFields`（addr/database/username/password）与 `IndexFields`（collection_name/shards_num/replica_number），存 DB，不依赖 env 重启 | `internal/types/vectorstore.go:702-716` |

> `vectorstore.go:684-686` 注释：Postgres/SQLite 被排除在 UI 类型列表外，因为它们只能走 app 默认连接（`UseDefaultConnection=true`）；其余驱动可从 UI 动态添加。

**多驱动并存**：`RETRIEVE_DRIVER` 支持逗号分隔多个驱动，配合 `MULTI_STORE_RETRIEVE_TIMEOUT` 做并行检索（`.env.example:269,272-273`）。因此替代向量库不是「必须替换掉默认库」的二选一。

**Milvus 部署细节**：compose 内的 Milvus 是精简 standalone —— `ETCD_USE_EMBED=true` + `COMMON_STORAGETYPE=local`（`docker-compose.yml:667-671`），单容器即可，无需独立 etcd + MinIO。注意其 profiles 仅为 `milvus`，**不在 `full` 中**（对比 qdrant 为 `qdrant, full`，`docker-compose.yml:657-659,686-687`）。`MILVUS_METRIC_TYPE` 默认 `IP`，修改需先跑 `cmd/milvus-migrate` 重建 collection（`.env.example:295-298`）。自建时 `milvus` 主机名已在 SSRF 白名单默认放行（`.env.example:745-748`）。

### 2.5 Neo4j（GraphRAG，可选）

- 唯一开关 `NEO4J_ENABLE`（旧变量 `ENABLE_GRAPH_RAG` 已废弃、代码不再读取，`.env.example:331-333`）。
- profile `neo4j`，镜像 `neo4j:2025.10.1`（compose）/ `neo4j:latest`（dev），端口 7474/7687，需 APOC 插件（`docker-compose.yml:625-644`）。

### 2.6 可观测性 Langfuse（可选自建栈）

- `--profile langfuse` 会额外拉起：`langfuse-db-init`（一次性，复用 WeKnora postgres 建 `langfuse` 库）、`langfuse-clickhouse:24.8`、`langfuse-minio`、`langfuse-worker:3`、`langfuse-web:3`（`docker-compose.yml:799-996`）。
- 默认未启用；也可用 Langfuse Cloud（`LANGFUSE_HOST` 默认 `https://cloud.langfuse.com`，`docker-compose.yml:150`）。启用条件是 `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` 同时设置（`.env.example:656`）。

---

## 3. 应用自身进程构成

| 服务 | 语言/技术栈 | 基础镜像 | 容器内端口 | 职责 | 来源 |
|------|-----------|---------|-----------|------|------|
| `frontend` | Vue 3 SPA + nginx | 构建 `node:24-bookworm-slim`，运行 `nginx:1.30.3-alpine` | 80 | 静态 UI + 反代 `/api` | `frontend/Dockerfile:26,60,76` |
| `app` | Go 1.26（Gin/GORM） | 构建 `golang:1.26-bookworm`，运行 `debian:12.12-slim` | 8080 | REST API、RAG、Agent、队列 worker | `docker/Dockerfile.app:15,87,152` |
| `docreader` | Python 3.10（gRPC） | `python:3.10.18-bookworm` | 50051（仅 `expose`，不发布） | 文档解析；内置 LibreOffice/Playwright/antiword | `docker/Dockerfile.docreader:4,82,156`；`docker-compose.yml:406-413` |
| `mcp`（可选） | Python 3.11 + MCP SDK | `python:3.11-slim` | 8000 → 宿主机 8082 | 对外 MCP 工具（依赖 app 的 REST API） | `docker-compose.yml:998-1022`；`mcp-server/Dockerfile:1` |
| `odl-hybrid`（可选） | Python | `python:3.10.18-bookworm` | 5002 | OpenDataLoader hybrid PDF 解析 | `docker/Dockerfile.odl-hybrid:8,22` |
| `sandbox`（可选，非常驻） | Python 3.12 + Node 20 | `python:3.12-slim` 等 | 49983 / 6080 | 会话级技能沙箱（Docker/E2B/Cube） | `docker/Dockerfile.sandbox:29-38,125,184` |

**默认 profile 下的常驻容器共 5 个**：`frontend`、`app`、`docreader`、`postgres`、`redis`（`sandbox` 虽在文件里但带 `profiles: [full]` 且 `command: ["true"]`，非常驻，`docker-compose.yml:384-396`）。

依赖方向（`depends_on`）：`frontend` → `app`（healthy）；`app` → `redis`(started) + `postgres`(healthy) + `docreader`(healthy)（`docker-compose.yml:29-31,371-377`）。

---

## 4. 模型与 API 依赖

### 4.1 模型是「运行时配置」，不是 env 必填

- 模型（LLM/Embedding/Rerank/VLM/ASR）通过**前端「设置」写入数据库**，或通过 `config/builtin_models.yaml` 声明式预置（`config/builtin_models.yaml.example:1-27`）。
- `.env.example` 里**没有**任何形如 `OPENAI_API_KEY` 的硬性必填项；D2 段只给出 `LLM_*`/`EMBEDDING_*`/`RERANK_*` 的**占位命名参考**，且默认全部注释（`.env.example:358-379`）。
- 因此：环境变量层面模型非必填，但**功能层面必须配置至少一个 LLM + 一个 Embedding 模型**，否则无法问答/建库。
- `OLLAMA_OPTIONAL=true` 默认：Ollama 不可用仅告警不阻断启动（`.env.example:346-349`）。

### 4.2 支持的模型供应商（文档口径）

| 类型 | 支持的供应商/接口 | 来源 |
|------|------------------|------|
| LLM | OpenAI / Azure OpenAI / Anthropic / DeepSeek / Qwen(阿里云) / 智谱 / 混元 / 豆包 / Gemini / MiniMax / NVIDIA / Novita / SiliconFlow / OpenRouter / Requesty / LiteLLM / Ollama | `README_CN.md:158` |
| Embedding | Ollama / BGE / GTE / 智谱 / OpenAI 兼容接口 | `README_CN.md:160` |
| 模型类型枚举 | `KnowledgeQA / Embedding / Rerank / VLLM / ASR` | `config/builtin_models.yaml.example:34` |
| 解析引擎 | `simple`(Go 内置) / `builtin`(docreader) / `anydoc`(进程内) / `weknoracloud` / `mineru` / `mineru_cloud` / `paddleocr_vl` / `paddleocr_vl_cloud` | `internal/infrastructure/docparser/engines.go:13-30` |

### 4.3 自建模型服务

- **Rerank 可自建**：仓库根有 `rerank_server_demo.py`（FastAPI + Transformers，加载本地 rerank 模型），可自托管后把 `RERANK_BASE_URL` 指过去。
- **Ollama**：`.env.example:348-349` 默认 `OLLAMA_BASE_URL=http://host.docker.internal:11434`，compose 给 app 注入了 `host.docker.internal` 映射（`docker-compose.yml:381-382`）。
- **VLM/ASR**：作为能力接入（多模态/附件 OCR），可走云端 API；`config/config.yaml:46-47` 默认 `enable_multimodal: true`，但需配置 VLM 模型才生效。
- **GPU**：compose 本身**无 GPU 声明**（全仓 grep `nvidia`/`cuda` 未在 compose/Dockerfile 出现运行期要求）；GPU 只有在你自建 Ollama/VLM/Rerank 时才是外部诉求。`.env.example:413` 提到「默认 1 适合单机/GPU 受限」仅针对解析并发调优。

### 4.4 可选的外部 API Key

| 变量 | 用途 | 是否必需 | 来源 |
|------|------|:---:|------|
| `TAVILY_API_KEY` | Tavily 网页搜索 | 可选 | `.env.example:636-637` |
| `SEARXNG_*` | 自建搜索 | 可选 | `.env.example:616-635` |
| OIDC `*` | 企业 SSO 登录 | 可选（默认 false） | `.env.example:572-` |
| IM 渠道凭证 | 企微/飞书/Slack 等 | 可选 | `README_CN.md:162` |

---

## 5. 关键配置项与环境变量

### 5.1 部署层「必填/强烈建议显式设置」

| 变量 | 默认/示例 | 说明 | 来源 |
|------|----------|------|------|
| `DB_DRIVER` / `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `postgres` / `postgres` / `5432` / ... | 标注「⚠️ 必填」 | `.env.example:126-140` |
| `REDIS_ADDR` / `REDIS_PASSWORD` | `redis:6379` / `redis123!@#` | redis 模式下必填 | `.env.example:142-155` |
| `JWT_SECRET` | `weknora-jwt-secret` | 登录 Token 签名；留空启动时随机生成 | `.env.example:536-538` |
| `SYSTEM_AES_KEY` | 占位串 | **必须 32 字节**；加密 API Key 等落盘字段，丢失不可恢复（旧 `TENANT_AES_KEY` 已废弃） | `.env.example:539-544`；`docker-compose.yml:295-301` |
| `STREAM_MANAGER_TYPE` | `redis` | `memory` 则不依赖 Redis | `.env.example:145` |
| `STORAGE_TYPE` | `local` | 存储后端 | `.env.example:188` |
| `RETRIEVE_DRIVER` | `postgres` | 向量/检索引擎 | `.env.example:271` |
| `AUTO_MIGRATE` | `true` | 启动自动迁移 | `.env.example:74` |
| `WEKNORA_SANDBOX_DOCKER_ENABLED` | `false` | Docker 沙箱开关（默认关，因 docker.sock≈宿主 root） | `.env.example:600` |

### 5.2 其他值得注意的开关

- `DOCREADER_ADDR`（默认 `docreader:50051`）、`DOCREADER_TRANSPORT=grpc`（`.env.example:93-96`）。
- `MAX_FILE_SIZE_MB=50`，Go/Nginx/docreader/浏览器四层启动时各读一次，改后需同重启（`.env.example:195-196`）。
- `NEO4J_ENABLE`（GraphRAG 唯一开关，`.env.example:331`）。
- `WEKNORA_ASYNQ_*_CONCURRENCY` 多组 worker 池并发（`.env.example:169-184`）。
- 环境变量总数：README 称约 150 个（`README_CN.md:286`）。

---

## 6. 部署形态

### 6.1 Docker Compose（标准）

- `docker compose up -d` 只起默认 profile 的 5 个服务（`README_CN.md:241-255`）。
- 可选 profile（README 表格，`README_CN.md:245-251`）：`full` / `neo4j` / `minio` / `langfuse`。

**compose 文件里实际定义的 profile（比 README 多）**：

| 服务 | `profiles:` | 来源 |
|------|------------|------|
| `sandbox`、`mcp` | `full` | `docker-compose.yml:393-394,1021-1022` |
| `searxng-init`、`searxng` | `searxng`, `full` | `docker-compose.yml:565-567,598-600` |
| `minio` | `minio`, `full` | `docker-compose.yml:621-623` |
| `neo4j` | `neo4j`, `full` | `docker-compose.yml:642-644` |
| `qdrant` | `qdrant`, `full` | `docker-compose.yml:657-659` |
| `milvus` | `milvus` | `docker-compose.yml:686-687` |
| `weaviate` | `weaviate` | `docker-compose.yml:709-710` |
| `doris-fe`、`doris-be` | `doris` | `docker-compose.yml:744-745,764-765` |
| `dex` | `dex`, `full` | `docker-compose.yml:775-777` |
| `langfuse-*` | `langfuse`, `full` | `docker-compose.yml:828-830,952-954` |
| `odl-hybrid` | `odl-hybrid` | `docker-compose.yml:510-511` |
| `opensearch`（仅 dev） | `opensearch`, `full`, `opensearch-ui` | `docker-compose.dev.yml:149-154` |

### 6.2 源码开发模式

- `docker-compose.dev.yml` 只起基础设施（`postgres` + `redis`，其余带 profile），app/frontend 本地跑（`docker-compose.dev.yml:1-36`；`Makefile:331-350` `dev-start`/`dev-app`/`dev-frontend`）。
- 需要 Go 1.26、Node（前端）、Python 3.10（docreader，可容器化）等工具链。

### 6.3 Kubernetes（Helm）

- `helm/` 提供 chart，内置组件模板：`app`、`frontend`、`docreader`、`postgres`、`redis`、`neo4j`(可选)、`minio`(可选)、`qdrant`(可选)（`helm/templates/`；`helm/values.yaml`）。
- 默认资源限额示例：app `1 CPU / 1Gi`，docreader `500m / 512Mi`，postgres `500m / 512Mi`，redis `200m / 256Mi`（`helm/values.yaml:70-76,210-216,255-261,300-307`）。
- ⚠️ Helm 默认 postgres 镜像 tag 为 `v0.18.9-pg17`（`helm/values.yaml:252`），与 compose 的 `v0.22.2-pg17` **不一致**（见 §7）。

### 6.4 Lite（单二进制）

- 构建：`make build-lite`（`-tags sqlite_fts5`，先构建前端到 `web/`），`Makefile:265-288`；打包 `scripts/package-lite.sh`。
- 依赖裁剪：`DB_DRIVER=sqlite`（FTS5 + sqlite-vec）、`RETRIEVE_DRIVER=sqlite`、`STREAM_MANAGER_TYPE=memory`、`STORAGE_TYPE=local`、`NEO4J_ENABLE=false`（`.env.lite.example:14-44`）。
- 定位：单应用、零外部服务栈、默认仅本机访问；解析引擎仅内置 `simple` 类型（`docs/LITE.md`）。
- Go 依赖佐证：`asg017/sqlite-vec-go-bindings`、`gorm.io/driver/sqlite`（`go.mod`）。

### 6.5 GPU

- 运行期 compose/helm **无 GPU 要求**。GPU 仅当自建 Ollama / VLM / Rerank 等模型服务时由外部引入（全仓未见 compose 级 `deploy.resources.reservations.devices` 或 `runtime: nvidia`）。

---

## 7. 与文档不一致 / 存疑之处

| # | 现象 | 证据 | 判定 |
|---|------|------|------|
| 1 | README 的 profile 表只列 `full/neo4j/minio/langfuse`，但 compose 实际还定义了 `qdrant/milvus/weaviate/doris/searxng/dex/odl-hybrid/sandbox/mcp/opensearch(dev)` | `README_CN.md:245-251` vs `docker-compose.yml:386-1022`、`docker-compose.dev.yml:126-154` | **以 compose 为准**；README 表不完整 |
| 2 | Helm 默认 postgres tag `v0.18.9-pg17` ≠ compose `v0.22.2-pg17` | `helm/values.yaml:252` vs `docker-compose.yml:522` | 版本漂移；K8s 部署需自行对齐（尤其 ParadeDB 索引格式） |
| 3 | `docreader/README.md` 列了 `MINIO_ENDPOINT` / `MINIO_PUBLIC_ENDPOINT` / `MINERU_ENDPOINT` 等 docreader env，但当前 compose 的 `docreader` service 并未注入这些 | `docreader/README.md:9-16` vs `docker-compose.yml:416-489` | 该 README 疑似过期；以 compose 为准 |
| 4 | Lite 声称「零依赖/单应用」，但 `.env.lite.example` 仍保留 `DOCREADER_ADDR=127.0.0.1:50051` | `docs/LITE.md` vs `.env.lite.example:73-77` | 不矛盾但易误读：Lite 的 `simple` 引擎（md/txt/csv/json/图片）在 Go 内处理，docreader 仅用于复杂格式，非必需（`engines.go:96-118`） |
| 5 | README「环境要求」仅列 Docker + Git，未提「必须自备 LLM/Embedding 模型服务」 | `README_CN.md:210-213` | 文档弱化；从 `.env.example` D 段与前端模型配置看，模型是功能前提（见 §4.1） |
| 6 | 默认向量库是 ParadeDB（pgvector + pg_search），但 `RETRIEVE_DRIVER` 值仍写作 `postgres` | `docker-compose.yml:522`；`migrations/paradedb/00-init-db.sql:3-5` | 命名易误解：`postgres` 驱动实际要求带扩展的 ParadeDB 镜像 |
| 7 | 生产 compose 的 `postgres` / `redis` 未发布宿主机端口，dev compose 才发布 | `docker-compose.yml:521-548` vs `docker-compose.dev.yml:7-8,29-30` | 非缺陷，属设计差异（生产更收敛） |

---

## 8. 对 corporate_rag（本项目）的对照提示

> 仅列事实性差异，供选型参考，不构成建议。

| 维度 | WeKnora | corporate_rag |
|------|---------|---------------|
| 主数据库 | Postgres 默认，另支持 MySQL / SQLite | MySQL 8 |
| 向量库 | 默认复用 ParadeDB(pgvector+pg_search)；可换 9 种 | ChromaDB |
| 缓存/队列 | Redis（流缓冲 + Asynq），可切内存 | Redis 7（用途待核） |
| 文档解析 | 独立 Python gRPC 服务 `docreader`（LibreOffice/Playwright/OCR） | （本项目自有链路） |
| 对象存储 | local 默认；MinIO/云可选 | （待核） |
| 可观测 | Langfuse（唯一追踪后端），可自建 profile | Langfuse |
| 模型层 | LLM/Embedding/Rerank/VLM/ASR，UI 运行时配置，供应商 17+ | DashScope 为主 |
| 编排 | Compose（默认 5 容器 + 多 profile）/ Helm / 单二进制 Lite | Compose + Nginx |
| 语言 | Go 后端 + Python docreader + Python MCP | Python(FastAPI) |

---

## 9. 来源文件清单

以下文件为本文档全部结论的一手依据（相对 WeKnora 仓库根）：

**编排与镜像**
- `docker-compose.yml`（生产默认编排，1045 行）
- `docker-compose.dev.yml`（开发基础设施编排）
- `docker/Dockerfile.app`、`docker/Dockerfile.docreader`、`docker/Dockerfile.odl-hybrid`、`docker/Dockerfile.sandbox`
- `frontend/Dockerfile`、`mcp-server/Dockerfile`
- `helm/values.yaml`、`helm/templates/`（app/frontend/docreader/postgres/redis/neo4j/minio/qdrant/pvc/ingress/secrets）

**配置与环境变量**
- `.env.example`（约 780 行，含 A~J 分节）
- `.env.lite.example`
- `config/config.yaml`、`config/builtin_models.yaml.example`
- `migrations/paradedb/00-init-db.sql`、`migrations/`（`mysql`/`paradedb`/`sqlite`/`versioned`）

**依赖清单**
- `go.mod`（Go 1.26；gorm postgres/sqlite、mysql driver、redis、asynq、qdrant/milvus/weaviate/elasticsearch/opensearch/neo4j 客户端、sqlite-vec、ollama、mcp-go 等）
- `docreader/pyproject.toml`（Python 3.10.18；grpcio、markitdown、opendataloader-pdf、playwright、pypdfium2、textract 等）
- `mcp-server/requirements.txt`（mcp、starlette、uvicorn、requests）
- `frontend/package.json`（Vue 3.5 / Vite 7 / TDesign 等）

**文档与脚本**
- `README_CN.md`（架构、功能矩阵、快速开始、profile 表、服务地址）
- `docs/LITE.md`（Lite 与标准版差异）
- `docreader/README.md`（已标注过期）
- `Makefile`（build/build-lite/dev-start/docker-* 等）
- `scripts/start_all.sh`、`scripts/package-lite.sh`、`scripts/docker-entrypoint.sh`、`scripts/dev.sh`
- `internal/infrastructure/docparser/engines.go`（解析引擎与可用性判定）
- `rerank_server_demo.py`（自建 rerank 示例）
