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
- 新增诊断指标（不进主分/门禁）：`ContextualRelevancy`（**评分层截断前 8 条**）、`NonAdvice(["financial"])`；两者**必须显式 `model=judge`**（DeepEval 默认 `gpt-3.5-turbo` 已停服）。`ToolCorrectness` 等 v6 标注后再启用，本 change 记 `N/A`。逐题诊断值写入 `detail_json`（裸键名，`tool_correctness` 写 `null`），`kb_eval` API 不返回；`empty` / 跑图失败样本的诊断值写 NaN/null，不让 `[ERROR]` 文本参与评分。
- 429 SHALL 由外层有界重试统一负责（删"ragas 层已重试"的豁免依据），退避改指数 + jitter 并优先读 `Retry-After`；裁判 `max_retries` 硬编码 0。
- **空 verdict 的原生语义**：`Faithfulness` / `AnswerRelevancy` 在无可校验陈述时返回 1.0（`empty_score=1`）、CP/CR 返回 0，**不是 NaN**；NaN 仅表示异常/失败，主分可能偏乐观。本 change 接受该语义，不自行改写 verdict 解析。

### 退役 RAGAS

- 删除 `src/cli/eval_ragas_generate.py`；测试集加载器迁入 `src/cli/testset_io.py`（返回完整 sample，含 v6 预留的 `expected_tools`）。
- `POST /api/ragas/generate` **保留路由挂载**，改写为只返回 410 + 前迁路径（不 import 已删除的出题模块；摘除路由会变 404）；CLI `--generate` **保留参数**并明确报错（指向 `Synthesizer`）。
- 删除 `pyproject.toml` 的 `ragas==0.3.9`；删桥 import；删 `version("ragas")`。`click` / `huggingface_hub` 暂留，理由记入 ADR-0027。

### 命名与路径（彻底脱钩，**放在指标替换与退役之后**）

- `src/cli/eval_ragas.py` → **`src/cli/eval_deepeval.py`**（CLI 入口改为 `python -m src.cli.eval_deepeval`）；测试同步改名（`test_eval_ragas*` → `test_eval_deepeval*`，`test_eval_bridge_*` → `test_eval_native_*`）。
- `data/ragas/` → **`data/deepeval/`**（含 testset/reports；出题 KG 缓存随出题路删除）。**旧 `data/ragas` 目录不自动删除**，runbook 提示人工确认后清理；历史 `eval_report.report_path`（27 行）不动，注明已失效、无消费方。
- `RAGAS_*` 配置全量重命名：裁判 → `JUDGE_*`，评测目录/运行 → `EVAL_*`，`RAGAS_METRIC_COLUMNS` → `EVAL_METRIC_COLUMNS`，出题专用与 `RAGAS_HTTP_MAX_RETRIES` 删除；**旧 env 不兼容**，启动期 fail-fast 并给出「旧名 → 新名」提示；env 模板只写新名。
- Langfuse `--session-id` 默认值 `ragas_eval_session` → `deepeval_eval_session`；score 后缀 `@bridge-0.3.9` → `@deep-eval-native`；报告 `caliber`、CSV 文件名前缀、Markdown 标题同步切换。
- 台账 `docs/agents/ragas-eval-ledger.md` → **`docs/agents/eval-ledger.md`**（同步 `CLAUDE.md` 文档表、`check_docs` 锚点与全部引用）。
- `--deepeval-only` 保留，新增 `--behaviour-only` 别名；报告文案改 `主分: 未跑`。

### 口径

- 原生四指标与 RAGAS 不可比；首轮以 `run_type="baseline"` 落库（由新增 `--run-type` 参数写入，默认 `manual`）。**v6 阈值标定前，所有新口径全量运行 SHALL 显式传 `--run-type baseline`**；`baseline` 与 `--gate` 互斥。
- `kb_eval` 对 `run_type="baseline"` 返回 `passed=None`（转换落在 `_build_eval_payload`，不是 DB），前端**只改 KB 徽标**：`null` 显示"未标定"（灰，不显示 ✓/✗）；四项分值与 `overall_score` 用 `is not None` 判断，0.0 不得被吞。不改 `eval_report` schema。
- Langfuse score 后缀改 `@deep-eval-native`；台账口径版本记 `deep-eval-native`。
- 新增 ADR-0027，完全取代 ADR-0026 / 0022 / 0023，但正文重述仍生效的约束（async hooks / 细粒度超时 / `max_retries=0` / 评估检索镜像生产）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `evaluation-pipeline`：评测指标来源改为 DeepEval 原生四指标；新增三项诊断指标（不进主分）；移除 RAGAS 依赖与出题链路；评测模块与数据目录改名；明确新口径不可比、baseline 不判门禁。

## Impact

- **依赖**：删除 `ragas==0.3.9`；`click` / `huggingface_hub` 暂留（供 DeepEval 与后续 `Synthesizer`）。
- **代码**：`src/cli/eval_deepeval.py`（原 `eval_ragas.py`）、`eval_scoring.py`、`testset_io.py`（新建）、`eval_ragas_generate.py`（删除）、`src/api/ragas_generate.py`（改写为 410，路由保留）、`src/config/settings.py` / `const.py`、`src/infra/llm/provider/factory.py`、`src/core/log_events.py` / `log_event_specs.py`、`retrying_chat.py`。
- **数据**：`data/ragas/` → `data/deepeval/`；`eval_report` 无 schema 迁移。
- **文档**：ADR-0027（新增）/ 0026 / 0022 / 0023；`api_contract.md`、`eval-ledger.md`（原 `ragas-eval-ledger.md`）、`code-map.md`、`deploy-runbook.md`、`selection-baseline.md`、`reference-projects.md`、`glossary.md`、`logging-rules.md`、`troubleshooting.md`、`data-flow.md`、`cookbook.md`、`best-practices.md`、`requirements_pool.md`、`defensive-patterns.md`、`src/cli/README.md`、根 `README.md`、`src/api/README.md`、`.env.example` / `.env.template`、`deploy.sh`、`docker-compose.yml`、`docker-compose.image.yml`。
- **前端**：`deploy/nginx/html/index.html` 的 RAGAS 徽标文案改为"评测"；**只改 KB 徽标 1 处 JS + 1 个灰色样式**（`passed=null` → "未标定"）；分块质量弹窗不动。
- **不改**：`src/agents/**`、生产链路、`kb_eval` API 形状、前端交互结构。
