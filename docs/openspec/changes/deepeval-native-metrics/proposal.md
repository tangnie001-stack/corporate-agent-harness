## Why

用户决定（2026-10-17）：**项目先与 RAGAS 完全脱钩**——不跑、不用桥、不依赖、不留命名；同时把 DeepEval 整体用起来，而不是只跑三个行为轴指标。

现状与目标的差距：

1. **指标面**：主分仍来自 `deepeval.metrics.ragas` 桥的 RAGAS 经典四项。DeepEval **原生就有同类四指标**（`AnswerRelevancy` / `Faithfulness` / `ContextualPrecision` / `ContextualRecall`），输入同为 `input` / `actual_output` / `expected_output` / `retrieval_context`，不需要 ragas。
2. **诊断面**：只用了 3 个 agent 指标。DeepEval 还有 `ContextualRelevancy`（不需要 reference）、`NonAdvice`（财务合规）、`ToolCorrectness`（工具选择）可用。
3. **依赖与命名**：`ragas==0.3.9` 仍被桥与出题模块引用；`/api/ragas/generate` 仍 import 出题模块；`RAGAS_*` 配置 20+ 项；CLI 模块与数据目录也以 ragas 命名。

**spike 证据（2026-10-17，2 题真裁判，4m56s）**：原生四指标可用、分数可解释（Q0 全 1.0；Q14 长上下文 0.33/0.54/0.20），无 400/超时，21 题预计 30–50 分钟。`ContextualRelevancy` 对每条 context 单独调裁判（Q14 的 20 条 → 42 个 hook 事件），且某条输出畸形会让整个指标失败。

## What Changes

### 指标

- `build_metrics` 改为 **DeepEval 原生四指标**，裁判用 `BridgeDeepEvalLLM` 包装；**删除 `embeddings` 参数**。
- 裁判**显式 `temperature=0`**（删 ragas 后不再有按 `n` 分档，不设会落到 provider 默认值）。
- 新增诊断指标（不进主分/门禁）：`ContextualRelevancy`（**评分层截断前 8 条**）、`NonAdvice(["financial"])`；`ToolCorrectness` 等 v6 标注后再启用，本 change 记 `N/A`。
- 429 SHALL 由外层有界重试统一负责（删"ragas 层已重试"的豁免依据）。

### 退役 RAGAS

- 删除 `src/cli/eval_ragas_generate.py`；测试集加载器迁入 `src/cli/testset_io.py`（返回完整 sample，含 v6 预留的 `expected_tools`）。
- 下线 `POST /api/ragas/generate`（410 + 前迁路径），从 `src/main.py` 摘除路由。
- 删除 `pyproject.toml` 的 `ragas==0.3.9`；删桥 import；删 `version("ragas")`。

### 命名与路径（彻底脱钩）

- `src/cli/eval_ragas.py` → **`src/cli/eval_deepeval.py`**（测试同步改名）；CLI 入口改为 `python -m src.cli.eval_deepeval`。
- `data/ragas/` → **`data/deepeval/`**（含 testset/reports；出题 KG 缓存随出题路删除）。
- `RAGAS_*` 配置全量重命名：裁判 → `JUDGE_*`，评测目录/运行 → `EVAL_*`，`RAGAS_METRIC_COLUMNS` → `EVAL_METRIC_COLUMNS`，出题专用删除。
- `--deepeval-only` 保留，新增 `--behaviour-only` 别名；报告文案改 `主分: 未跑`。

### 口径

- 原生四指标与 RAGAS 不可比；首轮以 `run_type="baseline"` 落库，`kb_eval` 返回 `passed=None`，前端不显示红/绿；不改 `eval_report` schema。
- Langfuse score 后缀改 `@deep-eval-native`；台账口径版本记 `deep-eval-native`。
- 新增 ADR-0027，取代 ADR-0026 的桥相关部分与 ADR-0022/0023 的出题决策。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `evaluation-pipeline`：评测指标来源改为 DeepEval 原生四指标；新增三项诊断指标（不进主分）；移除 RAGAS 依赖与出题链路；评测模块与数据目录改名；明确新口径不可比、baseline 不判门禁。

## Impact

- **依赖**：删除 `ragas==0.3.9`；`click` / `huggingface_hub` 暂留（供 DeepEval 与后续 `Synthesizer`）。
- **代码**：`src/cli/eval_deepeval.py`（原 `eval_ragas.py`）、`eval_scoring.py`、`testset_io.py`（新建）、`eval_ragas_generate.py`（删除）、`src/api/ragas_generate.py`（下线）、`src/main.py`、`src/config/settings.py` / `const.py`、`src/infra/llm/provider/factory.py`、`retrying_chat.py`。
- **数据**：`data/ragas/` → `data/deepeval/`；`eval_report` 无 schema 迁移。
- **文档**：ADR-0027（新增）/ 0026 / 0022 / 0023；`api_contract.md`、`ragas-eval-ledger.md`、`code-map.md`、`deploy-runbook.md`、`selection-baseline.md`、`reference-projects.md`、`glossary.md`、`logging-rules.md`、`troubleshooting.md`、`data-flow.md`、`cookbook.md`、`best-practices.md`、`requirements_pool.md`、`defensive-patterns.md`、`src/cli/README.md`、根 `README.md`、`src/api/README.md`、`.env.example` / `.env.template`、`deploy.sh`、`docker-compose.image.yml`。
- **前端**：`deploy/nginx/html/index.html` 的 RAGAS 徽标文案（结构不变）。
- **不改**：`src/agents/**`、生产链路、`kb_eval` API 形状、前端交互结构。
