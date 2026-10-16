# evaluation-pipeline Specification

## Purpose
TBD - created by archiving change mvp-core-features. Update Purpose after archive.
## Requirements
### Requirement: QA test pair coverage

The system SHALL generate the evaluation test set **per knowledge base** from that knowledge base's documents, rather than maintaining a fixed global set of QA pairs.

Test set generation SHALL be driven by `python -m src.cli.eval_ragas --kb-id <uuid> --generate [--size N]`; when `--size` is omitted the size SHALL default to `RAGAS_TEST_SIZE`. Generated test sets SHALL be persisted under the configured test set directory (`RAGAS_TESTSET_DIR`, default `data/ragas/testset/`) with an incrementing version, and evaluation SHALL load the latest version for the knowledge base unless a version is specified.

#### Scenario: Generate a test set for a knowledge base

- **WHEN** the user runs `python -m src.cli.eval_ragas --kb-id <uuid> --generate --size 20`
- **THEN** a versioned test set file for that knowledge base SHALL be written under the configured test set directory with 20 generated QA pairs

#### Scenario: Evaluate against the latest test set

- **WHEN** the user runs evaluation without `--generate`
- **THEN** the system SHALL load the latest test set version for that knowledge base

### Requirement: Evaluation report archival

All RAGAS evaluation results SHALL be saved under the configured report directory (`RAGAS_REPORT_DIR`, default `data/ragas/reports/`) as a timestamped CSV file `ragas_eval_<timestamp>.csv` together with a sibling Markdown summary `ragas_eval_<timestamp>.md`.

Evaluation SHALL be invoked via `python -m src.cli.eval_ragas`. The Markdown summary SHALL list the date and configuration, the QA pair count, per-question metric scores, and aggregate metric averages.

#### Scenario: Evaluation saves CSV and Markdown

- **WHEN** the user runs `python -m src.cli.eval_ragas --kb-id <uuid>`
- **THEN** a CSV file and a Markdown summary SHALL be created under the configured report directory

### Requirement: Evaluation passes without crash on empty KB
The evaluation system SHALL handle the case where the knowledge base has no documents gracefully, logging a clear error message and exiting with a non-zero exit code.

#### Scenario: Empty KB evaluation
- **WHEN** running evaluation against a KB with no documents
- **THEN** the system SHALL log "Knowledge base is empty" and exit with code 1

### Requirement: NLI context uses shared entity rendering

The RAGAS evaluation SHALL use `RAGContext.to_prompt_text()` (the same rendering as production) for the NLI context, so entity metadata (company / report_period / sec_code / person / currency / report_type) present in chunk metadata SHALL be visible to the faithfulness NLI check.

#### Scenario: Faithfulness context includes time anchor
- **WHEN** a table chunk has `entities={company: 东软集团, report_period: 2025年第一季度}` and the statement references "2025年第一季度"
- **THEN** the NLI context SHALL include the `report_period` entity, giving the statement an anchor to verify against

#### Scenario: Evaluation output unchanged for empty entities
- **WHEN** evaluation runs against a KB whose chunks have no entity metadata
- **THEN** the NLI context SHALL match the pre-change format, and the evaluation SHALL still produce a report without crashing

### Requirement: Current date in evaluation context

system prompt SHALL 在**生产路径与评估路径**下都包含当前日期（例："今天是 2026年8月9日"）。

日期注入 SHALL 由 system prompt 组装器的**唯一注入点**承担：`_with_current_date`，在段拼装完成、产出 system 消息之前应用；按 `Asia/Shanghai` 计算，且幂等（日期行已存在时不重复追加）。SHALL NOT 由 `PromptManager` 另行追加 —— 经典 RAG 与 agent 两条路径经同一组装入口（见 `<prompt-composition>`「经典 RAG 与 agent 两条路径共用同一套段」），日期因此只出现一次。

#### Scenario: Current date present in system prompt
- **WHEN** a generation or evaluation run assembles the system prompt
- **THEN** the resulting system message SHALL contain today's date, enabling relative-time queries ("本报告期", "今年") to be anchored

### Requirement: Evaluation captures retrieval material with source kind

评测链路 SHALL 为每次评测的单题生成注入请求上下文，使被测 Agent 经工具（知识库检索 / 联网搜索）收集的检索材料能被评测读取，而不再走「评测入口从不设置上下文」的失效路径。

每条被读取的检索材料 SHALL 携带其来源类型（知识库 / 联网）。评测 SHALL 仅让**纯知识库样本**（检索材料不含联网块）参与常规上下文指标（主分）；**含联网材料的样本** SHALL 被标记为「降级联网」并从主分中排除，且其是否降级联网 SHALL 记入诊断元数据。

依赖检索材料的上下文类指标（`faithfulness` / `context_precision` / `context_recall`）SHALL 在无材料样本上被跳过；**不依赖上下文的指标（`answer_relevancy` 与答案正确性）SHALL 照常计算**。

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
- **AND** 不依赖上下文的 `answer_relevancy` 与答案正确性 SHALL 照常计算

#### Scenario: Sample composition and retrieval coverage reported

- **WHEN** 一次评测完成
- **THEN** 报告 SHALL 列出「纯 KB / 含 web / 无检索」三类样本计数与检索覆盖率（有检索样本占比）

#### Scenario: Gate uses the main score

- **WHEN** 以 `--gate` 运行评测
- **THEN** 门禁 SHALL 依据主分（纯 KB 样本的指标均值）判定，而非全量样本均值

#### Scenario: Gate fails on low coverage or empty main score

- **WHEN** 检索覆盖率低于阈值，或主分样本数为 0（无纯 KB 样本）
- **THEN** 门禁 SHALL 判定失败

### Requirement: Answer correctness metric

评测 SHALL 用与现有四指标同一条 `evaluate()` 路径的答案正确性指标，对最终回答与参考答案（test set 的 `reference`）计算，作为区别于忠实度（faithfulness）与相关性（answer_relevancy）的独立维度。测试集样本的 `reference` 恒存在。

#### Scenario: Correctness computed for each sample

- **WHEN** 评测运行且被测系统给出回答
- **THEN** 评测结果为每题输出答案正确性分数

### Requirement: Agent trajectory captured as diagnostic metadata

评测 SHALL 采集被测 Agent 的完整消息轨迹（含每次工具调用及其入参、迭代次数），作为诊断元数据随评测结果保存，供低分样本下钻。

工具调用序列 SHALL NOT 作为评分指标 —— 在常规检索轮场景其期望路径近似恒定，不具区分度。

#### Scenario: Trajectory available for drill-down

- **WHEN** 某样本的评测得分偏低
- **THEN** 可从评测结果读到该样本的工具调用序列与迭代次数

### Requirement: Evaluation execution uses the modern RAGAS stack

评测执行 SHALL 使用 RAGAS 现代栈：编排 SHALL 用 `@experiment` 装饰器 + `ragas.Dataset`；指标 SHALL 取自 `ragas.metrics.collections`；LLM 与 embedding 客户端 SHALL 经 `llm_factory` / `embedding_factory` 构造。

评测 SHALL NOT 使用已弃用的 `evaluate()` / `aevaluate()`、`ragas.metrics`（legacy 指标模块）或 `LangchainLLMWrapper` / `LangchainEmbeddingsWrapper`。

评测指标集 SHALL 保持五项不变：`faithfulness`、`answer_relevancy`、`context_precision`、`context_recall`、`answer_correctness`。

#### Scenario: Evaluation runs without deprecated-API warnings

- **WHEN** 运行一次评测
- **THEN** SHALL NOT 出现来自 `evaluate()` / legacy 指标导入 / `Langchain*Wrapper` 的 `DeprecationWarning`

#### Scenario: All five metrics are produced

- **WHEN** 对绑定知识库的一条样本运行评测
- **THEN** 报告 SHALL 含 `faithfulness` / `answer_relevancy` / `context_precision` / `context_recall` / `answer_correctness` 五项

### Requirement: Judge client is constructed with an explicit sampling and timeout policy

裁判 LLM 客户端 SHALL 以显式采样参数构造：构造时 SHALL 传 `temperature=0`，且 SHALL NOT 传入 `top_p`（RAGAS 内建默认 `temperature=0.01` / `top_p=0.1` 会被部分模型以 400 拒绝）。**可核验方式**：审阅裁判 client 构造处的实参（读代码即可判定，无需运行时）。

裁判 SHALL 配置显式请求超时，取值 SHALL 足以覆盖慢速推理模型（不得依赖过短默认值）。

裁判模型选型 SHALL 以「结构化输出可靠性」为判据，即 SHALL 有对候选裁判的活体抽样验证（覆盖裁判实际使用的每个指标），而非仅以「请求未报错」为准。

#### Scenario: Sampling parameters are explicit

- **WHEN** 构造裁判客户端
- **THEN** 构造 SHALL 传 `temperature=0`，且 SHALL NOT 传 `top_p`

#### Scenario: Judge selection is backed by live evidence

- **WHEN** 为 `judge` 角色选定模型
- **THEN** 该模型 SHALL 已通过覆盖全部裁判指标的活体抽样验证，且验证结果记入设计文档

### Requirement: Metric failures are explicit, not silently dropped

评测 SHALL 对每条样本独立执行各指标，任一指标失败（超时、额度、解析失败等）时 SHALL 在原样结果行上记录错误信息。

评测 SHALL NOT 因单个指标失败而丢弃该样本行；SHALL NOT 以 `NaN` 静默代替失败（须与「正常计算出的值」可区分）。

#### Scenario: A failed metric stays visible on its row

- **WHEN** 某样本的某个指标调用失败
- **THEN** 该样本行 SHALL 仍出现在报告中，且 SHALL 携带该失败的错误信息

#### Scenario: No row is silently dropped

- **WHEN** 评测 N 条样本
- **THEN** 报告 SHALL 含 N 行（不因失败少行）

### Requirement: Evaluation retrieval mirrors production scope

白名单（`RAGAS_DOC_WHITELIST`）SHALL 仅约束**出题**的输入文档范围。评估侧检索 SHALL 镜像生产口径——在被测 Agent 绑定的**整个知识库**内检索，SHALL NOT 人为按出题白名单收窄检索范围。

理由（记明，避免后人"修回"）：① 检索由题面驱动（`retrieval.search(query, kb_id, …)`），自足题的自然检索即应命中对应文档；② 检索命中白名单外文档是**切块/检索质量的可观测信号**，过滤会掩盖它；③ 人为收窄会让评测测一个**生产里不存在**的系统，并可能使 `context_precision` 虚高。

#### Scenario: Evaluation retrieval is not artificially scoped

- **WHEN** 白名单含 2 份文档、知识库共 4 份文档，且运行评测
- **THEN** 评估侧检索 SHALL 在知识库全部 4 份文档内进行（与生产一致），SHALL NOT 仅限白名单的 2 份

#### Scenario: Whitelist still bounds testset generation

- **WHEN** 运行测试集生成
- **THEN** 出题 SHALL 仅使用白名单文档

### Requirement: Reported overall score uses the main-score 口径

`eval_report` 的 `overall_score` SHALL 采用与报告主分一致的口径：**纯 KB 掩码后的五项指标均值**（含 `answer_correctness`）。SHALL NOT 使用仅含四项指标、且未按纯 KB 掩码的旧加权口径。

评测 API SHALL 暴露全部五项指标（含 `answer_correctness`）。

判定「通过」的阈值 SHALL 由**单一常量**提供 —— SHALL NOT 在评测落库与前端各硬编码一份。

#### Scenario: Overall score includes answer_correctness on pure-KB samples

- **WHEN** 一次评测产出一行 `eval_report`
- **THEN** 其 `overall_score` SHALL 等于该次**纯 KB 样本**五项指标的均值

#### Scenario: Evaluation API exposes all five metrics

- **WHEN** 前端经 `kb_eval` API 取最新评测报告
- **THEN** 响应 SHALL 含 `answer_correctness`

#### Scenario: Pass threshold is defined once

- **WHEN** 评测落库判定 `passed`、前端渲染通过/不通过
- **THEN** 两者 SHALL 引用同一阈值常量

### Requirement: Testset generation exercises every query path and yields self-contained questions

出题 SHALL 使用官方 `default_transforms` 全量转换链（含 `ThemesExtractor` 与 `CosineSimilarityBuilder`），使 `MultiHopAbstractQuerySynthesizer` 可正常运行，而 SHALL NOT 因缺件被静默丢弃。

出题 SHALL 通过两条**可核验**机制保证题面自足——明确指出公司名称与报告期，SHALL NOT 使用「本报告期」「该公司」等依赖原文的指代：

1. **源头锚定**：送入出题 prompt 的每段上下文 SHALL 携带其所属文档的公司名与报告期（取自分块元数据，如 `company` / `report_period` 及 `year`/`quarter`/`source` 兜底）。**可核验方式**：读测试集样本的 `reference_contexts`，其首行 SHALL 含公司名与报告期。
2. **出题后自足性门**：生成的每条题面 SHALL 经自足性校验；不自足者 SHALL 被改写成自足题或被丢弃，SHALL NOT 原样进入测试集。**可核验方式**：统计测试集全部题面命中 KB 内公司名的比例，SHALL 为 100%。

理由（记明，避免后人"修回"）：`llm_context` 只是官方 prompt 的**题型建议槽位**（软引导）。实测证据：21 条生成题中 12 条题面不含公司名，且这 12 条的 `reference_contexts` **一个公司名都没有**——上下文自称「本公司」，模型无从消解。故单靠 `llm_context`（原方案）不可达成；**不**复活官方 v0.2.0 已移除的旧 `QuestionFilter`。

#### Scenario: Abstract synthesizer is no longer silently dropped

- **WHEN** 运行测试集生成
- **THEN** SHALL NOT 出现 `Skipping multi_hop_abstract_query_synthesizer … Cannot form clusters`，且生成的测试集 SHALL 含 abstract 出题器产出的题

#### Scenario: Generation context carries the entity anchor

- **WHEN** 分块元数据含公司名与报告期（或多公司语料的任意一块）
- **THEN** 送入出题 prompt 的上下文 SHALL 携带该公司的名称与报告期

#### Scenario: Generated questions are self-contained

- **WHEN** 知识库含多家公司文档并生成测试集
- **THEN** 每条题面 SHALL 能唯一指向某公司与其报告期（不含依赖原文的指代）
