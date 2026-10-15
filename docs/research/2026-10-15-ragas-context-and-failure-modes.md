# RAGAS 的 `retrieved_contexts` 用法、Agent 失败模式与裁判可复现性（外部事实调研）

- **调研日期**：2026-10-15
- **调研方式**：一手来源检索（RAGAS 官方文档 docs.ragas.io + 官方仓库 vibrantlabsai/ragas 的源码 / PR / issue、LangGraph 官方文档、Anthropic 官方工程博客、RAGAS 原始论文 arXiv:2309.15217）；本机 ragas 0.4.3 源码实读（`/opt/venvs/corporate_rag/lib/python3.12/site-packages/ragas/`）。
- **范围**：① `retrieved_contexts` 的定义与 agentic 场景正确用法（去重 / 截断 / 条数对 ContextPrecision 的影响）；② 三个真实问题（Agent 不调检索工具直接作答 / Agent 打转不收敛 / 答案 scope 与 reference 不匹配）的官方或权威解法；③ LLM judge 的可复现性。
- **本文件性质**：**冻结记录**，只回答"外面是什么样"，不随本项目代码回写数字；结论后来被推翻时**不删原文**，改在头部标「后续修正见 xxx」或另写一篇。**决策归 `docs/adr/`**，本次调研不承担"我们定怎么做"的责任。

> **证据强度标注约定**（正文以标签区分）：
> - **[官方明确]** RAGAS / LangGraph 官方文档或官方源码里能直接引到原句/原公式。
> - **[官方源码/实测]** 本机依赖包源码可复核的行为，附 `path:line`。
> - **[维护者提案·未合并]** 官方仓库里由维护者/贡献者提出、但**未合入 main** 的改动，**不构成官方口径**。
> - **[权威第三方]** 官方之外的权威一手来源（论文、厂商工程博客）。
> - **[本文推断]** 无直接来源、由上述事实推导，需后续验证。

---

## 结论摘要

1. **[官方明确] `retrieved_contexts` 的官方语义是"检索器针对该 query 返回的文本块"**，其角色是被用来生成那条答案的上下文；RAGAS 原始论文将 `c(q)` 定义为"先检索、再据此生成答案 `a_s(q)`"的那个 context。官方**没有**任何一处把"多轮工具调用累积的全部产出"定义为 `retrieved_contexts`。→ 本项目把 `final_state["tool_contexts"]` **全部**铺平成 context 列表（`src/cli/eval_ragas.py:164`），**没有任何官方口径背书**，且与"针对该 query"的定义相悖。

2. **[官方明确] ContextPrecision 用的是 average precision，`K = retrieved_contexts 总条数`**，且**位置敏感**：官方文档明确写"无关 chunk 放在第 2 位分数不变，放在第 1 位分数下降"。→ 在一堆跨 query、未去重、未按相关性重排的累积 context 上跑它，等于让"相关块"被无关块稀释/压后，分数系统性地偏低。**这是 count/order 影响分数的官方机制**；"累积工具产出导致分数偏低"是 **[本文推断]**，但机制有官方公式支撑。

3. **[官方明确] 对 agentic 系统，RAGAS 官方提供的构造是 `MultiTurnSample` + Agent 指标族**（Agent Goal Accuracy / Tool Call Accuracy / Tool Call F1 / Topic Adherence），其中**工具产出以 `ToolMessage` 形式作为对话轨迹的一部分**被评估，而**不是**塞进 `retrieved_contexts`。→ 本项目用**单轮 RAG 指标**评一个**多轮 Agent**，属于范式错配；官方对"agentic 检索该喂哪些 context 给单轮指标"**没有明确规定**。

4. **[官方明确] AnswerCorrectness 的 FP 定义就是"答案里有、参考答案里没有的陈述"**；**[权威第三方]** 原始论文对 Answer Relevance 也写"惩罚答案不完整或含冗余信息"。→ 参考是"一句话 1 个事实"、答案却"综合多段"，多说的陈述**必然**算 FP、拉低 F1，这是**指标定义的直接后果，不是 bug**。官方**未给**"reference 粒度应与答案粒度对齐"的明文规定，此为 **[本文推断]**。

5. **[官方明确] LangGraph 用 `recursion_limit` + `GraphRecursionError` 兜底 agent 循环**（v1.0.6 起默认 1000 步，历史默认 25），并提供 `RemainingSteps` / `langgraph_step` 做**主动**降级；Anthropic 官方建议"用最大迭代数等 stopping conditions 维持控制"。→ "Agent 打转"有官方兜底手段，但**"去重 / 检测重复 query"没有官方特性**。

6. **[官方明确·有留白] 裁判可复现性没有官方保证。** RAGAS 只提供 `RunConfig.seed`（默认 42，**[官方源码]** 实为 numpy RNG 种子，`run_config.py:60-63`），**不控制 LLM 输出**；官方文档**没有**任何"temperature=0 即确定"的承诺。**[官方源码]** 现代 `InstructorLLM` 默认 `temperature=0.01`、`top_p=0.1`（`llms/base.py:717-727`），**默认并非 0**。→ 同配置重跑有波动属预期；需自建重复跑 / 多裁判方案，**不能假设 temperature=0 = 确定**。

7. **"我们这里是不是写错了"**：① context 喂法（全部累积工具产出）——**官方无背书、且被官方公式判为不利**，建议改（详见"待确认项"）；② temperature=0——**方向正确但不足以保证复现**；③ 用单轮指标评多轮 Agent——**范式错配**，官方对 agentic 另有指标族，建议补。

---

## 详述

### 一、`retrieved_contexts` 的正确定义与 agentic 用法

#### 1.1 官方语义：检索器针对该 query 的产出 / 用于生成答案的上下文

- **[官方明确]** 官方 Evaluation Sample 文档在 `SingleTurnSample` 示例里把 `retrieved_contexts` 注释为 *"Retrieved contexts (e.g., from a knowledge base or search engine)"*，即**检索器返回的上下文**（来源：<https://docs.ragas.io/en/stable/concepts/components/eval_sample/>）。
- **[权威第三方]** 原始论文把 RAG 流程定义为：*"given a question q, the system first retrieves some context c(q) and then uses the retrieved context to generate an answer a_s(q)"*；Faithfulness 的定义是 *"the answer a_s(q) is faithful to the context c(q) if the claims that are made in the answer can be inferred from the context"*（来源：<https://arxiv.org/html/2309.15217v2>）。→ **`c(q)` 是"那一条答案所依据的 context"**，是单 query ↔ 单答案 ↔ 单 context 集合的对应关系。
- **[维护者提案·未合并]** 官方仓库曾有一个 PR 想显式定义这两个字段（PR #2826 *"docs: clarify retrieved context fields for memory eval"*）：`retrieved_contexts` = *"Text chunks the retriever returned for the query"*，并注明 *"Each list item should be one atomic fact or chunk."*。**该 PR 于 2026-07-09 被关闭、未合并（`merged=false`）**，故**不作官方口径**，仅作维护者意图的旁证。（来源：<https://github.com/vibrantlabsai/ragas/pull/2826>，状态经 GitHub API `/pulls/2826` 核实。）

> **对本项目的直接含义**：本项目在 `src/cli/eval_ragas.py:164` 用 `[c.to_prompt_text() for c in final_state.get("tool_contexts", [])]` 把 **Agent 全程所有工具产出**（跨多次 `retrieve_kb` / `search_web`、不跨 query 去重、不取最终 top-k）铺平成一个 context 列表。这既不符合"针对该 query 的检索产出"（多次 query 混在一起），也不符合"每条一个 atomic chunk"（`to_prompt_text()` 渲染带有来源/页码锚点的整块）。**没有官方口径支持这种喂法。** **[本文推断]** 官方的"该 query 的检索产出"最贴近的候选是**最终 rerank 后交给生成模型的那批 chunk**，而非全部累积工具产出；但这一点官方未明说，属推断。

#### 1.2 ContextPrecision 是 average precision：K 与顺序都进公式

- **[官方明确]** 官方 Context Precision 文档给出公式与关键说明（来源：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/>）：
  - *"Where K is the total number of chunks in `retrieved_contexts`"* ——**K 就是 context 总条数**，没有截断/去重的余地。
  - *"Note that even if an irrelevant chunk is present at the second position in the array, context precision remains the same. However, if this irrelevant chunk is placed at the first position, context precision reduces"* ——**位置（顺序）直接影响分数**。
- **[官方源码]** 本机 0.4.3 实现与上述一致：逐条 context 让裁判给 0/1 verdict，再算 average precision（`ragas/metrics/collections/context_precision/metric.py:104-116`）；AP 公式 `denominator = sum(verdicts) + 1e-10`、`numerator = Σ (precision@i · verdict_i)`（同文件 `:118-138`）。
- **[本文推断] 机制层面的三条后果**（官方无原句，但由公式推出）：
  1. **无关块越靠前、相关块被压得越后 → AP 越低**。本项目 context 按"工具调用时间顺序"累积（早期轮次的检索排在前面），与"按相关性排序"相反，对 AP 不利。
  2. **末尾追加无关块，对 AP 可无影响**（官方 2 例已示）；真正致命的是**把无关块排到相关块前面**——而累积顺序恰恰做不到"相关优先"。
  3. **重复的相关块会进入 denominator（`sum(verdicts)`）**，且其 precision@k 取决于它出现的位置；跨 query 不去重会引入这种重复。"条数越多越吃亏"不是无条件成立，**取决于新增块的相关性与位置**，不能一概而论。

> **官方对"去重 / 截断 / 取最终 top-k"有无说明？** **[官方明确] 没有。** 官方文档、论文均未给去重或截断的操作建议。因此这块只能靠项目自定口径，且**必须在报告里写明口径**（否则数字不可比）。

#### 1.3 agentic 场景：官方构造是 MultiTurnSample + Agent 指标，工具产出走 `ToolMessage`

- **[官方明确]** 官方 Agent 指标文档给出四族指标：Topic Adherence、Tool Call Accuracy、Tool Call F1、Agent Goal Accuracy（含/不含 reference），全部建立在**多轮对话**上，消息类型是 `HumanMessage` / `AIMessage`(带 `tool_calls`) / `ToolCall` / `ToolMessage`（来源：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/>）。
- 同一文档明确：**工具产出以 `ToolMessage` 作为"被评估轨迹的一部分"**；Tool Call 类指标评判的是**调用（名字 + 参数）**，不评判工具产出正文；Goal 类指标看**终态**是否达成用户目标。文档**没有**为"agentic 检索的 context 质量"定义单独指标。
- **[官方明确]** Evaluation Sample 文档规定 `MultiTurnSample` 的 `user_input` 就是消息序列（含 `ToolMessage`），并给了"AI 调 WeatherAPI → ToolMessage → 最终回答"的完整示例（来源：<https://docs.ragas.io/en/stable/concepts/components/eval_sample/>）。

> **对本项目的含义**：RAGAS 官方对"Agent + 工具"的**原生**评测姿态是——把**整条轨迹**交给 Agent 指标族，而不是把工具的每个产出当作 `retrieved_contexts` 喂单轮 RAG 指标。本项目现在做的是后者。这**不是禁止**（单轮指标也可当"对最终答案的检索质量"的近似），但属于**官方未规定、需自负口径**的用法。

---

### 二、三个真实问题的官方 / 权威解法

#### 问题 1：Agent 完全不调用检索工具，用参数化知识直接作答

- **RAGAS 官方：** **无明确说法。** RAGAS 评测"给定 context 后的答案质量"，不管理"是否检索"；其原始论文把 RAG 描述为**默认先检索**的流程（*"the system first retrieves some context c(q)..."*），**未讨论 agent 放弃检索的情形**。
- **权威替代（可选其一/组合）：**
  - **[权威第三方] ReAct（Yao et al., 2022, arXiv:2210.03629）** 的框架本就允许模型"决定是否行动"；放弃工具是模型决策空间的合法取值——**要"保证检索"就不能只靠 ReAct 的自由决策**。
  - **[权威第三方] Anthropic《Building Effective Agents》**：*"workflows offer predictability and consistency for well-defined tasks, whereas agents are the better option when flexibility and model-driven decision-making are needed"*；*"we recommend finding the simplest solution possible... This might mean not building agentic systems at all"*。→ 若"必须先检索"是硬约束，官方给的方向是**用 workflow/固定路径（如强制检索节点/路由）**，而非寄望 agent 自觉（来源：<https://www.anthropic.com/engineering/building-effective-agents>）。
- **[本文推断] 可用缓解手段**（无单一官方出处）：① 在系统提示/工具描述里把"必须检索后作答"写成硬约束；② 图结构上做**强制检索前置节点**或"检索 → 生成"固定路径（Anthropic 的 workflow 路线）；③ 评测侧把"零工具调用"单列为**样本类别**并单独统计（本项目已有 `SAMPLE_CLASS_EMPTY` + 检索覆盖率门禁 `src/cli/eval_ragas.py:738`，方向正确）。

#### 问题 2：Agent 打转——反复发同一 query、不收敛、末了没给正式答案

- **[官方明确] LangGraph 的官方兜底是 `recursion_limit`**（来源：<https://docs.langchain.com/oss/python/langgraph/graph-api>）：
  - *"The recursion limit sets the maximum number of super-steps the graph can execute during a single execution."*
  - *"Starting in version 1.0.6, the default recursion limit is set to 1000 steps."*（**历史默认是 25**，见下条社区观测，需按实际版本核对）
  - 超限抛 `GraphRecursionError`；`recursion_limit` 是 `config` 的**顶层键**，不能在 `configurable` 里传：`graph.invoke(inputs, config={"recursion_limit": 5})`。
  - **官方提供主动降级**：`config["metadata"]["langgraph_step"]` 可在节点内读当前步数；托管值 `RemainingSteps` 跟踪剩余步数，可在图内条件路由做**优雅退出**。
- **[权威第三方] Anthropic《Building Effective Agents》**：*"it's also common to include stopping conditions (such as a maximum number of iterations) to maintain control"*；并提醒*"the autonomous nature of agents means higher costs, and the potential for compounding errors"*。
- **[权威第三方] 自反思模式**：Reflexion（Shinn et al., 2023, arXiv:2303.11366）提出"用语言化自我反思改进后续尝试"；LangGraph 官方亦提供 reflection 教程模式。**注意**：反思是**改善收敛**的手段，**不**解决"重复调用同一工具且参数完全相同"的死循环。
- **[官方明确] "检测重复 query / 调用去重"——LangGraph 无此内建特性。** 官方只给"计步 + 超限 + 剩余步数"三件套；**语义级去重需自建**（如节点内缓存已发出的 query、命中即短路）。
- **[本文推断] 落地**：本项目 `max_iterations` 若已设，等于 LangGraph `recursion_limit` 的语义；建议同时：(a) 确认 `recursion_limit` 数值合理且能触发 `GraphRecursionError`；(b) 在工具层对"相同 (tool, args)"做去重短路；(c) 评测侧把"未产出正式答案"单列为失败类别（现有 `trajectory` 已记 `tool_calls`/`iterations`，可支撑 `src/cli/eval_scoring.py:136-154`）。

#### 问题 3：答案 scope 与 reference 不匹配（多说算 FP、少说算 FN）

- **[官方明确] AnswerCorrectness 的 FP/FN 定义就是按陈述方向切的**（来源：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_correctness/>）：
  - *"FP (False Positive): Facts or statements that are present in the generated answer but not in the ground truth."*
  - *"FN (False Negative): Facts or statements that are present in the ground truth but not in the generated answer."*
  - 计分 `F1 = |TP| / (|TP| + 0.5·(|FP| + |FN|))`，再与语义相似度加权（默认 0.75/0.25）。**[官方源码]** 一致：`answer_correctness/metric.py:221-249`。
  - → **参考一句话、答案一大段：多出的陈述直接进 FP**，F1 分母变大、分数下降。这是**指标定义的必然结果**。
- **[官方明确] Faithfulness 同理**：答案被拆成原子陈述，逐条对 `"\n".join(retrieved_contexts)` 做 NLI，分数 = 被支持陈述 / 总陈述（来源：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/>；**[官方源码]** `faithfulness/metric.py:116-163`）。→ 答案多说的陈述**若 context 里没有**就进分母、拉低分数。
- **[权威第三方] 原始论文**对 Answer Relevance 也写：*"penalises cases where the answer is incomplete or where it contains redundant information"* —— **冗余即惩罚**是 RAGAS 的设计取向，不只是实现细节。
- **官方对"reference 粒度应与答案粒度对齐"有无明文？** **[官方明确] 没有。** 文档只给"高/低 correctness"的短例（如 ground truth `Einstein was born in 1879 in Germany.`），未规定参考长度或"参考该多细"。→ **这是 RAGAS 的一个公认留白**；社区 issue 里有大量 `AnswerCorrectness` / `FactualCorrectness` 的计分争议（#2585 *"answer_correctness is not working as expected"* 仍 open；#2724 指出 `AnswerCorrectness` 与 `FactualCorrectness` 对同一输入给出矛盾分，因两侧陈述拆分不一致；#2693/#2701/#2768/#2868/#3003 是 `FactualCorrectness` 的 TP 方向 bug 群——**均为 bug 修复类**，非粒度指导。来源：GitHub API 搜索 repo:vibrantlabsai/ragas）。
- **[本文推断] 缓解方向**：① 若想看"答案有没有覆盖参考事实"用 **ContextRecall**（官方文档：*"always requires a reference... the reference is broken down into claims... whether it can be attributed to the retrieved context"*）；② 若答案天然比参考长，**不要**用逐陈述 F1 型指标衡量"正确性"，或改用**把参考也升级为同等粒度的综合参考答案**；③ 用 `reference_contexts`（官方 Evaluation Sample 支持的字段）而非 `reference` 来做检索侧评估。**这些均为推断，官方无对应明文。**

---

### 三、裁判（LLM judge）的可复现性

- **[官方明确] 官方文档对"judge 复现性 / temperature=0 是否确定"无任何承诺或指导。** "Customise models" 页只讲如何换模型、传 `temperature` 等参数，**未讨论重跑波动**（来源：<https://docs.ragas.io/en/stable/howtos/customizations/customize_models/>）。[官方明确] "Agents"、"Context Precision" 等指标页亦未提及。
- **[官方明确] `RunConfig.seed` 不是 LLM 种子**：官方文档把它描述为 *"Random seed for reproducibility, by default 42"*，但 **[官方源码]** 明确指出它只是初始化一个 **numpy RNG**（`ragas/run_config.py:60-63`：`self.rng = np.random.default_rng(seed=self.seed)`），服务于 ragas 内部随机操作（如测试集生成、采样），**不控制裁判 LLM 的采样**（来源：<https://docs.ragas.io/en/stable/references/run_config/>）。
- **[官方源码] RAGAS 的裁判默认 temperature 不是 0：**
  - 现代 `InstructorLLM` 的 `InstructorModelArgs` 默认 `temperature=0.01`、`top_p=0.1`（`ragas/llms/base.py:717-727`；代码注释自称 *"deterministic defaults"*，但 **0.01 ≠ 0**）。
  - 旧式 wrapper 默认 `temperature=0.01`（`n>1` 时 `0.3`，`get_temperature`，`ragas/llms/base.py:71-73`、`:80/:90/:104`）。
  - → 若调用方不显式设 `temperature=0`，RAGAS 自己给的就是**非零**温度。**这也解释了大量社区 issue：**
    - **[维护者提案·未合并]** #2722 *"fix: respect underlying LLM's temperature when caller doesn't set one"*：wrapper 把用户设的温度覆盖成自带启发式（n=1→0.01），导致 `ResponseRelevancy` 对所有问题**输出雷同**（非期望的"确定"）。
    - **[维护者提案·未合并]** #2817：重试路径用 `temperature=0.01`，确定性的模型每次重试返回**同样的坏输出**，重试全部浪费。
    - **[维护者提案·未合并]** #2781：wrapper 覆盖用户温度导致 o3 等只接受 `temperature=1.0` 的模型 400。
    - （以上均 open/未合并，来源：GitHub API 搜索 repo:vibrantlabsai/ragas。）
- **[本文推断] "temperature=0 是否保证确定"**：**不保证。** 即便设 temperature=0，仍可能因**推理后端的批处理/并行归约顺序、MoE 路由、浮点非结合性、结构化输出重试**等产生逐次差异；RAGAS 官方对此既无承诺也无测试。→ 评价一个判据（如门禁是否 PASS）时，应至少：(a) 记录裁判模型与**显式 temperature**；(b) 对关键样本**重复跑**取分布而非单点；(c) 考虑多裁判/多数投票。**官方无对应明文，属工程推断。**

---

## 反面与风险（我们的口径可能错在哪、过度解读在哪）

1. **"累积全部工具产出"未必一定是"错"**：Agent 的多轮检索产出确实**都进入了最终生成的上下文窗口**，从"生成时可用的证据"角度看，把它们都算作 context 有自洽的解读。RAGAS 官方**没有**禁止这种用法——只是**没有背书**，且其 AP 公式对"顺序/条数"敏感，会系统性压低分数。**不要把"分数低"直接等同于"检索差"**：也可能只是**喂法**导致的度量伪影（artefact）。
2. **PR #2826 未合并**：那条"每条 context 应是一个 atomic chunk / 针对该 query"的明文化**没有进 main**。引用它时必须标"维护者提案，未生效"，**不能当成官方规定**。
3. **LangGraph 默认 recursion_limit 版本差异**：官方现文档写"v1.0.6 起默认 1000"，社区大量旧文写"默认 25"。本项目实际 LangGraph 版本需以 `pip show langgraph` / 源码为准，**别照抄网上数字**。
4. **不要把 Agent 指标与单轮指标混为一谈**：Agent 指标评的是**调用与目标**，不是**答案对 context 的忠实度**。二者互补，不是替代。
5. **社区 issue 多为 bug 修复**：`FactualCorrectness` 的 TP 方向 bug 群说明**指标实现历史上确有问题**；用旧版本时分数可能受实现 bug 影响，升级/核对版本是必要动作。

---

## 待确认项（留给决策阶段，非本文件结论）

1. **context 喂法采用哪种官方可辩护口径？** 候选：(a) 仅最终 rerank top-k；(b) 最终答案所依据的那批 chunk；(c) 全部累积但**跨 query 去重 + 按相关性重排**。三者官方都未明文，**需在 ADR/design 里显式选定并写清代价**。
2. **是否为多轮 Agent 补 Agent 指标族**（AgentGoalAccuracy / ToolCallAccuracy / ToolCallF1），与现有单轮 RAG 指标并存？官方对 agentic 的"原生"姿态是前者。
3. **`recursion_limit` 当前实际取值与本机 LangGraph 版本**（决定默认步数语义与是否可能静默不触发）。
4. **裁判稳定性方案**：重复跑次数 / 多裁判 / 是否把 `RunConfig` 的 `seed` 显式设定（并认清它不控 LLM）。
5. **参考粒度口径**：`reference` 是"一句话事实"还是"与答案同粒度的综合答案"？——直接决定 AnswerCorrectness 的基线。

---

## 来源

**RAGAS 官方文档（docs.ragas.io，一手）**
- Evaluation Sample（`SingleTurnSample` / `MultiTurnSample` 字段与示例）：<https://docs.ragas.io/en/stable/concepts/components/eval_sample/>
- Context Precision（K = context 总条数、位置敏感、With/WithoutReference 区分）：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/>
- Context Recall（需 reference，拆 claim 归因）：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/>
- Faithfulness（拆陈述 + NLI，比例公式）：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/>
- Answer Correctness（TP/FP/FN 定义、F1 + 相似度加权）：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_correctness/>
- Agent Evaluation Metrics（Agent Goal Accuracy / Tool Call Accuracy / Tool Call F1 / Topic Adherence，多轮消息）：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/>
- RunConfig（`seed` 默认 42 及其语义）：<https://docs.ragas.io/en/stable/references/run_config/>
- Customise models（传 temperature 等参数，无复现性指导）：<https://docs.ragas.io/en/stable/howtos/customizations/customize_models/>

**官方仓库（vibrantlabsai/ragas；原 `explodinggradients/ragas` 已改名/重定向）**
- PR #2826 "docs: clarify retrieved context fields for memory eval" —— **closed 未合并**（2026-07-09）：<https://github.com/vibrantlabsai/ragas/pull/2826>（状态经 `api.github.com/repos/vibrantlabsai/ragas/pulls/2826` 核实）
- 相关 issue/PR（经 GitHub API 检索，含 #2585、#2722、#2724、#2781、#2817、#2693/#2701/#2768/#2868/#3003、#3024 等）：<https://github.com/vibrantlabsai/ragas/issues>

**论文（权威第三方）**
- Es et al. *Ragas: Automated Evaluation of Retrieval Augmented Generation*, arXiv:2309.15217（v2, 2025-04-28）：<https://arxiv.org/abs/2309.15217> / 全文 <https://arxiv.org/html/2309.15217v2>
- Yao et al. *ReAct: Synergizing Reasoning and Acting in Language Models*, arXiv:2210.03629：<https://arxiv.org/abs/2210.03629>
- Shinn et al. *Reflexion: Language Agents with Verbal Reinforcement Learning*, arXiv:2303.11366：<https://arxiv.org/abs/2303.11366>

**LangGraph / Anthropic 官方**
- LangGraph Graph API（`recursion_limit`、`GraphRecursionError`、`RemainingSteps`、`langgraph_step`）：<https://docs.langchain.com/oss/python/langgraph/graph-api>
- Anthropic, *Building Effective Agents*（stopping conditions / workflows vs agents）：<https://www.anthropic.com/engineering/building-effective-agents>

**本机依赖实测（ragas 0.4.3，`/opt/venvs/corporate_rag/lib/python3.12/site-packages/ragas/`）**
- ContextPrecision 逐条 verdict + average precision：`metrics/collections/context_precision/metric.py:104-138`；prompt：`.../util.py:31`
- Faithfulness 拆陈述 + `"\n".join(contexts)` NLI + 比例：`metrics/collections/faithfulness/metric.py:116-163`
- AnswerCorrectness F1（TP/FP/FN）+ 加权：`metrics/collections/answer_correctness/metric.py:221-249`（权重默认 `[0.75, 0.25]`，`:83`）
- ContextRecall 拆 claim 归因比例：`metrics/collections/context_recall/metric.py`（`ascore`）
- 裁判默认温度：`llms/base.py:71-73`（旧式 `0.01/0.3`）、`llms/base.py:717-727`（`InstructorModelArgs` `temperature=0.01, top_p=0.1`）
- RunConfig `seed` 仅初始化 numpy RNG：`run_config.py:60-63`

**本项目代码（引用附 `path:line`）**
- context 取自全部工具产出：`src/cli/eval_ragas.py:164`
- context_precision 传 `reference`（= WithReference）：`src/cli/eval_ragas.py:347-352`
- 指标装配：`src/cli/eval_scoring.py:41-47`
- 检索覆盖率门禁：`src/cli/eval_ragas.py:738`
- 轨迹摘要（`tool_calls` / `iterations`）：`src/cli/eval_scoring.py:136-154`
