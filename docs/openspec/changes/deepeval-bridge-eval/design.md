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

**本次的执行模型（与现状对照，先读这一节）**

DeepEval 有**四种执行入口**：`evaluate([tc],[m])`（批量事后，官方明说不过门禁）、`assert_test()` + `deepeval test run`（pytest 形态，过门禁）、`dataset.evals_iterator(metrics=[...])`（数据集驱动，与 agent 运行交错）、`CallbackHandler(metrics=[...])`（挂 LangChain/LangGraph，**运行中 inline 评**）。

本 change 采用**混合式**，**不**走 `assert_test`（门禁自建，见 D12）也不走 `evals_iterator`（已有自己的逐题循环与 trace 体系）：

```
             现状（RAGAS 现代栈）                本次（DeepEval）
出题          --generate（已下线，代码保留）        不变
────────────────────────────────────────────────────────────────
① Agent 跑题  graph.ainvoke 逐题                  graph.ainvoke 逐题
              每题一个 eval_<hex>                 ＋ config.callbacks 挂 CallbackHandler
                                                  ⇒【行为轴在此 inline 产出】
────────────────────────────────────────────────────────────────
② 裁判        第二遍：5 指标逐行 ascore            第二遍：**只剩四指标**（桥 measure + to_thread）
              行级并发 5                          ⚠️ 行为指标**不在这一遍**
────────────────────────────────────────────────────────────────
③ 报告/落库/回写/门禁   不变                       不变（＋口径后缀、NaN 阈值）
```

⇒ **"跑两遍"这个心智模型要改**：行为轴是**inline**（挂图上、跑题时就评完），只有四指标是 **post-hoc**。

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
- **不做出题迁移**：本次**不**把出题迁到 DeepEval `Synthesizer`（**前迁路径已在 D8 定，另起 change**）；本次评测输入 = **固定的既有版本文件**（v5 / 21 条）。评测与出题解耦后，固定测试集反而是跨轮比对的前提。
- **不保留 0.4.x 现代栈的任何调用面**（不做双栈并行）。
- **不改** Agent 图、生产链路、`kb_eval` API 形状、前端。

## Decisions

### D1 环境与依赖：弃用 0.4.x 恰好解掉一个依赖死结

- **事实 A**：`deepeval 4.2.9 + ragas 0.4.3` **不可解析**（`ResolutionImpossible`）。链路：`ragas 0.4.3 → datasets → huggingface_hub(1.27.0) → click>=8.4.2`，而 `deepeval → click<8.4.0`，**交集为空**。
- **事实 B**：`deepeval 4.2.9 + ragas 0.3.9` **可解析**（落到 `click-8.3.3` / `huggingface_hub-1.16.1`，`pip check` 无新增冲突）。**实测已在项目 venv 上装成功**。
- **决策**：项目内 ragas 固定 **0.3.x**（与 deepeval 共存），并在 `pyproject.toml` 显式记录 `click` / `huggingface_hub` 的既有下限，避免下次解析又飘回 8.4.2 一侧。
- **运行期环境变量（必设，官方明文）**：`DEEPEVAL_DISABLE_DOTENV=1` —— DeepEval **import 时即自动加载** `.env` / `.env.local` 并写入 process env，会污染本项目的配置解析；`DEEPEVAL_TELEMETRY_OPT_OUT=1` —— 否则 `posthog` 遥测开启。两者均在官方 `environment-variables` 文档与源码中明写。
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

### D6 Agent 行为轴（DeepEval 原生；**轨迹必须走官方 `CallbackHandler`**）

- 候选：`task_completion` / `step_efficiency` / `plan_adherence` / `plan_quality` / `tool_correctness` / `argument_correctness` / **`agent_loop_detection`**（直接对口已登记的"Agent 打转、重复同一 query"）。
- 选型约束：**指标总数控制在 ≤5**（DeepEval 官方 "Choosing Your Metrics" 建议），先上 2–3 个高价值项，其余按需再加。
- **轨迹接入（已按官方模型修正，推翻了本决策的原写法）**：DeepEval 的行为指标只吃**它自己的 span 树 trace**，官方**不接受**"我们自行透传 message 列表"。被支持的形态是把官方 `CallbackHandler` 挂到图的 `config.callbacks`（官方示例原样）：

  ```python
  from deepeval.integrations.langchain import CallbackHandler
  from deepeval.metrics import TaskCompletionMetric

  result = graph.invoke(state, config={"callbacks": [
      CallbackHandler(metrics=[TaskCompletionMetric(strict_mode=True, async_mode=False)])
  ]})
  ```

  对应我们 `src/cli/eval_ragas.py` 的 `graph.ainvoke`。
  ⇒ **原写法作废**：「把 `final_state["messages"]` 透传到评测侧再自行构造轨迹」**不成立**；`summarize_trajectory`（`src/cli/eval_scoring.py:136-154`）**不得**作为行为指标的数据源（仍可作报告里的诊断摘要）。
- **`Golden` / `expected_output` 在基本路径上不需要**：官方示例就是"挂 handler + invoke"，不走 `evals_iterator`（那是数据集批量跑的路径）。
- **⚠️ 静默失败守卫（必做）**：三个行为指标均声明 `requires_trace = True`；其中 **`agent_loop_detection` 在无 trace 时源码直接置 `0.0`**、**不报错**。评测 SHALL 在打分前**断言 trace 非空**，否则 **fail-loud** —— 否则行为轴会安静地全是 0 而无人察觉（与 ragas 的 NaN 属**同一类静默假分数**）。
- **同进程前提**：该形态要求图与 `deepeval` **同环境**。本决策因 D1 的"单环境"（`ragas 0.3.9` 与 `deepeval` 同装一个 venv）而成立；若按早先"双环境"设想则**不可行**。PoC 的 K6 已实测该形态 WORKS（`CallbackHandler` 经 `config={"callbacks":[...]}` 捕获到含 `toolSpans` 的轨迹）。
- **待验项（落 tasks，不阻塞设计）**：官方示例用**同步 `invoke` + `async_mode=False`**，而我们用 **`ainvoke`**；异步路径下 handler 是否同样可用、`async_mode` 该取何值，实施时先验。

### D7 主分口径与门禁：换尺子 → 阈值必须重标定

- 历史四轮基线（faithfulness=0.7813 / cp=0.5004 等）与本次**不同尺子**，**不得作为对照**。
- 门禁阈值 `faithfulness≥0.85` / `context_precision≥0.80` / `context_recall≥0.70` / `answer_relevancy≥0.85` 全部**待重标定**（重跑首轮后按分布取）。
- 主分聚合仍限 `pure_kb` 样本，但需在 **NaN 与 empty 两种跳过语义**下重写（见 D4）。
- **门禁形态：保留自建 `--gate` + 单一阈值常量**（**有意偏离**官方推荐）。官方路径是 `assert_test()` + `pytest` + `deepeval test run`；我们保留自建，理由与代价见 D12。
- **并发模型：用 `asyncio.to_thread` 在外部包裹桥的同步 `measure()`**（**有意偏离**）。官方 Python 并发路径是 `a_measure()` + `gather`，或 `evaluate(..., async_config=AsyncConfig(max_concurrent=N))`；但**桥的四个度量 `a_measure` 只是 `return self.measure(...)`（伪异步）**，官方 async 路径对桥无效 ⇒ 只能用线程池变通。理由与代价见 D12。

### D8 出题路：代码保留、调用下线

- `src/cli/eval_ragas_generate.py` **原样保留**，但不再被调用；其依赖的 0.4.x `default_transforms_for_prechunked` 随之不再是依赖需求。
- **风险（明写）**：这是"保留的死代码"。**但重启路径不是"回退 ragas 0.4.x"，而是"前迁到 DeepEval 自带出题器"**——见下。
- **测试集来源（本次）**：评测输入 = **固定版本文件**（既有 `data/ragas/testset/testset_<kb>_v5.json`，21 条）。**评测与出题解耦**是本 change 的直接后果，也是好事：**固定测试集才能跨轮比对**（台账要求）。
- **出题的前迁路径（本次不做，登记为后续出口）**：DeepEval 自带 `deepeval/synthesizer/`，接口对口：
  ```python
  Synthesizer(...).generate_goldens_from_docs(document_paths, include_expected_output=True, ...) -> List[Golden]
  ```
  `Golden` 的 `input` / `expected_output` / `retrieval_context` **正是评测要的字段** ⇒ 出题与评测**同栈、同数据结构**，无需为出题单独维护 ragas 0.4.x。
  **待办/代价**（前迁时需处理）：① 我们自建的「题面自足门」（`src/cli/eval_self_contained.py`）与出题器解耦，**可直接带过去**，但选题型的多样性（原 ragas 的 multi-hop/abstract 分布）需要重设；② 生成物 schema 与现有 testset JSON 不同，需一次转换；③ 出题依赖 embedding 构造上下文（我们已有）。
- **v6 接手点（写死，避免无限推迟）**：
  - **承接方**：**下一个 change**（建议名 `deepeval-synthesizer-testset`，起于本 change 归档之后）。
  - **入口条件（三条，缺一不可）**：① 本 change 已归档且**新尺子的基线与阈值已标定**；② 台账已记入 `bridge-0.3.9 / v5` 一行；③ v6 生成后**与 v5 并存**（v5 不删，它是唯一的跨栈锚点）。
  - **验收形态（两步、不可颠倒）**：第 1 步 `v5 + DeepEval 尺子` → 新基线；第 2 步 `v6 + DeepEval 尺子` → 换卷子的对照。**台账记两行**（口径版本分别为 `bridge-0.3.9 / v5` 与 `bridge-0.3.9 / v6`）。
  - **为什么不可颠倒**：本 change 已换"尺子"，若同轮再换"卷子"，分数变化有**两个可能原因**、永远无法归因；分两步则**每次只动一个变量**。
  - **能力差异（明写，避免期待错位）**：ragas 出题器基于**知识图谱**（有 `multi_hop_abstract` 跨文档题），DeepEval Synthesizer 基于**文档分块 + 演化**（跨文档能力弱），但它**自带** `filtration_config`（critic 过滤低质题）、`evolution_config`（加难）、`styling_config`（风格/自足控制）——**这三样是 ragas 侧没有、而我们当时靠自建门补的**。v5 里真正的跨公司题只有 1 条，故该能力损失实际很小。
- **硬护栏（必做）**：仅靠文件头注释挡不住误用，且该路径现在**必然报错**（`default_transforms_for_prechunked` 在 0.3.x 不存在）。⇒ CLI 的 `--generate` 路径 SHALL **显式失败**并给出指引：说明原因、给出**前迁路径**（DeepEval `Synthesizer`）与"若确需暂用旧路，须临时环境 + ragas 0.4.x"，SHALL NOT 让用户撞一个裸 `ImportError`。

### D9 Langfuse 回写与台账/报告（DeepEval **无**官方 Langfuse 集成）

- **事实**：DeepEval **没有** Langfuse 集成（其 Integrations 清单不含 Langfuse）；Langfuse 侧那份官方配方用的是 **v3/v4 API**（`from langfuse import get_client` / `api.observations.get_many` / `LANGFUSE_BASE_URL`），与**本项目自托管 v2 不匹配**；DeepEval 的 tracing 也**没有** OTLP / Langfuse 导出。⇒ **必须由我们在 DeepEval 之外手写回写**。
- **通道与算分框架解耦（关键澄清，避免误以为要重建）**：这条通道**从来不是 RAGAS 提供的**——三个部件都属我们：① 开关 `configure_tracing()` → `langfuse_context.configure(enabled=LANGFUSE_ENABLE)`（`src/infra/llm/tracing.py:48-54`）；② trace id `f"eval_{uuid4().hex[:12]}"`（`eval_ragas.py:287`）经 `@observe` 以 `langfuse_observation_id` 落 trace；③ 回写 `write_scores()` → `client.score(...)`（`eval_scoring.py:157-203`）。**RAGAS 只在"被评的一侧"**。⇒ 换 DeepEval 后**通道零改动**，只换 metric 名与加口径后缀。
- **边界①：DeepEval 的轨迹（span 树）不会出现在 Langfuse 上**——它只在本进程内存（或发 Confident AI），DeepEval **没有 Langfuse 导出**。⇒ **分数可回写到 `eval_<hex>` trace，但行为指标的轨迹在 Langfuse 里看不到**，只能看报告 / 台账 / 本地日志。
- **边界②：不会有两个 handler 打架**——核实：本项目**只**用 `@observe` 装饰器，`graph.ainvoke(...)` **当前未传 `config=`**、未挂任何 LangChain callback ⇒ 加 DeepEval `CallbackHandler` 是**唯一的 callback**。
- **复用现有通道**：沿用 `src/cli/eval_scoring.py:157-203` 的 `write_scores` —— 客户端取 `langfuse_context.client_instance`（**不得** `new Langfuse()`：会绕过 `LANGFUSE_ENABLE` 开关与关停 flush）；逐条 `client.score(trace_id=…, name=…, value=…)`；失败只记 warning、不阻断评测。
- **行为轴的分数也回写**（与四项同级、同一个 `trace_id`）：收益是"一个 trace 上能看到该题的全部口径"，排障不用两地对照。
- **NaN 的 score 语义 = 不写**（维持现状：`pd.isna(value)` → 跳过）。理由：Langfuse v2 的 score 是**数值型**，用 `0` 代 NaN 会把"**裁判坏了**"与"**真得 0 分**"混起来——这正是本次整改一路在纠的那类错；"缺失 + 报告计数"语义干净。**该语义必须在此明写**，否则后人会当 bug 修。
- **新旧口径共存：新 score 名带口径后缀**（如 `faithfulness@bridge-0.3.x`），SHALL NOT 同名覆盖。理由：同名覆盖会让**同一 trace 上旧值消失**、可比性丢失；加后缀可新旧并存，与 D9 台账的"口径版本"列对齐。（清理旧 score 的做法**不采纳**——历史证据要留。）

- **台账与报告**：`docs/agents/ragas-eval-ledger.md` 增列"口径版本"（modern-0.4.x / bridge-0.3.x），历史行**不改**，新行标注口径。报告头记录：DeepEval 版本、ragas 版本、judge 模型、`model=` 是否显式传入、**NaN 计数**、**trace 是否非空**（对应此前已登记的"报告不落盘裁判配置"缺口）。

### D10 逃生门（非默认路径）

- **触发条件**（任一）：**NaN 比例 > 5%**（21 题即 >1 题）且重试无效；或默认模型/依赖坑在目标环境无法稳定满足。
  - **阈值取 5% 的理由**：实测裁判偶发退化是**稀有事件**（全量 21 题里出现 1 例）；阈值过严会让门禁天天红、过松则失去意义。
  - **判定机制**：由评测运行**自动判定** NaN 率并在报告中给出「**建议启用逃生门**」提示；**是否真启用由人决定**，启用须在台账记录原因与生效轮次。
- **同一阈值用于门禁**：主分排除 NaN 之后，**NaN 率 ≤5% 视为可接受**（不计失败）；**>5% 则门禁 fail**。这补上了风险 R1 里"NaN 进发布门禁无法判定"那个洞。
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

### D12 对官方推荐路径的两处**有意偏离**（按 `selection-baseline.md` 的偏离纪律记明）

> 纪律要求："凡有意偏离基线，SHALL 写下偏离理由与代价"。此处基线的上位来源是 **DeepEval 官方推荐**。

| # | 官方路径 | 我们的做法 | 偏离理由 | 接受的代价 |
|---|---|---|---|---|
| 1 | 门禁 = `assert_test()` + `pytest` + `deepeval test run` | 保留自建 `--gate` + 单一阈值常量 | 门禁是**我们的发行口径**，要和台账/落库共用同一套阈值常量；换成 `test run` 需改测试组织形态并动 CI 结构，收益远小于代价（官方亦明说 `evaluate()` **不**过门禁） | 用不上 DeepEval 的 UI 与回归对比能力；阈值维护在我们自己手里 |
| 2 | 并发 = `a_measure()` + `gather`，或 `async_config.max_concurrent` | `asyncio.to_thread` 外部包裹同步 `measure()` | **桥的 `a_measure` 是伪异步**（`deepeval/metrics/ragas.py` 内 `return self.measure(...)`）⇒ 官方 async 路径对桥**无效** | 多一层线程池；并发度需自行约束（对齐现有行级并发 5） |

**另记两处"官方无对应"、纯我们自定的口径**（不属偏离，但避免后人误当官方）：

- **NaN 处置**（排除均分 + 单独计数 + 阈值 5%）——官方只解释 NaN 的**来源**，未提供任何处置机制。
- **细粒度超时 / 关 SDK 重试 / 关思考**——机制属 langchain `ChatOpenAI` 的字段（`http_client` / `max_retries` / `extra_body`），DeepEval 文档全仓未提；这是**我们的既有收益**（ADR-0024），换入口后靠承载点换位保住。

### D13 框架内部调用的可观测性：把 `http_client` 变成"请求级日志出口"

- **问题（trace 覆盖的缺口）**：换桥后，"我们自己发起的裁判调用"仍可带 `trace_id`（沿用现有 `judge call` 事件的形态），但**框架内部的 LLM 调用默认完全不可见**——包括 ragas 内部的重试、`answer_relevancy` 的 3 次生成、以及 DeepEval 行为指标的 judge 调用。**而这些正是我们追了两天的根因所在**（退化输出、超时、NaN 全发生在这一层）。
- **做法**：我们**本来就要**给 `ChatOpenAI` 传 `http_client`（细粒度超时，见 D11）；httpx 支持 **event hooks**（`event_hooks={"request": [...], "response": [...]}`）⇒ 在同一个 client 上挂**请求级日志**（模型 / 耗时 / 状态码 / 请求体摘要），把框架内部调用也纳入 `trace_id` 序列，并与既有工具 `src/cli/trace_logs.py` 配合使用。
- **待验（落 tasks）**：裁判走 `asyncio.to_thread`，**contextvar 是否随线程传递**需实测；若不传递，则显式把 `trace_id` 塞进 client 闭包。
- **边界（明写）**：行为指标的 **DeepEval span 树**仍不会进日志（那需要 DeepEval 自身导出，见 D9 边界①）。本决策只解决"**LLM 请求级**"的可见性，**不**解决"轨迹"的可见性。
- **收益**：异常时可用 `trace_id` 直接看到"框架到底发出了什么、返回了什么"——这正是本次整改中"靠 trace 才找到根因"那条经验的制度化。

## Risks / Trade-offs

| # | 风险 | 说明 | 应对 |
|---|---|---|---|
| R1 | **NaN 进发布门禁无法判定** | 桥原样透传 ragas 的 NaN，门禁里"NaN 算 pass 还是 fail"无意义 | **已收敛**：主分排除 NaN + 单独计数 + **NaN 率 ≤5% 视为可接受、>5% 门禁 fail**，超限走 D10 逃生门（见 D10） |
| R2 | **版本耦合不对称** | 桥绑 ragas 版本；ragas 0.3→0.4 就动过 API，桥可能随之断 | 固定 ragas 0.3.x；升级前用"新旧分数对比"验证（桥正好可当一次性的校验工具） |
| R3 | **官方不推荐桥** | DeepEval 明说优先用其原生 RAG 指标 | 明写为"接受的代价"；若未来原生四指标足够，再评估收编 |
| R4 | **生态无生产参考** | GitHub 上除官方（18.9k★）外，第二名仅 173★，agentic 轨迹类最高 19★ | 自担设计风险；先小样本（1 题 → 5 题 → 全量）逐级验证 |
| R5 | **温度不可控** | 三项 0.01、`answer_relevancy` 0.3，且我们改不了 | 明写；`answer_relevancy` 的分数波动按分布看待，不按点值判门禁 |
| R6 | **口径变化被误当"分数回归"** | 换尺子后四个指标的分数都会动 | 台账加口径版本列；首轮重跑只用于**标定阈值**，不与历史比 |
| R7 | **喂法未修，context 轴仍偏低** | 本次不解决"多轮并集当单轮 `c(q)`" | 显式写成 Non-Goal；context 轴的根治（per-step 单轮化）另行跟踪 |
| R8 | **出题路死代码** | 保留但不再调用 | 在文件头与台账标注"保留未调用"；重启需先解决 0.4.x 依赖；`--generate` 加**硬护栏**（见 D8） |
| R9 | **行为轴的轨迹取材（最高风险，已修正）** | 原设想"透传 `messages` 自行构造"**不被 DeepEval 接受**——它只吃自己的 span 树 trace | 改用**官方 `CallbackHandler` 挂 `config.callbacks`**（官方唯一被支持路径，PoC K6 已实测）；并加**"trace 非空"断言 fail-loud**（`agent_loop_detection` 无 trace 会静默 `0.0`）；异步路径（我们用 `ainvoke`）待验 |
| R10 | **`CallbackHandler` 对被测图有侵入** | 评测运行时图会多挂一层 handler | 仅在**评测路径**挂（生产链路不动）；handler 开销与 `ainvoke` 兼容性在 1 题冒烟时先测 |
