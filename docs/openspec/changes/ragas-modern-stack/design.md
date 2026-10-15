## Context

本仓 RAGAS 评测链路的现状（**实读 + 活体实测**核实）：

- **执行入口是 legacy**：`src/cli/eval_ragas.py:367` 用 `evaluate(dataset=…, llm=LangchainLLMWrapper(...), embeddings=LangchainEmbeddingsWrapper(...), metrics=[…], raise_exceptions=False, run_config=RunConfig(timeout=600))`；指标来自 `from ragas.metrics import …`（`:346`）。
- **每次评测打出 7 条 `DeprecationWarning`**（实测）：5 条指标导入 + `LangchainLLMWrapper` + `LangchainEmbeddingsWrapper`。
- **代际断层是硬边界**：`evaluate()` 要求所有指标 `isinstance(m, Metric)`（`ragas/evaluation.py:132`），而 `ragas.metrics.collections.*` **不是**该子类 ⇒ **现代指标进不了 `evaluate()`**，执行层无法局部替换。
- **现代栈可用性已活体验证**：`llm_factory` + 百炼 `kimi-k3` 可跑通（唯需 `temperature=0, top_p=None`）；`embedding_factory` + 百炼 `qwen3.7-text-embedding-flash` 可嵌入（dim=1024）；5 指标在现代栈跑同题，**4/5 与 legacy 逐位一致**（见「证据」）。
- **出题链缺 transform**：我们手搓 5 个 transforms（`eval_ragas_generate.py:386-395`），官方 `default_transforms` 有 7 个（`transforms/default.py:185-207`），**缺 `ThemesExtractor` + `CosineSimilarityBuilder`** ⇒ `MultiHopAbstractQuerySynthesizer` 静默丢弃（日志 `logs/app_2026-10-13.log:3871`：`Skipping multi_hop_abstract_query_synthesizer ... Cannot form clusters`；v1 测试集实测 abstract = **0 条**）。
- **出题 prompt 无问题级校验**：`ragas/testset/synthesizers/single_hop/prompts.py` 的 instruction 只要求「答案忠于上下文」，**对问题自足性零要求**；0.4.3 全 testset 包 grep `self-contained`/`ambiguity`/`QuestionFilter` **零命中**（旧版有 `QuestionFilter`，随 PR #1401 于 v0.2.0 整体移除，官方无说明）。
- **失败静默**：`raise_exceptions=False` 把异常变 `nan`（实测：裁判 `kimi-k3` 因温度 400 → 5 指标全 nan；legacy 5 题中 5 个作业 `TimeoutError` → CSV 5 个空格）。
- **白名单两端口径不一致**：出题读 `RAGAS_DOC_WHITELIST`（`eval_ragas_generate.py:268`），评估检索**整库**（`eval_ragas.py:737` → `get_all_chunks(kb_id)`）。
- **可解释性缺口**：经典指标的 `MetricResult`（`ragas/metrics/result.py:11`）有 `reason` 字段，但 faithfulness / context_recall / context_precision 聚合时**只取分数、丢弃 reason**；只有 6 个 rubric/多模态指标会回填 reason。

**前身变更的边界**：`ragas-agent-eval`（已实施）的 Non-Goals 明写「**不引入 `ragas.metrics.collections` 现代指标与其 `llm_factory`/`instructor` 接线**（当时 D3 选 legacy）」——本次正是接棒做它。

## Goals / Non-Goals

**Goals:**

- 评测执行内核迁移到**现代 RAGAS 栈**，消除 7 条弃用警告，不再依赖 v1.0 将移除的 API。
- **失败不再静默**：任一指标失败在原样行上显式可见，不丢行、不写 NaN。
- **裁判可复现且可靠**：采样参数由我们显式控制；裁判选型以「结构化输出可靠性」为判据；超时给足。
- **出题产出可唯一作答的题**：恢复 abstract 题路，题面点名公司/报告期。
- **出题与评估同范围**：落地 ADR-0022 决策 C。

**Non-Goals:**

- 不换评测框架（仍用 RAGAS）。
- **不评多轮 / 澄清轮**（`ask_user`）——「澄清」应作为被评能力另案（需多轮 + 用户模拟器）。
- **不改 rubric 类指标**（D3 选经典 5 指标的现代版；可解释性增强另案）。
- 不改 `eval_report` 表 / `kb_eval` API / 前端。
- 不动 `evaluation-pipeline` 的「Empty KB」「NLI context 实体渲染」「当前日期」三条既有条款。

## Decisions

### D1 — 执行内核整层换现代栈，不做混合

**选**：`evaluate()` → `@experiment` + `ragas.Dataset`；`ragas.metrics` → `ragas.metrics.collections`；`Langchain*Wrapper` → `llm_factory`/`embedding_factory`。

**Why**：`evaluate()` 与现代指标**类型不兼容**（`evaluation.py:132` 校验 `isinstance(m, Metric)`），混合态不存在；且 `evaluate()`/`aevaluate()` 已弃用（`evaluation.py:448`、`:106`）。

**Alternatives**：① 留 legacy 不动 → v1.0 必碎，且持续吃弃用警告；② 只换客户端不换指标 → 类型不兼容，不可行。

### D2 — 失败语义：行内 catch 记 `error`，不丢行、不写 NaN

**选**：`@experiment` 的每行函数内部 `try/except`，失败时返回带 `error` 字段的行（指标留空但行保留）。

**Why**：现代 `@experiment` 的 `arun` 对异常**默认丢行 + print**（`experiment.py:190-195`），同样是静默——与 legacy 的 NaN 同类问题。我们要的是「失败可见」（治本）。

**Alternatives**：纯用 `arun` 默认行为 → 行数悄悄变少，报告看不出哪题失败。

**边界（stdout 契约）**：`error` **只进 CSV/MD**（新增列；无下游消费者、无列名断言，仅须不进主分白名单 `eval_scoring.py:72-91`）。**stdout 的形状永远不改**——它是 `src/cli/compare_retrieval.py:82-110` 的解析契约（子进程跑 `eval_ragas` 后正则取均值），且被 `tests/cli/test_eval_ragas.py:659` 锁成**恰好 5 行**。失败信息另经**日志事件**暴露。

**有界重试（2026-10-15 追加）**：单指标失败时**在我们这层重试至多 `RAGAS_METRIC_MAX_ATTEMPTS-1` 次**（默认总尝试 2 次），**任何失败都重试（含超时）**。
- **Why**：主因 `IncompleteOutputException`（输出爆量）**是随机的**（同输入一次 6.1s 成功、一次 110.9s 失败）⇒ 重试成功率高；且不重试会把失败行踢出该指标均值，造成**乐观偏差 + 各指标分母不齐**。
- **超时也重试（实测修订）**：初版按"超时重试大概率再挂"的**推测**排除超时；随后实测抓到反例——一次 **150s（read 上限）超时，重试后成功恢复**，该指标拿到真值。故改为不排除超时。最坏代价 = 2 × read 上限 / 指标。
- **Why not SDK 层**：SDK 重试是**静默乘法放大**（默认 2 ⇒ 3 次请求）；放在本层则**可见**（记 `metric retry` 事件）、**有界**、**逐指标独立**。SDK `max_retries` 保持 0。
- **`error` 语义不变**：仍**只在最终失败时非空**（重试成功不写 error，值即真值）。
- **记异常链（可诊断性）**：`error` 与 `metric retry` 事件均记 `_exc_chain_summary` = `外层类<-根因类: 根因消息`。**Why**：只记类名会丢根因——实测一次 150s 超时被包装成**无消息的 `RuntimeError`**，报告里只剩 `RuntimeError`，无从定位；带上链后可直接看出根因（如 `RuntimeError<-APITimeoutError: Request timed out.`）。
- **记原始响应（因果关系，2026-10-15 追加）**：新增 `judge call` 事件（`metric`/`q_index`/`attempt`/`seconds`/`ok`/`note`），`note` 经 `_judge_failure_note` 带出 instructor 异常的 `last_completion`：`finish_reason` + `out_tokens` + **原始输出片段**。
  - **Why**：此前只能凭结果反推成因（误判过"输入撑爆""生成失控"）。带上原始响应后一眼看出是**空结构输出**（`finish_reason=stop` + 输出仅 148–215 token + `{ }`），与"截断"是两种不同模式。`q_index` 使其可与报告行/trace 对齐。

### D3 — 指标集：沿用经典 5 个（collections 版），不加 rubric

**选**：`Faithfulness` / `AnswerRelevancy` / `ContextPrecision` / `ContextRecall` / `AnswerCorrectness`（`ragas.metrics.collections`）。

**Why**：口径与现状对齐（实测 4/5 与 legacy 逐位一致），迁移风险最小。rubric 类（带 `feedback`）是**可解释性**增强，属另一维度，另案。

**Alternatives**：换 `RubricsScoreWithReference` → 改变分数口径、需自定义 rubric，且非本次目标（`ragas-agent-eval` 亦已推迟）。

### D4 — 数据载体：`backend="inmemory"`，产物只走我们自己的报告层

**选**：`@experiment` 的 `Dataset` 用 **`backend="inmemory"`**（ragas 侧**不落盘**）；产出仍只走我们既有的 `data/ragas/reports/`（CSV/MD）。

**Why**：本仓**已有自己的报告层**（`data/ragas/reports/`）；若再用 `local/csv` 会额外产出 `data/ragas/datasets/` + `data/ragas/experiments/`，形成**双份产物 + 两个多余目录**。

**Alternatives**：`backend="local/csv"` + `root_dir="data/ragas"` → 多两个目录、双份产物，否；照官方示例用 `evals/` → 与既有目录结构分叉，否。

### D5 — 并发：行级信号量，默认 5

**选**：加可配置**行级**信号量 `RAGAS_EVAL_CONCURRENCY`（默认 **5**）限制 `arun` 的并发行数；**行内 5 指标串行**（不做指标级并发）。

**Why**：`arun` 对 dataset 全量 `as_completed`（`experiment.py:188`），无内建限流；实测裁判端点在连打下会 403/超时。限**行级**即可压住总并发（≈ `行并发 × 行内调用`），行内串行实现最简。

### D6 — 裁判构造：选型判据 + 采样 + 超时（**本次核心增量**）

**选**：
0. **裁判模型 = `qwen3.8-flash`**（实测可靠，见「Resolved」）。主模型侧拟切 `qwen3.7-flash` ⇒ 裁判 ≠ 选手，无自评偏置。
1. **选型判据 = 结构化输出可靠性**（活体抽样验证，不看「能不能调通」）。
2. **采样** = `temperature=0` 且**不发 `top_p`**。
3. **超时** = **细粒度分层 + 关闭 SDK 重试**（2026-10-15 定稿）。`httpx.Timeout(connect=10, read=150, write=30, pool=10)`，并把 SDK `max_retries=0`。失败按 D2 可见。
   - **依据（实测 5 题 69 次裁判调用）**：p50=7.6s · p95=66s · p99=111s。`read=150s` 高于 p99 约 35%（误杀少），同时把单次死等上限从 300s 压到 150s。
   - **为何必须细粒度**：原先是平铺 `timeout=300` —— **标量被 httpx 展开成四个维度全都 300**（`connect=read=write=pool=300`；httpx 无「整请求总预算」概念，四维是**分阶段**计时：pool→connect→write→read，平时命中的几乎总是 read）。且 openai SDK 默认 `DEFAULT_TIMEOUT=Timeout(connect=5, read=600, write=600, pool=600)`、`DEFAULT_MAX_RETRIES=2`，**`APITimeoutError` ⊂ `APIConnectionError` 会被 SDK 重试** ⇒ 一个逻辑调用最坏 = 3 × 300s = **900s** 空转。实测 21 题 91 分钟里约 80 分钟是这种空转。
   - **重试层勘误**：**唯一的重试放大层是 openai SDK**（实测 mock transport：`max_retries=2` → 实际发 **3** 次 HTTP；`=0` → **1** 次）。`instructor.from_openai` **无 `max_retries` 参数**，其自身只做 `Total attempts: 1`、不叠加。（更正早前“instructor 再叠一层”的说法。）故 `max_retries=0` 后整链路**只发 1 次、不重试**。
   - **长尾的成因（非网关故障）**：全量 21 题的 16 次 `Request timed out` 全部发生在**思考 ON**；关思考后同一批调用 max=111s。`111s ≈ 跑满 max_tokens=8192`（≈74 tok/s）⇒ 长尾被**输出上限**顶住。⇒ 关思考 + 细粒度超时后：5 题 **0 失败**、max=60.9s、0 次 ≥150s。
   - **排查记录（否定项，避免后人重走）**：① 今天 `429` **0 条**、40 并发全成功 ⇒ 非限流；② `build_ragas_judge()` 全程只构造一次 ⇒ 非连接池抖动；③ 最小复现同步 `embed_text` 在运行循环中正常（3.0s）⇒ RAGAS 的跨线程/跨循环桥接**未证实**为本次元凶（保留观察）。
4. legacy 路径（迁移完成前）关闭 RAGAS 对裁判温度的**静默覆盖**（`bypass_temperature=True`）。
5. **输出预算** = `max_tokens=8192`（`RAGAS_JUDGE_MAX_TOKENS`）。RAGAS 内建 `InstructorModelArgs.max_tokens=1024`，长上下文（年报表格 + 多 statements / TP-FP-FN 拆解）会被截断 → `IncompleteOutputException`（实测 v5 5 题中 2 题命中）。
6. **思考档位 = 关**（`RAGAS_JUDGE_THINKING=false`）。同题 A/B 定稿（2026-10-15）：关思考 + `max_tokens=8192` → **0 失败**且快 9×（2 硬题 2m24s vs 开思考 22m16s）；开思考 + 8192 虽无结构失败，但 21 题全量 **5/21 行 `InstructorRetryException: Request timed out`**、耗时 1h31m。
   - **踩坑记录（避免后人复踩）**：初测把「关思考」判为会引发 schema 形状错误，实为 **`max_tokens=4096` 截断**（JSON 被切半 → instructor 报成校验错误）——变量是预算不是思考。补 8192 后关闭思考 0 失败。
   - **边界**：RAGAS 对 openai 兼容端硬编码 instructor `Mode.JSON`；`Mode.TOOLS` 在思考 ON 下被百炼 400 拒（`tool_choice` 不支持 thinking mode），故不换模式。

**Why（证据见下节）**：
- 采样：RAGAS 内建默认 `temperature=0.01, top_p=0.1`（legacy `langchain_llm.py:73` / 现代 `InstructorModelArgs`），而百炼部分模型**直接 400 拒绝**；`temperature=0` 也**本来就是裁判正解**（评测要可复现）。
- 选型：不同模型**在不同指标上**的结构化输出可靠性天差地别（`kimi-k3` 在 `context_recall` 上 9/10 返回空；`qwen3.8-flash` 0/10）——**必须实测**。
- 超时：思考模型单次调用偶尔 >30s。

**Alternatives**：① 吃 RAGAS 默认 → 在受限模型上直接 400；② 按「模型名气/价格」选裁判 → 实测会选中不可靠的（kimi-k3）。

### D7 — 白名单只约束出题；评估检索镜像生产（**反转 ADR-0022 决策 C**）

**选**：`RAGAS_DOC_WHITELIST` **仅**约束出题的输入文档范围。评估侧检索**保持整库**（= 生产口径），**不**按白名单收窄。

**Why（反转理由，记明以免后人"修回"）**：
1. 检索由**题面驱动**（`retrieval.search(query, kb_id, …)`，`src/agents/tools/rag_tools.py:148`），与白名单无关；自足题的**自然检索即应命中**对应文档。
2. 检索命中白名单外文档是**切块/检索质量的可观测信号**——过滤会**掩盖**它。
3. 人为收窄会让评测测一个**生产里不存在**的系统（生产搜全库）。
4. 还可能使 `context_precision` **虚高**（把本该混进来的无关候选人为删掉）。

**Alternatives**：按白名单过滤检索（= 原 D7）→ 需改共享的**生产**检索栈（`retrieval.search` / `vector_store.*_search` / `rag_tools` 均**无 doc 级过滤**，独立评审 F1 已核实），且引入上述 4 个问题；已否。

**影响**：ADR-0022 的**决策 C（评估检索与出题同范围）**被本变更**局部取代** —— 新增 ADR-0023，并在 ADR-0022 的 `Status` 加反向指针。

### D8 — 出题链整改（新增 D12：题面自足靠「源头锚定 + 出题后门」，非 `llm_context`）

**选**：
① 改用官方 `default_transforms` 全量（补 `ThemesExtractor` + `CosineSimilarityBuilder`）；
② 传 `llm_context` 表达自足性期望（保留，但**不作为保证手段**）；
③ **源头锚定（P1）**：chunk→KG 节点`page_content` 前置 `【公司名 · 报告期】` 头，取值分块 `document_metadata`（`company` / `report_period`；缺失时以 `year`+`quarter`、`source` 兜底）；
④ **出题后自足性门（P2）**：生成样本经自足校验，不自足者改写或丢弃。

**Why**：`default_transforms` 是官方路径（当前我们缺件导致 abstract 静默丢失）。而**自足性**：原方案只传 `llm_context`，实测**不达成**——

- 官方的 `llm_context` 在 prompt 里是**第 3/4 条「Additional Context」**，原文定位为「guidance for **what type of question** to generate」（题型建议，如比较/因果题），**不是**命名/指代硬约束；且官方 testset 链路**无任何改写/指代消解步骤**（全包 grep `coreference`/`rewrite` 零命中）。
- **实测证据（2026-10-14，KB `test` v3 / 21 条）**：12 条题面不含公司名，**这 12 条的 `reference_contexts` 里一个公司名都没有**；对照全 21 条，「上下文含公司名 ⇒ 题面必含」（4/4），「上下文不含 ⇒ 12/17 不含」，**不存在"上下文有、题面却用『本公司』"**。⇒ 模型被要求"只用上下文作答"，而中文年报正文自称「本公司」，**它手上根本没有实体可消解**，不是它不消解。
- **实体本来就在手边**：同节点 `document_metadata` 已含 `company`/`report_period`/`sec_code`/`source`，`entities`/`themes` 亦含公司名；但出题 prompt 只收到 `context=[page_content]`+`themes`，**元数据从未进 prompt**。⇒ P1 只需把已有信息前置进上下文即可。

**`llm_context` 取值（保留）**：常量 `RAGAS_SELF_CONTAINED_HINT`（`src/config/const.py`），中英各一句：

```
"Each question MUST be self-contained: name the company and the reporting "
"period explicitly. Do NOT use context-dependent references such as "
"\"this reporting period\" or \"the company\".\n"
"每个问题必须自足：明确指出公司名称与报告期；禁止使用『本报告期』『该公司』"
"等依赖原文的指代。"
```

**P1 边界**：头部只进**出题用 KG** 的 `page_content`；**不动**生产索引、**不动**评估检索栈（ADR-0023），故评估仍镜像生产。副作用：测试集 `reference_contexts` 首行会带公司/报告期（可接受的增强，且成为可核验点）。缓存的 `data/ragas/kg_<kb>.json` 需重建方能生效。

**P2 边界**：只做**保义替换**（referent 不变 ⇒ 答案仍成立、检索输入微变可接受）。**多公司 multi-hop 题**（如「围绕联营公司与合营公司…」两跳来自不同公司）「本公司」**无唯一解**，替换不可行 ⇒ 丢弃（用户定稿）。

**P2 实现（定稿，2026-10-15）**：纯函数、**无额外 LLM 调用**（`src/cli/eval_self_contained.py`）。判定「题面是否点名公司」用**文档专属实体别名**——取自 KG 节点的 `entities`/`themes`，按「只出现在单一文档」过滤掉跨文档通用词（`人民币`/`2024` 自动出局）。该判据比字面「公司名」**宽**（关键产品名/期间亦视为唯一指向），但能识别跨语言（`tencent` ↔ `腾讯`），且在 v4 上**零误伤**（21/21 保留）。判定：题面已点名 → 保留；未点名但上下文仅一家公司 → 前缀「公司+报告期」保义改写后复检；否则丢弃并记 `testset self-contained dropped`（cli/warning）。

**Alternatives**：① 不传 `llm_context` → 现状（产出歧义题），否；② **只靠 `llm_context`** → 实测 12/21 不达成，否（原方案）；③ 单公司语料出题 → 源头消除歧义但丢失跨文档题、需改编排，列为 P2 之外的后续选项；④ 手写 golden set（`financial_rag-main` 与官方示例做法）→ 可控但贵、且无生产真实问题可依，列为后续选项；⑤ 多轮评测 → 属评估侧规避、不改善题面质量，另案。

### D9 — 现代 client 的构造归属与数据来源

**选**：新增**按角色集中构造 native client** 的入口（置于 provider 层，与既有 `build_chat` / `build_embeddings` 同处）：从 `resolve_role(role, registry)`（`src/infra/llm/provider/spec.py`，已给出 `ResolvedModel.base_url` / `.api_key`）取端点与密钥，构造 **`AsyncOpenAI`**；**裁判 LLM（供 `llm_factory`）与 embedding（供 `embedding_factory`）共用同一入口**。

**Why**：`llm_factory` / `embedding_factory` 的 `client` **必填且须 native `OpenAI` / `AsyncOpenAI`**；仓内现只有 LangChain `ChatOpenAI`（`get_judge_llm`），**无 native client 归属**；且 `answer_relevancy` / `answer_correctness` 依赖 embedding 侧，而 D6 只覆盖裁判 LLM。不指定归属 → 两种实现可自由发挥（端点/密钥解析外溢到 CLI）。

**Alternatives**：在 `eval_ragas.py` 里就地 `AsyncOpenAI(...)` → 与 provider 层重复端点/密钥解析逻辑，且绕开既有 `resolve_role`。

### D10 — 模型版图（本变更内一并定）

**选**：`judge → qwen3.8-flash`；`main` / `classify` / `summary → qwen3.7-flash`。

**Why**：① 裁判须**结构化输出可靠**（D6，实测 qwen3.8-flash 0/10 空）；② 裁判与选手**必须不同模型**以免自评偏置——现状 `main=qwen3.8-flash`，若只改 judge 则 `judge == 选手`；把 `main` 降到 `qwen3.7-flash` 后二者错开。③ `qwen3.7-flash` 更便宜（用户目标）。

**Alternatives**：只改 judge 不动 main → `judge == 选手`，D6 的无偏置论据不成立（独立评审 F2）。

**代价**：换**生产主模型**（影响线上答案质量），且**历史评测基线作废**（选手变了）。

### D11 — `eval_report.overall_score` 统一到主分口径

**选**：`eval_report` 的 `overall_score` 改为**主分口径** = `main_score_means(df)` 的**五项指标均值**（**纯 KB 掩码**、含 `answer_correctness`），替换现在的「旧四项加权」。同步：① **删掉** MD 报告里那行「两口径分叉」Note（`eval_ragas.py:575`）；② **API 补** `answer_correctness`（`src/api/kb_eval.py:44-59`）；③ 阈值提到**单一常量**（不在落库与前端各硬编码一份）。

**Why**：现状 `overall_score`（`eval_ragas.py:955-965`）= `{faithfulness .3, context_recall .3, context_precision .2, answer_relevancy .2}` 加权——**漏 `answer_correctness`**、且**未按纯 KB 掩码**。而它是**用户可见**的（前端 `deploy/nginx/html/index.html:1113` 显示大分数并 `>= 0.7` 上色）⇒ 前端展示的正是**已知口径不对**的分。

**「别动它」的三条理由经查均不成立**：① 「前端依赖」→ 前端依赖的恰是**该修的缺陷**；② 「属另一案」→ 本变更已在动评测产出层（D2）；③ 「历史不可比」→ **无消费方**（`kb_eval` API 只取 `get_latest_eval_report`，只显示最新一次，无趋势/告警读历史行）。

**代价**：
- **阈值要重标**：`passed = overall >= 0.70`（`eval_ragas.py:981`）与前端 `overall >= 0.7`（`index.html:1113`）都按旧口径标定。
- **实测（粗参考，n=7）**：新口径**系统性低于**旧口径 **−0.03 ~ −0.13**（`answer_correctness` 偏低所致，其值约 0.75）。样本区间 ≈ **0.68 ~ 0.92**、均值 0.82。旧口径 `0.7662` 判过 → 新口径同题 `0.6780` **会 FAIL**。⇒ 阈值先维持 `0.70` 并标「**待标定**」（新口径分布未测够）。
- 前端**展示值会变**——**这是目的**。

**证据**：`src/cli/eval_ragas.py:955-965`（旧公式）、`:980-981`（落库 + passed）、`src/api/kb_eval.py:44-59`（API 缺 answer_correctness）、`deploy/nginx/html/index.html:1113`（前端展示 + 上色）、上表实测区间。

## 证据（活体实测，2026-10-14）

| 实验 | 结论 |
|---|---|
| 现代栈 5 指标 vs legacy 同题 | faithfulness 1.0000=1.0000 · answer_relevancy 0.9357≈0.9371 · context_precision 0.9167=0.9167 · answer_correctness 0.7471≈0.7470 ·（context_recall 因裁判问题 nan） |
| **两栈对照的 nan 说明**（回应评审 OQ1） | 两次对照各**恰有 1 个 nan，成因不同且均可解释**：kimi-k3 裁判时 `context_recall` nan（模型返回**空分类**，n=10 实测 9/10 空）；qwen3.8-flash 裁判时 `faithfulness` nan（30s **超时**）。换正确裁判 + 充足超时后二者均正常（qwen 下 `context_recall`=1.0；`faithfulness` statements 空率 0/5）。⇒ 「5 指标齐出分」的验收**前提**= 可靠裁判（D6）+ 显式充足超时（D2/D6），而非「任取模型必得 5 分」。 |
| `context_recall` 空返回率（n=10） | **qwen3.8-flash 0/10** ✅ · **kimi-k3 9/10** ❌ |
| `faithfulness` statements 空率（n=5，qwen3.8-flash） | 0/5 ✅；但 30s 超时下 **2/5 超时** ⚠️ |
| `kimi-k3` 温度/`top_p` 接受度 | 仅接受 {0,1} 或省略；`0.01` / 任意 `top_p` → 400 |
| 排除假设（空分类成因） | 与 `max_tokens`（1024/4096/8192）、instructor mode（JSON/MD_JSON/TOOLS）**无关**；**裸调真实 prompt 亦 3/3 返回 `{"classifications": []}`** ⇒ 模型行为 |
| 现代 `llm_factory` 配置 | 默认（0.01/0.1）失败；`temperature=0` 失败（top_p 仍在）；**`temperature=0, top_p=None` 成功** |
| 裁判思考档位 × 输出预算（2026-10-15，v5 硬题 q2/q3 + 21 题全量） | **思考 OFF + 8192 → 0 失败、2 硬题 2m24s** ✅（定稿）；思考 ON + 8192 → 硬题 0 失败但 21 题全量 **5/21 行 `Request timed out`**、1h31m；思考 OFF + 4096 → `InstructorRetryException` 2/2（实为**截断**，8192 可救）。`Mode.TOOLS` 在思考 ON 下被百炼 400 拒（`tool_choice` 不支持 thinking mode）。 |
| 裁判是否默认开思考（探测） | `qwen3.8-flash` **默认开**：一句「只回复 OK」产生 `reasoning_tokens=51` 且带 `reasoning_content`；显式 `extra_body={"enable_thinking": False}` 可关（`completion_tokens=1`、无 `reasoning_content`）。 |
| 裁判调用耗时分布（5 题 69 次，思考 OFF） | p50=7.6s · p90=29.4s · p95=66.2s · p99=110.9s · max=110.9s；2 次失败为 `IncompleteOutputException` 且耗时 102.8s/110.9s（**撞满 max_tokens=8192**，同款正常仅 5.8s/454 字符）。 |
| 细粒度超时前后（5 题） | 旧（平铺 300s + SDK 重试 2）：失败 **3/69**、max 110.9s、墙钟 5m32s；新（connect10/read150/write30/pool10 + `max_retries=0`）：失败 **0/70**、max 60.9s、0 次 ≥150s、墙钟 4m59s。 |
| **裁判失败的真实成因（2026-10-15，抓原始响应）** | 同输入（16,037 字符）并发 12：**9/12 成功**（7–21s、1–2 条分类）；3 次失败均为 `finish_reason=stop`、`completion_tokens=**148–215**`、原始输出 = `{ "classifications": [ { }, { }, { } ] }` 或 `{"": "", "": 0}` ⇒ **模型吐「空结构」**（pydantic 校验失败）。**推翻**此前"输入撑爆 / 生成失控"的推断。 |
| 裁判 prompt 规模分解（实测渲染） | RAGAS 官方模板**固定开销 = 5,767 字符**（instruction+few-shot+schema）；每条 context ≈ 2,000 字符（DB 分块真实长度：均值 **652**、最大 **1,998** ⇒ **chunk 不大**）；5 条 ⇒ **16,037 字符**（与实测 16,674 吻合）。另：`prompt_tokens ≈ 38–40k`（`cached_tokens≈39k`）⇒ **字符数不可直接推 token**。 |
| 出题自足性（`--generate --size 20`，v3 / 21 条） | **12/21 题面不含公司名；这 12 条的 `reference_contexts` 全无公司名**；「上下文含公司名 ⇒ 题面必含」4/4 ⇒ 自足性由**上下文是否带实体**决定，与 `llm_context` 无关（D8） |

## Risks / Trade-offs

- **[现代栈是 `0.4.3` 的「新」接口，上游仍在动]** → 迁移后若上游再改，需跟版；缓解：调研文档记录版本基线（`docs/research/2026-10-13-ragas-testset-generation.md`），并固定 `ragas==0.4.3`。
- **[思考模型慢/偶发超时]** → 超时给足 + D2 显式失败；若某题反复失败，报告可见而非静默。
- **[裁判模型受额度/限流影响]** → D5 限流 + 报告 `error` 列；额度耗尽时输出「全 error」而非「全 nan」，一眼可辨。
- **[出题自足需自建（官方无问题级硬校验、无改写步）]** → 已由 D8 的 **P1 源头锚定** + **P2 出题后门**覆盖；P2 对多公司 multi-hop 题可能只能丢弃。**不**复活旧 `QuestionFilter`（对已移除实现打补丁）。
- **[`top_p` 不发对某些模型的影响未逐一验证]** → 只验证了 400 场景与成功场景；缓解：裁判构造集中在一处，便于按模型调。

## Migration Plan

1. **裁判构造与失败语义**（D6/D2/D9）：新增「按角色集中构造 native client」（采样 `temperature=0` / 不发 `top_p` + 显式超时）；失败记 `error`（**只进 CSV/MD，stdout 形状不变**）。legacy 路径的 `bypass_temperature` 已单独提交。
2. **执行内核迁移**（D1）：`run_evaluation` 改 `@experiment` + `collections`（`Dataset` 用 **`inmemory`**，**行级**信号量 `RAGAS_EVAL_CONCURRENCY=5`）；产出层（CSV/MD/DB/Langfuse）改读新表结构。**验收通过后删除 legacy 路径**（`evaluate()` + 双 wrapper），**不长期并存**。
3. **出题整改**（D8）：`default_transforms` 全量 + `RAGAS_SELF_CONTAINED_HINT` + **P1 源头锚定** + **P2 出题后门**；重出测试集验证 abstract 恢复**且题面 100% 自足**（须先删 `data/ragas/kg_<kb>.json` 重建 KG 方能生效）。
4. **模型版图**（D10）：registry 绑定（`judge→qwen3.8-flash`；`main`/`classify`/`summary→qwen3.7-flash`）+ `qwen3.7-flash` catalog 定价。
5. **活体验收**：1 题冒烟 → 小批 → 全量；逐项核对与 legacy 的复现度（见「证据」）。
- **回滚**：各步独立可回退；**legacy 路径在验收通过前保留**，通过即删（不长期并存）；配置/registry 单独提交。

## Resolved（原 Open Questions，2026-10-14 定）

1. **裁判模型**：定为 **`qwen3.8-flash`**。实测其结构化输出可靠（`context_recall` 空返回 0/10、`faithfulness` statements 空 0/5），而 `kimi-k3` 在 `context_recall` 上 9/10 返回空被否。主模型切 `qwen3.7-flash` **纳入本变更**（见 D10）⇒ **裁判 ≠ 选手，无自评偏置**。
2. **`Dataset` 落盘**：定为 **`backend="inmemory"`**（不落任何目录），产物只走既有 `data/ragas/reports/`——见 **D4**。实测 `ragas.backends` 提供 `inmemory` / `local/csv` / `local/jsonl` / `gdrive` 四种。
3. **裁判思考档位**：定为**关**（2026-10-15 经 A/B 定稿）。见「证据」：关思考 + `max_tokens=8192` → 0 失败且快 9×；开思考 + 8192 在 21 题全量下 5/21 行超时。评测离线、调用量可控，但思考带来的只是延迟与超时，结构化判定在 `temperature=0` 下不依赖 CoT。如需更高质量再评估思考预算（`thinking_budget`）。
4. **rubric 类指标**：本次不做（见 D3），留待「可解释性」另案。

## Original Open Questions（存档）

1. **D3 是否该上 rubric 类指标**（带 `feedback`）以补可解释性？——本次不做，留待另案。
2. **裁判模型最终选谁**？`qwen3.8-flash` 实测可靠，但它是/曾是主模型同族；若主模型换 `qwen3.7-flash`，裁判 `qwen3.8-flash` 无自评偏置。**需用户定**。
3. **`@experiment` 的 `Dataset` 落盘位置**：本地 CSV backend 会生成 `datasets/`+`experiments/` 子目录，是否接受与既有 `data/ragas/` 并存的目录结构。
4. **思考档位**：裁判是否需要开思考（成本 vs 质量）——`qwen3.8-flash` 可配。
