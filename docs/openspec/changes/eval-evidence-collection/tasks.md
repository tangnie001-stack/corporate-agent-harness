## 1. 证据模型与采集开关

- [ ] 1.1 新建 `src/core/eval_evidence.py`：`EvidenceCollector`（per-question，记录 query/retrieval/rerank/tool/context/judge/behavior）、`current_evidence` ContextVar、no-op 关闭路径；字段缺失写 `null`，schema_version=1。
- [ ] 1.2 `src/cli/eval_deepeval.py` 新增 `--evidence` / `--evidence-all` 参数；默认关闭；与 `--deepeval-only` 可组合；不改变现有参数互斥规则。
- [ ] 1.3 主流程每题 set/reset `current_evidence`；生成/评分结束后清空；关闭时不创建收集器。
- [ ] 1.4 单测：关闭时 no-op；开启时同一题内多次记录合并到同一收集器；异常不冒泡。

## 2. 检索链证据

- [ ] 2.1 `src/rag/retrieval.py`：dense / lexical / RRF 结果记录 `chunk_id/doc_id/source/page/score/rank/count/failed/truncated`；单路失败保留另一路。
- [ ] 2.2 `rerank_results`：记录 rerank 前（原始 rank/score）与 rerank 后（新 rank/score），fallback 时记 `scored="fallback"`。
- [ ] 2.3 `src/agents/tools/rag_tools.py`：`retrieve_kb` 记录工具名、query、top_k、结果数、耗时、最终 contexts 的 chunk_id/page/order_in_prompt。
- [ ] 2.4 候选上限默认 50/阶段；截断记 `truncated=true` 与 `total_count`。
- [ ] 2.5 单测：dense 有/rerank 后无；dense 失败 lexical 成功；截断。

## 3. Query / 工具 / 裁判 / 行为证据

- [ ] 3.1 Query：记录 original、tool_queries（来自 trajectory.args.query）、可用的 rewritten/sub_queries/intent_route；取不到写 null。
- [ ] 3.2 工具链：记录 name/args/result_count/iteration；`delegate_task` 只记顶层，不展开子代理。
- [ ] 3.3 评分：记录每指标 score/error/reason；`_score_metric_with_retry` 与诊断指标路径都覆盖；不持久化 verbose_logs。
- [ ] 3.4 行为轴：从 `behaviour_rows` 记录逐题 metrics/reasons；failed 题写 score=null/reason=failed。
- [ ] 3.5 单测：judge 无 reason → null；failed 行为题 → null/failed；工具参数保留。

## 4. Chunk 探针

- [ ] 4.1 新增 `probe_reference_chunks(kb_id, reference, chunks)`：提取数值 token + 归一化文本片段，在 `content`/`parent_content` 中匹配，返回 `fact_in_corpus` 与命中 chunk 列表（上限 20）。
- [ ] 4.2 一次 run 只读一次 `VectorStore.get_all_chunks(kb_id)`；KB 读取失败记 warning，`fact_in_corpus=null`。
- [ ] 4.3 单测：Q21 类“语料有但没召回” → true；臆造数值 → false；无 token → null；截断。
- [ ] 4.4 不在探针里调用 LLM。

## 5. 低分选择、落盘与汇总

- [ ] 5.1 低分判定：四主分低于门禁/NaN、tool_correctness 未完全命中/失败、行为分 <0.5/缺失、empty/failed；`--evidence` 只写这些题，`--evidence-all` 写全部。
- [ ] 5.2 写入 `data/deepeval/evidence/<run_id>/q_<q_index>.json` + `summary.json`；临时文件 + `os.replace` 原子落盘。
- [ ] 5.3 `summary.json`：run_id、题数、低分题号、路径、采集耗时；写入失败只 warning，不改退出码。
- [ ] 5.4 `.gitignore` 增加 `data/deepeval/evidence/`；文档提示含原文、勿外发。
- [ ] 5.5 单测：低分选择、summary 计数、写入失败不阻断、原子写。

## 6. 文档与验收

- [ ] 6.1 `src/cli/README.md` 增加 `--evidence` / `--evidence-all` 与证据路径、字段说明。
- [ ] 6.2 `docs/agents/eval-metrics-troubleshooting.md` 增加“6 步证据链 → 证据字段”对照表。
- [ ] 6.3 `docs/agents/api_contract.md` 登记证据目录与“不进 API/DB”的边界。
- [ ] 6.4 集成测试：构造“dense 有、rerank 后无、final 无”的题，跑 `--evidence`，断言证据 JSON 的 rank_before/rank_after/final_contexts。
- [ ] 6.5 活体冒烟：对 v6 的 Q21 跑一次 `--evidence`，确认 `fact_in_corpus=true` 且 final contexts 不含命中 chunk。
- [ ] 6.6 全量 `pytest tests/` + `ruff check src/cli src/rag src/agents` 通过。
