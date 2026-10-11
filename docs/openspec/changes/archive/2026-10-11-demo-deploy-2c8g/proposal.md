## Why

项目此前只在开发机（WSL）运行、**从未发布过**。现在**已建成云效 CD 流水线并跑通首次对外 demo 部署**（目标机为阿里云 ECS；交付物 = ACR 镜像 + 部署制品包）。但"发布/部署流程"仍**没有归属文档**：

- `README.md` §部署指南 / `docs/agents/cookbook.md` §部署 描述的是**本地起栈**，不是发布流程
- `docker-compose.prod.yml` 从未在目标机验证，且与目标机实际使用的 `docker-compose.image.yml` 不同源
- 流水线各段的口径（构建步骤字段、变量与缓存、制品 tag 与镜像 tag 的一致性、主机部署四个槽位命令、安全组）散落在会话与临时脚本里，换个会话就丢

> 本 change 初稿假设"**不引入 CI/CD、镜像仓库**"（当时未建）。**该假设已被实际实现取代**：现为**一条云效流水线**（构建并推送镜像 → 打包部署制品 → 主机部署）。本 change 据此重写；目标不变 —— 让发布/部署步骤第一次有**唯一归属文档**。

## What Changes

- **新增归属文档 `docs/agents/deploy-runbook.md`**：单机发布流程的唯一步骤来源，七段组织 —— 目标形态 / 发布链路与流水线配置 / 首次上机 / 数据迁移 / 冒烟 / 日常更新 / 回滚与备份
- **固化已实测的口径**（不再散落会话）：
  - 运行形态 = `docker-compose.image.yml`（镜像全部来自 ACR，**目标机不构建**）；6 个基镜像在公开库 `deploy_base`（免登录），app 在私有库 `deploy_store_local`
  - **发布链路**：流水线三段 = 构建并推送（`dockerTag=${BUILD_NUMBER}`）→ 打包制品（`pack-deploy-artifact.sh` 用**同一个** `BUILD_NUMBER` 注入 compose 的 `image:`）→ **部署**（流水线最后一步：**匹配到发布单就直接发布**；**匹配不到则挂起，等人工在部署页面点「创建发布单」**，创建成功后继续、此后无需人工确认）⇒ **制品 tag 与镜像 tag 天然一致**
  - **变量与缓存**：`PIP_REPO_USER` / `PIP_REPO_PASS` 是**制品仓库（packages / PyPI 代理仓 `repo-okxha`）认证**，配在「变量和缓存」，`PIP_REPO_PASS` 勾私密模式；**不是 ACR 凭据**。构建步骤 `options` 用**不带值**的 `--build-arg PIP_REPO_USER`（从环境变量取），**不写 `${...}`**（该字段不展开，会变字面量）
  - 资源档位沿用 dev 档位（image compose 合计 ≈2.28 GB）；实测整栈 ≈715 MiB
  - 对外只暴露 nginx:80；app 8000 与其余端口绑回环 / 由安全组封闭
  - 迁移在**容器内**执行（`alembic/` + `alembic.ini` 已打进镜像），由 `deploy.sh` 第 7 步自动完成
- **更正三项"前置必改项"为已完成**（nginx `client_max_body_size`、MinIO 两对键同源、alembic 入镜像）—— 初稿把它们列为"未改、照文档执行会撞缺口"
- **登记未决项**进 `docs/agents/requirements_pool.md`：HTTPS 选型、Langfuse 去留、`docker-compose.prod.yml` 与目标形态的关系、备份口径、托管化
- **`CLAUDE.md` 文档组织表登记该归属文档**（一事一档要求）
- **本 change 仍只落文档**：不改任何 compose / nginx / `.env` / 流水线配置

## Capabilities

### New Capabilities

- `deployment-runbook`: 单机对外 demo 的发布/部署流程文档——必须覆盖的环节（目标形态、发布链路与流水线配置、上机前置、迁移执行位置、冒烟清单、日常更新、回滚、备份、端口与密钥约束），以及文档的唯一归属性

### Modified Capabilities

（无 —— 本 change 不改任何既有能力的规格级行为）

## Impact

- **新增**：`docs/agents/deploy-runbook.md`（归属文档）
- **修改**：`CLAUDE.md`（文档组织表加一行）、`docs/agents/requirements_pool.md`（登记未决项）
- **不改**：任何代码、`docker-compose*.yml`、`deploy/nginx/`、`.env`、云效流水线配置
- **无影响**：API 契约、数据库结构、测试断言、前端取值
