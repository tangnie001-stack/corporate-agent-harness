## Context

**现状（代码事实）**：

- provider 特有的思考参数硬编码在 **5 处**：`middleware.py:85`（主链路每次调用发 `enable_thinking`）、`models.py:191/219`（`get_classify_llm` / `get_summary_llm`）、`executor.py:437`（fork 子代理）、`reasoning_chat.py:86`（构造注入 `preserve_thinking=True`）。
- `settings.py` 只有单一 `LLM_BASE_URL` / `LLM_API_KEY`（fallback `DASHSCOPE_API_KEY`），导致主 / 分类 / 摘要 / 评估被迫同一 provider；`EMBEDDING_BASE_URL` 还会 fallback 到 `LLM_BASE_URL`（切 provider 会误指）。
- reasoning 回传（`reasoning_chat.py:_get_request_payload`）与 `preserve_thinking` 语义分散、硬编码。

**外部差异（2026-10 调研）**：

- 思考参数有 4~5 个家族：`enable_thinking`(布尔，百炼/Qwen、硅基流动)、`thinking.type`(on/off，DeepSeek/GLM/Kimi/豆包/MiniMax)、`reasoning_effort`(OpenAI/xAI)、Anthropic `thinking`/`output_config`、Gemini `thinkingConfig`。
- **默认值相反**：Qwen 默认关；DeepSeek/Kimi/GLM 默认**开** ⇒ 不显式关闭即静默变贵。
- reasoning 字段名多为 `reasoning_content`；**带 tools 时必须原样回传否则 400**（DeepSeek/GLM/Kimi 同规则，Anthropic 用签名块）。
- 协议族：本仓锁定 OpenAI 兼容（DeepSeek/Kimi/GLM/豆包/百炼皆兼容）；Anthropic/Gemini 独有协议不在范围。
- 能力差异：**DeepSeek 无 embedding、无 rerank**。

**约束**：ADR-0020 否决网关（覆盖不全 / 内存 / 耗时）；鉴权密钥不得入库；镜像自足（`Dockerfile` 已有 `COPY src/ src/`）。

**角色消费者**：`main`（主循环）、`classify`（时间解析 `rag_tools` + 文档实体抽取 `document_service`）、`summary`（`history_summary`）、`judge`（RAGAS）、`embedding`、`rerank`。

## Goals / Non-Goals

**Goals:**

- 把 provider 差异收敛到**单一层**（一个包）；调用侧只认稳定接口。
- 切换 provider = **纯配置**（`.env` 改指针 + 密钥，不重建）。
- 新增 provider = **加一条数据**（参数形状属于已知家族时零代码）。
- 启动期 fail-fast，避免静默错误。
- Phase 1 等值重构（**除 D8 一处显式修复外**），其余行为不变、可测试兜底。

**Non-Goals:**

- ❌ 路由 / 跨 provider fallback / 统一限流 / 计费聚合（网关职责，⛔ ADR-0020）
- ❌ Anthropic / Gemini **原生协议**
- ❌ 力度（budget / effort）保真（本仓只用 on/off）
- ❌ 多模态**实现**；且 `vision` 字段位**也延后**（无真实消费者不预置）
- ❌ 各 provider 的**语义差异**（默认思考、温度失效、窗口）——层负责"发对参数"，不负责"改模型脾气"

## Decisions

### D1. 单一包 `src/infra/llm/provider/`，对外 3 个 `build_*` + 1 个纯函数

```
src/infra/llm/provider/
├─ registry.yaml   # 数据面：provider 事实 + 模型目录
├─ spec.py         # ProviderSpec / RoleBinding / ThinkingStyle + 角色解析 + 启动校验
├─ thinking.py     # thinking_extra_body(spec, enabled) → dict（唯一产生思考参数处）
├─ chat.py         # ReasoningPreservingChat（provider-aware：形状 / 回传 / preserve）
└─ factory.py      # build_chat(role, thinking) / build_embeddings() / build_rerank()
```

对外接口：`thinking_extra_body(provider, enabled)`、`build_chat(role, *, thinking=None, **overrides)`、`build_embeddings()`、`build_rerank()`。

`models.py` 门面保留 `get_llm` / `get_classify_llm` / `get_summary_llm` / `get_embeddings` / `get_rerank` 的**既有调用点不变**（参数列表向后兼容：`model` 默认改 `None`，空 / 未设 → `registry.models[role]`，见 D3），并**新增 `get_judge_llm()`**：现状 RAGAS 裁判用 `get_llm(model=RAGAS_LLM_MODEL)`（`eval_ragas.py:563`）借了 `main` 的角色，切 provider 会被错解析到 `main` 的 provider；`get_judge_llm()` 显式绑 `role=judge`，上述调用点改调它。
**替代方案（否）**：为每类模型建一套类层次 → 过度设计，YAGNI。

### D2. 数据面 = `registry.yaml`（YAML 带注释、入库、随镜像）

以 provider 为键声明：`base_url` / `api_key_env` / `thinking_style` / `reasoning_field` / `capabilities`（`tools`）/ `models`（角色→默认模型）/ `catalog`（模型→ context_window、pricing、tokens_per_cjk_char、embedding 批大小/维度）。**`vision` / `supports_disable_thinking` 等无真实消费者的字段延后**（见 D5–D6 / Non-Goals）。
**替代方案（否）**：纯 JSON（无注释，人维护差）；全部塞 `.env`（provider 事实与密钥/部署态混在一起）。

### D3. `.env` 只留密钥 + provider 指针 + 可选覆盖

覆盖优先级：**运行时值 = `.env` 若设，否则 registry 默认**。可覆盖的仅"部署态"四类：provider 指针、模型名、端点、密钥；`thinking_style` / `capabilities` / `reasoning_field` 属"事实"，**不可** env 覆盖。
**替代方案（否）**：registry 覆盖 `.env` → `.env` 形同虚设，失去应急/灰度手段。

```
角色 → provider:   main / classify / summary: <ROLE>_PROVIDER → LLM_PROVIDER → dashscope
                   embedding / rerank / judge: <ROLE>_PROVIDER → dashscope（独立缺省，见 D4）
模型名:            <ROLE>_MODEL（如 LLM_MODEL / RAGAS_LLM_MODEL）→ registry.models[role]
端点:              <PROVIDER>_BASE_URL → registry.base_url
密钥:              <PROVIDER>_API_KEY（唯一来源）
```

**角色 → env 名总表**：

| 角色 | provider env | 模型 env |
|---|---|---|
| `main` | `LLM_PROVIDER` | `LLM_MODEL` |
| `classify` | `CLASSIFY_PROVIDER` | `CLASSIFY_MODEL` |
| `summary` | `SUMMARY_PROVIDER` | `SUMMARY_MODEL` |
| `judge` | `RAGAS_PROVIDER` | `RAGAS_LLM_MODEL` |
| `embedding` | `EMBEDDING_PROVIDER` | `EMBEDDING_MODEL` |
| `rerank` | `RERANK_PROVIDER` | `RERANK_MODEL` |

**默认值解析的落地约束（评审 Blocker 1）**：现状 `settings.py` 的 `LLM_MODEL` 等常量默认**非空**（如 `"qwen3.7-flash-2026-07-15"`），而 `get_llm(model: str = LLM_MODEL)` 的默认参数在 **import 期**绑定 → 调用点永远显式传值，registry 的 `models[role]` **永远轮不到**。因此：① 这些 settings 常量默认改为**空串**（表示"未配置"）；② 5 个工厂的签名改 `model: str | None = None`，在**函数体内**解析；③ 空串 / None → `registry.models[role]`。registry 的 `models[main]` 初值须**逐字等于**今日生效值（`kimi-k3`），Phase 1 用测试锁定，避免"静默换模型"。

### D4. 缺省分两族：`main` / `classify` / `summary` 跟随 `LLM_PROVIDER`；`embedding` / `rerank` / `judge` 独立缺省 `dashscope`

**理由**：① DeepSeek 无 embedding/rerank，若跟随 `LLM_PROVIDER`，切 DeepSeek 会把向量化带过去 → 连带失败；② `judge`（RAGAS 裁判）是**离线评测**角色、且是"切换 provider 后的验证手段" —— 若跟随 `LLM_PROVIDER`，切 DeepSeek 会让评测连同默认模型名（`qwen3.8-max`，百炼专属）一起 404，并破坏 `eval_ragas.py:552-554` 写下的"选手 / 裁判分离"。故 `judge` 与 embedding/rerank 一样**独立缺省 `dashscope`**，可用 `RAGAS_PROVIDER` 覆盖。

### D5–D6. 思考形状 = `ThinkingStyle` 枚举；归一化输入 = `thinking: bool`

| `thinking_style` | on | off |
|---|---|---|
| `dashscope` | `{"enable_thinking": true}` + `preserve_thinking` | `{"enable_thinking": false}` |
| `deepseek` | `{"thinking": {"type": "enabled"}}` | `{"thinking": {"type": "disabled"}}` |

**本 change 只实现 `dashscope` + `deepseek` 两种形状**（唯一两个有真实 provider 的场景）。新增形状 = 加一个枚举成员 + 一个翻译分支 + 一条数据，调用侧零改动；但**不预置无消费者的形状**（`thinking_style:none` 与"不可关思考"分支延后，见 Important 3 / Non-Goals）。不做 effort/budget 归一化。

### D7. 永远显式发开关，不依赖 provider 默认值

**理由**：默认值相反（Qwen 关 / DeepSeek 开）。（`supports_disable_thinking=false` 的"不可关思考"模型属**延后项**，见 D5–D6。）

### D8. 单一纯函数产**完整** extra_body —— 这是**一处显式行为修复**，不是等值

`middleware.py` docstring 已指出 **per-call `extra_body` 整体覆盖构造时 `extra_body`**（langchain-openai **1.3.3**，`_get_request_payload` 浅合并）。

**现状是两条路径行为不一致**：`main` 路径构造期注入 `preserve_thinking`，但请求期被 per-call `{"enable_thinking": X}` **整体覆盖** → **`preserve_thinking` 在主链路被丢弃**；`fork` 路径（`executor` 构造期一次给全）却保留它。

层的对策：`thinking_extra_body(...)` 返回含 preserve 标志的**完整** dict，构造与请求**同源** → 不存在覆盖丢字段。**后果**：主链路请求体从 `{"enable_thinking": X}` 变为 `{"enable_thinking": X, "preserve_thinking": True}` —— **请求体变了，Phase 1 因此不是"完全等值"，而是"等值重构 + 这一处显式修复"**（见 Migration Plan）。

**六个角色路径的思考参数注入点**（证明没有路径丢标志）：

| 角色 / 路径 | 注入点 | 说明 |
|---|---|---|
| `main`（主循环） | **请求期**（`ModelParamsMiddleware`） | 每轮 `model_settings.extra_body` 须为**完整** dict（含 preserve） |
| `fork` 子代理 | **构造期**（`executor` 的 `get_llm(...)`） | 不经 `ModelParamsMiddleware`，构造期须给全 |
| `classify` | 构造期（`get_classify_llm`，关思考） | 关思考 → 不注入 preserve |
| `summary` | 构造期（`get_summary_llm`，关思考） | 同上 |
| `judge` | 构造期（`get_judge_llm`） | 无工具调用场景，按 provider 产形状 |
| `embedding` / `rerank` | 不涉及思考参数 | — |

**验证**：须覆盖"带工具多轮 + `preserve_thinking`"路径 —— DeepSeek/GLM/Kimi"带 tools 不回传 reasoning 即 400"的硬约束使其成为生死项。

**范围划界（评审 Important 4）**：本层**只拥有"思考参数"**；其它 provider 请求参数**不在范围**。`LLM_KWARGS`（JSON，可含 `extra_body`）会被请求期整体覆盖而**静默失效** → 启动期检测 `LLM_KWARGS` 是否含 `extra_body`，命中则**告警**（本 change 选择告警而非拒绝，保持存量兼容）。

### D9. reasoning 回传按能力驱动

字段名取 `spec.reasoning_field`；`preserve_thinking` 仅在 `thinking_style == dashscope` 且未关思考时注入；`reasoning_field` 为空则跳过注入（不给不认这字段的 provider 塞多余字段）。

### D10. `LLM_BASE_URL` / `LLM_API_KEY` 软迁移淘汰；密钥按 `<PROVIDER>_API_KEY`

**理由**：这两个是"单 provider 覆盖"，切 DeepSeek 时会把请求误指回百炼，须退出。
**做法（软迁移，避免 Phase 1 部署 breaking）**：Phase 1 仍读取这两个 env，但仅当"默认 provider 未显式配置端点/密钥"时作为该 provider 的覆盖，并打 **deprecation warning**；**Phase 2 删除**。
**完整删除清单**（迁入 registry / provider key）：`LLM_BASE_URL`、`LLM_API_KEY`、`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`RERANK_API_KEY`、`MODEL_INPUT_PRICE_PER_TOKEN`、`MODEL_OUTPUT_PRICE_PER_TOKEN`、`PROMPT_TOKENS_PER_CJK_CHAR`、`EMBEDDING_BATCH_SIZE`、`MODEL_CONTEXT_WINDOW_TOKENS`。
**保留** `LLM_MODEL` / `CLASSIFY_MODEL` / `SUMMARY_MODEL` / `RAGAS_LLM_MODEL` / `EMBEDDING_MODEL` / `RERANK_MODEL` 作**可选覆盖**（不填用 registry 默认）。

### D11. 定价 / 窗口 / tokenizer 系数 / embedding 批大小与维度进 registry

`PROMPT_TOKENS_PER_CJK_CHAR`（现为从 DeepSeek 文档借来的全局 0.6，消费于 `rag/prompt.py:188`）、`EMBEDDING_BATCH_SIZE`（DashScope 上限 20，消费于 `models.py:129`）、embedding `dimension`（现为死变量 `EMBEDDING_DIMENSION`，真实维度写死在 `chunk.py:52` 的 `Vector(1024)`）纳入 registry。**判断准则**：registry 只装"换 provider 就得跟着变的东西"；产品/部署旋钮（温度、TOP_K、预算阈值、功能开关、基础设施）一律留 `.env`/`const`。

**Phase 1 落地**：把 `chunk.py:52` 的 `Vector(1024)` 抽成单一常量，启动校验"registry 声明的 embedding 维度 == 该常量"（**不改列、不迁移**）——于是 `model-config` 规格里"维度不匹配 fail-fast"可真实实现，而不是延后。
**seed 范围**：`seed_langfuse_models` 从 registry 读，seed `catalog` 中**全部**有定价的模型（幂等，避免换角色/换 provider 后漏 seed）。

### D12. 启动期 fail-fast 校验（时机与范围受控）

校验项：未知 provider / `thinking_style` 无分支 / 角色绑到缺能力的 provider（`models` 里无该角色，如 `embedding→deepseek`）/ 被绑定 provider 缺 `<PROVIDER>_API_KEY` / registry 缺字段或格式错 → 失败并明确报错。

**时机与范围**：校验**只在应用启动（FastAPI lifespan）执行**，`import settings` / `import models` **不触发**（否则测试收集与无 key 的 CLI 会被打死）；只校验**被实际绑定**的 provider；提供 `PROVIDER_VALIDATE=off` 开关供测试 / 脚本跳过。

### D13. 新增 ADR + 更新 `model-config` 规格

ADR 记录"引入 provider 兼容层、拒绝网关"，给 ADR-0020 加反向指针；`model-config` 规格按层改写。

### D14. 定价播种：dry-run 诚实化 + 启动期幂等 ensure

现状 `seed_langfuse_models.py` 的 `--dry-run` 在 `run()` 里直接打印"将写入"，**绕过**了 `seed()` 的零价 / 已存在守卫（`langfuse-trace-enrichment` 已知遗留）——dry-run 会对"将跳过"的项谎报"将写入"。

**做法**：

- 把"创建 / 已存在 / 跳过"的判据抽成**单一纯函数**（输入：模型名、输入/输出价、已存在集合），CLI 的 `--dry-run` 与真跑**共用**它；dry-run 逐项如实报告动作（未配价/未知模型 → "将跳过"）。
- **应用启动期**（与 D12 同处）对被**绑定角色**所用模型执行一次**幂等 ensure**：registry 有价 → 确保 Langfuse 模型定义存在；无价 → 记 **WARNING**（静默 NULL 从根上消除）。换模型 / 切 provider 不再需要记得手动 seed。
- **非阻塞**：`LANGFUSE_ENABLE=false` 跳过；Langfuse 不可达记 warning 降级，不影响启动，也不受 `PROVIDER_VALIDATE` 影响。
- 手动 CLI 保留（首次开通 / 运维批量）。

**时序与异步安全（评审 Important 2）**：ensure 必须在 `configure_tracing()`（`main.py:63`，async lifespan 内）**之后**执行，且使用该 configure 出来的客户端（否则会造出默认 enabled 或空客户端，`settings.py:339-346` 注释已提示该陷阱）；实现**不得**在 async lifespan 里直接 `asyncio.run`（会嵌套事件循环）——须改为 await 现成 async 客户端 / 走线程或复用 CLI 的 async 实现；整体非阻塞、带超时兜底。

## Risks / Trade-offs

- [新增 provider 需重建镜像（registry 在镜像里）] → 缓解：**切换**已注册 provider 不需重建（纯 `.env`）；新增 provider 少见，走发布链路可接受。
- [`.env` 覆盖的模型名不在 `catalog` → 定价/窗口未知] → 记账一条 warn，成本按 NULL（与今天"未 seed 静默 NULL"一致）。
- [DeepSeek 默认开思考 + 思考档忽略 `temperature`（`top_p` 固定 1.0）] → 层保证非思考档显式发 `disabled`；产品语义变化需实测确认。
- [部分 provider 对意外 `reasoning_content` 字段可能 400] → 由 `reasoning_field` 能力驱动注入，不盲塞。
- [registry 是数据，typo 会静默] → fail-fast schema 校验（D12）。
- [`pyyaml` 现仅传递依赖] → 在 `pyproject.toml` 显式声明。
- [Phase 1 含一处**非等值**修复（D8 主链路恢复 `preserve_thinking`）] → 单列该修复的验证（带工具多轮 / 400 风险）；其余保持等值，全量测试绿后才进 Phase 2。
- [三 key 收敛为单一 `<PROVIDER>_API_KEY`：若生产三键不等，Phase 2 删键后鉴权失败] → 迁移加前置核对（Important 1）。
- [`.env` 覆盖与 registry 默认分叉，长期漂移] → 覆盖仅限"部署态"四类；文档写明。
- [fail-fast 若放 import 期会打死测试 / 无 key 的 CLI] → 校验限定应用启动期 + `PROVIDER_VALIDATE=off` 开关（D12）。
- [Phase 1 即改 `.env` 会 breaking 生产] → `LLM_BASE_URL` / `LLM_API_KEY` 软迁移（Phase 1 warn+当覆盖用、Phase 2 删，D10）。
- [启动期 ensure 定价引入一次网络调用、依赖 Langfuse 可达] → 非阻塞降级为 warning；仅对**被绑定**模型执行，量级小。

## Migration Plan

- **Phase 1（等值重构 + 一处显式修复）**：建层 + `registry.yaml` 只含 dashscope；settings 模型默认改空 + 工厂签名 `None`（D3）；**6 个工厂（5 个既有 + 新增 `get_judge_llm`）/ middleware / executor 全改走层**；embedding 维度常量与 registry 声明做一致性校验；`LLM_BASE_URL` / `LLM_API_KEY` 软迁移（warn + 当覆盖用）。**除 D8 那一处显式修复（主链路恢复 `preserve_thinking`）外**行为与今天一致（全百炼）；全量测试必须绿，并**单独验证**带工具多轮的 preserve / 回传。
- **Phase 2（切换）**：**前置核对**生产 `.env` 的 `LLM_API_KEY` / `EMBEDDING_API_KEY` / `RERANK_API_KEY` 是否相等（不等则先保留 `<ROLE>_API_KEY` 覆盖，见 Important 1）→ 往 registry 加 `deepseek` → `.env` 设 `LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY`（embedding/rerank/judge 自动留百炼）→ **删除 `LLM_BASE_URL` / `LLM_API_KEY`** → `seed_langfuse_models`（seed 全部有价模型）→ `--force-recreate` → 冒烟（非思考 / 思考 / 工具多轮）。
- **Phase 3（可选）**：加更多 provider。（`vision` / `thinking_style:none` / `supports_disable_thinking` 延后到有真实 provider 需求时，见 D5–D6 / Non-Goals。）
- **回滚**：① Phase 1 之后单回 `.env` 安全（`LLM_BASE_URL` / `LLM_API_KEY` 软迁移仍在）；② **Phase 2 之后必须"镜像 + `.env` 同时回"** —— `.env` 已删键，只回镜像会让旧版读不到 `LLM_BASE_URL` / `LLM_API_KEY` 而崩。

## Open Questions

- **已定**（评审 OQ）：`PROVIDER_VALIDATE` 默认 **`on`**，并在启动日志显式打印当前校验状态；回滚见 Migration Plan（Phase 2 后"镜像 + `.env` 同时回"）；角色 → env 名总表见 D3；`judge` 独立缺省 `dashscope`（D4）。
- 已知延后项（非本 change）：`reasoning_effort` 归一化、多模态**实现**、`vision` / `thinking_style:none` / `supports_disable_thinking` 三个无消费者字段、`LLM_KWARGS` blob **废弃**（本 change 只对含 `extra_body` 的情形告警，不废弃）。
- `QueryRouter` 类本体 + `tests/infra/search/test_query_router.py` 处于"生产死、CLI 活"灰区——**已登记** `docs/agents/requirements_pool.md`，本 change 不处置。
