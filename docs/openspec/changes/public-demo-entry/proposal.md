## Why

项目需要对外演示，但目前没有免登录入口：`chat.html` 依赖登录态拉取知识库列表，且入口一旦开放，任何访客都能通过 `/api/chat/stream` 自带 `kb_id` 检索任意知识库、调用写接口、无限消耗模型额度。演示是"把链接发出去、别人自己跑"，提问内容不可控，因此必须同时解决三件事：**能进得去、只能看演示库、花不完预算**。

当前无真实客户数据，采用"现网实例 + 开关"的成本最低；一旦有真实数据入库，必须拆独立实例（本变更的毕业条件）。

## What Changes

- **新增 demo 入口**：`demo.tangnie.cn`（同端口、同实例），nginx 增加 server 块转发到现有 app；正式站行为不变。
- **新增 `DEMO_MODE` 开关**：关闭时全部行为与今天一致，不留残余代码路径。
- **demo 身份**：demo 域名下自动分配 `demo_<uuid>` 访客身份（复用现有匿名 cookie 机制），不写入正式用户体系。
- **知识库强制绑定**：demo 身份下 `kb_id` 一律覆盖为 `DEMO_KB_ID`（腾讯 2024 年报），忽略请求传入值——这是唯一的数据边界。
- **写接口降权**：demo 下上传、删除文档、建/删知识库、评测、配置类接口一律 403；`/api/kbs/list` 只返回演示库。
- **配额**：会话最多 20 轮、单 IP 60 次/10 分钟（nginx）、全站日 30 万 token 预算（Redis 计数，超限统一提示）。
- **前端演示态**：首屏范围声明 + 6 个示例问题 + 额度用尽提示；隐藏知识库管理等入口。
- **演示语料**：独立 demo KB（与现有 `test` 库隔离），种子脚本灌入 `data/test_docs/tencent_2024_annual.pdf`。
- **紧急关停**：nginx 层返回 503 维护页，秒级生效，不依赖容器重启。
- **观测**：demo 流量统一 `channel=demo`，并记录 `demo_miss`（检索落空的事件），供后续补语料。

## Capabilities

### New Capabilities
- `demo-entry`: demo 访客身份、知识库强制绑定、写接口只读边界、配额与预算、关停与观测口径。

### Modified Capabilities
- `deployment-runbook`: 新增 demo 子域名、限流配置归属与紧急关停步骤（运维流程层面的变化）。

> 身份解析、知识库绑定、界面演示态三处均为**新增模式**（`DEMO_MODE` 关闭时行为不变），不修改既有需求，统一收敛进新能力 `demo-entry`，避免在既有 spec 里产生重复条款。

## Impact

- **代码**：`src/config/settings.py`（开关与配额）、`src/middleware/auth.py`（demo 身份）、新增 demo guard 中间件、`src/api/chat.py`（轮数与 kb 覆盖）、`src/api/knowledge_base.py` / `documents.py` / `kb_eval.py`（403）、`src/config/const.py`（`Channel.DEMO`）、`deploy/nginx/html/chat.html`（演示态）。
- **部署**：`deploy/nginx/nginx.conf`（demo server + `limit_req`）、`deploy/nginx/edge.conf`（demo 域名与证书）、DNS 新增 A 记录。
- **数据**：新增独立 demo KB（固定 UUID 写进配置）；不改动现有表结构。
- **评测**：新增 demo 验收清单（范围内正确率、范围外拒答、乱问稳定性），不影响现有评测基线。
- **风险**：与正式站同库同进程，demo 流量会挤占 CPU 与模型额度；企微 bot 同进程，需限制 demo 并发流数。
