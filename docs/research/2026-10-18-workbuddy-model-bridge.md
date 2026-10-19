# WorkBuddy 模型接入可行性（dsh-workbuddy-connect 实读与实测）

> **调研日期**：2026-10-18
> **性质**：外部事实与可行性评估。本文回答"能不能接、接进来要付什么代价"，**不构成决策**；是否落地、落在哪个环境，归 ADR / change。
> **被测对象**：`corrinehu/dsh-workbuddy-connect` v0.7.8（commit `b8ca335`，2026-10-18）；本机 WorkBuddy 桌面端 5.8.3（Windows，国内版）。
> **结论一句话**：技术链路已在 Python 侧完整复现并跑通真实调用；但它是**非公开的客户端侧接口**，且与桌面端登录态强绑定，不能当普通 OpenAI 兼容供应商接。

## 结论摘要

| 问题 | 结论 | 依据 |
|---|---|---|
| 能否调用 WorkBuddy 的模型？ | 能。HTTP 200 / `text/event-stream`，首字节约 1.0–1.2s | §3 实测 |
| 是否 OpenAI 兼容？ | 请求/响应形状兼容 chat completions，含流式 `tool_calls`、`reasoning_content` | §3.3 实测 |
| 能否直接当项目的 provider？ | 不能"只填 base_url"。需要自带凭据解密、特殊请求头、非流式→流式适配 | §5 |
| 生产机（Linux ECS）能用吗？ | 取凭据这一步依赖 Windows 桌面端 + Electron 二进制，生产机上没有这些；只能拷 token，长期续期未验证 | §6 |
| 有 embedding / rerank 吗？ | 没有。向量化与精排必须留在百炼 | §4 |
| 主要代价 | 非公开接口随时可能变；个人账号额度跑企业业务有条款风险；无 SLA | §6 |

## 1. 插件是什么

`dsh-workbuddy-connect` 是 DeepSeek Harness（DSH，TypeScript）的插件，不是独立服务，也不是公开 API 客户端。它做两件事：

1. 从本机 WorkBuddy 桌面端的登录态里取出 access/refresh token；
2. 把这些 token 背后的模型以 OpenAI 兼容形式注册进 DSH 的模型选择器。

它同时支持国内版（`WorkBuddy`）与国际版（`WorkBuddy AI`），两版共享 `CodeBuddyExtension` 凭据目录、按文件名区分（`src/variants.ts:105`、`src/auth.ts:106`）。本文只覆盖国内版。

插件自身在 README 中声明：这套集成搭的是 **WorkBuddy 客户端侧接口、而非有文档的公开 API**，WorkBuddy 一变就可能需要跟进；定位是个人自用工具，受 WorkBuddy 服务条款约束。

## 2. 凭据链路（已在 Python 复现）

### 2.1 凭据文件

国内版路径（`src/auth.ts:106`）：

```
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
（旧版写在 %APPDATA%\Roaming 下，探测顺序见 src/auth.ts:142）
```

WorkBuddy 5.6 起该文件的 token 字段是加密包装（`src/auth.ts:512`）：

```json
{ "auth": { "accessToken": { "$wbEncrypted": 1, "envelope": "<base64>" }, ... },
  "account": { "uid": "...", "nickname": {...}, ... } }
```

### 2.2 取 at-rest 密钥

密钥不在文件里，也不走 Windows DPAPI，而在 WorkBuddy 改造版 Electron 的私有 binding 中。插件通过运行 WorkBuddy **自己的二进制**取（`src/desktop-credential-protection.ts:1629`）：

```text
<WorkBuddy.exe> -e 'process.stdout.write(String(process._linkedBinding("electron_browser_workbuddy_storage").loggerGet()))'
环境变量：ELECTRON_RUN_AS_NODE=1
输出：{"version":1,"atRestSecretKey":"<base64 32B>"}
```

WSL 调 Windows exe 时，`ELECTRON_RUN_AS_NODE` 必须同时登记进 `WSLENV` 并带 `/w`（插件在 #133 踩过：只设 env 不会跨到 Windows 侧，exe 会以 GUI 模式启动）。本机实测该变量不登记则拿不到 payload。

### 2.3 解密

`src/desktop-credential-protection.ts:312`、`:220`、`:250`：

- `protectorKey = sha256(atRestSecretKey 的 UTF-8 字符串)`
- 算法：AES-256-GCM
- `keyId = sha256(protectorKey) 的 hex 前 16 位`
- AAD 构造（逐字节复刻 `buildAuthenticatedContextAad`）：

```text
"WB-AAD\0" + 0x01 + lp("WBEV1") + lp("sym-v1") + be32(suite) + lp(keyId) + 0x02 + 0x00 + 0x00
lp(x) = be32(len(utf8(x))) + utf8(x)
```

- envelope 为 `base64(JSON{suite, keyId, nonce, authTag, ciphertext})`

本机实测 `keyId` 与 envelope 声明一致，`accessToken`（1391 字符）与 `refreshToken`（700 字符）均解密成功。

### 2.4 token 生命周期

解密后的文档字段：`expiresAt` / `refreshExpiresAt`（毫秒时间戳）。本机个人账号实测：access token 剩余约 28 天，refresh token 剩余约 30 天。

刷新接口：`POST https://copilot.tencent.com/v2/plugin/auth/token/refresh`，头 `X-Refresh-Token` + `X-Auth-Refresh-Source: workbuddy`（`src/upstream.ts:861`）。**该链路未实测**（见 §7）。

## 3. 上游协议（已实测）

### 3.1 端点

| 项 | 值 | 出处 |
|---|---|---|
| 国内版 chat | `https://copilot.tencent.com/v2/chat/completions` | `src/upstream.ts:196`、`:841` |
| 国际版 chat | `https://www.workbuddy.ai/v2/chat/completions` | `src/upstream.ts:198` |
| 模型目录 | `GET /v3/config` | `src/upstream.ts:884` |
| 计费/积分 | `/v2/billing/meter/get-user-resource` | `src/upstream.ts:1045` |

### 3.2 请求头

除 `Authorization: Bearer <accessToken>` 外，实测必需或插件固定发送的头（`src/upstream.ts:473`、`:456`）：

```text
Origin: https://www.codebuddy.cn
Referer: https://www.codebuddy.cn/
User-Agent: WorkBuddy/<appVer> WorkBuddy/<appVer> [CLI/<cliVer>]
X-Requested-With: XMLHttpRequest
X-User-Id: <uid>                  （无 uid 时用 X-No-User-Id: 1）
X-Enterprise-Id: <id>             （个人账号用 X-No-Enterprise-Id: 1）
X-Domain: <domain>                （空时用 X-No-Department-Info: 1）
X-IDE-Type / X-IDE-Name: WorkBuddy
X-IDE-Version: <appVer>
X-Product: SaaS
X-Conversation-ID / X-Conversation-Request-ID / X-Conversation-Message-ID: 每请求新 UUID
```

`X-Conversation-*` 三件套：国际网关自 2026-10-16 起对缺失该组的请求返回 400 / `code 11128`；国内网关实测带不带都通过，插件两边统一携带。三件套只标记通道，不承载会话状态（`src/upstream.ts:438`）。

UA 版本解析顺序：已安装 App → 按区域保存的缓存 → 编译期兜底（国内兜底 `5.5.6`，`src/client-identity.ts:42`、`:122`）。

### 3.3 请求体约束

- `stream` 必须为 `true`。本节所有结论都基于流式请求。
- `tool_choice` 仅接受字符串形式，对象形式返回 400（`src/upstream.ts:557` 注释）。
- `role: "developer"` 会被国内端点以 400 / `code 11128` 拒绝，需改写为 `system`。
- `reasoning_effort: "off"` 不被接受，需省略该字段（`dropUnsupportedEffort`）。

### 3.4 实测记录（2026-10-18，本机 WSL2）

| 用例 | 结果 |
|---|---|
| `model=hy3`，普通问答，`stream=true` | 200，`text/event-stream`，首字节 1.17s；chunk 为 OpenAI 形状，含 `reasoning_content` |
| `model=hy3` + `tools=[get_weather]` + `tool_choice=auto` | 200；增量 `tool_calls` 正常（首块带 `id`/`name`，后续块分片 `arguments`），`finish_reason=tool_calls` |
| `model=hy3`，`stream=false` | 400 / `code 11101` / `msg: Non-stream chat request is currently not supported` |

`hy3` 为免费档（x0.00），上述验证不产生积分消耗。

## 4. 模型目录

模型清单来自 `/v3/config`，插件内置了兜底快照（`src/catalog.ts:44`）。兜底列表中的国内版条目（2026-10-18 快照）：

| 模型 id | 上下文 | 最大输出 | 图片输入 | 积分倍率 |
|---|---|---|---|---|
| `hy4-preview` | 960K | 64K | 是 | x0.29 |
| `hy3` | 192K | 64K | 是 | x0.00 |
| `hy3-x` | 192K | 64K | 是 | x0.05 |
| `step-5-preview` | 1M | 64K | 是 | x0.00 |
| `space-bunny` | 1M | 128K | 是 | x0.08 |
| `deepseek-v4.1-flash` | 1M | 128K | 是 | x0.11 |
| `glm-5.3` | 1M | 64K | 是 | x0.79 |
| `glm-5.3-flash` | 1M | 131K | 是 | x0.06 |
| `glm-5.3-flashx` | 1M | 131K | 是 | x0.14 |
| `glm-5.2` | 1M | 64K | 否 | x0.79 |
| `glm-5.1` | 200K | 48K | 否 | x0.79 |
| `glm-5v-turbo` | 200K | 64K | 是 | x0.71 |
| `minimax-m3-pay` | 512K | 128K | 是 | x0.25 |
| `minimax-m2.7` | 200K | 48K | 是 | x0.26 |
| `kimi-k3-2` | 1M | 32K | 是 | x1.62 |
| `kimi-k2.8-preview` | 1M | 64K | 是 | x0.77 |
| `kimi-k2.7` | 256K | 32K | 是 | x0.57 |
| `kimi-k2.6` | 256K | 32K | 是 | x0.52 |
| `deepseek-v4-pro` | 1M | 128K | 是 | x0.51 |

要点：

- **只有 chat 类模型**，无 embedding、无 rerank 端点。
- 多数模型声明 `onlyReasoning`（不能真正关闭思考）；思考档位为 `low/medium/high/xhigh/max` 的子集，未声明档位的模型使用上游默认。
- 模型是"客户端订阅额度"计费（积分），不是按 token 的公开价目；积分倍率以服务端为准，会变。

## 5. 接进本项目的可行落点（技术评估）

若要在本项目落地，最小可行改动面：

| 环节 | 落点 | 说明 |
|---|---|---|
| provider 注册 | `src/infra/llm/provider/registry.yaml` | 新增 `workbuddy`，`base_url: https://copilot.tencent.com/v2`；只有 main/classify/summary/judge 可绑，`embedding`/`rerank` 留百炼 |
| 凭据与鉴权 | 新增模块（如 `src/infra/llm/provider/workbuddy_auth.py`） | 读/解密凭据文件，注入 Bearer；token 过期前调用刷新接口 |
| 请求头 | 同上 | `X-IDE-*`、`X-User-Id`、UA、Origin/Referer 固定；`X-Conversation-*` 每次请求新 UUID，用 httpx request hook 注入 |
| 非流式适配 | 新增 `ChatOpenAI` 子类（继承现有 `ReasoningPreservingChat`） | `_generate` 内部发 `stream:true` 并聚合；`_astream` 直接用。项目现有 `agent_node.ainvoke`（`src/agents/graph/agent_node.py:293`）、`temporal.ainvoke`（`src/rag/temporal.py:128`）、`history_summary.ainvoke`（`src/chat/history_summary.py:112`）都会命中非流式路径 |
| 思考档位 | `src/infra/llm/provider/thinking.py` | 新增 `reasoning_effort` 风格（low/high/max）；`off` 需省略字段而非传值 |

规模判断：核心适配约 300–400 行 Python 加测试，不含凭据轮换与配置文档。

## 6. 限制、风险与反面

1. **非公开契约**：端点、请求头、模型 id 都是客户端抓包结果，没有版本承诺。插件自身即以此为由声明"可能随时需要跟进更新"。
2. **凭据与桌面端绑定**：取 at-rest 密钥要跑 Windows 版 Electron。生产环境是 Linux ECS，没有 WorkBuddy 桌面端；只能把明文 token 拷过去，再用 refresh 续期。
3. **refresh 的迁移性未知**：刷新是普通 HTTP 调用，但服务端是否校验设备/IP/sessionState 未验证。验证会轮换 refresh token，可能让桌面端当前登录态失效，因此本轮未做。
4. **条款与账号风险**：个人账号额度用于企业知识库，属于产品服务条款的灰区；额度耗尽或风控触发会直接影响线上可用性。
5. **无 SLA**：与项目现行选型基线（生产验证优先）不一致；若落地，需要记录偏离理由与代价。
6. **能力缺口**：无 embedding/rerank，意味着 RAG 的向量化与精排不可能整体迁移；只能替换生成侧。
7. **成本口径不同**：积分倍率不是 token 单价，Langfuse 的成本观测无法沿用现有 pricing 表，会静默为 0 或需要单独换算。

## 7. 待确认项

| # | 问题 | 为什么重要 | 建议怎么验 |
|---|---|---|---|
| 1 | refresh 是否绑定设备/IP | 决定生产机能否长期续期 | 在可回滚的前提下用副本 token 调一次刷新；或直接观察桌面端刷新后旧 refresh token 是否仍可用 |
| 2 | 生产无 Electron 时的凭据供给方式 | 决定部署形态 | 手工导入 + 定时刷新；或开发机定期导出 |
| 3 | 各模型对 `tools` 的支持是否一致 | 主链路依赖工具调用 | 逐模型发一次 `tools` 探测（hy3 已验证通过） |
| 4 | `onlyReasoning` 模型的实际行为 | 项目的 `enable_thinking` 开关可能对该模型无效 | 对 `canDisableThinking=false` 的模型测省略 vs 显式档位 |
| 5 | 思考档位与项目 `thinking_style` 的映射 | 影响 `get_classify_llm` / 摘要链路 | 对照 `registry.yaml` 现有 dashscope/deepseek 两种 style |
| 6 | 积分耗尽/风控的错误码 | 影响降级策略 | 插件已有错误分类表，可复用其 marker 列表 |

## 8. 复现方法

环境：WSL2（Ubuntu），可访问 `/mnt/c`、`/mnt/d`；WorkBuddy 5.8.3 安装于 `D:\Program Files\WorkBuddy\WorkBuddy.exe`。

```bash
git clone --depth 1 https://github.com/corrinehu/dsh-workbuddy-connect.git
```

验证顺序（本轮实际执行）：

1. 跑 Electron helper 取 `atRestSecretKey`（注意 `WSLENV` 要带 `/w`）；
2. Python 侧 AES-256-GCM 解开 `accessToken`，校验 `keyId`；
3. `httpx` 发一次 `stream=true` 的最小问答，确认 200 且首块为 `chat.completion.chunk`；
4. 发一次带 `tools` 的请求，确认 `tool_calls` 增量与 `finish_reason=tool_calls`；
5. 发一次 `stream=false`，确认被 400 拒绝。

本轮所有请求使用免费档 `hy3`；临时脚本未落盘保留，也未输出任何 token 值。

## 9. 来源

- 插件仓库：https://github.com/corrinehu/dsh-workbuddy-connect（v0.7.8 / `b8ca335`）
  - 凭据路径与探测顺序：`src/auth.ts:106`、`:142`
  - 加密凭据解析：`src/auth.ts:512`
  - 密钥派生与 AAD：`src/desktop-credential-protection.ts:220`、`:250`、`:312`
  - Electron helper：`src/desktop-credential-protection.ts:1629`
  - 端点与请求头：`src/upstream.ts:196`、`:456`、`:473`、`:821`、`:861`
  - 请求体归一化：`src/upstream.ts:557`、`:1530`
  - 模型兜底列表：`src/catalog.ts:44`
  - UA 构造：`src/client-identity.ts:122`
- 本地实测：2026-10-18，本机 WSL2 + WorkBuddy 5.8.3（国内版，个人账号，无 enterpriseId）
- 插件市场页（含版本兼容表与 WSL 说明）：https://dshpluginhub.ai/en/plugins/dsh-workbuddy-connect
