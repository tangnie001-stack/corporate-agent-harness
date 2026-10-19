# src/cli — 诊断与评估命令行工具

本目录存放 RAG 系统的检索诊断与质量评估 CLI 工具，不参与线上服务。所有命令在项目根目录执行：

```bash
python -m src.cli.<模块名> --help
```

## 前提条件

- 环境变量：`.env` 中配置有效的 `DASHSCOPE_API_KEY`（Embedding / LLM / Rerank 调用）
- 基础设施：PostgreSQL（关系元数据 + pgvector 向量存储）可用（知识库查询依赖）
- 数据就绪：目标知识库已创建且文档已入库

## 命令一览

| 命令 | 用途 |
|---|---|
| `eval_deepeval` | DeepEval 原生四指标评估主脚本：执行评估 / 质量门禁 |
| `testset_synth` | v6 出题：从知识库分块生成测试集 |
| `compare_retrieval` | 遍历「检索 top-K × 重排 top-K」参数网格，用评测指标对比选参 |
| `check_retrieval` | 对已有知识库执行语义检索，打印结果供人工检查 |

---

# v6 出题（DeepEval Synthesizer）

从知识库分块生成测试集，产出 `data/deepeval/testset/testset_<kb_id>_v<N>.json`，供 `eval_deepeval` 直接读取。

```bash
python -m src.cli.testset_synth --kb-id <uuid> --version 6 \
  --docs neusoft_2025_q1.pdf tencent_2024_annual.pdf --kb-name test
```

## 参数

| 参数 | 说明 |
|---|---|
| `--kb-id` | 知识库 UUID（必填） |
| `--version` | 版本号；省略时取「现有最大版本 + 1」 |
| `--docs` | 限定文档文件名（分块 metadata 的 `source`）；省略 = 全部 |
| `--kb-name` | 写入元数据的知识库名 |
| `--force` | 允许覆盖已存在的版本文件 |
| `--dry-run` | 只打印计划，不调用 LLM |

- 显式 `--version` 时必须同时传 `--docs`（保证版本可复现）。
- `--docs` 每一项必须匹配 ≥1 个分块，否则报错退出。
- `--dry-run` 打印实际 `doc_names`、上下文数、覆盖块数、目标路径与调用量区间。
- 每个上下文默认取 2 个连续分块（`EVAL_SYNTH_CHUNKS_PER_CONTEXT`），末尾不足 2 块的余量丢弃。

## 退出语义

| 情形 | 退出码 |
|---|---|
| 写出成功 / `--dry-run` 完成 | 0 |
| 参数非法（如显式 `--version` 未带 `--docs`） | 2 |
| 语料不匹配、全部上下文失败、语言门失效（丢弃率 > 50%）、通过样本 < 15、目标版本已存在且未 `--force` | 1 |

质量门为确定性检查、不调用 LLM，顺序：自足改写 → 完整句 → 语言 → 去重 → 多跳占比 ≤ 1/3。通过样本 < 15 时自动把每上下文出题数提到 2 重跑一次，仍不足则失败且不写文件。

> `eval_deepeval --generate` 保留参数，命中即报错并指向本命令。

---

# 评测工作流详解

评测的核心是**用固定版本的 QA 测试集评估 RAG 系统的检索与生成质量**。完整链路：

```
① 准备测试集 ──> ② 执行评估 ──> ③ 质量门禁 ──> ④ 参数选优（可选）
testset_synth     eval_deepeval --kb-id   eval_deepeval --gate   compare_retrieval
```

## 第一步：准备测试集

出题由 v6 出题器（DeepEval `Synthesizer`）承接，命令与退出语义见上文「v6 出题」一节。评测直接读取已有测试集 JSON，可用 `--testset-version` 指定历史版本。

- **位置**：`data/deepeval/testset/testset_<kb_id>_v<N>.json`（目录默认 `EVAL_TESTSET_DIR`）
- **版本管理**：文件名中的 `v<N>` 为版本号，评估时可指定任意历史版本
- **文件结构**：`metadata` + `samples`，完整字段契约见 `docs/agents/api_contract.md` §8.2；v6 每题额外带 `hop_type` / `hop_basis` / `expected_tools` / `numeric_risk` / `source_file`

> 历史测试集文件中 `ragas_version` / `synthesizer_name` 为遗留字段，当前加载器不消费。

## 第二步：执行评估（怎么测试）

### 运行命令

```bash
# 用最新版测试集评估
python -m src.cli.eval_deepeval --kb-id <知识库UUID>

# 指定测试集版本评估
python -m src.cli.eval_deepeval --kb-id <知识库UUID> --testset-version 4

# 自定义报告输出路径
python -m src.cli.eval_deepeval --kb-id <知识库UUID> --output /path/report.csv

python -m src.cli.eval_deepeval --kb-id <知识库UUID> --testset-version 5 --limit 1   # 冒烟：只跑前 1 题（勿配 --gate）

python -m src.cli.eval_deepeval --kb-id <知识库UUID> --testset-version 5 --deepeval-only   # 只跑行为轴（无主分/门禁）
```

`--limit N` 只用于逐级活体（1 → 5 → 21），**不要与 `--gate` 同用**——少数题上的门禁没有统计意义；全量口径仍是不加 `--limit`。

`--deepeval-only`（别名 `--behaviour-only`）跳过四指标：**不产出主分、不写 CSV、不写 `eval_report`、不回写 Langfuse、不执行门禁**，报告只含 Agent 行为轴；「工具选择」段显式写「未跑（--deepeval-only）」，不静默缺失。它用于 DeepEval 功能的快速迭代验收，**不要用于 A/B 对比或发布判定**。与 `--gate` 互斥。

### 内部流程

1. 加载测试集 JSON（取出 `questions`、`ground_truth` 与 `expected_tools`）
2. 构建生产同款 LangGraph（检索 → rerank → 生成）
3. 逐题让 RAG 系统生成回答 `answer` 与检索上下文 `contexts`
4. 用 DeepEval 原生四指标打分（`AnswerRelevancyMetric` / `FaithfulnessMetric` / `ContextualPrecisionMetric` / `ContextualRecallMetric`）；诊断指标（`contextual_relevancy` / `non_advice` / `tool_correctness`）另行记录，不进主分与门禁

> **上下文含实体锚点**：评估收集的 `contexts` 与生产 prompt 共用 `RAGContext.to_prompt_text()` 渲染格式（`src/rag/context.py`）。当分块 metadata 带实体（`company`/`report_period`/`sec_code` 等，标签为 `公司`/`期间`/`代码`）时，上下文会多出一行 `实体标签: 值` 锚点，再跟 `内容:` 正文；无实体时保持 `来源 → 内容` 原格式。这保证评测裁判看到的上下文与线上生成模型完全一致（实体锚点用于增强忠实度）。评估脚本入口见 `src/cli/eval_deepeval.py:157`。

> **评估模式跳过追问**：批量评估以 `skip_clarify=True` 运行图——即使 classify 判定问题缺实体（如缺年份），也不会停在「追问澄清」分支（生产交互链路会追问，评估不会），而是直接走检索+生成。这样单轮指标才有分数可算；追问分支的真实效果属于多轮/Agent 评估范畴，不在单轮评测内。

> 注意：评估阶段每个指标内部会多次调用 LLM（faithfulness 把回答拆句逐句判断、answer_relevancy 反向生成候选问题等），所以**评估耗时明显长于回答生成**。若知识库尚无可用分块或 `JUDGE_MODEL` 未配置会直接报错退出。

## 测试结果在哪里看

评估完成后产出两个文件（默认在 `data/deepeval/reports/`，可用 `--output` 自定义；`--deepeval-only` 只产出 `.md`）：

| 文件 | 内容 |
|---|---|
| `deepeval_eval_<timestamp>.csv` | **逐条明细**：每行一条 QA 及其四项指标得分 |
| `deepeval_eval_<timestamp>.md` | **摘要报告**：评估日期、配置（TOP_K_RETRIEVAL / TOP_K_RERANK）、QA 对数、每行指标表、平均值 |

CSV 每行结构（`question`、`ground_truth` 为原测试集内容，`trace_id` 为该问题链路追踪 ID；指标列按 `EVAL_METRIC_COLUMNS` 顺序，诊断指标紧随其后）：

```
index, question, ground_truth, trace_id, q_index, error, faithfulness, answer_relevancy, context_precision, context_recall, contextual_relevancy, non_advice, tool_correctness, ...
```

- `contextual_relevancy` / `non_advice` / `tool_correctness` 都是**诊断列**，`--deepeval-only` 不产出。
- `tool_correctness` 在**有 `expected_tools` 且非失败**时为 `0.0`–`1.0`；**无标签或跑图失败/空回答**时为空。
- 逐题原因**只进 `detail_json` 与 Markdown 报告**：CSV **不新增** `tool_correctness_reason` 列。

MD 摘要表格同样带 `trace_id` 列。每个问题在评估时会生成独立的 `eval_<hex>` 格式 trace_id，并注入到该问题期间的所有日志行（loguru patcher 读取 `current_trace_id`），同时作为 Langfuse trace id —— 用 CSV 里的 trace_id 即可在日志 / Langfuse 中回溯这个问题的完整链路（分类 → 改写 → 检索 → 精排 → 生成），定位指标异常的根因（前提：`.env` 的 `LANGFUSE_ENABLE=true` 且 Langfuse 后端可达 —— 关闭时该列无对应 trace）。

**按 trace_id 查日志（含裁判调用事件与请求级明细）**：

```bash
python -m src.cli.trace_logs eval_19128e15c446                    # 该题的全部日志
python -m src.cli.trace_logs eval_19128e15c446 --grep "judge call" # 只看裁判 measure 汇总
python -m src.cli.trace_logs eval_19128e15c446 --grep "judge http" # 只看每次裁判 HTTP
python -m src.cli.trace_logs eval_19128e15c446 --count             # 只看行数
```

### 评测 trace

- `run_trace_id`：每次评测一个（`eval_run_<timestamp>_<8hex>`），报告头 `Run trace`。
- `trace_id`：每题一个，CSV 已有。
- `judge_measure_id`：一次裁判指标打分一个（`<trace_id>:<metric>:<attempt>`）。
- `judge_call_id`：一次裁判 HTTP 调用一个（`<judge_measure_id>:<call_index>`）。

`judge call` 是 measure 汇总，`judge http` 是每次 HTTP 明细（成功与失败都记）。

裁判每次尝试记 `judge call` 事件（`metric` / `q_index` / `trace_id` / `run_trace_id` / `judge_measure_id` / `attempt` / `latency_ms` / `ok`，以及本次 measure 的 `request_count` / `response_count` / `statuses` / `tokens_in` / `tokens_out` / `error_count`）；**失败时 `error` 为异常链摘要**（`外层类<-根因类: 根因消息`），成功时为空串，**不含** reason 原文（只记 `reason_len` / `reason_hash`）。每次 HTTP 调用另记 `judge http`（`judge_call_id` / `status` / `finish_reasons` / tokens / `json_ok` / `error`）；`status=null` + `error=no_response` 表示连接失败/超时。

MD 摘要末尾会给出整体平均值，例如：

```
**Averages:** faithfulness=0.9020, answer_relevancy=0.8721, context_recall=0.8140, context_precision=0.7933
```

## 结果如何解读

### 四个指标（0~1，越高越好）

| 指标 | 门禁阈值 | 含义 | 得分低说明什么 |
|---|---|---|---|
| `faithfulness` 忠实度 | ≥ 0.85 | 回答是否忠于检索到的上下文（有无幻觉） | 模型脱离上下文编造内容 |
| `answer_relevancy` 相关性 | ≥ 0.85 | 回答与问题的相关程度 | 答非所问、回答跑题 |
| `context_recall` 召回率 | ≥ 0.70 | 检索上下文覆盖参考答案所需信息的比例 | 检索环节漏掉了关键内容 |
| `context_precision` 精确率 | ≥ 0.80 | 检索上下文中真正有用的信息占比 | 检索混入大量无关噪音 |

### 诊断指标（不进主分与门禁）

| 指标 | 含义 | 口径 |
|---|---|---|
| `contextual_relevancy` | 检索上下文与问题的相关程度 | 只判前 8 条 context（并集顺序，非最相关 8 条） |
| `non_advice` | 回答是否给出越界建议（`financial` 类型） | 不做金融建议；跑图失败/空样本不评分 |
| `tool_correctness` | 工具选择是否正确 | 命中的期望工具数 / **去重后**的期望数（0.0–1.0）；只比较工具名与类型（类型固定 `FUNCTION`），不比较参数、顺序、调用次数 |

`tool_correctness` 的三态：

- **有标签**：按 `expected_tools` 逐题评分；`[]` 表示「期望不调用任何工具」——无调用记 `1.0`，有调用记 `0.0`。
- **无标签**（字段缺失或 `null`，如 v5 题集）：N/A（`null` / CSV 空）。
- **失败**（跑图异常、`[ERROR]` 回答、空回答或纯空白）：N/A（`null` / CSV 空），不记 `0.0`。

`sample_class=empty` 只掩码四指标与 `contextual_relevancy` / `non_advice`，`tool_correctness` **照常评分**。

### 解读方法

1. **看平均值**（MD 摘要底部）——判断系统整体处于什么水平，对照门禁阈值
2. **看逐条明细**（CSV / MD 表格）——定位具体是哪些 QA 拖了后腿，阅读对应问题判断失败原因
3. **按指标定位环节**：

| 现象 | 排查方向 |
|---|---|
| recall 低 | 检索环节问题：调大 `TOP_K_RETRIEVAL`、检查分块质量 |
| precision 低 | 检索/精排噪音：调大 `TOP_K_RERANK`、检查重排效果 |
| faithfulness 低 | 生成环节幻觉：检查 prompt 约束、换模型 |
| answer_relevancy 低 | 生成环节相关性：检查 prompt 与模型 |

> 完整的三层指标（主分/诊断/行为轴）、`0` / `NaN` / `N/A` 语义与逐指标排查顺序见
> `docs/agents/eval-metrics-troubleshooting.md`。

### 评测证据（--evidence）

```bash
python -m src.cli.eval_deepeval --kb-id <知识库UUID> --testset-version 6 --evidence
python -m src.cli.eval_deepeval --kb-id <知识库UUID> --testset-version 6 --evidence-all
```

- `--evidence`：只写门禁指标（`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`）低分或 NaN / 跑图失败 / 工具未命中 / 行为低分题（`sample_class=empty` 必写）；诊断指标低分不触发写盘；web 样本主分低不触发（没有 KB 事实链），但工具与行为异常仍会写。
- `--evidence-all`：写全部题（隐含 `--evidence`）。
- 路径：`data/deepeval/evidence/<run_id>/q_<q_index>.json` + `summary.json`；`run_id` 为 UTC 时间戳 `%Y%m%d_%H%M%S`，与同轮报告同源。
- 单题内容：`retrieval` 各阶段证据（`dense` / `lexical` / `rrf` / `rerank` / `rerank_before` / `rerank_after` / `dedup` / `topk` / `final_contexts`，**四种形状并存**：阶段快照 / ID 对 / `final_contexts` 列表 / `rerank` 超时标记 `{failed, reason, score_kind}`，schema 见契约）、`facts` 事实链与 `attribution` 归因（`root_cause` / `confidence` / `evidence`）。`inconclusive` 是合法结果，表示证据不足以断定。
- `summary.json`：本轮 `evidence_files` / `low_score_questions` / `root_cause_counts` / `skipped_metric_count` 等汇总。
- 边界：faithfulness 在 F v1 不做 claim 级幻觉归因；多值题部分覆盖只给 `inconclusive`；web 样本不做 KB 链归因；证据是旁路观测，不改评分、门禁与 Langfuse，写盘失败只告警。

字段契约见 `docs/agents/api_contract.md` §8.7，按证据链排查的顺序见 `docs/agents/eval-metrics-troubleshooting.md` §8.5。

## 第三步：质量门禁（怎么判定达标）

```bash
python -m src.cli.eval_deepeval --kb-id <知识库UUID> --gate
```

- 对四个指标逐项打印 `PASS / FAIL`（用全量 QA 的平均值对比阈值）
- 任一指标不达标：退出码为 **1**，并列出未通过的具体问题
- 全部达标：退出码为 **0**
- 适合接入 CI / 发版前检查

## 第四步：参数选优（可选）

```bash
python -m src.cli.compare_retrieval --kb-name <知识库名称>
```

遍历 `TOP_K_RETRIEVAL × TOP_K_RERANK` 的 3×3 网格（`[10, 30, 50] × [3, 5, 8]`），每组组合跑一次评估，打印各指标对比表，据此确定最优检索参数。运行前需先有测试集。

---

# 命令参考

## eval_deepeval — 参数一览

| 参数 | 说明 |
|---|---|
| `--kb-id` | 知识库 UUID（与 `--list-kbs` 互斥） |
| `--list-kbs` | 列出所有可用知识库后退出 |
| `--generate` | 已下线（保留参数，命中即报错并指向 `testset_synth`） |
| `--size` | 出题已下线（保留参数，当前无效果） |
| `--model` | 出题已下线（保留参数，当前无效果） |
| `--testset-version` | 指定测试集版本号（默认取最新） |
| `--gate` | 评估后检查质量门禁，不达标退出码 1 |
| `--deepeval-only`（`--behaviour-only`） | 只跑 Agent 行为轴，跳过四指标（无主分/门禁、不写 CSV 与 `eval_report`；工具选择：未跑；与 `--gate` 互斥） |
| `--run-type` | `manual`（默认）/ `baseline`；`baseline` 不判门禁（与 `--gate` 互斥） |
| `--evidence` | 写低分/异常题的证据到 `data/deepeval/evidence/<run_id>/`（不改评分与门禁） |
| `--evidence-all` | 写全部题证据（隐含 `--evidence`） |
| `--limit` | 只跑前 N 题（冒烟用；勿与 `--gate` 同用） |
| `--output` | CSV 报告输出路径（默认 `data/deepeval/reports/deepeval_eval_<timestamp>.csv`） |
| `--session-id` | 评估用会话 ID，默认 `deepeval_eval_session` |

## compare_retrieval — 检索参数对比实验

```bash
python -m src.cli.compare_retrieval --kb-name rag_eval
```

- 通过子进程调用 `eval_deepeval` 并覆盖环境变量 `TOP_K_RETRIEVAL` / `TOP_K_RERANK`（不改 `.env`）
- 指标列：`faithfulness`、`answer_relevancy`、`context_precision`、`context_recall`

## check_retrieval — 检索质量检查

```bash
python -m src.cli.check_retrieval --kb <知识库名称> --query "<问题>" [--top-k 10]
```

对已有知识库执行一次语义检索，打印每条结果的距离分数、来源文件、页码与内容摘要。注意只走语义检索（pgvector），**不走** rerank 与生成环节。

---

## 数据目录速查（默认 `data/deepeval/`）

| 目录 | 内容 |
|---|---|
| `testset/` | 测试集 JSON：`testset_<kb_id>_v<N>.json`，按版本递增 |
| `reports/` | 评估报告：`deepeval_eval_<timestamp>.csv`（逐条）+ 同名 `.md`（摘要） |
| `evidence/` | 评测证据：`<run_id>/q_<q_index>.json`（逐题）+ `summary.json`（本轮汇总，仅 `--evidence` / `--evidence-all`） |
| `llm_cache/` | LLM 响应磁盘缓存（重复生成提速，支持中断恢复） |

## 典型工作流

1. **准备测试集**：`testset_synth --kb-id <uuid> --docs <文件...>` 生成，或确认 `data/deepeval/testset/` 下已有目标知识库的测试集
2. **执行评估**：`eval_deepeval --kb-id <uuid>` → 查看 `data/deepeval/reports/` 下的 CSV 与 MD
3. **质量门禁**：`eval_deepeval --kb-id <uuid> --gate`（CI / 提交前检查，不达标退出码 1）
4. **参数选优**：`compare_retrieval --kb-name <名称>` → 按指标对比表确定 `TOP_K` 组合
5. **人工抽检**：`check_retrieval --kb <名称> --query "<问题>"` 验证检索效果
