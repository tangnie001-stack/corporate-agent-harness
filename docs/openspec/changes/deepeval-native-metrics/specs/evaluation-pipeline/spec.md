## MODIFIED Requirements

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

### Requirement: NLI context uses shared entity rendering

The evaluation SHALL use `RAGContext.to_prompt_text()` (the same rendering as production) for the context shown to the native `Faithfulness` judge, so entity metadata (company / report_period / sec_code / person / currency / report_type) present in chunk metadata SHALL be visible to it.

#### Scenario: Faithfulness context includes time anchor

- **WHEN** a table chunk has `entities={company: 东软集团, report_period: 2025年第一季度}` and the statement references "2025年第一季度"
- **THEN** the judge's context SHALL include the `report_period` entity, giving the statement an anchor to verify against

#### Scenario: Evaluation output unchanged for empty entities

- **WHEN** evaluation runs against a KB whose chunks have no entity metadata
- **THEN** the context SHALL match the pre-change format, and the evaluation SHALL still produce a report without crashing

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

- **(a) 掩码跳过型**：`empty` 样本的 context 指标被项目主动掩码为 `NaN`。天然不在纯 KB 分母里。
- **(b) 非掩码型**：DeepEval 原生指标可能**合法**返回 `NaN`（如无可校验陈述时的 `Faithfulness`），也可能因裁判异常/解析失败而失败。二者在指标出口**不可区分** ⇒ SHALL NOT 依据请求级日志做子分类（该分类不可实现）。

因此评测 SHALL：① 让 `NaN` 显式可见、且**掩码 / 非掩码在报告中标记不同**（SHALL NOT 静默计入均分）；② **单独统计非掩码 `NaN` 计数**并在报告中呈现；③ **以「非掩码 `NaN`」为判据执行有界重试**（`EVAL_METRIC_MAX_ATTEMPTS`）；**限流类（HTTP 429 / `openai.RateLimitError`）SHALL 与其它失败一样由外层有界重试**——换用原生指标后，不再有 RAGAS 框架层的 429 重试，原"429 不重试"的豁免依据已消失；④ 失败留证 SHALL 由**框架内部请求级日志**承载（见「Framework-internal LLM calls…」）；**该日志只做留证，SHALL NOT 被当作分类依据**；⑤ 若该日志完全不可用，报告 SHALL **显式标注"请求级日志不可用"**。

**空回答的预检**：跑图失败（`answer=""`）的样本在进入指标评分前 SHALL 直接记 `NaN` 并跳过该题四指标，SHALL NOT 让 `MissingTestCaseParamsError` 触发无意义的三次重试。

**接受的代价（明写，勿当 bug）**：合法缺值型 `NaN` 会被计入分子、并触发一次有界重试（对该情形是**无收益的浪费**，不是错误）。阈值 10%（76 格允许 7 格）即为吸收该水位而设；**v6 定稿后的首轮全量**跑出真实分布后 SHALL 复核该假设并定稿（v5 分布仅作基线，不用于标定）。

`NaN`（指标出不来值）与样本类别 `empty`（未检索到材料）SHALL 被区分为**两种不同的跳过语义**。

#### Scenario: A failed metric stays visible on its row

- **WHEN** 某样本的某个指标调用失败
- **THEN** 该样本行 SHALL 仍出现在报告中，且 SHALL 携带该失败的错误信息

#### Scenario: No row is silently dropped

- **WHEN** 评测 N 条样本
- **THEN** 报告 SHALL 含 N 行（不因失败少行）

#### Scenario: A non-masked NaN stays visible and is counted

- **WHEN** 某纯 KB 样本的某个指标返回 `NaN`
- **THEN** 该行 SHALL 携带失败信息，该非掩码 `NaN` SHALL 被计数，且 SHALL NOT 参与主分均值

#### Scenario: Masked NaN is excluded by the denominator

- **WHEN** 某 `empty` 样本的 context 指标被掩码为 `NaN`
- **THEN** 该格 SHALL NOT 计入 NaN 率分子/分母，且 SHALL 在报告中以**掩码**语义标记

#### Scenario: No sub-classification of non-masked NaN is attempted

- **WHEN** 审阅实现
- **THEN** SHALL NOT 出现"按请求级日志把 NaN 分为合法缺值与裁判失败"的逻辑

#### Scenario: Retry is keyed on the non-masked NaN

- **WHEN** 某题某指标首次返回非掩码 `NaN`
- **THEN** 评测 SHALL 按 `EVAL_METRIC_MAX_ATTEMPTS` 重试该指标（判据为非掩码 `NaN`）

#### Scenario: Rate-limit failures are retried by the outer layer

- **WHEN** 某格失败于 HTTP 429
- **THEN** 外层 SHALL 仍按 `EVAL_METRIC_MAX_ATTEMPTS` 有界重试（不再有 RAGAS 框架层重试可依赖）

#### Scenario: Empty answer skips scoring without retries

- **WHEN** 某题跑图失败、回答为空
- **THEN** 该题四指标 SHALL 直接记 `NaN`，SHALL NOT 触发三次重试

### Requirement: Reported overall score uses the main-score 口径

`eval_report` 的 `overall_score` SHALL 采用与报告主分一致的口径：**纯 KB 掩码后、且排除 `NaN` 行的四项指标均值**（`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`）。

`answer_correctness` SHALL 不再参与 `overall_score`，且 `kb_eval` API SHALL NOT 再暴露该指标。

判定「通过」的阈值 SHALL 由**单一常量**提供 —— SHALL NOT 在评测落库与前端各硬编码一份。阈值取值 SHALL 在 **v6 定稿后**重新标定（换尺子后历史阈值作废；v5 分布仅作基线）。

**Baseline 语义（新口径首轮）**：首次以 `deep-eval-native` 口径跑出的全量 SHALL 以 `run_type="baseline"` 落库；`run_type` SHALL 由 CLI 参数 `--run-type` 写入（默认 `manual`），SHALL NOT 由"是否首轮"自动推断。`kb_eval` 对该 `run_type` SHALL 返回 `passed=None`（SHALL NOT 用未标定的阈值判红/绿），前端 SHALL 以**三态**渲染：`passed=null` 显示"未标定"（灰色，SHALL NOT 显示 ✓/✗），`true` 显示通过，`false` 显示不通过；前端 SHALL NOT 用 `overall_score >= 阈值` 自行重算通过态。

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
- **THEN** KB 徽标与评估弹窗 SHALL 显示"未标定"（灰色），且 SHALL NOT 显示 ✓ / ✗，也 SHALL NOT 依据 `overall_score` 重新判定

#### Scenario: Run type comes from the CLI argument

- **WHEN** 用户以 `--run-type baseline` 运行评测
- **THEN** 新写入的 `eval_report` 行 SHALL 为 `run_type="baseline"`；未传该参数时 SHALL 为 `manual`

#### Scenario: Pass threshold is defined once

- **WHEN** 评测落库判定 `passed`、前端渲染通过/不通过
- **THEN** 两者 SHALL 引用同一阈值常量

#### Scenario: Thresholds are recalibrated after v6

- **WHEN** **v6 定稿后**的全量分布产出（v5 分布仅作基线记录）
- **THEN** 门禁阈值 SHALL 按该分布重新取值，且报告/台账 SHALL 标注口径版本与标定轮次

### Requirement: Testset generation exercises every query path and yields self-contained questions

出题能力 SHALL 由**后续独立 change `deepeval-synthesizer-testset`** 承接（DeepEval `Synthesizer`），本 change SHALL 不再保留 ragas 出题实现。

- `src/cli/eval_ragas_generate.py` SHALL 被删除（含 `run_generate` / `_build_transforms` / `_ensure_vertexai_stub`）。
- 仍被评测路径使用的**测试集加载器** SHALL 迁入 `src/cli/testset_io.py`，且 SHALL 返回**完整 sample**（含为 v6 预留的 `expected_tools` 字段），SHALL NOT 连带 ragas 依赖。
- 评测输入 SHALL 仍为**固定的既有版本文件**（`testset_<kb>_v5.json`）；评测与出题 SHALL 保持解耦。
- `POST /api/ragas/generate` SHALL 下线（410 + 前迁路径），SHALL NOT 让用户撞到裸 `ImportError`。

#### Scenario: Evaluation does not trigger testset generation

- **WHEN** 运行评测
- **THEN** SHALL NOT 发生测试集生成调用，输入 SHALL 为既有版本文件

#### Scenario: Generation is blocked with a forward path

- **WHEN** 用户运行 `--generate` 或调用 `/api/ragas/generate`
- **THEN** 系统 SHALL 以明确错误退出/返回，给出前迁路径（DeepEval `Synthesizer`）

#### Scenario: Loader stays live after module removal

- **WHEN** 审阅仓库
- **THEN** `src/cli/eval_ragas_generate.py` SHALL 已删除，且评测路径 SHALL 通过 `src/cli/testset_io.py` 加载测试集

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

- **复用（改嫁到 provider 层）**：`JUDGE_MAX_TOKENS` / `JUDGE_THINKING` SHALL 经 `build_chat("judge", max_tokens=…, thinking=…)` 传入；`EVAL_METRIC_MAX_ATTEMPTS` SHALL 保留（判据见「Metric failures」）；`JUDGE_HTTP_TIMEOUT_*` 与 `JUDGE_HTTP_MAX_RETRIES` SHALL 经 **`http_async_client` + `max_retries=0`** 保留。
- **删除**：`build_ragas_judge()` / `build_ragas_embeddings()` / `build_native_client()` SHALL 已删除；`ragas` 依赖 SHALL 已从 `pyproject.toml` 移除；所有 `RAGAS_*` 配置 SHALL 已重命名（`JUDGE_*` / `EVAL_*`）或删除（出题专用）。
- **旧配置不兼容**：启动期 SHALL 检测残留的 `RAGAS_*` 环境变量并**立即失败**，错误信息 SHALL 列出「旧名 → 新名」映射；SHALL NOT 回退读取旧名。环境模板 SHALL 只写新名（迁移对照表放 runbook）。
- **旧目录保留**：部署脚本 SHALL 只创建 `data/deepeval/`，SHALL NOT 自动删除旧 `data/ragas/`；runbook SHALL 提示人工确认后清理。
- **命名收尾**：CLI 模块 SHALL 为 `src/cli/eval_deepeval.py`，测试 SHALL 为 `test_eval_deepeval*` / `test_eval_native_*`，台账 SHALL 为 `docs/agents/eval-ledger.md`，Langfuse `--session-id` 默认值 SHALL 为 `deepeval_eval_session`。
- **改写**：`eval_scoring.build_metrics()` SHALL 改为原生四指标；`judge call` / `metric retry` 日志事件口径 SHALL 随之重述；`--generate` 文案 SHALL 指向 DeepEval `Synthesizer`。
- **ADR**：SHALL 追加新 ADR（0027）取代 ADR-0026 的桥相关部分与 ADR-0022/0023 的出题决策，并在旧 ADR 的 Status 加**反向指针**、更新索引。

**判据边界**：自查 SHALL 以「**最终态**无悬空引用」为准，SHALL NOT 要求每个中间提交都无悬空引用——删除动作 SHALL 与"调用点改写 + 受影响测试"**同一提交**。

#### Scenario: No dangling references on the evaluation path

- **WHEN** 审阅评测路径代码
- **THEN** SHALL NOT 出现对 `build_ragas_judge` / `build_ragas_embeddings` / `build_native_client` / `deepeval.metrics.ragas` 的引用

#### Scenario: Reused settings land on the judge

- **WHEN** 审阅裁判构造处
- **THEN** token 上限、思考开关、细粒度超时 SHALL 以 `ChatOpenAI` 参数形式落到裁判上

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
- **分子** = **纯 KB 格里的全部 `NaN`**（掩码类由分母天然排除；合法缺值型与失败型一并计入）。

NaN 率超阈值时，报告 SHALL 给出「**建议启用逃生门**」的提示；**是否启用由人决定**，启用 SHALL 在台账记录原因与生效轮次。

理由（记明，避免后人"修回"）：阈值过严会让门禁常态失败、过松则失去意义；依据单薄这一点必须写进规格，避免后人把"10%"当作经实测标定的数字。

#### Scenario: NaN rate within threshold does not fail the gate

- **WHEN** 全量评测的 NaN 率为 3%（≤10%）
- **THEN** 门禁 SHALL NOT 因 NaN 判失败，且报告 SHALL 单列该 NaN 率

#### Scenario: NaN rate above threshold fails the gate

- **WHEN** NaN 率为 15%（>10%）
- **THEN** 门禁 SHALL 失败，且报告 SHALL 给出「建议启用逃生门」的提示

### Requirement: The testset generation path is guarded against accidental invocation

出题实现 SHALL 已迁移到后续 change（DeepEval `Synthesizer`），本仓库 SHALL NOT 保留 ragas 出题代码。CLI 的 `--generate` 路径 SHALL **显式失败**并给出指引（说明出题已迁移、指向 `deepeval-synthesizer-testset`），SHALL NOT 让用户撞到裸 `ImportError`。

#### Scenario: Generating a test set fails with guidance

- **WHEN** 用户运行 `--generate`
- **THEN** CLI SHALL 以明确错误信息退出，并指向 DeepEval `Synthesizer` 与后续 change

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

**行为轴判官调用的归因边界（明写，避免误期待）**：DeepEval 行为指标在 `evals_iterator` **耗尽之后**才评分，届时循环上下文已结束 ⇒ 其判官调用**不**经 `http_async_client` 的 hooks 做逐题 `trace_id` 归因（该层 `trace_id` 记为空）；行为轴的逐题失败证据 SHALL 由其 `test_run_*.json` 的 `testCases[].metricsData[].reason` 承载，并在报告「Agent 行为轴」段呈现。

实现手段 SHALL 为：在裁判的 **`http_async_client`**（`httpx.AsyncClient`）上挂**请求级日志**（httpx event hooks）。**当前落地记录** `url` / `status` / `body_bytes` / `json_ok` / `finish_reason`（响应体不可解析时另有 `body_exc`），**仅失败时产日志行**；**目标形态**另含模型 / 耗时 / 请求体摘要 / 响应体摘要。**SHALL 挂在 `http_async_client` 而非 `http_client`**。

**hook SHALL 为 `async def` 且以 `await response.aread()` 读体**。

**落地现状（避免归档后留下未满足的 SHALL）**：当前 hooks 只记录上述字段，且仅在失败时产日志行；模型与耗时分别由 `eval model init` 与四指标路径的 `judge call` 承载。**请求体摘要 / 响应体摘要 / 成功路径日志尚未落地**。因此「A malformed 200 response is diagnosable from the logs」**当前不成立**。

**该日志只做留证**：SHALL NOT 被当作 `NaN` 子分类的依据。

`trace_id` SHALL 经**显式闭包**绑定进 event hook，SHALL NOT 依赖 contextvar 传递；裁判 client 跨样本复用 ⇒ 闭包 SHALL 绑到**可变证据桶**（题级串行，安全）。

**验收范围（明写）**：本条只覆盖**经我们提供的 `http_async_client` 发出的 LLM 请求**；行为指标的 **DeepEval span 树**仍不进日志。

#### Scenario: Internal calls carry the question's trace id

- **WHEN** 某题的裁判调用在指标内部发生（含重试与多次调用）
- **THEN** 对应日志行 SHALL 携带该题的 `trace_id`

#### Scenario: The logging hook rides on the async client used for the timeout

- **WHEN** 审阅裁判客户端构造处
- **THEN** 请求级日志 SHALL 挂在用于细粒度超时的**同一个 `http_async_client`** 上，且 hook SHALL 为 `async def`

#### Scenario: Hook wiring is proven by a smoke assertion

- **WHEN** 实施期跑 1 题真调裁判
- **THEN** SHALL 断言响应 hook 触发 **> 0** 次，且能取到状态码与 `finish_reason`

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

### Requirement: QA test pair coverage

评测测试集 SHALL 仍然**按知识库**从该库文档生成（而非维护一份固定全局 QA 集），且评测 SHALL 默认载入该知识库**最新版本**的测试集。**但本次 `--generate` 的 CLI 驱动路径 SHALL 显式失败**（原因：出题已迁移到 DeepEval `Synthesizer`），SHALL 给出前迁路径而非裸 `ImportError`。

#### Scenario: Evaluation loads a fixed-version testset

- **WHEN** 运行评测
- **THEN** 输入 SHALL 为既有 `testset_<kb>_v<N>.json`，且不触发出题生成

## REMOVED Requirements

### Requirement: RAGAS dependency is pinned to the 0.3 line

**Reason**: 评测已改用 DeepEval 原生四指标，`ragas` 不再被任何运行路径引用；保留该依赖与"0.3 线"约束会误导后续维护者。

**Migration**: 删除 `pyproject.toml` 的 `ragas==0.3.9`；`click` / `huggingface_hub` 的 pin 暂留（供 DeepEval 与后续 `Synthesizer` 使用），待 v6 落地后按实际 import 清理。依赖策略变更记入 ADR-0027。

## ADDED Requirements

### Requirement: Diagnostic metrics do not affect the main score

评测 SHALL 提供三项**诊断指标**，仅作观测，SHALL NOT 进入主分、门禁或 `eval_report` 的四指标列：

- `ContextualRelevancyMetric`：不需要 `expected_output`；评分层 SHALL 只对**前 8 条** `retrieval_context` 判定（常量 `EVAL_RELEVANCY_MAX_CONTEXTS`），SHALL NOT 原地修改共享 test case；报告 SHALL 标注"仅判前 8 条（并集顺序，非最相关 8 条）"。
- `NonAdviceMetric(advice_types=["financial"])`：财务合规口径。
- `ToolCorrectnessMetric`：本 change SHALL NOT 构造（现有题集无 `expected_tools`）；待 `deepeval-synthesizer-testset` 产出该标注后启用，在此之前其值 SHALL 记 `N/A`。

诊断指标的 `NaN` SHALL 与四指标的 `NaN` 分开计数，SHALL NOT 影响 NaN 率门禁。

诊断指标的逐题值 SHALL 写入 `eval_report.detail_json`，键名使用**裸指标名**（`contextual_relevancy` / `non_advice` / `tool_correctness`），与四指标同级；`tool_correctness` 在本 change SHALL 写 `null`（键存在、值为空）。`kb_eval` API SHALL NOT 返回这三个键（只存不读）。"不进主分"SHALL 由 `EVAL_METRIC_COLUMNS` 白名单保证，SHALL NOT 依赖键名前缀。

#### Scenario: Diagnostic metrics are not part of the main score

- **WHEN** 审阅主分聚合与门禁
- **THEN** `contextual_relevancy` / `non_advice` / `tool_correctness` SHALL NOT 出现在 `EVAL_METRIC_COLUMNS` 或门禁判据中

#### Scenario: Diagnostics are stored for drill-down but not exposed

- **WHEN** 一次评测落库
- **THEN** `detail_json` 的逐题条目 SHALL 含三个裸键名，`tool_correctness` SHALL 为 `null`，且 `kb_eval` 响应 SHALL NOT 含这些键

#### Scenario: Relevancy is truncated and labeled

- **WHEN** 生成评测报告
- **THEN** `contextual_relevancy` 的分数 SHALL 基于前 8 条 context，且报告 SHALL 含截断说明

#### Scenario: ToolCorrectness is N/A without labels

- **WHEN** 对无 `expected_tools` 的题集运行评测
- **THEN** `tool_correctness` SHALL 记 `N/A`，SHALL NOT 触发 fail-loud
