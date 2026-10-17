## MODIFIED Requirements

### Requirement: Judge client is constructed with an explicit sampling and timeout policy

裁判 SHALL 以**显式**参数构造，且**模型与端点 SHALL 显式指定**——SHALL NOT 依赖 DeepEval RAGAS 桥的内建默认模型（默认为 `gpt-3.5-turbo`，该模型已停服）。**可核验方式**：审阅每个桥度量构造处的实参，`model=` SHALL 非空。

裁判采样温度 SHALL 接受由 ragas 按 `n` 分档决定的取值：`n=1` 的三个指标（faithfulness / context_precision / context_recall）为 **0.01**；`answer_relevancy`（`strictness` 默认 3，即 `n=3`）为 **0.3**。该取值**不由本项目控制**（ragas 的 `LangchainLLMWrapper` 会改写底层模型的 temperature 属性），本项目 SHALL NOT 以配置声称"温度为 0"。

裁判 SHALL **经项目既有 provider 层构造**（`build_chat("judge", …)`，其 `**overrides` 透传给 `ChatOpenAI`），SHALL NOT 在 `src/` 内直写 `ChatOpenAI` 并硬编码端点 / 密钥。**可核验方式**：审阅裁判构造处，provider 解析（`judge` 角色 → `RAGAS_PROVIDER` / `RAGAS_LLM_MODEL`）SHALL 生效，且无硬编码 base_url / api_key。

**裁判的 HTTP client SHALL 经 `http_async_client` 传入**（含细粒度超时与请求级 hooks），SHALL NOT 只传 `http_client`——ragas 的裁判调用走**异步**，而 langchain 的 `http_client` **只喂同步 client**（`langchain_openai/chat_models/base.py:806`；异步用 `:1273`）⇒ 只传同步字段会让超时与 hooks 双双失效。**可核验方式**：审阅构造实参含 `http_async_client=`。

**思考开关 SHALL 经 `thinking=` 传入**（由 `thinking_extra_body` 按 provider 归一化，`src/infra/llm/provider/thinking.py:8`），SHALL NOT 硬写 provider 专有键（如 `extra_body={"enable_thinking": …}`）——硬写会在非 dashscope 判官上静默失效或 400。

裁判构造 SHALL NOT 传入 `temperature`（温度由 ragas 决定，见下）；报告 SHALL NOT 出现"裁判 temperature=0"的表述。

本项目 SHALL NOT 依赖 ragas 的 `bypass_temperature` 开关来"拿回温度"。**理由（订正；上一稿"桥走同步路径所以开关无效"是错的）**：检查语句确实在 **`agenerate_text`（异步，`ragas/llms/base.py:269` 起，判断 `:281`）**、同步 `generate_text`（`:217` 起）不检查；但桥的 `measure()` 虽是同步 API，内部 `ragas.evaluate` 经 `asyncio.run(_async_wrapper())`（`evaluation.py:433/456`）提交 **异步** `metric.single_turn_ascore`（`:240`）⇒ **确实走到异步路径**，开关**会被检查**。拿不回温度 0 的真正原因是：`ragas.evaluate` 用 `LangchainLLMWrapper(llm, run_config=…)`（`evaluation.py:151`）重新包装裁判，`bypass_temperature` 默认 `False` 且**桥不暴露该参数**。**可核验方式**：读 `evaluation.py:151/240` 与 `base.py:281`。

温度分档的**事实与不可控性** SHALL 记入报告元数据与评测台账，使跨轮比对者可知该项差异。

裁判 SHALL 配置显式请求超时，取值 SHALL 足以覆盖慢速推理模型。

裁判构造 SHALL NOT 传入 `top_p`（部分模型对 `top_p` 与采样参数组合以 400 拒绝）。**（原条款保留）**

裁判模型选型 SHALL 以「结构化输出可靠性」为判据，即 SHALL 有对候选裁判的**活体抽样验证**（覆盖裁判实际使用的**每个指标**），而非仅以「请求未报错」为准。**（原条款保留：判官输出退化 → `NaN` 正是本 change 的核心痛点，该验证义务不因换入口而消失）**

理由（记明，避免后人误判）：0.01 是有意的工程折中——无限接近确定性，同时避开部分供应商对 `temperature=0` 的特殊逻辑；`answer_relevancy` 取 0.3 是因为该指标需要"生成多个多样化问题再取均值"，压到近 0 会使指标退化。

#### Scenario: Sampling parameters are explicit

- **WHEN** 构造裁判客户端
- **THEN** 构造 SHALL NOT 传 `temperature`（温度由 ragas 决定，见上）、且 SHALL NOT 传 `top_p`
  （**订正**：原场景 THEN 为"SHALL 传 `temperature=0`"，与本次口径冲突 ⇒ 场景随口径一并改写，SHALL NOT 静默删除。）

#### Scenario: Every bridge metric is constructed with an explicit model

- **WHEN** 构造任一桥度量（`RAGAS*Metric`）
- **THEN** 构造实参 SHALL 显式传入 `model=`，SHALL NOT 走 `gpt-3.5-turbo` 默认值

#### Scenario: Temperature regime is recorded, not claimed as zero

- **WHEN** 产出一次评测报告
- **THEN** 报告元数据与台账 SHALL 记录"温度由 ragas 分档（0.01 / 0.3）、本项目不可控"，SHALL NOT 出现"裁判 temperature=0"的表述

#### Scenario: Judge is built through the project provider layer

- **WHEN** 审阅裁判构造处
- **THEN** SHALL 经 `build_chat("judge", …)`（或等效 provider 层入口）构造，且 `src/` 内 SHALL NOT 出现硬编码的裁判端点 / 密钥

#### Scenario: Judge client is bound on the async side

- **WHEN** 审阅裁判构造实参
- **THEN** SHALL 含 `http_async_client=`（`httpx.AsyncClient`，带 `httpx.Timeout` 与 event hooks），SHALL NOT 只传 `http_client`

#### Scenario: Judge selection is backed by live evidence

- **WHEN** 为 `judge` 角色选定模型
- **THEN** 该模型 SHALL 已通过覆盖**全部裁判指标**的活体抽样验证，且验证结果记入设计文档

### Requirement: Metric failures are explicit, not silently dropped

评测 SHALL 对每条样本独立执行各指标，任一指标失败（超时、额度、解析失败等）时 SHALL 在原样结果行上记录错误信息。

评测 SHALL NOT 因单个指标失败而丢弃该样本行；SHALL NOT 以 `NaN` 静默代替失败（须与「正常计算出的值」可区分）。

**桥特有条款（`NaN` 只分「掩码」与「非掩码」；**SHALL NOT 设计可归因的子分类**）**：

- **(a) 掩码跳过型**：`empty` 样本的 context 指标被项目主动掩码为 `NaN`（`src/cli/eval_scoring.py:86-89`）。**天然不在纯 KB 分母里**（`empty` 不属于纯 KB 样本）⇒ 无需任何归因即被排除。
- **(b) 非掩码型（=「指标产出型」：合法缺值 **与** 裁判失败混在一起，出口不可区分）**：一方面 ragas 会**合法**返回 `NaN`（`faithfulness` 在答案无可校验陈述时，`ragas/metrics/_faithfulness.py:186-192/211`；`answer_relevancy` 在"生成问题全空"时，`_answer_relevance.py:118-127`）；另一方面桥调 `ragas.evaluate(...)` 时**未传 `raise_exceptions`**、ragas 该参数**默认 `False`**（`ragas/evaluation.py:68/344`），`ragas/executor.py:64-84` 对**任何** `Exception` 都 `return np.nan` ⇒ **超时 / 429 / 403 / 连接失败 / 输出不可解析**等失败也被吞成 `NaN`，且**桥不向上抛异常**。

**SHALL NOT 依据请求级日志把 (b) 再切成"合法缺值"与"裁判失败"（前稿方案已否决）**：桥把每个指标压成**一个 `float`**（`deepeval/metrics/ragas.py:92-97` 的 `scores[name][0]`），返回值不可判别；而唯一的旁证——HTTP 200 的 OpenAI envelope——**永远是合法 JSON**，主因失败（截断 / 垃圾内容）与"生成问题全空"在 HTTP 层与正常完成**同形**（2xx + `finish_reason=stop`）⇒ 该分类**不可实现**，做了只会把主因失败误判为"合法缺值"而放过（正是本次整改要根除的错）。

因此评测 SHALL：① 让 `NaN` 显式可见、且**掩码 / 非掩码在报告中标记不同**（SHALL NOT 静默计入均分）；② **单独统计非掩码 `NaN` 计数**并在报告中呈现；③ **以「非掩码 `NaN`」为判据执行有界重试**（`RAGAS_METRIC_MAX_ATTEMPTS` 保留；判据 SHALL NOT 沿用 `except Exception` —— 桥下它永不触发）；**唯一例外：限流类（= HTTP 429 / `openai.RateLimitError`）SHALL NOT 触发外层重试**（ragas 框架层已对该类重试 ≤10 次，见「RAGAS dependency is pinned to the 0.3 line」的复用条款，外层再叠会 10×3 放大；429 可由 hooks 的状态码判定；**403 额度类不属该列**，`openai/_client.py:694/706`，仍由外层重试）；④ 失败留证 SHALL 由**框架内部请求级日志**承载；**当前落地**记录请求状态与响应可解析性（`status` / `body_bytes` / `json_ok` / `finish_reason`），**仅失败时产日志行**；**目标形态**另含响应体摘要（主因失败是 HTTP 200 + 畸形响应体；未落地项见「Framework-internal LLM calls are observable by trace id」条的「落地现状」）；**该日志只做留证，SHALL NOT 被当作分类依据**；⑤ 若该日志完全不可用，报告 SHALL **显式标注"请求级日志不可用"**（NaN 率统计不依赖它，故门禁仍可判，但归因能力下降须可见）。

**接受的代价（明写，勿当 bug）**：合法缺值型 `NaN`（典型为 `faithfulness` 无陈述）会被计入分子、并触发一次有界重试（对该情形是**无收益的浪费**，不是错误）。阈值 10%（76 格允许 7 格）即为吸收该水位而设；**v6 定稿后的首轮全量**跑出真实分布后 SHALL 复核该假设并定稿（v5 分布仅作基线，不用于标定，见 design D8）。

`NaN`（指标出不来值）与样本类别 `empty`（未检索到材料）SHALL 被区分为**两种不同的跳过语义**。

#### Scenario: A failed metric stays visible on its row

- **WHEN** 某样本的某个指标调用失败
- **THEN** 该样本行 SHALL 仍出现在报告中，且 SHALL 携带该失败的错误信息

#### Scenario: No row is silently dropped

- **WHEN** 评测 N 条样本
- **THEN** 报告 SHALL 含 N 行（不因失败少行）

#### Scenario: A bridge NaN stays visible and is counted

- **WHEN** 某纯 KB 样本的某个桥度量返回 `NaN`
- **THEN** 该行 SHALL 携带该失败信息，该非掩码 `NaN` SHALL 被计数，且该值 SHALL NOT 参与主分均值

#### Scenario: Masked NaN is excluded by the denominator

- **WHEN** 某 `empty` 样本的 context 指标被掩码为 `NaN`
- **THEN** 该格 SHALL NOT 计入 NaN 率分子/分母（`empty` 不属于纯 KB），且 SHALL 在报告中以**掩码**语义标记（与非掩码 NaN 不同）

#### Scenario: No sub-classification of non-masked NaN is attempted

- **WHEN** 审阅实现
- **THEN** SHALL NOT 出现"按请求级日志把 NaN 分为合法缺值与裁判失败"的逻辑（该分类不可实现，见上）；非掩码 `NaN` SHALL 一律计入分子并按③重试（429 除外）

#### Scenario: Judge failure and empty sample are distinguishable

#### Scenario: Judge failure and empty sample are distinguishable

- **WHEN** 一行因指标出不了值（非掩码 `NaN`）、另一行因未检索到材料被掩码
- **THEN** 两者 SHALL 以不同的标记出现在报告中

#### Scenario: Retry is keyed on the non-masked NaN

- **WHEN** 某题某指标首次返回非掩码 `NaN`（且不是限流类 429）
- **THEN** 评测 SHALL 按 `RAGAS_METRIC_MAX_ATTEMPTS` 重试该指标（判据为非掩码 `NaN`），SHALL NOT 因"没有异常"而跳过重试

#### Scenario: Rate-limit failure is not retried by the outer layer

- **WHEN** 某格的失败证据是 HTTP 429（∈ `openai.RateLimitError`）
- **THEN** 外层 SHALL NOT 重试该格（ragas 框架层已重试该类 ≤10 次），SHALL NOT 形成 10×3 放大

### Requirement: Evaluation captures retrieval material with source kind

评测链路 SHALL 为每次评测的单题生成注入请求上下文，使被测 Agent 经工具（知识库检索 / 联网搜索）收集的检索材料能被评测读取，而不再走「评测入口从不设置上下文」的失效路径。

每条被读取的检索材料 SHALL 携带其来源类型（知识库 / 联网）。评测 SHALL 仅让**纯知识库样本**（检索材料不含联网块）参与常规上下文指标（主分）；**含联网材料的样本** SHALL 被标记为「降级联网」并从主分中排除，且其是否降级联网 SHALL 记入诊断元数据。

依赖检索材料的上下文类指标（`faithfulness` / `context_precision` / `context_recall`）SHALL 在无材料样本上被跳过；不依赖上下文的指标（**仅 `answer_relevancy`**）SHALL 照常计算。**订正**：原条款写"`answer_relevancy` 与答案正确性 SHALL 照常计算"——`answer_correctness` 本次已退出评测（见 REMOVED Requirements），故删去该提及，避免归档后留下无法满足的 SHALL。

#### Scenario: Retrieval material is captured

- **WHEN** 评测一次绑定知识库的单轮问答
- **THEN** 评测侧读到的该问答检索材料非空，且逐条带来源类型

#### Scenario: Pure knowledge-base sample contributes to main score

- **WHEN** 某问答的检索材料全部来自知识库（不含联网块）
- **THEN** 该题参与常规上下文指标，其结果计入主分聚合

#### Scenario: Web-bearing sample excluded from main score

- **WHEN** 某问答的检索材料含联网块
- **THEN** 该题 SHALL 被标记为「降级联网」，其上下文指标 SHALL NOT 计入主分聚合，且该标记 SHALL 记入诊断元数据

#### Scenario: Samples without retrieval skip context metrics

- **WHEN** 某问答未检索到任何材料（上下文为空）
- **THEN** 依赖材料的 `faithfulness` / `context_precision` / `context_recall` SHALL 被跳过并**显式标记为 `skipped`**（与"计算失败"区分），跳过值置空为 `NaN`（不计入均值，且不得为 `None` / 空串）
- **AND** 不依赖上下文的 `answer_relevancy` SHALL 照常计算

#### Scenario: Sample composition and retrieval coverage reported

- **WHEN** 一次评测完成
- **THEN** 报告 SHALL 列出「纯 KB / 含 web / 无检索」三类样本计数与检索覆盖率（有检索样本占比）

#### Scenario: Gate uses the main score

- **WHEN** 以 `--gate` 运行评测
- **THEN** 门禁 SHALL 依据主分（纯 KB 样本的指标均值）判定，而非全量样本均值

#### Scenario: Gate fails on low coverage or empty main score

- **WHEN** 检索覆盖率低于阈值，或主分样本数为 0（无纯 KB 样本）
- **THEN** 门禁 SHALL 判定失败

> **保留说明**：上面两条门禁场景原属本 requirement（主规格 `:96` / `:101`），本次**行为不变**——MODIFIED 块 SHALL 原样保留，SHALL NOT 因"本次只改指标口径"而静默丢弃。

### Requirement: Agent trajectory captured as diagnostic metadata

评测 SHALL 采集被测 Agent 的完整消息轨迹（含每次工具调用及其入参、迭代次数），作为诊断元数据随评测结果保存，供低分样本下钻。

工具调用序列 SHALL NOT 作为**主分 / 门禁**的评分指标 —— 在常规检索轮场景其期望路径近似恒定，不具区分度。

**例外（本次新增，避免与新行为轴冲突）**：DeepEval 原生**行为指标**（`TaskCompletion` / `StepEfficiency` / `ToolCorrectness` / `AgentLoopDetection` 等）**可以**对轨迹（含工具调用序列）评分，但 SHALL 仅作**独立观测**——SHALL NOT 进入 `overall_score`、SHALL NOT 参与门禁判据（见「Agent behaviour is evaluated with DeepEval native metrics」）。原条款"SHALL NOT 作为评分指标"在归档后**不得**被读成"禁止行为轴"。

#### Scenario: Trajectory available for drill-down

- **WHEN** 某样本的评测得分偏低
- **THEN** 可从评测结果读到该样本的工具调用序列与迭代次数

#### Scenario: Behaviour metrics may score the trajectory without entering the gate

- **WHEN** 行为指标对工具调用序列打分
- **THEN** 该分 SHALL 作为独立观测呈现，SHALL NOT 进入 `overall_score` 或门禁

### Requirement: Reported overall score uses the main-score 口径

`eval_report` 的 `overall_score` SHALL 采用与报告主分一致的口径：**纯 KB 掩码后、且排除 `NaN` 行的四项指标均值**（`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`）。

`answer_correctness` SHALL 不再参与 `overall_score`，且 `kb_eval` API SHALL NOT 再暴露该指标。

判定「通过」的阈值 SHALL 由**单一常量**提供 —— SHALL NOT 在评测落库与前端各硬编码一份。阈值取值 SHALL 在 **v6 定稿后**重新标定（换尺子后历史阈值作废；v5 分布仅作基线，见 design D8）。

跨口径分数 SHALL NOT 被直接比较：报告与台账 SHALL 标注「口径版本」，历史（modern-0.4.x）与本次（bridge-0.3.x）的分数不得作为回归判据。

**行为轴不进主分、不进门禁**：`overall_score` 与门禁判据 SHALL 只由上述**四指标**构成；行为指标为**独立观测**，其失败 SHALL 单独标记、SHALL NOT 影响该题主分。

**阈值标定规则与空窗期**：阈值 SHALL 在 **v6 定稿后**重新标定，且 SHALL 写死规则与空窗期行为——① **空窗期**：首次全量评测 SHALL 以 `--no-gate` 运行，只产出分布、不作通过/不通过判定（SHALL NOT 拿旧尺子的 `EVAL_PASS_THRESHOLD` 判新尺子）；② **标定**：取"纯 KB 且非 NaN 样本分布"的**保守下界**（建议不低于均值 ×0.9 且不低于历史同名阈值 ×0.9，具体在 **v6 分布**出来后定稿；v5 分布不用于标定，见 design D8）；③ 标定后 SHALL 同轮复核落库 `passed` 与 API 下发阈值同源。

**`answer_correctness` 退出的消费方**：SHALL 逐项处置——`kb_eval` API 响应字段、`RAGAS_METRIC_COLUMNS` 常量与相关注释、报告/stdout 的均值行与断言、受影响的测试、**契约文档 `docs/agents/api_contract.md`（逐处：`:733` 示例响应、`:744` 口径说明、`:909` `overall_score` 定义、`:912` `detail_json` 说明、`:1353` 综合分、`:1379` 指标列、`:1400` `avg`——CLAUDE.md 要求改响应结构须同步该文档）**、**`docs/agents/ragas-eval-ledger.md` 表头**。**台账改法（避免破坏历史行）**：表头 SHALL **新增「口径版本」列**；`answer_correctness` 列 SHALL **保留**（历史行不动，新行该列留空 / `N/A`）——SHALL NOT 删列（删列会让表头列数与历史行单元格数不等、Markdown 表破损，见 design D9「历史行不改」）。**前端不受影响**（已核：`deploy/nginx/html/index.html` 只读 `faithfulness` / `context_recall` / `overall_score` / `passed` / `pass_threshold`，不读 `answer_correctness`）。

**delta 边界（防归档后自相矛盾）**：主规格中独立存在的「Answer correctness metric」要求（`docs/openspec/specs/evaluation-pipeline/spec.md:106`）SHALL 由本 delta 的 `REMOVED Requirements` 一并移除；SHALL NOT 只移除 `overall_score` 相关条款而留下"评测 SHALL 输出每题答案正确性分数"的悬空要求。

#### Scenario: Overall score excludes answer_correctness and NaN rows

- **WHEN** 一次评测产出一行 `eval_report`
- **THEN** 其 `overall_score` SHALL 等于该次**纯 KB 且非 NaN** 样本的四项指标均值

#### Scenario: Evaluation API no longer exposes answer_correctness

- **WHEN** 前端经 `kb_eval` API 取最新评测报告
- **THEN** 响应 SHALL NOT 含 `answer_correctness`

#### Scenario: Pass threshold is defined once

- **WHEN** 评测落库判定 `passed`、前端渲染通过/不通过
- **THEN** 两者 SHALL 引用同一阈值常量

#### Scenario: Thresholds are recalibrated, not inherited

- **WHEN** **v6 定稿后**的全量分布产出（v5 分布仅作基线记录、**不用于标定**，见 design D8 的 v6 接手点）
- **THEN** 门禁阈值 SHALL 按该分布重新取值，且报告/台账 SHALL 标注口径版本与标定轮次

### Requirement: QA test pair coverage

评测测试集 SHALL 仍然**按知识库**从该库文档生成（而非维护一份固定全局 QA 集），且评测 SHALL 默认载入该知识库**最新版本**的测试集。**但本次 `--generate` 的 CLI 驱动路径 SHALL 显式失败**（原因：出题链依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`，而本次评测侧统一下移到 `0.3.9`），SHALL 给出前迁路径（DeepEval `Synthesizer`）而非裸 `ImportError`。

**口径（明写，避免与主规格既有条款冲突）**：主规格原条款要求"`--generate --size N` SHALL 写出 N 条"，本 delta 将其**改为"显式失败 + 指引"**；出题**能力**（代码）保留，但**不经 CLI 提供**，待后续 change 迁移到 DeepEval `Synthesizer`（见「Testset generation exercises every query path...」的 v6 接手点）。

#### Scenario: Generate a test set for a knowledge base

- **WHEN** 用户运行 `python -m src.cli.eval_ragas --kb-id <uuid> --generate --size 20`
- **THEN** CLI SHALL 以**明确错误信息退出**（说明依赖版本原因与**前迁路径**），SHALL NOT 写出测试集文件、SHALL NOT 抛裸 `ImportError`

#### Scenario: Evaluate against the latest test set

- **WHEN** 用户不带 `--generate` 运行评测
- **THEN** 系统 SHALL 载入该知识库最新的既有版本测试集（本次为 `testset_<kb>_v5.json`）

### Requirement: Evaluation retrieval mirrors production scope

白名单（`RAGAS_DOC_WHITELIST`）SHALL 仅约束**出题**的输入文档范围。评估侧检索 SHALL 镜像生产口径——在被测 Agent 绑定的**整个知识库**内检索，SHALL NOT 人为按出题白名单收窄检索范围。

理由（记明，避免后人"修回"）：① 检索由题面驱动（`retrieval.search(query, kb_id, …)`），自足题的自然检索即应命中对应文档；② 检索命中白名单外文档是**切块/检索质量的可观测信号**，过滤会掩盖它；③ 人为收窄会让评测测一个**生产里不存在**的系统，并可能使 `context_precision` 虚高。

**本次口径澄清（避免与"出题入口下线"冲突）**：出题**生成入口**在本次已不可达（CLI `--generate` 显式失败，见「QA test pair coverage」）；下述"出题"场景描述的是**保留代码在被显式运行时**应满足的行为，SHALL NOT 被读成"`--generate` 必须可用"。

#### Scenario: Evaluation retrieval is not artificially scoped

- **WHEN** 白名单含 2 份文档、知识库共 4 份文档，且运行评测
- **THEN** 评估侧检索 SHALL 在知识库全部 4 份文档内进行（与生产一致），SHALL NOT 仅限白名单的 2 份

#### Scenario: Whitelist still bounds testset generation

- **WHEN** 出题生成代码被显式运行（本次 CLI 不可达，属保留能力）
- **THEN** 出题 SHALL 仅使用白名单文档

### Requirement: Testset generation exercises every query path and yields self-contained questions

本要求描述的出题能力（官方 `default_transforms` 全量链 + 源头锚定 + 出题后自足性门）SHALL **继续作为仓库内既有能力保留**（代码与实现 SHALL NOT 被删除）。

该出题**生成**路径 SHALL NOT 被评测流程调用：评测入口 SHALL NOT 触发测试集生成，测试集 SHALL 以既有版本文件为输入。

**边界（订正：模块内并非全部"不可达"）**：`src/cli/eval_ragas_generate.py` 的**测试集加载器**（`_load_latest_testset`）与 **VertexAI stub**（`_ensure_vertexai_stub`）**仍被评测主流程使用**（`src/cli/eval_ragas.py:803/806/818-820`）⇒ SHALL NOT 删除该模块，SHALL NOT 声称"整个模块不可达"。只有**出题生成入口**（`run_generate` / CLI `--generate`）不可达。

理由（记明，避免后人误判）：出题链依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`；本次评测侧统一下移到 ragas 0.3.9，出题生成因此**暂不可用**（该符号在 `eval_ragas_generate.py:307` 是**函数级导入**，故仅导入模块不会炸）。保留代码是为了不丢失已验证的能力与经验。

**保留的验证口径（原条款，**SHALL NOT 静默丢弃**；适用于保留代码被显式运行时 / v6 迁移后）**：

- 出题 SHALL 使用官方 `default_transforms` 全量转换链（含 `ThemesExtractor` 与 `CosineSimilarityBuilder`），使 `MultiHopAbstractQuerySynthesizer` 可正常运行，SHALL NOT 因缺件被静默丢弃。
- 出题 SHALL 通过两条**可核验**机制保证题面自足——明确指出公司名称与报告期，SHALL NOT 使用「本报告期」「该公司」等依赖原文的指代：① **源头锚定**：送入出题 prompt 的每段上下文 SHALL 携带其所属文档的公司名与报告期（**可核验方式**：读样本的 `reference_contexts`，其首行 SHALL 含公司名与报告期）；② **出题后自足性门**：生成的每条题面 SHALL 经自足性校验，不自足者 SHALL 被改写或丢弃（**可核验方式**：统计题面命中 KB 内公司名的比例，SHALL 为 100%）。
- 这些验证在**出题入口本次不可达**期间不执行，但要求**仍然生效**；恢复可用（v6 或临时 0.4.x 环境）后 SHALL 按原口径核验。

**测试集来源**：本次评测的输入 SHALL 为**固定的既有版本文件**（`testset_<kb>_v5.json`，21 条）；评测与出题 SHALL 保持解耦——固定测试集是跨轮比对的前提。

**前迁路径（登记，不在本次范围）**：出题的**首选前迁目标**是 DeepEval 自带的 `deepeval/synthesizer/`（`generate_goldens_from_docs(...) -> List[Golden]`），因其产出物字段（`input` / `expected_output` / `retrieval_context`）与评测输入**同构**、且与本次评测**同栈**；SHALL NOT 把"回退 ragas 0.4.x"当作首选路径。

**v6 接手点**：出题迁移 SHALL 由**后续独立 change** 承接，入口条件 = ① 本 change 已归档，且 v5 的**新尺子分布**已记录（**不要求阈值已标定**——标定推迟到 v6 之后，见 design D8）；② 台账已记入 `bridge-0.3.9 / v5`；③ v6 与 v5 **并存**。验收 SHALL 分两步且**不可颠倒**：先 `v5 + 新尺子`（基线**与台账行**），再 `v6 + 新尺子`（换卷子对照）**并在该步之后标定阈值**——否则"换尺子"与"换卷子"两个变量无法归因。

#### Scenario: Evaluation does not trigger testset generation

- **WHEN** 运行评测
- **THEN** SHALL NOT 发生测试集生成调用，输入 SHALL 为既有版本文件

#### Scenario: Generation is blocked with a forward path in the message

- **WHEN** 用户运行 `--generate`
- **THEN** CLI SHALL 以明确错误信息退出，给出原因与**前迁路径**（DeepEval `Synthesizer`）

#### Scenario: Generation entry is retained but unreachable, loader stays live

- **WHEN** 审阅仓库
- **THEN** 出题**生成实现** SHALL 仍存在（含其自足性门与锚点逻辑），且 SHALL 在文件头/台账标注"**出题生成**保留未调用"；同时**测试集加载器 SHALL 仍被评测路径使用**（二者 SHALL 在标注中区分，不得写成"整个模块不再被调用"）

#### Scenario: Abstract synthesizer is no longer silently dropped

- **WHEN** 出题生成被显式运行（本次 CLI 不可达，属保留能力 / v6 迁移后）
- **THEN** SHALL NOT 出现 `Skipping multi_hop_abstract_query_synthesizer … Cannot form clusters`，且生成的测试集 SHALL 含 abstract 出题器产出的题

#### Scenario: Generation context carries the entity anchor

- **WHEN** 分块元数据含公司名与报告期（或多公司语料的任意一块）
- **THEN** 送入出题 prompt 的上下文 SHALL 携带该公司的名称与报告期

#### Scenario: Generated questions are self-contained

- **WHEN** 知识库含多家公司文档并生成测试集
- **THEN** 每条题面 SHALL 能唯一指向某公司与其报告期（不含依赖原文的指代）

## REMOVED Requirements

### Requirement: Evaluation execution uses the modern RAGAS stack

**Reason**：范式反转。该要求规定评测执行 SHALL 使用 RAGAS 现代栈（`@experiment` + `ragas.metrics.collections` + `llm_factory`/`embedding_factory`），并 SHALL NOT 使用 `evaluate()` / legacy 指标 / `Langchain*Wrapper`。本次决策与之**完全相反**：评测入口统一到 DeepEval，RAGAS 指标改由 DeepEval 的内建桥（其内部正是调用 legacy `ragas.metrics` + langchain chat model）承载。此外该要求隐含的 `temperature=0` 承诺在桥下不可实现（见本节 MODIFIED 的裁判条款）。

**Migration**：由本节 ADDED 的「Evaluation scoring entry is DeepEval, with RAGAS metrics reached through its bridge」与「RAGAS dependency is pinned to the 0.3 line」承接；五项指标的承诺由 MODIFIED 的「Reported overall score uses the main-score 口径」收敛为**四项**。

### Requirement: Answer correctness metric

**Reason**：范式变更后本项**退出评测**。`answer_correctness` **不属于** RAGAS 经典四指标（`context_precision` / `context_recall` / `answer_relevancy` / `faithfulness`），本次不再提供替代；其原实现基于 0.4.x 现代栈（`evaluate()` 路径），随现代栈一并弃用。**注意**：仅靠 MODIFIED 的「Reported overall score」条款移除会**只覆盖 `overall_score` 口径**，本要求（"评测 SHALL 输出每题答案正确性分数"）会残留在主规格中，与"`kb_eval` API SHALL NOT 暴露 `answer_correctness`"直接冲突 ⇒ 必须在此显式 REMOVE。

**Migration**：由本节的「Reported overall score uses the main-score 口径」（四项指标 + 消费方处置清单）承接。历史 `answer_correctness` 分数**不再作为基线**（换尺子）；`detail_json` 中不再产生该键；**前端不受影响**（不读该字段）。

## ADDED Requirements

### Requirement: Evaluation scoring entry is DeepEval, with RAGAS metrics reached through its bridge

评测打分的入口 SHALL 统一为 DeepEval：RAGAS 指标 SHALL 经 DeepEval 内建的 RAGAS 桥（`deepeval/metrics/ragas.py`）调用，指标集 SHALL 为**经典四项**：`RAGASContextualPrecisionMetric`、`RAGASContextualRecallMetric`、`RAGASAnswerRelevancyMetric`、`RAGASFaithfulnessMetric`。

评测 SHALL NOT 使用聚合指标 `RagasMetric`（其实现与官方文档不一致：源码为五项算术平均、含 `ContextualEntitiesRecall`，而文档写四项），SHALL NOT 使用 `RAGASContextualEntitiesRecall`。

每个桥度量 SHALL 以一条单轮样本构造：`input`（问题）/ `actual_output`（回答）/ `expected_output`（参考答案）/ `retrieval_context`（检索材料）。

`RAGASAnswerRelevancyMetric` SHALL 传入显式的 langchain `Embeddings`（其为四项中唯一需要 embedding 的指标）。

**四指标评分 SHALL 串行执行（并发=1）**。理由：桥复用的是 `ragas.metrics` 的**模块级指标实例**，而 `ragas.evaluate` 会在执行期把 `metrics[i].llm` 置为该次裁判、结束时复位为 `None`，`LangchainLLMWrapper` 还会**改写底层 `chat_model.temperature` 属性** ⇒ 并发会互踩，表现为**间歇性错分**（读不到 `llm`、或温度被别的指标改掉）。并发预算 SHALL 留给"跑图"那一侧；若将来确要并发，唯一安全形态是**进程隔离**且 SHALL 单独验证。

#### Scenario: Scoring is serial

- **WHEN** 审阅四指标评分实现
- **THEN** 同一时刻 SHALL 只有一条评分在跑（串行/并发=1），SHALL NOT 对同一套桥度量并发调用

#### Scenario: Scoring goes through the bridge with four classic metrics

- **WHEN** 对绑定知识库的样本运行评测
- **THEN** 报告 SHALL 含 `faithfulness` / `answer_relevancy` / `context_precision` / `context_recall` 四项，且四项 SHALL 由桥度量产出

#### Scenario: Aggregate bridge metric is not used

- **WHEN** 审阅评测代码
- **THEN** SHALL NOT 出现对 `RagasMetric` 或 `RAGASContextualEntitiesRecall` 的引用

### Requirement: RAGAS dependency is pinned to the 0.3 line

项目依赖的 `ragas` SHALL 固定为 **`0.3.9`**（精确 pin，与仓库既有 pin 纪律一致）；评测 SHALL NOT 依赖 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment` 等现代栈接口。

`pyproject.toml` SHALL 显式记录为满足该组合所需的 `click` 与 `huggingface_hub` 版本下限，SHALL NOT 让其回落到与 DeepEval 互斥的一侧。

理由（记明，避免后人误判）：**本下移是为"统一单环境"而主动做的**，不是被依赖死锁逼迫。订正说明：原文"`deepeval → click<8.4.0` 与 `ragas 0.4.3 → … → click>=8.4.2` 交集为空"的断言**不成立**——原证据是循环论证（dry-run 时显式 pin 了 `click==8.4.2`），而 wheel METADATA 显示 `datasets 4.0.0` 只要求 `huggingface-hub>=0.24.0`、`huggingface_hub 0.24.0` **不依赖 click** ⇒ **存在满足全部约束的赋值**。真实现象是：**在项目完整 pin 集下 pip 无法在合理时间内解析出该组合**（独立评审实测 90s / 150s 两次 dry-run 超时 `exit 124`）。下移到 `0.3.9` 后可解析（实测落 `click-8.3.3` / `huggingface_hub-1.16.1`）。

**回滚**：依赖下移 SHALL 有明确回滚路径——**恢复 `ragas==0.4.3` + `datasets==4.0.0` 的 pin，并同时放弃 `deepeval` 与行为轴**。触发条件（任一）：① 桥的 NaN 率 >10% 且逃生门不可行；② 行为轴的 `evals_iterator` 取分路径不可用且无替代（**该条已由 2026-10-16 探测否定**，见 design D6）。触发时 SHALL 在台账记录原因与回到的口径版本。SHALL NOT 采用"只回滚 ragas、保留 deepeval"的中间态（那正是本次下移要避免的"装不上"组合）。

#### Scenario: Modern stack symbols are absent from the evaluation path

- **WHEN** 审阅评测代码
- **THEN** SHALL NOT 出现 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment` 的引用

#### Scenario: Dependency set resolves

- **WHEN** 按 `pyproject.toml` 重建环境
- **THEN** 依赖解析 SHALL 成功（`pip check` 相对**既有脏基线**无**新增**冲突），且 `ragas` 版本 SHALL 等于 `0.3.9`

#### Scenario: Rollback path is documented

- **WHEN** 审阅本 change
- **THEN** SHALL 能找到依赖回滚的步骤与触发条件（见上）

### Requirement: Agent behaviour is evaluated with DeepEval native metrics

评测 SHALL 在上下文四指标之外，产出 **Agent 行为轴**的指标，取自 DeepEval 原生族（如 `TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`）。选用的数量 SHALL 收敛（总计 ≤5）；**具体清单在实施期定稿**（设计只给候选；SHALL NOT 出现"设计定稿"与"实施期定稿"互相推诿的悬空）。

**取分机制（已实测，两半缺一不可）**：评测 SHALL 以 `EvaluationDataset(goldens=[Golden(input=<题面>, expected_output=<参考答案>)])` 的 **`evals_iterator(metrics=[…], display_config=DisplayConfig(results_folder=…))`** 驱动，并在其循环体内以 `graph.ainvoke(..., config={"callbacks": [CallbackHandler()]})` 执行被测图。

评测 SHALL 从 **`<results_folder>/test_run_*.json`** 回读行为分数，**逐题分 SHALL 取 `testCases[j].metricsData[k].score`**（`metricsData[k]` 含 `name` / `score` / `success`）；SHALL NOT 以 `metricsScores` 作为**逐题**分来源——实测 `metricsScores` 是**按指标聚合**（`{"metric":…,"scores":[…],"passes":…}`），仅可用于"整体是否取到分"的粗守卫。`golden ↔ testCase ↔ trace_id` 的映射 SHALL 以 `order`（或 `input` 文本）为依据。SHALL NOT 以"读取调用方持有的 `metric` 实例的 `.score`"作为取分方式——**实测该值恒为 `None`**（eval loop 使用 `copy_metrics()` 的副本）。

**生成器必须耗尽（否则无分、无 JSON）**：`evals_iterator` 是 **generator**，`test_run_*.json` 只在**生成器耗尽**时才写。评测 SHALL 把循环**跑到耗尽**（SHALL NOT 提前 `break`），并在循环结束后断言 JSON 已落盘；循环体抛异常或提前结束导致的"无 JSON" SHALL 触发 fail-loud。

**异步可行性（已实测，2026-10-16）**：项目把跑图放在**单个** `asyncio.run` 内、循环体用 `graph.ainvoke`。实测在 `asyncio.run` 内驱动 `evals_iterator`（默认 `run_async=True`，或 `AsyncConfig(run_async=False)`）**两路均可用**（1 题、真调 judge、逐题分 = 1.0）。机制：`deepeval/utils.py:209 get_or_create_event_loop()` 在 `loop.is_running()` 时调 **`nest_asyncio.apply()`**，使内层 `run_until_complete` 在**同一个** loop 上重入、**不新建第二个事件循环** ⇒ "单事件循环 + 引擎连接池"假设**不破**。`nest_asyncio.apply()` 是**进程级 monkeypatch**，评测 CLI（短进程）可接受，SHALL NOT 在长驻服务里调用（SHALL 在代码注释写明）。证据：`docs/tmp/deepeval-poc/RESULTS.md`「Q1 探测」节。

评测 SHALL 为**每题新建独立的 `CallbackHandler` 实例**，SHALL NOT 跨题复用（handler 含实例态，复用会串 trace）。

评测 SHALL 断言「轨迹非空 **且** 已从 `test_run` JSON 取到该指标的分」；任一不成立时 SHALL 显式失败（fail-loud），SHALL NOT 让行为指标静默返回 `0.0`。

理由（记明，避免后人"修回"）：① 三个行为指标均声明 `requires_trace = True`，且 **`AgentLoopDetection` 在无 trace 时源码直接置 `0.0`、不报错**（与 ragas 的 `NaN` 同属**静默假分数**）；② 只挂 `CallbackHandler` 而**不**驱动 `evals_iterator` 时**完全不评分**（实测 `.score is None`、`reason=''`）；③ 分数落在 DeepEval 的 eval loop 内部（`copy_metrics()` 副本），**唯一可编程回读口是 `test_run_*.json`**；④ `evals_iterator` 是 generator，**只有耗尽才落盘 JSON** ⇒ 提前结束 = 无分。

#### Scenario: Behaviour metrics appear in the report

- **WHEN** 运行一次评测
- **THEN** 报告 SHALL 含至少一项 DeepEval 原生行为指标

#### Scenario: Behaviour scoring is driven by evals_iterator

- **WHEN** 评测执行行为轴
- **THEN** 被测图的每次执行 SHALL 处于 `dataset.evals_iterator(metrics=[…])` 的循环体内，且逐题分 SHALL 经 `test_run_*.json` 的 `testCases[].metricsData` 回读

#### Scenario: An exhausted generator leaves a readable score file

- **WHEN** 行为轴循环结束
- **THEN** `test_run_*.json` SHALL 已落盘；若循环因异常/提前结束而**未耗尽**、文件缺失，评测 SHALL 显式失败，SHALL NOT 静默返回无行为分

#### Scenario: Behaviour axis works inside the project's single asyncio.run

- **WHEN** 评测在单个 `asyncio.run` 内、循环体以 `graph.ainvoke` 驱动 `evals_iterator`
- **THEN** 行为轴 SHALL 正常产出分数（不禁用、不降级），且 SHALL NOT 新建第二个事件循环

#### Scenario: Score is not read from the caller's metric instance

- **WHEN** 审阅取分实现
- **THEN** SHALL NOT 以 `metric.score` 作为取分来源

#### Scenario: Each question gets its own handler instance

- **WHEN** 连续评测两题
- **THEN** 两题 SHALL 使用各自独立的 `CallbackHandler` 实例

#### Scenario: Missing trajectory fails loud

- **WHEN** 行为指标所需轨迹为空，或 `test_run` JSON 中取不到该指标的分
- **THEN** 评测 SHALL 显式失败并给出原因，SHALL NOT 输出 `0.0` 作为有效分数

### Requirement: A bounded escape hatch exists when the bridge is unusable

当桥不可用时，评测 SHALL 具备一条**有界**的替代路径。本轮**只要求预留可替换边界**：

- 评测 SHALL 把「**采样/用例构造**」与「**评分**」解耦到可替换的边界，桥为默认实现；
- 替代实现（由 DeepEval 采样 + 项目侧直调 ragas 0.3 线评分）SHALL **在首次启用逃生门时**作为前置工作实现并冒烟；该前置 SHALL 登记在台账与需求池。**本轮不实现**（避免出现"规格要求实现、任务只做边界"的错配）。

该路径 SHALL NOT 作为默认路径常驻；每次启用 SHALL 在评测台账记录**原因与生效轮次**。

触发条件 SHALL 至少包含：桥产出 `NaN` 的比例超过设定阈值且重试无效；或桥在当前环境无法稳定运行。

#### Scenario: Escape hatch activation is recorded

- **WHEN** 因桥不可用而启用替代路径
- **THEN** 台账 SHALL 记录启用原因、生效轮次与该轮的口径说明

#### Scenario: Escape hatch is not the default

- **WHEN** 桥可用
- **THEN** 评测 SHALL NOT 走替代路径

### Requirement: 0.4.x-era evaluation artifacts are disposed of explicitly

弃用 0.4.x 时，为现代栈而写的代码与配置 SHALL 被**逐项处置**（复用 / 删除 / 改写），SHALL NOT 留下悬空引用或失义配置。

- **复用**：`RAGAS_JUDGE_MAX_TOKENS` / `RAGAS_JUDGE_THINKING` SHALL 改嫁到 **provider 层 `build_chat("judge", max_tokens=…, thinking=…)`**（`thinking` 经 `thinking_extra_body` 按 provider 归一化），SHALL NOT 硬写 `extra_body={"enable_thinking": …}` 等 provider 专有键；`RAGAS_METRIC_MAX_ATTEMPTS` SHALL 保留（桥无内置重试，判据见「Metric failures」）；`RAGAS_HTTP_TIMEOUT_{CONNECT,READ,WRITE,POOL}` 与 `RAGAS_HTTP_MAX_RETRIES` SHALL 经 **`http_async_client`（`httpx.AsyncClient(timeout=httpx.Timeout(...))`）+ `max_retries=0`** 保留——**SHALL NOT 只传 `http_client`**（ragas 走异步，同步字段不生效，见「Judge client…」条款），SHALL NOT 因换入口而退化为单值超时或重新打开 **SDK** 重试。
  - **细粒度超时的有效性说明**：超时由该 `AsyncClient` **自带**保证；**SHALL NOT 依赖** ragas 的 `request_timeout` 赋值——它在**构造之后**写 `self.langchain_llm.request_timeout`（`ragas/llms/base.py:337`），而 langchain 的 client 在**构造期**建好（`validate_environment` 是 `@model_validator(mode="after")`，`chat_models/base.py:1140/1154`）⇒ 该赋值对已建 client **无效**。
  - **⚠️ 重试维度的边界（订正，SHALL 如实记录）**：`max_retries=0` **只关掉 OpenAI SDK 的重试**；**ragas 框架层自带重试**——`ragas/llms/base.py:113` 用 `add_async_retry` 包裹每次生成，`ragas/run_config.py:52` 默认 `max_retries=10`（`:89/:110 stop_after_attempt`），且其重试**只针对 `openai.RateLimitError`（= HTTP 429）**（`ragas/llms/base.py:325-335` 的 `set_run_config` 把 `exception_types` 设为 `RateLimitError`）；**桥不暴露 `RunConfig`**（与 `bypass_temperature` "开关存在却无法置真"**同型**）。⇒ SHALL **登记为接受的偏离**，SHALL NOT 在本规格或设计里声称"重试维度已完整保住"；防倍增效的口径见「Metric failures」③（**429 / `RateLimitError` 不触发外层重试**，其余失败类别仍由外层重试）。
- **删除**：`build_ragas_judge()` / `build_ragas_embeddings()` SHALL 被删除；`build_native_client()` 在确认无其他消费方后 SHALL 被删除。
- **改写**：`@experiment()` 编排与 `src/cli/eval_scoring.py` 的现代 `build_metrics()` SHALL 被替换；`judge call` / `metric retry` 日志事件的口径 SHALL 随埋点位置变化而重述。
- **ADR**：本次推翻既有裁判 client 政策（含 `temperature=0`），SHALL **追加新 ADR** 并在旧 ADR 的 Status 加**反向指针**（ADR 正文不可变），并更新 ADR 索引表。

**判据边界**：自查 SHALL 以「**最终态**（change 完成后）无悬空引用」为准，SHALL NOT 要求每个中间提交都无悬空引用——删除动作 SHALL 与"调用点改写 + 受影响测试"**同一提交**（否则 TDD 小步的提交点会红）。出题路对 0.4.x 的依赖按决策**保留**，SHALL 单独标注，不计入本条的违反。

#### Scenario: No dangling references on the evaluation path

- **WHEN** 审阅评测路径代码
- **THEN** SHALL NOT 出现对已删函数（`build_ragas_judge` / `build_ragas_embeddings` / `build_native_client`）的悬空引用

#### Scenario: Reused settings are re-attached to the bridge judge

- **WHEN** 审阅裁判构造处
- **THEN** token 上限与思考开关 SHALL 以 `ChatOpenAI` 参数形式落到桥的裁判上

#### Scenario: Superseded ADR gets a reverse pointer

- **WHEN** 审阅 `docs/adr/0024-*.md`
- **THEN** 其 Status SHALL 含指向新 ADR 的反向指针，且新 ADR SHALL 出现在 ADR 索引中

### Requirement: Results are written back to self-hosted Langfuse v2 through the project's own channel

评测 SHALL NOT 假定 DeepEval 提供 Langfuse 集成（官方 Integrations 清单不含 Langfuse；Langfuse 侧那份配方用的是 v3/v4 API；DeepEval tracing 亦无 OTLP/Langfuse 导出）。回写 SHALL 由**项目自建通道**完成。

回写 SHALL 复用现有实现：客户端取 `langfuse_context.client_instance`（SHALL NOT `new Langfuse()` —— 会绕过 `LANGFUSE_ENABLE` 开关与关停 flush）；逐条 `client.score(trace_id=…, name=…, value=…)`；失败 SHALL 只记 warning、SHALL NOT 阻断评测主流程。

回写 SHALL 覆盖**行为轴指标**，与四项同级并共用同一 `trace_id`。

**通道与算分框架解耦**：该通道的三个部件（`configure_tracing` 开关、`eval_<hex>` 的 trace id、`write_scores` 回写）**均属本项目**，SHALL NOT 依赖任何评测框架（RAGAS 或 DeepEval）提供；换评测框架 SHALL NOT 要求重建通道。

**边界（明写，避免误期待）**：DeepEval 的轨迹（span 树）SHALL NOT 被期望出现在 Langfuse 上——DeepEval **没有 Langfuse 导出**，其轨迹只在本进程内存或 Confident AI。行为指标的轨迹 SHALL 以**报告 / 台账 / 本地日志**为准。

#### Scenario: DeepEval trajectory is not expected on Langfuse

- **WHEN** 审阅行为指标的观测落点
- **THEN** SHALL 以报告/台账/本地日志为轨迹来源，SHALL NOT 以 Langfuse 为轨迹来源

`NaN` SHALL **不写入** Langfuse（维持"缺失"语义）；SHALL NOT 以 `0` 代替 NaN —— 否则会把"**裁判故障**"与"**真得 0 分**"混同，正是本次整改一路在纠的错。

新口径 score 名 SHALL 带**口径后缀**（如 `faithfulness@bridge-0.3.x`），SHALL NOT 与历史口径同名覆盖（同名覆盖会让同一 trace 上旧值消失、可比性丢失）。

#### Scenario: Scores go back through the project channel

- **WHEN** 一次评测产出分数且观测开关为真
- **THEN** 每题每个指标 SHALL 经 `client.score(trace_id=…)` 回写，行为轴指标 SHALL 一并回写

#### Scenario: NaN is not written as zero

- **WHEN** 某题某指标为 `NaN`
- **THEN** Langfuse 上 SHALL NOT 出现该 score，且 SHALL NOT 出现 `0`

#### Scenario: New score names carry the caliber suffix

- **WHEN** 回写新口径分数
- **THEN** score 名 SHALL 含口径后缀，历史同名 score SHALL NOT 被覆盖

### Requirement: NaN rate gates the evaluation and triggers the escape hatch

评测 SHALL 统计 **NaN 率**并在报告中单列。门禁判据 SHALL 为：**NaN 率 ≤ 阈值 视为可接受**（不因 NaN 判失败）；**> 阈值 则门禁失败**。**阈值 SHALL 标为"经验初值 10% / 待 v6 定稿后的全量复核"**：现有依据仅为换尺子**之前**的 21 题中 1 例（≈4.8%，样本量 n=1/21 偏薄），取 10% 是为在单薄证据之上留出余量；**v6 定稿后的首轮全量**跑出真实 NaN 率后 SHALL 复核并定稿（v5 分布仅作基线，不用于标定，见 design D8），若实测明显不同 SHALL 以实测改值。

**分子/分母 SHALL 精确定义（单位是"格"，不是"题"——否则门禁误判）**：

- **分母** = **纯 KB 样本 × 四项指标**（格数）。实测有效纯 KB = **19** 题 ⇒ 分母 **76 格**；`>10%` 即 **>7.6 格 ⇒ ≥8 格**判失败。（**订正**：更早一稿写"21 题下允许 ≤2 题"，与分母定义**单位不一致**，差约 4 倍，已按格数重述。）
- **分子** = **纯 KB 格里的全部 `NaN`**。(a) 掩码类**由分母天然排除**（`empty` 不属于纯 KB）；合法缺值型与裁判失败型**一并计入**——二者在桥出口**不可区分**，`SHALL NOT` 依赖请求级日志做子分类（见「Metric failures are explicit, not silently dropped」）。
- **接受的代价**：合法缺值型 `NaN` 会被计入分子并触发一次无收益的有界重试；阈值 10% 即为吸收该水位而设，**v6 定稿后的首轮全量**跑出真实 NaN 率后 SHALL 复核并定稿（v5 分布仅作基线，不用于标定，见 design D8）。

NaN 率超阈值时，报告 SHALL 给出「**建议启用逃生门**」的提示；**是否启用由人决定**，启用 SHALL 在台账记录原因与生效轮次。

理由（记明，避免后人"修回"）：阈值过严会让门禁常态失败、过松则失去意义；此条补上风险 R1 里"NaN 进发布门禁无法判定"那个洞。**但依据单薄这一点必须写进规格**，避免后人把"10%"当作经实测标定的数字。**且 `NaN` 的来源不止一种**（掩码 / 合法缺值 / 裁判失败）⇒ 口径按上述"格"定义执行；掩码与非掩码 SHALL 在报告中可区分，**重试 SHALL 对全部非掩码 `NaN` 触发**（429 类除外，见「Metric failures are explicit」）。

#### Scenario: NaN rate within threshold does not fail the gate

- **WHEN** 全量评测的 NaN 率为 3%（≤10%）
- **THEN** 门禁 SHALL NOT 因 NaN 判失败，且报告 SHALL 单列该 NaN 率

#### Scenario: NaN rate above threshold fails the gate

- **WHEN** NaN 率为 15%（>10%）
- **THEN** 门禁 SHALL 失败，且报告 SHALL 给出「建议启用逃生门」的提示

### Requirement: DeepEval runtime environment variables are set explicitly

评测运行环境 SHALL 显式设置 `DEEPEVAL_DISABLE_DOTENV=1`（DeepEval **import 时即自动加载** `.env` / `.env.local` 并写入 process env，会污染本项目配置解析）与 `DEEPEVAL_TELEMETRY_OPT_OUT=1`（否则 `posthog` 遥测开启）。两者均为官方文档/源码明文。

#### Scenario: DeepEval does not load the project dotenv

- **WHEN** 在含 `.env` 的工作目录下运行评测
- **THEN** DeepEval SHALL NOT 把该文件的值注入 process env

#### Scenario: Telemetry is opted out

- **WHEN** 运行评测
- **THEN** `posthog` 遥测 SHALL 处于关闭状态

### Requirement: The testset generation path is guarded against accidental invocation

出题实现 SHALL 保留，但评测流程 SHALL NOT 调用它。CLI 的 `--generate` 路径 SHALL **显式失败**并给出指引（说明该路径依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`，而当前依赖线为 0.3.9），SHALL NOT 让用户撞到裸 `ImportError`。

#### Scenario: Generating a test set fails with guidance

- **WHEN** 用户运行 `--generate`
- **THEN** CLI SHALL 以明确错误信息退出，并说明原因与前置条件

### Requirement: Deviations from DeepEval's recommended practices are recorded

本次对官方推荐路径的两处**有意偏离** SHALL 被记录（见 design D12）：

1. **门禁**：保留自建 `--gate` + 单一阈值常量，而非官方的 `assert_test()` + `pytest` + `deepeval test run`；
2. **并发**：四指标评分**串行（并发=1）**，而非官方的 `a_measure()` + `gather` / `async_config.max_concurrent`。理由**不只是**"桥的 `a_measure` 是伪异步"（`return self.measure(...)`），更是**并发本身不安全**——桥复用 `ragas.metrics` 的模块级指标实例、`evaluate` 会复位 `metric.llm`、`LangchainLLMWrapper` 会改写 `chat_model.temperature` ⇒ **间歇性错分**（见 design D7）。

每条偏离 SHALL 写明「官方路径 / 我们的做法 / 理由 / 接受的代价」。

#### Scenario: Deviations are documented with rationale

- **WHEN** 审阅 design
- **THEN** 两处偏离 SHALL 各含"官方路径 / 我们的做法 / 理由 / 代价"四项，且 SHALL NOT 留悬空

### Requirement: Framework-internal LLM calls are observable by trace id

评测 SHALL 使**框架内部的 LLM 调用**（ragas 桥内部的重试、`answer_relevancy` 的多次生成）在日志中可按该题的 `trace_id` 追溯。

**行为轴判官调用的归因边界（明写，避免误期待；design R9）**：DeepEval 行为指标在
`evals_iterator` **耗尽之后**才评分（异步路径），届时循环上下文已结束 ⇒ 其判官调用
**不**经 `http_async_client` 的 hooks 做逐题 `trace_id` 归因（该层 `trace_id` 记为空）；
行为轴的逐题失败证据 SHALL 由其 `test_run_*.json` 的 `testCases[].metricsData[].reason`
承载，并在报告「Agent 行为轴」段呈现。本条对行为轴**只**要求"证据可从 JSON 获得"，
SHALL NOT 读成"行为轴判官调用也须带逐题 trace_id"。

实现手段 SHALL 为：在裁判的 **`http_async_client`**（`httpx.AsyncClient`）上挂**请求级日志**（httpx event hooks）。**当前落地记录** `url` / `status` / `body_bytes` / `json_ok` / `finish_reason`（响应体不可解析时另有 `body_exc`），**仅失败时产日志行**；**目标形态**另含模型 / 耗时 / 请求体摘要 / 响应体摘要（见本条「落地现状」）。**SHALL 挂在 `http_async_client` 而非 `http_client`**——ragas 的裁判调用走**异步**，而 langchain 的 `http_client` **只喂同步 client**（`langchain_openai/chat_models/base.py:806`；异步用 `:1273` 的 `http_async_client`）⇒ 挂错字段 hooks **一条都不会响**。该 client 本已因细粒度超时而传入（见 D11 与「0.4.x-era evaluation artifacts」的复用条款），故 SHALL 复用它、SHALL NOT 为此另建客户端。

**hook SHALL 为 `async def` 且以 `await response.aread()` 读体**：异步 client 调用 hook 的形式是 `await hook(response)`（`httpx/_client.py:1697`；同步 client 才是 `hook(response)`，`:982`），传同步 hook 会抛 `TypeError`；同步 `read()` 在异步流上抛 `RuntimeError`（`httpx/_models.py:944`）。

**响应体必须记**：主因失败是 HTTP 200 + 畸形响应体，只记请求与状态码会**看不到它**（原 `_judge_failure_note` 正是靠 `finish_reason` / 输出片段区分"空结构"与"截断"）（目标形态；当前落地范围见下）。

**落地现状（2026-10-17，避免归档后留下未满足的 SHALL）**：当前 hooks 只记录
`url` / `status` / `body_bytes` / `json_ok` / `finish_reason`（响应体不可解析时另有 `body_exc`），
且**仅在失败时产日志行**；模型与耗时分别由 `eval model init` 与四指标路径的 `judge call` 承载。
**请求体摘要 / 响应体摘要 / 成功路径日志尚未落地**（见 design D13「落地现状」与 ADR-0026 决策 6、
复查触发条件 ④）。⚠️ 因此「A malformed 200 response is diagnosable from the logs」**当前不成立**：
可解析但内容畸形的 200 响应不产任何日志行（`json_ok=True`、无 warning），其证据只在
`HookEvidence` 内存桶（不落盘）⇒ 该场景的诊断能力随请求体/响应体摘要一并属**未落地项**。

**该日志只做留证**：SHALL NOT 被当作 `NaN` 子分类的依据（该分类不可实现，见「Metric failures are explicit, not silently dropped」）。若该日志不可用，报告 SHALL 显式标注。

`trace_id` SHALL 经**显式闭包**绑定进 event hook（`async def` 内闭包照常成立），SHALL NOT 依赖 contextvar 传递。**理由**：即便 `asyncio.to_thread` 会复制 contextvar，**ragas `evaluate` 内部还会用自己的 `Executor`（线程池）**分发，其 worker 线程**不保证**携带我们的 contextvar ⇒ 深处 hooks 可能读到 `trace_id=None`。**且**裁判 client 是每轮构造一次、跨样本复用 ⇒ 闭包 SHALL 绑到**可变证据桶**而非固定 `trace_id`（评分已串行，并发=1，形态安全）。

评测 SHALL NOT 以"框架内部调用不可见"为可接受状态——本次整改的根因（退化输出、超时、NaN）正发生在该层。

**验收范围（明写）**：本条只覆盖**经我们提供的 `http_async_client` 发出的 LLM 请求**；SHALL NOT 声称覆盖框架内更深、不经该 client 的调用。行为指标的 **DeepEval span 树**仍不进日志（见 design D9 边界①）。

#### Scenario: Internal calls carry the question's trace id

- **WHEN** 某题的裁判调用在框架内部发生（含重试与多次生成）
- **THEN** 对应日志行 SHALL 携带该题的 `trace_id`

#### Scenario: The logging hook rides on the async client used for the timeout

- **WHEN** 审阅裁判客户端构造处
- **THEN** 请求级日志 SHALL 挂在用于细粒度超时的**同一个 `http_async_client`**（`httpx.AsyncClient`）上，且 hook SHALL 为 `async def`；SHALL NOT 只挂在 `http_client`（同步路径，ragas 走异步、hooks 不会触发）

#### Scenario: Hook wiring is proven by a smoke assertion (both directions)

- **WHEN** 实施期跑 1 题真调裁判的桥度量（`RAGASFaithfulnessMetric` 等）
- **THEN** SHALL 断言 ① 响应 hook 触发 **> 0** 次、且 hook 内 `await response.aread()` + `json.loads` 成功并能取到 `finish_reason`；**且** SHALL 有一条**负向**断言——只传 `http_client`（同步字段）时响应 hook 触发 **== 0**，把"挂错字段就零触发"钉成回归防线。**实测基线（2026-10-16，真 judge）**：A 臂（`http_async_client`）**4** 事件（2 请求 + 2 响应，均 `200` / `json_ok` / `finish_reason=['stop']`），B 臂（仅 `http_client`）**0** 事件，两臂指标分均 1.0 —— 即错误接线**不报错、只是静默失去全部可观测性与细粒度超时**。证据见 `docs/tmp/deepeval-poc/RESULTS.md`「Hook 冒烟」节。

#### Scenario: A malformed 200 response is diagnosable from the logs（目标形态；当前未满足，见「落地现状」）

- **WHEN** 裁判返回 HTTP 200 但响应体畸形（空结构 / 截断）
- **THEN** 请求级日志 SHALL 含**响应体摘要与 `finish_reason`**，使该失败模式可从日志定位（SHALL NOT 只记请求体 / 状态码）；**该日志仅用于定位**，SHALL NOT 被用来把该 `NaN` 归成"合法缺值"
