## MODIFIED Requirements

### Requirement: Judge client is constructed with an explicit sampling and timeout policy

裁判 SHALL 以**显式**参数构造，且**模型与端点 SHALL 显式指定**——SHALL NOT 依赖 DeepEval RAGAS 桥的内建默认模型（默认为 `gpt-3.5-turbo`，该模型已停服）。**可核验方式**：审阅每个桥度量构造处的实参，`model=` SHALL 非空。

裁判采样温度 SHALL 接受由 ragas 按 `n` 分档决定的取值：`n=1` 的三个指标（faithfulness / context_precision / context_recall）为 **0.01**；`answer_relevancy`（`strictness` 默认 3，即 `n=3`）为 **0.3**。该取值**不由本项目控制**（ragas 的 `LangchainLLMWrapper` 会改写底层模型的 temperature 属性），本项目 SHALL NOT 以配置声称"温度为 0"。

温度分档的**事实与不可控性** SHALL 记入报告元数据与评测台账，使跨轮比对者可知该项差异。

裁判 SHALL 配置显式请求超时，取值 SHALL 足以覆盖慢速推理模型。

理由（记明，避免后人误判）：0.01 是有意的工程折中——无限接近确定性，同时避开部分供应商对 `temperature=0` 的特殊逻辑；`answer_relevancy` 取 0.3 是因为该指标需要"生成多个多样化问题再取均值"，压到近 0 会使指标退化。

#### Scenario: Every bridge metric is constructed with an explicit model

- **WHEN** 构造任一桥度量（`RAGAS*Metric`）
- **THEN** 构造实参 SHALL 显式传入 `model=`，SHALL NOT 走 `gpt-3.5-turbo` 默认值

#### Scenario: Temperature regime is recorded, not claimed as zero

- **WHEN** 产出一次评测报告
- **THEN** 报告元数据与台账 SHALL 记录"温度由 ragas 分档（0.01 / 0.3）、本项目不可控"，SHALL NOT 出现"裁判 temperature=0"的表述

### Requirement: Metric failures are explicit, not silently dropped

评测 SHALL 对每条样本独立执行各指标，任一指标失败（超时、额度、解析失败等）时 SHALL 在原样结果行上记录错误信息。

评测 SHALL NOT 因单个指标失败而丢弃该样本行；SHALL NOT 以 `NaN` 静默代替失败（须与「正常计算出的值」可区分）。

**桥特有条款**：RAGAS 桥在模型输出非法 JSON 时会**原样透传 ragas 的 `NaN`**（DeepEval 官方 FAQ 原文：*"`ragas` returns `NaN` when the model emits invalid JSON, and `RagasMetric` wraps it directly"*）。因此评测 SHALL：① 让 `NaN` 显式可见（写入该行的失败/跳过语义，SHALL NOT 静默计入均分）；② 单独统计 `NaN` 计数并在报告中呈现。

`NaN`（裁判故障）与样本类别 `empty`（未检索到材料）SHALL 被区分为**两种不同的跳过语义**。

#### Scenario: A bridge NaN stays visible and is counted

- **WHEN** 某样本的某个桥度量因模型输出非法 JSON 而返回 `NaN`
- **THEN** 该行 SHALL 携带该失败信息，报告 SHALL 单列 `NaN` 计数，且该值 SHALL NOT 参与主分均值

#### Scenario: Judge failure and empty sample are distinguishable

- **WHEN** 一行因裁判故障为 `NaN`、另一行因未检索到材料被跳过
- **THEN** 两者 SHALL 以不同的标记出现在报告中

### Requirement: Reported overall score uses the main-score 口径

`eval_report` 的 `overall_score` SHALL 采用与报告主分一致的口径：**纯 KB 掩码后、且排除 `NaN` 行的四项指标均值**（`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`）。

`answer_correctness` SHALL 不再参与 `overall_score`，且 `kb_eval` API SHALL NOT 再暴露该指标。

判定「通过」的阈值 SHALL 由**单一常量**提供 —— SHALL NOT 在评测落库与前端各硬编码一份。阈值取值 SHALL 在本次口径下**重新标定**（换尺子后历史阈值作废）。

跨口径分数 SHALL NOT 被直接比较：报告与台账 SHALL 标注「口径版本」，历史（modern-0.4.x）与本次（bridge-0.3.x）的分数不得作为回归判据。

#### Scenario: Overall score excludes answer_correctness and NaN rows

- **WHEN** 一次评测产出一行 `eval_report`
- **THEN** 其 `overall_score` SHALL 等于该次**纯 KB 且非 NaN** 样本的四项指标均值

#### Scenario: Evaluation API no longer exposes answer_correctness

- **WHEN** 前端经 `kb_eval` API 取最新评测报告
- **THEN** 响应 SHALL NOT 含 `answer_correctness`

#### Scenario: Thresholds are recalibrated, not inherited

- **WHEN** 本次口径下首次跑出全量分布
- **THEN** 门禁阈值 SHALL 按该分布重新取值，且报告/台账 SHALL 标注口径版本与标定轮次

### Requirement: Testset generation exercises every query path and yields self-contained questions

本要求描述的出题能力（官方 `default_transforms` 全量链 + 源头锚定 + 出题后自足性门）SHALL **继续作为仓库内既有能力保留**（代码与实现 SHALL NOT 被删除）。

该出题路径 SHALL NOT 被评测流程调用：评测入口 SHALL NOT 触发测试集生成，测试集 SHALL 以既有版本文件为输入。

理由（记明，避免后人误判）：出题链依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`；本次评测侧统一下移到 ragas 0.3 线，出题路因此**暂不可用**。保留代码是为了不丢失已验证的能力与经验；重启出题需先解决该依赖（见 design D8）。

#### Scenario: Evaluation does not trigger testset generation

- **WHEN** 运行评测
- **THEN** SHALL NOT 发生测试集生成调用，输入 SHALL 为既有版本文件

#### Scenario: Generation code is retained but unreachable

- **WHEN** 审阅仓库
- **THEN** 出题实现 SHALL 仍存在（含其自足性门与锚点逻辑），且 SHALL 在文件头/台账标注"保留未调用"

## REMOVED Requirements

### Requirement: Evaluation execution uses the modern RAGAS stack

**Reason**：范式反转。该要求规定评测执行 SHALL 使用 RAGAS 现代栈（`@experiment` + `ragas.metrics.collections` + `llm_factory`/`embedding_factory`），并 SHALL NOT 使用 `evaluate()` / legacy 指标 / `Langchain*Wrapper`。本次决策与之**完全相反**：评测入口统一到 DeepEval，RAGAS 指标改由 DeepEval 的内建桥（其内部正是调用 legacy `ragas.metrics` + langchain chat model）承载。此外该要求隐含的 `temperature=0` 承诺在桥下不可实现（见本节 MODIFIED 的裁判条款）。

**Migration**：由本节 ADDED 的「Evaluation scoring entry is DeepEval, with RAGAS metrics reached through its bridge」与「RAGAS dependency is pinned to the 0.3 line」承接；五项指标的承诺由 MODIFIED 的「Reported overall score uses the main-score 口径」收敛为**四项**。

## ADDED Requirements

### Requirement: Evaluation scoring entry is DeepEval, with RAGAS metrics reached through its bridge

评测打分的入口 SHALL 统一为 DeepEval：RAGAS 指标 SHALL 经 DeepEval 内建的 RAGAS 桥（`deepeval/metrics/ragas.py`）调用，指标集 SHALL 为**经典四项**：`RAGASContextualPrecisionMetric`、`RAGASContextualRecallMetric`、`RAGASAnswerRelevancyMetric`、`RAGASFaithfulnessMetric`。

评测 SHALL NOT 使用聚合指标 `RagasMetric`（其实现与官方文档不一致：源码为五项算术平均、含 `ContextualEntitiesRecall`，而文档写四项），SHALL NOT 使用 `RAGASContextualEntitiesRecall`。

每个桥度量 SHALL 以一条单轮样本构造：`input`（问题）/ `actual_output`（回答）/ `expected_output`（参考答案）/ `retrieval_context`（检索材料）。

`RAGASAnswerRelevancyMetric` SHALL 传入显式的 langchain `Embeddings`（其为四项中唯一需要 embedding 的指标）。

#### Scenario: Scoring goes through the bridge with four classic metrics

- **WHEN** 对绑定知识库的样本运行评测
- **THEN** 报告 SHALL 含 `faithfulness` / `answer_relevancy` / `context_precision` / `context_recall` 四项，且四项 SHALL 由桥度量产出

#### Scenario: Aggregate bridge metric is not used

- **WHEN** 审阅评测代码
- **THEN** SHALL NOT 出现对 `RagasMetric` 或 `RAGASContextualEntitiesRecall` 的引用

### Requirement: RAGAS dependency is pinned to the 0.3 line

项目依赖的 `ragas` SHALL 固定为 **`0.3.9`**（精确 pin，与仓库既有 pin 纪律一致）；评测 SHALL NOT 依赖 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment` 等现代栈接口。

`pyproject.toml` SHALL 显式记录为满足该组合所需的 `click` 与 `huggingface_hub` 版本下限，SHALL NOT 让其回落到与 DeepEval 互斥的一侧。

理由（记明，避免后人误判）：`deepeval` 要求 `click<8.4.0`，而 ragas 0.4.3 → `datasets` → `huggingface_hub`(1.27) 要求 `click>=8.4.2`，**交集为空**（实测 `ResolutionImpossible`）；下移到 `0.3.9` 后解析通过（实测落 `click-8.3.3` / `huggingface_hub-1.16.1`）。**弃用 0.4.x 正是解开该依赖死结的手段**。

#### Scenario: Modern stack symbols are absent from the evaluation path

- **WHEN** 审阅评测代码
- **THEN** SHALL NOT 出现 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment` 的引用

#### Scenario: Dependency set resolves

- **WHEN** 按 `pyproject.toml` 重建环境
- **THEN** 依赖解析 SHALL 成功（`pip check` 无新增冲突），且 `ragas` 版本 SHALL 等于 `0.3.9`

### Requirement: Agent behaviour is evaluated with DeepEval native metrics

评测 SHALL 在上下文四指标之外，产出 **Agent 行为轴**的指标，取自 DeepEval 原生族（如 `TaskCompletion` / `StepEfficiency` / `PlanAdherence` / `ToolCorrectness` / `AgentLoopDetection`）。

选用的行为指标的数量 SHALL 收敛（总计 ≤5，遵循 DeepEval 官方 "Choosing Your Metrics" 建议）；具体清单 SHALL 在设计文档中定稿。

行为指标所需的消息轨迹 SHALL 由评测侧从 Agent 的完整消息序列（含 `AIMessage.tool_calls` 与 `ToolMessage`）构造；SHALL NOT 只使用"工具名 + 入参 + 迭代数"的摘要。

#### Scenario: Behaviour metrics appear in the report

- **WHEN** 运行一次评测
- **THEN** 报告 SHALL 含至少一项 DeepEval 原生行为指标

#### Scenario: Trajectory is available at the required granularity

- **WHEN** 行为指标需要轨迹
- **THEN** 评测侧 SHALL 能取到含工具调用与工具产出的完整消息序列

### Requirement: A bounded escape hatch exists when the bridge is unusable

当桥不可用时，评测 SHALL 具备一条**有界**的替代路径：由 DeepEval 负责采样与用例构造（产出单轮样本集合），评分改由项目侧直接调用 ragas 0.3 线完成。

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

- **复用**：`RAGAS_JUDGE_MAX_TOKENS` / `RAGAS_JUDGE_THINKING` SHALL 改嫁到 langchain `ChatOpenAI` 的 `max_tokens` / `extra_body`；`RAGAS_METRIC_MAX_ATTEMPTS` SHALL 保留（桥无内置重试）；`RAGAS_HTTP_TIMEOUT_{CONNECT,READ,WRITE,POOL}` 与 `RAGAS_HTTP_MAX_RETRIES` SHALL 经 `ChatOpenAI` 的 `http_client`（细粒度 `httpx.Timeout`）与 `max_retries=0` **完整保留**——SHALL NOT 因换入口而退化为单值超时或重新打开 SDK 重试。
- **删除**：`build_ragas_judge()` / `build_ragas_embeddings()` SHALL 被删除；`build_native_client()` 在确认无其他消费方后 SHALL 被删除。
- **改写**：`@experiment()` 编排与 `src/cli/eval_scoring.py` 的现代 `build_metrics()` SHALL 被替换；`judge call` / `metric retry` 日志事件的口径 SHALL 随埋点位置变化而重述。
- **ADR**：本次推翻既有裁判 client 政策（含 `temperature=0`），SHALL **追加新 ADR** 并在旧 ADR 的 Status 加**反向指针**（ADR 正文不可变），并更新 ADR 索引表。

**判据边界**：自查 SHALL 以「**评测路径**无悬空引用」为准；出题路对 0.4.x 的依赖按决策**保留**，SHALL 单独标注，不计入本条的违反。

#### Scenario: No dangling references on the evaluation path

- **WHEN** 审阅评测路径代码
- **THEN** SHALL NOT 出现对已删函数（`build_ragas_judge` / `build_ragas_embeddings` / `build_native_client`）的悬空引用

#### Scenario: Reused settings are re-attached to the bridge judge

- **WHEN** 审阅裁判构造处
- **THEN** token 上限与思考开关 SHALL 以 `ChatOpenAI` 参数形式落到桥的裁判上

#### Scenario: Superseded ADR gets a reverse pointer

- **WHEN** 审阅 `docs/adr/0024-*.md`
- **THEN** 其 Status SHALL 含指向新 ADR 的反向指针，且新 ADR SHALL 出现在 ADR 索引中
