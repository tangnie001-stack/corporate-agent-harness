# evaluation-pipeline Specification

## Purpose
TBD - created by archiving change mvp-core-features. Update Purpose after archive.
## Requirements
### Requirement: QA test pair coverage

评测测试集 SHALL 仍然**按知识库**从该库文档生成（而非维护一份固定全局 QA 集），且评测 SHALL 默认载入该知识库**最新版本**的测试集。**但本次 `--generate` 的 CLI 驱动路径 SHALL 显式失败**（原因：出题已迁移到 DeepEval `Synthesizer`），SHALL 给出前迁路径而非裸 `ImportError`。

#### Scenario: Evaluation loads a fixed-version testset

- **WHEN** 运行评测
- **THEN** 输入 SHALL 为既有 `testset_<kb>_v<N>.json`，且不触发出题生成

### Requirement: Evaluation report archival

All evaluation results SHALL be saved under the configured report directory (`EVAL_REPORT_DIR`, default `data/deepeval/reports/`) as a timestamped CSV file `deepeval_eval_<timestamp>.csv` together with a sibling Markdown summary `deepeval_eval_<timestamp>.md`.

Evaluation SHALL be invoked via `python -m src.cli.eval_deepeval`. The Markdown summary SHALL list the date and configuration, the QA pair count, per-question metric scores, and aggregate metric averages.

When `--deepeval-only` is set, only the Markdown summary SHALL be written (no CSV), and it SHALL NOT contain four-metric rows or main-score lines.

Historical `eval_report.report_path` rows pointing at the retired `data/ragas/` directory SHALL NOT be rewritten; the field has no consumer. The runbook and the evaluation ledger SHALL note that those historical paths are stale.

#### Scenario: Evaluation saves CSV and Markdown

- **WHEN** the user runs `python -m src.cli.eval_deepeval --kb-id <uuid>`
- **THEN** a CSV file and a Markdown summary SHALL be created under the configured report directory

#### Scenario: Historical report paths are not migrated

- **WHEN** the data directory is renamed to `data/deepeval/`
- **THEN** historical `eval_report.report_path` values SHALL remain unchanged, and the runbook/ledger SHALL state they are stale and have no consumer

#### Scenario: DeepEval-only writes Markdown only

- **WHEN** the user runs `python -m src.cli.eval_deepeval --kb-id <uuid> --deepeval-only`
- **THEN** only the Markdown summary SHALL be written; no CSV SHALL be created

### Requirement: Evaluation passes without crash on empty KB
The evaluation system SHALL handle the case where the knowledge base has no documents gracefully, logging a clear error message and exiting with a non-zero exit code.

#### Scenario: Empty KB evaluation
- **WHEN** running evaluation against a KB with no documents
- **THEN** the system SHALL log "Knowledge base is empty" and exit with code 1

### Requirement: NLI context uses shared entity rendering

The evaluation SHALL use `RAGContext.to_prompt_text()` (the same rendering as production) for the context shown to the native `Faithfulness` judge, so entity metadata (company / report_period / sec_code / person / currency / report_type) present in chunk metadata SHALL be visible to it.

#### Scenario: Faithfulness context includes time anchor

- **WHEN** a table chunk has `entities={company: 东软集团, report_period: 2025年第一季度}` and the statement references "2025年第一季度"
- **THEN** the judge's context SHALL include the `report_period` entity, giving the statement an anchor to verify against

#### Scenario: Evaluation output unchanged for empty entities

- **WHEN** evaluation runs against a KB whose chunks have no entity metadata
- **THEN** the context SHALL match the pre-change format, and the evaluation SHALL still produce a report without crashing

### Requirement: Current date in evaluation context

system prompt SHALL 在**生产路径与评估路径**下都包含当前日期（例："今天是 2026年8月9日"）。

日期注入 SHALL 由 system prompt 组装器的**唯一注入点**承担：`_with_current_date`，在段拼装完成、产出 system 消息之前应用；按 `Asia/Shanghai` 计算，且幂等（日期行已存在时不重复追加）。SHALL NOT 由 `PromptManager` 另行追加 —— 经典 RAG 与 agent 两条路径经同一组装入口（见 `<prompt-composition>`「经典 RAG 与 agent 两条路径共用同一套段」），日期因此只出现一次。

#### Scenario: Current date present in system prompt
- **WHEN** a generation or evaluation run assembles the system prompt
- **THEN** the resulting system message SHALL contain today's date, enabling relative-time queries ("本报告期", "今年") to be anchored

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

**例外（本次新增，避免与新行为轴冲突）**：DeepEval 原生**行为指标**（`TaskCompletion` / `StepEfficiency` / `AgentLoopDetection` 等）**可以**对轨迹（含工具调用序列）评分，但 SHALL 仅作**独立观测**——SHALL NOT 进入 `overall_score`、SHALL NOT 参与门禁判据（见「Agent behaviour is evaluated with DeepEval native metrics」）。原条款"SHALL NOT 作为评分指标"在归档后**不得**被读成"禁止行为轴"。`ToolCorrectness` 不在该行为指标清单内；它是同步诊断，见「Tool correctness is scored per sample as a diagnostic」。

#### Scenario: Trajectory available for drill-down

- **WHEN** 某样本的评测得分偏低
- **THEN** 可从评测结果读到该样本的工具调用序列与迭代次数

#### Scenario: Behaviour metrics may score the trajectory without entering the gate

- **WHEN** 行为指标对工具调用序列打分
- **THEN** 该分 SHALL 作为独立观测呈现，SHALL NOT 进入 `overall_score` 或门禁

#### Scenario: ToolCorrectness is not classified as a behaviour metric

- **WHEN** 审阅行为指标清单与取分机制
- **THEN** `ToolCorrectness` SHALL NOT 被要求经 `evals_iterator` 或 `test_run_*.json` 取分

### Requirement: Judge client is constructed with an explicit sampling and timeout policy

裁判 SHALL 以**显式**参数构造，且**模型与端点 SHALL 显式指定**——SHALL NOT 依赖指标库的内建默认模型（DeepEval 默认 `gpt-3.5-turbo` 已停服）。**可核验方式**：审阅每个指标构造处的实参，`model=` SHALL 非空。

裁判采样温度 SHALL 显式为 **`temperature=0`**（本项目管理、可复现），SHALL NOT 传入 `top_p`。**理由**：换用 DeepEval 原生指标后，不再有 `LangchainLLMWrapper` 按 `n` 改写温度；若不显式设 0，会落到 provider/`LLM_KWARGS` 默认值（生产实测 0.6），使裁判 JSON 判定不可复现。

裁判 SHALL **经项目既有 provider 层构造**（`build_chat("judge", …)`，其 `**overrides` 透传给 `ChatOpenAI`），SHALL NOT 在 `src/` 内直写 `ChatOpenAI` 并硬编码端点 / 密钥。**可核验方式**：审阅裁判构造处，provider 解析（`judge` 角色 → `JUDGE_PROVIDER` / `JUDGE_MODEL`）SHALL 生效，且无硬编码 base_url / api_key。

**裁判的 HTTP client SHALL 经 `http_async_client` 传入**（含细粒度超时与请求级 hooks），SHALL NOT 只传 `http_client`——原生指标的 `a_measure` 走**异步**，而 langchain 的 `http_client` **只喂同步 client** ⇒ 只传同步字段会让超时与 hooks 双双失效。**可核验方式**：审阅构造实参含 `http_async_client=`。

**思考开关 SHALL 经 `thinking=` 传入**（由 `thinking_extra_body` 按 provider 归一化），SHALL NOT 硬写 provider 专有键（如 `extra_body={"enable_thinking": …}`）。

裁判 SHALL 配置显式请求超时，取值 SHALL 足以覆盖慢速推理模型；并 SHALL 关闭 SDK 自动重试（`max_retries=0`），重试由评测层有界负责（见「Metric failures」）。

裁判模型选型 SHALL 以「结构化输出可靠性」为判据，即 SHALL 有对候选裁判的**活体抽样验证**（覆盖裁判实际使用的**每个指标**），而非仅以「请求未报错」为准。

#### Scenario: Sampling parameters are explicit

- **WHEN** 构造裁判客户端
- **THEN** 构造 SHALL 显式传 `temperature=0`，且 SHALL NOT 传 `top_p`

#### Scenario: Every native metric is constructed with an explicit model

- **WHEN** 构造任一四指标度量（`AnswerRelevancyMetric` / `FaithfulnessMetric` / `ContextualPrecisionMetric` / `ContextualRecallMetric`）
- **THEN** 构造实参 SHALL 显式传入 `model=`（`BridgeDeepEvalLLM`），SHALL NOT 走 `gpt-3.5-turbo` 默认值

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

**两类 `NaN`（只分「掩码」与「非掩码」；SHALL NOT 设计可归因的子分类）**：

- **(a) 掩码跳过型**：`empty` 样本的四指标与两项裁判诊断（`contextual_relevancy` / `non_advice`）被项目主动掩码为 `NaN`/`null`。`tool_correctness` 不属于掩码集合，按自身要求评分。天然不在纯 KB 分母里。
- **(b) 非掩码型**：仅表示**裁判异常/解析失败/超时**等失败。DeepEval 4.2.9 的原生指标在"无 verdict / 无可校验陈述"时**不返回 `NaN`**——`Faithfulness` / `AnswerRelevancy` 返回 **1.0**（`score_qag_verdicts(empty_score=1)`），`ContextualPrecision` / `ContextualRecall` / `ContextualRelevancy` 返回 **0**。因此 SHALL NOT 再把"合法缺值"当作 `NaN` 的来源，也 SHALL NOT 依据请求级日志做子分类（该分类不可实现）。

因此评测 SHALL：① 让 `NaN` 显式可见、且**掩码 / 非掩码在报告中标记不同**（SHALL NOT 静默计入均分）；② **单独统计非掩码 `NaN` 计数**并在报告中呈现；③ **以「非掩码 `NaN`」为判据执行有界重试**（`EVAL_METRIC_MAX_ATTEMPTS`）；**限流类（HTTP 429 / `openai.RateLimitError`）SHALL 与其它失败一样由外层有界重试**，退避 SHALL 为指数 + jitter 并优先采用 `Retry-After`；裁判 SDK 重试 SHALL 固定为 0（`max_retries=0`），避免与外层叠乘；④ 失败留证 SHALL 由**框架内部请求级日志**承载（见「Framework-internal LLM calls…」）；**该日志只做留证，SHALL NOT 被当作分类依据**；⑤ 若该日志完全不可用，报告 SHALL **显式标注"请求级日志不可用"**。

**失败预检**：

- **跑图失败**（异常、`[ERROR]` 回答、空回答/纯空白）的样本在进入指标评分前 SHALL 把四指标与两项裁判诊断记 `NaN`/`null`，`tool_correctness` 记 `null`；SHALL NOT 触发无意义的多次重试。
- **未检索到材料但跑图成功**（`sample_class == empty`）的样本 SHALL 把四指标与两项裁判诊断记 `NaN`/`null`；`tool_correctness` SHALL 照常评分（未调用期望工具记 `0.0`，显式空标签且无调用记 `1.0`）。
- 两种路径 SHALL NOT 让 `[ERROR]` 文本进入 `NonAdvice`。

**错误信息可见**：`score_rows` 的 `error` SHALL 同时写入 Markdown 表格与 `detail_json` 条目；仅写 CSV 不满足"样本行携带失败信息"。

**接受的语义（明写，勿当 bug）**：原生指标对空 verdict 返回 1.0 / 0 而非 `NaN`，主分在空陈述样本上可能偏乐观；本 change 接受该原生语义，不自行改写 verdict 解析。NaN 率只度量失败面；阈值 10%（76 格允许 7 格）为经验初值，**v6 定稿后的首轮全量**跑出真实分布后 SHALL 复核并定稿（v5 分布仅作基线，不用于标定）。

`NaN`（指标出不来值）、样本类别 `empty`（未检索到材料）与跑图失败 SHALL 被区分为**三种不同的跳过语义**。

#### Scenario: A failed metric stays visible on its row

- **WHEN** 某样本的某个指标调用失败
- **THEN** 该样本行 SHALL 仍出现在报告中，且 Markdown 表格与 `detail_json` 条目 SHALL 都携带该失败的错误信息

#### Scenario: No row is silently dropped

- **WHEN** 评测 N 条样本
- **THEN** 报告 SHALL 含 N 行（不因失败少行）

#### Scenario: A non-masked NaN stays visible and is counted

- **WHEN** 某纯 KB 样本的某个指标返回 `NaN`
- **THEN** 该行 SHALL 携带失败信息，该非掩码 `NaN` SHALL 被计数，且 SHALL NOT 参与主分均值

#### Scenario: Masked NaN is excluded by the denominator

- **WHEN** 某 `empty` 样本的四指标与两项裁判诊断被掩码为 `NaN`/`null`
- **THEN** 该格 SHALL NOT 计入 NaN 率分子/分母，且 SHALL 在报告中以**掩码**语义标记；`tool_correctness` 不计入该 NaN 统计

#### Scenario: No sub-classification of non-masked NaN is attempted

- **WHEN** 审阅实现
- **THEN** SHALL NOT 出现"按请求级日志把 NaN 分为合法缺值与裁判失败"的逻辑

#### Scenario: Empty verdicts follow native semantics

- **WHEN** 某样本的回答抽取不出可校验陈述 / verdict
- **THEN** `Faithfulness` / `AnswerRelevancy` SHALL 采用原生返回值 1.0，CP/CR 采用 0，SHALL NOT 由项目改写成 `NaN`

#### Scenario: Retry is keyed on the non-masked NaN

- **WHEN** 某题某四指标首次返回非掩码 `NaN`
- **THEN** 评测 SHALL 按 `EVAL_METRIC_MAX_ATTEMPTS` 重试该指标（判据为非掩码 `NaN`）

#### Scenario: Rate-limit failures are retried by the outer layer

- **WHEN** 某格失败于 HTTP 429
- **THEN** 外层 SHALL 按 `EVAL_METRIC_MAX_ATTEMPTS` 有界重试，退避为指数 + jitter 并优先采用 `Retry-After`；SDK 重试 SHALL 为 0

#### Scenario: Failed generation skips scoring without retries

- **WHEN** 某题跑图失败（异常、`[ERROR]`、空回答/纯空白）
- **THEN** 该题四指标与两项裁判诊断 SHALL 直接记 `NaN`/`null`，`tool_correctness` SHALL 记 `null`，SHALL NOT 触发重试，`[ERROR]` 文本 SHALL NOT 进入 `NonAdvice`

#### Scenario: Empty material still scores tool correctness

- **WHEN** 某题跑图成功但 `sample_class == empty`
- **THEN** 四指标与两项裁判诊断 SHALL 记 `NaN`/`null`，`tool_correctness` SHALL 按期望工具评分（未调用为 `0.0`，显式空标签且无调用为 `1.0`）

#### Scenario: Diagnostic failures are not retried

- **WHEN** 某诊断指标失败
- **THEN** 该项 SHALL 直接记 `NaN`/`null`，SHALL NOT 触发 `EVAL_METRIC_MAX_ATTEMPTS` 重试

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

### Requirement: Reported overall score uses the main-score 口径

`eval_report` 的 `overall_score` SHALL 采用与报告主分一致的口径：**纯 KB 掩码后、且排除 `NaN` 行的四项指标均值**（`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`）。

`answer_correctness` SHALL 不再参与 `overall_score`，且 `kb_eval` API SHALL NOT 再暴露该指标。

判定「通过」的阈值 SHALL 由**单一常量**提供 —— SHALL NOT 在评测落库与前端各硬编码一份。阈值取值 SHALL 在 **v6 定稿后**重新标定（换尺子后历史阈值作废；v5 分布仅作基线）。

**Baseline 语义（新口径首轮）**：首次以 `deep-eval-native` 口径跑出的全量 SHALL 以 `run_type="baseline"` 落库；`run_type` SHALL 由 CLI 参数 `--run-type` 写入（默认 `manual`），SHALL NOT 由"是否首轮"自动推断。**v6 阈值标定前，所有新口径全量运行 SHALL 显式传 `--run-type baseline`**；`manual` 仅用于阈值标定后的常规回归。`--run-type baseline` 与 `--gate` SHALL 互斥（启动即报错）。

`kb_eval` 对 `run_type="baseline"` SHALL 返回 `passed=None`（SHALL NOT 用未标定的阈值判红/绿），该转换 SHALL 落在 API 响应组装层（`_build_eval_payload`），SHALL NOT 依赖把数据库 `passed` 写成 `NULL`（该列非空）。前端 SHALL 以**三态**渲染 **KB 评测徽标**：`passed=null` 显示"未标定"（灰色，SHALL NOT 显示 ✓/✗），`true` 显示通过，`false` 显示不通过；前端 SHALL NOT 用 `overall_score >= 阈值` 自行重算通过态。**分块质量弹窗**（`showEvalModal`）不是 KB 评测消费者，其布尔 `passed` 与 0.70 阈值 SHALL NOT 被本 change 改动。

API 返回四项分值与 `overall_score` 时 SHALL 用 `is not None` 判断，SHALL NOT 用真值判断——原生指标合法的 `0.0` 不得被转成 `null`（否则前端会把"真实 0 分"当成"没有评测"而隐藏徽标）。

跨口径分数 SHALL NOT 被直接比较：报告与台账 SHALL 标注「口径版本」（`deep-eval-native`），历史（modern-0.4.x / bridge-0.3.x）的分数不得作为回归判据。

**行为轴不进主分、不进门禁**：`overall_score` 与门禁判据 SHALL 只由上述**四指标**构成；行为指标与诊断指标为**独立观测**，其失败 SHALL 单独标记、SHALL NOT 影响该题主分。

#### Scenario: Overall score excludes non-pure-KB and NaN rows

- **WHEN** 一次评测产出一行 `eval_report`
- **THEN** 其 `overall_score` SHALL 等于该次**纯 KB 且非 NaN** 样本的四项指标均值

#### Scenario: Baseline does not judge pass/fail

- **WHEN** 首轮 `deep-eval-native` 全量以 `run_type="baseline"` 落库
- **THEN** `kb_eval` SHALL 返回 `passed=None`，且前端 SHALL NOT 渲染通过/不通过

#### Scenario: Frontend renders the unknown pass state as neutral

- **WHEN** `kb_eval` 返回 `passed=None`
- **THEN** KB 评测徽标 SHALL 显示"未标定"（灰色），且 SHALL NOT 显示 ✓ / ✗，也 SHALL NOT 依据 `overall_score` 重新判定；分块质量弹窗 SHALL NOT 被改动

#### Scenario: Run type comes from the CLI argument

- **WHEN** 用户以 `--run-type baseline` 运行评测
- **THEN** 新写入的 `eval_report` 行 SHALL 为 `run_type="baseline"`；未传该参数时 SHALL 为 `manual`

#### Scenario: Baseline cannot be gated

- **WHEN** 同时传 `--run-type baseline` 与 `--gate`
- **THEN** 启动即报错（非零退出），不执行任何评测

#### Scenario: Legitimate zero scores survive the API

- **WHEN** 某指标的真实值为 `0.0`
- **THEN** `kb_eval` 响应 SHALL 返回 `0.0`（SHALL NOT 变成 `null`）

#### Scenario: Pass threshold is defined once

- **WHEN** 评测落库判定 `passed`、前端渲染通过/不通过
- **THEN** 两者 SHALL 引用同一阈值常量

#### Scenario: Thresholds are recalibrated after v6

- **WHEN** **v6 定稿后**的全量分布产出（v5 分布仅作基线记录）
- **THEN** 门禁阈值 SHALL 按该分布重新取值，且报告/台账 SHALL 标注口径版本与标定轮次

### Requirement: Evaluation scoring entry is DeepEval, with RAGAS metrics reached through its bridge

评测打分的入口 SHALL 统一为 DeepEval，四指标 SHALL 使用 **DeepEval 原生指标**：`AnswerRelevancyMetric`、`FaithfulnessMetric`、`ContextualPrecisionMetric`、`ContextualRecallMetric`，SHALL NOT 使用 `deepeval.metrics.ragas` 桥。

每个度量 SHALL 以一条单轮样本构造：`input`（问题）/ `actual_output`（回答）/ `expected_output`（参考答案）/ `retrieval_context`（检索材料）。

裁判 SHALL 以 `BridgeDeepEvalLLM` 包装后传入 `model=`；`build_metrics` SHALL NOT 再接收 `embeddings` 参数（原生 `AnswerRelevancy` 不需要 embedding）。

**四指标评分 SHALL 在题级串行执行（并发=1）**：同一时刻只评一道题。理由：裁判 client 与证据桶跨题复用，题级串行保证 429 判定与 `trace_id` 归因的确定性；指标内部允许 DeepEval 自身的并发（`a_measure` 内 `gather`）。

#### Scenario: Scoring is serial at the question level

- **WHEN** 审阅四指标评分实现
- **THEN** 同一时刻 SHALL 只有一道题在评分（串行/并发=1）

#### Scenario: Scoring uses the four native metrics

- **WHEN** 对绑定知识库的样本运行评测
- **THEN** 报告 SHALL 含 `faithfulness` / `answer_relevancy` / `context_precision` / `context_recall` 四项，且四项 SHALL 由 DeepEval 原生指标产出

#### Scenario: RAGAS bridge is not referenced

- **WHEN** 审阅评测代码
- **THEN** SHALL NOT 出现对 `deepeval.metrics.ragas` / `RAGAS*Metric` 的引用

### Requirement: Agent behaviour is evaluated with DeepEval native metrics

评测 SHALL 在上下文四指标之外，产出 **Agent 行为轴**的指标，取自 DeepEval 原生族（如 `TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`）。选用的数量 SHALL 收敛（总计 ≤5）；**具体清单在实施期定稿**（设计只给候选；SHALL NOT 出现"设计定稿"与"实施期定稿"互相推诿的悬空）。

**取分机制（已实测，两半缺一不可）**：评测 SHALL 以 `EvaluationDataset(goldens=[Golden(input=<题面>, expected_output=<参考答案>)])` 的 **`evals_iterator(metrics=[…], display_config=DisplayConfig(results_folder=…))`** 驱动，并在其循环体内以 `graph.ainvoke(..., config={"callbacks": [CallbackHandler()]})` 执行被测图。

评测 SHALL 从 **`<results_folder>/test_run_*.json`** 回读行为分数，**逐题分 SHALL 取 `testCases[j].metricsData[k].score`**（`metricsData[k]` 含 `name` / `score` / `success`）；SHALL NOT 以 `metricsScores` 作为**逐题**分来源——实测 `metricsScores` 是**按指标聚合**（`{"metric":…,"scores":[…],"passes":…}`），仅可用于"整体是否取到分"的粗守卫。`golden ↔ testCase ↔ trace_id` 的映射 SHALL 以 `order`（或 `input` 文本）为依据。SHALL NOT 以"读取调用方持有的 `metric` 实例的 `.score`"作为取分方式——**实测该值恒为 `None`**（eval loop 使用 `copy_metrics()` 的副本）。

**生成器必须耗尽（否则无分、无 JSON）**：`evals_iterator` 是 **generator**，`test_run_*.json` 只在**生成器耗尽**时才写。评测 SHALL 把循环**跑到耗尽**（SHALL NOT 提前 `break`），并在循环结束后断言 JSON 已落盘；循环体抛异常或提前结束导致的"无 JSON" SHALL 触发 fail-loud。

**异步可行性（已实测，2026-10-16）**：项目把跑图放在**单个** `asyncio.run` 内、循环体用 `graph.ainvoke`。实测在 `asyncio.run` 内驱动 `evals_iterator`（默认 `run_async=True`，或 `AsyncConfig(run_async=False)`）**两路均可用**（1 题、真调 judge、逐题分 = 1.0）。机制：`deepeval/utils.py:209 get_or_create_event_loop()` 在 `loop.is_running()` 时调 **`nest_asyncio.apply()`**，使内层 `run_until_complete` 在**同一个** loop 上重入、**不新建第二个事件循环** ⇒ "单事件循环 + 引擎连接池"假设**不破**。`nest_asyncio.apply()` 是**进程级 monkeypatch**，评测 CLI（短进程）可接受，SHALL NOT 在长驻服务里调用（SHALL 在代码注释写明）。证据：`docs/tmp/deepeval-poc/RESULTS.md`「Q1 探测」节。

评测 SHALL 为**每题新建独立的 `CallbackHandler` 实例**，SHALL NOT 跨题复用（handler 含实例态，复用会串 trace）。

**失败样本例外**：`trajectory.failed is True` 的样本 SHALL 从行为轴 fail-loud 断言中豁免，行为分 SHALL 显示 `N/A`（SHALL NOT 写成 0）；若该题在 `test_run_*.json` 中没有对应 `testCase` 行，SHALL 按 `q_index` 对齐并放行该题，SHALL NOT 因缺行中止整轮。其余样本仍 SHALL 断言「轨迹非空 **且** 已从 `test_run` JSON 取到该指标的分」，任一不成立时 SHALL 显式失败（fail-loud），SHALL NOT 让行为指标静默返回 `0.0`。

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

- **WHEN** 未失败的样本轨迹为空，或 `test_run` JSON 中取不到该指标的分
- **THEN** 评测 SHALL 显式失败并给出原因，SHALL NOT 输出 `0.0` 作为有效分数

#### Scenario: Failed run is N/A instead of aborting

- **WHEN** 某题 `trajectory.failed is True`
- **THEN** 行为轴 SHALL 显示该题 `N/A`，SHALL NOT 因空轨迹中止整轮评测；其余样本仍 SHALL 正常评分并落盘

### Requirement: A bounded escape hatch exists when the bridge is unusable

当原生指标路径不可用时，评测 SHALL 具备一条**有界**的替代路径。本轮**只要求预留可替换边界**：

- 评测 SHALL 把「**采样/用例构造**」与「**评分**」解耦到可替换的边界，DeepEval 原生指标为默认实现；
- 替代实现 SHALL **在首次启用逃生门时**作为前置工作实现并冒烟；该前置 SHALL 登记在台账与需求池。**本轮不实现**。

该路径 SHALL NOT 作为默认路径常驻；每次启用 SHALL 在评测台账记录**原因与生效轮次**。

触发条件 SHALL 至少包含：原生指标产出 `NaN` 的比例超过设定阈值且重试无效；或原生指标在当前环境无法稳定运行。

#### Scenario: Escape hatch activation is recorded

- **WHEN** 因原生指标不可用而启用替代路径
- **THEN** 台账 SHALL 记录启用原因、生效轮次与该轮的口径说明

#### Scenario: Escape hatch is not the default

- **WHEN** 原生指标可用
- **THEN** 评测 SHALL NOT 走替代路径

### Requirement: 0.4.x-era evaluation artifacts are disposed of explicitly

弃用 0.4.x 时，为现代栈而写的代码与配置 SHALL 被**逐项处置**（复用 / 删除 / 改写），SHALL NOT 留下悬空引用或失义配置。

- **复用（改嫁到 provider 层）**：`JUDGE_MAX_TOKENS` / `JUDGE_THINKING` SHALL 经 `build_chat("judge", max_tokens=…, thinking=…)` 传入；`EVAL_METRIC_MAX_ATTEMPTS` SHALL 保留（判据见「Metric failures」）；`JUDGE_HTTP_TIMEOUT_*` SHALL 经 **`http_async_client`** 保留。**`JUDGE_HTTP_MAX_RETRIES` SHALL 删除**，裁判 `max_retries` SHALL 硬编码 0（可配置成 >0 会与外层重试叠乘）。
- **删除**：`build_ragas_judge()` / `build_ragas_embeddings()` / `build_native_client()` SHALL 已删除；`ragas` 依赖 SHALL 已从 `pyproject.toml` 移除；所有 `RAGAS_*` 配置 SHALL 已重命名（`JUDGE_*` / `EVAL_*`）或删除（出题专用 / `RAGAS_HTTP_MAX_RETRIES`）。
- **旧配置不兼容**：启动期 SHALL 检测残留的 `RAGAS_*` 环境变量并**立即失败**，错误信息 SHALL 列出「旧名 → 新名」映射；SHALL NOT 回退读取旧名。环境模板 SHALL 只写新名（迁移对照表放 runbook）。
- **旧目录保留**：部署脚本 SHALL 只创建 `data/deepeval/`，SHALL NOT 自动删除旧 `data/ragas/`；runbook SHALL 提示人工确认后清理。
- **命名收尾**：CLI 模块 SHALL 为 `src/cli/eval_deepeval.py`，测试 SHALL 为 `test_eval_deepeval*` / `test_eval_native_*`，台账 SHALL 为 `docs/agents/eval-ledger.md`，Langfuse `--session-id` 默认值 SHALL 为 `deepeval_eval_session`；报告 `caliber` / 默认 CSV 文件名前缀 / Markdown 标题 / 日志事件 `judge model missing` SHALL 一并切换，SHALL NOT 残留 `bridge-0.3.9` 或 `RAGAS Evaluation Report`。
- **改写**：`eval_scoring.build_metrics()` SHALL 改为原生四指标；`judge call` / `metric retry` 日志事件口径 SHALL 随之重述；`--generate` 文案 SHALL 指向 DeepEval `Synthesizer`。
- **ADR**：SHALL 追加新 ADR（0027）**完全取代** ADR-0026 / ADR-0022 / ADR-0023，并在 0027 正文重述仍生效的约束（async hooks / 细粒度超时 / `max_tokens` / `thinking` / `max_retries=0` / 评估检索镜像生产）；三份旧 ADR 的 Status SHALL 加**反向指针**并更新索引。

**判据边界**：自查 SHALL 以「**最终态**无悬空引用」为准，SHALL NOT 要求每个中间提交都无悬空引用——删除动作 SHALL 与"调用点改写 + 受影响测试"**同一提交**。

#### Scenario: No dangling references on the evaluation path

- **WHEN** 审阅评测路径代码
- **THEN** SHALL NOT 出现对 `build_ragas_judge` / `build_ragas_embeddings` / `build_native_client` / `deepeval.metrics.ragas` 的引用

#### Scenario: Reused settings land on the judge

- **WHEN** 审阅裁判构造处
- **THEN** token 上限、思考开关、细粒度超时 SHALL 以 `ChatOpenAI` 参数形式落到裁判上

#### Scenario: SDK retries are fixed at zero

- **WHEN** 审阅裁判构造处
- **THEN** `max_retries` SHALL 为字面量 0，且 SHALL NOT 存在可将其配成 >0 的环境变量

#### Scenario: Legacy environment names fail fast

- **WHEN** 启动时环境中仍存在任一 `RAGAS_*` 变量
- **THEN** 进程 SHALL 以明确错误退出，错误信息 SHALL 含旧名到新名的映射，SHALL NOT 静默回退到旧名

#### Scenario: The old data directory is not deleted automatically

- **WHEN** 部署脚本创建 `data/deepeval/`
- **THEN** SHALL NOT 删除 `data/ragas/`，且 runbook SHALL 提示人工确认后清理

#### Scenario: Superseded ADRs get reverse pointers

- **WHEN** 审阅 `docs/adr/0026-*.md` / `0022-*.md` / `0023-*.md`
- **THEN** 其 Status SHALL 含指向 ADR-0027 的反向指针，且 0027 SHALL 出现在 ADR 索引中

### Requirement: Results are written back to self-hosted Langfuse v2 through the project's own channel

评测 SHALL NOT 假定 DeepEval 提供 Langfuse 集成。回写 SHALL 由**项目自建通道**完成。

回写 SHALL 复用现有实现：客户端取 `langfuse_context.client_instance`（SHALL NOT `new Langfuse()`）；逐条 `client.score(trace_id=…, name=…, value=…)`；失败 SHALL 只记 warning、SHALL NOT 阻断评测主流程。

回写 SHALL 覆盖**行为轴指标**，与四项同级并共用同一 `trace_id`。

**边界（明写）**：DeepEval 的轨迹（span 树）SHALL NOT 被期望出现在 Langfuse 上；行为指标的轨迹 SHALL 以**报告 / 台账 / 本地日志**为准。

`NaN` SHALL **不写入** Langfuse（维持"缺失"语义）；SHALL NOT 以 `0` 代替 NaN。

评测运行 SHALL 使用 `--session-id` 指定 Langfuse session，默认值 SHALL 为 `deepeval_eval_session`；历史 session 不重命名。

新口径 score 名 SHALL 带**口径后缀** `@deep-eval-native`，SHALL NOT 与历史口径同名覆盖。

#### Scenario: Scores go back through the project channel

- **WHEN** 一次评测产出分数且观测开关为真
- **THEN** 每题每个指标 SHALL 经 `client.score(trace_id=…)` 回写，行为轴指标 SHALL 一并回写

#### Scenario: NaN is not written as zero

- **WHEN** 某题某指标为 `NaN`
- **THEN** Langfuse 上 SHALL NOT 出现该 score，且 SHALL NOT 出现 `0`

#### Scenario: New score names carry the caliber suffix

- **WHEN** 回写新口径分数
- **THEN** score 名 SHALL 含 `@deep-eval-native`，历史同名 score SHALL NOT 被覆盖

### Requirement: NaN rate gates the evaluation and triggers the escape hatch

评测 SHALL 统计 **NaN 率**并在报告中单列。门禁判据 SHALL 为：**NaN 率 ≤ 阈值 视为可接受**；**> 阈值 则门禁失败**。**阈值 SHALL 标为"经验初值 10% / 待 v6 定稿后的全量复核"**；v6 定稿后的首轮全量跑出真实 NaN 率后 SHALL 复核并定稿（v5 分布仅作基线）。

**分子/分母 SHALL 精确定义（单位是"格"，不是"题"）**：

- **分母** = **纯 KB 样本 × 四项指标**（格数）。实测有效纯 KB = 19 题 ⇒ 分母 76 格；`>10%` 即 `>7.6 格 ⇒ ≥8 格`判失败。
- **分子** = **纯 KB 格里的全部非掩码 `NaN`**（掩码类由分母天然排除；原生指标的空 verdict 返回 1.0/0、不产生 NaN，故不计入）。

NaN 率超阈值时，报告 SHALL 给出「**建议启用逃生门**」的提示；**是否启用由人决定**，启用 SHALL 在台账记录原因与生效轮次。

理由（记明，避免后人"修回"）：阈值过严会让门禁常态失败、过松则失去意义；依据单薄这一点必须写进规格，避免后人把"10%"当作经实测标定的数字。

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

出题入口 SHALL 为独立命令 `python -m src.cli.testset_synth`，评测入口 SHALL NOT 触发测试集生成。

- `python -m src.cli.eval_deepeval` SHALL NOT 调用任何出题代码，测试集输入 SHALL 为既有版本文件。
- CLI `--generate` SHALL 显式失败并指向 `python -m src.cli.testset_synth`，SHALL NOT 让用户撞到裸 `ImportError`。
- `POST /api/ragas/generate` SHALL 保持路由挂载并返回 410，响应文案 SHALL 给出同一 CLI 指引。
- 出题是分钟级长任务，本 change SHALL NOT 新增同步出题 API。

#### Scenario: Evaluation does not trigger generation

- **WHEN** 用户运行 `python -m src.cli.eval_deepeval`
- **THEN** SHALL NOT 发生任何出题调用，测试集 SHALL 从既有版本文件加载

#### Scenario: Legacy generation flags fail with the new command

- **WHEN** 用户运行 `eval_deepeval --generate` 或调用 `POST /api/ragas/generate`
- **THEN** 系统 SHALL 以明确错误退出或返回 410，并给出 `python -m src.cli.testset_synth` 指引

### Requirement: Deviations from DeepEval's recommended practices are recorded

本次对官方推荐路径的**有意偏离** SHALL 被记录：

1. **门禁**：保留自建 `--gate` + 单一阈值常量，而非官方的 `assert_test()` + `pytest` + `deepeval test run`；
2. **并发**：四指标评分**题级串行（并发=1）**，而非官方的 `a_measure()` + `gather` / `async_config.max_concurrent`。理由：裁判 client 与证据桶跨题复用，题级串行保证 429 判定与 `trace_id` 归因确定；指标内部仍允许 DeepEval 自身并发。

每条偏离 SHALL 写明「官方路径 / 我们的做法 / 理由 / 接受的代价」。

#### Scenario: Deviations are documented with rationale

- **WHEN** 审阅 design
- **THEN** 两处偏离 SHALL 各含"官方路径 / 我们的做法 / 理由 / 代价"四项，且 SHALL NOT 留悬空

### Requirement: Framework-internal LLM calls are observable by trace id

评测 SHALL 使**框架内部的 LLM 调用**（原生指标 `a_measure` 内部的多次裁判调用）在日志中可按该题的 `trace_id` 追溯。

**行为轴判官调用的归因边界（明写，避免误期待）**：DeepEval 行为指标在 `evals_iterator` **耗尽之后**才评分，届时循环上下文已结束 ⇒ 其判官调用**不**经 `http_async_client` 的 hooks 做逐题 `trace_id` 归因（该层 `trace_id` 记为空）；行为轴的逐题失败证据 SHALL 由其 `test_run_*.json` 的 `testCases[].metricsData[].reason` 承载，并在报告「Agent 行为轴」段呈现。行为轴请求没有 active measure 时，hook SHALL 照常记录 request/response 事件（保留 hooks 已触发的 smoke 断言），但 SHALL NOT 分配 `judge_call_id`、SHALL NOT 产 `JUDGE_HTTP`。

实现手段 SHALL 为：在裁判的 **`http_async_client`**（`httpx.AsyncClient`）上挂**请求级日志**（httpx event hooks）。**成功与失败路径都产日志行**：每次收到 response SHALL 产一条 `JUDGE_HTTP`（`status` / `finish_reasons` / `tokens_in` / `tokens_out` / `latency_ms` / `json_ok`）；传输失败（无 response）SHALL 由 `end_measure()` 补一条 `status=null` / `error="no_response"` 的记录。**SHALL 挂在 `http_async_client` 而非 `http_client`**。

**hook SHALL 为 `async def` 且以 `await response.aread()` 读体**。**hook 的整个函数体 SHALL 包 `try/except`；任何观测异常 SHALL NOT 向 httpx 抛出**，只记 warning。

**落地现状**：`JUDGE_HTTP` 覆盖成功与失败路径；`JUDGE_CALL` 记录 measure 级汇总（`request_count` / `response_count` / `statuses` / `tokens` / `reason_len` / `reason_hash` / `error`），`seconds` / `note` 已更名为 `latency_ms` / `error`。**请求体摘要 / 响应体摘要仍不记录**；`trace_id` 与 token 足以定位“哪次调用、哪个模型、什么状态、是否被截断”。

**该日志只做留证**：SHALL NOT 被当作 `NaN` 子分类的依据。

`trace_id` SHALL 经**显式闭包**绑定进 event hook，SHALL NOT 依赖 contextvar 传递；裁判 client 跨样本复用 ⇒ 闭包 SHALL 绑到**可变证据桶**（题级串行，安全）。`JUDGE_HTTP` SHALL 显式携带 `trace_id` / `q_index`；`current_trace_id` 只服务日志 patcher 与桥内部 warning，SHALL NOT 作为 hook 归因的唯一来源。

**验收范围（明写）**：本条只覆盖**经我们提供的 `http_async_client` 发出的 LLM 请求**；行为指标的 **DeepEval span 树**仍不进日志。

#### Scenario: Internal calls carry the question's trace id

- **WHEN** 某题的裁判调用在指标内部发生（含重试与多次调用）
- **THEN** 对应日志行 SHALL 携带该题的 `trace_id`

#### Scenario: The logging hook rides on the async client used for the timeout

- **WHEN** 审阅裁判客户端构造处
- **THEN** 请求级日志 SHALL 挂在用于细粒度超时的**同一个 `http_async_client`** 上，且 hook SHALL 为 `async def`

#### Scenario: Hook wiring is proven by a smoke assertion

- **WHEN** 实施期跑 1 题真调裁判
- **THEN** SHALL 断言响应 hook 触发 **> 0** 次，且能取到状态码与 `finish_reasons`

#### Scenario: Successful responses are logged

- **WHEN** 某次裁判 HTTP 调用返回 200 且 body 可解析
- **THEN** SHALL 产出一条 `JUDGE_HTTP`，含 status / finish_reasons / tokens / latency；SHALL NOT 只在失败时记录

#### Scenario: Transport failure is recorded with a null status

- **WHEN** 某次裁判调用已发出 request 但连接失败或超时
- **THEN** SHALL 产出一条 `JUDGE_HTTP(status=null, error="no_response")`，且该次 `judge_call_id` 可被检索

#### Scenario: Observability failure does not fail the request

- **WHEN** `JUDGE_HTTP` 的字段序列化或 `log_event` 抛异常
- **THEN** 裁判 HTTP 调用 SHALL 正常返回，评分/重试/门禁与无 trace 时一致，只记 warning

### Requirement: Evaluation can run with DeepEval metrics only

评测入口 SHALL 支持只跑 DeepEval 原生指标（Agent 行为轴）而跳过四指标的模式。

- 开关 SHALL 为 CLI 参数 `--deepeval-only`（别名 `--behaviour-only`），默认关闭；关闭时行为与现状**完全一致**。
- 开启时 SHALL NOT 构造四指标度量、SHALL NOT 调用四指标评分。
- 开启时报告 SHALL 显式标注 `主分: 未跑（--deepeval-only）`，SHALL NOT 输出四指标数值行，SHALL NOT 把缺失写成 `0`。
- 开启时 SHALL NOT 写入 `eval_report` 表；SHALL NOT 写 CSV；SHALL NOT 执行质量门禁；与 `--gate` 同时出现 SHALL 在启动时报错。
- 行为轴段、Markdown 报告落盘、`--limit` 冒烟 SHALL 仍可用。
- 报告头 SHALL 明写"行为轴仅作诊断观测、无门禁语义"。

#### Scenario: DeepEval-only run skips four-metric scoring

- **WHEN** 以 `--deepeval-only`（或 `--behaviour-only`）运行评测
- **THEN** 退出码 0；报告含「Agent 行为轴」段与 `主分: 未跑` 标注；`eval_report` 表无新行；不生成 CSV

#### Scenario: Default run is unchanged

- **WHEN** 不加 `--deepeval-only`
- **THEN** 四指标与行为轴照常执行，报告、CSV、落库与现状一致

#### Scenario: Gate and DeepEval-only are mutually exclusive

- **WHEN** 同时传 `--deepeval-only` 与 `--gate`
- **THEN** 启动即报错（非零退出），不执行任何评测

### Requirement: Diagnostic metrics do not affect the main score

评测 SHALL 提供三项**诊断指标**，仅作观测，SHALL NOT 进入主分、门禁或 `eval_report` 的四指标列：

- `ContextualRelevancyMetric(model=judge)`：不需要 `expected_output`；评分层 SHALL 只对**前 8 条** `retrieval_context` 判定（常量 `EVAL_RELEVANCY_MAX_CONTEXTS`），SHALL NOT 原地修改共享 test case；报告 SHALL 标注"仅判前 8 条（并集顺序，非最相关 8 条）"。
- `NonAdviceMetric(advice_types=["financial"], model=judge)`：财务合规口径。
- 每个诊断指标 SHALL 显式传 `model=judge`，SHALL NOT 使用 DeepEval 的默认模型（`gpt-3.5-turbo` 已停服）。
- `ToolCorrectnessMetric`：有 `expected_tools` 时 SHALL 按「Tool correctness is scored per sample as a diagnostic」评分；无该字段时记 `N/A`。构造 SHALL 使用 `async_mode=False`、`eval_mode="llm"`。

诊断指标的 `NaN` SHALL 与四指标的 `NaN` 分开计数，SHALL NOT 影响 NaN 率门禁。诊断指标失败 SHALL 直接记 `NaN`/`null`，SHALL NOT 重试。`sample_class == empty` 或跑图失败的样本：`contextual_relevancy` / `non_advice` SHALL 写 `NaN`/`null`；`tool_correctness` SHALL 按工具正确性要求处理（空样本可评分，跑图失败/空回答记 N/A）。SHALL NOT 让 `[ERROR]` 文本进入 `non_advice`，也 SHALL NOT 让空 context 的 `0.0` 进入诊断均值。

诊断指标的逐题值 SHALL 写入 `eval_report.detail_json`，键名使用**裸指标名**（`contextual_relevancy` / `non_advice` / `tool_correctness`），与四指标同级；`tool_correctness` 有标签时为 `0.0`–`1.0`、无标签或失败时为 `null`，并附 `tool_correctness_reason` 字符串。`kb_eval` API SHALL NOT 返回这些键（只存不读）。"不进主分"SHALL 由 `EVAL_METRIC_COLUMNS` 白名单保证，SHALL NOT 依赖键名前缀。

#### Scenario: Diagnostic metrics are not part of the main score

- **WHEN** 审阅主分聚合与门禁
- **THEN** `contextual_relevancy` / `non_advice` / `tool_correctness` SHALL NOT 出现在 `EVAL_METRIC_COLUMNS` 或门禁判据中

#### Scenario: Diagnostics use the project judge

- **WHEN** 构造任一诊断指标
- **THEN** 构造实参 SHALL 显式传入 `model=judge`（`BridgeDeepEvalLLM`），SHALL NOT 走 `gpt-3.5-turbo` 默认值

#### Scenario: Empty samples mask only judge diagnostics

- **WHEN** 某样本 `sample_class == empty` 或跑图失败
- **THEN** `contextual_relevancy` / `non_advice` SHALL 写 `NaN`/`null`，`tool_correctness` SHALL 按其评分要求处理（空样本可评分、跑图失败/空回答为 N/A），且 `non_advice` SHALL NOT 对 `[ERROR] ...` 文本评分

#### Scenario: Diagnostic failures are not retried

- **WHEN** 某诊断指标调用失败
- **THEN** 该项 SHALL 直接记 `NaN`/`null`，SHALL NOT 触发有界重试

#### Scenario: Diagnostics are stored for drill-down but not exposed

- **WHEN** 一次评测落库
- **THEN** `detail_json` 的逐题条目 SHALL 含三个裸键名，`tool_correctness` 按标签与执行状态写 `0.0`–`1.0` 或 `null`，且 `kb_eval` 响应 SHALL NOT 含这些键

#### Scenario: Relevancy is truncated and labeled

- **WHEN** 生成评测报告
- **THEN** `contextual_relevancy` 的分数 SHALL 基于前 8 条 context，且报告 SHALL 含截断说明

#### Scenario: ToolCorrectness is N/A without labels

- **WHEN** 对无 `expected_tools` 字段的题集运行评测
- **THEN** `tool_correctness` SHALL 记 `N/A`，SHALL NOT 触发 fail-loud

### Requirement: Evaluation consumes v6 testsets without changing the scoring contract

评测 SHALL 通过 `testset_io.load_testset_samples` 读取 `testset_<kb>_v<N>.json`，v6 新增字段 SHALL NOT 改变四项主分与两项裁判诊断的口径。

- 四项主分与两项裁判诊断 SHALL 只消费 `user_input` / `reference`；检索上下文 SHALL 来自本次运行的**实际检索结果**，SHALL NOT 使用题集里的 `reference_contexts`（后者只供出题阶段的自足门）。
- `expected_tools` SHALL 只供 `tool_correctness` 使用，SHALL NOT 进入四项主分或两项裁判诊断；`hop_type` / `hop_basis` / `evolutions` / `source_file` / `numeric_risk` / `synth_quality` SHALL NOT 进入任何指标计算。
- `tool_correctness` SHALL 按「Tool correctness is scored per sample as a diagnostic」评分；无 `expected_tools` 字段的旧题集 SHALL 记 `null`。
- 加载器 SHALL 同时兼容 v5（无新增字段）与 v6（有新增字段），并保留 `expected_tools` 缺失与显式 `[]` 的区别。
- 报告元数据 SHALL 记录 `testset_version`（实际加载到的版本号，含未显式传 `--testset-version` 时）；该字段 SHALL 在**正常与 `--deepeval-only` 两个报告分支**都存在；Markdown 报告头 SHALL 渲染该字段；DB `detail_json` 的逐题条目 SHALL 带 `testset_version`（CSV 不加列，避免破坏现有消费方）。

#### Scenario: v6 fields are inert for main scoring

- **WHEN** 用 v6 题集运行评测
- **THEN** 四项主分与两项裁判诊断的输入 SHALL 与同题面在 v5 结构下完全一致，`tool_correctness` SHALL 按 `expected_tools` 评分

#### Scenario: Testset contexts are not fed to metrics

- **WHEN** 某题的 `reference_contexts` 与本次实际检索结果不同
- **THEN** 评分 SHALL 使用实际检索结果，SHALL NOT 使用 `reference_contexts`

#### Scenario: Report states which testset it used

- **WHEN** 用默认版本（最新）运行评测
- **THEN** 报告元数据与 Markdown 报告头 SHALL 含 `testset_version`，且数值 SHALL 等于实际加载的文件版本；DB `detail_json` 的条目 SHALL 同样带该字段

#### Scenario: v5 remains readable

- **WHEN** 用户指定 `--testset-version 5`
- **THEN** 加载 SHALL 成功，且 SHALL NOT 因缺少 v6 字段而报错

### Requirement: Tool correctness is scored per sample as a diagnostic

评测 SHALL 对带 `expected_tools` 的样本逐题计算 `tool_correctness`，作为**诊断值**，SHALL NOT 进入 `overall_score`、门禁或 Langfuse score。

- `tool_correctness` SHALL 为 `0.0`–`1.0` 的浮点数，等于「命中的期望工具数 / 去重后的期望工具数」；显式空集时分母按 0 处理，无调用记 `1.0`、有调用记 `0.0`。实现 SHALL NOT 假设工具名是 `retrieve_kb`，SHALL NOT 假设标签只有一项。
- 标签 SHALL 按三态处理：字段缺失或 `None` → 无标签（N/A）；显式 `[]` → 期望不调用任何工具；非空列表 → 按比例评分。实现 SHALL NOT 用「空列表」表示「无标签」。
- 加载时 SHALL 校验非空标签为 `list[str]` 且每个元素为非空字符串；不合法时 SHALL 抛带 `q_index` 的 `ValueError`，SHALL NOT 静默错分。
- 非空标签在评分前 SHALL 按首次出现去重；发现重复项 SHALL 记 warning，SHALL NOT 把重复项当成多次独立期望。
- 指标 SHALL 使用 DeepEval `ToolCorrectnessMetric(model=judge, async_mode=False, eval_mode="llm")`；SHALL NOT 传入 `available_tools`；`evaluation_params` SHALL 为空（只比较工具名与类型）；SHALL NOT 启用 `should_exact_match` 或 `should_consider_ordering`。
- 实际调用 SHALL 取该题**顶层消息轨迹**的 `tool_calls[].name`；子代理内部的工具调用 SHALL NOT 计入，`delegate_task` SHALL NOT 获得特殊豁免。期望与实际的 `ToolCall.type` SHALL 固定为默认 `FUNCTION`。
- 期望工具 SHALL 按「每个期望工具至少被调用一次」匹配；重复调用与额外工具 SHALL NOT 扣分。
- `sample_class == empty` 的样本 SHALL 照常评分：未调用期望工具时记 `0.0`，SHALL NOT 按空样本掩码置 NaN。
- 失败样本 SHALL 记 `null`（N/A），SHALL NOT 记 `0.0`。失败判定 SHALL 至少覆盖：`trajectory.failed` 为真、回答以 `[ERROR]` 开头、回答为空或纯空白。空回答按失败处理，以避免 `expected_tools=[]` 时把失败样本记成 `1.0`。
- 指标构造或评分失败 SHALL 记 `null`，SHALL NOT 重试。
- `tool_correctness` SHALL 是同步诊断，SHALL NOT 归入行为轴；SHALL NOT 适用 `evals_iterator` / `test_run_*.json` 取分与行为轴 fail-loud。

#### Scenario: Expected tool was called

- **WHEN** 某样本 `expected_tools=["search_web"]`，顶层轨迹包含一次 `search_web`
- **THEN** 该题 `tool_correctness` SHALL 为 `1.0`

#### Scenario: Expected tool was not called

- **WHEN** 某样本 `expected_tools=["search_web"]`，顶层轨迹不含 `search_web`
- **THEN** 该题 `tool_correctness` SHALL 为 `0.0`，即使 `sample_class=empty`

#### Scenario: Duplicate and extra tools do not reduce the score

- **WHEN** 某样本期望一次 `retrieve_kb`，实际调用了两次 `retrieve_kb`，或同时调用了 `retrieve_kb` 与 `search_web`
- **THEN** 该题 `tool_correctness` SHALL 仍为 `1.0`

#### Scenario: Multiple expected tools yield a fraction

- **WHEN** 某样本 `expected_tools=["retrieve_kb","search_web"]`，顶层轨迹只包含 `retrieve_kb`
- **THEN** 该题 `tool_correctness` SHALL 为 `0.5`

#### Scenario: Explicit empty labels expect no tools

- **WHEN** 某样本显式带 `expected_tools=[]`
- **THEN** 无任何工具调用时 `tool_correctness` SHALL 为 `1.0`，有任一工具调用时为 `0.0`

#### Scenario: Duplicate labels are deduplicated

- **WHEN** 某样本 `expected_tools=["retrieve_kb","retrieve_kb"]`
- **THEN** 评分 SHALL 按去重后的单项期望计算，并记录 warning

#### Scenario: Invalid labels fail loud

- **WHEN** 某样本 `expected_tools` 是字符串、包含非字符串/空字符串，或不是列表
- **THEN** 加载 SHALL 抛出带 `q_index` 的 `ValueError`，SHALL NOT 逐字符迭代或静默跳过

#### Scenario: Failed run is N/A

- **WHEN** 某题跑图异常、回答为 `[ERROR] ...`，或回答为空/纯空白
- **THEN** 该题 `tool_correctness` SHALL 为 `null`，SHALL NOT 记为 `0.0`

#### Scenario: Unlabeled testset is N/A

- **WHEN** 用 v5（无 `expected_tools` 字段）运行评测
- **THEN** 所有题 `tool_correctness` SHALL 为 `null`，且评测 SHALL NOT 因缺少标签失败

#### Scenario: Delegation is not exempt

- **WHEN** 某样本期望 `retrieve_kb`，顶层轨迹只有 `delegate_task`
- **THEN** 该题 `tool_correctness` SHALL 为 `0.0`，原因 SHALL 可注明存在委派但期望工具未在顶层出现

#### Scenario: Tool correctness is not a behaviour metric

- **WHEN** 审阅取分路径
- **THEN** `tool_correctness` SHALL 由同步评分函数产出，SHALL NOT 依赖 `evals_iterator` 或 `test_run_*.json`

#### Scenario: Tool correctness never enters the main score

- **WHEN** 审阅主分聚合、门禁判据与 Langfuse score
- **THEN** `tool_correctness` SHALL NOT 出现在 `EVAL_METRIC_COLUMNS`、门禁判据或回写 score 中

### Requirement: Tool correctness is stored and reported for drill-down

`tool_correctness` SHALL 与诊断指标同级写入 `eval_report.detail_json`，并在 CSV/Markdown 报告中可见；`kb_eval` API SHALL NOT 返回该键。

- `detail_json` 的逐题条目 SHALL 含 `tool_correctness`（`0.0`–`1.0` 或 `null`）与 `tool_correctness_reason`（字符串；完全命中时为空串）。N/A SHALL 序列化为 JSON `null`，SHALL NOT 写 `NaN`。
- 报告 SHALL 单列「工具选择」小节，含均值、完全命中题数与计数行：
  - 均值与 `Fully matched: n/m` 的 `m` SHALL 取 `scored`（有标签、非失败、分数非 null）；
  - `qa_count` / `no_label` / `failed` / `metric_error` / `scored` SHALL 分别计数，保证分母可对账；
  - 四类 SHALL 互斥，判定优先级 SHALL 为 `failed` > `no_label` > `metric_error` > `scored`；
  - 未完全命中题 SHALL 列出题号与原因。
- 报告小节 SHALL 标注「只比较工具名与类型（类型固定 FUNCTION）；不比较参数、顺序、调用次数」，SHALL 使用「完全命中题数」表述，SHALL NOT 使用「通过数」。
- CSV SHALL 只写 `tool_correctness` 列，SHALL NOT 新增 `tool_correctness_reason` 列；原因只进 `detail_json` 与报告。
- `--deepeval-only` 模式 SHALL NOT 产出工具正确性；报告 SHALL 显式写「工具选择：未跑（--deepeval-only）」，SHALL NOT 无声缺失。
- `tool_correctness` 的 NaN/空值 SHALL NOT 计入主分 NaN 率门禁。

#### Scenario: Detail carries score and reason

- **WHEN** 一次带标签的评测落库
- **THEN** `detail_json` 每题 SHALL 含 `tool_correctness` 与 `tool_correctness_reason`，`kb_eval` 响应 SHALL NOT 含这两个键

#### Scenario: Report counts are reconcilable

- **WHEN** 报告渲染工具正确性
- **THEN** `qa_count = no_label + failed + metric_error + scored` SHALL 成立，且均值与 `Fully matched` 只使用 `scored` 题

#### Scenario: Unlabeled testset is rendered as N/A

- **WHEN** 用 v5 题集生成报告
- **THEN** 工具正确性段 SHALL 显示 N/A，SHALL NOT 显示 `0.0000`

#### Scenario: CSV keeps the existing schema

- **WHEN** 写出逐题 CSV
- **THEN** CSV SHALL NOT 出现 `tool_correctness_reason` 列；`tool_correctness` 列在有标签时为逐题值，v5 为空

#### Scenario: DeepEval-only reports not run

- **WHEN** 用户使用 `--deepeval-only`
- **THEN** 报告 SHALL 写「工具选择：未跑（--deepeval-only）」，SHALL NOT 静默省略该段
