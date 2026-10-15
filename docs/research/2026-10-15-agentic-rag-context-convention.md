# 多轮 / agentic RAG 离线评测该喂什么作 `context`（外部事实调研）

- **调研日期**：2026-10-15
- **调研方式**：一手来源检索——RAGAS 官方文档（docs.ragas.io）、官方仓库 `vibrantlabsai/ragas` 的**官方示例源码**（`examples/ragas_examples/`）与 issue/PR、DeepEval（confident-ai）官方文档、LangChain/LangSmith 官方文档、TruLens 官方文档、Arize Phoenix 官方文档、LlamaIndex 官方文档，以及论文（RAGAS / ARES / RAGChecker / MultiHop-RAG）。引用外部事实附 URL；官方示例代码附 `仓库路径`。
- **范围**：只回答一个问题——**多轮 / agentic RAG 做离线评测时，应把什么当作喂给评测指标的 `context`（`retrieved_contexts`）？** 三个候选口径：(a) 仅最终 rerank 后交给生成模型的那批 chunk；(b) 最终答案实际依据/引用的那批 chunk；(c) 全部轮次累积检索到的 chunk（跨 query 去重 + 按相关性重排）。
- **本文件性质**：**冻结记录**，只回答"外面是什么样"，不随本项目代码回写，不承担"我们定怎么做"的责任。结论后被推翻时**不删原文**，改在头部标「后续修正见 xxx」或另起一篇。**决策归 `docs/adr/`**。
- **前置结论（不再重复论证）**：见 `docs/research/2026-10-15-ragas-context-and-failure-modes.md`。要点：① RAGAS 官方术语里 `retrieved_contexts` = 检索器针对该 query 返回的文本块；② ContextPrecision 的 `K = retrieved_contexts 总条数` 且**位置敏感**；③ RAGAS 对 agentic 的原生构造是 `MultiTurnSample` + Agent 指标族，工具产出走 `ToolMessage`；④ 本项目现状是把 Agent **全部累积工具产出**（不去重、不重排）铺平喂进去（`src/cli/eval_ragas.py:164`），实测被系统性压低分。

> **证据强度标注约定**（正文以标签区分）：
> - **[官方明确]** 官方文档/官方源码里能直接引到原句/原公式。
> - **[官方源码]** 官方仓库源码可复核，附仓库路径。
> - **[贡献者答复·非官方]** 官方仓库 issue 下由 `CONTRIBUTOR`（非核心维护者）给出的答复，**不构成官方口径**。
> - **[维护者提案·未合并]** 提出但未合入 main 的改动。
> - **[权威第三方]** 论文、厂商工程博客。
> - **[本文推断]** 无直接来源的推导，需后续验证。

---

## 结论摘要

1. **没有任何权威来源给出"agentic 多轮累积 context 该怎么喂"的官方明文。** 三家主流框架（RAGAS / DeepEval / LangSmith / TruLens / Phoenix / LlamaIndex）对 `context`/`retrieved_contexts` 的**明文字面定义全部落在"检索器返回的块"**上，而且都是**单 query ↔ 单答案 ↔ 单 context 集合**的对应关系。**没有任何一家的文档把"跨多轮工具调用累积的全部产出"定义为 context。** → **(c) 无任何明文背书。**

2. **RAGAS 官方自己的 agentic 示例，直接放弃了构建 `retrieved_contexts`。** 官方教程《Evaluate and improve RAG》的示例代码里，agentic 分支明确返回 `"retrieved_documents": []` 并注释 *"In agentic mode, the agent controls retrieval internally so we don't return specific retrieved documents"* / *"Cannot determine exact count from agent execution"*（`examples/ragas_examples/improve_rag/rag.py`），且评测只用"答案 vs 参考答案"的 `DiscreteMetric`，**根本不走 context 类指标**。官方 agent / workflow 两篇教程同样只用答案级指标。→ **这是"agentic 下不要硬凑 context"的最强官方信号。**

3. **口径 (a)（最终 rerank 后交给生成模型的那批 chunk）是主流框架的实际做法，但靠的是"惯例"而非"agentic 明文"。** DeepEval 的 `retrieval_context`、LangSmith 的 `documents`、TruLens 的 groundedness、LlamaIndex 的 `source_nodes`、Phoenix 的 per-step retrieval context，**引入的都是"检索/精排后送进生成的那批块"**，且明确把"排序"当作 precision 的度量对象。→ **(a) 有最广的（单轮）明文支撑。**

4. **口径 (b)（答案实际引用/依据的那批 chunk）在主流框架里没有对应字段。** 六家框架**没有一家**用 "cited/used contexts" 作为 faithfulness/groundedness 的默认输入——它们全部用 **retrieved context**。"引用是否指向真实支撑块"被单列为另一个轴（**Citation Accuracy / attribution**），与 faithfulness **解耦**。→ **(b) 不是"喂给 context 指标的 context"，而是另一种指标。**

5. **去重 / 截断：主流框架与官方文档均无明文建议。** Phoenix 是最接近"合并多项"的一家，但它的合并限定在**单个 retrieval step 内**（*"all returned items joined together"*），**不跨 step 累积、不去重**。RAGAS 官方文档对"去重 / 截断 / 取最终 top-k"**均无任何说明**（前一篇已确认）。→ **"跨 query 去重 + 重排"属工程自定口径，无明文来源。**

6. **有证据支撑的推荐（分两轴，详见"详述·第四节"）：**
   - **检索质量轴（上下文精确率/召回率）** → 用 **(a)**：即"某一次检索/精排后，真正进入生成 prompt 的那批块"。多轮场景下**按 retrieval step 分别评**（对齐 Phoenix / DeepEval 的 per-step 姿态），**或**只评最终那次检索；**不要把跨轮全部产出拼成一个大列表**（会破坏单 query↔单 context 对应、并因 AP 位置敏感而系统性压分）。
   - **忠实度轴（faithfulness/groundedness）** → 用 **"生成器实际看到的 context"**（多轮 agent 里通常就是累积证据，但**喂前应去重+按相关性重排**）。这是 **(c) 去掉"不去重/不重排"后的形态**，其"去重/重排"部分为 **[本文推断]**（无框架明文），"用生成器所见 context"部分有 TruLens/LangSmith 的 retrieved-context 惯例支撑。
   - **(b)** 不建议作为 context 指标的输入；若要衡量"引用是否可信"，单列 **Citation Accuracy**。

   **一句话**：**没有任何"官方明文"直接规定 agentic 该喂 (a)/(b)/(c)**；官方明文只覆盖单轮 (`retrieved_contexts` = 检索器对该 query 的产出)。在有明文可循的范围内，**(a) 是唯一被六家框架一致实践的口径**；(c) 只有在"去掉不去重/不重排、且限定为生成器所见"之后才勉强可辩护；(b) 完全落在另一条指标轴上。

---

## 详述

### 一、六家框架对 `context` 的字面定义（对比表）

| 来源 | 明文字面定义 | 对应候选口径 | 是否有 agentic/多轮明文 |
|---|---|---|---|
| **RAGAS**（文档 + 论文） | `retrieved_contexts` = *"Retrieved contexts (e.g., from a knowledge base or search engine)"*，即**检索器针对该 query 返回的文本块**；`c(q)` 是"先检索、据此生成 `a_s(q)`"的那个 context | **单轮 (a)** | ❌ 无。agentic 原生走 `MultiTurnSample` + Agent 指标（工具产出走 `ToolMessage`），**不为 context 指标定义投喂法** |
| **RAGAS 官方示例**（`improve_rag`） | naive 模式显式返回 `retrieved_documents`；**agentic 模式返回 `[]`** 并注明"无法从 agent 执行确定条数" | agentic 下**回避** | ✅ 有——但结论是"**不构建 context**"，评测退回答案级指标 |
| **DeepEval** | `retrieval_context` = *"the actual retrieved context from your RAG pipeline"*；Contextual Precision 明说评的是 *"the ranking order of the text chunks in the `retrieval_context`"* | **(a)** | ❌ 无（单轮 metric；多轮有另设的 Knowledge Retention 等） |
| **LangSmith / LangChain** | 教程里返回 `{"answer": ..., "documents": docs}`，`docs = retriever.invoke(question)`（`k=6`）；groundedness 拿 `outputs["documents"]` 与答案比 | **(a)** | ❌ 无（字段名是 `documents`，语义是 retrieved） |
| **TruLens** | Context Relevance 评"每个检索到的 chunk 是否相关"；Groundedness *"search for evidence that supports each [claim] within the **retrieved context**"* | **(a)** | ❌ 无 |
| **Arize Phoenix** | Retrieval Relevance 的 `context` = *"The external information retrieved during the **step**, with all returned items joined together"*，*"scores the retrieved information as a whole — holistically, **per retrieval step**"* | **(a)，且明确"按步"** | ✅ 部分——**限定单 step 内合并，不跨 step 累积** |
| **LlamaIndex** | Response Synthesizer *"generates a response ... using a user query and a given set of retrieved text chunks"*；`no_text` 模式"只跑检索、不发给 LLM，其节点可通过 `response.source_nodes` 查看" | **(a)**（`source_nodes` = 送去合成的节点，即检索/后处理之后） | ❌ 无 |
| **论文·MultiHop-RAG** | 多跳 query 的证据集 `ℛq = {r₁..r_K}`，*"the chunks in the retrieval set ℛq collectively provide an answer"*；检索侧**用 retrieved set 与 gold evidence 比对**（MAP@K / MRR@K / Hit@K） | 检索侧 **(a)**（top-K retrieved set） | ⚠️ 讨论多跳检索，但"证据集"= 检索到的 top-K，**未讨论 agent 累积轮次** |
| **论文·ARES** | 三维：context relevance / answer faithfulness / answer relevance；对手注的 *"passages to retrieve"* 评检索侧 | 检索侧 **(a)** | ❌ 无 |
| **论文·RAGChecker** | claim 级蕴含判断，把指标拆成**检索模块指标**与**生成模块指标**两组 | 检索侧评 retrieved chunks | ❌ 无（未讨论多轮累积投喂） |

> 读表结论：**无论文档还是论文，明文字面定义都指向 (a)（该次检索返回/精排后的块）**；**没有一行明文支持 (c) 的"跨轮累积"**；**(b) 在表中没有对应字段**。

---

### 二、逐条一手来源

#### 2.1 RAGAS 官方：单轮有定义，agentic 无定义（且官方示例主动回避）

- **[官方明确] 单轮定义**：Evaluation Sample 文档把 `retrieved_contexts` 注释为 *"Retrieved contexts (e.g., from a knowledge base or search engine)"*（<https://docs.ragas.io/en/stable/concepts/components/eval_sample/>）。这就是"检索器返回的块"。
- **[官方明确] ContextPrecision 的 K 与位置敏感性**：*"Where K is the total number of chunks in `retrieved_contexts`"*；*"even if an irrelevant chunk is present at the second position ... context precision remains the same. However, if this irrelevant chunk is placed at the first position, context precision reduces"*（<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/>）。
- **[官方明确] agentic 的原生姿态 = `MultiTurnSample` + Agent 指标，工具产出走 `ToolMessage`**，**不**塞进 `retrieved_contexts`（<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/>；<https://docs.ragas.io/en/stable/concepts/components/eval_sample/>）。

**关键新证据——官方教程的示例代码（此前调研未覆盖）**：

- **[官方源码] 官方教程《Evaluate and improve RAG》**（教程索引：<https://docs.ragas.io/en/stable/tutorials/>）配套示例 `examples/ragas_examples/improve_rag/rag.py`：
  - naive 模式：`docs = self.retriever.retrieve(question, top_k)`，返回 `"retrieved_documents": [{"content": doc.page_content, ...}]`——**就是检索到的块**。
  - agentic 模式：agent 用 `@function_tool def retrieve(query)` 自行检索，返回：
    ```python
    # In agentic mode, the agent controls retrieval internally
    # so we don't return specific retrieved documents
    return {
        "answer": result.final_output,
        "retrieved_documents": [],   # Agent handles retrieval internally
        "num_retrieved": 0,          # Cannot determine exact count from agent execution
        ...
    }
    ```
  - 评测脚本 `examples/ragas_examples/improve_rag/evals.py` 对两种模式**都只用** `DiscreteMetric(name="correctness", prompt="... compare response to expected answer ...")`——**完全不喂 context 指标**。
  - 源码位置：`https://raw.githubusercontent.com/vibrantlabsai/ragas/main/examples/ragas_examples/improve_rag/rag.py` 与 `.../evals.py`。
- **[官方明确] 官方《Evaluate an AI Agent》教程**（<https://docs.ragas.io/en/stable/tutorials/agent/>）用 `@numeric_metric(name="correctness")` 比较 agent 输出与期望值；**不构建 `retrieved_contexts`**。
- **[官方明确] 官方《Evaluate an AI Workflow》教程**（<https://docs.ragas.io/en/stable/tutorials/workflow/>）用 `DiscreteMetric` 比较 response 与 pass criteria；**同样不构建 context**。

→ 官方对 agentic 的实际示范是：**要么用答案级指标（correctness），要么用 Agent 指标族（多轮轨迹）；不硬凑 `retrieved_contexts`。** 这与前一篇结论（官方对 agentic 检索该喂哪些 context **没有明确规定**）完全一致，并给出了更强的一手佐证——**官方示例是"显式返回空"**。

- **[贡献者答复·非官方] RAGAS issue #1953** *"What to include in context precision/recall for RAG LLM evaluation"*（<https://github.com/vibrantlabsai/ragas/issues/1953>）：提问者问"该喂 `instruction+retrieved content+chat history` 还是只喂 `retrieved content`"。答复人 `sahusiddharth`（`author_association: CONTRIBUTOR`，**非核心维护者**）称：
  > *"Context precision measures whether the retrieved chunks were actually helpful in generating the response. ... If your RAG architecture uses instruction, chat history, and the retrieved context together during generation, then your evaluation should consider the combined context. However, if you're solely evaluating the effectiveness of the retrieval component, then you'd use just the retrieved content."*（经 GitHub API `/issues/1953/comments` 核实作者与 association）
  - 该答复的含义偏向 **(a) 的"生成时实际使用的那套"**（甚至延伸到把 instruction/history 也算进去），**但它不是官方文档、不是维护者口径**，只能作为"社区惯例"的旁证，**不构成明文**。
- **[维护者提案·未合并] PR #3039** *"docs: clarify without-reference context precision inputs and dedupe e…"*（<https://github.com/vibrantlabsai/ragas/pull/3039>）实为**纯文档改动**（给 `LLMContextPrecisionWithoutReference` 等补 docstring、去重一段重复示例），PR 正文自述 *"No behavior change — docs only"*，**与"是否去重 context"无关**。**不要**据标题误读为官方给了去重建议。
- **[官方明确] issue #2013**（<https://github.com/vibrantlabsai/ragas/issues/2013>）复现了位置对 ContextPrecision 的影响（相关块在第 1/2/3 位分别得 1.0 / 0.5 / 0.33），并指出文档措辞"proportion of relevant chunks"具有误导性——**印证 AP 位置敏感是既定实现，不是 bug**。

#### 2.2 DeepEval：`retrieval_context` = 检索到的块，precision 评 rerank 排序

- **[官方明确]** `retrieval_context` 注释为 *"the actual retrieved context from your RAG pipeline"*；Contextual Precision *"focused on evaluating the re-ranker ... by assessing the ranking order of the text chunks in the `retrieval_context`"*（<https://docs.confident-ai.com/docs/metrics-contextual-precision>）。
- **[官方明确]** 公式分母：`k` 是 `retrieval_context` 中的第 (i+1) 个节点，**`n` 是 `retrieval_context` 的长度（按 `source` 分组后）**；`rk ∈ {0,1}` 为第 k 个节点的相关性；整体是*加权累积精确率*（WCP）。文档还给了"节点相关但 precision 仍低"的 FAQ：*"Relevant nodes buried beneath noise in the `retrieval_context` drop the score even when everything was retrieved — implicating your re-ranker or top-K ordering."*
- **→ DeepEval 明确把 `retrieval_context` 当作"精排后、喂给生成的排序列表"，并**没有**"cited/used"概念、也**没有**多轮累积口径。

#### 2.3 LangSmith / LangChain：`documents` = `retriever.invoke()` 的返回

- **[官方明确]**《Evaluate a RAG application》教程（<https://docs.langchain.com/langsmith/evaluate-rag-tutorial>）中：`retriever = vectorstore.as_retriever(k=6)`；`rag_bot` 返回 `{"answer": ai_msg.content, "documents": docs}`；evaluator 里 `doc_string = "\n\n".join(doc.page_content for doc in outputs["documents"])`，groundedness 用 `outputs["documents"]` 对答案判忠实、retrieval_relevance 用它对问题判相关。
- **→ LangSmith 的约定是：context = **检索器返回的文档**（retrieved），不是答案里引用的那批。字段名甚至是 `documents` 而非 `contexts`。

#### 2.4 TruLens：groundedness 对 "retrieved context"

- **[官方明确]** RAG Triad 文档（<https://www.trulens.org/getting_started/core_concepts/rag_triad/>）：Context Relevance *"to verify the quality of our retrieval, we want to make sure that each chunk of context is relevant to the input query"*；Groundedness *"we can separate the response into individual claims and independently search for evidence that supports each within the **retrieved context**"*。
- **→ TruLens 同样用 retrieved context；文档未区分 retrieved vs cited（引用的仍是检索到的块）。**

#### 2.5 Arize Phoenix：per-step 的 retrieved information，合并成单个 context

- **[官方明确]** Retrieval Relevance（<https://arize.com/docs/phoenix/evaluation/pre-built-metrics/retrieval-relevance>）：
  - *"It is source-agnostic: the retrieved information may come from a vector-database / semantic search, a tool or function call, an MCP server, a web search, or a database query. It scores the retrieved information as a whole — holistically, **per retrieval step** — against the request."*
  - 输入：`input` = *"The request the retrieval was serving"*；`context` = *"The external information retrieved during the step, **with all returned items joined together**"*。
  - 级别：**Span** 级——*"Apply to the span that performed the retrieval. ... Relevant span kinds: `RETRIEVER` and `RERANKER` spans, `TOOL` spans that return information..."*
  - 与 faithful 明确分工：*"This evaluator judges the retrieved information against the request — it is independent of any final answer. To judge whether the answer is grounded in the context, use the Faithfulness Evaluator."*
- **→ Phoenix 是"最接近合并多项"的一家，但它把范围钉死在"单个 retrieval step"（span 级），并把 Faithfulness 拆成另一个指标。这为"多轮场景**按步分别评**"提供了最强的框架级依据，而不是"跨步累积成一个 context"。**

#### 2.6 LlamaIndex：`source_nodes` = 送去合成的节点（检索/后处理之后）

- **[官方明确]** Response Synthesizer（<https://developers.llamaindex.ai/python/framework/module_guides/querying/response_synthesizers/>）：*"A Response Synthesizer is what generates a response from an LLM, using a user query and a given set of retrieved text chunks."*；`no_text` 模式 *"Only runs the retriever to fetch the nodes that would have been sent to the LLM, without actually sending them. Then can be inspected by checking `response.source_nodes`."*
- **[官方明确]** RAG 入门（<https://developers.llamaindex.ai/python/framework/understanding/rag/>）：*"Node Postprocessors ... takes in a set of **retrieved nodes** and applies transformations, filtering, or re-ranking logic to them."*
- **→ `source_nodes` 的语义是"经过检索与 node postprocessor（含精排/过滤）之后、交给 synthesizer 生成的那批节点"，即 **(a)**。LlamaIndex 未提供"答案实际引用节点"作为一等概念。

#### 2.7 论文

- **[权威第三方] RAGAS 论文**：RAG 流程 *"first retrieves some context c(q) and then uses the retrieved context to generate an answer"*；Faithfulness *"faithful to the context c(q)"*（<https://arxiv.org/abs/2309.15217>）。→ `c(q)` = 该条答案所依据的 context（单 query 单答案）。
- **[权威第三方] ARES**（Saad-Falcon et al., NAACL 2024, <https://arxiv.org/abs/2311.09476>）：*"evaluating RAG systems along the dimensions of context relevance, answer faithfulness, and answer relevance"*，检索侧对"passages to retrieve"评判。→ 检索侧用 retrieved passages。
- **[权威第三方] RAGChecker**（Ru et al., <https://arxiv.org/abs/2408.08067>）：*"a suite of diagnostic metrics for both the retrieval and generation modules"*，claim 级蕴含判断。→ 把**检索模块**与**生成模块**分开评，检索侧评 retrieved chunks；**未讨论多轮累积投喂**。
- **[权威第三方] MultiHop-RAG**（Tang & Yang, <https://arxiv.org/abs/2401.15391>）：多跳 query 用**证据集** `ℛq = {r₁, …, r_K}`，*"the chunks in the retrieval set ℛq collectively provide an answer to q"*；检索侧 *"compare the retrieved set with the ground truth evidence associated with each query"*，用 MAP@K / MRR@K / Hit@K；生成侧用 top-K（或 gold evidence）喂 LLM。→ **多跳场景的"检索证据集"仍是"检索到的 top-K"，未把 agent 多轮中间轮次累加进去。**

---

### 三、"retrieved" vs "used / cited" 的区分

- **六家框架的共同实践**：faithfulness / groundedness / context precision 的输入**都是 retrieved context**，**没有一家**默认改用"答案实际引用/依据的块"（见第一、二节逐条来源）。
- **"引用"被单列为另一条轴**：业界把"引用是否真的指向支撑该句的块"称作 **Citation Accuracy / attribution**，与 **Faithfulness 解耦**——即"回答忠实于 context"与"引用标注是否正确"是两件事（社区工程文普遍如此主张，如 <https://baijiahao.baidu.com/s?id=1868677661476515727>；**[权威第三方/社区惯例]**）。RAGAS 官方**未**提供 citation 类指标（其指标族里没有 Citation Accuracy）。
- **[本文推断]** 由此，(b) 的更合适归宿是"另设一个 citation/attribution 指标"，而**不是**把它当作喂给 `retrieved_contexts` / context precision 的输入——因为主流框架的 context 指标默认口径就是 retrieved。
- **[本文推断] 一个可辩护的例外**：若某系统的"引用"信息**本身就在生成时可见**（例如模型被要求打标 `[1][2]`），那么"答案实际依据的块"是 retrieved 集合的一个**子集**；用它替代 retrieved 集合会**改变分数的语义**（precision 会因分母变小而变化），**必须写清口径**，不能与 retrieved 口径的数字混比。

---

### 四、有证据支撑的推荐

**先给结论：没有任何官方明文规定 agentic 该喂 (a)/(b)/(c)。** 官方明文只覆盖**单轮**：`retrieved_contexts` = 检索器**针对该 query** 返回的块。因此推荐必须在"明文的单轮口径 + 六家框架的实践惯例 + AP 公式约束"三者之间取舍：

1. **检索质量轴（Context Precision / Recall）→ 用 (a).**
   - **依据强度最高**：**[官方明确]** DeepEval（`retrieval_context` = retrieved、评 rerank 排序）、RAGAS（K = context 条数、位置敏感）、LlamaIndex（`source_nodes` = 送入合成的节点）、LangSmith（`documents` = `retriever.invoke`）、TruLens（groundedness 对 retrieved context）、Phoenix（per-step retrieved）。
   - **多轮落地**：**按 retrieval step 分别评**（对齐 Phoenix 的 span 级 per-step；RAGAS 亦可逐 step 组装 `SingleTurnSample`），**或**只评最终那次检索的 top-k。**不要**把跨轮全部产出拼成一个大列表——这会破坏"单 query ↔ 单 context"对应，且因 AP 位置敏感（前一篇已证）系统性压分。**[本文推断]**（框架给了 per-step 依据，"逐 step 评"的落地是推断）。
   - **性质**：这是**框架惯例**（六家一致），**不是 agentic 官方明文**。

2. **忠实度轴（Faithfulness / Groundedness）→ 用"生成器实际看到的 context".**
   - **依据**：**[官方明确]** TruLens/LangSmith/Phoenix 都把 groundedness 建立在"生成时提供的 context"上；RAGAS 论文定义 faithfulness 为"答案陈述可从 `c(q)` 推出"。
   - **多轮落地**：agent 的最终生成 prompt 里通常**确实**包含了跨轮累积的证据，因此 faithfulness 用"累积证据"是自洽的——但**喂前应去重 + 按相关性重排**，以降低噪声与重复。
   - **性质**："用生成器所见 context" = **框架惯例**（TruLens/LangSmith/Phoenix）；"去重 + 重排" = **[本文推断]**，**无框架明文**（Phoenix 只在单 step 内合并，不跨 step 去重）。

3. **(b) cited / used → 不作为 context 指标输入，另设 Citation Accuracy 指标。**
   - **依据**：六家框架无一使用 cited 作为 context 指标输入（见第一、二节）；citation 与 faithfulness 解耦是社区惯例 **[权威第三方]**。RAGAS 无 citation 指标。
   - **性质**：**[本文推断]** + 社区惯例。

4. **(c) 全部累积（原样、不去重不重排）→ 不推荐。** 无任何明文背书；RAGAS 官方示例在 agentic 下**显式返回空**，且 AP 位置敏感会系统性压分。

> **对本项目的直接含义（仅陈述，不构成决策）**：现状（`src/cli/eval_ragas.py:164` 把 `final_state["tool_contexts"]` 全部累积、不去重不重排铺平喂 context）**落在 (c) 的"原样累积"形态**，是三者中最无背书的一种。若要在既有单轮指标上做，最贴近主流实践的是**改成 (a)/per-step**；若要评最终答案的忠实度，则**改成"累积但去重+重排"**（即 (c) 的净化版）。二者口径不同、数字不可比，**应在报告里写明口径**。

---

## 反面与风险（我们的口径可能错在哪、过度解读在哪）

1. **不要把"六家框架一致"读成"官方明文规定 agentic 口径"。** 六家的明文都只覆盖**单轮**；agentic 口径是"在这六家框架里自然延伸出来的惯例"，**没有一处是官方为 agentic 写的**。本报告已逐处标注。
2. **RAGAS 官方示例"返回空"是"回避"，不是"禁止"。** 官方只是不提供 agentic 的 context 组装示范，**没有**说"不许给 agentic 喂 context"。把 agentic 检索质量喂进单轮指标**不是官方禁止**，但属**官方未规定、需自负口径**。
3. **issue #1953 的答复不是官方口径。** 答复人是 `CONTRIBUTOR`，**非维护者**。引用时**必须**标"贡献者答复·非官方"，不能当成"RAGAS 官方说 context precision 要算 instruction+history+retrieved"。
4. **PR #3039 标题里的 "dedupe" 是文档去重（删掉重复示例），不是"对 context 去重"。** PR 正文自述 docs-only、无行为变更。**不要**据标题编造"官方建议去重"。
5. **"(a) 最终 top-k"在多轮下并不等于"最终那次检索的 top-k"。** 若生成器看到的是多轮累积窗口，"生成时所用"其实是一个**并集**而非单次 top-k。把 (a) 机械理解为"最后一次检索"可能**低估**生成器实际可见的证据。本报告用"**按 step 评**"来规避这个歧义。
6. **(c) 的"去重/重排"没有任何框架明文。** 它是有道理的工程做法（去噪、对齐排序），但**是推断**，不能宣称"业界标准"。Phoenix 的"合并"只发生在单 step 内，**不是**跨轮去重的先例。
7. **论文都是单跳/多跳检索评测，不是"agent 多轮累积"评测。** MultiHop-RAG 的"证据集"是检索到的 top-K，**不等于** agent 中途多次检索的累加。别把多跳 bench 的口径直接套到 agent 轨迹上。

---

## 待确认项（留给决策阶段，非本文件结论）

1. **agentic 是否仍要用单轮 context 指标？** 官方原生姿态是 `MultiTurnSample` + Agent 指标族（答案级/轨迹级）；本项目是否补 Agent 指标族，与单轮 RAG 指标并存？
2. **若保留单轮 context 指标，采用 (a) per-step 还是"最终检索 top-k"？** 两者口径不同、不可比，需在 ADR/design 里显式选定。
3. **faithfulness 的 context 用"累积并集"还是"(a)"？** 取决于"最终生成 prompt 里到底放了什么"——需读代码确认本项目最终生成节点实际可见的证据范围。
4. **是否引入 Citation Accuracy 独立指标**来承接 (b)（引用是否指向真实支撑块）？
5. **去重/重排的实现与度量为工程自定**（无明文），需写明口径并在报告里声明"数字不可与其他口径混比"。
6. **官方示例返回空这件事**：本项目是否有必要模仿（agentic 只评答案级/轨迹级），还是坚持 context 指标但自负口径？

---

## 来源

**RAGAS 官方文档（docs.ragas.io，一手）**
- Tutorials 索引（含 Evaluate a prompt / RAG / Workflow / Agent）：<https://docs.ragas.io/en/stable/tutorials/>
- Evaluate a simple RAG system：<https://docs.ragas.io/en/stable/tutorials/rag/>
- Evaluate an AI workflow：<https://docs.ragas.io/en/stable/tutorials/workflow/>
- Evaluate an AI agent：<https://docs.ragas.io/en/stable/tutorials/agent/>
- Evaluation Sample（`SingleTurnSample` / `MultiTurnSample` 字段与示例）：<https://docs.ragas.io/en/stable/concepts/components/eval_sample/>
- Context Precision（K = context 总条数、位置敏感）：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/>
- Agent Evaluation Metrics（Goal / Tool Call / Topic Adherence，多轮消息）：<https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/>

**RAGAS 官方仓库源码（vibrantlabsai/ragas）**
- `examples/ragas_examples/improve_rag/rag.py`（naive 返回 `retrieved_documents`；**agentic 返回 `[]`**）：<https://raw.githubusercontent.com/vibrantlabsai/ragas/main/examples/ragas_examples/improve_rag/rag.py>
- `examples/ragas_examples/improve_rag/evals.py`（两模式均只用 `DiscreteMetric` 答案级指标）：<https://raw.githubusercontent.com/vibrantlabsai/ragas/main/examples/ragas_examples/improve_rag/evals.py>
- issue #1953 *"What to include in context precision/recall for RAG LLM evaluation"*（**贡献者答复·非官方**）：<https://github.com/vibrantlabsai/ragas/issues/1953>（作者/association 经 `api.github.com/repos/vibrantlabsai/ragas/issues/1953/comments` 核实为 `sahusiddharth` / `CONTRIBUTOR`）
- issue #2013 *"Context Precision score heavily influenced by position of relevant contexts in array"*：<https://github.com/vibrantlabsai/ragas/issues/2013>
- PR #3039 *"docs: clarify without-reference context precision inputs and dedupe e…"*（**docs-only，无行为变更**）：<https://github.com/vibrantlabsai/ragas/pull/3039>

**DeepEval（confident-ai，官方）**
- Contextual Precision（`retrieval_context` 语义、WCP 公式、`n = retrieval_context 长度`、rerank FAQ）：<https://docs.confident-ai.com/docs/metrics-contextual-precision>

**LangChain / LangSmith（官方）**
- Evaluate a RAG application（`documents = retriever.invoke()`，groundedness / retrieval_relevance）：<https://docs.langchain.com/langsmith/evaluate-rag-tutorial>
- Evaluation concepts（evaluator 输入约定）：<https://docs.langchain.com/langsmith/evaluation-concepts>

**TruLens（官方）**
- RAG Triad（Context Relevance / Groundedness 对 "retrieved context"）：<https://www.trulens.org/getting_started/core_concepts/rag_triad/>

**Arize Phoenix（官方）**
- Retrieval Relevance（per-step、`context` = 该步所有返回项合并、Span 级、与 Faithfulness 分工）：<https://arize.com/docs/phoenix/evaluation/pre-built-metrics/retrieval-relevance>

**LlamaIndex（官方）**
- Response Synthesizer（`source_nodes` 与 `no_text` 模式语义）：<https://developers.llamaindex.ai/python/framework/module_guides/querying/response_synthesizers/>
- Introduction to RAG（Node Postprocessor / Response Synthesizer 术语）：<https://developers.llamaindex.ai/python/framework/understanding/rag/>

**论文（权威第三方）**
- Es et al. *Ragas: Automated Evaluation of Retrieval Augmented Generation*：<https://arxiv.org/abs/2309.15217>
- Saad-Falcon et al. *ARES: An Automated Evaluation Framework for RAG Systems*（NAACL 2024）：<https://arxiv.org/abs/2311.09476>
- Ru et al. *RAGChecker: A Fine-grained Framework for Diagnosing RAG*：<https://arxiv.org/abs/2408.08067>
- Tang & Yang *MultiHop-RAG: Benchmarking RAG for Multi-Hop Queries*（证据集 ℛq 与检索评测）：<https://arxiv.org/abs/2401.15391>

**社区惯例（[权威第三方/社区]，非官方）**
- "Faithfulness 与 Citation Accuracy 解耦"的工程主张（示例）：<https://baijiahao.baidu.com/s?id=1868677661476515727>

**本项目代码（引用附 `path:line`，仅陈述现状）**
- context 取自全部累积工具产出：`src/cli/eval_ragas.py:164`
