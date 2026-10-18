## ADDED Requirements

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

## MODIFIED Requirements

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
