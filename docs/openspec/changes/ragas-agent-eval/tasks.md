# Tasks

> 执行档：单一计划到底（无阶段拆分 —— 全为普通代码任务、执行器相同）。
> 规格见 `specs/evaluation-pipeline/spec.md` 与 `specs/llm-tracing/spec.md`；决策见 `design.md`（D1–D7）。
> 排序声明：本 change 与在途 `prompt-layering-and-domain-binding` 落在 `evaluation-pipeline` 的**不同 requirement**（我们不动第 6 条）；归档时按序 apply 即可，不互相阻塞。

## 1. 修 `F-32`：评测链路检索材料收集（D1）

- [ ] 1.1 `src/cli/eval_ragas.py` 每题调用图前构造 `RequestContext(session_id, kb_id)` 并 `set`，调用后 `reset`（镜像 `turn_runner`）
- [ ] 1.2 评测从 `final_state["tool_contexts"]` 读取材料，确认有检索的样本 `contexts` 非空
- [ ] 1.3 守卫 (A)：轨迹调过 `retrieve_kb` 且返回非空 ⇒ 该样本 `contexts` 非空（pytest 用例，注入假图）
- [ ] 1.4 守卫 (B)：轨迹未调 `retrieve_kb` 且未调 `search_web` ⇒ 该样本 `contexts` 为空（防跨样本泄漏 / 多收）

## 2. 样本分流与指标跳过（D4）

- [ ] 2.1 按 `RAGContext.kind` 把样本标记为「纯 KB / 含 web / 无检索」三类
- [ ] 2.2 无检索样本跳过 `faithfulness` / `context_precision` / `context_recall`（不产生 nan、不进均值）；`answer_relevancy` 与答案正确性**照常计算**
- [ ] 2.3 含 web 样本标记「降级联网」并从主分聚合中排除
- [ ] 2.4 诊断元数据记录：是否降级联网、**实际**工具调用序列、迭代次数

## 3. 答案正确性指标（D3：legacy `AnswerCorrectness`）

- [ ] 3.1 把 `from ragas.metrics import AnswerCorrectness` 加入 `run_evaluation` 的 `metrics=[...]`（llm / embeddings 已接线，无需新增）
- [ ] 3.2 样本缺 `reference` 时跳过该指标（不产生 nan）
- [ ] 3.3 记录：该 legacy 指标会打 Deprecation 警告（可接受；若噪声大可局部 suppress，不做版本迁移）

## 4. Agent 轨迹采集（D3）

- [ ] 4.1 把 `state.messages`（含工具调用入参）转成诊断结构
- [ ] 4.2 诊断结构随评测结果保存（供低分样本下钻）
- [ ] 4.3 明示工具调用序列**不评分**（仅诊断）

## 5. Langfuse score 回写（D5）

- [ ] 5.1 每题用 `langfuse_context.score_current_trace(...)`（或 `Langfuse.score`）回写指标分数；client 取 `langfuse_context.client_instance`
- [ ] 5.2 `LANGFUSE_ENABLE` 关闭时整条路径短路；用例断言零出网写入
- [ ] 5.3 宿主侧跑 CLI 的 `LANGFUSE_HOST` 覆写写进文档 / 注释

## 6. 报告与文档

- [ ] 6.1 CSV / Markdown 报告新增答案正确性与按样本分流的输出
- [ ] 6.2 Markdown 显式写明 **DB 与 CSV/MD 口径分叉**（DB `overall_score` 仍为旧 4 指标、未按样本分流；「主分」= 纯 KB 样本的指标均值）
- [ ] 6.3 文档登记：如需则更新 `docs/agents/api_contract.md` / `glossary.md` / `cookbook.md`（一事一档自检）
- [ ] 6.4 记录「需重采评测基线」（原报告产生于 `F-32` 失效期，context 指标不可比）

## 7. 闸门与验收

- [ ] 7.1 `POSTGRES_HOST=localhost pytest tests/` 全绿（宿主侧）
- [ ] 7.2 `ruff check .` 无错误、`pyright src/` 不新增 error
- [ ] 7.3 `openspec validate ragas-agent-eval --strict` 通过
- [ ] 7.4 活体一次真库评测：确认有检索样本 `contexts` 非空、答案正确性出现、含联网样本被排除主分
