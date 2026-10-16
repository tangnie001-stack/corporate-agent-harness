## Context

**现状（本 change 的起点）**

- 评测打分在 `src/cli/eval_ragas.py` 的 `run_evaluation`：现代 RAGAS `@experiment` + 逐行 `ascore`，指标由 `src/cli/eval_scoring.py:23-47` 的 `build_metrics()` 用 `ragas.metrics.collections` + `llm_factory` 构造（五指标：faithfulness / answer_relevancy / context_precision / context_recall / answer_correctness）。
- 被测对象是**多轮 LangGraph Agent**（`src/agents/graph/workflow.py`，会多次调 `retrieve_kb` / `search_web`）。喂给裁判的 context 取自 `src/cli/eval_ragas.py:164`：`final_state["tool_contexts"]` **全部累积**、不跨 query 去重、不重排。
- 依赖：`pyproject.toml` pin `ragas==0.4.3` / `datasets==4.0.0` / `langchain*` 精确版本；`click` 实测 **8.4.2**，`huggingface_hub` 实测 **1.27.0**。
- 已确认的既有问题（本次的动机，均有实测/源码证据）：
  - context_precision 被喂法系统性压低（`ctx≥10` 的题均值 ≈0.17，`ctx=5` ≈0.73）；
  - context_precision 不可复现（同题面跨配置 1.0→0.0），且存在 `recall=0.875` 与 `precision=0.0` 并存的判错行（Q13）。

**本次采纳的外部事实（均已一手核对，附录见 design 引用）**

- DeepEval 的 RAGAS 桥是**薄适配器**：`deepeval/metrics/ragas.py` 把 `LLMTestCase` 包成一行 `datasets.Dataset`，调 `ragas.evaluate(dataset, metrics=[<legacy ragas 指标>], llm=<langchain BaseChatModel>)`，取 `scores[name][0]`。
- 桥的默认 `model="gpt-3.5-turbo"`（该模型已停服，DeepEval issue #3404）；`embeddings` 仅 `RAGASAnswerRelevancyMetric` 需要。
- 桥对 ragas 的**版本要求仅是 `>=0.2.1`**（字符串比较，`import_ragas()`）。
- 官方 FAQ 明说：**推荐 DeepEval 原生 RAG 指标**；桥的 `NaN` 来源是"ragas 在模型输出非法 JSON 时返回 NaN，桥原样透传"。
- 温度由 ragas 内部决定：`get_temperature(n) = 0.3 if n>1 else 0.01`（`ragas/llms/base.py:71-73`），`LangchainLLMWrapper` 会**改写底层模型的 temperature 属性**（同步路径 `:281` 尊重 `bypass_temperature`，**异步路径 `:229` 漏判**）。

## Goals / Non-Goals

**Goals:**

1. **评测入口统一到 DeepEval**，一个入口承载"上下文轴 + Agent 行为轴"。
2. **RAGAS 指标改经桥调用，采用经典四指标**（ContextualPrecision / ContextualRecall / AnswerRelevancy / Faithfulness）。
3. **彻底弃用 RAGAS 0.4.x 现代栈**，项目内 ragas 下移到 0.3.x。
4. 保留一条**可切换的逃生门**（桥不可用时改"DeepEval 采样 + 单独跑 ragas 0.3.x"）。

**Non-Goals:**

- **不修"喂法"**：本次**不**把多轮累积 context 改成 per-step 单轮样本。context 轴的根治（样本单轮化）是**独立的样本构造问题**，与选哪个框架正交，另行跟踪。
- **不修检索质量**：跨公司噪声 / 精排分数扁平（需求池 F-44）不在本次范围。
- **不补 `answer_correctness` 的替代**：该项直接退出评测。
- **不保留 0.4.x 现代栈的任何调用面**（不做双栈并行）。
- **不改** Agent 图、生产链路、`kb_eval` API 形状、前端。

## Decisions

### D1 环境与依赖：弃用 0.4.x 恰好解掉一个依赖死结

- **事实 A**：`deepeval 4.2.9 + ragas 0.4.3` **不可解析**（`ResolutionImpossible`）。链路：`ragas 0.4.3 → datasets → huggingface_hub(1.27.0) → click>=8.4.2`，而 `deepeval → click<8.4.0`，**交集为空**。
- **事实 B**：`deepeval 4.2.9 + ragas 0.3.9` **可解析**（落到 `click-8.3.3` / `huggingface_hub-1.16.1`，`pip check` 无新增冲突）。**实测已在项目 venv 上装成功**。
- **决策**：项目内 ragas 固定 **0.3.x**（与 deepeval 共存），并在 `pyproject.toml` 显式记录 `click` / `huggingface_hub` 的既有下限，避免下次解析又飘回 8.4.2 一侧。
- **代价**：这是一次**不可逆的依赖下移**（0.4.x 起 `ragas.testset.transforms.default.default_transforms_for_prechunked` 不存在，见 D8）。

### D2 入口与调用面：用桥的四个"单项"，不用聚合

- 桥内共 6 个类：`RAGASContextualPrecisionMetric` / `RAGASContextualRecallMetric` / `RAGASContextualEntitiesRecall` / `RAGASAnswerRelevancyMetric` / `RAGASFaithfulnessMetric` / 聚合 `RagasMetric`（`deepeval/metrics/ragas.py:38/115/186/334/414/484`）。
- **必填四字段**：`input` / `actual_output` / `expected_output` / `retrieval_context`（官方 Required Arguments）。
- **决策**：**只用经典四个单项**，不用 `ContextualEntitiesRecall`，也不用 `RagasMetric` 聚合。理由：聚合在本地 4.3.0 源码里是 **5 项算术平均**（含 entity recall，`ragas.py:521-541`），而官方文档写的是 4 项——**文档与源码不一致**，用聚合会把口径绑在一个未对齐的实现上；单项更可控、更易诊断。

### D3 裁判与温度：接受 ragas 的分档温度（本项为公开接受的代价）

- 裁判构造：`OpenAIModel(model="<judge>", api_key=<DASHSCOPE>, base_url=https://dashscope.aliyuncs.com/compatible-mode/v1)`。
- **温度不由我们决定**，由 ragas 按 `n` 分档：
  - `faithfulness` / `context_precision` / `context_recall`（`n=1`）→ **0.01**；
  - **`answer_relevancy` → 0.3**（legacy `_answer_relevance.py:142-143` 传 `n=self.strictness`，`strictness` 默认 **3**；`generate_multiple` 的 ragas-LLM 分支把 `n` 透传，`temperature=None` → `get_temperature(3)=0.3`）。
- **为什么 0.01 是可接受的**：0.01 ≈ 确定性，同时**避开部分供应商对 `temperature=0` 的特殊逻辑**（社区既有此实践）。0.3 对 `answer_relevancy` 是**有意**的——该指标需要"生成多个多样化问题再取均值"，压到 0 会退化。
- **必须写的代价**：温度**不受我们控制**，且**按指标分档**；`answer_relevancy` 那一项的分数稳定性天然低于其余三项。

### D4 两个已知坑（必须显式处理，否则门禁不可判）

1. **默认模型坑**：桥的 `model` 默认是 `"gpt-3.5-turbo"`（6 处），该模型**已停服**（DeepEval issue #3404）。→ **每一个度量构造都必须显式传 `model=`**，并在测试里加断言防止回退。
2. **NaN 坑**：官方 FAQ 原文——*"`ragas` returns `NaN` when the model emits invalid JSON, and `RagasMetric` wraps it directly."* → 桥**不提供 NaN 保护**。设计上要求：
   - NaN **显式可见**（写入报告的 `error`/`skipped` 语义，不得静默参与均分）；
   - 主分聚合**排除 NaN 行**，并单独统计 NaN 计数；
   - 与既有的 `SAMPLE_CLASS_EMPTY` 掩码语义**区分开**（一个是"没检索"，一个是"裁判坏了"）。

### D5 覆盖与丢失（明写，避免以后误读）

| 现在（0.4.x 现代） | 本次（桥 + 经典四指标） |
|---|---|
| faithfulness / answer_relevancy / context_precision / context_recall | ✅ 同名保留 |
| **answer_correctness** | ❌ **退出**（非经典四指标，本次不做替代） |
| —— | ➕ DeepEval 原生 agentic 族（另加，见 D6） |

- 注意两项口径变化：① `answer_relevancy` 走 **0.3 温度 + 3 个生成问题**；② 桥的 `context_precision` 判据是"该 chunk 是否相关"（定位为评 reranker 排序），与 0.4.x 的"是否有助于得到 reference"**不完全等价**。

### D6 Agent 行为轴（DeepEval 原生，本次唯一"净增"能力）

- 候选：`task_completion` / `step_efficiency` / `plan_adherence` / `plan_quality` / `tool_correctness` / `argument_correctness` / **`agent_loop_detection`**（直接对口已登记的"Agent 打转、重复同一 query"）。
- 选型约束：**指标总数控制在 ≤5**（DeepEval 官方"Choosing Your Metrics"建议），先上 2–3 个高价值项，其余按需再加。
- 轨迹来源：现有 `src/cli/eval_scoring.py:136-154` 的 `summarize_trajectory` 只产"工具名+入参+迭代数"**摘要**，不足；需要把 `final_state["messages"]`（含 `AIMessage.tool_calls` 与 `ToolMessage`）**透传**到评测侧。

### D7 主分口径与门禁：换尺子 → 阈值必须重标定

- 历史四轮基线（faithfulness=0.7813 / cp=0.5004 等）与本次**不同尺子**，**不得作为对照**。
- 门禁阈值 `faithfulness≥0.85` / `context_precision≥0.80` / `context_recall≥0.70` / `answer_relevancy≥0.85` 全部**待重标定**（重跑首轮后按分布取）。
- 主分聚合仍限 `pure_kb` 样本，但需在 **NaN 与 empty 两种跳过语义**下重写（见 D4）。

### D8 出题路：代码保留、调用下线

- `src/cli/eval_ragas_generate.py` **原样保留**，但不再被调用；其依赖的 0.4.x `default_transforms_for_prechunked` 随之不再是依赖需求。
- **风险（明写）**：这是"保留的死代码"，将来若重启出题路，需要一并把 ragas 升回 0.4.x 或改写出题实现。决定保留是为了不丢失既有能力与验证经验。

### D9 台账与报告

- `docs/agents/ragas-eval-ledger.md` 增列"口径版本"（modern-0.4.x / bridge-0.3.x），历史行**不改**，新行标注口径。
- 报告头记录：DeepEval 版本、ragas 版本、judge 模型、`model=` 是否显式传入、NaN 计数（对应此前已登记的"报告不落盘裁判配置"缺口）。

### D10 逃生门（非默认路径）

- **触发条件**（任一）：NaN 比例超过设定阈值且重试无效；或默认模型/依赖坑在目标环境无法稳定满足。
- **形态**：DeepEval 只负责**采样与用例构造**（产出 `LLMTestCase` 集合），评分改由**项目侧直接调用 ragas 0.3.x**（legacy `evaluate` 或按需的 `collections`）完成。
- **定位**：**不是常驻架构**，是桥不可用时的迁移/兜底路径；启用需在台账写明原因与生效轮次。

### D11 0.4.x 遗留的处置：**三分——复用 / 删除 / 改写**（不是"删光"）

弃用 0.4.x 不是只改依赖与入口；**为现代栈写的那批代码要逐项落点**，否则会留下悬空引用与失义配置。经全仓扫描，处置如下：

| 遗留物 | 位置 | 处置 | 理由 |
|---|---|---|---|
| `build_ragas_judge()` / `build_ragas_embeddings()` | `src/infra/llm/provider/factory.py:132/169` | **删** | 产出 `InstructorBaseRagasLLM` / 现代 embeddings，桥**类型不接**（桥要 langchain `BaseChatModel` / `Embeddings`） |
| `build_native_client()` | 同上 `:91` | **删（先核引用）** | 为 `llm_factory` / `embedding_factory` 的 `client` 参数而生；无消费方即删 |
| `@experiment()` 编排、`_EvalResult`、`_score_metrics`、`_score_metric_with_retry`、`_exc_chain_summary`、`_judge_failure_note` | `src/cli/eval_ragas.py` | **删/改写** | 桥是**同步** `measure()`；编排改为构造 `LLMTestCase` + 调度量 |
| `build_metrics()`（现代版） | `src/cli/eval_scoring.py:23-47` | **改写** | 换成桥的四个单项 |
| `RAGAS_JUDGE_MAX_TOKENS` / `RAGAS_JUDGE_THINKING` | `src/config/settings.py:189/192` | **复用（改嫁）** | 改嫁到 `ChatOpenAI(max_tokens=…)` / `ChatOpenAI(extra_body={"enable_thinking": …})`。**依据**：mock server 抓包已证实这两项经桥的调用链**透传成功**（`max_completion_tokens: 8192`、`enable_thinking: false`） |
| `RAGAS_HTTP_TIMEOUT_{CONNECT,READ,WRITE,POOL}` / `RAGAS_HTTP_MAX_RETRIES` | 同上 `:168-177` | **复用（承载点换位）** | **不必降级**：已核 `ChatOpenAI` 有 `http_client` 与 `max_retries` 字段 → 用 `ChatOpenAI(http_client=httpx.Client(timeout=httpx.Timeout(connect=10, read=150, write=30, pool=10)), max_retries=0)` **完整保住 ADR-0024 的两项收益**（细粒度超时 + 关 SDK 重试防放大）。`ChatOpenAI` 无独立 `timeout` 字段，超时只能经 `http_client` 承载 |
| `RAGAS_METRIC_MAX_ATTEMPTS` | 同上 `:183` | **复用** | 桥无内置重试，本层有界重试仍需要 |
| `judge call` / `metric retry` 日志事件 | `src/core/log_events.py` + `log_event_specs.py` | **改写** | 埋点位置从"我们调裁判"变成"桥内部调 ragas"；口径要重述 |
| `src/cli/trace_logs.py` | 同上 | **保留并重述适用范围** | 仍可用于按 trace 拉日志，但其"裁判诊断"叙事基础变了 |
| `tests/cli/test_eval_modern_stack.py`、`test_metric_retry.py`、`test_ragas_modern_factories.py`、`test_native_client.py` | `tests/` | **删/重写** | 断言对象已不存在 |
| `tests/infra/llm/provider/test_registry_models.py` | 同上 | **先核消费方再定** | judge 角色绑定若已无消费方需一并处置 |
| **ADR-0024**（RAGAS 裁判 client 政策，含 `temperature=0`） | `docs/adr/0024-…md` | **追加新 ADR 取代** | 仓库约定：ADR 不可变，**正文不改**，但旧 ADR 的 Status 必须加**反向指针**，并更新 ADR 索引表 |

**出题路的例外**：`src/cli/eval_ragas_generate.py` 对 0.4.x 的依赖（`default_transforms_for_prechunked`）**按决策保留**（代码留存、不调用），故"全仓无 0.4.x 引用"**不成立**——自查时应以"**评测路径**无悬空引用"为判据，出题路单独标注。

## Risks / Trade-offs

| # | 风险 | 说明 | 应对 |
|---|---|---|---|
| R1 | **NaN 进发布门禁无法判定** | 桥原样透传 ragas 的 NaN，门禁里"NaN 算 pass 还是 fail"无意义 | 主分排除 NaN + 单独计数 + 阈值以外单列 NaN 率；超限走 D10 逃生门 |
| R2 | **版本耦合不对称** | 桥绑 ragas 版本；ragas 0.3→0.4 就动过 API，桥可能随之断 | 固定 ragas 0.3.x；升级前用"新旧分数对比"验证（桥正好可当一次性的校验工具） |
| R3 | **官方不推荐桥** | DeepEval 明说优先用其原生 RAG 指标 | 明写为"接受的代价"；若未来原生四指标足够，再评估收编 |
| R4 | **生态无生产参考** | GitHub 上除官方（18.9k★）外，第二名仅 173★，agentic 轨迹类最高 19★ | 自担设计风险；先小样本（1 题 → 5 题 → 全量）逐级验证 |
| R5 | **温度不可控** | 三项 0.01、`answer_relevancy` 0.3，且我们改不了 | 明写；`answer_relevancy` 的分数波动按分布看待，不按点值判门禁 |
| R6 | **口径变化被误当"分数回归"** | 换尺子后四个指标的分数都会动 | 台账加口径版本列；首轮重跑只用于**标定阈值**，不与历史比 |
| R7 | **喂法未修，context 轴仍偏低** | 本次不解决"多轮并集当单轮 `c(q)`" | 显式写成 Non-Goal；context 轴的根治（per-step 单轮化）另行跟踪 |
| R8 | **出题路死代码** | 保留但不再调用 | 在文件头与台账标注"保留未调用"；重启需先解决 0.4.x 依赖 |
