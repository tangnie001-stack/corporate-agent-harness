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
- 官方 FAQ 说：**推荐 DeepEval 原生 RAG 指标**；桥的 `NaN` 来源是"ragas 在模型输出非法 JSON 时返回 NaN，桥原样透传"。**订正**：官方只解释了来源的**一种**；源码核实 `ragas.evaluate` 的 `raise_exceptions` 默认 `False`、executor 对**任何**异常都返回 NaN ⇒ NaN 是**一切失败**（超时/额度/403/连接/解析）的统一出口（见 D4）。
- 温度由 ragas 内部决定：`get_temperature(n) = 0.3 if n>1 else 0.01`（`ragas/llms/base.py:71-73`）；`LangchainLLMWrapper` 会**改写底层模型的 temperature 属性**——检查语句在 **`agenerate_text`（异步，`:269` 起，判断 `:281`）**，`generate_text`（同步，`:217` 起）**不检查**；桥经 `evaluate` 的异步 `single_turn_ascore` **确实走到异步路径**（机制与拿不回温度 0 的原因见 D3 订正）。

**本次的执行模型（与现状对照，先读这一节）**

DeepEval 有**四种执行入口**：`evaluate([tc],[m])`（批量事后，官方明说不过门禁）、`assert_test()` + `deepeval test run`（pytest 形态，过门禁）、`dataset.evals_iterator(metrics=[...])`（数据集驱动，与 agent 运行交错）、`CallbackHandler(metrics=[...])`（挂 LangChain/LangGraph，**运行中 inline 评**）。

本 change 采用**混合式**：**不**走 `assert_test`（门禁自建，见 D12）；**Agent 行为轴必须由 `evals_iterator` 驱动**（D6，实测只挂 handler 不评分）；而**四指标**走我们自己的逐题循环（已有 trace 体系）。

```
             现状（RAGAS 现代栈）                本次（DeepEval）
出题          --generate（已下线，代码保留）        不变
────────────────────────────────────────────────────────────────
① Agent 跑题  graph.ainvoke 逐题                  graph.ainvoke 逐题
              每题一个 eval_<hex>                 ＋ config.callbacks 挂 CallbackHandler()
                                                  ↳ 但**必须由 evals_iterator 驱动**才评分（见 D6）
────────────────────────────────────────────────────────────────
② 裁判        第二遍：5 指标逐行 ascore            第二遍：**只剩四指标**（桥 measure，**串行**）
              行级并发 5                          ⚠️ 行为指标**不在这一遍**（在①由 evals_iterator 评掉）
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

### D1 环境与依赖：**为统一单环境而主动下移**（原"click 死锁"表述已订正）

- **事实 A（原表述作废）**：原写的"`deepeval 4.2.9 + ragas 0.4.3` → `ResolutionImpossible`，**click 交集为空**"**不成立**。两点订正：
  1. 原证据是**循环论证**——当时那次 dry-run **显式 pin 了 `click==8.4.2`**（正是 deepeval 禁止的值），必然失败；
  2. **wheel METADATA 反证**：`datasets 4.0.0` 只要求 `huggingface-hub>=0.24.0`，而 `huggingface_hub 0.24.0` **不依赖 click**、`1.16.1` 也不要求 `click>=8.4.2` ⇒ **存在满足全部约束的赋值**。
  ⇒ 准确表述：**在项目完整 pin 集下，pip 在默认策略下无法在合理时间内解析出该组合**（独立评审实测：裸装 dry-run 90s 超时、加约束变体 150s 超时，均为 `exit 124`），**不是"数学上不可能"**。
- **事实 B**：`deepeval 4.2.9 + ragas 0.3.9` **可解析且已实装成功**（落 `click-8.3.3` / `huggingface_hub-1.16.1`）。
- **决策（理由改写）**：项目内 ragas 固定 **`0.3.9`**，并在 `pyproject.toml` 显式记录 `click` / `huggingface_hub` 下限。理由 = **"为统一单环境而主动下移"**（评测与抓取/拼装共用一条依赖线、避免双栈），**而不是**"被 click 死锁逼迫"。
- **回滚（原缺，必写）**：**回滚 = 恢复 `ragas==0.4.3` + `datasets==4.0.0` 的 pin，并同时放弃 `deepeval` 与行为轴**（桥与行为指标都依赖 deepeval）。
  - **触发条件**（任一）：① 实施中发现桥的 NaN 率 >10% 且逃生门不可行；② D6 的 `evals_iterator` 路径在 `ainvoke` 下被证实不可用且无替代——**该条已由 Q1 探测（2026-10-16）否定，见 D6**。
  - 触发时 SHALL 在台账记录原因与"回到的口径版本"。
  - **不做"只回滚 ragas、保留 deepeval"**：两者共存正是本次下移的前提，拆开会让依赖线回到"装不上"。
- **运行期环境变量（必设，官方明文）**：`DEEPEVAL_DISABLE_DOTENV=1`（import 即加载 `.env`/`.env.local` 进 process env）与 `DEEPEVAL_TELEMETRY_OPT_OUT=1`（否则 posthog 遥测开启）。
- **代价**：依赖下移（0.4.x 起 `ragas.testset.transforms.default.default_transforms_for_prechunked` 不存在，见 D8）。

### D2 入口与调用面：用桥的四个"单项"，不用聚合

- 桥内共 6 个类：`RAGASContextualPrecisionMetric` / `RAGASContextualRecallMetric` / `RAGASContextualEntitiesRecall` / `RAGASAnswerRelevancyMetric` / `RAGASFaithfulnessMetric` / 聚合 `RagasMetric`（`deepeval/metrics/ragas.py:38/115/186/334/414/484`）。
- **必填四字段**：`input` / `actual_output` / `expected_output` / `retrieval_context`（官方 Required Arguments）。
- **决策**：**只用经典四个单项**，不用 `ContextualEntitiesRecall`，也不用 `RagasMetric` 聚合。理由：聚合在本地 4.3.0 源码里是 **5 项算术平均**（含 entity recall，`ragas.py:521-541`），而官方文档写的是 4 项——**文档与源码不一致**，用聚合会把口径绑在一个未对齐的实现上；单项更可控、更易诊断。

### D3 裁判与温度：接受 ragas 的分档温度（本项为公开接受的代价）

- **裁判构造走项目既有 provider 层**：`build_chat("judge", thinking=RAGAS_JUDGE_THINKING, max_tokens=RAGAS_JUDGE_MAX_TOKENS, http_async_client=httpx.AsyncClient(timeout=httpx.Timeout(connect/read/write/pool=RAGAS_HTTP_TIMEOUT_*), event_hooks=...), max_retries=RAGAS_HTTP_MAX_RETRIES)`。
  - **⚠️ 必须挂在 `http_async_client`，不是 `http_client`（订正，前稿写错）**：`langchain_openai` 的 `http_client` **只喂同步 client**（`chat_models/base.py:806` 明写 *"Only used for sync invocations"*；异步 client 在 `:1273` 用 `self.http_async_client` 构造）。而 **ragas 的裁判调用是异步的**——`evaluation.py:240` 提交 `metric.single_turn_ascore`（异步），`agenerate_text` 走 `await self.langchain_llm.agenerate_prompt(...)`（`ragas/llms/base.py:269` 起），`ragas/prompt/base.py:129` 亦为 `await llm.agenerate_text`。⇒ 只传 `http_client` 时**异步路径用默认 httpx client**：请求级 hooks 一条不响、细粒度超时全部失效。**SHALL 传 `http_async_client`**；若希望同步路径同样受控，可**两个字段都传**（同一套 timeout/hooks）。
  - **hook 必须 `async def`**：httpx 0.28.1 同步 client 调 `hook(response)`、**异步 client 调 `await hook(response)`**（`httpx/_client.py:982` vs `:1697`），给 AsyncClient 传同步 hook 会 `await None` 抛 `TypeError`；读体也须 `await response.aread()`（同步 `read()` 在异步流上抛 `RuntimeError`，`httpx/_models.py:944`）。async hook 与"显式闭包绑定 trace_id"**可共存**（闭包内仍是 async def）。
  - **细粒度超时的承载点也随之改变**：`httpx.AsyncClient(timeout=httpx.Timeout(...))` **自带** timeout，故细粒度超时由该 client 保证；**不要依赖** ragas 的 `set_run_config` 去设 `request_timeout`——它在**构造之后**写 `self.langchain_llm.request_timeout = run_config.timeout`（`ragas/llms/base.py:337`），而 langchain 的 client 在**构造期**就建好（`validate_environment` 是 `@model_validator(mode="after")`，`base.py:1140/1154`）⇒ 该赋值**对已建 client 无效**。
  - **思考开关必须走 `thinking=`，SHALL NOT 硬编码 provider 专有键（订正）**：`build_chat` 的 `thinking` 参数经 `thinking_extra_body(spec, enabled)`（`src/infra/llm/provider/thinking.py:8`，仓库里**唯一**产生 provider 思考参数的地方）翻译——dashscope → `{"enable_thinking": …}`，deepseek → `{"thinking": {"type": …}}`。硬写 `extra_body={"enable_thinking": …}` 在**非 dashscope 判官**上会静默失效或 400，且违反 CLAUDE.md「provider 差异收敛到 `src/infra/llm/provider/`」。
  - **⚠️ 硬约束①：`thinking=True` 会静默打断 `answer_relevancy`（实测，2026-10-16）**：`answer_relevancy` 请求 `n=strictness=3`（`_answer_relevance.py:142-143`），而 DashScope 在 `enable_thinking=true` 时**拒绝 `n>1`**——实测 6/6 返回 `BadRequestError(400) InternalError.Algo.InvalidParameter: The n parameter must be 1 when enable_thinking is true`。该错误被 ragas 的 executor 吞成 `NaN`（不抛，见 D4）⇒ **该指标静默全线失效、且被 NaN 门禁记为一格"裁判失败"**。**影响范围已核源码：四项中只有 `answer_relevancy` 会请求 `n>1`**（`faithfulness` `:158/174`、`context_precision` `:151`、`context_recall` `:137` 的 `generate_multiple()` 均不传 `n`，默认 1）。⇒ **`RAGAS_JUDGE_THINKING` SHALL 保持 `False`**；若日后确需打开，SHALL 同时为该指标配 thinking 关的独立 judge（或把 `strictness` 降为 1，属改口径）并记录该口径变化，**SHALL NOT** 只翻开关了事。
  - **⚠️ 硬约束②：中文语料下 `faithfulness` 语言错配 ⇒ 分数不可复现（实测，根因已定位）**：ragas `Faithfulness` 的两段提示词均为**英文**且无语言约束（`_faithfulness.py:37` 抽陈述、`:74` NLI 判定）⇒ `thinking=False` 时判官会把**中文答案译成英文陈述**（`约 6603 亿元` → `…approximately 660.3 billion yuan`），NLI 段因此必须做**亿↔billion 换算**，而该换算在非思考模式下**偶发算错**（判官 `reason` 原文可证：`a discrepancy by a factor of 10 (6603 vs 660.3)`）⇒ `faithfulness` 在同一输入上**二值抖动**（同进程同题 4 次 = `[0.0,1.0,0.0,1.0]`；另一轮 6 次 = `[1,1,1,1,1,0]`）。**后果**：`faithfulness` 进主分，而主分 / 门禁 / **跨轮台账比对**都假定同输入可复现 ⇒ 不修会污染组 10 的阈值重标定。
    - **候选修法 C（待 N≥20 复核后才定稿）**：把 `ragas.metrics.faithfulness` 的 `statement_generator_prompt` / `nli_statements_prompt` 换成**强制中文、禁翻译禁单位换算**的提示词（`thinking` 保持关）。N=6 实测该修法两次 `[1]*6` 稳定（对照基线 `[1,1,1,1,1,0]`）。
    - **代价（SHALL 明写）**：这是**改第三方内部**——桥的 `measure()` 内 `from ragas.metrics import faithfulness` 用的正是该**模块级单例**，故 patch 生效；但若 deepeval 日后改为自建实例，patch 会**静默失效**（与本设计反复踩到的"开关存在却无效/挂错字段零触发"同型）。故 **SHALL 加身份断言守护**：`ragas.metrics.faithfulness.statement_generator_prompt is <我们的实例>`，并以真实链路冒烟（`hook_events > 0` 那条）作行为级兜底。
    - **被排除的替代**：`thinking=True` 也能稳定（且会让判官自动跟随答案语言），但被硬约束①否掉。
    - **证据**：`docs/tmp/deepeval-poc/RESULTS.md`「faithfulness 非确定性根因」节。
  - **理由**：桥要的是 langchain `BaseChatModel`；`build_chat(role, *, thinking, model, **overrides)`（`src/infra/llm/provider/factory.py:39-68`）会把 `**overrides` 透传给 `ChatOpenAI`，故上面这批参数**照旧生效**，同时保住 `judge` 角色的 provider 解析（`src/infra/llm/provider/spec.py:87` → `RAGAS_PROVIDER` / `RAGAS_LLM_MODEL`）。
  - **SHALL NOT** 在 `src/` 里直写 `ChatOpenAI` + 硬编码 DashScope 端点 / 密钥：那会绕过 provider 层、与既有 `get_judge_llm()`（`src/models.py:188`）重复，并违反 CLAUDE.md「provider 差异收敛到 `src/infra/llm/provider/`、硬编码集中管理」。上一稿 design 写的 `OpenAIModel(...)` 是 DeepEval 自己的模型类，**桥不接受**，已删。
  - **类型**：`build_chat` 返回 `ReasoningPreservingChat`（`src/infra/llm/provider/chat.py:24`，`ChatOpenAI` 子类）⇒ 桥可作为 langchain `BaseChatModel` 接收。
  - **不传 `temperature`**：温度由 ragas 决定（见下）。`get_judge_llm()` 现硬编码 `temperature=0` 与单值 `timeout=`，不满足本项（需要 `http_async_client` 细粒度超时 + `max_retries=0` + `max_tokens` + `thinking`）⇒ 本次用 `build_chat("judge", …)` 直构；若把 `get_judge_llm` 扩参亦可，但**不得**在该处声明"温度为 0"（与 spec 的报告条款冲突）。
- **embeddings 复用现有 `build_embeddings()`**（`RAGASAnswerRelevancyMetric` 是四项中唯一需要 embedding 的）。
- **温度不由我们决定**，由 ragas 按 `n` 分档：
  - `faithfulness` / `context_precision` / `context_recall`（`n=1`）→ **0.01**；
  - **`answer_relevancy` → 0.3**（legacy `_answer_relevance.py:142-143` 传 `n=self.strictness`，`strictness` 默认 **3**；`generate_multiple` 的 ragas-LLM 分支把 `n` 透传，`temperature=None` → `get_temperature(3)=0.3`）。
- **`answer_relevancy` 的语义与量程（组 10 阈值重标定须知；实测，2026-10-16）**：该指标 = **原问题** 与「**仅凭答案**重生成的问题」的余弦相似度均值（问题生成器**看不到 context**，`_answer_relevance.py:96-115/142`）⇒ 它实质在测"**答案能否自洽地反推问题**"。**答案不自包含时系统性偏低**：同一题的残句答案（`约 6603 亿元`，无主体）稳定 **0.42–0.44**（生成器只能凭空猜主体，实测猜成"中国的 GDP / 某公司的市值"），换成自包含答案（`腾讯控股 2024 年营业收入约为 6603 亿元。`）即 **0.88–0.97**。⇒ 重标定阈值 SHALL 按此语义取值，**SHALL NOT** 把低分当"故障分值"处理（ragas 本就按设计惩罚不完整答案）；另注：该低分**不是**语言问题（强制中文提示词仅把 0.44 抬到 0.45，且英文生成的问题同样能得 0.82–0.96）。
- **为什么 0.01 是可接受的**：0.01 ≈ 确定性，同时**避开部分供应商对 `temperature=0` 的特殊逻辑**（社区既有此实践）。0.3 对 `answer_relevancy` 是**有意**的——该指标需要"生成多个多样化问题再取均值"，压到 0 会退化。
- **必须写的代价**：温度**不受我们控制**，且**按指标分档**；`answer_relevancy` 那一项的分数稳定性天然低于其余三项。
- **订正（上一稿把机制说反了这一处，必须一并纠正）**：`bypass_temperature` 的检查**确实**在 **`agenerate_text`（异步，`ragas/llms/base.py:269` 起，判断在 `:281`）**，**`generate_text`（同步，`:217` 起）不检查**——这一点上一稿写对了。但**结论的理由写错了**：上一稿推"桥走 `measure()` → 同步路径 ⇒ 该开关对我们无效"，**不成立**。
  - **事实**：桥的 `measure()` 只是**外层同步 API**；其内部 `ragas.evaluate` 经 `asyncio.run(_async_wrapper())`（`ragas/evaluation.py:433/456`）提交 `metric.single_turn_ascore`（**异步**，`:240`）⇒ 最终走 `agenerate_text`，**正是检查 `bypass_temperature` 的那条路径**。
  - **拿不回温度 0 的真正原因**：`ragas.evaluate` 会用 `LangchainLLMWrapper(llm, run_config=run_config)`（`evaluation.py:151`）**重新包装**裁判，`bypass_temperature` 取默认 **`False`**，而**桥不暴露该参数** ⇒ 开关被检查、却无法置真。
  - **可核验方式**：读 `evaluation.py:151`（包装处）、`:240`（异步提交 `single_turn_ascore`）与 `base.py:281`（检查语句）。
  - **附带（不是削弱而是加强 D7）**：既然走的是 `agenerate_text`，它**确实会改写 `self.langchain_llm.temperature`** ⇒ D7 的"并发下温度被改掉"风险论证成立且比上一稿更实。

### D4 两个已知坑（必须显式处理，否则门禁不可判）

1. **默认模型坑**：桥的 `model` 默认是 `"gpt-3.5-turbo"`（**7 处**，已 `grep -c` 核实；原文写"6 处"有误），该模型**已停服**（DeepEval issue #3404）。→ **每一个度量构造都必须显式传 `model=`**，并在测试里加断言防止回退。
2. **NaN 坑（"NaN 只等于失败"是错的；但**不要**去做可归因分类——前稿的三分类已作废）**：桥/ragas 产出的 `NaN` 至少有两类来源，而**在桥的出口上无法区分**：
   - **(a) 掩码跳过型**：`empty` 样本的 context 指标被项目**主动掩码为 NaN**（`src/cli/eval_scoring.py:86-89`）——不是失败，是"没检索"。**该类天然不在纯 KB 分母里**（`empty` 不属于纯 KB 样本），故无需任何"归因"就被排除。
   - **(b) 非掩码型**（= 指标产出型；合法缺值 **与** 裁判失败**混在一起**）：一方面 ragas 会**合法**返回 NaN（`faithfulness` 在答案**无可校验陈述**时 `return np.nan`，`ragas/metrics/_faithfulness.py:186-192/211`；`answer_relevancy` 在"生成问题全空"时 `score = np.nan`，`_answer_relevance.py:118-127`）；另一方面桥调 `ragas.evaluate(...)` 时**未传 `raise_exceptions`**（`deepeval/metrics/ragas.py:92/170/393/468`），而 ragas 该参数**默认 `False`**（`ragas/evaluation.py:68/344`），`ragas/executor.py:64-84` 对**任何** `Exception` 都 `logger.error(...) → return np.nan` ⇒ **超时 / 429 / 403 / 连接失败 / 输出不可解析**等失败也被吞成 NaN、**桥不抛异常**。
   - **⚠️ 为什么不做 (b) 内部分类（前稿方案已否决，理由经第 5 轮评审证实）**：桥把每个指标压成**一个 `float`**（`deepeval/metrics/ragas.py:92-97` 的 `scores[name][0]`）⇒ 返回值不可判别；而**唯一能拿到的外部证据（请求级 hooks）粒度也不够**——HTTP 200 的 OpenAI envelope **永远是合法 JSON**，主因失败（截断 / 垃圾内容）与"生成问题全空"在 HTTP 层与正常完成**同形**（2xx + `finish_reason=stop`）。故"按 hooks 证据分 (b)/(c)"**不可实现**，SHALL NOT 再设计此类分类。
   - **口径（本 change 的最终口径，简单且可观测）**：
     - **分母** = **纯 KB 样本 × 四项指标**（格数）。实测台账有效纯 KB = **19** 题 ⇒ 分母 **76 格**；`>10%` 即 **>7.6 格 ⇒ ≥8 格**。（**订正**：更早一稿写"21 题下允许 ≤2 题"，与分母单位**不一致**，已按格数重述。）
     - **分子** = **纯 KB 格里的全部 NaN**（(a) 由分母天然排除；合法缺值型与裁判失败型**一并计入**）。
     - **接受的代价（明写，勿当 bug）**：合法缺值型 NaN（典型为 `faithfulness` 无陈述）会被计入分子、并触发一次有界重试（重试对它是**无收益的浪费**，但不是错误）。阈值 10%（76 格允许 7 格）即为吸收该水位而设；首轮全量 SHALL 用实测分布复核该假设（见 D10 / tasks 10.5），若合法缺值型占比明显偏高则上调阈值或改为按指标分列。
   - **连带后果（必须显式处理，否则 ADR-0024 的两条核心保护静默失效）**：
     - ① **重试判据要换**：现 `_score_metric_with_retry`（`src/cli/eval_ragas.py:423-483`）以 `except Exception` 为判据 ⇒ 在桥下**永不触发**，一次瞬时失败即永久 `NaN`、把该题踢出均值（正是 ADR-0024 决策 3 要消除的"乐观偏差 + 分母不齐"）⇒ 重试 SHALL 以 **非掩码 `NaN`** 为判据（有界，`RAGAS_METRIC_MAX_ATTEMPTS`）。**唯一例外：限流类（= HTTP 429 / `openai.RateLimitError`）SHALL NOT 触发外层重试**——ragas 框架层已对该类重试 ≤10 次（`ragas/llms/base.py:113` `add_async_retry` + `run_config.py:52` `max_retries=10`；`base.py:325-335` 把 `exception_types` 设为 `RateLimitError`，openai 只在 **429** 映射该类，`openai/_client.py:706`），外层再叠会 10×3 放大。**403 额度类不属该列**（`_client.py:694` 映射为 `PermissionDeniedError`）⇒ 仍由外层重试。
     - ② **失败留证要换承载点**：`_judge_failure_note` 依赖异常对象上的 `last_completion`（它抓 `finish_reason` / 输出 token / **原始输出片段**，`src/cli/eval_ragas.py:390-420`——ADR-0024 决策 6 称此为"最大教训"），桥下**拿不到异常、也拿不到该字段** ⇒ 失败证据改由 **D13 的请求级日志**承载。**注意**：该日志**只做留证，不再充当分类依据**（分类已取消）。故 hooks SHALL 记**响应体摘要 + `finish_reason`**，SHALL NOT 只记"请求体摘要"。**SHALL NOT** 保留"复用 `_judge_failure_note`"的声明。
     - ③ **观测不可用时的兜底**：若 hooks 完全不可用（例如裁判调用未经我们提供的 client），报告 SHALL **显式标注"请求级日志不可用"**；NaN 率的统计**不依赖** hooks（口径已可纯观测），故门禁仍可判，但失败归因能力下降这一点必须在报告里可见。
   - 设计上要求：
     - (a) 掩码 NaN 与非掩码 NaN SHALL 在报告中**标记不同**；
     - 主分聚合**排除 NaN 行**，并**单独统计非掩码 NaN 计数**（分子/分母见上）；
     - 与既有的 `SAMPLE_CLASS_EMPTY` 掩码语义**区分开**（一个是"没检索"，一个是"指标出不来值"）。

### D5 覆盖与丢失（明写，避免以后误读）

| 现在（0.4.x 现代） | 本次（桥 + 经典四指标） |
|---|---|
| faithfulness / answer_relevancy / context_precision / context_recall | ✅ 同名保留 |
| **answer_correctness** | ❌ **退出**（非经典四指标，本次不做替代） |
| —— | ➕ DeepEval 原生 agentic 族（另加，见 D6） |

- 注意口径变化：① `answer_relevancy` 走 **0.3 温度 + 3 个生成问题**；② `context_precision` **与 0.4.x 同源、等价**——0.3.9 的 `ragas.metrics.context_precision` 就是 `ContextPrecision`，MRO 为 `ContextPrecision → LLMContextPrecisionWithReference`（`ragas/metrics/_context_precision.py:82/311`，`name="llm_context_precision_with_reference"`，必填 `retrieved_contexts` + **`reference`**），判据即"该 chunk 是否有助于得到 reference"，**不是**"仅评 chunk 是否相关 / 评 reranker 排序"（上一稿此处写错，已删）。

### D6 Agent 行为轴（DeepEval 原生；**必须走 `evals_iterator`，分数从 test_run JSON 回读**）

> ⚠️ 本节经 **1 题 spike 实测**改写，**推翻了本决策此前两处写法与一处引用**（见下"作废记录"）。

- 候选：`task_completion` / `step_efficiency` / `plan_adherence` / `plan_quality` / `tool_correctness` / `argument_correctness` / **`agent_loop_detection`**（直接对口已登记的"Agent 打转、重复同一 query"）。
- 选型约束：**指标总数控制在 ≤5**（DeepEval 官方 "Choosing Your Metrics" 建议），先上 2–3 个高价值项，其余按需再加。
- **实测得到的唯一可用形态（两半缺一不可）**：

  ```python
  from deepeval.dataset import EvaluationDataset, Golden
  from deepeval.evaluate.configs import DisplayConfig
  from deepeval.integrations.langchain import CallbackHandler

  ds = EvaluationDataset(goldens=[Golden(input=<题面>, expected_output=<参考答案>)])
  for golden in ds.evals_iterator(
      metrics=[行为指标…], display_config=DisplayConfig(results_folder=<OUT>)
  ):
      graph.ainvoke(..., config={"callbacks": [CallbackHandler()]})   # 只挂 handler、不挂 metrics
  # 逐题分数从 <OUT>/test_run_*.json 的 testCases[j].metricsData[k].score 回读（见下"逐题归属"）
  ```

- **三条实测事实（spike，1 题，2026-10-16）**：
  1. **只挂 `CallbackHandler(metrics=[…])` + invoke ⇒ 拿不到分**：实测 `metric.score = None`、`success = None`、`reason = ''`（callback 只把指标对象挂到 `trace.metrics`，`deepeval/integrations/langchain/callback.py` 内 **零** `measure`/评分调用）。
  2. **必须 `dataset.evals_iterator(metrics=[…])` 驱动**：加上它之后评分才真的发生（实测打印 `Task Completion │ 1.00 │ passed=1`，耗时 30–38s，确系真调裁判，且**无 Confident AI key 也能跑**）。
  3. **分数不在 `metric.score`**：eval loop 用 `copy_metrics()` 的**副本**（`deepeval/evaluate/execute/loop.py` 导入该工具）⇒ 调用方持有的实例恒为 `None`。**分数落盘在 `<OUT>/test_run_*.json`**（顶层键含 `testCases` / `metricsScores` / `testPassed` / `testFailed` / `prompts` / `runDuration`），**必须回读该 JSON**。
- **逐题归属（订正：`metricsScores` 不是逐题分）**：`metricsScores` 是**按指标聚合**的列表（实测落盘物 `/tmp/spike-behaviour-out/test_run_*.json`：`metricsScores[0] = {"metric":"Task Completion","scores":[1.0],"passes":1,"fails":0,"errors":0}`），**只可用于"整体是否取到分"的粗守卫**。**逐题分在 `testCases[j].metricsData[k]`**：实测 `testCases[0]` 含 `input` / `order` / `success` / `toolsCalled` / `trace` / `metricsData`，而 `metricsData[k]` 含 `name` / `score` / `success` / `reason` / `threshold`。⇒ 回写"每题每指标"SHALL 用 `testCases[].metricsData`；`golden ↔ testCase ↔ trace_id` 的映射 SHALL 以 `order`（或 `input` 文本）为依据，并在实现中钉一个用例。
- **生成器未耗尽 = 没有落盘（必做守卫）**：`evals_iterator` 是 **generator**，`test_run_*.json` 只在**生成器被耗尽**时才写。⇒ 循环体抛异常或提前 `break` 会**既不产分也不产 JSON**。故 SHALL：① 驱动循环**跑到耗尽**（不得提前 break）；② 循环结束后断言 JSON 存在且每题都取到该指标的分，否则 **fail-loud**（与"轨迹非空"同级的守卫）。
- **作废记录（三处，勿再沿用）**：
  1. ~~"挂 `CallbackHandler` + invoke 即得行为分"~~ —— **不成立**（实测 `score=None`）。
  2. ~~"`Golden` / `evals_iterator` 在基本路径上不需要"~~ —— **反了**：`evals_iterator` 是**必需**的驱动；`Golden` 由题面 + 参考答案构造即可（`input` 必填、`expected_output` 可选）。
  3. ~~"PoC 的 K6 已实测该形态 WORKS"~~ —— **引用失真**：K6 实际走的是**私有路线**（`trace_testing_manager.test_dict` 捞轨迹 → `tc._trace_dict = …` → **手动 `metric.measure`**），**不是**官方 `evals_iterator` 路线；其 `1.0` 是手动 measure 的产物，**不能**用来论证官方路线成立。
- **⚠️ 静默失败守卫（必做）**：三个行为指标均声明 `requires_trace = True`；其中 **`agent_loop_detection` 在无 trace 时源码直接置 `0.0`**、**不报错**。评测 SHALL 断言"轨迹非空 **且** `test_run` JSON 中确实取到了该指标的分"，否则 **fail-loud**（与 ragas 的 NaN 属**同一类静默假分数**）。
- **`CallbackHandler` 实例作用域（并发安全）**：handler 含实例态（`_trace` / `_run_id_to_span_uuid` 等）⇒ **每题 MUST 新建独立 handler 实例**，SHALL NOT 跨题复用；验收需含"并发 2 题不串 trace"的**负向**验证。
- **费用**：每题多一次裁判调用（spike 1 题 30–38s，含跑图）⇒ 21 题会显著加时，需计入预算。
- **同进程前提**：该形态要求图与 `deepeval` **同环境**；本决策因 D1 的"单环境"而成立（若按早先"双环境"设想则不可行）。
- **异步可行性（已由 Q1 探测定案，2026-10-16；原为待验项）**：官方示例用**同步 `invoke`**、我们用 **`ainvoke`**，且项目把空库检查+跑图放在**一个** `asyncio.run(...)` 内 ⇒ 曾担心 nested-loop 撞车。**实测两路均 WORKS**（1 题、真调 judge、`testCases[].metricsData[].score = 1.0`）：① 默认 `AsyncConfig(run_async=True)` 在外层 `asyncio.run` 内驱动；② `AsyncConfig(run_async=False)`（纯同步驱动）。**机制**：`deepeval/utils.py:209 get_or_create_event_loop()` 在 `loop.is_running()` 时调 **`nest_asyncio.apply()`**（`nest_asyncio` 是 deepeval 硬依赖）⇒ 内层 `run_until_complete` 在**同一个** loop 上重入，**不新建第二个 loop** ⇒ 项目"单事件循环 + 引擎连接池"假设**不破**。证据见 `docs/tmp/deepeval-poc/RESULTS.md` 的「Q1 探测」节与同目录 `q1_probe_evals_iterator_async.py`。
  - **附带注意**：`nest_asyncio.apply()` 是**进程级全局 monkeypatch**；评测 CLI 是短进程可接受，SHALL 在代码注释写明，SHALL NOT 在长驻服务里顺手调用。
  - ⇒ D1 的**回滚触发条件 ②**（"`evals_iterator` 在 `ainvoke` 下不可用"）**不成立**；spec 的行为轴 SHALL 因此**可验收**。

### D7 主分口径与门禁：换尺子 → 阈值必须重标定

- 历史四轮基线（faithfulness=0.7813 / cp=0.5004 等）与本次**不同尺子**，**不得作为对照**。
- 门禁阈值 `faithfulness≥0.85` / `context_precision≥0.80` / `context_recall≥0.70` / `answer_relevancy≥0.85` 全部**待重标定**（重跑首轮后按分布取）。
- 主分聚合仍限 `pure_kb` 样本，但需在 **NaN 与 empty 两种跳过语义**下重写（见 D4）。
- **主分与门禁只看四指标；行为轴不进主分、不进阈值判据**（行为指标 inline 产生、口径与四指标不同，混入会让"分数动了"再次无法归因）。行为指标失败时**单独标记**，不影响该题主分。
- **四指标评分 MUST 串行（并发=1）**：桥复用的是 `ragas.metrics` 的**模块级指标实例**（`from ragas.metrics import context_precision` 等），且 `ragas.evaluate` 会 `metric.llm = llm` 并在结束时 `metrics[i].llm = None` 复位（`ragas/evaluation.py:316`），`LangchainLLMWrapper` 还会改写底层 `chat_model.temperature` 属性 ⇒ **并发会互踩**（读到 `llm=None`、温度被别的指标改掉），表现为**间歇性错分**。
  - 并发预算留给"跑图"那一侧；四指标这一遍串行（21 题 × 4 指标可接受）。
  - 若将来确要并发，**唯一安全形态是进程隔离**，且需单独验证。
  - ⚠️ 与此相关：**`bypass_temperature` 对本路径无效**（见 D3 订正），所以并发下"温度被改掉"连开关都救不了。
- **阈值标定规则 + 空窗期（原缺）**：现 `EVAL_PASS_THRESHOLD=0.70`（`src/config/const.py`）是**旧尺子**下的值。
  - **空窗期行为**：首轮全量**用 `--no-gate` 跑**，只产出分布、不做通过/不通过判定（避免拿旧阈值判新尺子）。
  - **标定规则**：取"纯 KB 且非 NaN 样本分布"的**保守下界**（建议：不低于均值的 90%，且不低于历史同名的 0.9 倍——具体数值在首轮分布出来后定稿并写回单一常量）。
  - 标定完成后 SHALL 同轮复核 `kb_eval` 下发的阈值与落库 `passed` 一致（沿用"阈值单一来源"纪律）。
- **门禁形态：保留自建 `--gate` + 单一阈值常量**（**有意偏离**官方推荐）。官方路径是 `assert_test()` + `pytest` + `deepeval test run`；我们保留自建，理由与代价见 D12。

### D8 出题路：代码保留、调用下线

- `src/cli/eval_ragas_generate.py` **原样保留**，其**出题入口**（`run_generate` / CLI `--generate`）**不再被调用**。
- **订正（上一稿"整个文件不再被调用"不成立）**：该模块内的**测试集加载器** `_load_latest_testset` 与 `_ensure_vertexai_stub` **仍被评测主流程使用**（`src/cli/eval_ragas.py:803/806/818-820`）⇒ **文件不可删**，"全模块未调用"的说法作废。判据：本 change 只下线**出题生成**，**不动**测试集加载。
  - 相关好消息（比 R8 乐观）：0.4.x 的 `default_transforms_for_prechunked` 是 `_build_transforms` 内的**函数级导入**（`eval_ragas_generate.py:307`），故 ragas 0.3.9 下**仅导入该模块不会炸**；会炸的是真正走到出题生成那一步。
- **风险（明写，已收窄）**：出题生成是"保留的死代码"。**重启路径不是"回退 ragas 0.4.x"，而是"前迁到 DeepEval 自带出题器"**——见下。
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
- **边界①：DeepEval 的轨迹（span 树）不会出现在 Langfuse 上**。**理由订正**（原文写"DeepEval 没有 OTLP 导出"是**错的**）：DeepEval **有** OTLP 导出（`deepeval/tracing/otel/`，含 `BatchSpanProcessor(OTLPSpanExporter(...))`），但其 exporter 指向 **Confident AI**；而**自托管 Langfuse v2 不提供 OTLP 端点** ⇒ **接不上**。**结论不变、理由更正。**
  - 交叉证据（spike 实测）：无 Confident AI key 时打印 `[Confident AI Trace Log] No Confident AI API key found. Skipping trace posting.` ⇒ 轨迹的投递目标是 Confident AI，与我们的 v2 无关。
  - ⇒ **分数可回写到 `eval_<hex>` trace，但行为指标的轨迹在 Langfuse 里看不到**；轨迹只能看**报告 / 台账 / 本地日志 / DeepEval 落盘物**（`test_run_*.json` 见 D6）。
- **边界②：不会有两个 handler 打架**——核实：本项目**只**用 `@observe` 装饰器，`graph.ainvoke(...)` **当前未传 `config=`**、未挂任何 LangChain callback ⇒ 加 DeepEval `CallbackHandler` 是**唯一的 callback**。
- **复用现有通道**：沿用 `src/cli/eval_scoring.py:157-203` 的 `write_scores` —— 客户端取 `langfuse_context.client_instance`（**不得** `new Langfuse()`：会绕过 `LANGFUSE_ENABLE` 开关与关停 flush）；逐条 `client.score(trace_id=…, name=…, value=…)`；失败只记 warning、不阻断评测。
- **行为轴的分数也回写**（与四项同级、同一个 `trace_id`）：收益是"一个 trace 上能看到该题的全部口径"，排障不用两地对照。
- **NaN 的 score 语义 = 不写**（维持现状：`pd.isna(value)` → 跳过）。理由：Langfuse v2 的 score 是**数值型**，用 `0` 代 NaN 会把"**裁判坏了**"与"**真得 0 分**"混起来——这正是本次整改一路在纠的那类错；"缺失 + 报告计数"语义干净。**该语义必须在此明写**，否则后人会当 bug 修。
- **新旧口径共存：新 score 名带口径后缀**（如 `faithfulness@bridge-0.3.x`），SHALL NOT 同名覆盖。理由：同名覆盖会让**同一 trace 上旧值消失**、可比性丢失；加后缀可新旧并存，与 D9 台账的"口径版本"列对齐。（清理旧 score 的做法**不采纳**——历史证据要留。）

- **台账与报告**：`docs/agents/ragas-eval-ledger.md` 增列"口径版本"（modern-0.4.x / bridge-0.3.x），历史行**不改**，新行标注口径。报告头记录：DeepEval 版本、ragas 版本、judge 模型、`model=` 是否显式传入、**NaN 计数**、**trace 是否非空**（对应此前已登记的"报告不落盘裁判配置"缺口）。

### D10 逃生门（非默认路径）

- **触发条件**（任一）：**NaN 比例 > 10%**（分母 = 纯 KB 样本 × 四指标；实测 19×4 = **76 格** ⇒ >10% 即 **≥8 格**）且**以 NaN 为判据的有界重试**（见 D4）无效；或默认模型/依赖坑在目标环境无法稳定满足。
  - **阈值取 10% 的理由（经验值，勿当定论）**：依据是"旧尺子（0.4.x 现代栈）下 21 题里出现 1 例"≈4.8%——**样本量 n=1/21 偏薄，且来自换尺子之前**，换桥后 NaN 率未知；且新口径把**合法缺值型 NaN 也计入分子**（D4 的接受的代价），故取 **10%** 既在该单薄证据之上留余量、又吸收合法缺值的水位（2026-10-16 由 5% 调整为 10%）。故 10% 在规格中 SHALL 标为**经验初值 / 待首轮复核**，首轮全量跑出真实 NaN 率后**同轮复核并定稿**（若真实率明显不同，以实测为准改值）。
  - **分子/分母（否则门禁误判）**：**分母 = 纯 KB 样本 × 四指标**（格数；实测 19×4 = 76 格）；**分子 = 纯 KB 格里的全部 NaN**（(a) 掩码类由分母天然排除；合法缺值型与裁判失败型**一并计入**——二者在桥出口不可区分，见 D4）。**单位警告**：不要用"题数"算阈值——`10% × 76 格 = 7.6 ⇒ >7.6 格（即 ≥8 格）才判失败`。
  - **判定机制**：由评测运行**自动判定** NaN 率并在报告中给出「**建议启用逃生门**」提示；**是否真启用由人决定**，启用须在台账记录原因与生效轮次。
- **同一阈值用于门禁**：主分排除 NaN 之后，**NaN 率 ≤10% 视为可接受**（不计失败）；**>10% 则门禁 fail**。这补上了风险 R1 里"NaN 进发布门禁无法判定"那个洞。
- **形态**：DeepEval 只负责**采样与用例构造**（产出 `LLMTestCase` 集合），评分改由**项目侧直接调用 ragas 0.3.x**（legacy `evaluate` 或按需的 `collections`）完成。
- **定位**：**不是常驻架构**，是桥不可用时的迁移/兜底路径；启用需在台账写明原因与生效轮次。

### D11 0.4.x 遗留的处置：**三分——复用 / 删除 / 改写**（不是"删光"）

弃用 0.4.x 不是只改依赖与入口；**为现代栈写的那批代码要逐项落点**，否则会留下悬空引用与失义配置。经全仓扫描，处置如下：

| 遗留物 | 位置 | 处置 | 理由 |
|---|---|---|---|
| `build_ragas_judge()` / `build_ragas_embeddings()` | `src/infra/llm/provider/factory.py:132/169` | **删** | 产出 `InstructorBaseRagasLLM` / 现代 embeddings，桥**类型不接**（桥要 langchain `BaseChatModel` / `Embeddings`） |
| `build_native_client()` | 同上 `:91` | **删（先核引用）** | 为 `llm_factory` / `embedding_factory` 的 `client` 参数而生；无消费方即删 |
| `@experiment()` 编排、`_EvalResult`、`_score_metrics`、`_exc_chain_summary` | `src/cli/eval_ragas.py` | **删/改写** | 桥外层是**同步** `measure()`（内部走 ragas 异步 `single_turn_ascore`，见 D3）；编排改为构造 `LLMTestCase` + 调度量 |
| `_score_metric_with_retry` | 同上 | **改写（判据换成 NaN）** | 桥把一切失败吞成 `NaN`、**不抛异常**（D4）⇒ 原 `except Exception` 判据**永不触发**；SHALL 改为以 `NaN` 为判据的有界重试 |
| `_judge_failure_note` | 同上 | **删** | 它依赖异常对象上的 `last_completion`，桥下拿不到 ⇒ 失败证据改由 D13 的请求级日志承载，SHALL NOT 保留"复用该函数"的声明 |
| `build_metrics()`（现代版） | `src/cli/eval_scoring.py:23-47` | **改写** | 换成桥的四个单项 |
| `RAGAS_JUDGE_MAX_TOKENS` / `RAGAS_JUDGE_THINKING` | `src/config/settings.py:189/192` | **复用（改嫁）** | 改嫁到 **`build_chat("judge", max_tokens=…, thinking=…)`**（`build_chat` 的 `thinking` 经 `thinking_extra_body` 归一化、`**overrides` 透传给 `ChatOpenAI`，见 D3）。**SHALL NOT** 硬写 `extra_body={"enable_thinking": …}`。**依据**：mock server 抓包已证实 `max_tokens` / 思考参数经桥的调用链**透传成功**（`max_completion_tokens: 8192`、`enable_thinking: false`） |
| `RAGAS_HTTP_TIMEOUT_{CONNECT,READ,WRITE,POOL}` / `RAGAS_HTTP_MAX_RETRIES` | 同上 `:168-177` | **复用（承载点换位）** | **细粒度超时保住（承载点是 `http_async_client`，不是 `http_client`——订正）**：ragas 的裁判调用走**异步**（`evaluation.py:240` 提交 `single_turn_ascore`），而 langchain 的 `http_client` **只喂同步 client**（`langchain_openai/chat_models/base.py:806`、异步在 `:1273` 用 `http_async_client`）⇒ 须 `build_chat("judge", http_async_client=httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=150, write=30, pool=10), event_hooks=…), max_retries=0, …)`；超时由该 client **自带**保证（**不要**依赖 ragas 的 `request_timeout` 赋值：它在构造后写 `base.py:337`，而 client 是构造期建的、改不动）。**⚠️ 重试这一维不能声称"完整保住"**：`max_retries=0` 只关掉 **OpenAI SDK** 的重试；**ragas 框架层自带重试**——`ragas/llms/base.py:113` 用 `add_async_retry` 包裹每次生成，`ragas/run_config.py:52` 默认 **`max_retries=10`**、`:89/:110 stop_after_attempt(run_config.max_retries)`，且**桥不暴露 `RunConfig`**（与 `bypass_temperature` "开关存在却无法置真"**同型**）。⇒ **接受该偏离并登记**；防倍增效的口径是：**外层 `RAGAS_METRIC_MAX_ATTEMPTS` SHALL NOT 重试限流类（HTTP 429 / `RateLimitError`）**（该类已由 ragas 重试 ≤10 次 ⇒ 再叠会 10×3 放大），其余失败（超时 / 畸形响应 / 403 额度）才由外层重试 |
| `RAGAS_METRIC_MAX_ATTEMPTS` | 同上 `:183` | **复用（判据改为非掩码 NaN）** | 桥无内置重试；但桥把失败吞成 NaN 不抛（D4）⇒ 重试判据 SHALL 从 `except Exception` 改为 **非掩码 `NaN`**（不做 (b)/(c) 分类，见 D4）；**唯一例外：限流类（HTTP 429 / `RateLimitError`）不触发外层重试**（ragas 框架层已重试该类 ≤10 次），否则乘法放大 |
| `judge call` / `metric retry` 日志事件 | `src/core/log_events.py` + `log_event_specs.py` | **改写** | 埋点位置从"我们调裁判"变成"桥内部调 ragas"；口径要重述 |
| `src/cli/trace_logs.py` | 同上 | **保留并重述适用范围** | 仍可用于按 trace 拉日志，但其"裁判诊断"叙事基础变了 |
| `tests/cli/test_eval_modern_stack.py`、`test_metric_retry.py`、`test_ragas_modern_factories.py`、`test_native_client.py` | `tests/` | **删/重写** | 断言对象已不存在 |
| `tests/infra/llm/provider/test_registry_models.py` | 同上 | **先核消费方再定** | judge 角色绑定若已无消费方需一并处置 |
| **ADR-0024**（RAGAS 裁判 client 政策，含 `temperature=0`） | `docs/adr/0024-…md` | **追加新 ADR 取代** | 仓库约定：ADR 不可变，**正文不改**，但旧 ADR 的 Status 必须加**反向指针**，并更新 ADR 索引表 |

**出题路的例外**：`src/cli/eval_ragas_generate.py` 对 0.4.x 的依赖（`default_transforms_for_prechunked`，**函数级导入**）**按决策保留**（出题生成不再调用）；但**测试集加载器 `_load_latest_testset` / `_ensure_vertexai_stub` 仍被评测主流程使用**（见 D8）⇒ "全仓无 0.4.x 引用"**不成立**——自查以"**评测路径**无悬空引用"为判据，出题生成路单独标注。

### D12 对官方推荐路径的两处**有意偏离**（按 `selection-baseline.md` 的偏离纪律记明）

> 纪律要求："凡有意偏离基线，SHALL 写下偏离理由与代价"。此处基线的上位来源是 **DeepEval 官方推荐**。

| # | 官方路径 | 我们的做法 | 偏离理由 | 接受的代价 |
|---|---|---|---|---|
| 1 | 门禁 = `assert_test()` + `pytest` + `deepeval test run` | 保留自建 `--gate` + 单一阈值常量 | 门禁是**我们的发行口径**，要和台账/落库共用同一套阈值常量；换成 `test run` 需改测试组织形态并动 CI 结构，收益远小于代价（官方亦明说 `evaluate()` **不**过门禁） | 用不上 DeepEval 的 UI 与回归对比能力；阈值维护在我们自己手里 |
| 2 | 并发 = `a_measure()` + `gather`，或 `async_config.max_concurrent` | **四指标评分串行（并发=1）** | **不只是"官方 async 对桥无效"**（桥的 `a_measure` 是 `return self.measure(...)` 伪异步），更是**并发本身不安全**：桥复用 `ragas.metrics` **模块级指标实例**，`evaluate` 会复位 `metrics[i].llm = None`，`LangchainLLMWrapper` 还会改写 `chat_model.temperature`（见 D7）⇒ 并发会**间歇性错分** | 评测更慢（21 题 × 4 指标串行）；并发预算留给"跑图"那侧；若将来要并发，唯一安全形态是**进程隔离**且需单独验证 |

**另记两处"官方无对应"、纯我们自定的口径**（不属偏离，但避免后人误当官方）：

- **NaN 处置**（排除均分 + 单独计数 + 阈值 10% + **不做 (b)/(c) 归因分类**）——官方只解释 NaN 的**来源**，未提供任何处置机制。
- **细粒度超时 / 关 SDK 重试 / 关思考**——超时与 SDK 重试机制属 langchain `ChatOpenAI` 的字段（**`http_async_client`** / `max_retries`），思考开关经 **provider 层 `build_chat(thinking=)`**（`thinking_extra_body` 归一化）；DeepEval 文档全仓未提；这是**我们的既有收益**（ADR-0024），换入口后靠承载点换位保住（超时须落 `http_async_client`，落 `http_client` 不生效）。**注意**：`max_retries=0` 只关 **SDK** 重试，**ragas 框架层自带 `RunConfig.max_retries=10`（限流类）不可配置** ⇒ 该项**不属"完整保住"**，按 D11 的①口径处理。

### D13 框架内部调用的可观测性：把 `http_async_client` 变成"请求级日志出口"

- **问题（trace 覆盖的缺口）**：换桥后，"我们自己发起的裁判调用"仍可带 `trace_id`（沿用现有 `judge call` 事件的形态），但**框架内部的 LLM 调用默认完全不可见**——包括 ragas 内部的重试、`answer_relevancy` 的 3 次生成、以及 DeepEval 行为指标的 judge 调用。**而这些正是我们追了两天的根因所在**（退化输出、超时、NaN 全发生在这一层）。
- **做法**：我们**本来就要**给裁判传 `http_async_client`（细粒度超时，见 D11）；httpx 支持 **event hooks**（`event_hooks={"request": [...], "response": [...]}`）⇒ 在同一个 client 上挂**请求级日志**（模型 / 耗时 / 状态码 / 请求体摘要 / **响应体摘要 + `finish_reason`**），把框架内部调用也纳入 `trace_id` 序列，并与既有工具 `src/cli/trace_logs.py` 配合使用。
  - **⚠️ 必须是 `http_async_client` + `async def` hook（订正，前稿写错）**：ragas 走**异步**路径，而 langchain 的 `http_client` **只喂同步 client**（`langchain_openai/chat_models/base.py:806`；异步用 `:1273` 的 `http_async_client`）⇒ 把 hooks 挂在 `http_client` 上**一条都不会响**。且异步 client 的 hook 是 **`await hook(response)`**（`httpx/_client.py:1697`，同步 client 才是 `hook(response)`，`:982`）⇒ hook 必须 `async def`，读体必须 `await response.aread()`（同步 `read()` 在异步流上抛 `RuntimeError`，`httpx/_models.py:944`）。
  - **响应体必须记（否则本项救不了主因失败）**：主因失败模式是 **HTTP 200 + 畸形响应体**（空结构 / 截断 / 垃圾对象），只记请求体与状态码**看不到它**。故 hooks SHALL 记**响应体摘要 + `finish_reason`**（原 `_judge_failure_note` 正是靠这几样区分"空结构"与"截断"，ADR-0024 决策 6 称此为"最大教训"）。
  - **实测已验（2026-10-16，真 judge）**：A 臂传 `http_async_client` → 响应 hook **4** 次事件（2 请求 + 2 响应，均 `200` / `json_ok` / `finish_reason=['stop']`）；B 臂只传 `http_client` → **0** 次事件，而两臂指标分同为 1.0 ⇒ **挂错字段不报错、只是静默失去全部可观测性与细粒度超时**（这正是它一路未被发现的原因）。证据与脚本见 `docs/tmp/deepeval-poc/RESULTS.md`「Hook 冒烟」节；实施期须把 A>0 / B==0 两条**反向断言**落成用例（tasks 3.3a）。
- **`trace_id` 的传递方式（SHALL 用显式闭包，SHALL NOT 依赖 contextvar）**：即便 `asyncio.to_thread` 会复制 contextvar（`copy_context()`），**ragas `evaluate` 内部还会用自己的 `Executor`（线程池）分发**，那些 worker 线程**不保证**带上我们的 contextvar ⇒ 深处 hooks 可能读到 `trace_id=None`。故 **直接以显式闭包把 `trace_id` 绑定进 event hook**（`async def` 内闭包照常成立），不把它当"待验项"。
  - 附带：四指标评分改**串行**后（D7），我们这一层已无 `to_thread`，但 **ragas 内部 Executor 仍在** ⇒ 显式闭包依然必需。
  - 附带（串行下的安全性）：裁判 client 是**每轮构造一次、跨样本复用**，而 hooks 需按样本归集证据 ⇒ 闭包须绑到一个**可变持有者**（per-sample 证据桶）而非固定 `trace_id`；评分已串行（并发=1，D7），该形态安全。
- **验收范围（订正）**：spec 的"内部调用日志 SHALL 带 trace_id"只覆盖**我们挂 hook 的那一层**（即经我们提供的 `http_async_client` 发出的请求），SHALL NOT 声称覆盖框架内更深、不经该 client 的调用。
- **边界（明写）**：行为指标的 **DeepEval span 树**仍不会进日志（那需要 DeepEval 自身导出，见 D9 边界①）。本决策只解决"**LLM 请求级**"的可见性，**不**解决"轨迹"的可见性。
- **收益**：异常时可用 `trace_id` 直接看到"框架到底发出了什么、返回了什么"——这正是本次整改中"靠 trace 才找到根因"那条经验的制度化。
- **与 D4 联动（这是失败留证的唯一承载点）**：桥把失败吞成 `NaN`、不抛异常（D4），故"这次 NaN 到底为什么"**只能靠本条的请求级日志**（状态码 / `finish_reason` / 耗时 / 响应体摘要）。**注意**：本项**只做留证**，**不再充当 (b)/(c) 分类依据**——该分类已因证据粒度不足而取消（D4）。

## Risks / Trade-offs

| # | 风险 | 说明 | 应对 |
|---|---|---|---|
| R1 | **NaN 进发布门禁无法判定** | 桥原样透传 ragas 的 NaN，门禁里"NaN 算 pass 还是 fail"无意义 | **已收敛**：主分排除 NaN + 单独计数 + **NaN 率 ≤10% 视为可接受、>10% 门禁 fail**（分子 = 纯 KB 格里的**全部** NaN；不做 (b)/(c) 归因分类，见 D4/D10），超限走 D10 逃生门 |
| R2 | **版本耦合不对称** | 桥绑 ragas 版本；ragas 0.3→0.4 就动过 API，桥可能随之断 | 固定 ragas 0.3.x；升级前用"新旧分数对比"验证（桥正好可当一次性的校验工具） |
| R3 | **官方不推荐桥** | DeepEval 明说优先用其原生 RAG 指标 | 明写为"接受的代价"；若未来原生四指标足够，再评估收编 |
| R4 | **生态无生产参考** | GitHub 上除官方（18.9k★）外，第二名仅 173★，agentic 轨迹类最高 19★ | 自担设计风险；先小样本（1 题 → 5 题 → 全量）逐级验证 |
| R5 | **温度不可控** | 三项 0.01、`answer_relevancy` 0.3，且我们改不了 | 明写；`answer_relevancy` 的分数波动按分布看待，不按点值判门禁 |
| R6 | **口径变化被误当"分数回归"** | 换尺子后四个指标的分数都会动 | 台账加口径版本列；首轮重跑只用于**标定阈值**，不与历史比 |
| R7 | **喂法未修，context 轴仍偏低** | 本次不解决"多轮并集当单轮 `c(q)`" | 显式写成 Non-Goal；context 轴的根治（per-step 单轮化）另行跟踪 |
| R8 | **出题生成成为死代码**（注意：模块**不是**死代码） | 出题生成不再调用，但**测试集加载器仍被评测路径使用**（`eval_ragas.py:803/818`），文件不可删 | 在文件头与台账标注"**出题生成**保留未调用"（区分生成入口与加载器）；重启需先解决 0.4.x；`--generate` 加**硬护栏**（见 D8） |
| R9 | **行为轴的取分路径（最高风险；已由 spike 定案并修正）** | 原设想"透传 `messages` 自建轨迹"不被接受；**改成只挂 `CallbackHandler` 后实测仍拿不到分**（`metric.score=None`、`reason=''`） | **定案**：必须 `EvaluationDataset.evals_iterator(metrics=[…])` 驱动；**逐题分从 `test_run_*.json` 的 `testCases[].metricsData` 回读**（`metricsScores` 只是按指标聚合，见 D6）；加"轨迹非空 **且**取到分 **且** 生成器已耗尽（JSON 已落盘）"fail-loud；`ainvoke`（异步）兼容性待验 |
| R10 | **行为轴对被测图有侵入，且每题多一次裁判调用** | 评测运行时图会多挂一层 handler；每题多一次裁判调用（spike 1 题 30–38s，含跑图） | 仅在**评测路径**做（生产链路不动）；**每题新建独立 handler**（并发负向验证）；时间成本计入评测预算 |
