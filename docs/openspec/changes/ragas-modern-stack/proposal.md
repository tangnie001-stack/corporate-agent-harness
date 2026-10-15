## Why

现有 RAGAS 用法**整体建在已弃用的 legacy API 上**（实测每次评测打出 7 条 `DeprecationWarning`：5 个指标导入 + 2 个 wrapper），且暴露三个**会产出错误结论**的真问题：

1. **出题链产出歧义题 + 静默丢一路出题器**
   - 手搓 5 个 transforms，**缺 `ThemesExtractor` / `CosineSimilarityBuilder`**（官方 `default_transforms` 有 7 个）→ `MultiHopAbstractQuerySynthesizer` 找不到簇被**静默丢弃**（日志实锤：`Skipping multi_hop_abstract_query_synthesizer ... Cannot form clusters`；v1 测试集实测 **0 条 abstract**）。
   - 出题 prompt（`ragas/testset/synthesizers/single_hop/prompts.py`）**对问题自足性零要求**（只要求「答案忠于上下文」）→ 生成「稀释每股收益本报告期多少」这类**不点公司名**的题 → 被测 Agent **正确地**澄清、单轮评测无应答者 → 兜底倒出全部候选 → 答案/上下文暴涨 → 指标重罚（实测该批 `answer_correctness` 0.36）甚至超时 NaN。
   - **根因（实测）**：题面是否自足由**上下文是否携带公司名**决定——v3 实测 12/21 题面不含公司名，且这 12 条的 `reference_contexts` **全无公司名**（中文年报正文自称「本公司」）；而实体本就在同节点 `document_metadata`（`company`/`report_period`），只是**从未进 prompt**。官方的 `llm_context` 只是 prompt 的**题型建议**软槽位（且官方无改写步），**不足以达成**。

2. **失败静默变 NaN**
   `evaluate(raise_exceptions=False)` 把异常吞成 `nan`。实测两例：① 裁判换 `kimi-k3` 后因 `temperature` 不被支持 → 400 → **5 指标全 nan**；② legacy 5 题跑中 5 个作业 `TimeoutError` → CSV 出现 5 个空格。

3. **白名单两端口径不一致**
   出题只读 `RAGAS_DOC_WHITELIST`（2 份文档），评估却检索**整库**（4 份）（`src/cli/eval_ragas.py:737`）。ADR-0022 决策 C 要求两端同范围，**尚未落地**。

同时上游能力**已整体换代**：`evaluate()` / `aevaluate()` 已弃用 → `@experiment`（`ragas/evaluation.py:448`）；`ragas.metrics` → `ragas.metrics.collections`；`LangchainLLMWrapper` / `LangchainEmbeddingsWrapper` → `llm_factory` / `embedding_factory`。且**现代指标进不了 `evaluate()`**（`evaluation.py:132` 要求 `isinstance(m, Metric)`，而 `collections.*` 不是其子类）——执行层只能**整体换**，没有局部改的中间态。

> 依据：调研落档 `docs/research/2026-10-13-ragas-testset-generation.md`；决策 ADR-0022（文档选择由使用方负责）；活体验证见 `design.md`「证据」节。

## What Changes

- **评估执行内核迁移到现代栈**：`evaluate(dataset=…)` → `@experiment` + `ragas.Dataset`；`from ragas.metrics import …` → `from ragas.metrics.collections import …`；`LangchainLLMWrapper`/`LangchainEmbeddingsWrapper` → `llm_factory`/`embedding_factory`。**指标集不变**（faithfulness / answer_relevancy / context_precision / context_recall / answer_correctness）。
- **裁判策略（新增，D0/D6）**：① **选型判据 = 结构化输出可靠性**（不只看「能调通」）；② **采样** = `temperature=0` 且**不发 `top_p`**（RAGAS 默认 `0.01`/`0.1` 被百炼部分模型直接 400）；③ **超时** = 思考模型须显式给足（实测 30s 内 2/5 超时）；④ 关掉 legacy 路径对裁判温度的**静默覆盖**（`bypass_temperature`）。
- **失败显式可见（D2）**：每行独立 catch，失败记 `error` 字段——**不丢行、不写 NaN**（现代 `@experiment` 默认「丢行 + print」同样是静默）。
- **出题链路整改（D8）**：① 改用官方 `default_transforms`（补回 `ThemesExtractor` + `CosineSimilarityBuilder`，恢复 abstract 出题器）；② **源头锚定**——出题上下文前置 `【公司名 · 报告期】` 头（取自分块元数据）；③ **出题后自足性门**——不自足题改写或丢弃；④ 保留 `llm_context` 作辅助软引导。
- **白名单只约束出题（D7，局部反转 ADR-0022 决策 C）**：评估侧检索**镜像生产**（整库），**不**按白名单收窄（理由见 design D7）。
- **模型版图（D10）**：`judge → qwen3.8-flash`；`main` / `classify` / `summary → qwen3.7-flash`（裁判 ≠ 选手，避免自评偏置）。

## Capabilities

### New Capabilities

（无 —— 本次是重做既有评测能力。）

### Modified Capabilities

- `evaluation-pipeline`（**全部为 ADDED，不改既有 requirement**，详见下方「排序声明」）：
  - ADD「Evaluation execution uses the modern RAGAS stack」：执行入口/指标/客户端 SHALL 均用现代 API，SHALL NOT 用已弃用的 `evaluate()` / `ragas.metrics` / `Langchain*Wrapper`。
  - ADD「Judge client is constructed with an explicit sampling and timeout policy」：采样（`temperature=0`、不发 `top_p`）、超时（显式充足值）、选型判据（结构化输出可靠性 + 活体抽样证据）。
  - ADD「Metric failures are explicit, not silently dropped」：任一指标失败 SHALL 在原样行上记录错误，SHALL NOT 丢弃该行或以 NaN 静默代替。
  - ADD「Evaluation retrieval mirrors production scope」：白名单 SHALL 仅约束出题；评估检索 SHALL 镜像生产（整库），SHALL NOT 人为按出题范围收窄。
  - ADD「Testset generation exercises every query path and yields self-contained questions」：出题 SHALL 用官方 `default_transforms` 全量；题面自足 SHALL 由**源头锚定**（上下文携带公司名/报告期）+ **出题后自足性门**保证。
  - ADD「Reported overall score uses the main-score 口径」：`eval_report.overall_score` SHALL 用**纯 KB 掩码的五项均值**（含 `answer_correctness`）；API SHALL 暴露 `answer_correctness`；通过阈值 SHALL 单一来源。

### 排序声明

本 change 与在途 `ragas-agent-eval` 都触及 `evaluation-pipeline`，但落在**不同 requirement**：`ragas-agent-eval` **MODIFY** 了「QA test pair coverage」「Evaluation report archival」两条；本 change **不改这两条**，全部为 **ADDED**。归档按序 apply 即可，不互相阻塞。

## Impact

- **代码**：`src/cli/eval_ragas.py`（执行内核 + 裁判接线 + 失败语义 + `overall_score` 口径）、`src/cli/eval_ragas_generate.py`（transforms + `llm_context` + **P1 上下文实体锚点**）、**新增出题后自足性门（P2，模块待定）**、`src/infra/llm/provider/`（**新增按角色构造 native `AsyncOpenAI` 的集中入口**，见 design D9；`registry.yaml` 的 `judge`/`main`/`classify`/`summary` 绑定 + `qwen3.7-flash`/`qwen3.8-flash` catalog 定价）、`src/models.py`（裁判/embedding 接线）、`src/api/kb_eval.py`（补 `answer_correctness` + 下发阈值）、`deploy/nginx/html/index.html`（阈值改读下发值）。只读参照 `spec.py`（端点/密钥解析）。**不动**检索栈（`src/rag/retrieval.py` / `src/infra/db/vector_store/` / `src/agents/tools/rag_tools.py`）。
- **文档**：新增 **ADR-0023**（白名单仅约束出题；评估检索镜像生产）并在 ADR-0022 `Status` 加反向指针；`docs/adr/README.md` 索引登记。
- **依赖**：**无新增**（`ragas==0.4.3` 已含现代栈；`instructor 1.15.4` 已在装）。
- **数据**：**无 schema 变更**；`data/ragas/` 目录与测试集版本机制不变。
- **行为**：评测报告新增 `error` 列（**stdout 形状不变**——它是 `src/cli/compare_retrieval.py` 的解析契约）；失败不再伪装成 nan；出题恢复 abstract 题路且**题面 100% 自足**（点名公司+报告期）；**`eval_report.overall_score` 改为主分口径**（纯 KB 五项均值，前端展示值随之变化），前端通过阈值改由 API 下发而非硬编码。
- **连带面（D10）**：`judge` 变更**同时影响出题裁判**（`eval_ragas_generate.py:339` 与评估共用 `get_judge_llm`）；`main`/`classify`/`summary` 降级**同时影响回答生成 + fork 子代理 + 文档分类 + 历史摘要**（生产调用点：`agent_service.py:813` / `agents/skills/executor.py:443` / `document_service.py:372` / `chat/history_summary.py:110`）。
- **不改**：`evaluation-pipeline` 的「Evaluation passes without crash on empty KB」「NLI context uses shared entity rendering」「Current date in evaluation context」三条既有条款；`eval_report` 表 / API / 前端。
