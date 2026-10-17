# Tasks

> 执行顺序即编号顺序；每组内为 TDD 小步（先写失败测试 → 实现 → 跑通 → 提交）。
> 规格：`specs/evaluation-pipeline/spec.md`；决策：`design.md`。
>
> **顺序原则**：先换指标（第 1 组）、再加诊断（第 2 组）、再退役 RAGAS（第 3 组），功能稳定后才做「改名 + 数据迁移」（第 4 组）；文档/ADR（第 5 组）与验收（第 6 组）全部按**新名字/新路径**执行。改名后置的理由见 design D5/D9。

## 1. 四指标换成 DeepEval 原生（D1 / D2）

- [ ] 1.1 写失败测试：`build_metrics(judge)` 返回四个原生指标（`AnswerRelevancyMetric` / `FaithfulnessMetric` / `ContextualPrecisionMetric` / `ContextualRecallMetric`），键序 = `EVAL_METRIC_COLUMNS`，且**不再 import** `deepeval.metrics.ragas`。
- [ ] 1.2 实现：`build_metrics` 改为原生四项，签名去掉 `embeddings`；`main()` 构造一次 `BridgeDeepEvalLLM(bridge_judge)` 并复用于四指标与行为轴；更新全部调用点与测试。
- [ ] 1.3 写失败测试：`build_bridge_judge` 返回的裁判 `temperature == 0`，且未传 `top_p`（若 `LLM_KWARGS` 自带 `top_p`，裁判构造须显式剥离，不能原样下沉）。
- [ ] 1.4 实现：`build_bridge_judge` 显式 `temperature=0`；删 `build_report_meta` 的温度分档字段，**同时改 `save_markdown_report` 的两个分支**（`ragas_skipped` 与默认分支）里的 `temperature_regime` / `versions['ragas']`，否则报告生成 KeyError。
- [ ] 1.5 实现：删除 `version("ragas")` 调用；`runner_versions` 只留 deepeval，并加 `metrics="native"`；两个报告分支同步只输出 deepeval。补“默认 / `--deepeval-only` 均能生成 MD”的测试。
- [ ] 1.6 跑通并提交。

## 2. 诊断指标（D3，不进主分）

- [ ] 2.1 写失败测试：`ContextualRelevancyMetric` 构造时 `retrieval_context` 被截断到 `EVAL_RELEVANCY_MAX_CONTEXTS=8`，且**共享 test case 未被原地修改**（CP/CR/faithfulness 仍收到全量 context）。
- [ ] 2.2 实现：诊断指标单独构造 test case；**每个指标都显式传 `model=judge`**（`ContextualRelevancyMetric(model=judge)`、`NonAdviceMetric(advice_types=["financial"], model=judge)`）；常量入 `src/config/const.py`。补断言“每个诊断指标 `m.model is judge`”。
- [ ] 2.3 实现：`ToolCorrectnessMetric` 不构造；逐题诊断值写入 `detail_json`，键名用裸指标名（`contextual_relevancy` / `non_advice` / `tool_correctness`），`tool_correctness` 写 `null`；`kb_eval` 不返回这些键。
- [ ] 2.4 实现：`empty` / 跑图失败的样本，三项诊断 SHALL 写 NaN/null 并单独计数，**不得**让 `[ERROR]` 文本进入 `non_advice`、也不得让空 context 的 0.0 进入诊断均值（复用 `sample_class` / 失败标记，不只看 `answer == ""`）。
- [ ] 2.5 实现：报告新增「诊断指标」段并标注 Relevancy 截断口径；`main_score_means` / `check_gate` 不读三项诊断；诊断 NaN 与四指标 NaN 分开统计，不影响 NaN 率门禁。`score_rows` 的 `error` SHALL 同时写入 Markdown 表格与 `detail_json` 条目。
- [ ] 2.6 跑通并提交。

## 3. 退役 RAGAS（D4 / D7）

- [ ] 3.1 新建 `src/cli/testset_io.py`：迁入 `_load_latest_testset`，返回完整 sample（含 `expected_tools`）；`eval_ragas.py` 改为从它 import。同步把 `tests/test_tracing_order.py:81`、`tests/cli/test_deepeval_only.py:100` 的 patch 目标改到 `src.cli.testset_io._load_latest_testset`。
- [ ] 3.2 删除 `src/cli/eval_ragas_generate.py`（含 `_ensure_vertexai_stub`）；删除/改写出题测试：`tests/cli/test_generate_*`（含 `test_generate_llm_context.py` / `test_generate_entity_anchor.py` / `test_generate_transforms.py` / `test_generate_guard.py`）、`test_eval_ragas_personas.py`、`test_self_contained_gate.py` 的 ragas 部分；`test_deepeval_only.py:104` 的 stub patch 一并删除。
- [ ] 3.3 **保留** `POST /api/ragas/generate` 路由挂载（`src/main.py:272` 不动），把 `src/api/ragas_generate.py` 改写为只返回 410 + 前迁路径，**不 import** 已删除的 `eval_ragas_generate`；`src/api/README.md` 同步。CLI `--generate` **保留参数**，命中即明确报错并指向 DeepEval `Synthesizer`。
- [ ] 3.4 删除 `pyproject.toml` 的 `ragas==0.3.9`；重建/校验依赖（`pip check` 无新增冲突）。
- [ ] 3.5 `_score_metric_with_retry`：删除 429 短路与对应测试；退避改**指数 + jitter 并优先读 `Retry-After`**；新增“空回答/跑图失败预检”（按 `sample_class == empty` 或显式失败标记，直接记 NaN，不重试）；诊断指标失败**不重试**。
- [ ] 3.6 自查（**不用全仓子串零命中**）：`rg 'deepeval\.metrics\.ragas|from ragas|import ragas' src/` 零命中；`rg 'ragas' src/ --glob '*.py'` 的剩余命中逐条确认只是待改名的模块/注释；`pytest tests/ -q` 全绿；提交。

## 4. 改名与数据迁移（D5 / D6）

- [ ] 4.1 `git mv src/cli/eval_ragas.py src/cli/eval_deepeval.py`；测试改名：`tests/cli/test_eval_ragas*.py` → `test_eval_deepeval*.py`，`tests/cli/test_eval_bridge_*.py` → `test_eval_native_*.py`（桥时代命名已失真）。
- [ ] 4.2 全仓改 import / 命令：`src.cli.eval_ragas` → `src.cli.eval_deepeval`（含 `src/cli/compare_retrieval.py:67`、`src/infra/llm/tracing.py:6`、测试 import、`src/cli/README.md`、根 README、`docs/agents/{data-flow,cookbook,api_contract}.md`）。
- [ ] 4.3 配置重命名：`RAGAS_JUDGE_*` / `RAGAS_HTTP_TIMEOUT_*` / `RAGAS_METRIC_MAX_ATTEMPTS` / `RAGAS_LLM_MODEL` / `RAGAS_PROVIDER` → `JUDGE_*`；`RAGAS_DATA_DIR` / `RAGAS_TESTSET_DIR` / `RAGAS_REPORT_DIR` / `RAGAS_LLM_CACHE_DIR` / `RAGAS_USER_ID` → `EVAL_*`；`RAGAS_METRIC_COLUMNS` → `EVAL_METRIC_COLUMNS`；删除 `RAGAS_TEST_SIZE` / `RAGAS_TEST_PERSONAS` / `RAGAS_SELF_CONTAINED_HINT` / `RAGAS_DOC_WHITELIST` / **`RAGAS_HTTP_MAX_RETRIES`**（裁判硬编码 `max_retries=0`）。同步 `provider/spec.py` 的 `_PROVIDER_ENV["judge"]`、`src/models.py`、`tests/conftest.py`、两份 env 模板（**只写新名**）。
- [ ] 4.4 旧 env fail-fast：`settings.py` 在 `load_dotenv()` 之后扫描 `os.environ`，命中任何 `RAGAS_*` 即抛错，报错列出「旧名 → 新名」映射；不 fallback。补一条**子进程**启动测试（`settings` 是导入期单例，同进程无法重新触发）。
- [ ] 4.5 日志事件改名：`RAGAS_MODEL_MISSING` / `"ragas model missing"` → `JUDGE_MODEL_MISSING` / `"judge model missing"`，同步 `EVENT_SPECS` 与 `logging-rules.md` 的事件表。
- [ ] 4.6 数据目录迁移：`mv data/ragas data/deepeval`（保留 `testset/`、`reports/`；`kg_*.json` 与 `llm_cache` 不迁移，随后删除）；同步 `settings.py` 默认值、`deploy.sh:339` 的 `mkdir -p`（**不删除**旧目录）、**`docker-compose.yml:175` 与 `docker-compose.image.yml:227` 两处挂载**、`.env.example` / `.env.template`、README/文档中的路径。
- [ ] 4.7 Langfuse 命名：`--session-id` 默认值 `ragas_eval_session` → `deepeval_eval_session`（含 help 与 `src/cli/README.md`）。
- [ ] 4.8 台账改名：`git mv docs/agents/ragas-eval-ledger.md docs/agents/eval-ledger.md`；同步 `CLAUDE.md` 文档表、`check_docs` 锚点与全部引用。
- [ ] 4.9 `--run-type` 与 `passed` 语义：CLI 新增 `--run-type`（默认 `manual`）；`_save_eval_report` 增加 `run_type` 参数并写入；`--run-type baseline` 与 `--gate` 互斥。`_build_eval_payload` 对 `run_type == "baseline"` 返回 `passed=None`，四项分值与 `overall_score` 改用 `is not None`（0.0 不能被吞）。确认仓库内无其它落库入口绕过 `_save_eval_report`。
- [ ] 4.10 报告与观测口径收尾：`LANGFUSE_SCORE_SUFFIX` → `@deep-eval-native`；`build_report_meta` 的 `caliber` → `deep-eval-native`；默认 CSV 文件名 `ragas_eval_*` → `deepeval_eval_*`；Markdown 标题 → `# Deepeval Evaluation Report`；加 `--behaviour-only` 别名（与 `--deepeval-only` 同义）。
- [ ] 4.11 自查：`grep -rn 'RAGAS_' src/ tests/` 与 `grep -rn 'data/ragas' src/ tests/ scripts/ deploy/ docker-compose*.yml` 零命中（归档文档除外）；`pytest tests/ -q` 全绿；提交。

## 5. 文档与 ADR（D8 / D6）

- [ ] 5.1 新增 ADR-0027，**完全取代** ADR-0026 / ADR-0022 / 0023；正文必须重述仍生效的约束：细粒度超时落 `http_async_client`、async hooks、`max_tokens`/`thinking` 透传、`max_retries=0`（0026）、**评估检索镜像生产、不按白名单收窄**（0023）。三份旧 ADR 的 `Status` 加反向指针；更新索引。0027 同时记录 `click` / `huggingface_hub` 保留 pin 的理由。
- [ ] 5.2 同步文档：`api_contract.md`（路径/命令/指标来源/诊断字段/`passed=None` 语义/`--run-type`/`--session-id`/0.0 不吞）、`eval-ledger.md`（口径版本 `deep-eval-native`、报告文件名前缀、历史 `report_path` 失效说明）、`code-map.md`（模块改名）、`deploy-runbook.md`（**旧名 → 新名对照表**、旧 `data/ragas` 人工清理、共享 `.env` 的切换顺序）/ `deploy.sh` / 两份 compose / env 模板、`selection-baseline.md`、`reference-projects.md`、`glossary.md`、`logging-rules.md`、`troubleshooting.md`、`data-flow.md`、`cookbook.md`、`best-practices.md`、`requirements_pool.md`、`defensive-patterns.md`、`src/cli/README.md`、根 `README.md`、`src/api/README.md`。
- [ ] 5.3 前端：`deploy/nginx/html/index.html` 的 RAGAS 徽标文案改为"评测"；**只改 KB 徽标 1 处 JS（`:1100-1102`）**：`passed === null` → 灰色"未标定"（不显示 ✓/✗），`true` / `false` 照旧；新增 1 个灰色样式。**不动** `showEvalModal`（`:1110-1148`）的分块质量三态与 `overallCls`。
- [ ] 5.4 `openspec validate deepeval-native-metrics --strict` / `check_docs` / `check_adr` 全过；提交。

## 6. 验收（D6，跑在新路径/新命令上）

- [ ] 6.1 静态闸门：`pytest tests/ -q` 全绿；`ruff format --check src/ tests/`；`ruff check src/ tests/` 与 `pyright src/` 不新增 error。
- [ ] 6.2 **手工选样冒烟（5 题）**：造临时子集（v5 index `0` 简单中文 / `2` 多跳中文 / `7` 英文 / `13` 纯数值 / `14` 多轮），用 `python -m src.cli.eval_deepeval` 确认四项 + 三项诊断均有值、`detail_json` 含裸键名且 `tool_correctness=null`、失败样本的 `error` 在 MD 与 `detail_json` 可见、`empty` 样本诊断值为 NaN/null、无 400/超时；用完**删除** `_v99.json`。
- [ ] 6.3 21 题全量：以 `--run-type baseline`（**不加 `--gate`**，默认即不判门禁）跑新口径，产出 baseline；记录四项均值、诊断均值、NaN、耗时。
- [ ] 6.4 校验 `kb_eval` 对 `run_type="baseline"` 返回 `passed=None`（0.0 指标不得变 null）；前端徽标显示"未标定"（非 ✗）；台账追加 `deep-eval-native / v5` 行；验收记录写回计划文件并提交。
