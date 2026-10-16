## Why

**现状**：评测用 RAGAS **0.4.x 现代栈**（`ragas.metrics.collections` + `llm_factory` + `@experiment`）去评一个**多轮 LangGraph Agent**，属范式错配，已实测出两类硬问题：

1. **喂法错配**：`src/cli/eval_ragas.py:164` 把 Agent **全部累积工具产出**（跨多次 `retrieve_kb`，不去重、不重排）当作 RAGAS 的 `retrieved_contexts`。而 RAGAS 的 `c(q)` 定义是"为回答**这个** q 检索出的那批"——多轮并集**既不是任何一轮的 `c(qᵢ)`、也不是 `c(q)`**。后果实测：`ctx=5` 的题 context_precision 均值 ≈0.73，`ctx≥10` 的题 ≈**0.17**。
2. **口径不可复现**：同题面、同 TOP_K，跨配置出现 **1.0 → 0.0** 的翻转；且存在 `context_recall=0.875` 而 `context_precision=0.0` 的自相矛盾行（Q13 已用原始 context 逐条证实是裁判判错，不是检索错）。

**为什么改成 DeepEval 入口**：DeepEval 对"评什么"有原生姿态——上下文轴与 **agentic 行为轴**（`task_completion` / `step_efficiency` / `plan_*` / `tool_correctness` / **`agent_loop_detection`**）都齐，且自带 reason、可 debug、JSON 可约束、与 pytest/CI 集成。它内建的 **RAGAS 桥**（`deepeval/metrics/ragas.py`）能让我们**继续用 RAGAS 的经典四指标**，不必自己维护第二套 ragas 管道。

**为什么彻底弃用 0.4.x**：经源码与官方材料核对，RAGAS 现代与经典**公式同源**（官方迁移标准就是"新实现匹配 legacy 行为"，PR #2370 并配了逐指标比分工具），差别只在**接口**（`ascore` vs `evaluate`）与 **LLM 管道**（现代可传 `temperature=0`、经典由 `LangchainLLMWrapper` 统一改写）。既然决定"只保留一条"，就没有理由同时背 0.4.x 的 API 面与依赖面。

## What Changes

- **评测打分入口统一到 DeepEval**：以 DeepEval 的度量对象 + `evaluate()` / `pytest` 形态承载评测，接受其温度与结果口径。
- **RAGAS 指标经 DeepEval 桥调用，采用经典四指标**：`RAGASContextualPrecisionMetric` / `RAGASContextualRecallMetric` / `RAGASAnswerRelevancyMetric` / `RAGASFaithfulnessMetric`（或聚合 `RagasMetric`）。
- **弃用 RAGAS 0.4.x 现代栈**：移除对 `ragas.metrics.collections` / `llm_factory` / `@experiment` 的依赖；项目内 ragas 下移到 **0.3.x**。
- **出题生成下线、测试集加载保留**：`src/cli/eval_ragas_generate.py` 原样留存（只作为历史能力），其**出题入口**（`run_generate` / `--generate`）不再被调用；但该模块的**测试集加载器** `_load_latest_testset` / `_ensure_vertexai_stub` **仍被评测主流程使用**（`src/cli/eval_ragas.py:803/818`）⇒ 文件不可删。其出题生成依赖的 0.4.x `default_transforms_for_prechunked` 随之不再需要。
- **`answer_correctness` 退出评测**：它不属于 RAGAS 经典四指标，本次不做替代。
- **基线重跑、阈值重标定**：口径换了尺子（且 `answer_relevancy` 内部走 `n=3` / 温度 0.3），历史分数不作对照基线。
- **保留一个"逃生门"**：若桥的 NaN 或默认模型坑在实际运行中明显，改为 **DeepEval 只做采样与用例构造、单独跑 ragas 0.3.x 评分**。该路径不常驻、只在桥不可用时启用。

## Capabilities

### New Capabilities

（无 —— 本次是替换既有评测能力的实现与口径，不新开 capability。）

### Modified Capabilities

- `evaluation-pipeline`：① **ADD** 评测打分入口与指标来源（入口 = DeepEval；RAGAS 指标 = 经典四指标经桥调用，且**评分串行**）；② **REMOVE** 答案正确性指标（`answer_correctness` 退出，**delta 同步 REMOVE 主规格对应 requirement**）；③ **MODIFY** 主分口径与门禁阈值（换尺子 → 重标定 + 空窗期用 `--no-gate`；**新增 NaN 率门禁**）；④ **ADD** Agent 行为指标（DeepEval 原生族；**须由 `EvaluationDataset.evals_iterator` 驱动**、**逐题分从 `test_run_*.json` 的 `testCases[].metricsData` 回读**、"轨迹非空且取到分且生成器耗尽"否则 fail-loud；**行为轴不进主分/门禁**）；⑤ **MODIFY** 评测运行环境与依赖（`ragas==0.3.9` + deepeval + 两个 DeepEval 环境变量，**含回滚路径**）；⑥ **MODIFY** QA 出题路的调用状态（**出题入口**下线 + `--generate` 硬护栏 + 前迁路径；**测试集加载器保留在评测路径**）；⑦ **ADD** 0.4.x 遗留物的**显式处置**（复用 / 删除 / 改写；**含「NaN 分三类（掩码跳过 / 合法缺值 / 裁判失败）、仅裁判失败类参与重试与门禁」**）；⑧ **ADD** Langfuse v2 回写口径（自建通道 / 行为分一并回写 / NaN 不写 / 新 score 名带口径后缀）；⑨ **ADD** NaN 门禁判据（**经验初值 10% / 待首轮复核；分子分母定义**）与逃生门自动判定；⑩ **ADD** 对官方两处推荐路径的**偏离记录**（自建门禁、**串行评分**）。

## Impact

- **代码**：`src/cli/eval_ragas.py`（打分阶段从 modern `@experiment` 改为 DeepEval 度量对象；**重试判据从异常改为 `NaN`**）；`src/cli/eval_scoring.py`（`build_metrics` 从 modern collections 改为桥的度量构造；`apply_sample_masks` / `main_score_means` 口径需随指标名与 NaN 语义调整）；`src/cli/eval_ragas_generate.py`（**出题入口**去调用，**测试集加载器保留**，文件不可删）。
- **0.4.x 遗留清理（本次**新增**范围，见 design D11）**：删 `build_ragas_judge()` / `build_ragas_embeddings()`（`src/infra/llm/provider/factory.py:132/169`）与（核引用后）`build_native_client()`；删/改写 `_EvalResult` / `_score_metrics` / `_exc_chain_summary`，`_score_metric_with_retry` 改判据，`_judge_failure_note` 删除（桥不给异常）；**复用** `RAGAS_JUDGE_MAX_TOKENS` / `RAGAS_JUDGE_THINKING` / `RAGAS_HTTP_TIMEOUT_*` / `RAGAS_HTTP_MAX_RETRIES` / `RAGAS_METRIC_MAX_ATTEMPTS`（经 **`build_chat("judge", …)`** 改嫁到 provider 层：`max_tokens` / **`thinking=`**（经 `thinking_extra_body` 归一化，不硬写 `enable_thinking`）/ `http_client`(细粒度 `httpx.Timeout`) / `max_retries=0`——已抓包证实透传、已核字段存在；**细粒度超时保住；但"重试维度完整保住"不成立**：`max_retries=0` 只关 SDK 重试，**ragas 框架层自带 `RunConfig.max_retries=10`（限流类）且桥不暴露** ⇒ 登记为接受的偏离 + 外层不重试限流类）；删/重写 `tests/cli/test_eval_modern_stack.py`、`test_metric_retry.py`、`tests/infra/llm/provider/test_ragas_modern_factories.py`、`test_native_client.py`。
- **文档**：**追加新 ADR** 取代 `docs/adr/0024-ragas-judge-client-policy.md` 的裁判 client 政策，并在旧 ADR 的 Status 加**反向指针** + 更新 ADR 索引；`docs/agents/logging-rules.md` 的 `judge call` / `metric retry` 事件口径随埋点位置重述。
- **依赖**：新增 `deepeval`（4.2.9 量级）；`ragas` 由 **0.4.3 → 0.3.9**；连带 `click`、`huggingface_hub` 下移（见 design D1）。**这是本次唯一的"不可逆"动作**（`pyproject.toml` 的 pin 要改）⇒ **必须写明回滚路径**（恢复 `ragas==0.4.3` + `datasets==4.0.0`，并同时放弃 deepeval 与行为轴；触发条件见 design D1）。
- **数据**：**`eval_report` 表无 schema 迁移**（订正：该表**本就无** `answer_correctness` 列，且已有四列正是经典四指标——见 `src/infra/db/models/eval_report.py`）。会随指标名/阈值变的是 `detail_json` 口径与报告；`data/ragas/reports/` 旧报告**不再作为基线**。
- **行为**：门禁阈值必须在重跑后重新标定；报告与台账的指标名从"现代五指标"变为"经典四指标 + agentic 族"。
- **观测**：**DeepEval 无 Langfuse 集成**（其 Integrations 清单不含 Langfuse；Langfuse 侧那份配方用的是 v3/v4 API，与本项目自托管 v2 不匹配），故回写沿用**项目自建通道**（`langfuse_context.client_instance` + `client.score`）；**行为轴指标一并回写**；`NaN` **不写**（SHALL NOT 以 `0` 代替）；新口径 score 名**带后缀**（如 `faithfulness@bridge-0.3.x`），不与历史同名覆盖。
- **被测图侵入面**：行为轴要求被测图标配 `EvaluationDataset.evals_iterator(...)` 驱动的循环，并在循环体内 `graph.ainvoke(..., config={"callbacks": [CallbackHandler()]})`（**handler 不挂 metrics**）；生产链路不动。**订正**：原文"`Golden` / `evals_iterator` 在基本路径上不需要"**是错的**——`evals_iterator` 是**必需**的驱动（实测只挂 handler 时 `metric.score=None`）。
- **不改**：Agent 图与生产链路（`src/agents/**`）、`kb_eval` API 的形状、前端。
