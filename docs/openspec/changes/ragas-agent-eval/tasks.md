# Tasks

> 执行档：单一计划到底（无阶段拆分 —— 全为普通代码任务、执行器相同）。
> 规格见 `specs/evaluation-pipeline/spec.md` 与 `specs/llm-tracing/spec.md`；决策见 `design.md`（D1–D7）。
> 排序声明：本 change 与在途 `prompt-layering-and-domain-binding` 落在 `evaluation-pipeline` 的**不同 requirement**（我们不动第 6 条）；归档时按序 apply 即可，不互相阻塞。

## 1. 测试集 schema（前置，独立性最高）

- [ ] 1.1 `src/cli/eval_ragas_generate.py` 的 sample 结构新增**可选**字段 `reference_tool_calls` / `reference_topics`（生成时若无内容则不写入）
- [ ] 1.2 `_load_latest_testset` 读回时容忍缺失字段（不报错、不填默认）
- [ ] 1.3 用例：无新字段的旧测试集文件仍可加载并通过评测流程

## 2. 修 `F-32`：评测链路检索材料收集（D1）

- [ ] 2.1 `src/cli/eval_ragas.py` 每题调用图前构造 `RequestContext(session_id, kb_id)` 并 `set`，调用后 `reset`（镜像 `turn_runner`）
- [ ] 2.2 评测从 `final_state["tool_contexts"]` 读取材料，确认有检索的样本 `contexts` 非空
- [ ] 2.3 守卫 (A)：轨迹调过 `retrieve_kb` 且返回非空 ⇒ 该样本 `contexts` 非空（pytest 用例，注入假图）
- [ ] 2.4 守卫 (B)：轨迹未调 `retrieve_kb` 且未调 `search_web` ⇒ 该样本 `contexts` 为空（防跨样本泄漏 / 多收）

## 3. 样本分流与指标跳过（D4）

- [ ] 3.1 按 `RAGContext.kind` 把样本标记为「纯 KB / 含 web / 无检索」三类
- [ ] 3.2 无检索样本跳过 context 类指标（不产生 nan、不进均值）
- [ ] 3.3 含 web 样本标记「降级联网」并从主分聚合中排除
- [ ] 3.4 诊断元数据记录：是否降级联网、工具调用序列、迭代次数

## 4. 答案正确性指标（D3）

- [ ] 4.1 引入现代 `llm_factory` 接线（指向同一 judge 模型 `RAGAS_LLM_MODEL`），供 `collections` 指标使用
- [ ] 4.2 接入 `RubricsScoreWithReference`（自定义准则强调「数字/期间与原文一致、不编造条款」）
- [ ] 4.3 1–5 分归一到 0–1 后再参与聚合
- [ ] 4.4 样本缺 `reference` 时跳过该指标（不产生 nan）

## 5. Agent 轨迹采集（D3）

- [ ] 5.1 把 `state.messages`（含工具调用入参）转成诊断结构
- [ ] 5.2 诊断结构随评测结果保存（供低分样本下钻）
- [ ] 5.3 明示工具调用序列**不评分**（仅诊断）

## 6. Langfuse score 回写（D5）

- [ ] 6.1 每题用 `langfuse_context.score_current_trace(...)`（或 `Langfuse.score`）回写指标分数；client 取 `langfuse_context.client_instance`
- [ ] 6.2 `LANGFUSE_ENABLE` 关闭时整条路径短路；用例断言零出网写入
- [ ] 6.3 宿主侧跑 CLI 的 `LANGFUSE_HOST` 覆写说明写入文档/注释

## 7. 报告与文档

- [ ] 7.1 CSV / Markdown 报告新增答案正确性与按样本分流的输出
- [ ] 7.2 文档登记：如需则更新 `docs/agents/api_contract.md` / `glossary.md` / `cookbook.md`（按一事一档自检）
- [ ] 7.3 记录「需重采评测基线」（原报告产生于 `F-32` 失效期，context 指标不可比）

## 8. 闸门与验收

- [ ] 8.1 `POSTGRES_HOST=localhost pytest tests/` 全绿（宿主侧）
- [ ] 8.2 `ruff check .` 无错误、`pyright src/` 不新增 error
- [ ] 8.3 `openspec validate ragas-agent-eval --strict` 通过
- [ ] 8.4 活体一次真库评测：确认有检索样本 `contexts` 非空、答案正确性出现、含联网样本被排除主分
