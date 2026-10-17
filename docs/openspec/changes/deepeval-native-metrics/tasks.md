# Tasks

> 执行顺序即编号顺序；每组内为 TDD 小步（先写失败测试 → 实现 → 跑通 → 提交）。
> 规格：`specs/evaluation-pipeline/spec.md`；决策：`design.md`。
>
> **顺序原则**：先做「改名 + 数据迁移」（第 1 组），让后续所有改动直接落在新名字上，避免二次改名；再做指标替换（第 2–3 组）；最后退役 RAGAS（第 4 组）。

## 1. 改名与数据迁移（D5）

- [ ] 1.1 `git mv src/cli/eval_ragas.py src/cli/eval_deepeval.py`；同步 `git mv` 测试文件（`tests/cli/test_eval_ragas*.py` → `test_eval_deepeval*.py`）。
- [ ] 1.2 全仓改 import / 命令：`src.cli.eval_ragas` → `src.cli.eval_deepeval`（含 `src/cli/compare_retrieval.py:67`、`src/infra/llm/tracing.py:6`、测试 import、`src/cli/README.md`、根 README、`docs/agents/{data-flow,cookbook,api_contract}.md`）。
- [ ] 1.3 数据目录迁移：`mv data/ragas data/deepeval`（保留 `testset/`、`reports/`；`kg_*.json` 与 `llm_cache` 不迁移，随后删除）；同步 `settings.py` 默认值、`deploy.sh:339`、`docker-compose.image.yml` 挂载、`.env.example` / `.env.template`、README/文档中的路径。
- [ ] 1.4 配置重命名：`RAGAS_JUDGE_*` / `RAGAS_HTTP_*` / `RAGAS_METRIC_MAX_ATTEMPTS` / `RAGAS_LLM_MODEL` / `RAGAS_PROVIDER` → `JUDGE_*`；`RAGAS_DATA_DIR` / `RAGAS_TESTSET_DIR` / `RAGAS_REPORT_DIR` / `RAGAS_LLM_CACHE_DIR` / `RAGAS_USER_ID` → `EVAL_*`；`RAGAS_METRIC_COLUMNS` → `EVAL_METRIC_COLUMNS`；删除 `RAGAS_TEST_SIZE` / `RAGAS_TEST_PERSONAS` / `RAGAS_SELF_CONTAINED_HINT` / `RAGAS_DOC_WHITELIST`。同步 `provider/spec.py` 的 `_PROVIDER_ENV["judge"]`、`models.py`、`tests/conftest.py`、两份 env 模板。
- [ ] 1.5 自查：`grep -rn 'RAGAS_' src/ tests/` 与 `grep -rn 'data/ragas' src/ tests/ scripts/ deploy/` 零命中（归档文档除外）；`pytest tests/ -q` 全绿。
- [ ] 1.6 提交。

## 2. 四指标换成 DeepEval 原生（D1 / D2）

- [ ] 2.1 写失败测试：`build_metrics(judge)` 返回四个原生指标（`AnswerRelevancyMetric` / `FaithfulnessMetric` / `ContextualPrecisionMetric` / `ContextualRecallMetric`），键序 = `EVAL_METRIC_COLUMNS`，且**不再 import** `deepeval.metrics.ragas`。
- [ ] 2.2 实现：`build_metrics` 改为原生四项，签名去掉 `embeddings`；`main()` 构造一次 `BridgeDeepEvalLLM(bridge_judge)` 并复用于四指标与行为轴；更新全部调用点与测试。
- [ ] 2.3 写失败测试：`build_bridge_judge` 返回的裁判 `temperature == 0`，且未传 `top_p`。
- [ ] 2.4 实现：`build_bridge_judge` 显式 `temperature=0`；删 `build_report_meta` 的温度分档字段与报告对应文案。
- [ ] 2.5 实现：删除 `version("ragas")` 调用；`runner_versions` 只留 deepeval，并加 `metrics="native"`。
- [ ] 2.6 跑通并提交。

## 3. 诊断指标（D3，不进主分）

- [ ] 3.1 写失败测试：`ContextualRelevancyMetric` 构造时 `retrieval_context` 被截断到 `EVAL_RELEVANCY_MAX_CONTEXTS=8`，且**共享 test case 未被原地修改**（CP/CR/faithfulness 仍收到全量 context）。
- [ ] 3.2 实现：诊断指标单独构造 test case；常量入 `src/config/const.py`；`NonAdviceMetric(advice_types=["financial"])` 接入。
- [ ] 3.3 实现：`ToolCorrectnessMetric` 不构造；报告/`detail_json` 对其记 `N/A`。
- [ ] 3.4 实现：报告新增「诊断指标」段并标注 Relevancy 截断口径；`main_score_means` / `check_gate` 不读三项诊断；诊断 NaN 单独计数。
- [ ] 3.5 跑通并提交。

## 4. 退役 RAGAS（D4 / D7）

- [ ] 4.1 新建 `src/cli/testset_io.py`：迁入 `_load_latest_testset`，返回完整 sample（含 `expected_tools`）；`eval_deepeval.py` 改为从它 import。
- [ ] 4.2 删除 `src/cli/eval_ragas_generate.py`（含 `_ensure_vertexai_stub`）；删除/改写出题测试（`test_generate_*` / `test_eval_ragas_personas.py` / `test_self_contained_gate.py` 的 ragas 部分）。
- [ ] 4.3 `POST /api/ragas/generate` 返回 410 + 前迁路径；`src/main.py` 摘除路由；`src/api/README.md` 同步。
- [ ] 4.4 删除 `pyproject.toml` 的 `ragas==0.3.9`；重建/校验依赖（`pip check` 无新增冲突）。
- [ ] 4.5 `_score_metric_with_retry`：删除 429 短路与对应测试；新增"空回答预检"（`answer=""` 直接记 NaN，不重试）。
- [ ] 4.6 自查：`grep -rn 'ragas' src/ tests/ --include='*.py'` 零命中（`eval_deepeval.py` 文件名本身不算）；`pytest tests/ -q` 全绿；提交。

## 5. 文档与 ADR（D8 / D6）

- [ ] 5.1 新增 ADR-0027，取代 ADR-0026（桥相关）、ADR-0022 / 0023（出题决策）；三份旧 ADR 的 `Status` 加反向指针；更新 `docs/adr/README.md` 索引。
- [ ] 5.2 同步文档：`api_contract.md`（路径/命令/指标来源/诊断字段/`passed=None` 语义）、`ragas-eval-ledger.md`（口径版本 `deep-eval-native`、报告文件名前缀）、`code-map.md`（模块改名）、`deploy-runbook.md` / `deploy.sh` / compose / env 模板、`selection-baseline.md`、`reference-projects.md`、`glossary.md`、`logging-rules.md`、`troubleshooting.md`、`data-flow.md`、`cookbook.md`、`best-practices.md`、`requirements_pool.md`、`defensive-patterns.md`、`src/cli/README.md`、根 `README.md`、`src/api/README.md`。
- [ ] 5.3 前端 `deploy/nginx/html/index.html` 的 RAGAS 徽标文案改为"评测"（结构不变）。
- [ ] 5.4 `openspec validate deepeval-native-metrics --strict` / `check_docs` / `check_adr` 全过；提交。

## 6. 验收（D6）

- [ ] 6.1 静态闸门：`pytest tests/ -q` 全绿；`ruff format --check src/ tests/`；`ruff check src/ tests/` 与 `pyright src/` 不新增 error。
- [ ] 6.2 **手工选样冒烟（5 题）**：造临时子集（v5 index `0` 简单中文 / `2` 多跳中文 / `7` 英文 / `13` 纯数值 / `14` 多轮），确认四项 + 三项诊断均有值、无 400/超时；用完**删除** `_v99.json`。
- [ ] 6.3 21 题全量：以 `--no-gate` 跑新口径，产出 baseline（`run_type="baseline"`）；记录四项均值、诊断均值、NaN、耗时。
- [ ] 6.4 校验 `kb_eval` 对 `run_type="baseline"` 返回 `passed=None`；台账追加 `deep-eval-native / v5` 行；验收记录写回计划文件并提交。
