## Why

生产链路上，**除 RAGAS 裁判之外的三类模型调用全是"裸奔"**：超时零配置、重试吃 SDK 默认、失败没有降级动作、失败没有留痕。已实测的两个机理使这条问题的代价被**静默放大**：

1. **超时缺失 ⇒ 一次挂死最坏 1800s**。现状（2026-10-15 核实，现仍成立）：`build_chat` 的 `ChatOpenAI` `request_timeout=None` / `max_retries=None`；`build_embeddings` 的 `OpenAIEmbeddings` `None` / `2`；`build_rerank` 的 `DashScopeRerank` **无该字段**。⇒ 等价于 read **600s 平铺** + SDK 重试 2 次。用户在线等待的场景里，这不是"慢"，是"看起来死了"。
2. **SDK 是唯一的隐藏乘法放大层**（ADR-0024 E3）：`max_retries=2` 时 `APITimeoutError ⊂ APIConnectionError` 会被重试 ⇒ 一个逻辑调用实际发 **3 次** HTTP。日志只看得到墙钟变长。

同时**降级动作严重不齐**（池 A-03）：只有 rerank 一处真正降级（失败/超时 → 退化向量距离排序），且**只有它留下事件**（`rerank skip/failed/timeout`）；其余依赖（LLM / Embedding / 向量库）失败即抛，无可判定动作、无用户可见说明。

而**先例已经存在且已验证**：ADR-0024 在裁判路径上确立了「细粒度超时 + 关 SDK 重试 + 本层有界重试 + 失败必留原始响应」，实测把 21 题全量从 **1h41m → 29m48s**。本次是把同一套口径从"裁判一条链"推广到"全部生产调用"，并按依赖补齐降级。

## What Changes

- **超时按角色显式化（A-05）**：三个 `build_*` 全部显式传入超时；`build_chat` 增加 `max_retries` 显式参数（**关掉 SDK 重试**，重试收敛到我们这层）。取值按**角色**配置（`main` 流式 / `classify` 短分类 / `summary` 短生成 / `embedding` / `rerank` 各自独立），值住 `settings`，**不进 `registry.yaml`**（registry 装 provider 的固有事实，超时是我们的 SLO 取舍，且同一 provider 的不同角色取值不同 ⇒ 它是角色属性）。
- **超时语义按流式拆开（A-05 难点）**：`read` = **流间隔**（httpx 天然语义，正好吻合）；**首 token 超时**需独立守卫；**整请求超时不得设**（长回答天然数分钟，设了就是误杀）。任何新超时必须 **< nginx `proxy_read_timeout` 300s**，否则用户先看到断连而不是友好降级。
- **异常分类收敛为一个无状态原语（A-03/A-05 共同缺口）**：现 `with_retry(retryable_exceptions=(Exception,))` 默认重试一切 —— 含 400/401/403 与 `ValueError`（池 A-03 2026-10-15 实测量到 "检索上下文为空" 被白跑 3×3 = **9 次纯浪费**）。新增分类判据（不可重试：输入校验/鉴权类；可重试：timeout/连接/429/5xx），并补 **jitter**（防惊群）与 **async 版**（现 `with_retry` 用 `time.sleep`，只能用于同步函数）。
- **降级动作按依赖补齐 + 留痕（A-03）**：给出「依赖 → 失败判定 → 动作 → 用户可见文案」的矩阵；动作**留在各调用点**（每依赖语义不同），但**两个出口收敛**：事件进 `log_event_specs.EVENT_SPECS`、用户文案进 `config/const.py`。
- **主循环补"生成任务级"保险丝**：复用 `fork_stream` 已有的三层形状（idle / total / turn）与"事件条数不增长"判据，补上主 agent 这一格（**不是**回退已被推翻的 SSE 订阅层 180s 空闲收流 —— 见 design D3 的层间区分）。**档 1 只记录不中止** ⇒ 使用自有降级事件（`reason = generation_idle`），**不套用** `DelegateStopReason`（该枚举语义为"fork 结束原因"，见 design D12 的原因词处理）。
- **修复 embedding 单点**：查询侧 `QueryEmbedder.embed_query` 每次 `get_embeddings()` **新建实例**（`src/infra/db/vector_store/pg_store.py:46`，入库侧同）⇒ HTTP 连接池不复用，且超时是**按实例**设的，落点必须在这里。

## Capabilities

### New Capabilities

- `timeout-policy`: 按角色的超时/重试取值与「唯一产地」（`build_*` 显式传入、SDK 重试关闭）、流式语义拆分（首 token / 流间隔 / 禁整请求超时）、异常分类与有界重试原语（含 jitter、async 版）。
- `degradation-policy`: 各外部依赖（LLM / Embedding / 向量库 / Rerank / Prompt 来源）失败时的降级动作矩阵、降级事件的唯一口径、降级时对用户可见的文案契约、生成任务级失控兜底、以及"不引入进程内模型 fallback"的约束。

### Modified Capabilities

> **⚠️ 2026-10-16 按主规格逐字核查后修正了分类**（原判"3 个 MODIFIED"不准确）：三份规格里 `超时|重试|timeout|retry` **全部 0 命中** ⇒ 新增这部分**没有可逐字复述的原文**，只能写 **ADDED**；而"值住哪里"**已有明文**，这些**必须 MODIFIED**，否则新规格会与旧明文自相矛盾。

- `provider-compat`：**ADDED**（超时/重试入参与其取值归属 —— 原规格 0 命中）+ **MODIFIED**「配置覆盖规则」：其现明文把 `.env` 可覆盖面**限定为四类**（provider 指针 / 角色模型名 / 端点 / 密钥），超时·重试要可被 env 覆盖 ⇒ 须扩为五类。**此改动与该条自身理由一致** —— 它排除的是"provider 事实"（`thinking_style` / `capabilities` / `reasoning_field`），而超时是**部署态策略**，不属事实。
- `model-config`：**MODIFIED**「三类模型独立配置」与「配置默认值」：后者现明文称"由注册表提供**全部**模型相关默认值" ⇒ 须写明边界 —— **固有事实**由注册表提供，**超时/重试等策略值**住 `settings`。
- `hybrid-retrieval`：**ADDED**（路级失败降级）。现有「融合结果的来源可辨」只覆盖"某一路**返回空结果**（缺席）"，**不覆盖"抛错 / 失败"** ⇒ 须新增 requirement；这也正是 `asyncio.gather(…)` 未带 `return_exceptions` 的规格缺口（`src/rag/retrieval.py:86`）。

> **已定（design D9）**：**不引入进程内模型 fallback**，抖动与长时不可用两个尺度都覆盖 —— 抖动 → 重试（仅可重试类）；长时不可用 → **(A) 快速失败 + 明确告知（检索原文兜底）+ 告警 + 人工部署级切换**（用户 2026-10-16 拍板，依据：降级产物可为"检索原文 + 告知"、且分钟级可人工介入切换）。"(B) 进程内换 provider" 已明确否决，其可接受形态 (C) 留作将来 SLO 收紧时的路径。
> **另附一个必修项（2026-10-16 按代码复核后修正）**：上游**可用性类错误（403 额度耗尽 / 429 穷尽）未被识别**（全仓 401/403 字样均属我方 API 鉴权）。按今日代码：这类错误走 `turn_runner` 的 `except` 分支（**不落 assistant 行** + 发 `error` 事件、**不发 `done`**），前端显示**通用文案"连接异常，请重试"并在鼓励重试**（重试必然再失败）⇒ 需按错误类别给可区分文案。且**运维侧无告警触发源**（无 metrics 端点、应用侧不发告警；`src/api/alerts.py` 是入站）⇒ **D9-(A) 的"分钟级可介入"前提目前不成立，须先补出站触发源**。此项独立于 fallback 决策。

## 受影响用户与成功度量

**受影响用户**：① **终端用户**（站点 / 企微）—— 今天在依赖挂死或额度耗尽时可能长时间无响应、或拿到空回答/误导性的"请重试"；② **运维**（唯一能执行"部署级切换"的人）；③ **评测使用者** —— 评测的选手侧用的就是生产模型构造，超时形状变化会传导到评测（故 D14 只跟随形状、不跟随数值）。

**成功度量**（可当场核对，不靠感觉）：

| 度量 | 阈值 / 判据 |
|---|---|
| 挂死在"隐式 600s + SDK 2 次"的调用 | 归零：任一角色客户端均有**显式**超时，且 SDK `max_retries=0` |
| 主链路抖动类失败重试能力 | **不得退化**：关 SDK 重试的同时本层有界重试到位（"不得只关不加"，见 D16） |
| 单路失败时的检索可用性 | 稠密路失败而词法路可用 ⇒ 仍返回结果（当前是整次抛错） |
| 降级对用户可见 | 每次降级都有事件（含依赖名 + 原因）与（按取值集合）用户文案 |
| 可用性类错误的误导 | 文案**不含**"重试"语义（该类重试必再失败） |
| 数值收敛 | 首版保守值上线后，可用**已留痕**的耗时数据回填 `design.md` D11 取值表的"待校准"项 |

> **说明**：D11 把"分位校准"推到上线后，故本表**不设**"p95 低于 X 秒"这类需要基线数据的指标 —— 那类指标在首版无法判定，写了就是假验收。

## Impact

- **代码**：`src/infra/llm/provider/factory.py`（三个 `build_*` + `build_chat` 的 `max_retries`）、`src/models.py`（`with_retry` 迁出/改造为无状态原语）、`src/rag/retrieval.py`（gather 语义 + 重试调用点）、`src/agents/tools/rag_tools.py`（rerank 降级与事件）、`src/infra/db/vector_store/pg_store.py`（embedding 客户端复用 + 超时落点）、`src/services/document_service.py`（入库侧 embedding 超时）、`src/agents/graph/*`（主循环 idle 保险丝）、`src/core/log_event_specs.py` + `src/config/const.py`（事件与文案出口）。
- **前端（上一版漏列，评审 I2 —— 本仓要求"接口/行为变化须同步前端消费链"）**：`deploy/nginx/html/chat.html` 的 `error` 分支现**硬编码** `showError('连接异常，请重试')` 且**不消费** `data.error` ⇒ **文案差异化必须同步改前端**；`generation_idle` 若要呈现给用户，还须先定**SSE 事件通道**（现无对应事件类型）。
- **配置**：新增角色级超时/重试 env；`.env.template` 与 `.env.example` **两份都要改**（本仓约定）。
- **文档**：`docs/agents/best-practices.md` §11（每层超时 / 不重试所有错误 / jitter / 降级后告知用户 四行状态）、`requirements_pool.md`（A-03 / A-05 现状与依赖）、若引入新 env 则 `docs/agents/deploy-runbook.md`。
- **风险**：`main` 是**流式**，一旦开始输出就不能重试（会重复输出）⇒ 重试窗口只在首 token 之前；a-fix 会影响评测的**选手侧**（评测用 `get_llm()` 即生产模型），需在 design 里定"评测是否跟随生产取值"。
