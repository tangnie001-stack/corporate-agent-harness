## ADDED Requirements

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

出题 SHALL 通过 `llm_context` 向出题 prompt 传达约束：问题须**自足**——明确指出公司名称与报告期，SHALL NOT 使用「本报告期」「该公司」等依赖原文的指代。

#### Scenario: Abstract synthesizer is no longer silently dropped

- **WHEN** 运行测试集生成
- **THEN** SHALL NOT 出现 `Skipping multi_hop_abstract_query_synthesizer … Cannot form clusters`，且生成的测试集 SHALL 含 abstract 出题器产出的题

#### Scenario: Generated questions are self-contained

- **WHEN** 知识库含多家公司文档并生成测试集
- **THEN** 每条题面 SHALL 能唯一指向某公司与其报告期（不含依赖原文的指代）
