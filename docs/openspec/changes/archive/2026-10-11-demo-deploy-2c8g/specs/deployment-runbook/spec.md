## ADDED Requirements

### Requirement: 部署流程的唯一步骤来源

仓库 SHALL 只在 `docs/agents/deploy-runbook.md` 一处承载发布/部署的**操作步骤**；其余文档（`README.md`、`docs/agents/cookbook.md` 等）SHALL 只链接该文档，不得复制其步骤正文。

#### Scenario: 查阅发布步骤

- **WHEN** 需要执行或复核一次发布
- **THEN** 操作步骤只在 `docs/agents/deploy-runbook.md` 中，其他文档以指针形式引用它

### Requirement: 运行形态与资源档位

runbook SHALL 规定运行形态为 `docker-compose.image.yml`（镜像全部来自 ACR，**目标机不构建**），SHALL 给出各服务 `mem_limit`（沿用 dev 档位，合计 ≈2.28 GB）与实测整栈占用（≈715 MiB），并 SHALL 明确 `docker-compose.prod.yml` 的限额（合计 14.25 GB）**不适用**于 2C8G 目标机。runbook SHALL 写明镜像来源分两库：6 个基镜像在公开库 `deploy_base`（免登录），app 在私有库 `deploy_store_local`（需 `docker login`）。

#### Scenario: 选择资源档位

- **WHEN** 在 2C8G 机器上部署
- **THEN** 采用 `docker-compose.image.yml` 的 dev 档位，且 runbook 写明 prod 档位会因超出物理内存而触发 OOM

#### Scenario: 确认镜像来源与登录要求

- **WHEN** 读者要确认镜像从哪来、要不要登录
- **THEN** runbook 写明基镜像免登录、app 镜像需先 `docker login` 私有库

### Requirement: 发布链路与流水线配置有唯一归属

runbook SHALL 描述发布链路：**流水线**三段（构建并推送镜像 → 打包部署制品 → **部署**），其中**部署步骤匹配到发布单则直接发布、匹配不到则挂起等人工在部署页面「创建发布单」**（创建成功后继续，且此后再运行无需人工确认）；并 SHALL 固化：① `BUILD_NUMBER` 同源 ⇒ **制品里的镜像 tag = 实际推送的镜像 tag**；② 「变量和缓存」中 `PIP_REPO_USER` / `PIP_REPO_PASS` 是**制品仓库（packages / PyPI 代理仓）认证**、`PIP_REPO_PASS` 为私密变量，**不是 ACR 凭据**；③ 构建步骤字段口径（`dockerfilePath` / `contextPath` / `dockerTag` / `options` 使用**不带值**的 `--build-arg`）；④ 主机部署**四槽位命令**与其作用（**配在部署页面**）；⑤ 原则「**破坏性动作必须排在启动之后**」。

#### Scenario: 复现一次发布

- **WHEN** 读者按 runbook 复现一次发布
- **THEN** 能照抄各段配置（构建字段、变量、四槽位命令）而无须回去翻会话记录

#### Scenario: 区分两套凭据

- **WHEN** 读者配置凭据
- **THEN** runbook 明确 `PIP_REPO_*` 服务于制品仓库、ACR 凭据另行提供（构建侧 serviceConnection / 目标机 `docker login`），二者不可混用

### Requirement: 内部服务端口不对外且密钥不依赖弱默认

runbook SHALL 规定仅入口端口（nginx:80）对外开放、安全组只放行 22 与入口端口；redis / postgres / app / minio 的宿主端口 SHALL 只绑回环或被安全组封闭；SHALL 说明 MinIO 两对键（服务端 `MINIO_ROOT_*` 与应用侧 `MINIO_ACCESS_KEY/SECRET_KEY`）在 `docker-compose.image.yml` 中**已同源**，不得改回需要人工保持相等的两对；密钥不得依赖 compose 的 `${X:-默认值}` 兜底。

#### Scenario: 核对对外暴露面

- **WHEN** 部署完成后检查对外可达端口
- **THEN** 只有入口端口可达，redis / app / minio 等宿主端口不可从外部访问

#### Scenario: 配置对象存储凭据

- **WHEN** 设置 MinIO 凭据
- **THEN** 只设 `.env` 的 `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` 一处即可（compose 已同源引用），runbook 指出改回两对会导致静默不一致

### Requirement: 数据迁移在容器内执行且先于冒烟

runbook SHALL 规定数据库迁移（`alembic upgrade head`）在 **app 容器内**执行（`alembic/` 与 `alembic.ini` 已打进镜像），由 `deploy.sh` 的迁移步骤自动完成，并 SHALL 规定迁移**先于冒烟**。

#### Scenario: 首次上机的迁移

- **WHEN** 首次在目标机拉起服务
- **THEN** 迁移由 `deploy.sh` 自动在冒烟之前完成，runbook 给出可复制的命令与预期输出

#### Scenario: 手工重跑迁移

- **WHEN** 需要单独重跑迁移
- **THEN** runbook 给出容器内执行的命令（`docker compose exec app alembic upgrade head`），并说明该步幂等

### Requirement: 发布制品源于已提交 commit 且工作区干净

runbook SHALL 规定发布制品为**已提交 commit** 经流水线打出的部署包（tgz），并要求发布前置检查工作区干净（`git status` 为空）。

#### Scenario: 发布前置检查

- **WHEN** 开始一次发布
- **THEN** 先确认工作区干净，未提交的改动不得进入发布

### Requirement: 冒烟清单可执行且作为发布成功判据

runbook SHALL 提供最小冒烟清单（健康检查 → 登录 → 上传一份文档 → 等待就绪 → 提问并看到引用），并 SHALL 规定缺任一步不得判定发布成功。

#### Scenario: 判定发布成功

- **WHEN** 部署动作执行完毕
- **THEN** 逐项跑通冒烟清单才算发布成功，任一步失败即视为发布未完成

### Requirement: 回滚与备份口径

runbook SHALL 规定代码回滚方式（回指旧的镜像 tag / 重跑旧构建），SHALL 规定数据侧备份要求，并 SHALL 指出 MinIO 中的原始上传文件是**唯一不可再生的源头**。

#### Scenario: 执行回滚

- **WHEN** 发布后需要回滚
- **THEN** 按 runbook 的回滚步骤执行（镜像 tag = 构建号，ACR 中可回指），并明确数据侧是否需要依赖备份

#### Scenario: 识别备份缺口

- **WHEN** 评审部署就绪度
- **THEN** runbook 已写明哪些数据必须备份、以及不备份的后果

### Requirement: 未决项显式登记而非以已决定口吻出现

本变更未决的项（HTTPS 选型、Langfuse 去留、`docker-compose.prod.yml` 与目标形态的关系、备份口径、托管化）SHALL 登记在 `docs/agents/requirements_pool.md`；runbook SHALL NOT 以"已决定"的口吻描述这些项。

#### Scenario: 阅读 runbook 时区分已定与未定

- **WHEN** 读者查看 HTTPS / Langfuse / 备份之类段落
- **THEN** 能明确看出哪些是**当前采用口径**、哪些仍是**待定项**
