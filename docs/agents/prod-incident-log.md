# 生产问题记录

线上（含上线前预发/演示机）发生过的异常，按发生时间**追加**记录，不覆盖、不改写历史（后续结论以「跟进」小节补，不回头改原文）。

## 记录协议

每条必填：**时间 / 现象 / 影响面 / 定位证据 / 根因 / 处置 / 防复发 / 关联**。

- 只记"发生过的事实"，推测必须标注"推测"并写清依据；事后被推翻的结论保留原文，在「跟进」里更正。
- 防复发优先落到可执行物：配置值、代码注释、`docs/agents/defensive-patterns.md` 规则、`docs/agents/troubleshooting.md` 的"症状 → 先查哪里"。
- 与线上无关的纯需求讨论不记在这里（去 `requirements_pool.md` / `docs/research/`）。

## 记录

### 2026-10-20 · 上传超过 600KB 的文件导致 app 容器重启（OOM）

- **现象**：知识库页上传文件，文件稍大（实测阈值约 600KB）时上传失败；`docker ps` 看到 app 容器**重启过**（进程被杀后被 `restart: unless-stopped` 拉起）。表现为上传接口返回 202 后轮询状态一路 502，几十秒后"自己好了"。
- **影响面**：所有上传入口（站点知识库页）。上线前发现，未影响外部用户。
- **定位证据**：`dmesg` 有 `oom-kill ... task=python3.12`（uvicorn 进程被 cgroup OOM 杀），当时容器 `mem_limit: 512m`。
- **根因**：上传接口 `await file.read()` 把整份文件读进内存（`src/services/document_service.py`），解析器再展开（图片解码放大数倍）；512m 档位下峰值触顶被杀。**不是** nginx 挡的 —— 当时 nginx 是 12m，600KB 远未触顶。
- **处置**：`docker-compose.image.yml` / `docker-compose.prod.yml` 的 app `mem_limit` 提到 2g / 4g（`docker-compose.yml` 开发档仍 512m，本地不需要传大文件）。调整后实测 1MB 文件正常上传。
- **防复发**：
  - app 内存档位与理由写进 compose 注释，改小前先看注释；
  - 上传上限统一为 **10MB**（见下表），消掉"网关 12MB / 应用 50MB / 前端另一套"的三口径；
  - 前端在选择文件时即拦截并给出可操作提示，不让超限文件走到网关吃 413。
- **关联**：`docs/agents/deploy-runbook.md`（app 为什么是 2g）、`docs/agents/api_contract.md` §2.2.2（上传接口 413）。

## 上传大小限制现状（10MB 单一上限）

| 位置 | 值 | 说明 |
|---|---|---|
| `deploy/nginx/nginx.conf` | `client_max_body_size 10m` | 应用机网关，第一道 |
| `deploy/nginx/edge.conf` | `client_max_body_size 10m` | 入口机网关，最外层；两处必须一致 |
| `.env.template` / 目标机 `.env` | `MAX_FILE_SIZE=10485760` | 应用侧校验读取（`src/config/settings.py`） |
| `src/config/settings.py` 默认值 | `10 * 1024 * 1024` | 未配置时生效 |
| `deploy/nginx/html/index.html` | `_maxUploadSize` 硬上限 10MB | 选文件即拦截；`/api/config` 返回更小值时取更小 |

**规则**：应用侧上限 SHALL 小于或等于 nginx 上限；改任一处必须同步其余（含 `.env.template`）。否则小的一方先挡，413 出现在谁身上会误导排查。

## 待跟进

- **正好 10MB 的文件会被网关 413**：multipart 除文件本体外还有 boundary 与头部开销（约 200 字节），而 `client_max_body_size 10m` 限的是**整个请求体**，所以 `file.size == 10485760` 的文件过不去，前端与后端校验却都放行。若要严格支持"10MB 以内皆可传"，nginx 侧应留开销余量（如 `11m`），或应用侧下调（如 9.5MB）。当前未定。
