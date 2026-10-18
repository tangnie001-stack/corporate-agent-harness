## 1. 证据模型、采集开关与生命周期

- [ ] 1.1 新建 `src/core/eval_evidence.py`（不 import rag/infra）：`EvidenceRun`、`QuestionEvidence`、`current_evidence` ContextVar、no-op 关闭路径；字段缺失写 `null`，`schema_version=1`。
- [ ] 1.2 `src/cli/eval_deepeval.py` 新增 `--evidence` / `--evidence-all`；默认关闭；与 `--deepeval-only` / `--gate` / `--limit` 可组合；`--evidence-all` 隐含开启。
- [ ] 1.3 生成阶段每题 set/reset `current_evidence`；评分阶段 `run_evaluation(..., evidence_run=None)` → `score_rows(..., evidence_run=None)` 显式传同一 `EvidenceRun`，每行按 `q_index` 取 collector、set `current_evidence`，评分结束 drain judge；`evidence_run=None` / collector 缺失时跳过采集，不中断评分。
- [ ] 1.4 `main()` 把空库检查读到的全量 chunks 交给 `EvidenceRun.corpus_chunks`；探针不二次读库。
- [ ] 1.5 evidence 模式单独 `load_testset_samples` 并按 `--limit` 切片，拿 `reference_contexts` / `source_file` / `source_chunk_ids` / `match_scope` / `chunking_version` / `numeric_risk`；默认评测路径的三元组加载不动。
- [ ] 1.6 新建 `src/cli/eval_evidence_report.py`：探针、归因、落盘；可 import `ChunkResult` / `VectorStore`，不反向被 core 依赖。
- [ ] 1.7 单测：关闭时 no-op；开启时同一题内多次记录合并；异常不冒泡；`--deepeval-only` 下不因主分缺失而选全部。
- [ ] 1.8 `src/agents/skills/executor.py:_run_fork`：切子 `current_request_ctx` 时同时 `current_evidence.set(None)`，`finally` 恢复；单测子代理 `retrieve_kb` 不写入父题证据。

## 2. RRF 分数与共享类型

- [ ] 2.1 `src/infra/db/vector_store/types.py`：`ChunkResult` 增加 `rrf_score: float | None = None`。
- [ ] 2.2 `src/rag/fusion.py`：`rrf_fusion` / `rrf_fusion_multi` 回填 `rrf_score`；现有 RRF 排序不变。
- [ ] 2.3 单测：融合结果的 `rrf_score` 等权公式正确、未出现在某一路时 rank 为 None、现有调用方不受影响。

## 3. 检索链证据

- [ ] 3.1 `src/rag/retrieval.py:search`：记录 dense / lexical / RRF 候选的 `chunk_id` / `parent_key` / `doc_id` / `source` / `page` / `rank` / `score` / `score_kind` / `distance`，以及 `count` / `failed` / `truncated`；单路失败保留另一路。
- [ ] 3.2 `rerank_results`：记录 rerank 前（`rank_before` / dense_rank / sparse_rank）与 rerank 后（`rank_after` / relevance_score / `score_kind="rerank"`；降级为 `"fallback"`）。
- [ ] 3.3 `rerank_results`：用 dedup 前后 `parent_key` 集合差记录 `dropped_chunk_ids` / `kept_chunk_ids`；用 top-k 前后集合差记录截断丢弃。
- [ ] 3.4 `src/agents/tools/rag_tools.py:retrieve_kb`：记录工具名、query、top_k、结果数、耗时、`call_index`；每次调用的 `final_contexts`（`chunk_id` / `parent_key` / `source` / `page` / `score` / `order_in_prompt` 全局 1-based）；顶层 `retrieval.final_contexts` 记录多次调用合并后的 prompt 顺序。
- [ ] 3.5 候选上限默认 50/阶段；截断记 `truncated=true` 与 `total_count`。
- [ ] 3.6 单测：RRF 分数入证据；dense 有/rerank 后无；dense 失败 lexical 成功；dedup/top-k 丢弃 ID；parent/child 等价；多次 retrieve 的 `order_in_prompt` 连续。

## 4. Query / 工具 / 裁判 / 行为 / trace 证据

- [ ] 4.1 Query：记录 `original`、`tool_queries`（每次 retrieve 的实际 query + `call_index`）、`rewritten` / `sub_queries` / `intent_route`；评测路径不执行改写时写 null，不伪造。
- [ ] 4.2 工具链：记录 name/args/result_count/latency/iteration/call_index；`delegate_task` 只记顶层，不展开子代理。
- [ ] 4.3 评分：`score_rows` 每题结束、下一次 `evidence.reset()` 前，把 `HookEvidence.measures` 拷入当前 `QuestionEvidence.judge`；记录 `metric_attempted` / `skipped_reason`（empty/failed 不调用 measure → false）；`reason` 只在 measure 成功后读取；不持久化 verbose_logs。
- [ ] 4.4 行为轴：从 `behaviour_rows` 记录 metrics/reasons；failed 题写 `score=null` / `reason=failed`；用 `link_behavior_traces` 得到 `trace_id` / `link_method`。
- [ ] 4.5 token 归一化 helper：全角→半角、去千分位逗号/空白、统一负号、数值键=数字+单位、文本去 `【公司 · 报告期】` 头并压缩空白；探针与 `answer_mentions_fact` 共用。
- [ ] 4.6 trace：`run_trace_id` / `sut_trace_id` / `judge_measures[]` / 嵌套 `judge_calls[]`；缺失写 null；不重新生成。
- [ ] 4.7 单测：judge 无 reason → null；failed 行为题 → null/failed；measure 记录按题归属；空样本 `metric_attempted=false`；行为轴不产 `judge_calls`；`link_behavior_traces` 被消费；归一化正负号/千分位/全角。

## 5. Gold chunk 与 Chunk 探针

- [ ] 5.1 读取题集可选 `source_chunk_ids` / `match_scope` / `chunking_version`：`match_scope="exact"` → `gold_chunk_source=testset`、`gold_chunk_confidence=high`；`group`/`partial`/缺失 → 最高 medium；校验每个 id 在 `corpus_chunks` 中存在且同 doc，缺失/跨 doc → warning + 降级探针或 null，不跑探针覆盖 exact。
- [ ] 5.2 缺失时 `probe_reference_chunks(reference, chunks)`：用 4.5 的归一化提取数值 token（负号/千分位/百分号/单位）与文本片段（数字 + 稀有词窗口），在 `content`/`parent_content` 中匹配，返回 `gold_chunk_ids` / `gold_parent_keys` / `fact_in_corpus` / `probe_tokens`（命中上限 20）。
- [ ] 5.3 复用 `EvidenceRun.corpus_chunks`；为空时 `fact_in_corpus=null` 并 warning，不二次读库。
- [ ] 5.4 `testset_conflict`：仅 `numeric_conflict`（数值 token 不存在且 `numeric_risk=true`）触发；`unit_missing`（数值后 4 字符内无 元/万元/亿元/%/百分点）只做信号，不单独触发 `testset_error`；`period_conflict` v1 恒 null。
- [ ] 5.5 `fact_in_candidates` / `fact_in_reranked` / `fact_in_final` 用 `chunk_id OR parent_key` 判交集；同时记录 `fact_tokens` / `fact_hit_tokens` / `fact_coverage` / `missing_fact_tokens`。
- [ ] 5.6 单测：Q21 类“语料有但没召回” → true；臆造数值 + numeric_risk → numeric_conflict；无 token → null；source_chunk_ids + exact → high；group/partial → medium；stale id → 降级；parent/child 等价；部分数值覆盖 → inconclusive；unit_missing 不单独 testset_error；命中截断；KB 不可用 → null。

## 6. 确定性归因引擎

- [ ] 6.1 定义根因枚举：`judge_failure` / `testset_error` / `parsing_or_chunking` / `retrieval_miss` / `rerank_or_topk` / `prompt_or_generation` / `tool_routing` / `agent_orchestration` / `inconclusive`。
- [ ] 6.2 事实阶段 helper：`testset_conflict → testset_error`；`fact_coverage.<stage> < 1.0 → inconclusive（partial_fact_coverage + missing_fact_tokens）`；`!fact_in_corpus → parsing_or_chunking`；`!fact_in_candidates → retrieval_miss`；`!fact_in_reranked → rerank_or_topk`；`!fact_in_final → rerank_or_topk`；否则 `prompt_or_generation`/`inconclusive`。
- [ ] 6.3 metric-scoped 规则（design D9）：先判 `metric_attempted=false` → `inconclusive`（`skipped:<reason>`），再判 `judge_failed`/`testset_conflict`/`fact_coverage`；`judge_failed` 仅在 attempted=true 且 NaN/error 时为 true；`tool_correctness_reason=="failed"` → `inconclusive`，不是 `tool_routing`；CREL 的 `fact_in_corpus=false` → `parsing_or_chunking`/`testset_error`。
- [ ] 6.4 置信度：high/medium/low/inconclusive 按 design D10；`evidence` 单行、可核查。
- [ ] 6.5 归因异常隔离：某题归因失败只把该题 attribution 写全 `inconclusive`，不影响证据写入/评测。
- [ ] 6.6 单测：Q21 retrieval_miss；Q20 rerank_or_topk；Q19 testset_error；Q16/Q22 judge_failure；空样本/失败样本不归 judge_failure；failed run 不归 tool_routing；CREL 语料缺失不归 retrieval_miss；抽象题 inconclusive；source_chunk_ids exact high / group medium；置信度分级。

## 7. 低分选择、落盘与汇总

- [ ] 7.1 低分判定：四主分低于门禁/NaN、tool_correctness 未完全命中/失败、行为分 <0.5/缺失、empty/failed；web 题只在 tool_correctness<1 或行为低/缺失时选；`--evidence` 只写这些题，`--evidence-all` 写全部。
- [ ] 7.2 `--deepeval-only` 下只按行为分 <0.5/缺失选；没有主分时不写全部、不把缺失当 0。
- [ ] 7.3 写盘顺序：`_save_eval_report` / `_write_langfuse_scores` 之后、`check_gate` 之前；`--gate` 的 `sys.exit` 不丢证据。
- [ ] 7.4 写入 `data/deepeval/evidence/<run_id>/q_<q_index>.json` + `summary.json`；临时文件 + `os.replace` 原子落盘。
- [ ] 7.5 `summary.json`：run_id / run_trace_id / qa_count / low_score_questions（0-based）/ evidence_files（相对名）/ elapsed_ms / root_cause_counts.by_metric / total / scored_metric_count / skipped_metric_count / inconclusive_count；root_cause_counts 只统计 attempted；无低分题也写 summary（零计数 + 空 evidence_files）。
- [ ] 7.6 `.gitignore` 增加 `data/deepeval/evidence/`；文档提示含原文、勿外发。
- [ ] 7.7 单测：低分选择、deepeval-only 选择、summary 计数（scored/skipped/inconclusive 对账）、无低分仍写 summary、写入失败不阻断、原子写、gate 退出前已落盘、归因失败隔离。

## 8. 文档与验收

- [ ] 8.1 `src/cli/README.md` 增加 `--evidence` / `--evidence-all`、证据路径、归因枚举、`--deepeval-only` 选择口径与 summary 说明。
- [ ] 8.2 `docs/agents/eval-metrics-troubleshooting.md` 增加“6 步证据链 → 证据字段 → root_cause”对照表，并写明 `inconclusive` 是合法结果。
- [ ] 8.3 `docs/agents/api_contract.md` 登记证据目录、归因枚举与“不进 API/DB”的边界。
- [ ] 8.4 集成测试：构造“dense 有、rerank 后无、final 无”的题，跑 `--evidence`，断言 retrieval 证据与 `attribution.context_precision.root_cause=="rerank_or_topk"`。
- [ ] 8.5 活体冒烟：对 v6 的 Q21 跑一次 `--evidence`，确认 `fact_in_corpus=true`、`fact_in_final=false`、`retrieval_miss` 或 `rerank_or_topk`；同时确认报告头 `Run trace` 与证据 `trace` 段一致。
- [ ] 8.6 全量 `pytest tests/` + `ruff check src/cli src/rag src/agents src/core src/infra` 通过。
