# Tasks

> 执行档：单一计划到底（无阶段拆分 —— 全为普通代码任务、执行器相同）。
> 规格见 `specs/evaluation-pipeline/spec.md` 与 `specs/llm-tracing/spec.md`；决策见 `design.md`（D1–D7）。
> 排序声明：本 change 与在途 `prompt-layering-and-domain-binding` 落在 `evaluation-pipeline` 的**不同 requirement**（我们不动第 6 条）；归档时按序 apply 即可，不互相阻塞。

## 1. 修 `F-32`：评测链路检索材料收集（D1 / D7）

- [ ] 1.1 `src/cli/eval_ragas.py` 每题调用图前构造 `RequestContext(session_id, kb_id)` 并 `set`，调用后 `reset`（镜像 `turn_runner`）
- [ ] 1.2 评测从 `final_state["tool_contexts"]` 读取材料，确认有检索的样本 `contexts` 非空
- [ ] 1.3 守卫 (A)：**用真图 + 真工具**跑一条绑定 KB 的问答，断言 seeded ctx 下 `contexts` 非空（假图不能证伪 F-32）
- [ ] 1.4 守卫 (B)：**真图**跑一条不检索的问答，断言 `contexts` 为空（防漏 `reset` 的跨样本泄漏 / 多收）

## 2. 样本分流、跳过标记与聚合口径（D4）

- [ ] 2.1 按 `RAGContext.kind` 把样本标为「纯 KB / 含 web / 无检索」三类
- [ ] 2.2 无检索样本跳过 `faithfulness` / `context_precision` / `context_recall`，并**显式标记 `skipped`**（与"计算失败"区分，置空、不计均值）；`answer_relevancy` 与答案正确性**照常计算**
- [ ] 2.3 含 web 样本标记「降级联网」并从主分聚合中排除
- [ ] 2.4 报告列出三类样本计数与**检索覆盖率**（有检索样本占比）
- [ ] 2.5 `--gate`（`check_gate`）改读**主分**（纯 KB 样本均值），并加检索覆盖率断言，杜绝"不检索即跳空"
- [ ] 2.6 诊断元数据记录：是否降级联网、**实际**工具调用序列、迭代次数

## 3. 答案正确性指标（D3：legacy `AnswerCorrectness()`）

- [ ] 3.1 以**实例** `AnswerCorrectness()` 加入 `run_evaluation` 的 `metrics=[...]`（须实例化，类会 `TypeError`；列映射 `ground_truth→reference`、embeddings 由 `evaluate` 自动接线）
- [ ] 3.2 记录：该 legacy 指标会打 Deprecation 警告（可接受；噪声大可局部 suppress，不做版本迁移）

## 4. Agent 轨迹采集（D3）

- [ ] 4.1 把 `state.messages` 转成诊断结构：**只存工具名 + 入参摘要 + 迭代次数，工具输出截断**（避免把 KB 正文复制进 `detail_json` / 报告）
- [ ] 4.2 诊断结构随评测结果保存（供低分样本下钻）
- [ ] 4.3 明示工具调用序列**不评分**（仅诊断）

## 5. Langfuse score 回写（D5）

- [ ] 5.1 用 **`langfuse_context.client_instance.score(trace_id=<每题 trace_id>, name=…, value=…)`** 显式回写（**不用** `score_current_trace`：评分在批量 `evaluate()` 之后、逐题观测上下文已关闭，它无 trace 且吞异常）
- [ ] 5.2 `LANGFUSE_ENABLE` 关闭时整条路径短路；用例断言零写入
- [ ] 5.3 `LANGFUSE_HOST` **代码级判空回退 localhost + 启动校验**（不止写注释）

## 6. 报告、契约与文档

- [ ] 6.1 CSV / Markdown 报告新增答案正确性、三类样本输出与 `skipped` 标记列
- [ ] 6.2 Markdown 写明 **DB 与 CSV/MD 口径分叉**（DB `overall_score` 仍为旧 4 指标、未按样本分流；「主分」= 纯 KB 样本指标均值）
- [ ] 6.3 更新 `tests/cli/test_eval_ragas.py` 的「恰 4 指标」断言（加指标后必红）
- [ ] 6.4 同步 `src/cli/compare_retrieval.py` 经 stdout 解析指标均值的契约（主分语义变更）
- [ ] 6.5 文档登记：如需则更新 `docs/agents/api_contract.md` / `glossary.md` / `cookbook.md`（一事一档自检）
- [ ] 6.6 记录「需重采评测基线」（原报告产生于 `F-32` 失效期，context 指标不可比）

## 7. 闸门与验收

- [ ] 7.1 `POSTGRES_HOST=localhost pytest tests/` 全绿（宿主侧）
- [ ] 7.2 `ruff check .` 无错误、`pyright src/` 不新增 error
- [ ] 7.3 `openspec validate ragas-agent-eval --strict` 通过
- [ ] 7.4 活体一次真库评测：确认有检索样本 `contexts` 非空、答案正确性出现、含联网样本被排除主分、**Langfuse trace 上分数可见**（不能只看"没报错"）
