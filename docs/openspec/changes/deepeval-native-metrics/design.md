## Context

`deepeval-only-eval` 已归档：评测可以只跑行为轴，RAGAS 四指标成为可选路径。本 change 完成"脱钩"的另一半——**把四指标换成 DeepEval 原生实现，并彻底移除 RAGAS**。

三组前置证据：

1. **spike（2026-10-17，2 题真裁判，4m56s）**：原生四指标可用，分数可解释（Q0 全 1.0；Q14 长上下文 0.33/0.54/0.20）；无 400/超时；单题约 1–2 分钟，21 题预计 30–50 分钟。
2. **接口差异（源码核对，deepeval 4.2.9）**：原生指标只接受 `str | DeepEvalBaseLLM`；`AnswerRelevancy` 不需要 embeddings；`ContextualRelevancy` 对**每条 context 单独调一次**（`asyncio.gather`），Q14 的 20 条 → 42 个 hook 事件；CP/CR 是批量一次。
3. **遗漏审计**：主 spec 仍明确要求 RAGAS 桥/0.3.9/温度分档/429 豁免；`eval_report` 无 caliber 列；`version("ragas")` 在正常报告路径；删出题模块会断 testset loader；配置重命名波及 `.env`/runbook/流水线；前端把 `passed=null` 渲染成 ✗。

## Goals / Non-Goals

**Goals**
- 四指标由 DeepEval 原生实现承担，评分编排（`score_rows` / 用例构造 / 报告 / 落库）不变。
- 新增两项诊断（`ContextualRelevancy` / `NonAdvice`），不进主分与门禁。
- 删除 ragas 依赖、桥、出题模块/API、出题专用配置。
- `eval_ragas` 模块与 `data/ragas/` 目录一并改名，做到"项目里不再有 RAGAS"。
- 明确"新口径不可比"，产出新 baseline。

**Non-Goals**
- 不标定新阈值（与 `deepeval-only-eval` 一致，留给 v6 后）。
- 不做 `ToolCorrectness`（依赖 v6 的 `expected_tools`）。
- 不改 Agent 图、生产链路、`kb_eval` API 形状、前端结构。
- 不清理 `datasets` / `rapidfuzz` / `huggingface_hub`（等 v6 出题落地后按实际 import 再清）。

## Decisions

### D1 四指标换成 DeepEval 原生，judge 必须是 `BridgeDeepEvalLLM`

- `build_metrics(judge: BridgeDeepEvalLLM)`：`AnswerRelevancy` / `Faithfulness` / `ContextualPrecision` / `ContextualRecall`，全部 `model=judge`。
- **删 `embeddings` 参数**：原生 `AnswerRelevancy` 不用 embeddings；调用点与测试同步收窄。
- `main()` 里构造一次 `BridgeDeepEvalLLM(bridge_judge)`，四指标与行为轴共用。
- **评分编排不变**：`score_rows` 仍串行逐题 `metric.measure(test_case)`。
- **理由**：审计确认 `initialize_model` 只接受 `str | DeepEvalBaseLLM`，直接传 langchain 模型会 `TypeError`；复用同一个包装器避免两套 judge。
- **代价**：与桥的内部实现不同，分数不可比。

### D2 judge 温度显式设 0（删 ragas 后不再有"按 n 分档"）

- `build_bridge_judge` 增加 `temperature=0`，删除"温度由 ragas 决定"的注释与报告里的分档字段。
- **理由**：现在不传温度是因为 `LangchainLLMWrapper` 会按 `n` 改写；删 ragas 后该层消失，温度会落到 provider/`LLM_KWARGS` 默认值（生产模型实测 0.6），裁判 JSON 判定更不稳（spike 已见一次 `invalid JSON`）。
- **代价**：与旧桥的 0.01/0.3 不同；反正要新基线。

### D3 诊断指标：Relevancy 截断 8、NonAdvice financial、ToolCorrectness 延后

- `ContextualRelevancy`（不需要 `expected_output`）：
  - **在评分层截断**：为它单独构造 `retrieval_context=contexts[:8]` 的 test_case，**不得**原地修改共享 test_case（否则 CP/CR/faithfulness 一起变）。
  - 截断上限常量入 `src/config/const.py`（如 `EVAL_RELEVANCY_MAX_CONTEXTS = 8`）。
  - 报告标注"Relevancy 仅判前 8 条（并集顺序，非最相关 8 条）"。
  - **理由**：源码确认它是**每 context 一次 LLM + gather**；Q14 的 20 条产生 42 个 hook 事件。`gather` 中任一条非法 JSON 会让整个指标失败并触发整题重试，条数越多失败面越大。
- `NonAdviceMetric(advice_types=["financial"])`：只进报告诊断段。
- `ToolCorrectness`：本 change **不构造**；等 `deepeval-synthesizer-testset` 产出 `expected_tools` 后再启用（v5 上记 `N/A`）。
- 三者**不进主分/门禁**，不写 `eval_report` 的四指标列；**逐题值写入 `detail_json`**，键名用裸指标名（`contextual_relevancy` / `non_advice` / `tool_correctness`），与四指标同级，下钻脚本无需特判；`tool_correctness` 写 `null`（键存在、值为空），与四指标"缺值 → null"统一。`kb_eval` API **不返回**诊断值（只存不读）。
- **理由（裸键名）**："不进主分"由 `EVAL_METRIC_COLUMNS` 白名单保证，不靠键名前缀；`detail_json` 是报告之外唯一可下钻的逐题载体。

### D4 彻底退役 RAGAS

| 动作 | 说明 |
|---|---|
| 删依赖 | `pyproject.toml` 删除 `ragas==0.3.9`；`click` / `huggingface_hub` 的 pin 暂留，理由写入 ADR-0027 |
| 删桥 | `build_metrics` 不再 import `deepeval.metrics.ragas` |
| 迁加载器 | 新建 `src/cli/testset_io.py`，迁入 `_load_latest_testset`；**返回完整 sample**（含 `expected_tools`，为 v6 预留），调用方按需取字段 |
| 删出题模块 | 删除 `src/cli/eval_ragas_generate.py`（`_ensure_vertexai_stub` 一并删除） |
| 保留 `--generate` | CLI 参数**不删除**，命中即明确报错并指向 DeepEval `Synthesizer`（与 API 410 对齐），避免 `unrecognized arguments` |
| 下线 API | `POST /api/ragas/generate` 返回 410 + 前迁路径；`src/main.py` 摘除路由 |
| 删 429 豁免 | `_score_metric_with_retry` 不再短路 429；由外层 3 次有界重试统一负责（原豁免的唯一依据是 ragas 层重试，已消失） |
| 删 `version("ragas")` | `build_report_meta` 的 `runner_versions` 只留 deepeval（并加 `metrics="native"`） |

### D5 命名与路径全量切换（用户拍板，**放在指标与退役之后**）

| 项 | 旧 | 新 |
|---|---|---|
| CLI 模块 | `src/cli/eval_ragas.py` | `src/cli/eval_deepeval.py` |
| 对应测试 | `tests/cli/test_eval_bridge_*.py` / `test_eval_ragas*.py` | `tests/cli/test_eval_native_*.py` / `test_eval_deepeval*.py` |
| 数据根 | `data/ragas/` | `data/deepeval/` |
| 配置前缀 | `RAGAS_JUDGE_*` / `RAGAS_HTTP_*` / `RAGAS_METRIC_MAX_ATTEMPTS` / `RAGAS_LLM_MODEL` / `RAGAS_PROVIDER` | `JUDGE_*` |
| 评测目录 | `RAGAS_DATA_DIR` / `RAGAS_TESTSET_DIR` / `RAGAS_REPORT_DIR` / `RAGAS_LLM_CACHE_DIR` / `RAGAS_USER_ID` | `EVAL_*` |
| 指标白名单 | `RAGAS_METRIC_COLUMNS` | `EVAL_METRIC_COLUMNS` |
| 出题专用 | `RAGAS_TEST_SIZE` / `RAGAS_TEST_PERSONAS` / `RAGAS_SELF_CONTAINED_HINT` / `RAGAS_DOC_WHITELIST` | **删除** |
| Langfuse session | `ragas_eval_session` | `deepeval_eval_session` |
| 台账文档 | `docs/agents/ragas-eval-ledger.md` | `docs/agents/eval-ledger.md` |
| CLI 开关 | `--deepeval-only` | 保留，加 `--behaviour-only` 别名 |

- **改名后置的理由**：改名是纯机械变更；先改会让后续每个中间 diff 混入大量路径噪声，且指标替换需回滚时改名也要跟着回滚。代价是文档改两遍（先改指标语义、再改路径）。
- **数据迁移**：`mv data/ragas data/deepeval`（`data/` 是共享软链，迁移即对主工作区生效）；同步 `settings.py` 默认值、`deploy.sh` 的 `mkdir -p`、`docker-compose.image.yml` 的挂载、`.env.example`/`.env.template`。
- **旧目录不自动删除**：`deploy.sh` 只建新目录，**SHALL NOT** `rm` 旧 `data/ragas`（含 29M 历史报告，删除不可逆）；runbook 写明"确认不再需要后人工清理"。
- **历史 `report_path` 不动**：DB 27 行指向 `data/ragas/`，该字段只写不读、API 也不返回；不动历史数据，runbook/台账注明"历史路径已失效、无消费方"。
- **旧环境变量不兼容**：启动期检测到任何 `RAGAS_*` 即 **fail-fast**，报错列出"旧名 → 新名"映射；**不 fallback**（fallback 会让旧名长期半生效，与"彻底脱钩"冲突）。失败即提示比静默半生效安全。
- **模板只写新名**：`.env.example` / `.env.template` 不保留"旧名已废弃"注释（否则看起来两个都支持），迁移对照表放 `deploy-runbook.md`。
- **出题 KG 缓存** `data/ragas/kg_*.json` 随出题路删除，不迁移。
- **代价**：改名与迁移的 diff 大；外部脚本若硬编码 `src.cli.eval_ragas` 或 `data/ragas` 会断（仓库内已全部纳入改造）。

### D6 口径、基线与门禁

- 原生四指标与 RAGAS 四指标**不可比**；旧报告保留，不作对照。
- 首次全量写 `run_type="baseline"`、`passed=False`；`kb_eval` 对 `run_type="baseline"` 返回 `passed=None`。
- **`run_type` 由 CLI 参数写入**：新增 `--run-type`（默认 `manual`），验收时显式传 `baseline`；**不用**"自动判断首轮"（要查 DB、有并发歧义）。`_save_eval_report` 签名增加 `run_type` 参数。
- **前端 `passed` 三态**：`null` 显示"未标定"（灰色），**不显示 ✓/✗**；改 4 处 JS（KB 徽标 `:1100-1102`、弹窗 `:1115`、文案 `:1143`、总分 `:1145`）+ 1 个灰色样式。`overallCls` 不再用 `overall >= 0.7` 重算，改由 `passed` 三态决定，消除"气泡 pass、文案 ✗"的自相矛盾。
- **不改 `eval_report` schema**（不加 caliber 列）；"加列"登记为后续。
- Langfuse score 后缀由 `@bridge-0.3.9` 改为 `@deep-eval-native`；台账「口径版本」记 `deep-eval-native`。
- **理由（baseline 不改 schema）**：`passed` 非空 Boolean、加列要迁移 + 改 API/前端，而 `run_type` 字段已存在，足以区分"baseline 不判门禁"；库里仍写 False 的代价由 `run_type` 约定兜住。

### D7 失败与重试语义

- 原生指标的异常（`ValueError: invalid JSON` 等）由 `_score_metric_with_retry` 的 `except Exception` 捕获 → NaN → 有界重试 3 次；行为与桥一致。
- `answer_relevancy` 等对空 `actual_output` 会抛 `MissingTestCaseParamsError`：跑图失败题（`answer=""`）在进入评分前**直接记 NaN 并跳过该题的四指标**，不浪费 3 次重试。
- 429 不再短路（见 D4）。
- `HookEvidence` 仍只做留证；原生指标内部并发会让同一题的事件交错，但 `trace_id` 相同（评分串行、题内并发），不串题。

### D8 ADR-0027 完全取代 ADR-0026 / 0022 / 0023

- 0027 记录：原生指标替换、judge 温度改显式 0、429 策略变更、出题路退役、命名/路径切换、`click`/`huggingface_hub` 保留 pin 的理由。
- 0026（裁判 client 政策）、0022（RAGAS 出题输入边界）、0023（白名单只约束出题）**三份都标完全取代**（正文全部围绕 RAGAS 桥/出题，随出题路删除整体失效；"局部"需逐条列举仍有效项，徒增歧义）；三份 `Status` 加指向 0027 的反向指针并更新索引。
- **理由**：三者失效原因同一（不再依赖 RAGAS），拆多个 ADR 会产生重复背景。

### D9 执行顺序（改名后置）

1. 四指标换原生（D1 / D2）
2. 诊断指标（D3）
3. 退役 RAGAS（D4 / D7）
4. 改名与数据迁移（D5）
5. 文档与 ADR（D8 / D6）
6. 验收（D6，**全部跑在新路径/新命令上**）

- **理由**：验收必须在最终命名上跑，否则验完还要再验一次；改名放在退役后，保证改名时功能已稳定、只需验证"路径替换无误"。

## Risks / Trade-offs

| # | 风险 | 应对 |
|---|---|---|
| R1 | 原生指标分数与旧基线差异大，被误读为"质量回退" | 台账/报告标 `deep-eval-native`；首轮 baseline 不判门禁 |
| R2 | `ContextualRelevancy` 的 `gather` 一坏全坏 | 截断 8 条 + 外层 3 次重试；报告标注截断口径 |
| R3 | 改名/迁移导致外部脚本或部署配置失效 | 仓库内全量替换；`.env`/runbook/compose 同步；在 plan 里列清单；旧 env fail-fast 给出改名提示 |
| R4 | 删 `ragas` 后仍有隐藏 import（出题 API/测试） | `grep -rn 'ragas'` 零命中（排除 `eval_deepeval.py` 文件名与归档文档）作为验收判据 |
| R5 | `data/deepeval/` 迁移影响主工作区（共享软链） | 迁移前确认无进程占用；迁移后跑一次报告路径校验 |
| R6 | 原生指标成本高于桥 | spike 数据：21 题 30–50 分钟；若超预算，优先降 Relevancy 条数 |
| R7 | 前端 `passed=null` 仍被渲染为 ✗ | D6 明列 4 处 JS + 1 个样式；验收时用 baseline 行实看徽标与弹窗文案一致 |
| R8 | 旧 env fail-fast 导致部署窗口起不来 | runbook 提供完整「旧名 → 新名」对照表；部署为一次性切换，失败信息直接给出要改的键 |
