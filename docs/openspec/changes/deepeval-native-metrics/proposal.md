## Why

用户决定（2026-10-17）：**项目先与 RAGAS 完全脱钩**——不跑、不用桥、不依赖；同时把 DeepEval 整体用起来，而不是只跑三个行为轴指标。

现状与目标的差距：

1. **指标面**：主分仍来自 `deepeval.metrics.ragas` 桥的 RAGAS 经典四项。DeepEval **原生就有同类四指标**（`AnswerRelevancy` / `Faithfulness` / `ContextualPrecision` / `ContextualRecall`），输入同为 `input` / `actual_output` / `expected_output` / `retrieval_context`，不需要 ragas。
2. **诊断面**：我们只用了 3 个 agent 指标（`TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`）。DeepEval 还有 `ContextualRelevancy`（不需要 reference，可评中间检索）、`NonAdvice`（不提供投资建议，财务合规）、`ToolCorrectness`（工具选择是否正确）等未用。
3. **依赖与出题链路**：`ragas==0.3.9` 仍被 `src/cli/eval_scoring.py`（桥）与 `src/cli/eval_ragas_generate.py`（出题死代码）引用；`/api/ragas/generate` 路由仍直接 import 出题模块；`RAGAS_*` 配置 20+ 项散布在 `settings.py` / `const.py`。

本变更把「换原生指标」与「移除 RAGAS」合并完成（用户拍板：2 和 3 合并），避免出现「两套四指标并存」的中间态。

## What Changes

### 指标替换

- `build_metrics` 从「RAGAS 桥四指标」改为 **DeepEval 原生四指标**：
  `AnswerRelevancyMetric` / `FaithfulnessMetric` / `ContextualPrecisionMetric` / `ContextualRecallMetric`。
- 新增 **`ContextualRelevancyMetric`**（不需要 reference）：评「query ↔ 检索材料」的相关性，作为多轮场景下 context 轴的补充诊断。
- 新增 **`NonAdviceMetric`**：财务合规口径——答案不得给出投资建议。
- 新增 **`ToolCorrectnessMetric`**：评工具选择是否正确。**前提是测试集带 `expected_tools` 标注**；现有 v5 无标注 ⇒ 该指标在 v5 上记 `N/A`（不 fail-loud），v6 起启用。

### 移除 RAGAS

- 删除 `src/cli/eval_ragas_generate.py`（出题生成）；把仍被评测路径使用的 `_load_latest_testset` 迁到新模块 `src/cli/testset_io.py`，`_ensure_vertexai_stub` 随 ragas 一起删除。
- 下线 `POST /api/ragas/generate`：返回明确的「已下线 + 前迁路径」错误（不裸 `ImportError`），并从 `src/main.py` 摘除路由注册。
- 删除 `pyproject.toml` 的 `ragas==0.3.9`。
- 配置重命名：`RAGAS_*` → 语义化前缀（裁判 `JUDGE_*`、评测目录 `EVAL_*`、测试集 `TESTSET_*`），`RAGAS_METRIC_COLUMNS` → `EVAL_METRIC_COLUMNS`；删除仅出题使用的配置（`RAGAS_TEST_PERSONAS` / `RAGAS_SELF_CONTAINED_HINT` 等）。
- `--generate` 的护栏文案改为「出题已迁移到 DeepEval Synthesizer（见 change `deepeval-synthesizer-testset`）」，不再提「装回 ragas==0.4.3」。
- 删除/改写出题相关测试与 `deepeval/metrics/ragas` 桥相关断言。

### 口径

- 原生四指标与 RAGAS 四指标**不是同一把尺子**（公式/提示词/判官行为不同）⇒ **历史分数不可比**，需在 v5 上重跑一次新基线；旧报告保留但不作对照。
- 主分与门禁仍只看四指标；`ContextualRelevancy` / `NonAdvice` / `ToolCorrectness` 只作诊断观测，**不进主分/门禁**。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `evaluation-pipeline`：
  - 评测指标来源由「RAGAS 经典四指标经 DeepEval 桥」改为「**DeepEval 原生四指标**」；
  - 新增三项诊断指标（`ContextualRelevancy` / `NonAdvice` / `ToolCorrectness`，均不进主分）；
  - 移除 RAGAS 依赖与出题链路（模块 / API / 配置 / 依赖）；
  - 明确「新旧口径不可比、需重跑基线」。

## Impact

- **依赖**：删除 `ragas==0.3.9`（本次第二个"不可逆"动作；回滚需恢复 `ragas==0.3.9` + 桥代码）。
- **代码**：`src/cli/eval_scoring.py`（指标构造）、`src/cli/eval_ragas.py`（指标名/白名单/报告）、`src/cli/eval_ragas_generate.py`（删除）、`src/cli/testset_io.py`（新建）、`src/api/ragas_generate.py`（下线）、`src/main.py`（路由）、`src/config/settings.py` / `const.py`（配置重命名）、`docs/agents/api_contract.md`。
- **测试**：`tests/cli/test_eval_modern_stack.py` 类残留、`test_generate_*`、`test_eval_ragas_personas.py`、`test_self_contained_gate.py`（部分）、桥指标测试需删/改；新增原生指标与三项诊断的测试。
- **文档**：新增 ADR 取代 `docs/adr/0026` 的「桥相关」部分（裁判 client 政策仍适用，但「桥不可控旋钮」失效）；台账、`evaluation-pipeline` 主 spec、`src/cli/README.md`、`reference-projects.md` 的 RAGAS 条目。
- **数据**：`eval_report` 列名不变（沿用 `faithfulness` 等）；`detail_json` 增加三项诊断字段。
- **不改**：`src/agents/**`、生产链路、`kb_eval` API 形状、前端（`kb-harness-page-mockup.html` 的 `ragas-tag` 仅 mockup，确认后不改）。
