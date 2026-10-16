# Tasks

> 执行顺序即编号顺序；每组内为 TDD 小步（先写失败测试 → 实现 → 跑通 → 提交）。
> 规格：`specs/evaluation-pipeline/spec.md`；决策：`design.md`（D1–D10）；风险：`design.md` 的 R1–R8。

## 1. 依赖与环境（D1）

- [ ] 1.1 `pyproject.toml`：`ragas` 由 `0.4.3` 改为 **`ragas==0.3.9`**（精确 pin，与仓库既有 pin 纪律一致）；新增 `deepeval`；显式记录 `click` 与 `huggingface_hub` 的下限，防回落。
- [ ] 1.2 重建环境并留证：`pip install -e .` 后 `pip check` 无**新增**冲突；记录实装版本（`ragas` / `deepeval` / `click` / `huggingface_hub`）。
- [ ] 1.3 自查：全仓 grep 确认评测路径不再引用 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment`。

## 2. 评测入口与指标面（D2）

- [ ] 2.1 写失败测试：`build_metrics()` 返回的指标集为**经典四项**，且**不含** `RagasMetric` / `RAGASContextualEntitiesRecall`。
- [ ] 2.2 写失败测试：每个桥度量构造时**显式传入 `model=`**（防止回落到 `gpt-3.5-turbo`）。
- [ ] 2.3 实现：`src/cli/eval_scoring.py` 的指标构造改为桥的四个单项；`RAGASAnswerRelevancyMetric` 传入显式 langchain `Embeddings`（指向现有 embedding 端点）。
- [ ] 2.4 实现：`src/cli/eval_ragas.py` 打分阶段由 modern `@experiment` 改为桥度量驱动（逐样本构造 `LLMTestCase`：`input` / `actual_output` / `expected_output` / `retrieval_context`）。
- [ ] 2.5 跑通并提交。

## 3. 裁判与元数据（D3 / D9）

- [ ] 3.1 写失败测试：报告元数据含 `ragas` 版本、`deepeval` 版本、judge 模型、**温度档位说明（0.01 / 0.3，本项目不可控）**、`model=` 已显式传入的标记。
- [ ] 3.2 实现：报告头与台账条目补齐上述字段；断言报告中 SHALL NOT 出现"裁判 temperature=0"表述。
- [ ] 3.3 跑通并提交。

## 4. 失败语义与主分口径（D4 / D7）

- [ ] 4.1 写失败测试：桥返回 `NaN` 时该行**仍存在**、携带失败信息、被计入 `NaN` 计数，且**不参与**主分均值。
- [ ] 4.2 写失败测试：`NaN`（裁判故障）与样本类别 `empty`（未检索）在报告中**标记不同**。
- [ ] 4.3 实现：`apply_sample_masks` / `main_score_means` 改为「纯 KB **且非 NaN**」的四指标均值；报告新增 `NaN` 计数。
- [ ] 4.4 实现：`eval_report` 落库与 `kb_eval` API 去掉 `answer_correctness`。
- [ ] 4.5 跑通并提交。

## 5. Agent 行为轴（D6）

- [ ] 5.1 写失败测试：评测侧可取到含 `AIMessage.tool_calls` 与 `ToolMessage` 的**完整消息序列**（非摘要）。
- [ ] 5.2 实现：把 `final_state["messages"]` 沿 `_collect_one_question → generate_answers_and_contexts → run_evaluation` 透传。
- [ ] 5.3 选型并接入 ≤5 项 DeepEval 原生行为指标（候选：`TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`；清单在 design 定稿后再落）。
- [ ] 5.4 跑通并提交。

## 6. 出题路下线（D8）

- [ ] 6.1 实现：评测入口不再触发测试集生成（输入为既有版本文件）。
- [ ] 6.2 实现：`src/cli/eval_ragas_generate.py` 文件头标注"保留未调用（依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`）"；台账同步登记。
- [ ] 6.3 跑通并提交。

## 7. 逃生门（D10，结构预留）

- [ ] 7.1 实现：把"采样/用例构造"与"评分"解耦到可替换的边界（桥为默认实现，替代实现为项目侧直调 ragas 0.3 线）。
- [ ] 7.2 实现：台账增列"口径版本"与"是否启用逃生门 + 原因 + 轮次"。
- [ ] 7.3 跑通并提交。

## 8. 0.4.x 遗留处置（D11）

> 原则：**不是"删光"，而是三分——复用 / 删除 / 改写**。每一项都要有落点，不得留悬空引用。

- [ ] 8.1 **复用（改嫁到 ChatOpenAI）**：`RAGAS_JUDGE_MAX_TOKENS` → `ChatOpenAI(max_tokens=…)`；`RAGAS_JUDGE_THINKING` → `ChatOpenAI(extra_body={"enable_thinking": …})`；`RAGAS_HTTP_TIMEOUT_{CONNECT,READ,WRITE,POOL}` → `ChatOpenAI(http_client=httpx.Client(timeout=httpx.Timeout(...)))`；`RAGAS_HTTP_MAX_RETRIES` → `ChatOpenAI(max_retries=0)`。**依据**：已抓包证实 `max_tokens` / `extra_body` 经桥透传；已核 `ChatOpenAI` 有 `http_client` / `max_retries` 字段 ⇒ **ADR-0024 的"细粒度超时 + 关 SDK 重试"两项收益完整保住**，只是承载点从 `build_native_client()` 换到 ChatOpenAI。
- [ ] 8.2 **复用（本层仍需要）**：`RAGAS_METRIC_MAX_ATTEMPTS`（桥无内置重试）+ `RAGAS_JUDGE_TIMEOUT` 语义并入 8.1 的 timeout。
- [ ] 8.3 **删除**：`src/infra/llm/provider/factory.py` 的 `build_ragas_judge()` / `build_ragas_embeddings()`；`build_native_client()` 若确认无其他调用方则一并删除（先全仓核引用）。
- [ ] 8.4 **删除/改写**：`src/cli/eval_ragas.py` 的 `@experiment()` 编排、`_EvalResult`、`_score_metrics`、`_score_metric_with_retry`、`_exc_chain_summary`、`_judge_failure_note`；`src/cli/eval_scoring.py` 的 `build_metrics()`（现代 collections 版）。
- [ ] 8.5 **改写**：`judge call` / `metric retry` 日志事件（埋点位置从"我们调裁判"变为"桥内部调 ragas"）；`src/cli/trace_logs.py` 的适用范围随之重述。
- [ ] 8.6 **测试**：删除/重写 `tests/cli/test_eval_modern_stack.py`、`tests/cli/test_metric_retry.py`、`tests/infra/llm/provider/test_ragas_modern_factories.py`、`test_native_client.py`；核对 `test_registry_models.py`（judge 角色绑定是否仍有其他消费方）。
- [ ] 8.7 **ADR**：新增一条 ADR 取代 `docs/adr/0024-ragas-judge-client-policy.md` 的裁判 client 政策（含 `temperature=0` 那条），并按仓库约定在旧 ADR 的 Status 加**反向指针**、更新 ADR 索引表。
- [ ] 8.8 **死引用自查**：`grep -rn` 确认全仓不再出现 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment` / `build_ragas_judge` / `build_ragas_embeddings` 的**悬空引用**（出题路的 0.4.x 依赖例外，需在第 6 组标注）。
- [ ] 8.9 跑通并提交。

## 9. 验收

- [ ] 9.1 静态闸门：`ruff check` / `pyright`（不新增 error）/ `openspec validate deepeval-bridge-eval --strict` / `python -m src.cli.check_docs` 全过。
- [ ] 9.2 活体：1 题 → 5 题 → 全量 21 题；逐轮确认四项指标与行为指标均有值、`NaN` 计数为 0 或已解释。
- [ ] 9.3 **阈值重标定**：按全量分布取门禁阈值，写入单一常量，并更新 `kb_eval` 下发。
- [ ] 9.4 台账新增一行（标注口径版本 = bridge-0.3.x），历史行不改。
- [ ] 9.5 验收记录写回计划文件并提交。
