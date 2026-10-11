## Context

本仓评测链路现状（实读核实）：

- `src/cli/eval_ragas.py:129` 直接 `graph.ainvoke({...})` 跑完整 Agent 图，**不经过** `turn_runner`；`_answer_one_question` 只取回 `answer` + `final_state["tool_contexts"]`。
- `RequestContext` 的 seed 只有两处：生产入口 `src/services/turn_runner.py:111`（`_run_with_finalize` 内 `set`）与 fork 子代理 `src/agents/skills/executor.py:231`。评测 CLI **两处都不经过**。
- 工具侧：`retrieve_kb`（`src/agents/tools/rag_tools.py:244-249`）与 `search_web`（`src/agents/tools/web_tools.py:137`）**共用** `ctx.tool_contexts` 累加器；`ctx is None` 时写入一个即弃的局部 list。
- `agent_finalize`（`src/agents/graph/agent_node.py:321-327`）只从 `current_request_ctx.get()` 读 `tool_contexts`；`ctx is None` 时返回空列表。
- ⇒ **`F-32`**：评测链路 `ctx` 恒为 None → 回传 state 的 `tool_contexts` 恒空 → context 类指标恒失效。
- `RAGContext.kind`（`src/rag/context.py:25-27`）区分 `kb` / `web`；`kind` 已随上下文块保存。
- 评测为**单轮**（`src/cli/eval_ragas.py:135` 传 `_history: []`）→ 历史 / 摘要逻辑不进入评测。
- `ragas==0.4.3` 实测 `ragas.metrics.collections` 已提供 `AnswerAccuracy`、`ToolCallAccuracy/ToolCallF1`、`AgentGoalAccuracy*`、`TopicAdherence`、`RubricsScore*` 等，本 change 需要的能力**无需新增依赖**。
- Langfuse SDK 实测 `2.60.10`：`Langfuse.score(...)`、`langfuse_context.score_current_trace(...)` 均存在；既有口径要求 client 统一取 `langfuse_context.client_instance`、**不得 `new Langfuse()`**。

## Goals / Non-Goals

**Goals:**

- 修复 `F-32`，让评测能真正测到检索质量（context 类指标恢复）。
- 补上「答案正确性」维度，使评测能回答「Agent 答对了没有」。
- 让评测产出对单轮 RAG 场景（常规检索轮）可信、可下钻（工具轨迹作诊断）。
- 顺带把 `evaluation-pipeline` 主规格的过时条款改对（不改第 6 条）。

**Non-Goals:**

- 不评多轮 / 追问（`ask_user`）、skill 直出（fork）轮。
- 不引入 DeepEval 等新框架；不做自研轨迹 judge（另案）。
- 不扩 `eval_report` 表、不改 `kb_eval` API、不动前端。
- 不碰 `evaluation-pipeline` 第 6 条（当前日期，归 `prompt-layering`）。

## Decisions

**D1 — `F-32` 修法：eval 侧 seed `RequestContext`（选定）**
在评测每题调用图前构造并 `set` 一个轻量 `RequestContext`（`session_id` 必填，`kb_id` 一并带上），调用后 `reset`——镜像 `turn_runner` 的做法。
- 备选 A（`agent_finalize` 回落）：**否决**——`ctx is None` 时工具已把材料写进即弃 list，state 里**没有任何可回落的数据**；要回落须先让工具把 `RAGContext` 写进 state，改动面更大。
- 备选 B（评测改走 `turn_runner`）：**否决**——`start_turn` 依赖会话锁、落库、SSE 流与注册表，评测不需要且会污染数据。
- 落地后 `agent_finalize` 会把 ctx 中的材料写入 `state.tool_contexts`，评测**无需**改 `agent_node.py`。

**D2 — 能力归属：认领 `evaluation-pipeline`（选定）**
本次是「重做 RAGAS 评测」，而 `evaluation-pipeline` 即该能力的 owner，故将变更放入它并顺带改对过时条款；**不新开** `agent-evaluation`（避免同一件事两处归属）。唯一约束：**不触碰第 6 条**（`prompt-layering` 正在 MODIFY 它），以保证两个 change 落在**不同 requirement**、非硬冲突。

**D3 — 指标集：保留 4 RAG 指标 + 新增「答案正确性」；工具/轨迹降为诊断（选定）**
- 保留（修 `F-32` 后恢复）：`faithfulness` / `context_precision` / `context_recall` / `answer_relevancy`。
- 新增：**答案正确性**，采用 ragas `RubricsScoreWithReference`（准则式 LLM 打分：1–5 分 + feedback，准则可自定义以强调「数字/期间与原文一致、不编造条款」）。选它而非 `AnswerAccuracy`（口径严格二值、每题 2 次 LLM）与 `SemanticSimilarity`（只测措辞相似、不判对错）。这是「Agent 回答准确性」的正解；现有四指标无一测「答对没」。**量纲归一**：1–5 需先归一到 0–1 再与其它指标聚合。
- `ToolCallAccuracy` / `AgentGoalAccuracy`：本场景（单轮、期望路径近似 `[retrieve_kb]`）**信号极弱**（命中率近恒 1），不纳入评分，仅把**工具调用序列 / 迭代次数**作为诊断元数据保存供低分下钻。

**D4 — 含联网样本处置：纯 KB 出主分 + web 另标（选定）**
仅由知识库材料构成的样本（检索材料不含 `RAGContext.kind=web` 块）参与常规上下文指标（主分）；**含联网材料的样本 SHALL 被标记为「降级联网」并从主分中排除**，其「是否降级联网」记入诊断元数据。`contexts` 为空的样本**跳过** context 类指标并单列一类（「无检索直答」）。
未选「按 `kind` 分算」的理由：常规检索轮会因知识库不足而自主降级联网（`kind=web`），而对单组算 `context_recall`/`faithfulness` 有**语义陷阱**——参考信息或答案事实本就来自另一组，会把「合理降级」系统性判低，污染主分与门禁。web 样本的正式评测留给后续（联网轮 / 多轮）change。

**D5 — score 回写：归 `llm-tracing`，沿用既有 client 口径（选定）**
每题结束后用 `langfuse_context.score_current_trace(...)`（或 `Langfuse.score`）把指标分数写回对应 trace；client 一律取 `langfuse_context.client_instance`，**不得 `new Langfuse()`**（否则绕过 `LANGFUSE_ENABLE` 开关与关停 flush）。`LANGFUSE_ENABLE` 关闭时整条路径短路。

**D6 — 测试集 schema 向后兼容扩字段（选定）**
`samples[]` 新增**可选** `reference_tool_calls` / `reference_topics`；解析时缺失即跳过对应指标。旧测试集文件原样可读。

**D7 — `F-32` 守卫：两条轨迹判据断言，都做（选定）**
守卫以**轨迹**为准（该样本是否调用了 `retrieve_kb` / `search_web`），不用启发式：
- **(A)** 调过 `retrieve_kb` 且其返回非空 ⇒ 该样本 `contexts` **非空**（靶向 `F-32` 复发）；
- **(B)** 未调 `retrieve_kb` 且未调 `search_web` ⇒ 该样本 `contexts` **为空**（靶向本次修复自带的风险：漏 `reset` 导致的**跨样本泄漏**、或多收）。

只做 (A) 无法发现泄漏——泄漏只会制造"不该有的非空"，而 (A) 只看"该非空的"。形态：pytest 守卫用例（对评测循环注入假图 / mock 图）；可选外加一条运行时 warning 日志；**不做运行时异常**（一题不满足即中断整轮评测，过脆）。

## Risks / Trade-offs

- **与 `prompt-layering` 同 capability** → 我们只改第 1/2/3 条、ADD 新条，**不碰第 6 条**；两个 change 落在不同 requirement，非硬冲突。缓解：在计划顶部写排序声明，归档时按序 apply。
- **答案正确性指标依赖 `reference` 质量** → 测试集 `reference` 由 ragas synthesizer 生成，存在同音错字（已有 `_clean_garbled_questions` 校对）；正确性分数会受其影响。缓解：准则里显式要求「数字一致」以降低对错字的敏感度，校对环节保留。
- **现代 LLM 接口接线** → `collections` 指标需 `llm_factory`（结构化输出走 `instructor`），与现有 legacy `LangchainLLMWrapper` 不同源；`instructor` 已随 `ragas` 装上（1.15.4）但不在 `pyproject`。缓解：为裁判补一条现代 `llm_factory` 接线，指向同一 judge 模型（`RAGAS_LLM_MODEL`）。
- **faithfulness 基线随 prompt 渲染漂移** → 本 change 建立的新基线需在 prompt 渲染稳定后采；缓解：baseline 采集时点挂在 `prompt-layering` 收口之后。
- **宿主侧跑 CLI 连不上 Langfuse** → `.env` 的 `LANGFUSE_HOST` 是容器内地址；缓解：宿主跑评测时覆写 `LANGFUSE_HOST=http://localhost:3000`。
- **`F-32` 修复可能掩盖** → 若只 seed ctx 而不加守卫，未来再次断链会静默回到空 contexts；且漏 `reset` 会造成跨样本泄漏而不被察觉。缓解：见 **D7** 的双向轨迹判据守卫。

## Migration Plan

- 无数据库迁移；无破坏性数据操作。
- 测试集 JSON 仅新增可选字段 → 旧文件兼容。
- 上线后需**重采评测基线**（原报告产生于 `F-32` 失效期间，context 指标不可比）。

## Open Questions

1. ~~答案正确性指标选型~~ **已定：`RubricsScoreWithReference`**（见 D3）。待定：是否允许经配置切换到 `AnswerAccuracy`。
2. ~~含 web 的常规轮样本处置~~ **已定：(b) 纯 KB 出主分 + web 另标**（见 D4）。待定：「纯 KB」判定口径是否取「检索材料不含 web 块」。
3. ~~`F-32` 守卫断言形式~~ **已定：双向断言（A+B），轨迹判据**（见 D7）。
4. ~~`compare_retrieval` 是否正式承接~~ **已定：保留规格承接** —— 移除「Chunk size comparison」后，由 ADD 的「Retrieval configuration comparison」如实登记既有 `python -m src.cli.compare_retrieval` 能力。
