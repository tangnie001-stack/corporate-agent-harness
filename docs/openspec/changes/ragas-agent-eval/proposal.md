## Why

Agent 已就绪（工具 / 技能 / 多轮），但现有 RAGAS 评测**鉴定不了 Agent 的回答质量**，原因有二：

1. **context 类指标恒失效**（已登记缺陷 `F-32`）：评测链路从不设置 `RequestContext`，而 `agent_finalize` 只从 `current_request_ctx` 读 `tool_contexts` → 工具把检索材料写进一个即弃列表 → 评测拿到的 `contexts` 恒空 → `context_precision` / `context_recall` 恒 0、`faithfulness` 算 nan。
2. **指标口径是单轮 RAG 组件**：四个指标（faithfulness / answer_relevancy / context_precision / context_recall）都不回答「答对了没有」，缺「答案正确性」这一 Agent 评测最核心的维度。

同时，承载评测规格的主规格 `evaluation-pipeline` **已过时**——引用了已死的命令（`python -m src.eval_ragas`、`python -m scripts.compare_chunk`）与旧报告路径（`data/reports/`）。既然正在重做 RAGAS，本次一并把它改对，不留一份过时规格。

## What Changes

- **修 `F-32`**：评测链路在每题调用图之前注入 `RequestContext`（eval 侧 seed，镜像 `turn_runner._run_with_finalize`），使工具收集的检索材料进入图状态，恢复 context 类指标。
- **含联网样本处置**：仅**纯知识库样本**（检索材料不含 `kind=web` 块）参与常规上下文指标（主分）；含联网材料的样本标记为「降级联网」并从主分排除（「是否降级联网」记入诊断元数据）；`contexts` 为空的样本**跳过** context 类指标（单列为「无检索直答」）。
- **新增答案正确性指标**：用 **legacy `from ragas.metrics import AnswerCorrectness`**（与现有四指标同一条 `evaluate()` 路径，对 `reference` 计算，输出 0–1）补上「答对没」这一维度（区别于忠实度与相关性）。准则式 `RubricsScoreWithReference`（可定制 + feedback）推迟为后续增强。
- **采集 Agent 消息轨迹**：把 `state.messages`（含每次工具调用）转成评测侧结构，工具调用序列 / 迭代次数作为**诊断元数据**保存，**不单独评分**（常规检索轮路径近似恒定，无区分度）。
- **评测结果回写 Langfuse**：每题分数回写到对应 trace（沿用既有接线口径）。
- **对齐 `evaluation-pipeline` 过时条款**：改正命令与路径（第 1、3 条）；移除已死的「分块尺寸对比」（第 2 条，Migration 指向既有 `compare_retrieval`）。

## Capabilities

### New Capabilities

（无 —— 本次是重做既有评测能力，不新开 capability。）

### Modified Capabilities

- `evaluation-pipeline`：① MODIFY「QA test pair coverage」对齐按 KB 生成测试集；② MODIFY「Evaluation report archival」对齐实际命令与报告路径；③ REMOVE「Chunk size comparison」（路径已死；Migration 指向既有 `compare_retrieval`，**不新增规格条款**）；④ ADD 评测链路检索材料收集（修 F-32）+ 纯 KB 样本出主分 / 含联网样本另标；⑤ ADD 答案正确性指标；⑥ ADD Agent 轨迹作为诊断元数据。
- `llm-tracing`：ADD「评测结果回写为 trace 分数」。

## Impact

- **代码**：`src/cli/eval_ragas.py`（seed `RequestContext`、轨迹采集、指标、score 回写）；仅**只读参照** `src/services/turn_runner.py`（seed 方式）与 `src/rag/context.py`（`kind` 字段），不改它们。
- **依赖**：无新增（`ragas==0.4.3` 已含所需指标；legacy `AnswerCorrectness` 亦在其内）。
- **数据**：**无测试集 schema 变更**（答案正确性只需已有 `reference`）；**不改数据库**（不扩 `eval_report` 表、不改前端）。
- **行为**：评测报告多出「答案正确性」与按来源分流的 context 指标；Langfuse 出现新的 score 记录。
- **不改**：`src/agents/graph/agent_node.py`（F-32 由 eval 侧 seed 解决，不碰生产节点）、`eval_report` 表 / `kb_eval` API / 前端。
