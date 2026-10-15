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
