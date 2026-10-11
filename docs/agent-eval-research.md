# Deep Research: 让 RAGAS 支持 Agent 回答质量鉴定

> 调研时间：2026-10-10 ｜ 深度：Thorough（5+ 组检索、15+ 来源，含官方文档与本地参考项目源码）
> 结论面向：把本仓现有 RAGAS 离线评测（`src/cli/eval_ragas.py`）从「单轮 RAG 组件评测」升级为「Agent 评测」。

## Executive Summary

本仓的评测脚本**已经在调用真实 Agent 图**（`graph.ainvoke`，`src/cli/eval_ragas.py:129`），但产出的四指标（faithfulness / answer_relevancy / context_precision / context_recall）是**单轮 RAG 的组件指标**，对 Agent 特有的行为——工具选择、调用轨迹、目标达成、追问澄清、无检索直答、fork skill 直出——**零覆盖**。更重要的是，这条链路上 **context 类指标当前恒失效**（已登记的缺陷 `F-32`）：`agent_finalize` 从 `current_request_ctx` 取 `tool_contexts`，而 CLI 入口从不 set 该 contextvar，导致检索上下文被追加进一个即弃的空列表，`contexts=[]` → precision/recall 恒 0、faithfulness 算 nan。

好消息是**不需要引入新依赖**：本仓已锁 `ragas==0.4.3`，其 `ragas.metrics.collections` 已原生提供 Agent/Tool-use 指标（实测本机可导入）：`ToolCallAccuracy`、`ToolCallF1`、`AgentGoalAccuracyWithReference/WithoutReference`、`TopicAdherence`，以及用于「答案准确性」的 `AnswerAccuracy`、`FactualCorrectness`、`SemanticSimilarity`、`RubricsScore*`、`DomainSpecificRubrics` 等；官方还给了 LangGraph Agent 评测 howto。

除 RAGAS 外，主流做法分三条路线：**轨迹评测框架**（DeepEval、LangSmith，能评「第 14 步有没有调对工具」，而非只看最终答案）、**确定性 scorer harness**（ragflow 的 Go `internal/harness/core/evals`，`ToolCalled/Steps/FileContains` + 可选 LLM judge，可进 `go test`/CI）、**图内自纠质检**（financial_rag 的 `RetrievalGrader`/`FaithfulnessChecker`，把评测器做成在线节点）。对本仓的推荐路径见文末「落地建议」。

## Key Findings

1. **评测已接 Agent 图，但指标口径不匹配**。`_answer_one_question` 走的是完整 Agent 图，产物只有 `answer` 与 `tool_contexts`；四指标全是单轮 RAG 口径，缺 Agent 维度。
2. **context 类指标恒失效（`F-32`，已登记未修）**。根因在 `src/agents/graph/agent_node.py:321-327` 读 `current_request_ctx`，而 `src/cli/eval_ragas.py` 从不设置它；工具侧的收集器回退为空列表（`src/agents/tools/rag_tools.py:244-249`）。**这是「修 RAGAS」的第一优先级。**
3. **RAGAS 0.4.3 原生支持 Agent 指标**，实测 `from ragas.metrics.collections import ToolCallAccuracy, ToolCallF1, AgentGoalAccuracyWithReference, AgentGoalAccuracyWithoutReference, TopicAdherence` 全部可导入（新 API，旧的 `ragas.metrics` 导入已 deprecated）。
4. **Agent 指标吃「消息轨迹」**：需要把本仓 `state.messages`（含 `AIMessage.tool_calls`）转成 `ragas.messages.{HumanMessage, AIMessage, ToolCall, ToolMessage}`，测试集需要新增 `reference_tool_calls` / reference goal / `reference_topics` 字段。
5. **DeepEval 是「Agent 原生」框架**：轨迹级 `PlanQuality/PlanAdherence/TaskCompletion/StepEfficiency` + 组件级 `ToolCorrectness/ArgumentCorrectness`，pytest/CI 原生；LangSmith 有 `create_trajectory_match_evaluator`（strict/unordered/subset/superset）与 multi-turn online evaluators。
6. **同领域参考项目 financial_rag-main 的做法可直接借鉴**：选手/裁判分离（本仓已有）、**评估前过滤掉 `contexts` 为空的数据**、以及用关键词/正则做零依赖兜底评估（`run_ragas_eval.py`）。
7. **评测框架通病**：离线套件是「写的时候的流量快照」，线上分布会漂移（Morph/YC 调查：多数 agent 团队觉得 evals under-deliver）。需要「离线回归 + 线上每轮分类」两层，而不是一套离线指标包打天下。
8. **strict 轨迹匹配会误判**：同一任务常有多个正确路径，LangSmith 官方明确点名 exact-match 轨迹评测的缺陷；应优先用 subset/unordered/F1 或 LLM-judge。

## Detailed Analysis

### A. 本仓现状诊断

| 维度 | 现状 | 对 Agent 评测的缺口 |
|------|------|--------------------|
| 被测对象 | 完整 Agent 图（`graph.ainvoke`） | 已具备，不需要重建 |
| 采集产物 | `answer` + `tool_contexts` | **缺 `state.messages`（工具调用轨迹）** |
| 指标 | 4 个单轮 RAG 指标 | 缺工具选择、轨迹、目标达成、话题约束 |
| context 指标 | **恒失效（`F-32`）** | contexts 为空 → precision/recall=0、faithfulness=nan |
| 测试集 schema | `{user_input, reference}` | 缺 `reference_tool_calls` / goal / topics |
| 落库/前端 | `eval_report` 仅 4 列，`kb_eval` API 仅返 4 指标 | 加 Agent 指标需扩表 + 扩接口 |

`F-32` 的机制：`retrieve_kb` 把上下文写进 `ctx.tool_contexts`（有 ctx 时）或一次性局部 list（无 ctx 时，`rag_tools.py:244-249`）；`agent_finalize` 只从 `current_request_ctx.get()` 读（`agent_node.py:321-327`）。CLI 从不 set → 回传 state 的 `tool_contexts` 恒空。

### B. RAGAS 0.4.3 对 Agent 的原生支持（首选，零新依赖）

`ragas.metrics.collections` 实测可用指标（分组）：

- **工具/Agent 专用**：`ToolCallAccuracy`（序列+参数，strict/flexible order，得分 = 参数准确率 × 序列是否对齐）、`ToolCallF1`（precision/recall 版）、`AgentGoalAccuracyWithReference` / `AgentGoalAccuracyWithoutReference`（二值，结果导向）、`TopicAdherence`（precision/recall/F1）。
- **答案准确性（可用于最终答复）**：`AnswerAccuracy`（NVIDIA）、`AnswerCorrectness`、`FactualCorrectness`、`SemanticSimilarity`、`ExactMatch`、`RougeScore`/`BleuScore`/`CHRFScore`、`StringPresence`、`QuotedSpansAlignment`、`SQLSemanticEquivalence`。
- **自定义/打分**：`RubricsScoreWithReference`/`WithoutReference`、`DomainSpecificRubrics`、`InstanceSpecificRubrics`、`DiscreteMetric`、`@numeric_metric`、`AspectCritic`、`SimpleCriteriaScore`、`SummaryScore`。
- **接地/噪声**：`ResponseGroundedness`、`Faithfulness`、`ContextRelevance`、`ContextEntityRecall`、`NoiseSensitivity`、`ContextUtilization`。
- **消息模型**：`ragas.messages.{HumanMessage, AIMessage, ToolCall, ToolMessage}`；批量用 `EvaluationDataset`/`MultiTurnSample`（后者 legacy）；实验框架 `@experiment`。

官方 howto：LangGraph Agent 评测（`docs.ragas.io/en/stable/howtos/integrations/_langgraph_agent_evaluation/`）——把 LangGraph 的 messages 转成 ragas 消息后跑 `ToolCallAccuracy` + `AgentGoalAccuracy`；多轮/话题见 `agents/` 指标页与 `evaluating_multi_turn_conversations`。注意 v0.4 起旧 `ragas.metrics` 导入会 deprecation，Agent 指标统一走 `collections`。

### C. 除 RAGAS 外的评测方法（对比）

| 方法 / 框架 | 评测层次 | 关键指标/能力 | 接入方式 | 许可/成本 | 对本仓的适配度 |
|---|---|---|---|---|---|
| **RAGAS 0.4.3（本仓已装）** | 输出 + 工具单步 | ToolCallAccuracy/F1、AgentGoalAccuracy、TopicAdherence、AnswerAccuracy、Rubrics | Python，`collections` API | Apache-2.0，免费 | **最高**：零新依赖，官方有 LangGraph howto |
| **DeepEval** | 端到端 + 轨迹 + 组件 | PlanQuality/PlanAdherence、TaskCompletion、StepEfficiency、ToolCorrectness、ArgumentCorrectness、GEval/DAGMetric | `@observe` tracing + pytest `assert_test` | Apache-2.0，免费（有云） | 高：Agent 原生、可进 CI，但需新依赖 |
| **LangSmith** | final response / single step / trajectory | 三种评测 + `create_trajectory_match_evaluator`（strict/unordered/subset/superset）+ `create_trajectory_llm_as_judge` + multi-turn online | LangChain/LangGraph 同源 | 商业（免费档 5k traces/月） | 中高：与 LangGraph 同源，但为付费平台 |
| **Arize Phoenix / Braintrust / Galileo / OpenAI Evals** | 端到端 + 轨迹（各别） | 观测扩展型评测；Braintrust/Galileo 偏生产在线 | SDK/平台 | 商业/开源混合 | 中：可作对照，非首选 |
| **Promptfoo** | 端到端（配置化） | 声明式断言 + LLM-judge，轻量、CI 友好 | YAML/CLI | MIT | 中：适合快速回归门禁 |
| **确定性 scorer harness（自研，仿 ragflow）** | 轨迹（结构化断言） | `ToolCalled`/`Steps`/`FinalTextContains`/`FileContains` + 可选 `LLMJudge` | 代码即断言，可进 pytest | 无（自研） | **高**：零依赖、可做硬门禁，适合与 RAGAS 互补 |
| **图内自纠质检（仿 financial_rag）** | 在线（非离线） | `RetrievalGrader`（CRAG）、`QueryRewriter`、`FaithfulnessChecker`（Self-RAG） | 加进图节点 | 无（自研） | 中：是「质检」不是「评测」，但能在线止损 |
| **经典 LLM-as-judge / G-Eval** | 端到端 | 自定义自然语言准则 | 任意 | 免费 | 高：灵活，但有偏差（见风险段） |
| **规则指标 EM/F1/Rouge/SemanticSim** | 输出 | 与 reference 比对 | 任意 | 免费 | 中：适合有确定答案的题 |

### D. 本地参考项目做法（实读源码）

- **financial_rag-main（同域，最高价值）**
  - `rag_backend/tests/evaluators/`：`run_v2_system.py`（跑链路 → `v2_system_answers.csv`）→ `run_ragas_host.py`（宿主机跑 RAGAS，judge=deepseek-chat，**先用 `df[df['contexts'].apply(len)>0]` 过滤空上下文**）→ `run_ragas_eval.py`（**零 RAGAS 依赖**的关键词兜底：`has_ground_truth_key`/`has_numbers`/`is_fallback`）。README 报数：Context Recall 0.89 / Precision 0.79 / Faithfulness 0.75。
  - `rag_backend/app/langgraph/quality_nodes.py`：`RetrievalGrader`（CRAG，<0.6 触发改写重检）、`QueryRewriter`、`FaithfulnessChecker`（Self-RAG，<0.7 触发重生成）——把「评测器」做成**在线自纠节点**。
- **ragflow-0.26.4**：`internal/harness/core/evals/evals.go` —— Go 版轨迹评测 harness（`EvalCase` + `Scorer`，含 `ToolCalled/Steps/FinalTextContains/FileContains/LLMJudge`，`RunT` 进 `go test`，输出 JSON/CSV 报告）。**「测试即评测」范式的最佳样板。**
- **Qwen-Agent**：`benchmark/deepplanning/.../evaluation/` 有规划/视觉判分 pipeline，偏 benchmark，非通用评测。
- **deepseek-harness**：无质量评测框架（仅性能 baseline 脚本）。
- **LangBot / WeKnora / openakita / dify / claude-code / codex**：未发现 ragas/deepeval/trulens 等通用答案质量评测。

### E. 落地建议（分阶段，最小改动优先）

**Phase 0（必修 bug，先做）——修 `F-32`**
在 `eval_ragas.py` 每题调用前构造并 `set current_request_ctx`（携带 `tool_contexts` 收集器、`kb_id`、`session_id`），或让 `agent_finalize` 在 `ctx is None` 时从 state/消息回落取上下文。并补「`contexts > 0`」守卫断言。**不做这步，任何 RAG context 指标都测不到。**

**Phase 1（让 RAGAS 真正测 Agent）**
- 采集轨迹：除 `answer`/`tool_contexts` 外，把 `state.messages` 转成 `ragas.messages`（`AIMessage.tool_calls` → `ToolCall`）。
- 扩测试集 schema：每条样本增 `reference_tool_calls`（可选）、`reference`（goal）、`reference_topics`（可选）；生成器一并合成。
- 跑指标：`ToolCallAccuracy`/`ToolCallF1`、`AgentGoalAccuracyWithReference`（有 goal）/`WithoutReference`、`TopicAdherence`（域约束）。
- 保留四指标，但**对 `contexts` 为空的样本跳过 context 类**（学 financial_rag），并把「无检索直答」单列一类，避免拉低/污染均值。
- 最终答案准确性：`AnswerAccuracy`（需 reference）或 `RubricsScoreWithReference`/`DomainSpecificRubrics`（企业域更合适）。

**Phase 2（分层评测，对齐 LangSmith/DeepEval 三分法）**
`final response`（端到端 judge/answer accuracy） + `single step`（工具选择/参数 → ToolCallAccuracy） + `trajectory`（路径 → ToolCallF1 / LangSmith match / DeepEval TaskCompletion+StepEfficiency）。

**Phase 3（可选）**
多轮 clarify 场景用 `MultiTurnSample` + `AspectCritic`/`AgentGoalAccuracyWithoutReference`；在线采样评测（需求池 `F-08`）；扩 `eval_report` 表与 `kb_eval` API 承载 Agent 指标。

**依赖取舍**：本仓已 pin `ragas==0.4.3`，Agent 指标**零新依赖** → 首选「扩 RAGAS 用法」；DeepEval/LangSmith 作对照或后续增量（新依赖/平台）。符合 CLAUDE.md「优先对齐生产在跑 + 领域契合」的选型纪律。

## Contrarian Views And Risks

- **离线套件的时效缺口**：Morph 引 YC 调查——多数 agent 团队的 evals「under-deliver」，因为离线套件是写时的快照，线上是每天漂移的分布。应「离线回归层 + 线上每轮分类层」两层，别指望一套离线指标兜住生产。
- **LLM-as-judge 的偏差**：长度/位置/自我偏好偏差、非确定性、每题一次模型调用（成本）。DeepEval/LangSmith 都强调 judge 用于离线采样，**不适合每轮生产**；确定性 scorer 应作硬门禁，judge 作补充。
- **strict 轨迹匹配误判**：多正确路径场景下 exact-match 假阴性高。用 subset/unordered/F1 或让 judge 评完整轨迹（LangSmith 明确点名的缺陷）。
- **RAG 指标之于 Agent 是「语义不适用」而非 bug**：Agent 可能合理不检索（直答/澄清/联网/fork），此时 context 指标无意义，**不要为了刷分强行计算**——跳过并分类才是正解。
- **评测器可能变成「越权上报」**：图内自纠节点（financial_rag 风）若把关不严会把「重试成功」掩盖成「一次成功」，评测数字失真。

## Open Questions

1. `reference_tool_calls` / reference goal 怎么产？合成器能否一并生成，还是需人工标注？（成本决定可行性）
2. **追问（`ask_user`）场景算成功还是失败**？多轮如何切分评测单位（单轮 vs 整个对话）？
3. **skill 直出（fork）轮是否纳入评测**？其轨迹是子代理消息，与主图消息形态不同。
4. 是否允许新增依赖（deepeval/promptfoo）或改走 Langfuse judge（需求池 `A-08` 升级 v3 才有 judge/experiments）？

## Sources

| URL | 说明 |
|---|---|
| https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/ | RAGAS Agent/Tool-use 指标官方页（ToolCallAccuracy/F1、AgentGoalAccuracy、TopicAdherence、legacy 迁移说明） |
| https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ | 指标总表（Agents 分类） |
| https://docs.ragas.io/en/stable/howtos/integrations/_langgraph_agent_evaluation/ | RAGAS 官方 LangGraph Agent 评测 howto（消息转换 + 指标用法） |
| https://docs.ragas.io/en/stable/tutorials/agent/ | RAGAS「Evaluate an AI Agent」教程（`@experiment`、`@numeric_metric` 自定义） |
| https://docs.ragas.io/en/stable/howtos/applications/evaluating_multi_turn_conversations/ | RAGAS 多轮对话评测（MultiTurnSample + AspectCritic） |
| https://deepeval.com/guides/guides-ai-agent-evaluation | DeepEval Agent 评测总览（轨迹/组件/端到端三分法） |
| https://deepeval.com/docs/evaluation-trajectory-based-llm-evals | DeepEval 轨迹评测细节（Golden、`@observe`、CI） |
| https://docs.langchain.com/langsmith/evaluation-approaches | LangSmith 三种 Agent 评测方式（final response / single step / trajectory） |
| https://www.langchain.com/resources/llm-evaluation-framework | LangChain「Trajectories vs Outputs」 |
| https://www.morphllm.com/ai-agent-evaluation-frameworks | 7 大 Agent 评测框架对比（2026）+ evals 时效缺口 |
| https://mlflow.org/top-5-agent-evaluation-frameworks/ | 2026 Top 5 评测工具对比 |
| https://fractal.ai/blog/ai-agents-ragas/ | RAGAS 评 Agent 的实战指南（tool call / goal accuracy 解读） |
| https://thedataguy.pro/writing/2025/04/evaluating-ai-agents-with-ragas/ | 用 RAGAS 评 Agent（goal/tool/topic 三指标） |
| https://langfuse.com/resources/engineering/evaluation-of-rag-with-ragas | Langfuse 用 RAGAS 打分并回写 trace score |
| 本地 `financial_rag-main/rag_backend/tests/evaluators/*` 与 `app/langgraph/quality_nodes.py` | 同域选手/裁判分离、空上下文过滤、零依赖兜底、图内自纠 |
| 本地 `ragflow-0.26.4/internal/harness/core/evals/evals.go` | Go 版轨迹 scorer harness（测试即评测范式） |

## Rerun Inputs
workflow: firecrawl-deep-research
topic: 让 RAGAS 支持 Agent 回答质量鉴定 + 除 RAGAS 外的 Agent 评测方法
depth: thorough
output: markdown
