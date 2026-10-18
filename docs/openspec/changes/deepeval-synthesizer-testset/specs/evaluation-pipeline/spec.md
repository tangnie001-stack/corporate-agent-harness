## REMOVED Requirements

### Requirement: Testset generation exercises every query path and yields self-contained questions

**Reason**: 该要求描述的是 RAGAS 0.4.x 出题链的保留口径（`default_transforms` 全量转换、`MultiHopAbstractQuerySynthesizer`、RAGAS 出题后自足性门）。出题能力已由 DeepEval `Synthesizer` 重做，判据与实现整体迁入新能力 `testset-generation-deepeval`；继续保留该要求会让两套互斥的出题契约同时生效。

**Migration**: 出题契约见 `openspec/specs/testset-generation-deepeval/spec.md`；评测侧「不触发出题、按固定版本读取」的约束由本文件的 `The testset generation path is guarded against accidental invocation` 与 `Evaluation consumes v6 testsets without changing the scoring contract` 承接。v5 题集保留，作为跨栈对照锚点。

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Evaluation consumes v6 testsets without changing the scoring contract

评测 SHALL 通过 `testset_io.load_testset_samples` 读取 `testset_<kb>_v<N>.json`，v6 新增字段 SHALL NOT 改变现有评分口径。

- 四指标与诊断指标 SHALL 只消费 `user_input` / `reference`；检索上下文 SHALL 来自本次运行的**实际检索结果**，SHALL NOT 使用题集里的 `reference_contexts`（后者只供出题阶段的自足门）。
- `expected_tools` / `hop_type` / `hop_basis` / `evolutions` / `source_file` / `numeric_risk` / `synth_quality` SHALL NOT 进入任何指标计算。
- `tool_correctness` SHALL 继续为 `null`，直到 `tool-correctness-diagnostic` 接入。
- 加载器 SHALL 同时兼容 v5（无新增字段）与 v6（有新增字段）。
- 报告元数据 SHALL 记录 `testset_version`（实际加载到的版本号，含未显式传 `--testset-version` 时）；该字段 SHALL 在**正常与 `--deepeval-only` 两个报告分支**都存在；Markdown 报告头 SHALL 渲染该字段；DB `detail_json` 的逐题条目 SHALL 带 `testset_version`（CSV 不加列，避免破坏现有消费方）。

#### Scenario: v6 fields are inert for scoring

- **WHEN** 用 v6 题集运行评测
- **THEN** 四指标与诊断指标的输入 SHALL 与同题面在 v5 结构下完全一致，`tool_correctness` SHALL 为 `null`

#### Scenario: Testset contexts are not fed to metrics

- **WHEN** 某题的 `reference_contexts` 与本次实际检索结果不同
- **THEN** 评分 SHALL 使用实际检索结果，SHALL NOT 使用 `reference_contexts`

#### Scenario: Report states which testset it used

- **WHEN** 用默认版本（最新）运行评测
- **THEN** 报告元数据与 Markdown 报告头 SHALL 含 `testset_version`，且数值 SHALL 等于实际加载的文件版本；DB `detail_json` 的条目 SHALL 同样带该字段

#### Scenario: v5 remains readable

- **WHEN** 用户指定 `--testset-version 5`
- **THEN** 加载 SHALL 成功，且 SHALL NOT 因缺少 v6 字段而报错
