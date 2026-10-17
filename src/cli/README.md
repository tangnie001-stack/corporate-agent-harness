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
| `eval_deepeval` | RAGAS 评估主脚本：执行评估 / 质量门禁 |
| `compare_retrieval` | 遍历「检索 top-K × 重排 top-K」参数网格，用 RAGAS 指标对比选参 |
| `check_retrieval` | 对已有知识库执行语义检索，打印结果供人工检查 |

---

# RAGAS 工作流详解

RAGAS 评估的核心是**先用文档自动生成 QA 测试集，再用测试集评估 RAG 系统的检索与生成质量**。完整链路：

```
① 生成测试集 ──> ② 执行评估 ──> ③ 质量门禁 ──> ④ 参数选优（可选）
eval_deepeval --generate   eval_deepeval --kb-id   eval_deepeval --gate   compare_retrieval
```

## 第一步：生成测试集（怎么生成）

### 运行命令

```bash
# 先查出知识库的 UUID（评估与生成都只需要 kb-id）
python -m src.cli.eval_deepeval --list-kbs

# 用文档自动生成 20 条 QA 测试集（size 默认取配置 RAGAS_TEST_SIZE）
python -m src.cli.eval_deepeval --kb-id <知识库UUID> --generate
python -m src.cli.eval_deepeval --kb-id <知识库UUID> --generate --size 30
```

### 内部流程

1. 从 PostgreSQL 查询知识库名称，按白名单（`RAGAS_DOC_WHITELIST`）取文档 ID
2. 从 PostgreSQL 取出这些文档已有的分块
3. 分块脱敏后构建 KnowledgeGraph，应用 Summary / NER 抽取
4. 生成 Personas（用户角色）→ Scenarios（场景）→ QA Samples（问答对）
5. 写入测试集 JSON，版本号自动递增

### 生成结果在哪里看

生成结束时终端会打印产物路径，形如：

```
测试集已保存: data/ragas/testset/testset_<kb_id>_v1.json (v1, 20 条)
```

- **位置**：`data/ragas/testset/testset_<kb_id>_v<N>.json`（目录默认 `RAGAS_TESTSET_DIR`）
- **版本管理**：同一知识库每生成一次版本号 +1（`v1` → `v2`），评估时可指定任意历史版本
- **文件结构**：

```json
{
  "metadata": {
    "kb_name": "知识库名",
    "version": 1,
    "generated_at": "ISO 8601 时间",
    "llm_model": "生成用模型",
    "testset_size": 20,
    "ragas_version": "0.4.x",
    "doc_ids": ["文档UUID..."]
  },
  "samples": [
    {
      "user_input": "问题",
      "reference": "参考答案",
      "reference_contexts": ["依据文档片段..."],
      "synthesizer_name": "生成器类型"
    }
  ]
}
```

- **生成加速**：LLM 响应有磁盘缓存 `data/ragas/llm_cache/`（DiskCacheBackend），重复生成可复用，支持中断恢复

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

`--deepeval-only` 跳过 RAGAS 四指标：**不产出主分、不写 CSV、不写 `eval_report`、不回写 Langfuse、不执行门禁**，报告只含 Agent 行为轴。它用于 DeepEval 功能的快速迭代验收，**不要用于 A/B 对比或发布判定**。与 `--gate` 互斥。

### 内部流程

1. 加载测试集 JSON（取出 `questions` 与 `ground_truth`）
2. 构建生产同款 LangGraph（检索 → rerank → 生成）
3. 逐题让 RAG 系统生成回答 `answer` 与检索上下文 `contexts`
4. 用 RAGAS 对四个指标打分

> **上下文含实体锚点**：评估收集的 `contexts` 与生产 prompt 共用 `RAGContext.to_prompt_text()` 渲染格式（`src/rag/context.py`）。当分块 metadata 带实体（`company`/`report_period`/`sec_code` 等，标签为 `公司`/`期间`/`代码`）时，上下文会多出一行 `实体标签: 值` 锚点，再跟 `内容:` 正文；无实体时保持 `来源 → 内容` 原格式。这保证 RAGAS 的 NLI 裁判看到的上下文与线上生成模型完全一致（实体锚点用于增强忠实度）。评估脚本入口见 `src/cli/eval_deepeval.py:157`。

> **评估模式跳过追问**：批量评估以 `skip_clarify=True` 运行图——即使 classify 判定问题缺实体（如缺年份），也不会停在「追问澄清」分支（生产交互链路会追问，评估不会），而是直接走检索+生成。这样单轮指标才有分数可算；追问分支的真实效果属于多轮/Agent 评估范畴，不在单轮评测内。

> 注意：评估阶段每个指标内部会多次调用 LLM（faithfulness 把回答拆句逐句判断、answer_relevancy 反向生成候选问题等），所以**评估耗时明显长于回答生成**。若知识库尚无可用分块或 `RAGAS_LLM_MODEL` 未配置会直接报错退出。

## 测试结果在哪里看

评估完成后产出两个文件（默认在 `data/ragas/reports/`，可用 `--output` 自定义；`--deepeval-only` 只产出 `.md`）：

| 文件 | 内容 |
|---|---|
| `ragas_eval_<timestamp>.csv` | **逐条明细**：每行一条 QA 及其四项指标得分 |
| `ragas_eval_<timestamp>.md` | **摘要报告**：评估日期、配置（TOP_K_RETRIEVAL / TOP_K_RERANK）、QA 对数、每行指标表、平均值 |

CSV 每行结构（`question`、`ground_truth` 为原测试集内容，`trace_id` 为该问题链路追踪 ID，后四列是逐条得分）：

```
index, question, ground_truth, trace_id, faithfulness, answer_relevancy, context_recall, context_precision
```

MD 摘要表格同样带 `trace_id` 列。每个问题在评估时会生成独立的 `eval_<hex>` 格式 trace_id，并注入到该问题期间的所有日志行（loguru patcher 读取 `current_trace_id`），同时作为 Langfuse trace id —— 用 CSV 里的 trace_id 即可在日志 / Langfuse 中回溯这个问题的完整链路（分类 → 改写 → 检索 → 精排 → 生成），定位指标异常的根因（前提：`.env` 的 `LANGFUSE_ENABLE=true` 且 Langfuse 后端可达 —— 关闭时该列无对应 trace）。

**按 trace_id 查日志（含裁判调用事件与失败时的请求级证据）**：

```bash
python -m src.cli.trace_logs eval_19128e15c446                    # 该题的全部日志
python -m src.cli.trace_logs eval_19128e15c446 --grep "judge call" # 只看裁判调用
python -m src.cli.trace_logs eval_19128e15c446 --count             # 只看行数
```

裁判每次尝试记 `judge call` 事件（`metric` / `q_index` / `trace_id` / `attempt` / `seconds` / `ok` / `note`）；**失败时 `note` 为异常链摘要**（`外层类<-根因类: 根因消息`），**不含** `finish_reason` / 输出 token 数 / 原始输出片段。请求级证据（请求 URL、响应状态码 / 体积 / `finish_reason`）由裁判 client 的 event hooks 收集在 `HookEvidence` 桶中，**仅在失败时落日志**（非 2xx 或响应体不可读各一条 warning），成功调用不产生日志行。

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
| `--generate` | 生成测试集模式 |
| `--size` | 生成的 QA 对数，默认取配置 `RAGAS_TEST_SIZE` |
| `--model` | 生成测试集用的 LLM 模型名 |
| `--testset-version` | 指定测试集版本号（默认取最新） |
| `--gate` | 评估后检查质量门禁，不达标退出码 1 |
| `--deepeval-only` | 只跑 Agent 行为轴，跳过 RAGAS 四指标（无主分/门禁、不写 CSV 与 `eval_report`；与 `--gate` 互斥） |
| `--output` | CSV 报告输出路径 |
| `--session-id` | 评估用会话 ID，默认 `ragas_eval_session` |

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

## 数据目录速查（默认 `data/ragas/`）

| 目录 | 内容 |
|---|---|
| `testset/` | 测试集 JSON：`testset_<kb_id>_v<N>.json`，按版本递增 |
| `reports/` | 评估报告：`ragas_eval_<timestamp>.csv`（逐条）+ 同名 `.md`（摘要） |
| `llm_cache/` | LLM 响应磁盘缓存（重复生成提速，支持中断恢复） |

## 典型工作流

1. **生成测试集**：`eval_deepeval --kb-id <uuid> --generate --size 20`
2. **执行评估**：`eval_deepeval --kb-id <uuid>` → 查看 `data/ragas/reports/` 下的 CSV 与 MD
3. **质量门禁**：`eval_deepeval --kb-id <uuid> --gate`（CI / 提交前检查，不达标退出码 1）
4. **参数选优**：`compare_retrieval --kb-name <名称>` → 按指标对比表确定 `TOP_K` 组合
5. **人工抽检**：`check_retrieval --kb <名称> --query "<问题>"` 验证检索效果
