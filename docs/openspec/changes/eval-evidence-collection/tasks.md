## 1. 证据模型与采集开关

- [ ] 1.1 新建 `src/core/eval_evidence.py`：`EvidenceCollector`（per-question，记录 query/retrieval/rerank/dedup/topk/tool/context/facts/judge/behavior）、`current_evidence` ContextVar、no-op 关闭路径；字段缺失写 `null`，schema_version=1。
- [ ] 1.2 `src/cli/eval_deepeval.py` 新增 `--evidence` / `--evidence-all` 参数；默认关闭；与 `--deepeval-only` 可组合；不改变现有参数互斥规则。
- [ ] 1.3 主流程每题 set/reset `current_evidence`；生成/评分结束后清空；关闭时不创建收集器。
- [ ] 1.4 单测：关闭时 no-op；开启时同一题内多次记录合并到同一收集器；异常不冒泡。
- [ ] 1.5 证据 `trace` 段只消费 `eval-trace-correlation` 的进程内接口（`HookEvidence.measures` / `EvalTraceContext` / 行为映射）；按 `judge_measure_id` 组装 `judge_measures[]` 与嵌套 `judge_calls[]`；不重解析日志；取不到写 null，不重新生成。

## 2. 检索链证据

- [ ] 2.1 `src/rag/retrieval.py`：dense / lexical / RRF 结果记录 `chunk_id/doc_id/source/page/score/rank/count/failed/truncated`；单路失败保留另一路。
- [ ] 2.2 `rerank_results`：记录 rerank 前（原始 rank/score）与 rerank 后（新 rank/score），fallback 时记 `scored="fallback"`。
- [ ] 2.3 记录 dedup 与 top-k 截断：被丢弃的 `chunk_id`、`rank_before_topk/rank_after_topk`；不得只记数量。
- [ ] 2.4 `src/agents/tools/rag_tools.py`：`retrieve_kb` 记录工具名、query、top_k、结果数、耗时、最终 contexts 的 chunk_id/page/order_in_prompt；多次调用按 `call_index` 分组。
- [ ] 2.5 候选上限默认 50/阶段；截断记 `truncated=true` 与 `total_count`。
- [ ] 2.6 单测：dense 有/rerank 后无；dense 失败 lexical 成功；dedup/top-k 丢弃；截断。

## 3. Query / 工具 / 裁判 / 行为证据

- [ ] 3.1 Query：记录 original、tool_queries（来自 trajectory.args.query）、可用的 rewritten/sub_queries/intent_route；取不到写 null。
- [ ] 3.2 工具链：记录 name/args/result_count/iteration/call_index；`delegate_task` 只记顶层，不展开子代理。
- [ ] 3.3 评分：记录每指标 score/error/reason；`_score_metric_with_retry` 与诊断指标路径都覆盖；`reason` 只在 measure 正常返回后读取，异常不读 stale reason；不持久化 verbose_logs。
- [ ] 3.4 行为轴：从 `behaviour_rows` 记录逐题 metrics/reasons；failed 题写 score=null/reason=failed。
- [ ] 3.5 事实标记：由 gold_chunk_ids 与各阶段 chunk 交集计算 `fact_in_corpus/candidates/reranked/final`；answer 用 fact token 计算 `answer_mentions_fact`；无法判定写 null。
- [ ] 3.6 单测：judge 无 reason → null；failed 行为题 → null/failed；事实标记交集；answer token 匹配。
- [ ] 3.7 单测：trace 段在 measure 记录可用/不可用时分别为有值/null；一条 measure 的多条 HTTP 记录归到同一 `judge_measure_id`；证据仍落盘，不生成伪 ID。
- [ ] 3.8 单测：行为轴只产出分数 + `trace_id`/`link_method`，不产出行为 judge 的 `judge_calls`。

## 4. Gold chunk 与 Chunk 探针

- [ ] 4.1 读取题集可选 `source_chunk_ids`：存在则 `gold_chunk_ids` 用它，`gold_chunk_source=testset`、`gold_chunk_confidence=high`，不跑探针覆盖。
- [ ] 4.2 缺失时新增 `probe_reference_chunks(kb_id, reference, chunks)`：提取数值 token + 归一化文本片段，在 `content`/`parent_content` 中匹配，返回 `fact_in_corpus` 与命中 chunk（上限 20）；`gold_chunk_source=probe`、`gold_chunk_confidence=medium`。
- [ ] 4.3 一次 run 只读一次 `VectorStore.get_all_chunks(kb_id)`；KB 读取失败记 warning，`fact_in_corpus=null`。
- [ ] 4.4 testset_conflict：记录 `period_conflict`（期间/季度不一致）、`numeric_conflict`（参考数值不存在或与语料矛盾）、`unit_missing`（数值无单位）。
- [ ] 4.5 单测：Q21 类“语料有但没召回” → true；臆造数值 → false；无 token → null；source_chunk_ids 优先；截断。
- [ ] 4.6 不在探针里调用 LLM。

## 5. 确定性归因引擎

- [ ] 5.1 定义根因枚举：`judge_failure` / `testset_error` / `parsing_or_chunking` / `retrieval_miss` / `rerank_or_topk` / `prompt_or_generation` / `tool_routing` / `agent_orchestration` / `inconclusive`。
- [ ] 5.2 实现事实阶段根因 helper：`!fact_in_corpus → parsing_or_chunking`；`!fact_in_candidates → retrieval_miss`；`!fact_in_reranked → rerank_or_topk`；`!fact_in_final → rerank_or_topk`；否则 `prompt_or_generation`。
- [ ] 5.3 实现 metric-scoped 规则（design D9）：F/AR/CP/CR/CREL/NonAdvice/ToolCorrectness/TaskCompletion/StepEfficiency/AgentLoopDetection；judge_failed 与 testset_conflict 优先。
- [ ] 5.4 置信度：high/medium/low/inconclusive 按 design D10；evidence 写单行、可核查。
- [ ] 5.5 归因异常隔离：某题归因失败只把该题 attribution 写全 inconclusive，不影响证据写入/评测。
- [ ] 5.6 单测：Q21 retrieval_miss；Q20 rerank_or_topk；Q19 testset_error；Q16/Q22 judge_failure；抽象题 inconclusive；source_chunk_ids 优先；置信度分级。

## 6. 低分选择、落盘与汇总

- [ ] 6.1 低分判定：四主分低于门禁/NaN、tool_correctness 未完全命中/失败、行为分 <0.5/缺失、empty/failed；`--evidence` 只写这些题，`--evidence-all` 写全部。
- [ ] 6.2 写入 `data/deepeval/evidence/<run_id>/q_<q_index>.json` + `summary.json`；临时文件 + `os.replace` 原子落盘。
- [ ] 6.3 `summary.json`：run_id、题数、低分题号、路径、采集耗时、`root_cause_counts.by_metric` / `total`、`inconclusive_count`；计数与逐题 attribution 对账。
- [ ] 6.4 `.gitignore` 增加 `data/deepeval/evidence/`；文档提示含原文、勿外发。
- [ ] 6.5 单测：低分选择、summary 计数、写入失败不阻断、原子写、归因失败隔离。

## 7. 文档与验收

- [ ] 7.1 `src/cli/README.md` 增加 `--evidence` / `--evidence-all`、证据路径、归因枚举与 summary 说明。
- [ ] 7.2 `docs/agents/eval-metrics-troubleshooting.md` 增加“6 步证据链 → 证据字段 → root_cause”对照表。
- [ ] 7.3 `docs/agents/api_contract.md` 登记证据目录、归因枚举与“不进 API/DB”的边界。
- [ ] 7.4 集成测试：构造“dense 有、rerank 后无、final 无”的题，跑 `--evidence`，断言 retrieval 证据与 `attribution.context_precision.root_cause=="rerank_or_topk"`。
- [ ] 7.5 活体冒烟：对 v6 的 Q21 跑一次 `--evidence`，确认 `fact_in_corpus=true`、`fact_in_final=false`、`retrieval_miss` 或 `rerank_or_topk`。
- [ ] 7.6 全量 `pytest tests/` + `ruff check src/cli src/rag src/agents src/core` 通过。
