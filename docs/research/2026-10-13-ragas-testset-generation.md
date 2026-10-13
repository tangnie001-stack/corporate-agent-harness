# RAGAS 测试集生成链路调研（对齐 0.4.3 与 main）

> 调研日期：2026-10-13 ｜ 类型：外部事实调研（第三方库真实行为）
> 结论面向：本仓出题代码 `src/cli/eval_ragas_generate.py` 生成「不自足问题」（如多公司报表语料里只问「稀释每股收益多少」不点公司名）的根因判断与后续处置方向。
> 核对基线：**本地 `/opt/venvs/corporate_rag/` 安装的 `ragas==0.4.3`**（源码实读）＋ **PyPI 最新发布** ＋ **GitHub `vibrantlabsai/ragas` 的 `main` 分支**（旧仓库名 `explodinggradients/ragas`）。
>
> 一句话背景：被测 Agent 单轮评测、无用户应答，遇到不自足问题会触发 `ask_user` 澄清，澄清超时后把所有公司数据倒出来 → 上下文暴涨 → `faithfulness` / `answer_correctness` 被重罚。用户已裁定「旧这一套有问题，直接废弃，不打补丁」。本文只负责把 RAGAS 的真实做法查清楚。

---

## 结论摘要

1. **当前（0.4.3，也是 PyPI 最新版）RAGAS 出题全链路是"知识图谱 → transforms → persona → scenario → query synthesizer → prompt"**，节点/场景级有质量把控，**问题级（question-level）零校验**。出题 prompt（`QueryAnswerGenerationPrompt`）只要求"答案忠于上下文"，**完全不提问题自足性、无歧义、standalone**。

2. **旧版（0.1.x）确有 `QuestionFilter`**，判据正是 **"Independence（self-contained）+ Clear Intent（无歧义）"**，不合格（verdict=0）就用反馈重写。它**在 0.2.0（2024-10-14）被整体重写时移除**，触发点是 PR **#1401 `feat!: moving experimental.testset to replace old testset`**（breaking）。**但没有任何官方说明/CHANGELOG 明写"因何移除"**——它是架构重写的副产品，不是一项被论证过的决策。这是本次调研**最关键的不确定项**。

3. **官方当前支持的扩展点**：`llm_context`（0.4.1 引入，注入出题 prompt 的 "Additional Context" 槽位，**软引导**）；`query_distribution`（换 synthesizer，**硬扩展点**）；子类化 synthesizer / 换 `generate_query_reference_prompt`（**源码级，官方文档基本是空壳**）；`transforms/filters.py` 的 `CustomNodeFilter`/`QuestionPotentialPrompt`（**节点级、非问题级**，表达不了"问题自足性"）。**没有任何官方扩展点是"对每条生成的问题做质量判定并重写"的**。

4. **0.4.3 与 GitHub main 的 testset 模块无差异**：目录结构一致、`single_hop/prompts.py` 逐字一致、`generate.py` 字节数一致（26171 B）。**main 没有重新引入问题过滤，没有新 testset API**。0.4.3 就是当前最新。

5. **RAGAS 官方没有"多轮测试集生成"能力**：所有内置 synthesizer 只产出 `SingleTurnSample`。`MultiTurnSample` 存在但**只用于评测**（工具调用、目标达成、AspectCritic 等），官方多轮 howto 是纯评测教程，**也从未声明"单轮 RAG 评测应假设问题自足"**——那只是业界惯例/我方推断，不是官方立场。

6. **我方用法有多处偏离当前官方路径**：固定了 persona、手搓 transforms（导致抽象多跳 synthesizer 被静默丢弃）、没传 `llm_context`、用节点级 `CustomNodeFilter`、额外加了一道 LLM 纠错。这些**没有一处是"问题自足性"的硬保证**——这解释了为什么旧链路会系统性产出不自足问题。

---

## 详述

### 问题 1：最新 RAGAS 出题全链路（逐步 + 实现落点）

以本地 `ragas==0.4.3` 源码为准，路径前缀 `…/site-packages/ragas/`。

**入口层** —— `TestsetGenerator`（`testset/synthesizers/generate.py:54`）是 `@dataclass`，字段：`llm`、`embedding_model`、`knowledge_graph`、`persona_list`、`llm_context`（`:70-74`）。三种入口方法：
- `generate_with_langchain_docs()`（`:114`）：从 LangChain 文档起，内部跑 `default_transforms` 建 KG，再调 `generate()`。
- `generate_with_chunks()`（`:302`）：**预分块**文档直接当 `NodeType.CHUNK`（0.4.2 新增，PR #2526）。
- `generate()`（`:414`）：**我们直接调用的就是这个**——假定 `knowledge_graph` 已建好。

**① 文档 → 节点（KG）** —— 每个 chunk/doc 变成一个 `Node(type=CHUNK|DOCUMENT, properties={page_content, document_metadata})`（`generate.py:195-205` / 我们 `eval_ragas_generate.py:421-433`）。节点集合构成 `KnowledgeGraph`。

**② transforms（图谱加工）** —— `apply_transforms(kg, transforms, run_config)`（`testset/transforms/engine.py`，被 `generate.py:208` / `:400` 调用）。官方默认集合见 `transforms/default.py:34` `default_transforms()`（0.4.2 新增分支逻辑）：按文档长度分档，长文档走
`HeadlinesExtractor → HeadlineSplitter → SummaryExtractor → CustomNodeFilter → Parallel(EmbeddingExtractor, ThemesExtractor, NERExtractor) → Parallel(CosineSimilarityBuilder, OverlapScoreBuilder)`（`:123-130`）。
预分块场景用 `default_transforms_for_prechunked()`（`:170-212`，无 headline/splitter）。
- `SummaryExtractor`（`transforms/extractors/llm_based.py:159`）→ 节点属性 `summary`
- `NERExtractor`（同文件 `:272`）→ 节点属性 `entities`（`max_num_entities=10`）
- `ThemesExtractor`（`:379`）→ 节点属性 `themes`
- `EmbeddingExtractor` → `summary_embedding`
- `OverlapScoreBuilder`（`transforms/relationship_builders/traditional.py:80`）→ 建立 **`type="entities_overlap"`** 的关系，携带 `overlapped_items`（`:170-174`）
- `CosineSimilarityBuilder` → 建立 `summary_similarity` 关系
- `CustomNodeFilter`（`transforms/filters.py:54`）→ **按节点**打 1–5 分，`score <= min_score(=2)` 则**移除该节点**（详见问题 3④）

**③ persona** —— 若 `generator.persona_list is None`，调 `generate_personas_from_kg()`（`testset/persona.py:61`）：取节点的 `summary`，按 `summary_embedding` 余弦相似度 **阈值 0.75** 聚类，每簇取最长 summary 作代表，逐簇用 `PersonaGenerationPrompt`（`:30`，`temperature=1.0`）生成 persona。**若已设 `persona_list` 则跳过该步**，只做 `random.shuffle`（`generate.py:516-524`）。

**④ scenario（场景）** —— `query_distribution` 决定用哪些 synthesizer，默认 `default_query_distribution(llm, kg, llm_context)`（`testset/synthesizers/__init__.py:24-56`）：三个 synthesizer，**等权 `1/n`**：
| synthesizer | 依赖的节点属性 / 关系 | 落点 |
|---|---|---|
| `SingleHopSpecificQuerySynthesizer` | `entities`（CHUNK 优先） | `single_hop/specific.py:40` |
| `MultiHopAbstractQuerySynthesizer` | `themes` + `summary_similarity` 关系 | `multi_hop/abstract.py:31` |
| `MultiHopSpecificQuerySynthesizer` | `entities` + `entities_overlap` 关系 | `multi_hop/specific.py:29` |

`default_query_distribution` 会用 `query.get_node_clusters(kg)` **实测每个 synthesizer 有无可用簇**，取不到簇的**静默丢弃**（`:36-52`）。

每个 `BaseSynthesizer.generate_scenarios()`（`synthesizers/base.py:96`）→ 子类 `_generate_scenarios()`：查图取节点簇 → `ThemesPersonasMatchingPrompt` 把 persona 与 theme/entity 做匹配（`synthesizers/prompts.py:18`）→ 组合 `(node, term/theme, persona, style, length)` → 采样出场景。`QueryStyle`/`QueryLength` 枚举见 `synthesizers/base.py:34-53`。

**⑤ query synthesizer → prompt → sample** —— `BaseSynthesizer.generate_sample()`（`base.py:125`）→ 子类 `_generate_sample()`：把场景包成 `QueryCondition`（单跳，`single_hop/prompts.py:9`）或 `QueryConditions`（多跳，`multi_hop/prompts.py:58`），**把 `self.llm_context` 一起塞进去**，调 `generate_query_reference_prompt.generate(...)`：
- 单跳：`SingleHopQuerySynthesizer.generate_query_reference_prompt: QueryAnswerGenerationPrompt`（`single_hop/base.py:48`，调用点 `:131`）
- 多跳：`MultiHopQuerySynthesizer.generate_query_reference_prompt`（`multi_hop/base.py:52`，调用点 `:170`）

**出题 prompt 本体**（`single_hop/prompts.py:23-57`，多跳同构 `multi_hop/prompts.py:72-118`）的 instruction 只有三条：
> 1. 依据 context/persona/term/style/length **生成一个问题**（"incorporates the term"）；
> 2. **用且仅用 context 生成答案**（faithful to the context）；
> 3. **Additional Context**（若提供 `llm_context`）：按它指导**问题类型与答案结构**。

**只约束"答案忠于上下文"，对"问题本身是否自足/点名主体/无歧义"只字未提。**（这是本次调研对"不自足"问题的直接机制解释。）

**⑥ 产出** —— `_generate_sample` 返回 `SingleTurnSample(user_input, reference, reference_contexts, persona_name, query_style, query_length)`（`single_hop/base.py:134-141`）→ `generate()` 组装成 `TestsetSample(eval_sample=…, synthesizer_name=…)` → `Testset`（`synthesizers/testset_schema.py:20/46`），`to_list()`（`:69-78`）落成 dict。

---

### 问题 2：新版有没有"问题级"质量校验？——**没有**；旧版有，0.2.0 移除

**（a）0.4.3 里零命中。** 对整包检索 `self-contained` / `ambiguity` / `independence` / `standalone` / `QuestionFilter`：
- 命中项全部落在**无关上下文**：`answer_relevance` 指标里说"非承诺型答案可以是 vague/ambiguous"、`factual_correctness` 把句子拆成 standalone 陈述、`HeadlinesExtractorPrompt` 说"可切成 independent sections"。
- `testset/` 子系统内**零命中**；没有 `filters.py` 顶层文件（只有 `transforms/filters.py`，且是**节点**级）。

结论：**当前 RAGAS 对"生成出来的问题"不做任何自足性/清晰度判定，更不会重写不合格问题。**

**（b）旧版 0.1.x 确有 `QuestionFilter`。** 证据两条一手/近一手：
- GitHub issue **#1029**（"QuestionFilter filter logic"，`explodinggradients/ragas`）直接贴出旧 `filter()` 实现与判据文本；该 issue 引用的文件路径是 `src/ragas/testset/filters.py`（**现已不存在**）。
- Pixion 博客《Ragas test set generation breakdown》完整引了 v0.1.21 的 filter prompt，判据原文：
  > **1. Independence**: Can the question be understood and answered without needing additional context or access to external references… Questions should be **self-contained**…
  > **2. Clear Intent**: … convey its purpose **without ambiguity**…
  > assign a verdict of "1" if … independent … assign "0" if it fails … 判定 0 时**用 feedback 重写问题**。

旧链路（0.1.x）是 **evolution 式**（`simple` / `reasoning` / `multi_context`，见 v0.1.21 文档与 `distributions={simple:0.5, multi_context:0.4, reasoning:0.1}`），共 ~11 步：keyphrase → 上下文打分 → 种子问题 → **QuestionFilter（independence+clear intent）** → 重写 → evolution → 压缩 → 去重 → 找上下文 → 生成答案。

**（c）何时/因何移除。** 见 GitHub Release **v0.2.0（2024-10-14）**：
- **PR #1401 `feat!: moving experimental.testset to replace old testset`** —— breaking，直接用新的 experimental testset 顶替旧 testset；
- 同期 #1375 `implement TestsetGenerator`、#1344/#1345/#1352 `transforms for testset generation / Knowledge Graphs / transform engine`、#1359 `abstractQA`、#1386 `output new Testset object`、#1394 `moving to pydantic2`（breaking）。
- 旧 `evolutions` 体系（含 `QuestionFilter`）被整体替换为**知识图谱式**生成。

**诚实结论**：**移除是一次架构重写（evolution → knowledge graph）的副产品，不是"我们论证后决定不要 independence 过滤"**。翻遍 release notes / CHANGELOG 也**找不到任何官方解释**"为什么不再校验问题自足性"。这一点必须作为不确定项保留——不能对外说"RAGAS 官方认为问题级过滤不必要"。

---

### 问题 3：当前官方扩展点逐一核实

#### ① `llm_context` —— 真实存在，注入到出题 prompt 的 "Additional Context" 槽位 ✅
- 字段：`TestsetGenerator.llm_context`（`generate.py:74`，`from_langchain()/from_llama_index()` 也收）。
- 传递链：`generate()` 里 `default_query_distribution(self.llm, self.knowledge_graph, self.llm_context)`（`generate.py:478-480`）→ `default_query_distribution` 把它透传给三个 synthesizer（`synthesizers/__init__.py:31-33`）→ `BaseSynthesizer.llm_context`（`synthesizers/base.py:90`）→ `_generate_sample` 里塞进 `QueryCondition.llm_context`（`single_hop/base.py:129`）/ `QueryConditions.llm_context`（`multi_hop/base.py:168`）。
- prompt 侧：`single_hop/prompts.py:33-35` 第 3 条 "Additional Context"（多跳 `multi_hop/prompts.py:89-91`）。
- **性质：软引导，非硬保证。** 原文是 "use it as guidance for **what type of question** to generate … and how to structure the answer"，面向"问题类型/答案结构"，**并没有"必须自足"的判定与重试**。要拿它表达"问题须点明公司名/主体"，只能写成一句自然语言指引，模型**可能遵守也可能不遵守**，且**无校验兜底**。
- 来源：0.4.1 release notes（PR #2474 "New input parameter for the TestsetGenerator Class : LLM_CONTEXT"）。

#### ② `query_distribution` —— 官方明示可替换/调权重 ✅（换 synthesizer 的硬扩展点）
- 类型：`QueryDistribution = List[Tuple[BaseSynthesizer, float]]`（`synthesizers/__init__.py:21`）。
- 入口：`generate(query_distribution=…)`（`generate.py:417`）、`generate_with_*` 各方法同名参数。
- 官方 getting-started 演示了显式构造并传入（`query_distribution = default_query_distribution(generator_llm)` → `generator.generate(testset_size=10, query_distribution=query_distribution)`）。
- **能用来做什么**：换/加权 synthesizer（比如只用单跳、或加权多跳）。**不能直接用来做"问题级过滤"**——除非自己写一个 synthesizer（见 ③）。
- 注意官方文档**没有**逐字写"用户可以换掉 `query_distribution`"，可替换性是由「默认值本身就是普通 list」推得的，不是 prose 断言。

#### ③ 子类化 `QueryAnswerGenerationPrompt` / synthesizer —— 源码可行，文档缺失 ⚠️
- prompt 是 dataclass 字段，可替换：`SingleHopQuerySynthesizer.generate_query_reference_prompt: PydanticPrompt = QueryAnswerGenerationPrompt()`（`single_hop/base.py:48`）、多跳同名字段（`multi_hop/base.py:52`）。`PydanticPrompt` 带 `instruction` / `input_model` / `output_model` / `examples`，可子类化改写 instruction（**改 instruction 仍是软引导**）。
- 也可子类化 `BaseSynthesizer`（`synthesizers/base.py:80`）或 `SingleHopQuerySynthesizer`，重写 `_generate_scenarios` / `_generate_sample` —— **唯一能做到"生成后判定+重写"这种硬保证级别的路径**（即在 `_generate_sample` 里加一轮校验）。但**官方没有这层的现成组件，得自研**。
- **文档现状**：官方 `howtos/customizations/testgenerator/` 页面基本是空壳（原文仅"我们正在增加对自定义测试集生成的支持…"），子类化/换 prompt 的示例**要读源码，不在文档里**。

#### ④ `transforms/filters.py` 的 LLM 过滤范式 —— **节点级，明确不是问题级** ❌
- `CustomNodeFilter`（`transforms/filters.py:54`）配 `QuestionPotentialPrompt`（`:43`）：输入 `document_summary` + `node_content`，**给"这段内容有多适合出题"打 1–5 分**（rubric 见 `:15-21`），`score <= min_score(=2)` → 调用 `kg.remove_node` **删掉这个节点**（机制见 `transforms/base.py:382-430` `NodeFilter.transform`）。
- 评判对象是**节点（chunk/document）内容与文档摘要的相关性**，**不是**已生成的问题；因此**表达不了"问题是否自足/无歧义"**。
- 它是"硬过滤"（真删节点），但在**问题语义**这个维度上是盲的。我们代码里正是把它当成了"过滤器"在用（`eval_ragas_generate.py:386`）——**名不副实**。

---

### 问题 4：最新版本 vs 0.4.3 有无差异？—— 无

| 核对项 | 结果 | 证据 |
|---|---|---|
| PyPI 最新版本 | **0.4.3** | `https://pypi.org/pypi/ragas/json` 的 `info.version` |
| GitHub main 版本机制 | `setuptools_scm` 动态版本（无静态号），最新 tag = v0.4.3 | `main/pyproject.toml`（`dynamic=["version"]`，`[tool.setuptools_scm]`）；releases 列表最新 v0.4.3 |
| main 的 `testset/` 结构 | 与 0.4.3 一致：`__init__.py, graph.py, graph_queries.py, persona.py, synthesizers/, transforms/`，**无顶层 `filters.py`、无 `evolutions.py`** | GitHub contents API `src/ragas/testset?ref=main` |
| main 的出题 prompt | `single_hop/prompts.py` 与 0.4.3 **逐字一致**（含 "Additional Context" 段） | raw.githubusercontent main 对照本地 |
| main 的 `generate.py` | 26171 B = 本地 0.4.3 的 26171 B（同尺寸） | `wc -c` vs API `size` |
| main 的 synthesizers 目录 | 与 0.4.3 同（base/generate/prompts/utils/testset_schema + single_hop/ + multi_hop/），**无新"问题过滤/多轮"文件** | GitHub contents API |

**结论**：新版本**没有**重新引入问题过滤、**没有**改出题 prompt、**没有**新 testset API。0.4.3 即当前最新，本地源码即可代表最新官方行为。（若要更保守，可留意 main 上后续 commit，但截至 2026-10-13 的核对未见相关改动。）

---

### 问题 5：官方对"被测系统反问/澄清"的立场 —— 只做多轮**评测**，不做多轮**出题**

- **`MultiTurnSample` 存在但只服务评测**：`dataset_schema.py:100-122`，字段 `user_input: List[HumanMessage|AIMessage|ToolMessage]` + 可选 `reference` / `reference_tool_calls` / `rubrics` / `reference_topics`。消费方是**指标**：`ToolCallF1`（`metrics/_tool_call_f1.py:29` `is_multi_turn=True`）、`AgentGoalAccuracy`（`metrics/_goal_accuracy.py`）、`AspectCritic`（`metrics/_simple_criteria.py`）、`DomainSpecificRubrics`（`metrics/_domain_specific_rubrics.py`），入口 `evaluation.py:265`。
- **出题侧无多轮能力**：三个内置 synthesizer 的 `_generate_sample` 全部 `return SingleTurnSample(...)`。`TestsetSample.eval_sample` 类型上允许 `MultiTurnSample`（`testset_schema.py:32`），`Testset.from_list()` 也能把 `user_input` 为 list 的样本解析成多轮（`:94-100`），**但没有任何内置 synthesizer 会产出多轮样本**。
- **官方多轮 howto（`evaluating_multi_turn_conversations`）是纯评测教程**（`MultiTurnSample` + `AspectCritic`），**不涉及多轮测试集生成**。
- **官方从未声明"单轮 RAG 评测应假设问题自足"**。这层假设在 RAGAS 官方文档里**找不到**——它是 RAG 评测的行业惯例，以及"单轮 + 无应答"这种评测设定下的**我方推断**。
- 对本仓的含义：**RAGAS 不提供"生成含澄清轮的多轮测试集"的能力**；要评"Agent 该不该澄清/澄清后如何"，只能走 `MultiTurnSample` 的**评测**路线（手工/自建造多轮样本），不能指望出题器。

---

### 问题 6：我方用法 vs 最新官方推荐用法（逐条差异 + 证据）

我方代码：`src/cli/eval_ragas_generate.py`（对照官方 `ragas==0.4.3` 源码）。

| # | 我方做法（证据） | 官方默认/推荐（证据） | 差异与影响 |
|---|---|---|---|
| 1 | **固定 `persona_list`**（`:364-366`，人设来自 `src/config/const.py:521-525` RAGAS_TEST_PERSONAS） | 默认 `persona_list is None` → `generate_personas_from_kg()` 自动生成（`generate.py:516-522`） | 官方**支持**传 `persona_list`（是字段），但默认是自动生成。我方固定是为了绕开内置 persona 重名导致的 `PersonaList` KeyError（注释见 const.py:516-520）——属**有意偏离**，代价是人设不随语料自适应。 |
| 2 | **手搓 transforms**（`:371-396`）：`CustomNodeFilter + SummaryExtractor + EmbeddingExtractor(summary) + NERExtractor + OverlapScoreBuilder` | 默认 `default_transforms`（`transforms/default.py:34`）含 `HeadlinesExtractor + HeadlineSplitter + ThemesExtractor + CosineSimilarityBuilder` 等 | **关键偏离**：我方**没建 `summary_similarity` 关系**（缺 `CosineSimilarityBuilder`）、**没建 `themes` 属性**（缺 `ThemesExtractor`）⇒ `MultiHopAbstractQuerySynthesizer` 的 `get_node_clusters`（依赖 `summary_similarity`，`multi_hop/abstract.py:48-54`）取不到簇 ⇒ **在 `default_query_distribution` 里被静默丢弃**（`synthesizers/__init__.py:36-52`）。**实际只剩 `single_hop_specific` + `multi_hop_specific` 两个 synthesizer**，问题多样性下降。 |
| 3 | **没传 `llm_context`**（`:361` 只传 `llm=…, embedding_model=…`） | `TestsetGenerator(llm_context=…)` 会把指引注入 prompt 的 "Additional Context"（`generate.py:74` → `single_hop/prompts.py:33-35`） | 我方面前**没有任何**能影响"问题该不该点名主体"的官方杠杆。这是后续最省事、最贴合官方路径的抓手（但仅软引导）。 |
| 4 | **拿 `CustomNodeFilter` 当"过滤器"**（`:386`，`use_filter` 开关） | `CustomNodeFilter` 是**节点级**相关性打分（`transforms/filters.py:54-86`），评判 chunk 有无"出题潜力" | 与"问题是否自足"**无关**；我方语义上误用。关掉它（`use_filter=True`）只省 LLM 调用，不会改善问题质量。 |
| 5 | **手工建 KG**：直接构造 `Node(type=CHUNK)` 再 `apply_transforms`（`:421-439`），绕开 `generate_with_langchain_docs` | 官方主推 `generate_with_langchain_docs` / `generate_with_chunks`（`generate.py:114/302`） | 属"自行组装"，功能等价但**没吃到默认 transforms 的完整链路**（与 #2 同因）。`generate_with_chunks` 更贴近我方"已有分块"场景。 |
| 6 | **额外一道 LLM 纠错**（`:103-165` `_proofread_question` + `:470-471`） | 官方无任何生成后问题处理 | 我方自研的**软后处理**（修同音错别字），**只改错别字，不改自足性**，且**不校验结果**（best-effort，失败保留原文）。 |
| 7 | `generate(testset_size=size, num_personas=len(RAGAS_TEST_PERSONAS))`（`:454-458`） | 同名 API（`generate.py:414`） | API 一致；因 `persona_list` 非空，`num_personas` 实际只用于截断 persona 列表。 |

**归纳**：我方用法**没有任何一处**对"问题自足性"提供硬保证；官方当前路径**本身也不含**该保证（见问题 2）。二者共同导致"多公司语料 + 单跳取 entity 出题"必然产出不点主体的问题——**这是当前官方机制下的正常输出，不是配错参数**。

---

## 反面与风险

- **"官方废弃了问题过滤"是一个有证据的推断，但不是官方声明。** 我们只能确证：旧版有、0.2.0 起没有、且无解释。风险：若真有讨论/issue 说明理由，我们没有找到；对外表述应限定为"随 0.2.0 架构重写移除，未见官方说明"。
- **不要把 `llm_context` 当成硬解。** 它是 prompt 里的一句 guidance，无判定、无重试、无保证；指望它 100% 让问题点名公司名是高估。
- **`CustomNodeFilter` 名字有迷惑性。** "Filter" 指过滤**节点**，不是过滤**问题**。把它当问题质检会白花 LLM 调用却拿不到想要的保证。
- **官方自定义文档基本是空壳。** `howtos/customizations/testgenerator/` 只有一句"正在增加支持"。任何"子类化 synthesizer / 换 prompt"的方案都要**自研且无官方背书**，维护成本与升级风险由我方承担。
- **RAGAS 不产多轮测试集**，所以"用官方出多轮题来测澄清行为"这条路**不通**；澄清/反问的评测得另想办法（`MultiTurnSample` 手造 + 对应指标，属另一话题）。
- **知识图谱缓存会固化问题。** 我方把 KG 存盘（`kg_{kb_id}.json`，`:408-445`）并跳过 transforms 复用——若修正了 transforms 组合却复用旧 KG，问题仍照旧，需注意重建。

## 待确认项

1. **移除 `QuestionFilter` 的官方理由仍缺一手说明。** 已查：v0.2.0 release notes、PyPI、当前文档、issue #1029。**未查**：`explodinggradients/ragas` 上 0.2.0 前后相关 PR/issue 的讨论串（#1401 等 pull request 正文与 review comments）、以及 Discourse/官方讨论渠道。若要坐实"因何移除"需补这一跳（可能仍需翻 PR 评论区）。
2. **`main` 是否在本调研后继续前进。** 本文核对截至 **2026-10-13**；main 的 testset 模块与 0.4.3 一致，但 setuptools_scm 版本号依赖 tag，未来若有新 tag 需重核。
3. **"问题自足性"要在哪一层兜底**，不属于本调研（外部事实）职责，属设计与决策：① 走 `llm_context`（软、官方）；② 自研 synthesizer/后处理做"判定+重写"（硬、自研）；③ 评测侧跳过/分类处理澄清超时样本。这三条的产品取舍应落到后续 ADR / change。
4. **`num_personas` 与固定 persona 的交互**：我方传 `len(RAGAS_TEST_PERSONAS)=3`，`generate.py:553` 用 `persona_list[:num_personas]`；无异常，但语义上"num_personas"已不表达"生成几个 persona"。低风险，记录备查。

---

## 来源

### 外部 URL
| URL | 说明 |
|---|---|
| https://pypi.org/pypi/ragas/json | PyPI 包元数据；`info.version` = **0.4.3**（截至 2026-10-13 最新）；homepage 已迁至 `vibrantlabsai/ragas` |
| https://github.com/vibrantlabsai/ragas/releases/tag/v0.2.0 | v0.2.0（2024-10-14）。含 **PR #1401 `feat!: moving experimental.testset to replace old testset`**、#1375 `implement TestsetGenerator`、#1344/#1345/#1352 transforms、#1359 abstractQA、#1386 Testset object、#1394 pydantic2 —— 旧 evolution 体系（含 QuestionFilter）在此被整体替换 |
| https://github.com/vibrantlabsai/ragas/releases | 各版本 note。**PR #2474 "New input parameter : LLM_CONTEXT"** 出现在 v0.4.1（2024-12-10）；最新 v0.4.3 |
| https://github.com/explodinggradients/ragas/issues/1029 | "QuestionFilter filter logic"；贴出旧 `filter()` 实现与判据（independence / clear intent），引用旧路径 `src/ragas/testset/filters.py`（现已不存在） |
| https://pixion.co/blog/ragas-test-set-generation-breakdown | 二手（但**逐字引了源码 prompt**）：v0.1.21 的 filter prompt（Independence + Clear Intent，verdict 0→重写）与 11 步 evolution 链路 |
| https://docs.ragas.io/en/v0.1.21/concepts/testset_generation.html | 旧版文档：evolution 式出题（`simple`/`reasoning`/`multi_context`、`distributions`），佐证"旧链路是 evolution 式" |
| https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/ | 当前文档：KG 式出题总览；**全文无 self-contained / independence / ambiguity / 问题过滤** |
| https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/ | 官方 getting-started：演示传 `query_distribution`（用户可构造/加权） |
| https://docs.ragas.io/en/stable/howtos/customizations/testgenerator/ | 官方"自定义测试集生成"页——**基本是空壳**（仅一句"正在增加支持"） |
| https://docs.ragas.io/en/stable/howtos/applications/evaluating_multi_turn_conversations/ | 官方多轮**评测**教程（`MultiTurnSample` + `AspectCritic`）；**不含多轮出题**，也未声明单轮需自足 |
| https://github.com/vibrantlabsai/ragas/blob/main/src/ragas/testset/synthesizers/single_hop/prompts.py | main 分支出题 prompt（raw）；与本地 0.4.3 **逐字一致** |
| https://api.github.com/repos/vibrantlabsai/ragas/contents/src/ragas/testset?ref=main | main 的 testset 目录清单：无顶层 `filters.py`、无 `evolutions.py` |
| https://api.github.com/repos/vibrantlabsai/ragas/contents/src/ragas/testset/synthesizers?ref=main | main 的 synthesizers 目录清单：无新增"问题过滤/多轮"文件 |
| https://github.com/vibrantlabsai/ragas/blob/main/pyproject.toml | main 的依赖与 `setuptools_scm` 动态版本配置（无静态版本号） |

### 本地源码（`/opt/venvs/corporate_rag/lib/python3.12/site-packages/ragas/`，0.4.3）
| 路径:行 | 说明 |
|---|---|
| `testset/synthesizers/generate.py:54-74` | `TestsetGenerator` dataclass，含 `llm_context` 字段 |
| `testset/synthesizers/generate.py:114 / :302 / :414` | 三个入口：`generate_with_langchain_docs` / `generate_with_chunks` / `generate` |
| `testset/synthesizers/generate.py:478-480` | `default_query_distribution(self.llm, kg, self.llm_context)` —— llm_context 注入点 |
| `testset/synthesizers/generate.py:516-524` | persona：默认自动生成，有 `persona_list` 则只 shuffle |
| `testset/synthesizers/__init__.py:21 / :24-56` | `QueryDistribution` 类型；默认分布 + 取不到簇则静默丢弃 synthesizer |
| `testset/synthesizers/base.py:80-139` | `BaseSynthesizer`（`llm_context` 字段）、`generate_scenarios` / `generate_sample` |
| `testset/synthesizers/base.py:34-53` | `QueryLength` / `QueryStyle` 枚举 |
| `testset/synthesizers/single_hop/prompts.py:9-16 / :23-57` | `QueryCondition`；`QueryAnswerGenerationPrompt`（**instruction 只要求答案忠于上下文**，"Additional Context" 在 :33-35） |
| `testset/synthesizers/single_hop/base.py:46-48 / :117-141` | `generate_query_reference_prompt` 字段；`_generate_sample`（`llm_context` 在 :129） |
| `testset/synthesizers/single_hop/specific.py:40-44 / :113-162` | `SingleHopSpecificQuerySynthesizer`（`property_name="entities"`）与其场景生成 |
| `testset/synthesizers/multi_hop/prompts.py:58-92` | 多跳 `QueryConditions` + 出题 prompt（"Additional Context" 在 :89-91） |
| `testset/synthesizers/multi_hop/base.py:51-52 / :156-187` | 多跳 synthesizer 的 prompt 字段；`_generate_sample`（`llm_context` 在 :168）；`make_contexts` |
| `testset/synthesizers/multi_hop/abstract.py:31-56` | `MultiHopAbstractQuerySynthesizer`（依赖 `summary_similarity` + `themes`） |
| `testset/synthesizers/multi_hop/specific.py:29-46` | `MultiHopSpecificQuerySynthesizer`（`relation_type="entities_overlap"`、`relation_overlap_property="overlapped_items"`） |
| `testset/synthesizers/prompts.py:18-49` | `ThemesPersonasMatchingPrompt`（persona↔theme/entity 匹配） |
| `testset/persona.py:16-22 / :25-27 / :30-48 / :61-150` | `Persona`、`default_filter`、`PersonaGenerationPrompt`、`generate_personas_from_kg`（阈值 0.75、temperature 1.0） |
| `testset/synthesizers/testset_schema.py:20-33 / :46-78 / :94-100` | `TestsetSample`、`Testset.to_list()`、`from_list` 对多轮样本的分支 |
| `testset/transforms/default.py:34-167 / :170-212` | `default_transforms`（含 Headlines/Themes/Cosine）与 `default_transforms_for_prechunked` |
| `testset/transforms/filters.py:15-21 / :43-86` | `DEFAULT_RUBRICS`；`QuestionPotentialPrompt`；`CustomNodeFilter`（**节点级**） |
| `testset/transforms/base.py:382-437` | `NodeFilter` / `LLMBasedNodeFilter`（`flag=True` 即 `remove_node`） |
| `testset/transforms/extractors/llm_based.py:159-181 / :272-301 / :379-409` | `SummaryExtractor`（→`summary`）、`NERExtractor`（→`entities`）、`ThemesExtractor`（→`themes`） |
| `testset/transforms/relationship_builders/traditional.py:80-178` | `OverlapScoreBuilder` —— 关系 `type=f"{property_name}_overlap"`（:170）、`overlapped_items`（:173） |
| `dataset_schema.py:100-122` | `MultiTurnSample`（消息列表 + `reference_tool_calls`/`rubrics`/`reference_topics`） |
| `metrics/_tool_call_f1.py:29` / `metrics/_goal_accuracy.py` / `metrics/_simple_criteria.py` / `metrics/_domain_specific_rubrics.py` | `MultiTurnSample` 的消费方（多轮评测指标） |
| （全包 grep）`self-contained`/`ambiguity`/`independence`/`standalone`/`QuestionFilter` | `testset/` 内**零命中**；其余命中均在 metrics/headline 等无关处 |

### 本仓代码
| 路径:行 | 说明 |
|---|---|
| `src/cli/eval_ragas_generate.py:361` | `TestsetGenerator(llm=…, embedding_model=…)` —— **未传 `llm_context`** |
| `src/cli/eval_ragas_generate.py:364-366` | 固定 `generator.persona_list` |
| `src/cli/eval_ragas_generate.py:371-396` | 手搓 transforms（含 `CustomNodeFilter`、缺 `CosineSimilarityBuilder`/`ThemesExtractor`） |
| `src/cli/eval_ragas_generate.py:408-445` | 手工建 KG + `apply_transforms` + KG 存盘复用 |
| `src/cli/eval_ragas_generate.py:454-458` | `generator.generate(testset_size=…, num_personas=len(RAGAS_TEST_PERSONAS))` |
| `src/cli/eval_ragas_generate.py:103-165 / :470-471` | 自研 LLM 纠错后处理 `_proofread_question`（仅修错别字，best-effort） |
| `src/config/const.py:521-525` | `RAGAS_TEST_PERSONAS`（固定人设及其注释里的 KeyError 成因） |
| `src/config/settings.py:159 / :174` | `RAGAS_LLM_MODEL` / `RAGAS_DOC_WHITELIST` |

### 相关既有文档
| 路径 | 说明 |
|---|---|
| `docs/research/agent-eval-research.md` | 同目录既有调研（2026-10-12）：RAGAS 0.4.3 的 Agent 指标、`MultiTurnSample` 用于**评测**、官方 LangGraph howto；与本篇互补（那篇讲"怎么评 Agent"，本篇讲"题怎么出的"） |
