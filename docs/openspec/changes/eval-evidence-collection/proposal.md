## Why

当前评测只能给出**聚合分数**：`faithfulness` / `context_recall` / `context_precision` 低分时，无法从现有产物判断事实是在**解析/分块、query rewrite、向量召回、rerank、prompt 组装、生成、工具路由还是裁判**哪一步丢的。

现有证据缺口：

- `detail_json.retrieval_details` 只有 `source / score / kind`，**没有 chunk_id、没有 rerank 前后的候选集**；
- `trajectory` 只有工具名 / 参数 / 迭代数，**没有结果数、耗时、检索阶段快照**；
- 题集有 `reference_contexts` 文本与 `source_file`，**没有 gold chunk 归属**；
- 裁判只落 `error` 摘要，**没有逐条 verdict / reason / raw output**。

结果就是：Q21 这种“参考答案的 80,348 在题集 reference_contexts 里有、实际检索 contexts 没有”的题，只能推断是检索/排序，**无法区分 query rewrite、embedding、lexical、RRF、rerank、top-k 哪一段**；Q15/Q19 这种题集错误也会和检索漏召混在一起。

本 change 只做**证据采集**，不做自动根因判定，也不改评分口径：给低分/NaN/失败题生成一份可复盘的逐题证据包，让 6 步证据链（事实在语料→候选→rerank→最终上下文→回答→工具/裁判）从“猜”变成“查表”。

## What Changes

- 新增 `eval_deepeval --evidence`（可选 `--evidence-all`）调试开关：默认关闭，关闭时零额外开销。
- 逐题采集并落盘证据 JSON，默认只写**低分/NaN/失败/工具未完全命中/行为分低于阈值**的题；`--evidence-all` 写全部。
- 证据包覆盖：
  - **题集**：`reference` / `reference_contexts` / `source_file` / `expected_tools` / 可选的 `source_chunk_ids`；
  - **query 路径**：原始问题、实际 `retrieve_kb.args.query`、可用的 rewritten / sub_queries；
  - **检索链**：dense / lexical / RRF 候选（`chunk_id` + score）、rerank 前后（chunk_id / rank / score）、最终 contexts（chunk_id / source / page / score / order）；
  - **chunk 探针**：在 KB 全量分块中定位参考答案的数值/文本片段，记录命中的 `doc_id/chunk_id/page`，区分“语料里没有”与“语料里有但没召回”；
  - **工具链**：工具名、参数、结果数、迭代；`delegate_task` 只记顶层，不展开子代理；
  - **生成/裁判**：最终 answer、每个指标的 score / error / reason（有 verdict 时一并记录）、行为轴分数与 reason；
  - **汇总**：`summary.json` 记录本次 run 的题数、落盘题号、低分原因与证据路径。
- 证据只落本地 `data/deepeval/evidence/<run_id>/`（gitignored），不新增 API、不进 CSV / DB / Langfuse。
- 证据采集失败只记 warning，SHALL NOT 影响评测主流程、主分或门禁。

## Capabilities

### New Capabilities

- `eval-evidence`：评测逐题证据采集（opt-in、非评分、可复盘）。

### Modified Capabilities

（无）

> 实施前置：本 change 的代码建立在 `tool-correctness-diagnostic` 合并之后（复用 `tool_correctness` / `trajectory` / `expected_tools` 链路）。规格本身独立，不修改 `evaluation-pipeline` 的既有 requirement。

## Impact

- **代码**：`src/cli/eval_deepeval.py`（CLI 开关、编排、落盘）、`src/rag/retrieval.py`（dense/lexical/RRF/rerank 证据）、`src/agents/tools/rag_tools.py`（retrieve_kb 证据）、`src/infra/llm/request_context.py`（每轮 EvidenceCollector）、可能的 `src/infra/search/query_router.py`（query rewrite 证据）。
- **测试**：证据 schema、低分选择、非评分副作用、失败降级、chunk 探针、CLI 开关。
- **数据**：`data/deepeval/evidence/`（新增、gitignored）；不改 `eval_report` / CSV / Markdown 结构。
- **文档**：`src/cli/README.md`、`docs/agents/eval-metrics-troubleshooting.md`、`docs/agents/api_contract.md`（只登记证据路径与字段，不改评分契约）。
- **依赖/API**：无新增依赖，无 API 变更。
