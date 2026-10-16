## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: Chunk size comparison

**Reason**: 该要求依赖的固定分块尺寸对比路径（`python -m scripts.compare_chunk`，比较 512 / 768 / 1024）在本仓已不存在；分块配置不再是运行时对比维度。

**Migration**: 检索 / 重排参数组合的对比评测由**既有 CLI** `python -m src.cli.compare_retrieval` 承担（本变更**不**为其新增规格条款）；本变更不保留固定分块尺寸对比。

## ADDED Requirements

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
