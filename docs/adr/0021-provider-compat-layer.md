# ADR-0021：引入应用内 Provider 兼容层，拒绝网关

- **Status**：Accepted
- **Date**：2026-10-11
- **Deciders**：用户（决策）；助手（实现）
- **关系**：在 ADR-0020「不启用网关、三类模型直连 Provider」之上引入应用内 provider 兼容层；本层**不是网关**（不做多 provider 路由 / fallback / 限流 / 计费聚合），落在 ADR-0020 明确保留的「不引入替代网关」边界内，**不取代任何在先 ADR**。相关规格：`provider-compat` / `model-config` / `llm-tracing`。

## 背景与问题

大模型底座与代码绑定，切换或新增 Provider 必须改代码，且容易漏：

- **provider 特有思考参数散落 5 处**：主链路每次调用发 `enable_thinking`（middleware）、`get_classify_llm` / `get_summary_llm` 构造期写死、fork 子代理构造期写死、reasoning 子类构造期注入 `preserve_thinking=True`。
- **reasoning 回传与 `preserve_thinking` 语义分散、硬编码**：字段名写死 `reasoning_content`，回传策略只对 DashScope 有效。
- **单一 `LLM_BASE_URL` / `LLM_API_KEY`** 迫使主 / 分类 / 摘要 / 评估四类角色同 Provider；`EMBEDDING_BASE_URL` 还会 fallback 到 `LLM_BASE_URL`，切 Provider 会误指。
- **外部差异（2026-10 调研）**：思考参数有 4~5 个家族（`enable_thinking` 布尔 / `thinking.type` on-off / `reasoning_effort` / Anthropic `thinking` / Gemini `thinkingConfig`）；**默认值相反**（Qwen 默认关，DeepSeek/Kimi/GLM 默认**开** ⇒ 不显式关闭即静默变贵）；带 tools 多轮时 reasoning 字段**必须原样回传否则 400**（DeepSeek/GLM/Kimi 同规则）；能力差异上 **DeepSeek 无 embedding、无 rerank**。

**约束**：ADR-0020 已否决用网关统一（覆盖不全 + 内存/耗时）；鉴权密钥不得入库。

**触发**：需要把「切换 Provider」从「改代码」变成「改配置」，且不能再漏一处思考参数。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A 为每类模型建一套类层次 | 抽象基类 + 各 provider 子类 | 过度设计（YAGNI）：角色 × provider 组合爆炸 | 类型期强约束 |
| **B 单包兼容层 + 数据面（选）** | 一个包 `src/infra/llm/provider/`，provider 事实收进 `registry.yaml`，调用侧只认 `build_*` | 新增 Provider 仍需重建镜像；registry 是数据，typo 靠启动校验兜 | 切换 = 纯 `.env`；新增 = 加一条数据；差异收敛到一层 |
| C 网关统一 | 把 LiteLLM 之类放回默认路径 | 盖不全（rerank 必须绕过）+ 内存/耗时（ADR-0020 已实测口述） | 统一路由 / fallback / 限流 |

## 决策

**选 B：在应用内引入单一 provider 兼容层，不用网关。**

- 包结构：`registry.yaml`（数据面）+ `spec.py`（`ProviderSpec` / 角色解析 / 启动校验）+ `thinking.py`（`thinking_extra_body`，唯一产生思考参数处）+ `chat.py`（`ReasoningPreservingChat`，形状/回传/preserve 按 provider 驱动）+ `factory.py`（`build_chat` / `build_embeddings` / `build_rerank`）。
- 对外接口收窄为 `thinking_extra_body(provider, enabled)`、`build_chat(role, *, thinking=None, **overrides)`、`build_embeddings()`、`build_rerank()`；`models.py` 门面保留既有调用点（参数向后兼容），并新增 `get_judge_llm()`。
- **角色缺省分两族**：`main` / `classify` / `summary` 跟随 `LLM_PROVIDER`；`embedding` / `rerank` / `judge` **独立缺省 `dashscope`**（DeepSeek 无 embedding/rerank；judge 是离线评测角色、也是切 Provider 后的验证手段）。
- **思考参数归一化为 `bool`（on/off）**，由 `thinking_extra_body` 翻译成目标 provider 的形状；`preserve_thinking` 只在 `thinking_style==dashscope` 且未关思考时注入。
- **覆盖优先级**：运行时值 = `.env` 若设，否则 registry 默认；`thinking_style` / `capabilities` / `reasoning_field` 属「事实」，**不可** env 覆盖。
- **启动期 fail-fast**：未知 provider / 无分支的 `thinking_style` / 角色绑到缺能力的 provider / 缺 `<PROVIDER>_API_KEY` → 启动失败；校验只在应用启动执行（`import` 期不触发），带 `PROVIDER_VALIDATE=off` 开关。

## 理由

关键一行：**把 provider 事实收进可评审的数据文件、把差异收敛到一层，调用侧只认稳定接口 —— 于是「切换 Provider」回到纯配置。**

- 代码里只会剩下「发对参数」的逻辑，不再有 `provider == "dashscope"` 这类分支散落。
- 思考开关**始终显式发**（默认值相反，不显式即静默变贵），且构造与请求**同源**产出完整 dict，消除「per-call `extra_body` 整体覆盖构造期 `extra_body`」导致的丢字段。
- reasoning 回传由 `reasoning_field` **能力驱动**：不给不认该字段的 provider 塞多余字段。

## 后果

**正面**：

- 切换已注册 Provider（如百炼 → DeepSeek）= 改 `.env`（provider 指针 + 密钥），不重建镜像。
- 新增 Provider = 加一条 registry 数据（形状属已知家族时零代码）。
- provider 差异集中在一层，可评审、可测试；启动期 fail-fast 把「未知 provider / 缺密钥 / 缺能力」挡在启动前。

**负面 / 接受的代价**：

- registry 随镜像打包（`Dockerfile` COPY `src/`），**新增** Provider 仍需重建镜像；只有**切换**已注册 Provider 才是纯配置。
- registry 是数据，typo 不会编译报错 → 依赖启动期 schema 校验兜底。
- Phase 1 为**等值重构 + 一处显式修复**（主链路恢复 `preserve_thinking`，请求体因此变化），非「完全等值」。

**不解决的问题**（明确列出，避免后人误以为这条 ADR 管了它）：

- ❌ **Provider 的语义差异**（默认思考开/关、温度在思考档是否失效、上下文窗口口径）—— 本层负责「发对参数」，不负责「改模型脾气」。
- ❌ **路由 / 跨 Provider fallback / 统一限流 / 计费聚合** —— 网关职责，仍在 ADR-0020 否决范围内。
- ❌ **Anthropic / Gemini 原生协议** —— 本仓锁定 OpenAI 兼容协议族。
- ❌ **思考力度（budget / effort）保真** —— 本仓只用 on/off。

## 复查触发条件

- 出现**多 Provider 路由 / 跨 Provider fallback / 统一限流 / 成本分摊**的硬需求时（此时应重新对照 ADR-0020）；
- Provider 思考参数出现**第三种本层无法表达的形态**时（加枚举成员 + 翻译分支 + 一条数据，若已超出一层的表达能力则重审）；
- 需要接入**非 OpenAI 兼容协议**（Anthropic / Gemini 原生）时；
- 角色 → Provider 绑定需要**运行时动态切换**（当前为启动期静态解析）时。
