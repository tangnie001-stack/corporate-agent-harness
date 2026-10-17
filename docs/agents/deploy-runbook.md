# 部署 Runbook（单机对外 demo）

> **归属**：本文件是**发布 / 部署步骤的唯一归属**。`README.md`、`docs/agents/cookbook.md`、各 compose 文件注释等一律**只链接本文件**，不复述步骤正文。
>
> 适用目标：阿里云 ECS **单机（2C8G）对外 demo**。运行形态见 §1。
> 相关：发布链路与流水线配置见 §2；首次上机 §3；迁移 §4；冒烟 §5；日常更新 §6；回滚与备份 §7；仍未决定的口径见文末「待定项」。

---

## 1. 目标形态

- **运行形态**：目标机只运行 `docker-compose.image.yml` —— **全部镜像来自 ACR，目标机不构建**。
- **与 dev / prod compose 的关系**：
  - `docker-compose.yml`（dev）面向开发机，**不在目标机使用**；
  - `docker-compose.prod.yml` 从未在目标机验证、且档位会 OOM，**不在本目标形态内**（其去留见文末待定项）。

### 1.1 服务与镜像来源

| 服务 | 镜像（ACR） | 库 |
|---|---|---|
| redis | `deploy_base:redis-7.4.9-alpine` | 公开 |
| postgres（pgvector） | `deploy_base:pgvector-pg15.19-0.8.6` | 公开 |
| minio | `deploy_base:minio-2026-09-21` | 公开 |
| langfuse-web | `deploy_base:langfuse-2.95.11` | 公开 |
| nginx | `deploy_base:nginx-1.31.6-alpine` | 公开 |
| app | `deploy_store_local:<构建号>` | **私有** |

- **公开库 `deploy_base` 免登录**；**私有库 `deploy_store_local` 需先 `docker login`**（app 镜像内含 `src/ scripts/ skills/ agents/ alembic/`，故不进公开库）。
- tag 一律带 patch 号（升级基镜像要改引用、走评审，避免"同 tag 覆盖"静默换基）。

### 1.2 资源档位

沿用 **dev 档位**（`docker-compose.image.yml` 的 `mem_limit` 合计 **≈2.28 GB**：redis 128m / postgres 1g / minio 256m / langfuse-web 256m / nginx 128m / app 512m）。

实测整栈占用 **≈715 MiB**（app 254 / langfuse-web 212 / minio 144 / postgres 83 / nginx 12 / redis 10）→ **2C8G 有富余**。

> ⚠️ `docker-compose.prod.yml` 的档位合计 **14.25 GB**，在 2C8G 上**必被 OOM**，不要用它。
> 构建镜像已移到流水线构建机，目标机不再承担构建的三峰压力。

### 1.3 端口与安全组

- **对外开 nginx 的 80 与 Langfuse 的 3000**；安全组**放行 22、80、3000**。
- 其余端口一律绑回环或由安全组封闭：app `127.0.0.1:8000`、redis / postgres / minio 均不对外。
- **langfuse-web `3000:3000` 对外**（2026-10-09 由 `127.0.0.1:3000:3000` 改为对外，改的是 `docker-compose.image.yml`）。**对外门牌号 `NEXTAUTH_URL` 已在部署档里硬编码**（`"http://8.133.218.201:3000"`）—— NextAuth 用它拼绝对 URL（跳转/邮件链接/SSO 回调），填 `localhost` 会让这些链接指向用户自己的机器。**硬编码而非 `.env`**：该键是"生产独有"的值，走 `.env` 会被"从开发机拷 `.env` 上机"覆盖成 localhost（2026-10-10 实测踩中）；换域名/换机时改这一行并重新发布。**自助注册同样已硬编码关闭**（`AUTH_DISABLE_SIGNUP: "true"`）。这两个键都**不写进 `.env`、也不进 `deploy.sh` 第 3 步的必填清单**（避免"拷 `.env` 带错误值/缺键 ⇒ 静默错或部署 fail"）。
- 冒烟与健康检查走 `http://127.0.0.1/api/health`（经 nginx:80 → app:8000），与对外路径一致。

### 1.4 密钥与挂载路径

- 密钥**只放** `/opt/www/corporate_rag/.env`；**制品与流水线都不携带 `.env`**（镜像、制品、云效变量都不会把它送上机）。
- **`.env` 当前由人工从开发机拷到目标机**（尚无自动下发），拷完收紧权限：
  ```bash
  scp .env root@<ECS-IP>:/opt/www/corporate_rag/.env
  ssh root@<ECS-IP> 'chmod 600 /opt/www/corporate_rag/.env'
  ```
  ⇒ **本地新增/修改的键不会自动上机**：凡涉及 `.env` 的改动（如新增 `WECOM_BOTS`），都要先手工同步到目标机、再重跑部署（或 `deploy.sh start`），否则容器读不到新键、对应功能静默不生效。
  - **超时/降级那一组键全部可选、有内置默认** —— `HTTP_TIMEOUT_{CONNECT,WRITE,POOL}`、`MAIN_TIMEOUT_READ`、`CLASSIFY_TIMEOUT_READ`、`SUMMARY_TIMEOUT_READ`、`EMBEDDING_TIMEOUT_READ_{QUERY,INGEST}`、`RERANK_TIMEOUT_{READ,OUTER}`、`RERANK_MAX_ATTEMPTS`、`MAIN_IDLE_{FIRST,FIRST_THINKING,GAP,GAP_THINKING}_S`、`MAIN_IDLE_POLL_INTERVAL_S`（change `timeout-and-degradation-hardening`；取值依据见其 design D11）。**不设即用默认**，故首次上机**不需要**动 `.env`；只有要按目标机环境调值时，才按上面那条规则手工同步并重跑部署。⚠ `RERANK_*` 三个键之间有**嵌套约束**（内层 × attempts 必须 < 外层），配错会在**启动期** fail-fast 报错。
- 企微长连接的多机器人配置写在 `WECOM_BOTS`，值是**一行 JSON**（`[{"key":"dev","bot_id":"<BotID>","secret":"<Secret>"}, {"key":"support","bot_id":"<BotID>","secret":"<Secret>"}]`）：**必须单行** —— 多行会导致 JSON 解析失败、通道启动报错（`WECOM_BOT_MODE=long_connection` 且 `WECOM_BOTS` 为空同样启动报错）。
- **企微开关归属（`WECOM_BOT_ENABLED`）**：本地 `.env` **一律保持 `false`**（开发机 uvicorn 与 dev compose 都不连，避免抢占生产机器人的连接、两边互踢）；**生产侧的取值由 `docker-compose.image.yml` 的 app `environment:` 决定**（覆盖 `env_file`，已实测）—— **关停 / 恢复线上机器人就是改这一行再重新发布**（改目标机 `.env` 无效）。⇒ 同一份 `.env` 通用；生产 `.env` 只需 `WECOM_BOT_MODE=long_connection` + `WECOM_BOTS`，**不必**写 `WECOM_BOT_ENABLED`。
  - ⚠️ **顺序陷阱**：若先让含该覆盖的新 compose 上机、而生产 `.env` 尚无 `WECOM_BOTS` ⇒ `ENABLED=true` + 空注册表 ⇒ **应用启动 fail-fast**（部署 `health_check` 失败）。**先同步 `.env`，再让新 compose 上机**。
- 不得依赖 compose 的 `${X:-默认值}` 兜底 —— `deploy.sh` 会按 compose 插值变量清单校验非空，缺键即失败。
- MinIO 两对键**已同源**：`docker-compose.image.yml` 直接用 `.env` 的 `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` 供 `MINIO_ROOT_USER/PASSWORD`。**不要**改回需要人工保持相等的两对。
- 目标机必须存在的路径（缺任一条对应功能失效）：`.env`、`deploy/nginx/nginx.conf`、`deploy/nginx/html/`、`deploy/postgres/init/`、`data/deepeval/`。

---

## 2. 发布链路与流水线配置（云效）

### 2.0 发布全流程（端到端）

**默认发布分支：`dev-wsl`。** 一次发布按下面顺序走（1–4 在开发机/目标机，其余在云效网页）：

| # | 步骤 | 在哪 | 入口 / 命令 |
|---|---|---|---|
| 1 | **同步 `.env`**（**仅本次有 `.env` 变更时**） | 开发机 → 目标机 | `scp .env root@<ECS-IP>:/opt/www/corporate_rag/.env` + `chmod 600`（见 §1.4）。**必须排在发布之前**（新制品不含 `.env`；缺键会让部署 `fail`）。**无变更 ⇒ 跳过** |
| 2 | **登录 ACR 镜像凭证**（**仅首次上机**，之后一律跳过） | 目标机 | `docker login --username=hi31383397@aliyun.com crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com`（密码从**团队渠道**取，**不入库**）。登录成功后凭据落 `/root/.docker/config.json`，**后续发布不再需要密码**（`deploy.sh` 先 `docker manifest inspect`，过了就跳过登录）。仅当需要在**无 TTY** 环境自动登录时，才以**环境变量** `ACR_USER`/`ACR_PASSWORD` 提供（`deploy.sh` **不读 `.env`**，须由部署槽位注入） |
| 3 | **前置检查**：工作区干净 + 本地 `dev-wsl` 已推送、未落后 | 开发机 | `git fetch && git status -sb`（**说"开始部署"时由助手代查**）|
| 4 | **推代码** | 开发机 | `git push origin dev-wsl` |
| 5 | **GitHub → Codeup 同步代码**（当前**手动**）| 云效网页 | `https://deploytest-cn-shanghai.devops.aliyuncs.com/codeup/deploytest/tangnie001-stack/corporate_rag/settings/mirror_sync` |
| 6 | **流水线自动开始**（Codeup 有提交 → 钩子触发：构建推镜像 → 打包制品 → **部署**）| 云效网页 | `https://deploytest-cn-shanghai.devops.aliyuncs.com/flow/my?page=1` |
| 7 | **人工创建发布单**（**仅"匹配不到发布单"时**需要：流水线最后一步会挂起等你；匹配到则**跳过此步、直接发布**）| 云效网页 | 同上（流水线页）|
| 8 | **主机部署执行**（四槽位 `preflight → start → health_check → clean`；由流水线最后一步触发）| 目标机 | 自动；日志在部署页 |
| 9 | **冒烟**（见 §5；缺任一步不算成功）| 浏览器 | `http://<ECS-IP>/` |
| 10 | **查看版本 / 回滚** | 云效网页 | `https://deploytest-cn-shanghai.devops.aliyuncs.com/appstack/app/deploy-app/versions` |

> **第 3 步可以交给助手**：只要说「开始部署」，助手会先 `git fetch` 并比对本地 `dev-wsl` 与 `origin/dev-wsl`，确认没有未推送 / 落后的提交（并检查工作区是否干净）再往下走。

#### 2.0.1 代码从 GitHub 到 Codeup（两条路，只留一条）

流水线**代码源是 Codeup**，而开发机只推 **GitHub** ⇒ 中间必须把提交搬到 Codeup：

1. **手动「镜像同步」（当前口径）**：到 §2.0 第 5 步的同步设置页点「立即同步」。零维护，代价是每次发布多一步人工。
2. **自动化（未实施）**：在 **GitHub Actions** 里 `git push --mirror` 到 Codeup（GitHub 侧网络好、几秒），Codeup 的 push 事件再触发流水线。

⚠️ **两条路只应保留一条** —— Codeup 的镜像同步是**强制覆盖**；若既手动镜像同步、又让本地/Actions 直推 Codeup，两边会互相打架。直接"一次推两边"（`git remote set-url --add --push`）同理，且 Codeup 需另配凭据，不如走第 2 条。

#### 2.0.2 常用入口（速查 / 可点击）

- **代码同步**（GitHub → Codeup，第 5 步）：`https://deploytest-cn-shanghai.devops.aliyuncs.com/codeup/deploytest/tangnie001-stack/corporate_rag/settings/mirror_sync`
- **流水线**（看进度 / 第 7 步在此创建发布单）：`https://deploytest-cn-shanghai.devops.aliyuncs.com/flow/my?page=1`
- **版本 / 回滚**（第 10 步）：`https://deploytest-cn-shanghai.devops.aliyuncs.com/appstack/app/deploy-app/versions`
- **生产入口**：站点 `http://8.133.218.201/` ｜ Langfuse `http://8.133.218.201:3000` ｜ 目标机 `root@8.133.218.201`（发布路径 `/opt/www/corporate_rag`）

> 本节只做**链接速查**；步骤正文见上面的 §2.0 表格，不在此复述。

### 2.1 发布链路：流水线最后一步是「部署」，按发布单匹配决定是否挂起

```
Git 提交 ──▶ ① 构建并推送 app 镜像 ──▶ ② 打包部署制品（tgz）──▶ ③ 部署步骤 ──▶ ④ 主机部署
              [云效构建机]              [云效构建机]              [云效]         [目标机]

③ 的行为：匹配到发布单 ──▶ 直接发布（继续 ④）
          匹配不到       ──▶ 挂起，等人工在部署页「创建发布单」，创建成功后继续 ④
```

- ① 用 `DockerBuildPushACR` 步骤（**在云效构建机执行**）：`dockerTag = ${BUILD_NUMBER}`。
- ② 用 `scripts/ci/pack-deploy-artifact.sh`（**在云效构建机执行**，即**流水线打包阶段的执行命令**）：把**同一个 `${BUILD_NUMBER}`** 注入 `docker-compose.image.yml` 的 `image:`。
  ⚠️ **本脚本不在本地运行、也不在目标机运行** —— 它的运行位置就是流水线的打包阶段。本地仅在演练时手工跑，且必须显式 `--tag <tag>`（本地没有 `BUILD_NUMBER`，脚本会 `fail`）。
- ③ **流水线的最后一步是「部署」步骤** —— 它**先匹配发布单**再决定怎么走：
  - **匹配到发布单** → **直接发布**（继续执行 ④ 主机部署）；
  - **匹配不到**（首次，或换了应用 / 分支）→ 该步骤**挂在最后一步等人工** —— 到**部署页面点「创建发布单」**，创建成功后它继续。
- ④ **主机部署**（**在目标机执行**）：制品下发 → 解压到 `/opt/www/corporate_rag` → 依次跑 4 个槽位脚本（见 §2.4）。

> ⚠️ 两点别搞反：①「主机部署」是**流水线的最后一步**（不是流水线之外的纯手工动作）；② **人工创建发布单只在"匹配不到"时需要** —— 一旦发布单建立，**后续运行直接发布、不再需要人工确认**。

> 各段执行机器：①② 在**云效构建机**（云效杭州集群），③ 在云效控制面，④ 在**目标机 ECS**。`deploy.sh` 只在第 ④ 段（目标机）跑。

> **代码源前置**：流水线代码源 = **Codeup**；GitHub 的提交需先**同步到 Codeup**（当前为手动「立即同步」）才会被本次运行取到 —— 这是 ① 与 ② 的**共同**前置，不是 ① 独有的。

> **`BUILD_NUMBER` 是平台内置变量，不是 ① 的产物**：它是**本次流水线运行序号**（云效内置、每次运行 +1），同一运行内**每个步骤都拿到同一个值**（① 段日志 `BUILD_NUMBER=4`、③ 段日志 `+ export BUILD_NUMBER=4` 即证）。所以 ① 的 `dockerTag: ${BUILD_NUMBER}` 与 ② 的 `TAG="$BUILD_NUMBER"` 结构性相同，**不需要 ① 先跑、也没有段间传递**。
> ⚠️ 唯一会让两者不一致的情形是 **① 与 ② 不在同一条流水线**（各有独立运行序号）—— 因此"**同一条流水线**"才是 tag 一致的前提。

> **依赖关系**：② 只读仓库文件 + `${BUILD_NUMBER}`，**不碰 docker / ACR / 镜像** ⇒ 与 ① **无依赖，二者可并行**。而 **④（部署单被执行时）需要 ① 和 ② 都已成功** —— 既需要 ② 产出的制品，也需要 ① 已把镜像推进 ACR（`deploy.sh` 第 4 步 `docker manifest inspect` 校验，取不到即 `fail`）。（①② 之间怎么编排不影响正确性：可并行，也可串行。）

> **tag 一致性**：因为 ① 与 ② 在同一条流水线、共享 `BUILD_NUMBER`，所以**制品里的 tag = 实际推送的镜像 tag**，不需要人工同步。
> 部署步骤的 `deploy.sh` 第 4 步会 `docker manifest inspect` 校验该镜像存在；取不到即说明"制品与镜像不配套"（多半是构建段失败）。

### 2.2 变量与缓存（「变量和缓存」）

| 变量 | 作用 | 注意 |
|---|---|---|
| `PIP_REPO_USER` | **制品仓库（云效 packages）认证**的用户名 | 供构建期 `pip install` 走 PyPI 代理仓；**不是 ACR 凭据** |
| `PIP_REPO_PASS` | 上述密码 | **勾「私密模式」** |
| （ACR 凭据） | 拉/推 ACR 镜像 | 构建侧走 serviceConnection；**目标机另需 `docker login`**（`ACR_USER`/`ACR_PASSWORD` 或手工登一次） |

❗ **两套凭据不可混用**：`PIP_REPO_*` 服务制品仓库（HTTP Basic），登不了 ACR（Registry v2 token 流）。

**新建/复制流水线时最容易漏的就是这组变量** —— 若构建期报"索引里查不到任何包"，先回来核对本节。

> ⚠️ `LLM_MODEL` 之类的流水线变量注入的是**目标机的环境变量**；而 **app 容器只读 `env_file: .env`**（compose 的 `environment:` 段没列 `LLM_MODEL`）。所以**改模型 / 切换 provider 要改服务器上的 `.env` 并重跑部署（切 provider 见 §2.7）**，改流水线变量对 app 无效。

### 2.3 构建步骤字段口径

| 字段 | 值 | 说明 |
|---|---|---|
| `dockerfilePath` | `Dockerfile` | 相对**项目根**；填 `/` 或带仓库名前缀都会报错 |
| `contextPath` | **留空** | 默认解析成项目根，别手动填 |
| `dockerTag` | `${BUILD_NUMBER}` | 与打包段同源 |
| `options` | `--platform linux/amd64 --build-arg PIP_REPO_USER --build-arg PIP_REPO_PASS` | **不带值**，从环境变量取 |

❗ `options` 字段**不展开 `${...}`** —— 写 `--build-arg PIP_REPO_USER=${PIP_REPO_USER}` 会把**字面量** `${PIP_REPO_USER}` 当用户名传给 pip（表现为"`Could not find a version ... (from versions: none)`"）。

### 2.4 主机部署四槽位（配在**部署页面**上）

这四个槽位**配在云效「部署」页面的主机部署里**（不是配在流水线里），由**人工在部署页面点「创建部署单」**后按顺序执行。制品由平台下发到目标机（路径以部署配置为准，常见 `/home/admin/app/package.tgz`），下面的启动槽位把它解压到标准发布路径 `/opt/www/corporate_rag`。

调用顺序：**停止 → 启动 → 健康检查 → 清理**。原则：**破坏性动作必须排在启动之后**。

**停止槽位（只做预检，不停服）**：
```bash
if [ -f /opt/www/corporate_rag/scripts/deploy/deploy.sh ]; then
  cd /opt/www/corporate_rag && bash scripts/deploy/deploy.sh preflight
else
  echo "[preflight] 首次部署：机上尚无 deploy.sh，跳过（无旧版本需保护）"
fi
```

**启动槽位**：
```bash
set -euo pipefail
ROOT=/opt/www/corporate_rag
# ★ 必须用「平台为这张部署单下发的制品」—— 回滚时它指向旧版本。
#   不要自己拼 URL / 写死版本，否则回滚会拉回最新版（回滚失效的典型成因）。
ART="${package_download_path:-/home/admin/app/package.tgz}"
[ -f "$ART" ] || { echo "[start] 制品不存在: $ART"; exit 1; }
gzip -t "$ART" 2>/dev/null || { echo "[start] 制品不是有效 gzip（$(stat -c%s "$ART") 字节）"; exit 1; }
mkdir -p "$ROOT"
rm -rf "$ROOT/scripts"; rm -f "$ROOT/docker-compose.image.yml"   # 非挂载路径，可安全清
tar zxf "$ART" -C "$ROOT"                                        # deploy/ 不动（bind mount）
cd "$ROOT"
bash scripts/deploy/deploy.sh start
```

> 🔑 **回滚能否生效，取决于这一步"制品从哪来"**：制品里烘着 `deploy_store_local:<构建号>`，用部署单下发的制品 ⇒ tag 与页面（`deploy/nginx/html`）一起退回旧版；自行下载 ⇒ 永远最新版，回滚形同虚设。

**健康检查槽位**：
```bash
cd /opt/www/corporate_rag && bash scripts/deploy/deploy.sh health_check
```

**清理槽位**：
```bash
cd /opt/www/corporate_rag && bash scripts/deploy/deploy.sh clean
```

> 为什么"停止"只做预检：若它真停服，之后任一步失败（制品损坏、拉镜像失败）会把服务留在**下线**状态。停旧起新由启动阶段的 `compose up -d` 一次完成，失败时旧版本保持在线。
> `deploy.sh` 支持的完整模式：`deploy|start`、`preflight`、`health_check`、`stop`、`clean`（`bash scripts/deploy/deploy.sh -h`）。

### 2.5 已踩坑清单（换流水线/重建时对照）

1. **`{{ }}` 会在云效侧被当占位符渲染** —— 主机部署脚本里禁用（`docker --format '{{.X}}'` 一律改 shell 解析）。
2. **`options` 字段不展开 `${...}`** —— 用不带值的 `--build-arg NAME`。
3. **Alinux 4 自带 `docker` 与 `docker-ce` 互斥** —— 只补 `docker-compose-plugin` 即通（别装 `docker-ce` 全家桶）。
4. **`docker compose restart` 不吃 `.env`** —— 改 `.env` 后要 `docker compose up -d --force-recreate app`。
5. **制品下载**：用云效内建「下载制品」；若自己 `curl`，务必 `-fL`（`-f` 让 HTTP 错误直接失败，避免把错误响应体当成包）。
6. **第 4 步报「非交互环境无法输入密码 —— 请用 ACR_USER / ACR_PASSWORD 环境变量提供凭据」** —— 说明**该机没有 ACR 凭据**（流水线的部署槽位无 TTY，输不了密码；常见于**机器重建**或从未登录过）。两条修法，任选其一：① 在目标机上**手工登录一次**（§2.0 第 2 步 / §3 第 2 条，凭据固化到 `/root/.docker/config.json`，之后永久免登录）；② 把 `ACR_USER`/`ACR_PASSWORD` 配到**部署槽位的环境变量**里。验证：`docker manifest inspect <app 镜像> >/dev/null && echo LOGIN_OK`。
7. **ECS 与 ACR 必须同地域 —— 香港机房拿不到大陆个人版 ACR 的镜像**（2026-10-09 实测）：报错形如 `Get "https://aliregistry-<region>.oss-<region>.aliyuncs.com/docker/registry/v2/blobs/…": dial tcp …: i/o timeout`。ACR 把 **config 与镜像层**放在**同地域 OSS**：ECS 跨地域时 **registry 侧可达**（`curl …/v2/` → `401`、0.17s；`docker manifest inspect` **能把 manifest 取回来**）但 **OSS blob 取不到**（`curl https://aliregistry-cn-shanghai.oss-cn-shanghai.aliyuncs.com/` → `000` / 20s；`docker pull` 取 config blob 时 ~31s 超时）⇒ `compose pull` / `docker pull` 必失败。
   - ⚠️ **别用 `docker manifest inspect` 判断"能不能拉"** —— 它走 registry 侧、**能过**；真正卡死的是层/配置 blob（实测它甚至会打出完整 manifest 再报 `unsupported manifest format`，极具误导性）。
   - ⚠️ **不是"只有 app 镜像受影响"**：`docker-compose.image.yml` 的 **6 个镜像全在这台 cn-shanghai ACR**（5 个公开基镜像 `deploy_base:*` + app `deploy_store_local:*`）⇒ 跨地域机上**一个都拉不下来**（实测：**公开库与私有库表现完全一致**——公开库只是免登录，blob 仍在同一 OSS）；`docker save/load` 应急要搬 6 个镜像且**每次发布都要重复**，不实用。
   - ✅ **结论（唯一低成本解）**：**ECS 必须与 ACR 同地域**。若确需香港/海外机器，只能 ① 把 ACR 换成当地实例（**个人版无香港地域，需企业版**），或 ② 给 dockerd 配**常驻**的大陆中转代理（`/etc/systemd/system/docker.service.d/http-proxy.conf` 里设 `HTTPS_PROXY`）。

### 2.6 新增 pip 依赖的影响（以企微长连接 SDK 为例）

本仓库新增依赖 `wecom-aibot-python-sdk==1.0.2`（导入名 `aibot`；传递依赖 `websockets` / `aiohttp` / `pyee` / `cryptography` / `certifi`）与 `pycryptodome`。新增 / 升级 pip 依赖合并后必须做两件事：

1. **重建 app 镜像**：依赖烘进镜像，`restart` / `up -d` 不吃新依赖，须 `docker compose build --no-cache app`（或走流水线重新构建）。
2. **预热云效 PyPI 代理仓 `repo-okxha`**：该代理仓是懒加载缓存，若构建机首次要新包而缓存未命中会构建失败 —— 先在代理仓侧触发一次拉取预热，再跑流水线构建。

> 该 SDK 的用法与约束（只调公开接口、禁止 `run()`）见 `docs/agents/code-map.md` 的「官方 aibot SDK 用法与约束」。

### 2.7 切换 LLM provider（模型供应商）

provider 差异（思考参数形状 / reasoning 字段 / 端点 / 密钥 / 定价 / 窗口）已收敛到单一层 `src/infra/llm/provider/`，事实写在数据面 **`registry.yaml`**（随 app 镜像发布，见 ADR-0021）。**切换 provider 不改代码**，只改目标机 `.env` 后重跑部署：

1. **选 provider + 给密钥**：设 `LLM_PROVIDER=<provider 名>`（如 `deepseek`；缺省 `dashscope`），并设对应密钥 `<PROVIDER>_API_KEY`（如 `DEEPSEEK_API_KEY`）。被绑定 provider **缺密钥会在启动期 fail-fast**（`validate_config`，日志 `[provider] validate ...`）。
2. **删掉旧键**（否则静默出错）：
   - `LLM_BASE_URL` / `LLM_API_KEY` —— 单 provider 时代的"软迁移"键；留着会把请求**指回旧 provider**；
   - `LLM_MODEL` / `CLASSIFY_MODEL` —— 写死的旧模型名；留着会把旧名**发到新 provider**（如 `kimi-k3` 发去 DeepSeek → 404）。
3. **角色级覆盖（可选）**：`<ROLE>_PROVIDER`（`main` / `classify` / `summary` / `judge` / `embedding` / `rerank`）。缺省分两族 —— `main` / `classify` / `summary` **跟随 `LLM_PROVIDER`**；`embedding` / `rerank` / `judge` **独立缺省 `dashscope`**（如 DeepSeek 无 embedding / rerank 端点 ⇒ 向量化与精排**必须留在百炼**，否则启动期校验拒绝）。
4. **模型名**：缺省取 `registry.yaml` 的 `models.<role>`，可用 `<ROLE>_MODEL` 覆盖。**`.env` 里不必再写任何模型名**（这正是"切 provider 只改指针"的由来）。
5. **定价 / 窗口 / tokenizer 系数**：同来自 `registry.yaml` 的 `catalog`。**无需手动 seed 定价** —— 应用启动期会按 `catalog.pricing` **幂等 ensure** Langfuse 模型定义（无价模型记 WARNING）。
6. **同步 + 重跑**：`scp .env root@<ECS-IP>:/opt/www/corporate_rag/.env`（§1.4），再按 §2.0 走一次发布（或目标机 `bash scripts/deploy/deploy.sh start`）。⚠️ `docker compose restart` **不吃 `.env`**，须 `up -d --force-recreate app`（§2.5 第 4 条）。
7. **验证判据**：目标机日志出现 `[provider] validate state=on`，且真实对话出现 `[agent] model turn model=<新 provider 的模型>`。
8. **回退**：把 `LLM_PROVIDER` 改回旧值即可（`.env` 改动可逆）；**跨版本回滚必须镜像与 `.env` 一起回**（见 §7 —— 新旧版对 `.env` 键的期待不同）。

> 例（百炼 → DeepSeek）：`.env` 设 `LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY`，**删** `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `CLASSIFY_MODEL`；保留 `DASHSCOPE_API_KEY` / `EMBEDDING_*` / `RERANK_*`（embedding / rerank 仍走百炼）。

### 2.8 告警接入（Grafana → 企微群）

把 Grafana 告警接进企微群自动分析：端点是 `POST /api/alerts/webhook/{token}`，**token 走 URL 路径**
（Grafana 的 contact point 不支持自定义请求头，这是实测结论；两侧日志均已脱敏），未配置或不匹配 → `401`；
校验后立即 `202`、分析在后台任务里跑（**5 秒内返回**，见 `docs/agents/api_contract.md` §2.9）。
配置项见 `src/config/alert.py`，编排见 `src/services/alert_service.py`。

1. **token 由 `docker-compose.image.yml` 的 app `environment:` 提供**（`ALERT_WEBHOOK_TOKEN`，与
   `NEXTAUTH_URL` / `AUTH_DISABLE_SIGNUP` / `WECOM_BOT_ENABLED` 同款：**生产独有值硬编码、不走 `.env`**）。
   原因：目标机 `.env` 是人工从开发机拷上去的，本地测试值会跟着上机 —— **2026-10-13 实测踩中**
   （本地 `.env` 的 `ALERT_WEBHOOK_TOKEN=tok` 被带上机，Grafana 侧一直 `401`，而目标机只读没法改）。
   **轮换 = 改那一行 + 重新发布**，并同步把 Grafana contact point 的 URL 末段改成同一个值。
2. **`ALERT_TARGET` 仍需目标机 `.env`**（**先同步 `.env`、再上新制品** —— 顺序陷阱同 §1.4：新制品不含 `.env`）。
   **改完 `.env` 必须重建容器**（`env_file` 只在建容器时读）：`bash scripts/deploy/deploy.sh start`。
   ⚠️ 试运行期务必确认里面的 `chatid` **就是要发消息的群** —— 它同样是从开发机拷上去的。
3. **在 Grafana 里加 contact point**（`Alerting → Contact points → 新建/编辑 Webhook`）：
   - **URL**：`http://<站点>/api/alerts/webhook/<ALERT_WEBHOOK_TOKEN>`（**token 拼在路径末段**）
   - **Method**：`POST`（默认）
   - **不要**去 ARMS 配「通用 Webhook」，也**不需要**任何自定义 body 模板 —— Grafana 默认推的就是
     原生 Alertmanager 载荷，`fingerprint` / `alertname` / `summary` 都在里面（实测见 §2.9 字段表）
   - 确认**没有**勾「Disable resolved message」（否则恢复通知不发）
4. **在通知策略里引用该 contact point**（或设为 default）。用 contact point 的「**Test**」按钮先打一条；
   注意此时端点会对**测试载荷**返回 `202` 并真跑一轮分析（**Test 也会发群消息**）—— 想零副作用地只验
   token 是否对上，用坏载荷打：`400` = token 对上了、`401` = 没对上（探针用法与判据见 §2.8 末尾）。
5. **首次取群 `chatid`**：在目标群 **@ 一次机器人** → 查 `sessions.title`（形如 `[企微·dev] 群聊 <chatid>`）→ 把 `chatid` 填进 `ALERT_TARGET`（`bot_key` 复用 `WECOM_BOTS` 里的 key）→ 同步 `.env` 后重建容器。
6. **冒烟**：真实触发一次告警 → 群里**先受理占位、后收结论**；等它恢复 → 群里应收到 `[已恢复] …`
   （恢复**不走大模型**，直接推模板消息）；再造一次分析失败 → 兜底转发带 `[原始告警]` 前缀。

> 排障：`401` = token 不对或未配置；`404` = **URL 没带 token 段**（路由不匹配）；`400` = 载荷解析失败，
> 此时拒绝路径会把**原始载荷**记进应用日志（见 `api_contract.md` §2.9），拿它核对上游形状。
> 两条查证命令：
> ```bash
> cd /opt/www/corporate_rag
> docker compose -f docker-compose.image.yml exec app \
>   sh -c 'grep -h "\[alert\]" /data/logs/app_$(date +%Y-%m-%d).log | tail -20'
> ```

---

## 3. 首次上机

0. ⚠️ **换机 / 新旧并行期**：两台机都开 `WECOM_BOT_ENABLED` 会**抢同一批机器人的长连接**（后连者顶掉先连者，日志出现 `disconnected_event`，表现为三台机器人时而回话时而不回）。切换顺序：**先在旧机 `bash scripts/deploy/deploy.sh stop`** → 再上机新机 → 确认新机锚点 `[wecom] bots connected n=3 total=3` → 最后下线旧机。另外新机的 PG / MinIO / Redis 是**空卷**：站点侧数据（知识库、会话、文件）从零开始，需要保留就必须另行搬迁数据卷（alembic 迁移只保证 schema，**不搬数据**）。
1. **装 Docker + compose 插件**（Alinux 4：`dnf install -y docker-compose-plugin`）。
2. **ACR 登录**（app 镜像在私有库，**只在上机时做一次**，凭据落 `/root/.docker/config.json`）：
   ```bash
   docker login --username=hi31383397@aliyun.com \
     crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com
   ```
   密码从**团队渠道**取，**不写进仓库**（写进仓库 = 永久留在 Git 历史）。
   登录一次即固化到 `/root/.docker/config.json`，**之后发布不再需要密码**；
   仅无 TTY 自动化时用 `ACR_USER`/`ACR_PASSWORD` **环境变量**（`echo "$ACR_PASSWORD" | docker login -u "$ACR_USER" --password-stdin <registry>`）——
   注意 `deploy.sh` **不读 `.env`**，这两个键放 `.env` 无效。
3. **放 `.env`**：从团队渠道取键清单，填真实值，`chmod 600 /opt/www/corporate_rag/.env`（**当前为人工拷上机，方式与影响见 §1.4**）。
4. **安全组**：只放行 22 + 80。
5. **系统**：swap ≥2 GB、系统盘 ≥60 GB（本地演练构建时 build cache 可达数 GB）。
6. **拉起服务**（等价于流水线的启动槽位）：
   ```bash
   cd /opt/www/corporate_rag
   bash scripts/deploy/deploy.sh            # 8 步：环境判定→依赖→前置检查→ACR 登录→拉镜像→起栈→迁移→健康检查
   ```
   前置检查会校验：`docker-compose.image.yml` / `.env` 非空 / `deploy/nginx/*` / `deploy/postgres/init/`；缺任一报错而非静默降级。

---

## 4. 数据迁移

- **在 app 容器内执行**（`alembic/` 与 `alembic.ini` 已打进镜像），由 `deploy.sh` 的迁移步骤自动完成（含等待 PostgreSQL 就绪 + 幂等重试）。
- 手工重跑（幂等）：
  ```bash
  cd /opt/www/corporate_rag
  docker compose -f docker-compose.image.yml exec app alembic upgrade head
  ```
- **顺序**：迁移**先于冒烟**（首版建 8 张表，第二版加 `knowledge_base.domain` 列）。

---

## 5. 冒烟（发布成功判据）

**缺任一步都不得判定发布成功**：

1. 健康检查：`curl -fsS http://127.0.0.1/api/health` → 200。
2. 浏览器打开 `http://<ECS-IP>/` → 用测试账号登录。
3. 建/选一个知识库 → **上传一份文档**。
4. 等该文档状态变为 **`ready`**。
5. 提问 → **回答里带引用来源**。

> 上传步骤专治"nginx 上传上限被挡"，`ready` 专治"迁移未跑/存储凭据不匹配"，引用专治"检索链路断"。这三类正是静态检查覆盖不到、只有真实交付才暴露的。

---

## 6. 日常更新

**正常发布流程见 §2.0**（推代码 `dev-wsl` → Codeup 镜像同步 → 流水线自动跑完并发布；**仅"匹配不到发布单"时**才需人工到部署页创建一次，之后自动）。之后日常只需：

- 改 `LLM_PROVIDER` / `LLM_MODEL` 等运行配置、或**新增环境变量** → **同步目标机 `.env`**（当前为人工 `scp`，见 §1.4）→ 重跑部署（或 `deploy.sh start`）；**切换 provider 见 §2.7**（须删 `LLM_BASE_URL` / `LLM_API_KEY` 等旧键）；只推代码不改 `.env` 时，新键不会生效；
- 单独体检：`bash scripts/deploy/deploy.sh health_check`；
- 临时停服（维护 / 下线）：`bash scripts/deploy/deploy.sh stop`（**人工运维用**，别接进流水线停止槽位）；
- 回收磁盘：`bash scripts/deploy/deploy.sh clean`（保留最近 2 个 app 镜像 + 清悬空镜像与残留包）。

**发布前置**：制品源于**已提交的 commit** —— 发布前 `git status` 必须为空，未提交改动不得进入发布。

### 6.1 RAGAS → DeepEval 配置迁移（2026-10-17）

| 旧名 | 新名 |
|---|---|
| `RAGAS_LLM_MODEL` | `JUDGE_MODEL` |
| `RAGAS_PROVIDER` | `JUDGE_PROVIDER` |
| `RAGAS_JUDGE_TIMEOUT` | `JUDGE_TIMEOUT` |
| `RAGAS_HTTP_TIMEOUT_*` | `JUDGE_HTTP_TIMEOUT_*` |
| `RAGAS_METRIC_MAX_ATTEMPTS` | `EVAL_METRIC_MAX_ATTEMPTS` |
| `RAGAS_DATA_DIR` | `EVAL_DATA_DIR` |
| `RAGAS_TESTSET_DIR` | `EVAL_TESTSET_DIR` |
| `RAGAS_REPORT_DIR` | `EVAL_REPORT_DIR` |
| `RAGAS_LLM_CACHE_DIR` | `EVAL_LLM_CACHE_DIR` |
| `RAGAS_USER_ID` | `EVAL_USER_ID` |
| `RAGAS_HTTP_MAX_RETRIES` | 删除（裁判固定 `max_retries=0`） |
| 出题专用 `RAGAS_TEST_*` / `RAGAS_DOC_WHITELIST` | 删除（随出题路退役） |

启动期检测到任何 `RAGAS_*` 会 fail-fast；先改共享 `.env` 再部署。
旧 `data/ragas/` **不自动删除**，确认无引用后人工清理；历史 `report_path` **不迁移**（旧报告的 CSV 路径继续指向旧目录，仅作留档，不再被新运行覆盖）。
`_obsolete_ragas_cache/`（旧 `kg_*.json` 与 `llm_cache/` 的归档）**不自动删除**，确认无需复用后人工清理该目录。

---

## 7. 回滚与备份

**版本回滚（首选，走云效）**：应用版本页 `https://deploytest-cn-shanghai.devops.aliyuncs.com/appstack/app/deploy-app/versions` → 选旧版本 → 回滚 → 平台**按那张部署单的制品**重新下发并执行主机部署。

- 🔑 **前提（否则回滚不生效）**：启动槽位必须**消费部署单下发的制品**（见 §2.4 的 `${package_download_path}`）—— 制品里烘着 `deploy_store_local:<构建号>` 与 `deploy/nginx/html`，二者一起退回旧版；**脚本若自行下载制品，回滚就永远是"最新版"**。
- 旧镜像需仍在 **ACR**：ACR 的 tag 不受本机 `clean` 影响（`clean` 只删本机镜像），故正常够用。
- ⚠️ **本链路在 demo 上尚未验证通过**：实测回滚后页面版本未退回，2026-10-05 起**搁置待查** —— 排查顺序、已确认机理（ACR 是 Registry v2 不能 `curl` 镜像；tag 跟制品走、不跟环境走）见 `requirements_pool.md` H-11。

**兜底（手工）**：

- 首选：重跑一次旧的构建（回到那个构建号对应的代码），产出对应镜像；
- 应急：临时把 `docker-compose.image.yml` 的 `image:` 改指旧的 `deploy_store_local:<旧tag>`，`docker compose -f docker-compose.image.yml up -d app`。

**数据备份**：

- **MinIO 里的原始上传文件是唯一不可再生的源头** —— 丢了不可重建，必须备份；
- PostgreSQL 卷（`postgres_data`）承载元数据与向量，建议与 MinIO 同批备份；
- 频率 / 保留期 / 存放位置：**待定**（见下）。

---

## 待定项（不得当作已决定）

| 项 | 当前口径 | 待定 |
|---|---|---|
| HTTPS | 纯 HTTP（nginx 只有 80） | 是否上 443 + 证书来源 / SLB 终止 TLS |
| Langfuse | 保留在栈内，**对外暴露在 `:3000`**（2026-10-09 由绑回环改为对外，见 §1.3；`NEXTAUTH_URL` 已硬编码） | 长期去留；是否改走 nginx 反代（免开 3000 端口）或加 HTTPS |
| `docker-compose.prod.yml` | 不参与本目标形态 | 废弃 / 删除 / 与目标形态对齐 |
| 备份口径 | 见 §7（MinIO 必备） | 频率、保留期、是否上 OSS |
| 托管化 | 单机自建（PG / Redis / MinIO 同机） | 是否迁 RDS / Redis / OSS / SLB |
| 首次语料导入 | — | 导入哪批文档、由谁、走界面上传还是灌库 |

> 以上未决项已登记在 `docs/agents/requirements_pool.md`；正式决定前，runbook 只以"当前口径"描述。
