# retrieval-quality Specification

## Purpose
TBD - created by archiving change mvp-core-features. Update Purpose after archive.
## Requirements
### Requirement: Retrieval parameter configuration
The system SHALL keep `TOP_K_RETRIEVAL` and `TOP_K_RERANK` configurable via environment variables in `src/config/settings.py`, defaulting to 30 and 5 respectively.

The system SHALL support overriding these values at evaluation time without modifying source code.

`TOP_K_RETRIEVAL` SHALL bound each retrieval branch's fetch size (the dense path and the lexical path each fetch up to this many). It SHALL NOT be read as the exact size of the rerank input: on the hybrid path the rerank input is the RRF-fused result, whose size is bounded by `RRF_TOP_N`. The effective number of chunks entering the model SHALL be bounded by `TOP_K_RERANK` downstream.

#### Scenario: Parameter override via environment
- **WHEN** user sets `TOP_K_RETRIEVAL=15` and `TOP_K_RERANK=8` in `.env`
- **THEN** the RAG pipeline SHALL fetch up to 15 results **per retrieval branch** and keep 8 after reranking（15 为**各支路**各取 15；hybrid 路径送入精排的是 RRF 融合结果，其上界为 `RRF_TOP_N`，不等于 15）

#### Scenario: 候选池不构成最终上下文条数
- **WHEN** `TOP_K_RETRIEVAL` 配置大于 `TOP_K_RERANK`
- **THEN** 进入模型的 chunk 条数 SHALL 不超过 `TOP_K_RERANK`；`TOP_K_RETRIEVAL` 只约束各支路取数，**不构成精排输入的上界**（hybrid 路径的精排输入上界为 `RRF_TOP_N`）

### Requirement: Short query handling
The system SHALL handle short queries (under 5 Chinese characters) gracefully by:
- Returning a fallback response indicating the query is too short
- Not performing an empty or meaningless vector search

#### Scenario: Short query returns guidance
- **WHEN** user sends a query of fewer than 5 Chinese characters (e.g., "你好", "是的")
- **THEN** the system SHALL respond with a message suggesting a more specific financial question

### Requirement: Cross-document aggregation
When a query requires information spread across multiple chunks from different documents within the same KB, the RAG chain SHALL aggregate context from up to TOP_K_RERANK chunks regardless of which document they originate from.

The selection of those chunks SHALL be decided by rerank relevance, not by a per-document quota: multiple chunks originating from the same document SHALL remain eligible when they are the most relevant candidates.

#### Scenario: Cross-document query returns aggregated results
- **WHEN** user asks a question whose answer spans multiple documents in the same KB
- **THEN** the response SHALL include information from all relevant documents, with citations tracing back to each source document

#### Scenario: 单文档多片段不被截断
- **WHEN** 一个问题需要同一文档的多个不同片段（如跨章节的指标与说明）
- **THEN** 该文档的多个片段 SHALL 能同时进入精排候选，且不因"每文档配额"被提前丢弃

### Requirement: Retrieval quality comparison
The system SHALL provide a CLI command to compare retrieval quality across different parameter combinations:
- TOP_K_RETRIEVAL: 10, 30, 50
- TOP_K_RERANK: 3, 5, 8

Results SHALL include average relevance score and recall@K metrics per combination.

#### Scenario: Retrieval comparison report
- **WHEN** user runs retrieval comparison CLI
- **THEN** a comparison table SHALL be printed showing metrics per parameter combination

### Requirement: 删除 grader 与重试环

系统 SHALL 移除 grader 关键词覆盖度评分与检索重试环，工作流 SHALL 为 `retrieve → rerank` 直连。不再存在 `grader` 图节点、`route_by_grader` 条件边、`RetrievalGrader` 评分器，以及 `grader_score`/`retrieval_retries`/`downgraded`/`downgrade_reason`/`_prev_rewritten_query` 状态字段。

#### Scenario: 图无 grader 节点
- **WHEN** 编译工作流时检查节点注册
- **THEN** 节点列表不包含 grader，检索结果直接进入 rerank

#### Scenario: 无 grader 状态残留
- **WHEN** 请求完成时检查状态
- **THEN** AgentState 不包含 `grader_score`/`retrieval_retries`/`downgraded` 等字段，且请求只执行一次检索和一次 rerank

### Requirement: abstention 决策路径（全模型决策）

删除 classify 节点与固定流水线后，系统 SHALL 将空检索的 abstention 触发全部改为"模型决策"：`retrieve_kb` 返回空结果（或证据不足）时作为普通工具结果回喂模型，模型 SHALL 基于证据情况自行选择——输出 abstention 文案、调用 `ask_user` 追问、调用 `escalate_to_human` 转人工、调用 `search_web` 联网补充、或基于已有证据作答。不再存在"确定性 abstention 分支"。

`retrieve_kb` SHALL 只检索会话绑定的那一个知识库；不存在按 query 与 KB 元数据相似度自动选库的路径。

#### Scenario: 空检索模型决策
- **WHEN** retrieve_kb 返回空结果
- **THEN** 模型收到空工具结果后自行决定 abstain / 追问 / 转人工 / 联网搜索，不由流水线硬编码判断

#### Scenario: 检索不相关 → 换词再检 → 联网兜底
- **WHEN** retrieve_kb 结果为空或全部明显不相关
- **THEN** 模型 SHALL 提炼核心实体换一种问法再次检索；仍无相关结果时 SHALL 调用 search_web 联网搜索补充，回答先说明"该问题不在当前知识库范围内"；web 搜索关闭或失败时走纯拒答

#### Scenario: 未绑定 KB 时不检索
- **WHEN** 会话未绑定知识库（`kb_id` 为空）
- **THEN** retrieve_kb SHALL 直接返回空结果，不触发跨库检索

### Requirement: Context rendering includes entity metadata

The system SHALL render entity metadata (company / report_period / sec_code / person / currency / report_type) into the retrieval context shown to the LLM, via `RAGContext.to_prompt_text()`. Only entities that exist SHALL be rendered, in the order defined by `ENTITY_RENDER_ORDER`.

The rendered format SHALL be shared between production prompt generation and evaluation judge context, so both see identical context.

#### Scenario: Context with entities
- **WHEN** a retrieved chunk has `entities={company: 东软集团, report_period: 2025年第一季度}` and is included in `format_context`
- **THEN** the resulting prompt context SHALL include both entity fields alongside source/page/content

#### Scenario: Context without entities
- **WHEN** a retrieved chunk has an empty entities dict
- **THEN** the prompt context SHALL contain only source/page/content, matching the previous format exactly

### Requirement: Rerank context passthrough

The system SHALL carry entity metadata from the `chunks` table through the rerank stage into `RAGContext.entities`.

#### Scenario: Entities survive rerank
- **WHEN** a chunk with `company`/`report_period` metadata is returned by rerank
- **THEN** the corresponding `RAGContext` SHALL expose those values via its `entities` dict

### Requirement: 在线检索质量诊断（agent 行为信号）

系统 SHALL 通过 agent 行为信号在线观测检索质量，不引入额外 LLM grader：agent 对检索结果的"不满意行为"（同轮换词重检、绑定 KB 仍转联网、检索后拒答、judge 无支撑、检索空）作为检索质量差的判据。信号 SHALL 仅在会话绑定 KB（检索真实发生）时激活；未绑定 KB 的纯对话不产生检索质量信号（联网为主路径、空检索为设计行为）。信号经统一 helper 以 `retrieval_signal:` 前缀输出，携带 query / iteration / kb_id，trace_id 由日志框架注入。

#### Scenario: 绑定 KB 检索不足转联网产生信号

- **WHEN** 会话绑定 KB，agent 自主经检索降级路径调 search_web（先 retrieve_kb 且结果空/无关，按 prompt 规则换词再检仍无后转联网）
- **THEN** 日志产生 `retrieval_signal: signal=to_web query=... iteration=... kb_id=...`，可按 trace 回放该 query 检索全过程

#### Scenario: verify 完整性补数据联网不产生 to_web 信号

- **WHEN** search_web 由 verify 节点指派（完整性检测缺失年份 → 用户确认联网 → 注入指引驱动，`ctx.web_guided` 已置位）
- **THEN** 不产生 to_web 检索缺陷信号（该路径属知识库数据覆盖不足，非检索质量问题；search_web 读 ctx.web_guided 判定排除）

#### Scenario: 未绑定 KB 联网不产生缺陷信号

- **WHEN** 会话未绑定 KB（纯对话），agent 直接调 search_web 回答
- **THEN** 不产生 to_web / empty_result 检索质量缺陷信号（联网是主路径非降级）

#### Scenario: 纯对话检索空不标缺陷

- **WHEN** 会话未绑定 KB，retrieve_kb 返回空
- **THEN** 不产生 empty_result 信号（未绑定即不检索，空是设计行为）

#### Scenario: 对照基线正常引用

- **WHEN** 答案正常带 [n] 引用
- **THEN** 产生 `signal=cited` 对照基线信号，引用 kind 区分 kb/web

### Requirement: 迁移等价性与词项命中探针

本变更同时发生两件性质不同的事：**存储替换**（不应改变行为）与**词法检索算法替换**（必然改变行为）。因此系统 SHALL 为两路提供**各自可判定**的验收判据，SHALL NOT 让两路共用同一判据。

**dense 路** SHALL 以**迁移等价性**验收：固定查询集（≥20 条，覆盖单知识库的中文 / 数值 / 时间三类），在同一份语料与同一批查询下比对替换前后的 top-k，**重合率 SHALL ≥ 0.9**。验收范围 SHALL 只覆盖单知识库路径。**该查询集 SHALL 落盘为可复现的清单**（不得只存在于任务描述中），否则验收不可复现。

**词法路** SHALL 以**词项命中探针**验收，判据固化如下：

- 探针词项 SHALL 从**原始分块正文**抽取，SHALL NOT 用分词后的检索文本或查询条件自判（那会让同一分词器既造索引又造标签，构成自我循环）
- 词项 SHALL 按文档频率筛选：保留 `2 ≤ df ≤ 0.1 × 语料分块数` 的词项（剔除 `df=1` 的无从判断项与高频的平凡通过项），长度 SHALL ≥ 2 字
- "含该词项的分块" SHALL 以**原始正文的字符串包含**判定，与分词器无关
- 横向比较不同分词配置时 SHALL 固定打分算法，或只比较与打分无关的量（词项可召回率）；SHALL NOT 同时改变分词器与打分算法（无法归因）
- 词项**被重新分词后的形态**与词项本身的差异 SHALL 纳入用例，SHALL NOT 只测"词项恰好等于文档词元"这一类
- 查询条件的**构造方式**（前缀 AND / 前缀 OR / 整词 AND / 原文子串）SHALL 作为与分词器**并列**的变量单独比较，SHALL NOT 与分词器变更同时进行
- 报告 SHALL 包含命中数与词项总数及词项清单，并声明**仅供相对比较**

端到端答案质量（评测指标）**明确不在本变更的验收范围内**，SHALL 登记为语料到位后的独立评估活动。

#### Scenario: dense 迁移等价性可判定

- **WHEN** 对同一份语料、同一批固定查询，分别用替换前后两种存储执行单知识库 dense 检索
- **THEN** 两次 top-k 的重合率 SHALL ≥ 0.9；未达标 SHALL 阻止进入后续步骤，并先排查距离语义与过滤条件

#### Scenario: 词法命中率用于横向选型

- **WHEN** 用同一组词项探针比较候选分词配置
- **THEN** 每种配置的命中率 SHALL 被记录，配置 SHALL 依据实测选定而非推断

#### Scenario: 探针不自我循环

- **WHEN** 检查探针的词项来源与命中判定
- **THEN** 两者 SHALL 都基于原始正文，SHALL NOT 经过被测的分词器

#### Scenario: 探针覆盖被切碎与全单字查询

- **WHEN** 构造探针用例
- **THEN** SHALL 显式包含两类用例并断言词法路 SHALL NOT 静默 0 命中：
  ① **查询经分词后词元全部被滤掉**（如「涨了吗」「5 月」）→ 须走原文子串兜底；
  ② **查询词与文档词元不同形**（如词项「营业收入」/「增长」对应文档词元「营业」+「收入」/「增长率」）→ 须靠前缀通配召回。
  探针自带"长度 ≥ 2"过滤与"原始正文字符串包含"判据，会让这两类**永远测不到**，必须显式补

#### Scenario: 查询构造与分词器分开比较

- **WHEN** 比较候选查询构造方式（前缀 AND / 前缀 OR / 整词 AND / 原文子串）
- **THEN** SHALL 在同一分词器下比较，或只比较与打分无关的量（词项可召回率）；SHALL NOT 与分词器变更同时进行（H2 的修复使查询构造成为第二个独立变量，同改两个变量即无法归因）

#### Scenario: 小语料下不产出绝对质量结论

- **WHEN** 语料规模远小于目标规模（当前 176 个分块 / 5 个知识库）
- **THEN** 探针结果 SHALL 仅用于**相对比较**；SHALL NOT 被当作质量基线或发布判据。该限制 SHALL 在验收记录中显式写出（k=30~50 时单词项命中集合可能已占语料 17%–28%，多数词项会平凡通过）

#### Scenario: 端到端质量评估的遗留被显式登记

- **WHEN** 本变更验收通过
- **THEN** 端到端答案质量的回归判定 SHALL 作为显式遗留项登记，SHALL NOT 被默认为已通过
