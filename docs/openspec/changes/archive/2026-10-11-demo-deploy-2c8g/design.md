## Context

项目 2026-08 起只在开发机（WSL / ext4）运行；**2026-10 首次完成对外 demo 部署**（阿里云 ECS 单机，云效流水线交付）。仓库里叫"部署"的内容有两类，都不是发布流程：

| 位置 | 实际是什么 |
|---|---|
| `README.md` §部署指南 | 本地启动说明（`docker compose up -d --build`） |
| `docs/agents/cookbook.md` §部署 | 本地容器操作（改 `.py` 后 restart、ALTER TABLE） |
| `docker-compose.prod.yml` | 一机全栈配置，从未在目标机验证过，且**不是目标机实际使用的那个** |

**目标机实际使用的运行形态 = `docker-compose.image.yml`**（全镜像来自 ACR，目标机不构建）。已实测的环境事实：

- 目标机：阿里云 **2C8G 单机**，对外 demo；安全组只放行 22 与 80
- `docker-compose.image.yml` 的 `mem_limit`：redis 128m / postgres 1g / minio 256m / langfuse-web 256m / nginx 128m / app 512m（**合计 ≈2.28 GB**，即 dev 档位）
- 实测整栈占用 **≈715 MiB**（app 254 / langfuse-web 212 / minio 144 / postgres 83 / nginx 12 / redis 10），CPU 近 0（推理在远端）
- 镜像分两库：**公开** `deploy_base`（redis / pgvector / minio / langfuse / nginx / python 基镜像，免登录）+ **私有** `deploy_store_local`（app，含 `src/ scripts/ skills/ agents/ alembic/`，故不进公开库）
- **发布链路（一条云效流水线）**：Git → 构建并推送 app 镜像（`dockerTag=${BUILD_NUMBER}`）→ 打包部署制品（`pack-deploy-artifact.sh` 用**同一个** `BUILD_NUMBER` 注入 compose 的 `image:`）→ **部署**（流水线最后一步：**匹配到发布单就直接发布**；**匹配不到则挂起，等人工在部署页面点「创建发布单」**，创建成功后继续、此后无需人工确认）
- 目标机 `/opt/www/corporate_rag` 上存在：`.env`（人工放置、600）、`docker-compose.image.yml`、`deploy/**`、`scripts/deploy/deploy.sh`、`data/ragas/`
- 迁移在 **app 容器内**执行（`alembic/` 与 `alembic.ini` 已 `COPY` 进镜像）

**初稿的三条断言已被实现推翻**（本 change 据此更新）：

| 初稿 | 现状（证据） |
|---|---|
| nginx 无 `client_max_body_size`（必 413） | **已有** `client_max_body_size 12m`（`deploy/nginx/nginx.conf`） |
| MinIO 两对键需人工保持相等 | **已同源**（`docker-compose.image.yml` 用 `${MINIO_ACCESS_KEY:-…}` 供 `MINIO_ROOT_*`） |
| 镜像内无 `alembic/`，容器内跑不了迁移 | **已 COPY 进镜像**（`Dockerfile`），`deploy.sh` 迁移步骤实测跑过 0001→0002 |
| Non-Goal：不引入 CI/CD、镜像仓库 | **已建成**：构建流水线 → ACR → 主机部署 |

## Goals / Non-Goals

**Goals：**

- 让"发布/部署流程"第一次有**唯一归属文档**，七段齐全：目标形态 / 发布链路与流水线配置 / 首次上机 / 数据迁移 / 冒烟 / 日常更新 / 回滚与备份
- 把已在实现与实测中确认的口径**固化进文档**（运行形态、镜像来源、流水线各段配置、变量、端口、迁移位置、制品形态、回滚），不再依赖会话记忆
- 明确区分"当前采用口径"与"仍待定项"，待定项登记进需求池

**Non-Goals：**

- **不改任何 compose / nginx / `.env` / 流水线配置**（本 change 只落文档；配置改动另开 change，便于独立评审）
- 不引入监控告警、托管化（RDS/Redis/OSS/SLB）
- 不为 2 机 + SLB 的横向扩展做设计（与"流式状态在进程内、单 worker"的既定形态冲突，属独立问题）
- 不动 `docker-compose.prod.yml`

## Decisions

### D1 目标形态 = 单机 2C8G 跑 `docker-compose.image.yml`，目标机不构建

**理由**：镜像全部来自 ACR（基镜像公开、app 私有），目标机只 `pull`；把最贵的一步（构建）从 2C8G 上移走。`docker-compose.yml`（dev）与 `docker-compose.prod.yml` 都不参与目标机运行 —— 前者面向开发机，后者从未验证且档位（14.25 GB）会 OOM。

**替代方案**：① 直接把 dev compose 拷到目标机 —— 会把 2C8G 变成构建机，且依赖 Docker Hub；② 对齐 prod compose —— 仍需重验，收益小。

### D2 本 change 只落文档

**理由**：文档先行可让配置改动被文档反向约束（先定口径，再改配置）。配置项（prod compose 关系、HTTPS、备份口径）在需求池登记，另开 change。

### D3 发布链路 = 流水线最后一步「部署」按发布单匹配决定是否挂起；`BUILD_NUMBER` 同源保证 tag 一致

**理由**：构建推镜像用 `dockerTag=${BUILD_NUMBER}`，打包制品用 `pack-deploy-artifact.sh` 的同一个 `BUILD_NUMBER` 注入 compose 的 `image:`；同一流水线 ⇒ 两者必然配套，**不需要人工同步 tag**。流水线的**最后一步是「部署」**：**匹配到发布单就直接发布**；**匹配不到则挂起，等人工在部署页面点「创建发布单」**（创建成功后继续，且**此后再运行无需人工确认**）—— 四槽位脚本配在**部署页面**而非流水线里。目标机的 ACR 登录（私有库）在部署执行时完成（ACR 个人版不支持平台代拉）。

### D4 变量与缓存：`PIP_REPO_*` 服务制品仓库，ACR 凭据另配

**理由**：`PIP_REPO_USER` / `PIP_REPO_PASS` 是**云效制品仓库（packages / PyPI 代理仓）认证**，供构建期的 `pip install` 拉依赖；它**登不了 ACR**（不同服务、不同认证）。ACR 凭据由构建侧 serviceConnection 与目标机 `docker login` 分别承担。

**已知坑（写进 runbook）**：构建步骤的 `options` 字段**不展开 ` ${...}`**，故必须用**不带值**的 `--build-arg PIP_REPO_USER`（从环境变量取），否则字面量会被当作 pip 用户名，表现为"索引里什么都查不到"。

### D5 迁移在容器内执行

**理由**：`alembic/` 与 `alembic.ini` 已打进 app 镜像，`deploy.sh` 的迁移步骤即 `docker compose exec app alembic upgrade head`，迁移幂等、自动重试。顺序**先于冒烟**（建表 + 加列）。

### D6 制品形态 = 流水线打出的部署包（tgz）+ ACR 镜像

**理由**：制品由 `pack-deploy-artifact.sh` 生成（白名单：`docker-compose.image.yml` + `scripts/deploy/deploy.sh` + `deploy/**`），经「主机部署」下发解压到 `/opt/www/corporate_rag`。`src/` 等代码随镜像走，宿主不再需要。

**代价**：目标机不再是"代码工作副本"，回滚靠镜像 tag 而非 `git checkout`；`.env` 必须人工放置（制品不含密钥）。

### D7 文档归属 = 新建 `docs/agents/deploy-runbook.md`，并在 `CLAUDE.md` 文档组织表登记

**理由**：`CLAUDE.md` 的「一事一档」要求每条事实只有一个归属文档、新归属文档必须同步登记进表。

### D8 冒烟清单（5 步）作为发布成功判据

健康检查 `/api/health` → 登录 → 上传一份文档 → 等待 `ready` → 提问并看到引用。缺任一步不算发布成功。**理由**：现有验证资产（pytest / ruff / pyright / doc 闸门）覆盖开发侧，交付侧一条都没有。

### D9 主机部署四槽位：停止槽位只做 preflight，不真停

**理由**：云效按「停止 → 启动 → 健康检查 → 清理」顺序调用；若"停止"槽位真的停服，则后续任一步失败（如制品损坏）会把服务留在**下线**状态。故停止槽位只调 `deploy.sh preflight`（校验制品、不停服），停旧起新交给启动阶段的 `compose up -d`。**原则：破坏性动作必须排在启动之后。**

## Risks / Trade-offs

- **[runbook 与流水线配置会各自漂移]** → runbook 明确"流水线配置以本文档为口径来源"；改流水线字段须同 PR 改 runbook。
- **[`docker-compose.prod.yml` 长期"不可用且未标注"且与目标形态无关]** → 在需求池登记其与目标形态的关系为待定项；runbook 说明它不参与本目标形态。
- **[2C8G 上构建镜像是三峰同时（CPU/磁盘/内存）]** → 但构建**已移到流水线构建机**，目标机不再构建 —— 风险已消除；runbook 仍要求 swap ≥2 GB、盘 ≥60 GB 以防本地演练。
- **[制品含 `.env` 的缺失]** → `.env` 人工放置，制品硬禁 `.env*`；runbook 单列"首机第一步放 `.env`"。
- **[单机是单点：PG 与 MinIO 同机]** → 接受（demo 口径）；用备份缓解，并写明 MinIO 原始文件是**唯一不可再生源**。
- **[新流水线易漏配变量]** → runbook 的「变量与缓存」段把 `PIP_REPO_USER/PASS` 列为**必备**，并给出"构建失败时先核对变量"的排查顺序。

## Migration Plan

1. 本 change 落文档（`deploy-runbook.md` + `CLAUDE.md` 登记 + 需求池登记）
2. 后续按 runbook 执行发布（**待**：HTTPS / 备份口径定稿）
3. **回滚**：本 change 是纯文档，回滚 = `git revert` 对应提交；无数据面与契约面回滚

## Open Questions

1. **HTTPS**：纯 HTTP（当前口径）／nginx 443 + 证书（自签 or Let's Encrypt）／SLB 终止 TLS？
2. **Langfuse**：当前口径 = **保留在栈内**（`docker-compose.image.yml:125`，绑回环不对外）；是否长期保留、是否要对外看？
3. **`docker-compose.prod.yml`**：废弃、删除，还是与目标形态对齐？
4. **备份口径**：频率、保留期、存放位置（是否上 OSS）？
5. **首次语料导入**：demo 展示哪批文档、由谁导入、走界面上传还是灌库？
