## ADDED Requirements

### Requirement: Evaluation can run with DeepEval metrics only

评测入口 SHALL 支持只跑 DeepEval 原生指标（Agent 行为轴）而跳过 RAGAS 四指标的模式，用于在 RAGAS 主分口径未定期间快速迭代验收 DeepEval 功能。

- 开关 SHALL 为 CLI 参数 `--deepeval-only`，默认关闭；关闭时行为与现状**完全一致**。
- 开启时 SHALL NOT 构造四指标度量、SHALL NOT 调用四指标评分。
- 开启时报告 SHALL 显式标注 `RAGAS: 未跑（--deepeval-only）`，SHALL NOT 输出四指标数值行，SHALL NOT 把缺失写成 `0`。
- 开启时 SHALL NOT 写入 `eval_report` 表（其四指标列与 `overall_score` 非 nullable、默认 `0.0`，写入会把"未评"显示成"0 分且未通过"）。
- 开启时 SHALL NOT 执行质量门禁，且与 `--gate` 同时出现 SHALL 在启动时报错。
- 行为轴段、Markdown 报告落盘、`--limit` 冒烟 SHALL 仍可用；CSV SHALL NOT 写入（其列结构绑定四指标）。
- 报告头 SHALL 明写"行为轴仅作诊断观测、无门禁语义"。

#### Scenario: DeepEval-only run skips RAGAS scoring

- **WHEN** 以 `--deepeval-only` 运行评测
- **THEN** 退出码 0；报告含「Agent 行为轴」段与 `RAGAS: 未跑（--deepeval-only）` 标注；`eval_report` 表无新行；不生成 CSV

#### Scenario: Default run is unchanged

- **WHEN** 不加 `--deepeval-only`
- **THEN** 四指标与行为轴照常执行，报告、CSV、落库与现状一致

#### Scenario: Gate and DeepEval-only are mutually exclusive

- **WHEN** 同时传 `--deepeval-only` 与 `--gate`
- **THEN** 启动即报错（非零退出），不执行任何评测
