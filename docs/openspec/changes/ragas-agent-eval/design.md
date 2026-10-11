## Context

本仓评测链路现状（实读核实）：

- `src/cli/eval_ragas.py:129` 直接 `graph.ainvoke({...})` 跑完整 Agent 图，**不经过** `turn_runner`；`_answer_one_question` 只取回 `answer` + `final_state["tool_contexts"]`。
- `RequestContext` 的 seed 只有两处：生产入口 `src/services/turn_runner.py:111`（`_run_with_finalize` 内 `set`）与 fork 子代理 `src/agents/skills/executor.py:231`。评测 CLI **两处都不经过**。
- 工具侧：`retrieve_kb`（`src/agents/tools/rag_tools.py:244-249`）与 `search_web`（`src/agents/tools/web_tools.py:137`）**共用** `ctx.tool_contexts` 累加器；`ctx is None` 时写入一个即弃的局部 list。
- `agent_finalize`（`src/agents/graph/agent_node.py:321-327`）只从 `current_request_ctx.get()` 读 `tool_contexts`；`ctx is None` 时返回空列表。
- ⇒ **`F-32`**：评测链路 `ctx` 恒为 None → 回传 state 的 `tool_contexts` 恒空 → context 类指标恒失效。
- `RAGContext.kind`（`src/rag/context.py:25-27`）区分 `kb` / `web`；`kind` 已随上下文块保存。
- 评测为**单轮**（`src/cli/eval_ragas.py:135` 传 `_history: []`）→ 历史 / 摘要逻辑不进入评测。
- `check_gate`（`src/cli/eval_ragas.py:432-470`）现取**全量 df 均值**判定门禁。
- Langfuse SDK 实测 `2.60.10`：`Langfuse.score(...)`、`langfuse_context.score_current_trace(...)` 均存在；既有口径要求 client 统一取 `langfuse_context.client_instance`、**不得 `new Langfuse()`**。

**关键事实（事实子代理实跑核实，决定 D3 选型）**：

- `ragas.metrics.collections` 的现代指标（`RubricsScoreWithReference` / `AnswerAccuracy`）**不是 `Metric` 子类**（继承独立的 `SimpleBaseMetric`）⇒ 与 legacy 指标**不能同列**进 `evaluate()` 的 `metrics=[...]`；**单独**喂 `evaluate()` 也抛 `TypeError: All metrics must be initialised metric objects`。
- 现代指标的 LLM 必须是 `InstructorBaseRagasLLM`；本仓现用的 `LangchainLLMWrapper` **不是**该类型，喂进去即 `ValueError`。要跑现代指标须引入 `llm_factory(...)` + OpenAI 兼容 client + `instructor`。
- **legacy `from ragas.metrics import AnswerCorrectness` 仍可用**（`MetricWithLLM + MetricWithEmbeddings + SingleTurnMetric`），**可直接进现有 `evaluate()`**（列映射 `ground_truth → reference`、embeddings 由 `evaluate` 自动接线），无需任何新接线。
- **`metrics=[...]` 必须放「已初始化的指标实例」**（`evaluation.py` 校验）；故须写 `AnswerCorrectness()` 而非类本身。

## Goals / Non-Goals

**Goals:**

- 修复 `F-32`，让评测能真正测到检索质量（context 类指标恢复）。
- 补上「答案正确性」维度，使评测能回答「Agent 答对了没有」。
- 让评测产出对单轮 RAG 场景（常规检索轮）可信、可下钻（工具轨迹作诊断），且**不可被"不检索"规避**。
- 顺带把 `evaluation-pipeline` 主规格的过时条款改对（不改第 6 条）。

**Non-Goals:**

- 不评多轮 / 追问（`ask_user`）、skill 直出（fork）轮。
- 不引入 DeepEval 等新框架；不做自研轨迹 judge（另案）。
- **不引入 `ragas.metrics.collections` 现代指标与其 `llm_factory`/`instructor` 接线**（D3：v1 用 legacy `AnswerCorrectness`）。
- 不扩 `eval_report` 表、不改 `kb_eval` API、不动前端。
- 不碰 `evaluation-pipeline` 第 6 条（当前日期，归 `prompt-layering`）。

## Decisions

**D1 — `F-32` 修法：eval 侧 seed `RequestContext`（选定）**
评测每题调用图前构造并 `set` 一个轻量 `RequestContext`（`session_id` 必填，`kb_id` 一并带上），调用后 `reset`——镜像 `turn_runner`。落地后 `agent_finalize` 会把 ctx 中的材料写入 `state.tool_contexts`，评测**无需**改 `agent_node.py`。
- 备选 A（`agent_finalize` 回落）：**否决**——`ctx is None` 时工具已把材料写进即弃 list，state 里没有可回落的数据。
- 备选 B（评测改走 `turn_runner`）：**否决**——`start_turn` 依赖会话锁 / 落库 / SSE 流 / 注册表，评测不需要且会污染数据。

**D2 — 能力归属：认领 `evaluation-pipeline`（选定）**
本次是「重做 RAGAS 评测」，`evaluation-pipeline` 即该能力的 owner；**不新开** `agent-evaluation`。约束：**不触碰第 6 条**（`prompt-layering` 正在 MODIFY），保证两 change 落在**不同 requirement**、非硬冲突。

**D3 — 指标集：保留 4 RAG 指标 + 新增「答案正确性」（legacy `AnswerCorrectness`）（选定）**
- 保留（修 `F-32` 后恢复）：`faithfulness` / `context_precision` / `context_recall` / `answer_relevancy`。
- 新增：**答案正确性**，采用 **legacy `from ragas.metrics import AnswerCorrectness`**，**以实例 `AnswerCorrectness()` 加入 `metrics=[...]`**（类会触发 `TypeError`）。它**与现有四指标同一条 `evaluate()` 路径**，零新接线 / 零新依赖 / 零结构化输出风险。
- **推迟**：`RubricsScoreWithReference`（准则式 + feedback）须另起现代指标路径 + `llm_factory`，列为后续增强。
- `ToolCallAccuracy` / `AgentGoalAccuracy`：本场景信号极弱，不评分；仅把**实际**工具调用序列 / 迭代次数作诊断元数据。

**D4 — 含联网样本处置与聚合口径（选定）**
- **样本三分**：按 `RAGContext.kind` 分「纯 KB / 含 web / 无检索」。
- **主分口径**：**主分 = 纯 KB 样本的全部适用指标均值**；含 web 样本标记「降级联网」并从主分排除；**无检索样本不进主分**（其 `answer_relevancy` / 答案正确性仅单列参考）。
- **跳过集合**：依赖 `contexts` 的 `{faithfulness, context_precision, context_recall}` 在无材料样本上**跳过并显式标记 `skipped`**（与"计算失败"区分，置空且不计入均值）；不依赖上下文的 `answer_relevancy` 与答案正确性**照常计算**。
- **防规避**：报告 SHALL 列出三类样本计数与**检索覆盖率**（有检索样本占比）；`--gate` SHALL 依据**主分**判定。否则"不检索即可把 context 指标整批跳空"会绕过评测。
- 未选「按 `kind` 分算」的理由：对单组算 `context_recall`/`faithfulness` 有语义陷阱（参考信息或答案事实本就来自另一组），会把「合理降级」系统性判低。

**D5 — score 回写：显式 `client_instance.score(trace_id=…)`，归 `llm-tracing`（选定）**
指标在**批量 `evaluate()` 之后**才算出，此时每题 `@observe` 已关闭 ⇒ `score_current_trace` 无当前 trace、且它**吞异常**（静默 no-op）。故必须用 **`langfuse_context.client_instance.score(trace_id=<该题 trace_id>, name=…, value=…)`** 显式回写；client 一律取 `langfuse_context.client_instance`，**不得 `new Langfuse()`**。`LANGFUSE_ENABLE` 关闭时整条路径短路。**验收**：启用时断言 trace 上**出现分数**（不能只看"没报错"）。

**D6 — 不改测试集 schema（选定）**
答案正确性只需已有 `reference`；轨迹取**实际** `state.messages`。故 v1 **不新增任何测试集字段**。

**D7 — `F-32` 守卫：真图优先 + 双向轨迹判据（选定）**
- **(A)** 调过 `retrieve_kb` 且其返回非空 ⇒ 该样本 `contexts` **非空**（靶向 `F-32` 复发）；
- **(B)** 未调 `retrieve_kb` 且未调 `search_web` ⇒ 该样本 `contexts` **为空**（靶向漏 `reset` 的跨样本泄漏 / 多收）。
- **形态**：至少 (A) 用**真图 + 真工具**跑一条绑定 KB 的问答来断言（假图会绕过 ctx 传播与工具累加这两条真路径，属自证、证伪不了 F-32）；(B) 可同样用真图跑一条不检索的问答。可另加 mock 单测作补充，但**不能只靠 mock**。

## Risks / Trade-offs

- **与 `prompt-layering` 同 capability** → 只改第 1/2/3 条、ADD 新条，不碰第 6 条；非硬冲突；计划顶部写排序声明。
- **评测可被「不检索」规避** → 若不对无检索样本设约束，context 三指标可被整批跳空。缓解：D4 的三类计数 + 检索覆盖率断言 + `--gate` 读主分。
- **跳过与失败同形** → 单次 `evaluate()` 无法按样本跳过；掩码后「跳过 nan」与「真失败 nan」不可区分。缓解：输出显式 `skipped` 标记列，跳过值置空并排除均值。
- **答案正确性依赖 `reference` 质量** → `AnswerCorrectness` 做 claim 级比对，对 `reference` 依赖强；测试集 `reference` 由 synthesizer 生成，存在同音错字（已有 `_clean_garbled_questions` 校对）。缓解：保留校对；报告保留逐题原值以便复核。
- **DB 与 CSV/MD 口径分叉** → `_save_eval_report` 仍按**旧 4 指标**加权算 `overall_score` 并写 `eval_report` 表（Non-Goal：不动表），且**未按样本分流**；CSV/MD 会多出答案正确性、且主分只含纯 KB 样本。缓解：Markdown 报告显式写明差异。
- **既有测试与 A/B 契约受影响** → `tests/cli/test_eval_ragas.py:198-205` 断言"恰 4 指标"（加指标必红）；`src/cli/compare_retrieval.py:66-82` 的子进程 `parse_metrics` 吃 eval stdout 均值（主分语义变更会污染 A/B 基线）。缓解：同步更新测试与契约（见 tasks）。
- **faithfulness 基线漂移** → 新基线需在 prompt 渲染稳定后采（挂 `prompt-layering` 收口后）。
- **宿主侧跑 CLI 连不上 Langfuse** → `ragas` 在 `[dev]` 依赖下 ⇒ 评测只在宿主 dev 跑；`.env` 的 `LANGFUSE_HOST` 是容器内地址。缓解：**代码级判空回退 localhost + 启动校验**（不止写注释）。

## Migration Plan

- 无数据库迁移；无破坏性数据操作；**无测试集 schema 变更**。
- 上线后需**重采评测基线**（原报告产生于 `F-32` 失效期间，context 指标不可比）。

## Open Questions

1. ~~答案正确性选型~~ **已定：legacy `AnswerCorrectness()`**（见 D3）。
2. ~~含 web 样本处置~~ **已定：纯 KB 出主分 + web 另标**（见 D4）。
3. ~~守卫断言形式~~ **已定：真图 + 双向轨迹判据**（见 D7）。
4. ~~`compare_retrieval` 承接~~ **已定：不 ADD 规格条款**。
5. ~~三类样本 × 指标聚合口径 / 门禁读哪个数~~ **已定：主分 = 纯 KB 样本均值；`--gate` 读主分；报告三类计数 + 检索覆盖率**（见 D4）。
6. **待定（非阻塞）**：`RubricsScoreWithReference`（准则式 + feedback）何时作为独立增强引入。
