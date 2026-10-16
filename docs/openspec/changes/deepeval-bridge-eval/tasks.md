# Tasks

> 执行顺序即编号顺序；每组内为 TDD 小步（先写失败测试 → 实现 → 跑通 → 提交）。
> 规格：`specs/evaluation-pipeline/spec.md`；决策：`design.md`（D1–D12）；风险：`design.md` 的 R1–R10。

## 1. 依赖与环境（D1）

- [ ] 1.1 `pyproject.toml`：`ragas` 由 `0.4.3` 改为 **`ragas==0.3.9`**（精确 pin，与仓库既有 pin 纪律一致）；新增 `deepeval`；显式记录 `click` 与 `huggingface_hub` 的下限，防回落。
- [ ] 1.2 重建环境并留证：`pip install -e .` 后 `pip check` 无**新增**冲突；记录实装版本（`ragas` / `deepeval` / `click` / `huggingface_hub`）。
- [ ] 1.3 自查：全仓 grep 确认评测路径不再引用 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment`。
- [ ] 1.4 **环境变量（官方明文，防污染）**：评测运行环境显式设 `DEEPEVAL_DISABLE_DOTENV=1`（否则 import 即把 `.env`/`.env.local` 注入 process env）与 `DEEPEVAL_TELEMETRY_OPT_OUT=1`（否则 posthog 遥测开启）；加测试断言两者生效。

## 2. 评测入口与指标面（D2）

- [ ] 2.1 **先建 langchain 裁判与 Embeddings（前移自第 9 组的"改嫁"；本组必须先做，否则本组提交点必红——桥要的是 langchain `BaseChatModel`，旧 `build_ragas_judge()` 产出的是 `InstructorBaseRagasLLM`，类型不接）**：`ChatOpenAI(model=<judge>, api_key=<DASHSCOPE>, base_url=<dashscope>, temperature=0, max_tokens=RAGAS_JUDGE_MAX_TOKENS, extra_body={"enable_thinking": RAGAS_JUDGE_THINKING}, http_client=httpx.Client(timeout=httpx.Timeout(connect/read/write/pool=RAGAS_HTTP_TIMEOUT_*)), max_retries=RAGAS_HTTP_MAX_RETRIES)`；`Embeddings` 复用现有 `build_embeddings()`。
- [ ] 2.2 写失败测试：指标集为**经典四项**，且**不含** `RagasMetric` / `RAGASContextualEntitiesRecall`。
- [ ] 2.3 写失败测试：每个桥度量构造时**显式传入 `model=`**（防回落到 `gpt-3.5-turbo`；该默认值在源码里是 **7 处**）。
- [ ] 2.4 实现：`src/cli/eval_scoring.py` 的指标构造改为桥的四个单项；`RAGASAnswerRelevancyMetric` 传入显式 langchain `Embeddings`。
- [ ] 2.5 实现：`src/cli/eval_ragas.py` 打分阶段由 modern `@experiment` 改为桥度量驱动（逐样本构造 `LLMTestCase`：`input` / `actual_output` / `expected_output` / `retrieval_context`）。
- [ ] 2.6 实现：**四指标评分串行（并发=1）** —— 停用对桥度量的并发调用。**依据**：桥复用 `ragas.metrics` 的模块级指标实例，`ragas.evaluate` 会复位 `metrics[i].llm = None`、`LangchainLLMWrapper` 会改写 `chat_model.temperature` ⇒ 并发会互踩、间歇性错分（design D7）。
- [ ] 2.7 跑通并提交。

## 3. 裁判与元数据（D3 / D9）

- [ ] 3.1 写失败测试：报告元数据含 `ragas` 版本、`deepeval` 版本、judge 模型、**温度档位说明（0.01 / 0.3，本项目不可控）**、`model=` 已显式传入的标记。
- [ ] 3.2 实现：报告头与台账条目补齐上述字段；断言报告中 SHALL NOT 出现"裁判 temperature=0"表述。
- [ ] 3.3 **框架内部调用的可观测性（D13）**：给裁判的 `ChatOpenAI(http_client=…)` 挂 httpx **event hooks**，记录请求级日志（模型 / 耗时 / 状态码 / 请求体摘要）。**`trace_id` 以显式闭包绑定进 hook**，SHALL NOT 依赖 contextvar（ragas `evaluate` 内部用自己的 `Executor` 线程池，不保证携带）。验收范围限「**经我们 `http_client` 发出的请求**」。
- [ ] 3.4 跑通并提交。

## 4. 失败语义与主分口径（D4 / D7）

- [ ] 4.1 写失败测试：桥返回 `NaN` 时该行**仍存在**、携带失败信息、被计入 `NaN` 计数，且**不参与**主分均值。
- [ ] 4.2 写失败测试：`NaN`（裁判故障）与样本类别 `empty`（未检索）在报告中**标记不同**。
- [ ] 4.3 实现：`apply_sample_masks` / `main_score_means` 改为「纯 KB **且非 NaN**」的四指标均值；报告新增 `NaN` 计数。
- [ ] 4.4 实现：`eval_report` 落库与 `kb_eval` API 去掉 `answer_correctness`。
- [ ] 4.5 跑通并提交。

## 5. Agent 行为轴（D6，**必须 `evals_iterator` 驱动、分数从 `test_run` JSON 回读**）

> 已由 1 题 spike 实测（2026-10-16）：只挂 `CallbackHandler` **不评分**（`metric.score=None`、`reason=''`）；分数落在 eval loop 内部（`copy_metrics()` 副本），**只能从 `test_run_*.json` 的 `metricsScores` 回读**。

- [ ] 5.1 **先验异步可行性**：官方示例用同步 `invoke`，我们用 `ainvoke`。先探：`evals_iterator` 循环体内 `ainvoke` 能否被正确捕获、`async_mode` 取何值、对图有无副作用（1 题冒烟）。
- [ ] 5.2 写失败测试：**只挂 handler、不驱动 `evals_iterator` 时取不到分**（防止回归到被否定的形态）。
- [ ] 5.3 实现（两半缺一不可）：`EvaluationDataset(goldens=[Golden(input=<题面>, expected_output=<参考答案>)])` + `evals_iterator(metrics=[…], display_config=DisplayConfig(results_folder=<OUT>))` 驱动循环；循环体内执行 `graph.ainvoke(..., config={"callbacks": [CallbackHandler()]})`（**handler 不挂 metrics**）。
- [ ] 5.4 实现：从 `<OUT>/test_run_*.json` 的 `metricsScores` **回读**行为分；SHALL NOT 读调用方持有的 `metric.score`。**每题新建独立 `CallbackHandler` 实例**（不得跨题复用，否则串 trace）。
- [ ] 5.5 写失败测试 + 断言：**轨迹非空 且 已从 `test_run` 取到该指标的分**，否则 fail-loud（`agent_loop_detection` 无 trace 时源码直接置 0.0）。
- [ ] 5.6 **并发负向验证**：并发跑 2 题，断言两者的 trace 与分**不串**。
- [ ] 5.7 选型并接入 ≤5 项 DeepEval 原生行为指标（候选：`TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`）；**清单在本组内定稿并写回 design D6**（spec 已声明"实施期定稿"，不得与 design 互相推诿）。
- [ ] 5.8 `summarize_trajectory` 保留，仅作报告诊断摘要；**不得**作为行为指标数据源。
- [ ] 5.9 跑通并提交。

## 6. 出题路下线（D8）

- [ ] 6.1 实现：评测入口不再触发测试集生成（输入为既有版本文件）。
- [ ] 6.2 实现：`src/cli/eval_ragas_generate.py` 文件头标注"保留未调用（依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`）"，并**指向前迁路径**（DeepEval `Synthesizer`，见 design D8）；台账同步登记。
- [ ] 6.3 实现：**硬护栏** —— `--generate` 路径显式失败并给出指引（说明原因 + **前迁路径 DeepEval `Synthesizer`** + "若确需暂用旧路须临时环境 + ragas 0.4.x"），SHALL NOT 让用户撞到裸 `ImportError`；加测试断言该路径的报错文案。
- [ ] 6.4 确认评测输入为**固定版本文件**（v5 / 21 条），评测流程与出题解耦。
- [ ] 6.5 跑通并提交。

## 7. 逃生门（D10，结构预留）

- [ ] 7.1 实现：把"采样/用例构造"与"评分"解耦到**可替换边界**（桥为默认实现）。**本组不实现替代实现** —— spec 已同步降级为"首次启用逃生门时再实现并冒烟，且登记台账/需求池"，避免"规格要求实现、任务只做边界"的错配。
- [ ] 7.2 实现：台账增列"口径版本"与"是否启用逃生门 + 原因 + 轮次"；并把"替代实现待做"登记进需求池。
- [ ] 7.3 跑通并提交。

## 8. Langfuse 回写与 score 口径（D9）

> 前提事实：**DeepEval 无 Langfuse 集成**，Langfuse 侧配方是 v3/v4 API（与我们自托管 v2 不匹配）⇒ 回写全由我们自建。

- [ ] 8.1 实现：沿用 `src/cli/eval_scoring.py` 的 `write_scores` 通道（`langfuse_context.client_instance` + `client.score`；**不得** `new Langfuse()`）；**行为轴指标也一并回写**，与四项同级、共用同一 `trace_id`。
- [ ] 8.2 实现：**新口径 score 名带后缀**（如 `faithfulness@bridge-0.3.x`），**不覆盖**历史同名 score；加测试断言命名规则。
- [ ] 8.3 实现：**NaN 不写**（维持"缺失"语义，SHALL NOT 写 `0`）；在 design/代码注释写明该语义，避免后人当 bug 修。
- [ ] 8.4 跑通并提交。

## 9. 0.4.x 遗留处置（D11）

> 原则：**不是"删光"，而是三分——复用 / 删除 / 改写**。每一项都要有落点。
> **提交粒度**：删除动作 SHALL 与「调用点改写 + 受影响测试」**同一提交**；判据是**最终态**无悬空引用，不要求每个中间提交都干净（否则 TDD 小步的提交点必红）。
> **注**：9.1/9.2 的"改嫁"已在**第 2 组前移**（裁判必须在第 2 组就可用）；本组只做剩余复用与删除/改写。

- [ ] 9.1 **复用（改嫁到 ChatOpenAI）**：`RAGAS_JUDGE_MAX_TOKENS` → `ChatOpenAI(max_tokens=…)`；`RAGAS_JUDGE_THINKING` → `ChatOpenAI(extra_body={"enable_thinking": …})`；`RAGAS_HTTP_TIMEOUT_{CONNECT,READ,WRITE,POOL}` → `ChatOpenAI(http_client=httpx.Client(timeout=httpx.Timeout(...)))`；`RAGAS_HTTP_MAX_RETRIES` → `ChatOpenAI(max_retries=0)`。**依据**：已抓包证实 `max_tokens` / `extra_body` 经桥透传；已核 `ChatOpenAI` 有 `http_client` / `max_retries` 字段 ⇒ **ADR-0024 的"细粒度超时 + 关 SDK 重试"两项收益完整保住**，只是承载点从 `build_native_client()` 换到 ChatOpenAI。
- [ ] 9.2 **复用（本层仍需要）**：`RAGAS_METRIC_MAX_ATTEMPTS`（桥无内置重试）+ `RAGAS_JUDGE_TIMEOUT` 语义并入 9.1 的 timeout。
- [ ] 9.3 **删除**：`src/infra/llm/provider/factory.py` 的 `build_ragas_judge()` / `build_ragas_embeddings()`；`build_native_client()` 若确认无其他调用方则一并删除（先全仓核引用）。
- [ ] 9.4 **删除/改写**：`src/cli/eval_ragas.py` 的 `@experiment()` 编排、`_EvalResult`、`_score_metrics`、`_score_metric_with_retry`、`_exc_chain_summary`、`_judge_failure_note`；`src/cli/eval_scoring.py` 的 `build_metrics()`（现代 collections 版）。
- [ ] 9.5 **改写**：`judge call` / `metric retry` 日志事件（埋点位置从"我们调裁判"变为"桥内部调 ragas"）；`src/cli/trace_logs.py` 的适用范围随之重述。
- [ ] 9.6 **测试**：删除/重写 `tests/cli/test_eval_modern_stack.py`、`tests/cli/test_metric_retry.py`、`tests/infra/llm/provider/test_ragas_modern_factories.py`、`test_native_client.py`；核对 `test_registry_models.py`（judge 角色绑定是否仍有其他消费方）。
- [ ] 9.7 **ADR**：新增一条 ADR 取代 `docs/adr/0024-ragas-judge-client-policy.md` 的裁判 client 政策（含 `temperature=0` 那条），并按仓库约定在旧 ADR 的 Status 加**反向指针**、更新 ADR 索引表。
- [ ] 9.8 **死引用自查**：`grep -rn` 确认全仓不再出现 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment` / `build_ragas_judge` / `build_ragas_embeddings` 的**悬空引用**（出题路的 0.4.x 依赖例外，需在第 6 组标注）。
- [ ] 9.9 跑通并提交。

## 10. 验收

- [ ] 10.1 静态闸门：`ruff check` / `pyright`（不新增 error）/ `openspec validate deepeval-bridge-eval --strict` / `python -m src.cli.check_docs` 全过。
- [ ] 10.2 **首轮用 `--no-gate`**：先跑出全量分布（**不用旧阈值判新尺子**），确认四项指标与行为指标均有值、`NaN` 计数为 0 或已解释、行为分确系从 `test_run_*.json` 回读。
- [ ] 10.3 活体：1 题 → 5 题 → 全量 21 题，逐级确认。
- [ ] 10.4 **阈值重标定**：按 10.2 的分布取保守下界（≥均值×0.9 且 ≥历史同名×0.9，具体在分布出来后定稿），写入单一常量，并更新 `kb_eval` 下发。
- [ ] 10.5 **NaN 阈值复核**：以 10.2 的**实测 NaN 率**复核"经验初值 5%"，必要时改值并同步 spec。
- [ ] 10.6 **行为指标清单已定稿**（第 5 组 5.7 的产出已写回 design D6），且行为轴**不进主分/门禁**已在实现中体现。
- [ ] 10.7 台账新增一行（标注口径版本 = `bridge-0.3.9 / v5`），历史行不改。
- [ ] 10.8 验收记录写回计划文件并提交。
