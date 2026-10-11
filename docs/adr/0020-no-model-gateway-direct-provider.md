# ADR-0020：不启用 LiteLLM 模型网关，三类模型直连 Provider

- **Status**：Accepted（**本决策不变；在其『不引入替代网关』边界内，ADR-0021 引入应用内 provider 兼容层**）
- **Date**：2026-10-10
- **Deciders**：用户（决策，2026-07-31 实际生效）；助手（2026-10-10 追记）
- **关系**：与 openspec 规格 `model-config` / `model-gateway` 相关；不取代任何在先 ADR

## 背景与问题

2026-07-28 曾部署 **LiteLLM Proxy** 作为统一模型网关（DashScope + DeepSeek 路由与 fallback，提交 `063f292`）。三天后（2026-07-31）提交 `5aad92a` 给它加上 `profiles: ["disabled"]`，**注释只写了一行**「开发环境直连 DashScope，需要时启用」，**没有任何决策说明**。

此后应用一直直连 Provider：

- `.env` 的 `LLM_BASE_URL` / `EMBEDDING_BASE_URL` 均指向 DashScope 兼容端点（`https://dashscope.aliyuncs.com/compatible-mode/v1`）；
- 该服务**从未进入生产档** `docker-compose.image.yml`（只存在于 dev 的 `docker-compose.yml`）。

触发：2026-10-10 复盘"当初为什么关掉"，仓库里**找不到原因** —— 既无 ADR，也无 change/plan 记录该决定（有的只是 2026-07-28 那批"引入它"的文档）。使用者补充了真实原因，本 ADR 即为其追记。

两条决定性事实：

- **网关照不全**：`src/models.py:234` 明写「Rerank 不走 LiteLLM Proxy，因为 LiteLLM 不支持 DashScope 的 rerank 端点」 ⇒ 三类模型（LLM / Embedding / Rerank）里**必有一类绕过它**，"统一入口"的价值打折。
- **它吃内存，而内存紧张会推高耗时**：当时的提交自述「need ~1GB, 512m unreliable」（`6c81339`、`106eeef` 一串内存调整）。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A 保留网关为默认路径 | 应用经 `litellm-proxy:4000` 调用 | 链路多一跳且耗时上升；盖不全（Rerank 必须绕过）；常驻 ~1GB 内存 | 统一路由 / 跨 Provider fallback / 统一鉴权与限流 |
| **B 直连 Provider（选）** | 三类模型各自配 `model` / `api_key` / `base_url` | 失去统一 fallback / 限流 / 成本统计层 | 少一跳、无额外内存；模型切换仍由 `.env` 完成 |

## 决策

**选 B：不启用 LiteLLM 网关作为默认路径。** 保留其 `disabled` profile 备查（需要时 `docker compose up -d litellm-proxy` 显式启动）；三类模型直连 Provider，配置项各自独立。

## 理由

关键一行：**网关带来的链路耗时上升，与它只覆盖 2/3 模型的事实不相称。**

- **① 端到端耗时上升** —— 使用者口述"经 LiteLLM 到本服务约 **2s**"（⚠️ **仓库内无实测数据**，此值为 2026-10-10 追记时的口述，不是已验证数字）。机理：**LiteLLM 进程需要较多内存，内存紧张会显著推高其响应耗时** —— 与当时提交的自述吻合（`6c81339`「remove litellm-proxy mem_limit (need ~1GB, 512m unreliable)」、`106eeef` 的内存调整）。此外 `litellm/config.yaml` 带 `num_retries: 3` / `request_timeout: 10` / `allowed_fails: 3` / `cooldown_time: 30`，主模型一抖即先超时再重试、秒级延迟随之而来。
- **② 覆盖不全** —— Rerank 必须绕过网关（`src/models.py:234`），"统一入口"名不副实。
- **③ 收益在当前规模为空** —— 单一 Provider 下"多 Provider 路由/切换"用不到；`config.yaml` 的 fallback（`qwen3.7-max → deepseek-v4-flash`）还需补 `DEEPSEEK_API_KEY` 才可用，而该键当时为空。

## 后果

**正面**：

- 链路少一跳，无代理层内存占用；模型/端点切换仍由 `.env` 的 `LLM_MODEL` / `LLM_BASE_URL` 等独立完成。

**负面 / 接受的代价**：

- 失去**跨 Provider 自动 fallback**、统一限流/重试、统一成本统计与鉴权；Provider 单点故障时无自动兜底。

**不解决的问题**（明确列出，避免后人误以为这条 ADR 管了它）：

- **不引入替代网关**（Phoenix / 自建 router 等）—— 本 ADR 只否定"把 LiteLLM 放回默认路径"。
- **不做多 Provider 路由**。
- **不清理备查资产** —— `litellm/config.yaml`（含 dormant 的模型路由）与 `tests/test_litellm_fallback.py` 保留；`config.yaml` 的模型清单已是启用时的**起点**而非现状，真启用前须按当前选型更新。

## 复查触发条件

- 出现**多 Provider 路由 / 跨 Provider fallback / 统一限流 / 成本分摊**的硬需求时；
- 出现对 **Provider 单点故障**的兜底要求时；
- 若有人想重启网关：**必须先实测**延迟开销（不要沿用本 ADR 里那条口述值），并复核其内存档位。
