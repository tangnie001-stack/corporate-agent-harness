## Why

换尺子（DeepEval 桥 + ragas 0.3.9）与 Agent 行为轴已经落地并实测可用（21 题：Task Completion 0.9024 / Step Efficiency 0.5929 / Agent Loop Detection 1.0000）。但 **RAGAS 经典四指标在多轮 Agent 上存在结构性问题**，且已被三次独立证据确认：

1. **定义问题**：`context_precision` / `context_recall` 的 `c(q)` 前提是「一题一次检索」。多轮 Agent 每轮 query 不同，不存在单一 `c(q)`；压成任何单一列表都是口径选择，不是指标能解决的问题。
2. **探针实测（2026-10-17，v5 历史数据，5 题 × 3 种喂法）**：17/19 道纯 KB 题只检索 1 次（三种喂法结果完全相同）；2 道多轮题上「只取最后一轮」反而更差（0.2 → 0.0），per-step 平均也无提升。
3. **上游对照**：参考项目 `financial_rag-main` 是**单轮 RAG**（一次 `retrieve(top_k=5)` → 一次生成），其 RAGAS `context_precision` 也只有 11/18 有效（7 个 NaN）。四指标的适用前提是「一次检索」，不是「多轮 Agent」。

**用户决定（2026-10-17）**：收缩战线——先让 DeepEval 自闭环（跑图 + 行为轴 + 报告），等 DeepEval 功能（含出题）正常后，再决定 RAGAS 四指标是继续走桥还是独立跑。

**当前阻碍**：评测入口**默认跑四指标**。21 题串行约 56 分钟，无法只验收 DeepEval；报告与台账也没有「RAGAS 未跑」的合法形态，`eval_report` 落库还会以 `0.0` / `passed=False` 污染看板。因此需要先给评测入口一个**只跑 DeepEval** 的模式。

## What Changes

- 评测 CLI 新增 `--deepeval-only`：跳过 RAGAS 四指标评分，只跑「跑图 + Agent 行为轴 + 报告」。
- 该模式下：
  - **不构造**四指标度量、**不调用** `run_evaluation`；
  - 报告**不输出**四指标主分行，改为显式标注 `RAGAS: 未跑（--deepeval-only）`；
  - **不写 `eval_report` 表**（四指标列非 nullable、默认 `0.0`，写入即污染看板）；
  - 行为轴段、CSV/MD 落盘、`--limit` 冒烟保持原样。
- `--deepeval-only` 与 `--gate` **互斥**（无主分可判），运行时报错。
- **默认行为不变**：不加开关时仍跑四指标 + 行为轴。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `evaluation-pipeline`：新增「评测可只跑 DeepEval 指标」的要求——模式开关、跳过四指标、报告标注、不落 `eval_report`、与门禁互斥、默认行为不变。

## Impact

- **代码**：`src/cli/eval_ragas.py`（参数、`main()` 分支、报告组装）；`src/cli/eval_scoring.py`（如报告组装需要新增纯函数）。
- **数据**：`eval_report` 表**不新增列、不改 schema**；该模式下**不写入**。
- **文档**：`docs/agents/api_contract.md`（若 `kb_eval` 返回形状不变则无需改，确认即可）、`src/cli/README.md`、`docs/agents/ragas-eval-ledger.md`（口径版本列可记「RAGAS 未跑」）。
- **不改**：四指标实现、桥、行为轴、`src/agents/**`、`kb_eval` API 形状、前端。
- **被测行为**：评测耗时从约 56 分钟降到约 10–15 分钟（21 题），使 DeepEval 功能可以快速迭代验收。
