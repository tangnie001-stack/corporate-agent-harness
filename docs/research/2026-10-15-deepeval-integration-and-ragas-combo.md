# DeepEval 接入方式、与 RAGAS 的结合、以及多框架并用成本（外部事实调研）

- **调研日期**：2026-10-15
- **调研方式**：Firecrawl 深度检索（全网搜索 + 官网抓取 + GitHub 索引，Thorough 档）+ 本地 `pyproject.toml` 实读 + PyPI 元数据核对；四个并行 worker 按角度取证（DeepEval 官方接入 / Langfuse 对接与结合实例 / GitHub 项目与 star / 依赖成本）
- **范围**：在一个「多轮 LangGraph Agent 的 RAG 系统 + 自托管 Langfuse v2 + OpenAI 兼容裁判（百炼）」的语境下，回答三件事：① DeepEval 官网的接入方式与最佳实践；② RAGAS 与 DeepEval 如何结合；③ 多框架并用的真实成本
- **本文件性质**：**冻结记录**，只回答"外面是什么样"，不随本项目代码回写。决策归 `docs/adr/`

> **证据强度标注约定**：`[官方明确]`（官方文档能引原句）/ `[官方源码]`（官方仓库源码可复核）/ `[权威第三方]` / `[社区经验·非权威]` / `[本文推断]`。
> **前置结论**（不再重复论证）见 `docs/research/2026-10-15-ragas-context-and-failure-modes.md` 与 `docs/research/2026-10-15-agentic-rag-context-convention.md`：RAGAS 的单轮 RAG context 指标用在多轮 Agent 上属**范式错配**，官方 agentic 示例**显式回避** context 指标。

---

## Executive Summary

1. **DeepEval 是可控成本引入的，但接法有版本陷阱。** 官网已从 `docs.confident-ai.com` 迁到 **`deepeval.com`**，当前 Python 版 **v4.2.9**（release 2026-10-02）；4.x 的 API 与网上绝大多数老教程（1.x/2.x 的 `LLMTestCaseParams`、`deepeval test run --metrics`）**不兼容**。接自定义裁判（百炼 OpenAI 兼容端点）有官方一等参数：`OpenAIModel(base_url=..., temperature=...)`。[官方明确]
2. **DeepEval 与 LangGraph 有官方原生集成**，`deepeval.integrations.langchain.CallbackHandler` 直接挂到 `graph.invoke(config={"callbacks":[...]})`，是四路材料中**与本项目最直接对口**的一条。[官方明确]
3. **DeepEval × Langfuse v2 没有官方集成**；官方那份 DeepEval-Langfuse 配方是 **Langfuse v3/v4 API**，与我们的 v2 不匹配。可行路径是**复用我们已有的 RAGAS→v2 回写通道**（`langfuse.score(trace_id, name, value, comment)`）。[官方明确 + 本文推断]
4. **「RAGAS + DeepEval 怎么结合」的官方答案是"用 DeepEval 作统一入口、内嵌 RAGAS 指标"**：DeepEval 自带 `RAGASMetric`（= answer relevancy / faithfulness / contextual precision / contextual recall 四项均值）与 4 个单项包装 metric，**底层直接调 ragas 库**。第三方则**没有**成熟可抄的组合仓库。[官方源码 + 官方明确]
5. **但"两个框架各自独立跑、再合并结果"没有任何成熟先例，且官方口径是反对的**：DeepEval 官方博客自述「keep one framework as the source of truth for release quality」；MLflow 直言多框架并用「managing each library's own interface, data format, and result storage quickly becomes a mess」。[官方明确 + 权威第三方]
6. **一条硬约束**：`ragas[tracing]` 需要 `langfuse>=3.2.4`，而本项目 pin `langfuse>=2.60,<3.0` → **依赖解析必失败**，RAGAS 的自动 tracing 集成本项目不可用（只能手写回写）。[官方源: PyPI]
7. **成本量级**：若真并跑，裁判调用**近似翻倍**，判分口径重复维护，CI 门禁需额外仲裁；但 DeepEval 的 `DisplayConfig(results_folder=...)` 会落含**逐条分数 + reason + hyperparameters** 的 `test_run_*.json`，**正好补上我们台账里"报告不落盘裁判配置"的缺口**。[官方明确]

---

## Key Findings

### 一、DeepEval 的接入方式（官网）

1. **本地可跑，login 非必需。** `[官方明确]` getting-started FAQ：「Do I need Confident AI for this quickstart? **No. DeepEval runs locally. Confident AI is optional**」；CI 页 FAQ：「Do I need a Confident AI account to run evals in CI/CD? **No. Provide an LLM judge key such as `OPENAI_API_KEY` and your tests run entirely locally**」。
2. **两种形态**：`assert_test(tc, [m])` + `deepeval test run test_x.py`（**会门禁**）；或脚本形态 `evaluate([tc], [m])`（**只收集、不卡 CI**——原句「Unit testing is built for CI/CD… `evaluate()` is for scripts and notebooks and collects results without failing anything」）。`[官方明确]`
3. **接自定义裁判（关键，可照抄）** `[官方明确]`：
   ```python
   from deepeval.models import OpenAIModel
   judge = OpenAIModel(
       model="<model>",
       api_key="<DASHSCOPE_KEY>",
       base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
       temperature=0,
       generation_kwargs={"top_p": 0.9, "max_tokens": 4096},  # 透传 chat.completions.create
   )
   ```
   `temperature` 是一等参数；`top_p`/`max_tokens` 只能走 `generation_kwargs`。CLI `deepeval set-openai` **未提供 base_url**；本机兼容服务可用 `deepeval set-local-model --model=... --base-url=...`。
4. **自定义 LLM 的兜底**是 `DeepEvalBaseLLM` 子类，须实现 `get_model_name` / `load_model` / `generate(prompt[, schema])` / `a_generate`；带 `schema` 时**必须返回该 Pydantic 实例**，否则报 `ValueError: Evaluation LLM outputted an invalid JSON`。官方指定 JSON confinement 手段为 `lm-format-enforcer` 或 `instructor`。`[官方明确]`
5. **官方最佳实践（无独立 best-practices 页，散在 docs/FAQ）** `[官方明确]`：
   - 「Choosing Your Metrics」建议**指标 ≤5 个**（2–3 通用 + 1–2 定制）；
   - 「Customize Metric Prompts」**明确承认默认模板是为 OpenAI 模型调优的，用自定义 LLM 时应替换 template**——对本项目最实用；
   - CI 回归对比见 `/guides/guides-regression-testing-in-cicd`。
6. **工程细节（与本项目直接相关）** `[官方明确]`：DeepEval **import 时自动加载 `.env.local` → `.env`**（process env 优先）→ 要设 `DEEPEVAL_DISABLE_DOTENV=1` 防串味；本地落盘用 `DEEPEVAL_RESULTS_FOLDER`；默认裁判是 `gpt-5.4` + 读 `OPENAI_API_KEY`（**本项目 `.env` 里没有**）→ **每个 metric 必须显式传 `model=`**。

### 二、DeepEval 的 agentic / 多轮能力

`[官方明确]`（来源：`deepeval.com/docs/getting-started-agents`、`evaluation-trajectory-based-llm-evals`、`metrics-tool-correctness`、`evaluation-end-to-end-multi-turn` 等）

| 指标 | 需要字段 | LLM 裁判 | 备注 |
|---|---|---|---|
| `TaskCompletion` / `StepEfficiency` / `PlanAdherence` / `PlanQuality` | 无逐字段要求，**须先 tracing**，走 `dataset.evals_iterator(metrics=[...])` | 是 | trajectory 类 |
| `ToolCorrectness` | `input`,`actual_output`,`tools_called`,`expected_tools` | 基本否（纯规则；仅传 `available_tools` 才用 LLM） | 需要"期望工具调用" |
| `ArgumentCorrectness` | `tools_called` | 是 | 无参考 |
| `ConversationCompleteness` / `TurnRelevancy` / `RoleAdherence` / `KnowledgeRetention` | `ConversationalTestCase(turns=[...])` | 是 | 多轮对话 |
| `Expectations(must=[], must_not=[])` | 挂在 test case 上，**可无 metric** | 是 | 断言式 |

- **LangGraph 官方集成**：`from deepeval.integrations.langchain import CallbackHandler` → `graph.invoke(..., config={"callbacks":[CallbackHandler()]})` → `assert_test(golden=golden, metrics=[TaskCompletionMetric()])`。`[官方明确]`
- 官方示例路径（仓库内）：`examples/notebooks/langgraph.ipynb`（**与本项目栈最相关**）、`examples/rag_evaluation/`、`examples/mcp_evaluation/`、`examples/tracing/`。

### 三、RAGAS 与 DeepEval 的结合

- **官方原生桥**：DeepEval 内置 `RAGASMetric`（= answer relevancy / faithfulness / contextual precision / contextual recall **四项均值**）与 4 个单项包装 metric，**底层直接调 ragas 库**。`[官方源码]`（`docs/content/docs/(metrics-others)/metrics-ragas.mdx`）
- **框架层同时集成两者**：`deepset-ai/haystack` 同时提供 `RagasEvaluator` 与 `DeepEvalEvaluator` 两个组件。`[官方源码]`
- **第三方没有成熟组合仓库**：搜到的"RAGAS+DeepEval"仓库全是 0–1 star 个人 demo（如 `devrahulbanjara/Agentic-RAG`，README tags 含 ragas+deepeval，但**未证实**真组合两者指标）。
- **分工的经验说法**（社区）：RAGAS 强于严格蕴涵/事实错配，DeepEval 强于细微失实；`[社区经验·非权威]`。
- **结论**：想要"结合"，**官方可辩护的形态是「DeepEval 作统一入口 + 其内嵌 RAGAS 指标」**，而不是两套框架各自独立跑再合并（后者无成熟方案）。

### 四、GitHub 项目与 star（抓取日期 2026-10-15；`api.github.com` 不可达，star 取自 shields.io，为**量级值**）

| 仓库 | 用途 | ⭐ | 最近更新 | 活跃 |
|---|---|---|---|---|
| `confident-ai/deepeval` | 官方框架：agentic + 多轮 + RAG + MCP + trajectory | **≈19k** | 提交 2026-10-15；release python-v4.2.9（2026-10-02） | 活跃 |
| `vibrantlabsai/ragas`（原 `explodinggradients/ragas` 已 301 重定向） | 官方 RAGAS | **≈16k** | 最新 release v0.4.3（2025-12-23） | 维护中，节奏**明显慢**于 deepeval |
| `deepset-ai/haystack` | 第三方框架，**同时内置** Ragas + DeepEval Evaluator | ≈27k | 提交 2026-10-15 | 活跃 |
| `langfuse/langfuse` | 观测平台（本项目自托管 v2） | ≈36k | — | 活跃 |
| `comet-ml/opik` / `truera/trulens` | 评测/观测平台（量级参照） | ≈23k / ≈3.6k | — | — |
| 第三方"用 DeepEval 评 RAG/agent"仓库 | 教学/个人 demo | 0–1.5k | 2025-07~10 | 多为个人 |

- **直接结论**：**除官方 DeepEval 的 RAGAS 指标桥 + Haystack 的双 evaluator 外，没有找到现成可抄的 RAGAS×DeepEval 组合仓库。** 其余为 0-star demo。
- **量级**：deepeval（≈19k）> ragas（≈16k）；deepeval 迭代节奏明显更快。

### 五、多框架并用的真实成本（基于本项目 `pyproject.toml` 实读）

- **硬冲突** `[官方源: PyPI]`：`ragas 0.4.3` 的 `tracing` extra 要求 `langfuse>=3.2.4`，项目 pin `langfuse>=2.60,<3.0` → **不可安装**（RAGAS 自动 tracing 集成不可用）。
- **版本漂移风险** `[官方源 + 本文推断]`：`ragas 0.4.3` 硬依赖整个 langchain 家族且**无版本上限**（`langchain` / `langchain-core` / `langchain-community` / `langchain_openai`），而项目逐条精确 pin → 任一锁文件刷新都可能悄悄抬高版本。
- **deepeval 的依赖面** `[官方源]`：4.2.9 把 `pytest` / `pytest-asyncio` / `pytest-xdist` / `pytest-rerunfailures` / `pytest-repeat` 当 **runtime dep**（均无上限）——与本项目 pin 不冲突，但**在 pytest 9.x 下是否可用未验证**；另含 `posthog` 遥测（官方当前 docs **未记录关闭开关**，未确认可否 opt-out）。
- **无冲突项**：`pydantic` / `numpy` / `datasets` / `tiktoken` / `instructor`（ragas 已依赖 instructor，可与 DeepEval 自定义裁判共用同一 pin）。
- **运行成本**：裁判调用**近似翻倍**；判分口径重复维护（RAGAS `context_precision/recall` ↔ DeepEval `ContextualPrecision/Recall`）；CI 门禁会出现"A 过 B 不过"，需额外仲裁规则；Langfuse v2 只有一套 score 命名空间，两框架回写需统一前缀 + run 级 metadata。
- **正协同** `[官方明确]`：`DisplayConfig(results_folder=...)` 落 `test_run_*.json`（含 hyperparameters + 逐条分数 + reason），`@deepeval.log_hyperparameters` 记配置 → **正好补上"报告不落盘裁判配置"的缺口**；`deepeval test run -s/-i/-c` 分别对应"空检索题 / 裁判 JSON 失败 / 重跑成本"；`AsyncConfig(throttle_value, max_concurrent)` 对应我们的 `RAGAS_EVAL_CONCURRENCY`。

---

## Detailed Analysis

### A. 三条可走的路线（按"官方可辩护度"排序）

1. **只用 RAGAS 的 Agent 指标族**（我们已经确认 0.4.3 现代 API 自带 `AgentGoalAccuracy`/`ToolCallAccuracy`/`ToolCallF1`/`TopicAdherence`）。
   - 优点：零新依赖、复用现有裁判与 `@experiment` 管线、不引入第二套口径。
   - 局限：多轮"对话级/任务完成度/轨迹质量"覆盖不如 DeepEval 细（无 trajectory 类指标）。
2. **RAGAS 守单轮 RAG + DeepEval 守多轮 Agent**（用户提出的方案；框架分工）。
   - 优点：各站主场；DeepEval 对 LangGraph 有官方集成、agentic 指标更全。
   - 代价：新依赖 + 第二套裁判/结果/门禁；Langfuse v2 需自建回写；判分口径重复。
3. **用 DeepEval 作统一入口 + 内嵌 RAGAS 指标**（官方原生桥）。
   - 优点：一个入口、一套报告/CI 门禁，且能复用 RAGAS 指标——**这是"结合"的最官方形态**。
   - 代价：把 RAGAS 的账算进 DeepEval；`RAGASMetric` 只是"四项均值"，粒度不如分开跑；仍有 ragas 依赖漂移问题。

### B. 对我们约束最硬的三条

1. **RAGAS 的自动 tracing 用不了**（`ragas[tracing]` ↔ langfuse v2 硬冲突）——任何方案都得手写回写。
2. **DeepEval 无 Langfuse 集成**，且判分口径/结果格式与现有台账不共享——引入即产生"两套真源"问题。
3. **参考数据缺口不分框架**：`ToolCallAccuracy`/`ToolCallF1`（RAGAS）、`ToolCorrectness`（DeepEval）都要求"期望工具调用"，我们的测试集**没有**；`TopicAdherence` 要允许话题。

### C. 若两套并用（推荐的最小风险配置）

- 两套**共用同一个 judge 端点/模型/温度**（复用现有裁判工厂），否则不确定性来源乘二，本就不可复现的 `context_precision` 更难归因。
- 评测依赖**隔离到独立 venv 或 `[project.optional-dependencies].eval`**，绝不进运行时 dependencies，且 **pin 精确版本**。
- **不要装 `ragas[tracing]`**；设 `DEEPEVAL_DISABLE_DOTENV=1`；每个 metric 显式传 `model=`。
- 统一 Langfuse v2 的 score 命名空间（前缀区分框架 + run 级 metadata）。

---

## Contrarian Views And Risks

1. **官方反对"双真源"** `[官方明确]`：DeepEval 博客 FAQ「…**so keep one framework as the source of truth for release quality**」；同一博客明荐自研 RAG metrics 优于 ragas：「we highly recommend that you use deepeval's RAG metrics instead」。→ 注意这是 **DeepEval 自营内容**，带商业立场。
2. **MLflow 的反面证据** `[权威第三方]`：「Can I use multiple evaluation frameworks together? **Yes, but managing each library's own interface, data format, and result storage quickly becomes a mess.**」
3. **没有成熟组合先例**：第三方 RAGAS×DeepEval 组合仓库全是 0–1 star；组合方案**没有可抄的工程实践**，等于自担设计风险。
4. **`RAGASMetric` 会掩盖差异**：它把四项 RAG 指标压成一个均值，"结合"后你看不到 RAGAS 各单项的分布——诊断力下降。
5. **版本漂移是既有前例**：项目对 langchain 家族精确 pin 是刻意纪律（对照 `tiktoken` 的 pin 注释），ragas 的无上限声明与之相悖。
6. **star/活跃度 ≠ 适配度**：deepeval ≈19k、迭代快，但它**不集成 Langfuse**、默认跑 OpenAI、且文档承认默认模板为 OpenAI 调优——对我们的栈并非开箱即用。

---

## Open Questions

1. 百炼 DashScope 与 DeepEval `OpenAIModel` 的 `beta.chat.completions.parse` 结构化输出**是否兼容**（未验证）。
2. deepeval 4.2.9 的 pytest 插件在 **pytest 9.0.2** 下是否可用（未验证）。
3. `posthog` 遥测**能否关闭**（当前官方 docs 无开关）。
4. deepEval `EvaluationResult` 的准确类名与 JSON 结构（文档未出现）。
5. 项目最终走哪条路线（RAGAS Agent 指标 / RAGAS+DeepEval 分工 / DeepEval 统一入口）——**决策项，归 `docs/adr/`**。
6. 若走"RAGAS 单轮 + 多轮分开跑"：单轮路径如何定义（真·单轮检索一次 vs 复用 agent 取最终 top-k）——见与用户的前序讨论。

---

## Sources

**DeepEval 官方**
- Getting started / FAQ（本地可跑、login 非必需）：<https://deepeval.com/docs/getting-started>
- Models（`OpenAIModel` 的 `base_url`/`temperature`/`generation_kwargs`）：<https://deepeval.com/docs/metrics-introduction> 与 `deepeval.com/docs/models`
- Custom LLM `DeepEvalBaseLLM`（schema 约束、instructor/lm-format-enforcer）：<https://deepeval.com/docs/metrics-introduction>
- Agentic / trajectory：<https://deepeval.com/docs/getting-started-agents>、<https://deepeval.com/docs/evaluation-trajectory-based-llm-evals>
- Tool correctness：<https://deepeval.com/docs/metrics-tool-correctness>
- Multi-turn：<https://deepeval.com/guides/guides-multi-turn-evaluation>、<https://deepeval.com/docs/evaluation-end-to-end-multi-turn>
- LangGraph 集成（`CallbackHandler`）：<https://deepeval.com/docs/evaluation-end-to-end-multi-turn> 及 `confident-ai/deepeval` 的 `examples/notebooks/langgraph.ipynb`
- RAGAS 指标桥：<https://deepeval.com/docs/metrics-ragas>（源码 `docs/content/docs/(metrics-others)/metrics-ragas.mdx`）
- 对比博客（含 Integration 清单、无 Langfuse、single source of truth）：<https://deepeval.com/blog/deepeval-vs-ragas>、<https://deepeval.com/blog/top-5-llm-evaluation-frameworks>
- Flags / configs（`-s`/`-i`/`-c`、`DisplayConfig`）：<https://deepeval.com/docs/evaluation-flags-and-configs>

**Langfuse 官方**
- DeepEval 胶水配方（**v3/v4 API**）：<https://langfuse.com/resources/engineering/deepeval>
- 外部评测流水线 cookbook（v2 可行路径的模板）：<https://langfuse.com/guides/cookbook/example_external_evaluation_pipelines>
- RAGAS × Langfuse（v4 配方）：<https://langfuse.com/integrations/frameworks/ragas>

**PyPI / 依赖**
- ragas 0.4.3 元数据（`tracing` extra 要求 `langfuse>=3.2.4`；langchain 家族无上限）：<https://pypi.org/pypi/ragas/0.4.3/json>
- deepeval 4.2.9 元数据（pytest 家族为 runtime dep；posthog）：<https://pypi.org/pypi/deepeval/json>

**第三方 / 对照**
- MLflow：多框架并用警告：<https://mlflow.org/top-5-agent-evaluation-frameworks/>
- Haystack（Ragas + DeepEval 双 Evaluator）：<https://github.com/deepset-ai/haystack>
- Atlan 框架对比（"多框架并用"实践）：<https://atlan.com/know/llm-evaluation-frameworks-compared>

**GitHub（star 抓取日期 2026-10-15，shields.io 量级值）**
- <https://github.com/confident-ai/deepeval>（≈19k）
- <https://github.com/vibrantlabsai/ragas>（≈16k；原 explodinggradients 已重定向）
- <https://github.com/langfuse/langfuse>（≈36k）、<https://github.com/comet-ml/opik>（≈23k）、<https://github.com/truera/trulens>（≈3.6k）

---

## Rerun Inputs

```yaml
workflow: firecrawl-deep-research
topic: DeepEval 接入方式（AgentRAG + Langfuse v2）、与 RAGAS 的结合、多框架并用成本
depth: thorough
output: markdown
```

> **说明**：本文为三路 worker（DeepEval 官方接入 / Langfuse 与结合实例 / GitHub 项目）+ 一份依赖成本核查的合成。`api.github.com` 在本环境不可达，star 数为 shields.io 量级值；其余结论均附一手 URL。

---

## 附录：本地源码指引（coding 用，2026-10-15 补）

> 本附录是**追加**的源码导航，不改上文任何结论与数字。有了本地镜像后，下文所有"官方源码经 GitHub API 核实"的结论都可以**直接读源码复核**，不必再靠 URL 转述。

**本地镜像**：`/root/code/github/deepeval` · **版本 `4.3.0`**（`pyproject.toml` 的 `version`）。
⚠ **版本差异**：本地镜像是 **4.3.0**；本次 1 题 PoC 实装的是 **4.2.8**；前文调研引用的线上文档标注 **4.2.9**。三者不要混用——尤其 4.x 内部 API 变动频繁。

### 我们要用的能力 → 本地源码路径

| 能力 | 本地路径（相对 `/root/code/github/deepeval`） | 备注 |
|---|---|---|
| **RAGAS 指标桥** | `deepeval/metrics/ragas.py` | 单文件。裁判构造在 `:71-72`、`:152-153`、`:224-225`：`if isinstance(self.model, str): chat_model = OpenAIModel(model=self.model).load_model()` —— **本地源码逐行印证了 PoC 的 K2 发现**（传字符串会拿到裸 client；必须传 langchain `BaseChatModel`） |
| **LangGraph / LangChain 挂件** | `deepeval/integrations/langchain/callback.py` | `CallbackHandler` 实现；同目录 `patch.py` / `utils.py` |
| **裁判模型（OpenAI 兼容）** | `deepeval/models/llms/openai_model.py` | `OpenAIModel`；同目录还有 `local_model.py`（自建 OpenAI 兼容端点）、`kimi_model.py`、`deepseek_model.py` |
| **自建裁判基类** | `deepeval/models/base_model.py` | `DeepEvalBaseLLM` |
| **用例类型** | `deepeval/test_case/llm_test_case.py`（`LLMTestCase` / `ToolCall`）、`conversational_test_case.py`（多轮）、`mcp.py` | 字段以源码为准 |
| **指标全量目录** | `deepeval/metrics/` | 见下表 |

### 源码实读的**新发现**（前文未覆盖）

**① 存在一套「多轮版」context 指标** —— 这直接改变了"context 轴只能单轮"的判断：

```
deepeval/metrics/turn_contextual_precision/
deepeval/metrics/turn_contextual_recall/
deepeval/metrics/turn_contextual_relevancy/
deepeval/metrics/turn_faithfulness/
deepeval/metrics/turn_relevancy/
```

→ 即 DeepEval **有 per-turn 的上下文精度/召回/相关性与忠实度**。前文"RAGAS 与 DeepEval 都是单轮 context 指标"这一说法，**对 DeepEval 的 `turn_*` 族不成立**，需按此更正选型视角。

**② 存在 `agent_loop_detection`（Agent 打转检测）** —— 直接对口我们已登记的"Agent 打转、重复同一 query"（Q17）。

**③ 其它与 agentic 相关的指标（本地实读）**：
`task_completion` · `step_efficiency` · `plan_adherence` · `plan_quality` · `goal_accuracy` · `tool_correctness` · `argument_correctness` · `topic_adherence` · `agent_loop_detection` · `conversation_completeness` · `knowledge_retention` · `role_adherence` · `conversational_dag` · `mcp` · `mcp_use_metric`

**④ click 冲突在最新版依旧** —— `pyproject.toml:33`：`click = ">=8.0.0,<8.4.0"`。与 `huggingface_hub` 的 `click>=8.4.2` 互斥，**4.3.0 未修**。

### 怎么用这份指引

1. 写代码前先读对应 `deepeval/metrics/<指标名>/` 下的 `metric.py` 与其 `schema.py`/prompt，确认**构造参数与必填字段**——比查文档快且准。
2. 任何"DeepEval 能不能做 X"的问题，先 `ls deepeval/metrics/`；**指标名即能力清单**。
3. 版本差异要留神：本地是 4.3.0，**实装前先确认目标版本**（PoC 装到的是 4.2.8）。

---

## 附录 B：DeepEval 桥与 RAGAS 两套 API 的源码级核对（2026-10-16 补）

> 本附录同样是**追加**记录，来源为「本机实装源码 + 本地镜像 `/root/code/github/deepeval`（4.3.0）+ 官方仓库 PR/issue + 官方文档」。不改上文任何结论。

### B1. DeepEval 的 RAGAS 桥到底做了什么（`deepeval/metrics/ragas.py`，564 行）

**它是一个薄适配器**，不是重新实现 RAGAS：

```python
from ragas import evaluate
from ragas.metrics import context_precision            # legacy ragas.metrics，非 collections
chat_model = OpenAIModel(model=self.model).load_model() # 字符串 → 裸 client
dataset = Dataset.from_dict({"contexts":[…], "question":[…], "ground_truth":[…]})  # 1 行
scores = evaluate(dataset, metrics=[context_precision], llm=chat_model)
score = scores["context_precision"][0]
```

| 事实 | 证据 |
|---|---|
| 桥内 6 个类：ContextualPrecision / ContextualRecall / **ContextualEntitiesRecall** / AnswerRelevancy / Faithfulness / 聚合 `RagasMetric` | `ragas.py:38/115/186/334/414/484` |
| **必填四字段**：`input` / `actual_output` / `expected_output` / `retrieval_context` | 官方 Required Arguments |
| **默认 model = `gpt-3.5-turbo`**（6 处），该模型**已停服** | `ragas.py:44` 等；deepeval issue **#3404** |
| `embeddings` **仅** `RAGASAnswerRelevancyMetric` 需要 | 官方文档 + `ragas.py:341,395-397,491,527` |
| **聚合 `RagasMetric` 在源码里是 5 项算术平均**（含 ContextualEntitiesRecall），**官方文档写 4 项** | `ragas.py:521-541` vs 文档 —— **文档与源码不一致** |
| **NaN 无保护**，官方 FAQ 原文："`ragas` returns `NaN` when the model emits invalid JSON, and `RagasMetric` wraps it directly." | `deepeval.com/docs/metrics-ragas` FAQ |
| 桥对 ragas 的版本要求仅 **`>=0.2.1`**，且用**字符串比较**（`"0.10.0" < "0.2.1"` 会误判） | `ragas.py` 的 `import_ragas()` |
| 官方对桥的定位：「**Use `RagasMetric` only if you're already standardized on ragas**」 | 同上 FAQ |

### B2. RAGAS 现代（0.4.x `metrics.collections`）与经典（legacy `metrics`）：**公式同源，不是"更优"**

- **[官方源码]** 迁移标准就是"行为一致"——PR **#2370**：*"Migrating metrics requires consistent validation that **new implementations match legacy behavior**"*，并配了逐指标比分工具 **`metric_score_diff.ipynb`**。
- **[官方源码]** PR **#2407**：文档改为 *"showcase the new `ragas.metrics.collections` API as the **primary recommended approach**, while preserving legacy API documentation for backward compatibility"*。
- **[本机源码实读]** 公式逐行一致：
  - context_precision：legacy `_context_precision.py:113-126`（`denominator=sum(verdicts)+1e-10`、`Σ precision@i·v_i`）与 collections 版同式（后者 docstring 原文 *"Matches legacy logic exactly"*）；
  - faithfulness：legacy `_faithfulness.py:182-189`（`faithful/num_statements`）与 collections 版同式。
- **三处真实差别**：① 接口（`ascore(参数)` vs `evaluate(dataset)`）；② **LLM 管道**（collections 走 `llm_factory → InstructorLLM`，**temperature 由调用方 kwargs 决定**；legacy 走 `LangchainLLMWrapper`，**会改写底层模型温度**）；③ **覆盖面**（见 B3）。

### B3. ragas 0.3.9 与 0.4.3 的能力差（决定"能不能降版"）

| | 0.3.9 | 0.4.3 |
|---|---|---|
| `metrics.collections`（现代） | ✅ 存在（AnswerCorrectness / Faithfulness / ContextPrecision / ContextRecall / ContextUtilization / SemanticSimilarity …） | ✅ |
| **agent 指标进现代化** | ❌ **零个**（`goal_accuracy` / `tool_call_accuracy` / `tool_call_f1` / `topic_adherence` **只在 legacy**） | ✅ 现代化里有 |
| `testset.transforms.default.default_transforms_for_prechunked`（出题路） | ❌ **不存在** → `ImportError` | ✅ |
| 实装验证 | `pytest tests/cli`：**147 passed / 1 failed**（失败即出题路 `ImportError`，`src/cli/eval_ragas_generate.py:307`）；打分路（modern-stack 测试）**3 passed** | —— |

⇒ **降版只打断"出题路"，不打断"打分路"**；但会**丢掉现代化里的 agent 指标**。

### B4. 温度：不是"统一 0.01"，而是**按 `n` 分档**，且**被 ragas 改写**

- 源码：`get_temperature(n) = 0.3 if n > 1 else 0.01`（`ragas/llms/base.py:71-73`）；`generate_text`/`agenerate_text` **直接改写** `self.langchain_llm.temperature`（`:229-231` / `:281-283`），调用后还原。
- **实测（mock server 抓真实请求体）**：faithfulness（`n=1`）→ `"temperature": 0.01`；`max_completion_tokens: 8192` 与 `enable_thinking: false` **均透传成功**。ragas 0.3.9 与 0.4.3 **表现相同**。
- **`answer_relevancy` 是例外**：legacy `_answer_relevance.py:142-143` 传 `n=self.strictness`（默认 **3**）→ `get_temperature(3)` = **0.3**。这是**有意**的（该指标需生成多个多样化问题；issue **#1812** 报的正是温度被压低导致问题雷同）。
- **官方 bug**：`bypass_temperature` 开关**只在同步路径生效**（`:281` 有 `and not self.bypass_temperature`），**异步路径漏判**（`:229` 无此判断）。
- 相关 issue：#1812（温度被忽略）、#656 / PR #657（提议删除 temperature 关键字，**未合并**）、#2722 / #2781 / #2817（覆盖用户温度 → 雷同输出 / o3 只接受 1 → 400 / 重试返回同样的坏输出）。
- **对照**：DeepEval 自身是 `temperature 显式 > settings.TEMPERATURE > 默认 0.0`，并**对声明不支持温度的模型自动改 1**（`deepeval/models/llms/openai_model.py:102-116`）；`DEEPEVAL_MODEL_THINKING` 默认关（描述原文：*"Off unless set, so judges spend their token budget on the verdict"*）。

### B5. 依赖：**弃用 0.4.x 恰好解开 click 死结**

```
deepeval 4.2.9 + ragas 0.4.3  →  ResolutionImpossible
    ragas0.4.3 → datasets → huggingface_hub(1.27.0) → click>=8.4.2
    deepeval                                          → click<8.4.0      ⇒ 交集 = ∅

deepeval 4.2.9 + ragas 0.3.9  →  解析通过（实测项目 venv 装成功）
    落 click-8.3.3 / huggingface_hub-1.16.1；pip check 无新增冲突
```

`ragas[tracing]` 另需 `langfuse>=3.2.4`，与本项目 `langfuse<3.0` 硬冲突 ⇒ RAGAS 自动 tracing 不可用。

### B6. DeepEval 原生四指标 vs RAGAS：**名字同、机制未全同**

| RAGAS | DeepEval | 等价性 | 关键证据 |
|---|---|---|---|
| `faithfulness` | `FaithfulnessMetric` | 🟡 近似 | DeepEval 从**上下文逐文档抽 truths** 再判答案（`faithfulness.py:40-45`），且有 `BORDERLINE` 档（`:408-413`）；RAGAS 从**答案抽 statements** 做 NLI |
| `answer_relevancy` | `AnswerRelevancyMetric` | 🔴 **机制不同** | DeepEval = 答案拆陈述判"是否切题"取比例（`answer_relevancy.py:326-331`，仅需 `input`/`actual_output`，**不需 embeddings**）；RAGAS = **反向生成 N 问 → 与原问余弦相似度 → 均值** |
| `context_precision` | `ContextualPrecisionMetric` | 🟡 近似 | DeepEval = per-context 0/1 + **加权累积精度**（`contextual_precision.py:366-390`），定位为**评 reranker 排序** |
| `context_recall` | `ContextualRecallMetric` | 🟢 最接近 | `contextual_recall.py:262-267` |

**两处"说法"未获证实**（勿当事实引用）：① 「按 chunk 粒度评估利用率的指标」——`deepeval/metrics/` 下**没有** util/chunk/usage 类指标；② 「2024 官方把 ragas 重写进原生指标就是为了同名替换」——官方文档无此表述。

### B7. `turn_*` 的粒度是"对话单元"，不是"检索步"

`Turn(role: "user"|"assistant", content, retrieval_context, expected_outcome)` → `ConversationalTestCase(turns=[...], expected_outcome=...)`；流程为 `get_unit_interactions(turns)`（按 user→assistant 切单元）→ `get_turns_in_sliding_window(units, window_size=10)` → **窗口内把所有 assistant turn 的 `retrieval_context` 合并成一个列表**再算分（`turn_contextual_precision.py:133-141, 223-245`）。

⇒ 对"**一个用户问题 + Agent 内部连搜 N 次**"的形态，只会得到一个单元 ⇒ 行为与单轮 context 指标相同。**`turn_*` 不能提供 per-step 检索质量**。

### B8. GitHub：使用 DeepEval 的项目（star 抓取 2026-10-16，`api.github.com` 实测可达）

**名字/描述含 `deepeval`（total 1,278）**

| ⭐ | 仓库 | 备注 |
|---|---|---|
| **18,897** | `confident-ai/deepeval` | 官方 |
| 173 | `kanchengw/cnllm` | 中文 LLM 工具包 |
| 156 | `AgentEvalHQ/AgentEval` | .NET agent 评测 |
| 116 | `bansalkanav/GenAI-AgenticAI-From-Zero-to-Production` | 教程 |
| 44 | `campusx-official/rag-eval-deepeval` | RAG + DeepEval 教学 |
| 38 | `SDETBMan/qa-automation-portfolio` | 测试工程 |
| 37 | `theaiautomators/deepeval-wrapper` | REST 封装 |
| 29 | `Srivatsa03/rag-redteam` | RAG 红队 |
| 22 | `droideronline/pyconf-hyd-2026-trustworthy-llm-agents` | workshop |
| **19** | `Magical-Bear/langchain-mcp-deepeval-trajectory-eval` | **多 MCP 工具轨迹评测 demo**（与本项目 agentic 形态最像） |
| 16 / 15 | `avnlp/rag-pipelines` / `avnlp/dspy-opt` | 高级 RAG 管线 |

**`topic:deepeval`（total 212）**：`cnllm` 173 → `GenAI-AgenticAI…` 116 → `qa-automation-portfolio` 38 → `avnlp/rag-pipelines` 16 → `avnlp/dspy-opt` 15 → `messeb/py-deepeval-behave-bdd-testing-example` 11 → `awslabs/uaef`（AWS 的通用 Agent 评测框架）6

**README 提到 `deepeval`（total 9,977）**：`mlabonne/llm-course` 83,619 → `rohitg00/ai-engineering-from-scratch` 69,889 → `NirDiamant/RAG_Techniques` 29,888 → `EthicalML/awesome-production-machine-learning` 21,011 → `confident-ai/deepeval` 18,897

**读法**：除官方外**生态里"有星"的 DeepEval 项目几乎没有**（第二名即掉到 173），且 agentic 轨迹类最高仅 19★。⇒ **无可抄的生产级参考，设计风险自担**。
