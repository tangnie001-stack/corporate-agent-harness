# 评测指标异常排查手册

> **归属**：评测报告出现**低分 / NaN / N/A / 0** 时的解释与路由。本文只回答三件事：
> 这个分数代表什么、异常通常由什么引起、下一步先查哪里。指标定义以 `src/cli/README.md`
> 与 `docs/agents/glossary.md` 为准；trace 排障以 `docs/agents/troubleshooting.md` 为准；
> 跨轮数字对比以 `docs/agents/eval-ledger.md` 为准。本文不复制这些文档的正文。

## 1. 先分清异常类型

看到“异常”先不要按低分处理，先判断它属于哪一类：

| 现象 | 含义 | 是否质量问题 | 先看 |
|---|---|---|---|
| `0.0` / 低分 | 指标正常算出了一个低值 | 是（或题集/检索问题） | 该题的 `reference`、`contexts`、`retrieval_details` |
| `NaN`（主分） | 裁判调用失败：超时、JSON 解析失败、额度耗尽等 | **不是**，是指标没算出来 | `error` 列、NaN 率、`trace_id` 对应的裁判日志 |
| `N/A` / `null`（诊断/行为） | 不该评或没评到：empty 掩码、无标签、跑图失败、指标失败 | 不是分数质量结论 | `sample_class`、`tool_correctness_reason`、行为分 `reason` |
| `1.0` | 可能是真好，也可能是“没东西可判” | 不一定 | 该指标的 reason / verboseLogs |

**主分 NaN 率门禁**：`EVAL_NAN_RATE_MAX=0.10`，单位是**格**（纯 KB 样本 × 四指标），不是题数；
诊断指标与 `tool_correctness` 的 NaN 不计入该门禁。

## 2. 三层指标的边界

| 层 | 指标 | 进主分 | 进门禁 | 进 Langfuse | 说明 |
|---|---|---|---|---|---|
| 主分 | faithfulness / answer_relevancy / context_precision / context_recall | 是 | 是 | 是 | 只统计 `sample_class=pure_kb` 的样本 |
| 诊断 | contextual_relevancy / non_advice / tool_correctness | 否 | 否 | 否 | 只写 `detail_json` 与 Markdown |
| 行为轴 | TaskCompletion / StepEfficiency / AgentLoopDetection | 否 | 否 | 是 | 从 `test_run_*.json` 回读；threshold=0.5 只影响 `success` 字段 |

主分门禁阈值（`GATE_THRESHOLDS`）：

| 指标 | 阈值 |
|---|---|
| faithfulness | 0.85 |
| answer_relevancy | 0.85 |
| context_precision | 0.80 |
| context_recall | 0.70 |

`overall_score` 是四项主分有效均值的平均；`--run-type baseline` 不判门禁。

## 3. 主分四指标

### 3.1 faithfulness — 回答是否忠于检索材料

- **度量**：裁判从回答中抽 claim，对照 `retrieval_context` 判断每条是否被支持；分数 = 被支持 claim / 总 claim。
- **低分说明**：回答包含检索材料不支持的内容——幻觉、脑补、把常识当文档事实、引用错 chunk。
- **常见根因**：检索没召回到关键句；分块把表格/上下文切碎；prompt 允许自由发挥；参考答案/问题有歧义。
- **先查**：回答 vs 实际 contexts；该题 `retrieval_details` 的 source/score；chunk 是否包含答案所在的表格行。
- **注意**：回答抽不出 claim 时原生返回 **1.0**，不是 0；1.0 也可能表示“没东西可查”。

### 3.2 answer_relevancy — 回答是否切题

- **度量**：裁判把回答拆成 statement，判断每条是否在回应问题；分数 = 相关 statement / 总 statement。
- **低分说明**：答非所问、绕圈子、大量背景铺垫/套话、只答了一半、答成另一个问题。
- **常见根因**：问题歧义；检索到偏题材料；prompt 过度要求“全面”导致跑题；多轮历史污染；回答截断。
- **先查**：问题原文、回答首段、history 是否带入无关上下文。
- **注意**：它**不检查事实对错**；事实错但切题，AR 仍可能高。回答抽不出 statement 时原生返回 **1.0**。

### 3.3 context_precision — 检索结果的排序质量

- **度量**：裁判逐条判断 retrieved context 是否与问题+参考答案相关；按相关块出现的位置计算加权 precision@k。
- **低分说明**：相关材料排在后面，前面塞了大量无关块；或检索根本没找到相关内容。
- **常见根因**：rerank 参数不合适；query rewrite 偏了；top-k 太大引入噪声；chunk 粒度太粗。
- **先查**：`retrieval_details` 的 `score` 排序、相关块在第几位；`TOP_K_RETRIEVAL` / `TOP_K_RERANK`。
- **注意**：它是排序指标，不是覆盖率；相关块只要排在后面，分数就会低。

### 3.4 context_recall — 检索是否覆盖参考答案所需的信息

- **度量**：裁判把 `expected_output` 拆成事实/句子，逐条判断能否在 retrieved context 中找到；分数 = 覆盖数 / 总数。
- **低分说明**：检索漏了参考答案里的关键事实；或参考答案本身写了语料里没有/写错的内容；或分块把事实切散。
- **常见根因**：检索召回不足；文档没有该数据；题集参考答案臆造/期间错标；chunk 切分不合理。
- **先查**：参考答案 vs 实际 contexts；该题 `reference` 是否与语料一致；`retrieval_details` 是否命中对应文档/页。
- **注意**：CR 低不一定是检索的锅。v6 基线里 3 道 0 分题分别对应：题集臆造数值（Q19）、期间错标（Q15）、真实检索未命中（Q21）。

## 4. 诊断三指标

### 4.1 contextual_relevancy — 检索材料对问题是否相关

- **度量**：对每条 context 单独判断“它和问题相关吗”；分数 = 相关 context / 判断总数。
- **低分说明**：检索里混入大量无关块，或 chunk 太长、一条里只有一小段相关。
- **常见根因**：top-k 偏大、rerank 没压住噪声、chunk 过大、query 太宽泛。
- **先查**：前 8 条 context 的噪声比例、chunk 长度、top-k 配置。
- **注意**：只判**前 8 条**（并集顺序，不是最相关 8 条）；长财报 chunk 很容易被判“部分不相关”。v6 基线只有 **0.3128**，这是诊断信号，不是门禁失败，不要和主分横比。

### 4.2 non_advice — 是否给了投资建议

- **度量**：裁判从回答抽“建议型表述”，判断是否属于 financial advice；通过条件是 **NO**。
- **低分说明**：回答包含“建议买入/卖出/持有”“推荐配置”等投资建议——合规风险，不是回答质量。
- **常见根因**：问题本身在诱导建议；prompt 没限制；模型自由发挥。
- **先查**：回答中是否出现建议性动词/配置建议；`advice_types=["financial"]` 是否覆盖了该表述。
- **注意**：没有建议时返回 1.0；它不看事实正确性。

### 4.3 tool_correctness — 期望工具是否被调用

- **度量**：`expected_tools` 与实际**顶层**轨迹工具名对比；分数 = 命中的期望工具数 / 去重后的期望数。
- **低分说明**：该调的工具没调——直接参数化作答、只用了 `search_web`、只委派了 `delegate_task`、或跑图失败。
- **不扣分**：重复调用、额外调用；不比较参数/顺序/次数。
- **N/A**：题集无 `expected_tools`；跑图异常/`[ERROR]`/空回答；工具指标自身失败。
- **先查**：`trajectory.tool_calls`、`tool_correctness_reason`、该题是否发生了委派。
- **注意**：它只回答“调没调”，不回答“调得对不对”。v6 基线 22/22 = 1.0，只说明每题都调了 `retrieve_kb`。

## 5. 行为轴三指标

### 5.1 TaskCompletion — 任务是否完成

- **度量**：裁判从 trace（或 input/output）抽 task 与 outcome，判断任务是否达成，给 0–1 分。
- **低分说明**：最终回答没完成任务——拒答、只给过程不给结论、答了别的问题、空回答、关键步骤缺失。
- **常见根因**：Agent 中途放弃；工具结果没被用上；迭代触顶；prompt 让模型“解释过程”而不是“给答案”。
- **先查**：行为分 `reason`、最终回答、`test_run_*.json` 的 trace。
- **注意**：它看任务达成，不检查事实细节；细节错但任务形式完成，可能仍高分。

### 5.2 StepEfficiency — 执行步骤是否高效

- **度量**：裁判读 task + 完整 trace，评估是否存在多余步骤、重复检索、绕路、无意义格式化；给 0–1 分。
- **低分说明**：步骤冗余——同一 query 反复检索、先检索再联网再检索、为格式加很多不必要节点、迭代次数明显偏多。
- **常见根因**：Agent 打转；rerank/verify 循环过多；prompt 要求过度格式化；工具返回不稳定导致重试。
- **先查**：迭代数、重复工具调用、节点路径、行为分 `reason`（常直接点名多余步骤）。
- **注意**：低分不一定代表用户结果差；它衡量过程成本。v6 基线均值 0.7455，有一题低于 0.5。

### 5.3 AgentLoopDetection — 是否陷入循环

- **度量**：完全确定性、不调裁判，三个子信号加权：工具重复 40%、推理停滞 35%、调用图环 25%；1.0 = 未检出循环，0.0 = 严重循环。
- **低分说明**：Agent 打转、反复查同一内容、输出原地复读、调用图成环。
- **常见根因**：工具返回空/错误但 Agent 不换策略；verify 节点反复要求重试；prompt 没有退出条件；`max_iterations` 太大。
- **先查**：`score_breakdown`（三个子分）、`reason`、trace 中的重复 tool call / 相似 LLM 输出。
- **注意**：1.0 只表示三个确定性信号没检出，不等于绝对没有循环；当前 v6 的 verboseLogs 里可见 “No tool spans found”，工具重复子信号可能未参与判断。

## 6. 异常速查矩阵

| 指标 | 低分/异常先查什么 | 典型不是指标本身的问题 |
|---|---|---|
| faithfulness | 回答 vs 实际 contexts；未支持 claim | 检索漏召回、chunk 切碎、prompt 自由发挥 |
| answer_relevancy | 回答是否正面回答问题；跑题/套话 | 问题歧义、历史污染、输出截断 |
| context_precision | 相关块在 retrieved 列表中的位置；rerank/top-k | 相关块排在后面、噪声块太多 |
| context_recall | 参考答案的事实是否在语料/检索结果中 | 题集参考答案错、期间错标、检索漏召 |
| contextual_relevancy | 前 8 条 context 的噪声比例 | chunk 太大、query 太宽、top-k 太大 |
| non_advice | 回答是否出现投资建议 | 合规问题，不是质量分 |
| tool_correctness | 顶层轨迹是否调了 expected_tools | 参数化作答、只联网、只委派、跑图失败 |
| TaskCompletion | 最终回答是否完成任务 | 拒答、空回答、迭代触顶、只给过程 |
| StepEfficiency | 迭代数、重复工具调用、多余节点 | Agent 打转、verify 循环、过度格式化 |
| AgentLoopDetection | 相同工具调用/相似输出/调用图环 | 工具空返回、无退出条件、max_iterations 过大 |

## 7. 当前 v6 基线（判断“正常”长什么样）

来源：`deepeval_eval_20261018_041404`（22 题）+ 对应行为轴 JSON；`tool_correctness` 为 22 条既有轨迹 + v6 标签的离线重放结果，不是一次新的 agent 运行。详见 `docs/agents/eval-ledger.md`。

| 层 | 指标 | 基线 |
|---|---|---|
| 主分 | faithfulness | 0.9653 |
| 主分 | answer_relevancy | 0.9515 |
| 主分 | context_precision | 0.7913（低于 0.80 门禁） |
| 主分 | context_recall | 0.8413 |
| 诊断 | contextual_relevancy | 0.3128（诊断常态，不是门禁） |
| 诊断 | non_advice | 1.0000 |
| 诊断 | tool_correctness | 22/22 = 1.0000 |
| 行为 | TaskCompletion | 0.9818 |
| 行为 | StepEfficiency | 0.7455 |
| 行为 | AgentLoopDetection | 1.0000 |

已知异常样本：

- **Q19**：参考答案写“经审核毛利率 42%”，语料是 53% → CR 0，属**题集臆造数值**。
- **Q15**：题干写 2024 年，参考答案用 Q4 口径 → CR 0，属**期间错标**。
- **Q21**：参考答案的 80,348 在语料中确有，但检索未命中 → CR 0，属**真实检索问题**。
- **Q16 / Q22**：主分 NaN（Q16 `context_recall` 裁判 `JSONDecodeError`，Q22 `faithfulness` 裁判输出截断）→ **裁判失败**，不是质量回归。

## 8. 数据在哪里、怎么查

### 8.1 `eval_report` 在哪个库

`eval_report` 在 **PostgreSQL 的应用库 `corporate_rag`**（不是 Langfuse 的 `langfuse` 库）：

- ORM：`src/infra/db/models/eval_report.py`，`__tablename__ = "eval_report"`
- 引擎：`src/infra/db/engine.py` → `build_postgres_dsn()` → `postgresql+asyncpg://.../corporate_rag`
- compose：app 容器 `POSTGRES_HOST=postgres` / `POSTGRES_DATABASE=corporate_rag` / `POSTGRES_USER=corporate_rag`
- 宿主：`POSTGRES_HOST=localhost`、`POSTGRES_PORT=5432`
- Langfuse 是**同一个 PostgreSQL 实例的另一个 database**（`langfuse`），表不在这边

`detail_json` 是 **`TEXT` 列，不是 `JSONB`**（`eval_report.py` 的 `mapped_column(Text)`），内容是逐题 JSON 数组；`json.dumps` 可能写出非标准 `NaN`，所以 **`detail_json::jsonb` 在含 NaN 的行上会失败**。查文本后交给 Python `json.loads`（默认接受 NaN）更稳。

### 8.2 常用查询

```bash
# 宿主侧；容器内把 host 改成 postgres
PGPASSWORD="$POSTGRES_PASSWORD" psql \
  -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" \
  -U "${POSTGRES_USER:-corporate_rag}" -d "${POSTGRES_DATABASE:-corporate_rag}" \
  -c "SELECT id, kb_id, run_type, qa_count, eval_date
      FROM eval_report ORDER BY eval_date DESC LIMIT 5;"
```

```bash
# 取最新一次的逐题明细（文本）
PGPASSWORD="$POSTGRES_PASSWORD" psql -h localhost -U corporate_rag -d corporate_rag -tA \
  -c "SELECT detail_json FROM eval_report ORDER BY eval_date DESC LIMIT 1;" \
  > /tmp/detail.json
```

`detail_json` 每题的常用键：

- `q_index`、`question`、`testset_version`
- 四主分：`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`
- 诊断：`contextual_relevancy` / `non_advice` / `tool_correctness` / `tool_correctness_reason`
- `error`、`retrieval_details`（source / score / kind）

### 8.3 报告与 trace

| 产物 | 位置 | 用途 |
|---|---|---|
| CSV | `data/deepeval/reports/deepeval_eval_<timestamp>.csv` | 逐题分数、`trajectory`、`trace_id` |
| Markdown | 同名 `.md` | 主分均值、诊断段、工具选择段、行为轴段 |
| `detail_json` | PostgreSQL `eval_report` | 与 CSV 同源的逐题明细；`kb_eval` API 不返回诊断键 |
| 行为轴 | `data/deepeval/reports/behaviour_<timestamp>/test_run_*.json` | `testCases[].metricsData[]` 的逐题行为分 |
| 评测证据 | `data/deepeval/evidence/<run_id>/q_<q_index>.json` + `summary.json` | 事实链与确定性归因；仅 `--evidence` / `--evidence-all` 产出 |
| 日志 / Langfuse | 按 `trace_id` | 裁判调用、检索、工具、错误证据，见 `docs/agents/troubleshooting.md` |

### 8.4 裁判 / 被测者 trace 怎么看

- **被测者（SUT）**：每题一个 `trace_id`（CSV 同名），也是 Langfuse trace id；其 trace
  metadata 带 `component=sut`，有 run 上下文时另带 `run_trace_id`，可据后者聚一轮全部题。
- **裁判**：不进被测者的 Langfuse trace，走日志两层——`judge call` 是 measure 汇总
  （attempt / 耗时 / `error` / reason 摘要），`judge http` 是每次 HTTP 明细
  （status / finish_reasons / tokens / `json_ok`）。
- **串链路**：`evaluation run` 拿 `run_trace_id` → 每题 `question answer start/done` 拿
  `trace_id` → `judge call` 拿 `judge_measure_id` → `judge http` 拿 `judge_call_id`；
  命令见 `docs/agents/troubleshooting.md`「评测 trace 链」。
- **`judge http` 的 `status=null` + `error=no_response` 是传输失败**（连接失败 / 超时），
  **不是裁判判错**；拿到了 2xx 但 `json_ok=false` 才是响应内容不可用。行为轴 judge 没有
  active measure，故在 `judge http` 中缺席（v1 已知边界）。

### 8.5 用 `--evidence` 走六步证据链

`--evidence` 会为低分/异常题写 `data/deepeval/evidence/<run_id>/q_<q_index>.json`，
把“分数低在哪一层”变成可直接读的事实链。排查时按顺序走，不要跳步：

| 步 | 阶段 | 看哪些字段 | 判断 |
|---|---|---|---|
| 1 | 分数 | CSV / `detail_json` 的该题指标 | 先分清低分 / NaN / N/A（§1），NaN 与 0 不是一回事 |
| 2 | trace | `trace.run_trace_id` / `trace.sut_trace_id` / `trace.judge_measures` | 串起整轮、该题与裁判调用；`judge_measures` 有 error 即裁判失败 |
| 3 | corpus | `facts.fact_in_corpus` / `facts.fact_coverage.corpus` | false 或 null → 语料没有该事实：解析/分块或题集错误（null = 语料不可用，不下结论） |
| 4 | candidates | `facts.fact_in_candidates` / `facts.fact_coverage.candidates` | false → `retrieval_miss`：密集/词法/RRF 都没召回 |
| 5 | rerank | `facts.fact_in_reranked` | false 且 candidates 为 true → `rerank_or_topk`：候选有、精排丢 |
| 6 | final / answer | `facts.fact_in_final`、`facts.answer_mentions_fact`、`attribution.*` | final false → `rerank_or_topk`（top-k 截断）；final true 但指标低 → 排序或生成；**直接采信 `root_cause` / `confidence` / `evidence`，不要自行再推** |

- 归因只对实际执行的指标产出；`inconclusive` 是合法结果（证据不足），不是“没问题”。
- 多值题在某一阶段只命中部分 token 时，归因固定为 `inconclusive`，看 `missing_fact_tokens`。
- web 样本没有 KB 事实链（`retrieval.status=not_applicable`），不做 KB 链归因。
- faithfulness 在 F v1 没有 claim 级证据，最多只能判到 `judge_failure` 或 `inconclusive`。
- 命令、选择规则与产物边界见 `src/cli/README.md`「评测证据（--evidence）」；字段契约见
  `docs/agents/api_contract.md` §8.7。

## 9. 排查顺序

1. **看 `error` 与 NaN**：先排除裁判失败/超时/额度，不要把它当质量回归。
2. **定位题号**：从 CSV / `detail_json` 的 `q_index` 找到异常题。
3. **看 `retrieval_details`**：source / score / kind，先判断是“没检索到”还是“检索到了但排序差”。
4. **看回答 vs contexts**：判断是幻觉、跑题，还是上下文里根本没有答案。
5. **看 `reference`**：CP/CR 低时先确认参考答案本身对不对、期间/单位是否一致。
6. **看 `trajectory`**：工具是否该调没调、是否重复、是否委派、迭代数是否异常。
7. **拿 `trace_id` 查日志/Langfuse**：裁判失败、工具异常、图执行路径；再回到 `docs/agents/troubleshooting.md`。

## 10. 常见误判

- **1.0 不等于完美**：faithfulness / answer_relevancy / non_advice 在“没 claim / 没 statement / 没 advice”时原生返回 1.0。
- **CP/CR 低不一定是检索的锅**：参考答案臆造、期间错标、单位不一致都会让 CR 变 0。
- **contextual_relevancy 低是常态**：只判前 8 条，长财报 chunk 很容易被判部分不相关；它不是门禁。
- **StepEfficiency 低不等于答案错**：它惩罚的是过程冗余/格式化，不是事实正确性。
- **AgentLoopDetection 1.0 不是绝对没有循环**：只说明三个确定性信号未检出；工具 span 不可见时工具重复子信号可能没参与。
- **tool_correctness 1.0 不是检索质量好**：只说明期望工具被调用了，不代表参数对、结果好。
- **NaN 不是 0**：NaN 是指标没算出来；0 是算出来但确实差。
- **N/A 不是 0**：empty 掩码、无标签、跑图失败、指标失败都不该按 0 分处理。

## 11. 相关文档

- 指标定义与 CLI 用法：`src/cli/README.md`
- 术语：`docs/agents/glossary.md`
- 跨轮基线与口径：`docs/agents/eval-ledger.md`
- `detail_json` 契约：`docs/agents/api_contract.md`
- trace 排障：`docs/agents/troubleshooting.md`
- 评测待办与已知缺口：`docs/agents/requirements_pool.md`
