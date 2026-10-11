> 本变更**只落文档**：不改任何 `docker-compose*.yml` / `deploy/nginx/` / `.env` / 流水线配置。配置改动另开 change。

## 1. 归属文档骨架

- [x] 1.1 新建 `docs/agents/deploy-runbook.md`，按七段建骨架：目标形态 / 发布链路与流水线配置 / 首次上机 / 数据迁移 / 冒烟 / 日常更新 / 回滚与备份
- [x] 1.2 文件头写明归属与不复制原则：本文件是**发布/部署步骤的唯一归属**，`README.md`、`cookbook.md` 等只链接不复述（对应 spec「部署流程的唯一步骤来源」）
- [x] 1.3 「目标形态」段：单机 ECS 2C8G 对外 demo；运行形态用 `docker-compose.image.yml`（镜像全部来自 ACR，**目标机不构建**）；服务清单（redis / postgres / minio / langfuse-web / nginx / app）；`deploy_base` 公开库免登录 + `deploy_store_local` 私有库；mem_limit 沿用 dev 档位（合计 ≈2.28 GB），实测整栈 ≈715 MiB；写明 `docker-compose.prod.yml`（14.25 GB）**不适用**本目标（对应 spec「资源档位」）
- [x] 1.4 「端口与密钥」段：仅 nginx:80 对外（安全组只放行 22 + 80）；app 8000 / redis / postgres / minio 绑回环或被安全组封闭；MinIO 两对键**已同源**（image compose 直接用 `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`）；密钥不得依赖 compose 的 `${X:-默认值}`（对应 spec「内部端口不对外且密钥不依赖弱默认」）

## 2. 发布链路与流水线配置（本次新增的段）

- [x] 2.1 发布链路：流水线三段（Git → 构建并推送镜像(ACR 私有库) → 打包部署制品(tgz) → **部署**），其中**部署步骤：匹配到发布单 → 直接发布；匹配不到 → 挂起等人工在部署页「创建发布单」**（创建后继续、此后无需人工确认）；写明 `BUILD_NUMBER` 同源 ⇒ **制品里的 tag = 镜像 tag**，无需人工同步
- [x] 2.2 「变量与缓存」段：`PIP_REPO_USER` / `PIP_REPO_PASS` = **制品仓库（packages / PyPI 代理仓 `repo-okxha`）认证**，配在「变量和缓存」，`PIP_REPO_PASS` 勾**私密模式**，**不是 ACR 凭据**；ACR 凭据走构建侧 serviceConnection / 目标机 `docker login`（`ACR_USER`/`ACR_PASSWORD`）；`LLM_MODEL` 等注入的是**目标机环境变量**，而 app 容器只读 `env_file: .env` ⇒ **改模型要改服务器 `.env`**，流水线变量对 app 无效
- [x] 2.3 构建步骤字段口径：`dockerfilePath: Dockerfile`、`contextPath` 留空、`dockerTag: ${BUILD_NUMBER}`；`options` 用**不带值**的 `--build-arg PIP_REPO_USER --build-arg PIP_REPO_PASS`（**不写 `${...}`** —— 该字段不展开）
- [x] 2.4 主机部署四槽位命令（`bash scripts/deploy/deploy.sh {preflight,start,health_check,clean}`）各自作用（**配在部署页面**，由流水线最后一步的「部署」触发）；写明原则「**破坏性动作必须排在启动之后**」（停止槽位只做 preflight，不真停）
- [x] 2.5 已踩坑清单：① `{{ }}` 会被云效当占位符渲染（禁用）；② `options` 字段不展开 `${...}`；③ Alinux 4 自带 `docker` 与 `docker-ce` 冲突（补 `docker-compose-plugin` 即通）；④ `docker compose restart` 不吃 `.env`，改配置要 `--force-recreate`

## 3. 首次上机

- [x] 3.1 「目标机前置」：装 docker + compose 插件、ACR 登录、放 `.env`（`chmod 600`）、安全组只开 22 + 80、加 2–4 GB swap、系统盘 ≥60 GB
- [x] 3.2 「拉起服务」：给出可复制命令（`bash scripts/deploy/deploy.sh`，等价 `start`），并说明前置检查覆盖哪些路径（`.env` 非空、`deploy/nginx/*`、`deploy/postgres/init/`）
- [x] 3.3 「数据迁移」段：迁移在**容器内**执行（镜像已含 `alembic/` + `alembic.ini`），由 `deploy.sh` 第 7 步自动完成；顺序**必须在冒烟之前**（对应 spec「数据迁移有明确执行位置与顺序」）

## 4. 冒烟与日常更新

- [x] 4.1 「冒烟清单」：`/api/health` → 登录 → 上传一份文档 → 等待 `ready` → 提问并看到引用；写明缺任一步不得判定发布成功（对应 spec「冒烟清单可执行且作为发布成功判据」）
- [x] 4.2 「日常更新」：正常发布 = 跑一次流水线；补充 `deploy.sh` 各模式的人工用途（`health_check` / `stop` / `clean`）与各自边界
- [x] 4.3 「发布前置」：制品源于**已提交的 commit**；发布前 `git status` 必须为空，未提交改动不得进入发布（对应 spec「发布制品为已提交的 commit 且工作区干净」）

## 5. 回滚与备份

- [x] 5.1 「代码回滚」：重新跑一次旧构建（镜像 tag = 构建号，ACR 里可回指），或临时改 compose 的 `image:` 指向旧 tag 再 `up -d`
- [x] 5.2 「备份」：需要备份的数据与位置；写明 **MinIO 中的原始上传文件是唯一不可再生的源头**（对应 spec「回滚与备份口径」）；频率与保留期属未决项，标注「当前口径 / 待定」

## 6. 登记与同步

- [x] 6.1 `CLAUDE.md` 文档组织表新增一行：`docs/agents/deploy-runbook.md` —— 归属内容「发布/部署流程的唯一步骤来源」，何时查阅「执行发布、排查部署问题前」
- [x] 6.2 `docs/agents/requirements_pool.md` 新增登记项：① HTTPS 选型 ② Langfuse 去留 ③ `docker-compose.prod.yml` 与目标形态的关系 ④ 备份口径（频率/保留期/是否上 OSS）⑤ 托管化（RDS/Redis/OSS/SLB）
- [x] 6.3 复核 runbook 全文措辞：待定项用「当前口径 / 待定」表述，不得写成已决定（对应 spec「未决项显式登记而非以已决定口吻出现」）

## 7. 闸门与验收

- [x] 7.1 跑 `python -m src.cli.check_docs`，0 error（doc anti-rot 闸门）
- [x] 7.2 跑 `openspec validate demo-deploy-2c8g --strict` 通过
- [x] 7.3 可追溯性核对：spec 的每条 requirement 各自在 runbook 中有落点，无悬空、无多余
- [x] 7.4 按 `CLAUDE.md`「验证」清单自检：一事一档（新归属文档已登记进表）、无复制其他文档正文、无新增术语遗漏 glossary
