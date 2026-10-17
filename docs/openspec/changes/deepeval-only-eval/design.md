## Context

`deepeval-bridge-eval` 已完成换尺子与行为轴并归档。RAGAS 四指标在多轮 Agent 上的结构性缺陷已由三次证据确认（定义、探针、上游对照，见 proposal）。用户决定先收敛 DeepEval 自闭环，RAGAS 主分角色待定。

本变更只解决一个工程阻碍：**评测入口目前无法只跑 DeepEval**。21 题全量约 56 分钟，其中四指标评分占大头；不解决它，DeepEval 功能的迭代验收无法快速进行。

## Goals / Non-Goals

**Goals**
- 一个显式开关，让评测只跑「跑图 + Agent 行为轴 + 报告」。
- 该模式的输出（报告/台账）语义诚实：不出现 0 分假值，不污染 `eval_report` 看板。
- 默认路径完全不变。

**Non-Goals**
- 不删除、不改写四指标实现（保留为可选路径）。
- 不改 `eval_report` schema（不把列改成 nullable）。
- 不做 RAGAS 与 DeepEval 的分数对照（待 RAGAS 角色拍板后另做）。
- 不做 DeepEval 出题（属 `deepeval-synthesizer-testset`）。

## Decisions

### D1 开关形态：CLI `--deepeval-only`，默认关闭

- **决策**：`argparse` 增加 `--deepeval-only`（`action="store_true"`）。
- **理由**：评测是 CLI 工具，显式参数最适合"这一次想跑什么"；环境变量容易被遗忘且在报告里难以留痕。
- **代价**：参数面增加一个；与 `--limit` 可自由组合（冒烟尤其有用）。

### D2 该模式不写 `eval_report`

- **决策**：跳过 `_save_eval_report`。
- **理由**：该表四指标列与 `overall_score` **非 nullable、默认 `0.0`**，`passed` 默认 `False`。写入会让看板显示"0 分且未通过"，与"未评"混淆——这正是本项目一路在纠的"假分数"。
- **替代方案（不采纳）**：把列改为 nullable 需迁移 + 改 `kb_eval` 的假值判断；成本与本变更不成比例。
- **代价**：看板看不到该模式的结果；报告文件与台账仍可查。

### D3 报告形态：只写 Markdown，不写 CSV

- **决策**：该模式只落 `.md`（含题数、口径、`RAGAS: 未跑（--deepeval-only）`、行为轴表）；不写 CSV。
- **理由**：CSV 的列结构绑定四指标（`faithfulness` 等），无四指标时写空列会诱导下游误读；`compare_retrieval` 等脚本也按四指标解析 stdout，本模式不打印四指标行。
- **实现**：`save_markdown_report` 增加 `result=None` 路径（不输出逐题指标表与主分行，只输出头部元数据 + 行为轴）。
- **代价**：报告形态分叉，需要专门测试。

### D4 与既有参数的交互

| 组合 | 行为 |
|---|---|
| `--deepeval-only` 单独 | 跑图 + 行为轴 + MD 报告；不落库；退出码 0 |
| `--deepeval-only --limit N` | 同上，仅前 N 题（冒烟） |
| `--deepeval-only --gate` | **启动即 `parser.error`**（无主分可判） |
| `--deepeval-only --generate` | 仍走 `--generate` 的硬护栏（出题路优先） |

- **理由**：门禁判据是四指标主分；没有主分时"通过/不通过"无意义，静默忽略会让 `--gate` 用户误以为过了门禁。

### D5 行为轴是唯一产出，且它不进门禁

- 该模式下**没有任何门禁语义**：行为轴按既定口径只作诊断观测（不进主分/门禁）。报告头 SHALL 明写这一点，避免被当成"通过了评测"。

## Risks / Trade-offs

| # | 风险 | 应对 |
|---|---|---|
| R1 | 下游脚本（如 `compare_retrieval`）解析不到四指标 stdout 行 | 该模式不打印四指标行；在 README 注明"本模式不产出主分，勿用于 A/B 对比" |
| R2 | 看板缺失该模式结果 | 报告文件 + 台账（口径版本列记 `deepeval-only`）可查；若将来需要看板，再评估 schema |
| R3 | 被误当"评测通过" | 报告头显式写 `RAGAS: 未跑` + "无门禁语义"；与 `--gate` 互斥 |
| R4 | 四指标代码长期不跑而腐化 | 默认路径保持不变，CI 的全量测试仍覆盖四指标单测；全量活体由后续 RAGAS 决策时再跑 |
