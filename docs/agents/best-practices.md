# 最佳实践基线（业界做法 ↔ 我们的现状）

> **唯一归属**：各能力域「业界最佳实践」与「我们现状」的对照登记。
> 回答两件事：① 设计某能力前，业界成熟做法是什么；② 我们做到哪一步、差距登记在哪。
>
> **三档状态（与 `selection-baseline.md` 同口径）**：**✅ 对齐** / **⚠️ 偏离**（有意或待修，须有理由）/ **❌ 未做**。

## 边界（与其它文档的分工，勿重复）

| 文档 | 回答什么 | 与本份的关系 |
|------|---------|-------------|
| 本份 `best-practices.md` | 业界**应该怎么做** + 我们现状 | —— |
| `selection-baseline.md` | **以谁为基线**（选谁）+ 上下文治理与记忆域的对照表 | 记忆域的**现状对照**归它，本份相应条目只引不抄 |
| `defensive-patterns.md` | **我们自己踩过的坑**（事后防复发） | 互补：那份是教训，本份是业界做法 |
| `rules.md` | 代码**怎么写**（架构规约 / 注释标准） | 不重叠（本份不写编码规约） |
| `docs/research/` | 外部事实的**冻结记录**（多而散的原料） | 本份是原料**提纯后的可参照结论**，不复制原始调研 |
| `requirements_pool.md` | **意向需求池**（待办条目 `A-xx`/`E-xx`/`F-xx`/`H-xx`） | 本份的 ❌/⚠️ 行统一指向池中条目，条目本身不搬 |

## 怎么用

1. **设计 / 评审新能力前**：先查本域有无对应实践点 → 有则**默认对齐**；确有意偏离，须写下「偏离理由 + 接受的代价」（落该变更的 `design.md`，不可逆则落 ADR）。
2. **状态为 ⚠️ / ❌ 的行必须指向一个待办**（需求池条目或 change），不留悬空（同 `selection-baseline.md` 维护规则）。
3. **新增实践点**：按下方模板追加；**出处须可核**——外部做法附 URL 或论文/厂商文档标识，本地实现附 `文件路径` 或 `文件:行号`。

## 模板（每个能力域一张表）

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 一句话点题 | 做法 + 量化效果 + 出处 | 现役实现（文件路径）或「无」+ 差距条目 | ✅ / ⚠️ / ❌ |

---

## 1. 检索前（Pre-Retrieval）

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| Query Rewriting（查询改写） | 用 LLM 把口语化查询转写为检索友好形式（生产命中率 +10~20%）；HyDE 先生成假设回答再检索（zero-shot recall +15~20%） | 改写**已实现**（`src/infra/search/query_router.py` 的 LLM 改写 + 规则兜底 `rewrite_query`），但**只在 CLI 路径**（`src/cli/check_abstain.py`）被调；生产工具 `retrieve_kb`（`src/agents/tools/rag_tools.py`）用原始 query 直检，**不改写** | ❌ 未接线（`F-04`） |
| Query Decomposition（查询分解） | 复杂多面问题拆独立子查询再合并（FinSage，CIKM 2025，Recall 92.51%） | 规则版 `decompose_query`（`src/rag/retrieval.py`）存在，同属未接线路径 | ❌ 未接线（并入 `F-04`） |
| 多变体生成（Multi-HyDE / RAG-Fusion） | 生成多个**不等价**假设查询覆盖不同维度（EMNLP 2025：准确率 +11.2%、幻觉 −15%） | 无 | ❌（`F-09`） |
| Metadata Filtering（元数据过滤） | 检索前按文档类型 / 日期 / 安全级别过滤（Red Hat OGX 三级过滤层） | 时间约束**已实现**（`parse_temporal`，命中时间词才触发）；按 company / doc 过滤**未接入检索路径**（`F-44` 实测跨公司噪声混入 top-5） | ⚠️ 部分（`F-44`） |
| 查询分类路由（Adaptive） | 按复杂度路由：简单→直答、中等→单步 RAG、复杂→多步（平均成本 −30~40%） | `QueryRouter`（`src/infra/search/query_router.py`）生产死、CLI 活（`D-12`）；生产意图判定已并入主 agent 单循环 | ❌（`F-06`） |

## 2. 检索（Retrieval）

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| Hybrid Search（混合检索） | 关键词稀疏路 + 向量稠密路，按 RRF 融合（PwC 2025：金融文档 68% 胜率） | **已落地**：dense（pgvector）+ 词法（PG 全文检索 `tsv` + `ts_rank`，jieba 分词）并发两路，应用层 RRF 融合（`src/rag/retrieval.py` + `src/rag/fusion.py`；`RRF_K=60` / `RRF_TOP_N=50`，开关 `HYBRID_SEARCH_ENABLED`） | ✅ 对齐 |
| Parent-Child 分块（Small-to-Big） | 小块精准检索 + 大块完整上下文（NVIDIA 实测 accuracy 61% → 89%） | **已落地**：`src/chunking/strategies/parent_child.py`；命中 child 后回填 `parent_content` 送 LLM（`rerank_results`） | ✅ 对齐 |
| Semantic Chunking（语义分块） | 按句 / 段边界切分（QASC 2026：+18~27%） | 按**文档类型**自动路由（`src/chunking/router.py`：表格→`table_preserving`、FAQ→`qa`、其余→`parent_child`），但无句界语义切分 | ⚠️ 部分（`E-01`） |
| Multi-Path Retrieval（多路径检索） | 稀疏 + 稠密 + Metadata + HyDE 多路融合（FinSage 92.51% Recall） | 两路（稀疏 + 稠密）已落地；Metadata / HyDE 路未做 | ⚠️ 部分 |
| Contextual Retrieval（上下文检索） | 索引时给每个 chunk 拼 LLM 摘要再 embedding（Anthropic：结合 reranker 减少 67% 检索失败） | 无（chunk 元数据的实体注入属 `E-04`，非文档级摘要） | ❌ |
| 候选量取 50~150 | 精排前取 50~150 候选（PwC：MRR@5 0.16 → 0.75） | `TOP_K_RETRIEVAL=30`（`src/config/settings.py`），低于业界推荐区间 | ⚠️ 偏离（`F-05`；该条目原文写"当前 =10"，与现状 30 不符，以此处为准） |

## 3. 检索后（Post-Retrieval）

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| Cross-Encoder Reranking | 对候选做交叉编码精排后取 top-5~20，**生产必备**（PwC：MRR@5 0.16 → 0.75） | **已落地**：`rerank_results` 调 provider 层 reranker（`build_rerank`），取 `TOP_K_RERANK=5` | ✅ 对齐 |
| Lost-in-the-Middle 防御 | LLM 对 context 中段 recall 最低；最高分 chunk 放 prompt 首尾 | 无显式重排（按分数降序直排） | ❌（`F-05`） |
| Adaptive Context Expansion | 选择性扩展相邻 chunk，而非无脑宽窗口（SCAR 2026：Recall 92.8%、token −27.1%） | parent 回填即粗粒度扩展；无按相关性选择性扩展 | ⚠️ 部分 |
| Self-RAG / Corrective RAG | 自评估检索相关性与充分性，不足则重检 / 联网兜底 | 局部替代：主循环允许模型自行重检索（`retrieve_call_seq` 信号）；无独立自评节点 | ⚠️ 部分（`F-07`） |
| Post-Retrieval Cascade | 最便宜优先，按需才升级到 LLM（Coverage Illusion 2026：72.2% 查询无需 LLM 增强） | 无 | ❌ |
| Context Compression | 压缩检索结果再送 LLM（省 30~50% context token） | 跨轮**历史**摘要已有（见 `selection-baseline.md`）；**检索结果**压缩无 | ❌ |
| 检索结果去重 | 按父块 / 文档去重，避免重复块占位 | **已落地**：`_dedup_by_parent`（`src/rag/retrieval.py`），落 `dedup done` 事件 | ✅ 对齐 |
| 低分不硬凑 top_k | 分数扁平 / 库内无内容时不硬凑 top_k、不返回无关块 | 取前 `TOP_K_RERANK` 且**不过滤绝对分数** ⇒ 实测跨公司块被凑进 top-5（`F-44`） | ❌（`F-44`） |

## 4. 评估与监控

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 三层独立评估 | 检索层（Recall@k / nDCG / MRR）→ 生成层（Faithfulness 等）→ 归因层（ChunkAttribution / Utilization） | 部分：RAGAS 四指标 + 检索对比 CLI（`src/cli/compare_retrieval.py`）；**无归因层** | ⚠️ 部分 |
| RAGAS 四指标不聚合 | Faithfulness / Answer Relevancy / Context Precision / Context Recall 分列，不合并为单一分 | **已落地**：`src/cli/eval_ragas.py` 四指标分列、保留单值 | ✅ 对齐 |
| 生产监控黄金信号 | TTFT / TPOT / 单请求成本 / 守卫命中率 / 隐式反馈 | 部分：Langfuse trace（生成 span + token + 成本）+ `src/cli/symptom_metrics.py`；TTFT / TPOT 未单列指标 | ⚠️ 部分 |
| Span 级追踪 | 每请求展开检索 / 生成 / 守卫 / 路由 span（Langfuse 或 Phoenix） | 部分：Langfuse v2 `@observe` + `src/infra/llm/tool_trace.py`（ADR-0015）覆盖生成与工具；守卫 / 路由 span 无 | ⚠️ 部分 |
| CI/CD 门禁 | Golden set 每次 PR 跑 RAGAS，按阈值卡口（如 faithfulness ≥ 0.90） | **无 CI**（`D-02`）；`--gate` 存在但**不作唯一发布门禁**（见 §5） | ❌ |
| Embedding Drift / Stale Context 检测 | 组件版本化 + MRR 随时间监控 + corpus 版本化（hash + date + chunk count） | 无 corpus 版本化与漂移监控 | ❌ |
| 在线采样评估 | 5~20% 生产流量在线评分（异常请求 100% 采样） | 无（`F-08`） | ❌ |
| Judge 模型校准 | **永远不要信任未校准的 LLM judge**：≥50~100 样本对齐人工、固定 judge 版本、与生成模型异族 | 部分：裁判与选手分离（`get_judge_llm` / `RAGAS_LLM_MODEL`，`temperature=0`）；**未与人工标注校准** | ⚠️ 部分 |

## 5. RAGAS 评估实操

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 评估 LLM temperature=0 | 评估是测量行为；`temperature=0` 保证同一样本多次运行分数一致 | **已落地**：裁判 `temperature=0` 且不传 `top_p`（`src/infra/llm/provider/factory.py`） | ✅ 对齐 |
| 裁判与生产模型异族 | 避免 self-bias（如生产用一族的 chat 模型、评估用另一族） | **已落地**：裁判独立缺省（`dashscope`），与主链路 `LLM_PROVIDER` 解耦 | ✅ 对齐 |
| 黄金测试集（Golden Dataset） | 50~200 条人工标注、固定版本作基线、每季度轮换 10~20% | 部分：有固定测试集（`data/ragas/testset/`）与 `--gate`；**规模与轮换策略未标定** | ⚠️ 部分 |
| 指标解读定位瓶颈 | `context_precision` 低→Top-K 过大或缺重排；`faithfulness` 低→LLM 未受约束；`context_recall` 低→分块不合理；`answer_relevancy` 低→答非所问 | **已落地**：形成实操口径（见 `docs/agents/ragas-eval-ledger.md`、`cookbook.md`） | ✅ 对齐 |
| 不合成单一综合分 | 保留各指标单值，不做 PASS/FAIL 判定；`--gate` 仅作参考 | **已落地**：`--gate` 不作唯一发布门禁 | ✅ 对齐 |
| 结构化输出便于 CI 集成 | 结果以 CSV + Markdown 持久化，便于回归对比 | **已落地**：报告落 `data/ragas/reports` 并写 DB 表 `eval_report`（`src/infra/db/models/eval_report.py`） | ✅ 对齐 |
| 评估时机策略 | 参数改动跑全量黄金集；生产抽样 1~5%；用户反馈触发单条深度评估 | 部分：离线全量已做；生产抽样未做（同 §4 在线采样） | ⚠️ 部分 |

## 6. 文档解析

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 解析决定上限 | 80% RAG 失败源于 ingestion / chunking 层而非 LLM（PremAI / DigitalOcean 2026）；检索决定下限、解析决定上限 | 解析为独立层 `src/parsers/`，PDF 双通道（fitz 表格 + 文本 / 标题树子进程），符合"解析优先"取向 | ✅ 对齐 |
| 表格不跨块切分 | 表格作独立整体处理，绝不跨块；先转 Markdown / JSON 再入库 | **已落地**：表格作独立 block + 跨页表格合并（`CROSS_PAGE_TABLE_MERGE_THRESHOLD`）+ 大表按行切分复制表头（`TABLE_ROW_CHUNK_CHARS`） | ✅ 对齐 |
| 解析失败 ≠ 拒绝入库 | 优先解析器失败后改备用解析器或全文兜底，让更多有效数据入库 | 解析失败即标记 `failed`，无备用解析器兜底 | ❌（`E-09` 相关） |
| 解析质量采样校验 | 乱码率 <5%、块大小分布、最小块 >50 token | **已落地**：入库前 `validate_chunks`（`src/chunking/validator.py`）做块质量校验 | ✅ 对齐 |
| 扫描件 OCR 兜底 | 检测扫描件后启用 OCR（Tesseract 4.x+ / PaddleOCR）；关键文档双引擎交叉校验 | 仅**检测**（`MIN_TEXT_CHARS` 判扫描页），不识别 | ❌（`E-03`） |
| 文档类型专用策略 | PDF→Layout-aware；Markdown→按标题层级；Word→按样式重建树；Excel→按行/区域提取 | 部分：PDF（fitz + pymupdf4llm 标题树）、docx、txt；**无 Excel 解析** | ⚠️ 部分 |

## 7. 表格提取

外部工具对照（2026 年实测金融财报数据，冻结记录）：

| 工具 | 方式 | 复杂表格能力 | 财务报告表现 | 速度 | 成本 |
|------|------|------------|------------|------|------|
| pdfplumber | Python 库 | ⭐⭐⭐⭐ | 高准确率 | 中等 | 免费 |
| PyMuPDF (fitz) | Python 库 | ⭐⭐⭐ | 中等 | 最快 | 免费 |
| Camelot | Python 库 | ⭐⭐ | 复杂报表直接失败 | 中等 | 免费 |
| Tabula | Java 包装 | ⭐⭐ | 格式问题多 | 慢 | 免费 |
| MinerU | 独立服务 | ⭐⭐⭐⭐⭐ | 高（含 OCR + VLM） | 慢 | 免费 + GPU |
| Unstructured | 企业服务 | ⭐⭐⭐⭐ | 好 | 中等 | 免费 / 付费 |
| LLMWhisperer | 云端 API | ⭐⭐⭐⭐⭐ | 最高 | 快 | 付费 |

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 主用 pdfplumber | 财务类复杂表格效果最好，纯代码替换、无需额外部署服务 | **有意偏离**：改用 PyMuPDF（`find_tables()` 提表格并转 Markdown，`src/parsers/pymupdf_parser.py`）——单一依赖同时承担文本 + 表格 + 扫描检测，代价是复杂表格能力弱一档 | ⚠️ 偏离（有理由） |
| 复杂排版 / 扫描件兜底 | pdfplumber 失败时上 MinerU（需 GPU） | 无兜底引擎 | ❌（`E-03`） |
| 数值一致性校验 | 合计 = 分项之和，不一致标记为解析异常 | 无 | ❌ |

## 8. 分块策略

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| Token 计数优于字符计数 | 中文 1 字 ≈ 1~2 token；须接 tokenizer 精确计数 | **偏离**：分块按**字符数**（`CHUNK_SIZE=512` / `CHUNK_OVERLAP=64`，`src/config/settings.py`）；token 计数模块（`src/infra/llm/token_count.py`，tiktoken 近似）只用于历史预算与表格合并上限 | ⚠️ 偏离（`E-05`，P0） |
| chunk_size 甜点 256~512 token | 512-token 分块在多基准中准确率第一 | 512 **字符** ≈ 300~400 token，实际偏小 | ⚠️ 偏离（`E-05`） |
| 策略 = 文档类型 + 查询类型 | 事实查询→偏小；分析推理→偏大；技术文档→按标题层级 | **已落地**：自动路由三类策略（表格 / FAQ / 父子） | ✅ 对齐（局部） |
| 文档类型特异性分块 | 合同→条款级；论文→语义分块；FAQ→固定 + 重叠；代码→late-interaction | 部分：FAQ 路由已做（`qa` 策略）；合同 / 代码 / 多语言未做 | ⚠️ 部分 |
| 分块质量采样校验 | 块大小分布 / 最小块 <50 token / 乱码率 <5% | **已落地** `validate_chunks`；但评分**权重口径存在漂移**（代码 0.45/0.45/0.10 vs 归档 spec 0.40/0.30/0.30，无测试断言） | ⚠️ 部分（`E-10`） |
| Parent-Child 分块 | 生产默认策略；Context Recall <80% 时优先考虑 | **已落地**（默认策略，见 §2） | ✅ 对齐 |
| 语义分块 | 按句 / 段边界而非固定字符数（QASC 2026：+18~27%） | 无句界语义切分 | ❌（`E-01`） |
| Late Chunking（延迟分块） | 先对整个文档 embedding 再按块 pooling（需 jina-embeddings-v3 类专用 embedding） | 无 | ❌ |
| 上下文关联索引 | chunk 前拼 LLM 生成的摘要 / doc 级信息 | 无 | ❌ |

## 9. Agent 架构

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 工具选择前置（Tool Selector） | 调 LLM 前从全量工具过滤出最相关 2~3 个，避免 Prompt 膨胀 | 全量注册（`retrieve_kb` / `ask_user` / `search_web` / `delegate_task`，`src/agents/tools/rag_tools.py`） | ❌（`A-06`） |
| 熔断优先于限流 | 内部系统限流价值有限，熔断（快速失败）比限流（排队等）更适合 AI 场景 | 无熔断、无限流 | ❌（`A-01` / `A-02`） |
| 混合部署（先框架后自研） | MVP 用框架快速验证，核心链路逐步自研 | **已落地**：单循环主 agent（`create_agent` 装配，ADR-0016），工具自研 | ✅ 对齐 |
| MCP 独立部署 | 工具需跨系统复用时封 MCP Server，否则直接 Function Calling 写代码里 | 部分：现为 4 个 native 工具，无 MCP；mcp_server 已独立部署待接（`F-01`） | ⚠️ 部分（`F-01`） |
| Agentic RAG 的前提顺序 | ① 基础检索（Hybrid + Chunking + Rerank）先达标；② 用 adaptive routing；③ 硬约束（循环上限 / 上下文裁剪 / 每步可观测） | ① 三件套已落地（§2）；② adaptive routing 未做（`F-06`）；③ 循环上限与上下文裁剪已做 | ⚠️ 部分 |
| 子代理委派 | 主从委派：主 agent 委派重任务给子代理并回收结果 | **已落地**：`src/agents/skills/`（`delegate_task` → fork 子代理），机制见 ADR-0016 / `glossary.md`「技能委派」 | ✅ 对齐 |
| 记忆提取异步化 | Buffer + 队列 + 批处理（N 条消息固定 3 次 LLM 调用，成本 −40~50%） | 无记忆提取层（`A-07` 条目以"当前同步 inline 提取"描述现状，**与事实不符**——项目中尚无记忆层，见 `docs/agents/memory-model.md`） | ❌（`A-07`） |

## 10. Prompt 管理

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 运行时管理 vs Git 管理 | 终态：Langfuse 统一管 prompt（编辑 / 版本 / A/B / 审计），产品可无 Git 权限操作 | **有意偏离（第一阶段）**：唯一事实源 = Git 内 `src/config/prompts/templates/*.yaml`；Langfuse 远端 3 个 prompt 已出列。远端读取实现保留未删，终态是"加回名单 + 固定 label/版本"（非重写） | ⚠️ 有意偏离（ADR-0010） |
| 避免双轨制 | 不要 Git 也存一份——两份不一致时没人知道哪个是"对的" | **已落地**：prompt 只在 Git YAML，Langfuse 未存副本，无双轨 | ✅ 对齐 |
| 本地兜底只做离线保护 | 代码兜底只应对远端宕机，不参与日常迭代 | 现状无远端数据源（`src/config/prompts/loader.py` 只读 Git），故无兜底需求 | ✅ 对齐（不适用） |
| 段归属与最小性 | prompt 分段、逐条条件注入，段数最小 | **已落地**：五段固定顺序 + 逐条条件判据（`src/rag/prompt.py`；归属见 `docs/agents/prompt-ownership.md`） | ✅ 对齐（本仓自定） |

## 11. 可靠性（限流 / 熔断 / 降级）

防护位置：`用户请求 → 网关限流 → Embedding → 向量库 → Rerank → LLM 生成 → 返回`

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 每层都有超时 | Embedding / 向量库 / Rerank / LLM 均设显式超时（超时是熔断的前置） | 偏离：精排有工具层超时兜底（`RERANK_TIMEOUT=5s`，`src/agents/tools/rag_tools.py`），但 provider 层 `build_chat` / `build_embeddings` / `build_rerank` 超时**零配置**、吃 SDK 默认 ⇒ read 600s 平铺、最坏 1800s（`A-05` 2026-10-15 核实）。**2026-10-16 更新（P1 已落地）**：三个 role 客户端**全部显式**超时（按角色取值、住 `settings`、env 可覆盖），SDK 重试关闭（`max_retries=0`）并补**本层**有界重试 + `model retry` 留痕；**关键坑**：超时必须**同时**以 `request_timeout` 传入，否则被 SDK 的 per-request timeout 覆盖（等于空转）；精排三层超时**必须嵌套**（内 5 秒 × attempts=1 < 外 6 秒），启动期有 fail-fast 断言 | ✅（P1） |
| 限流（API 入口 + LLM 调用） | 入口限流防滥用、LLM 调用限流控成本 | 无 | ❌（`A-01`） |
| 熔断（外部依赖） | Embedding / Rerank / LLM 各自熔断；连续失败 3~5 次打开、5 分钟后半开 | 无 | ❌（`A-02`） |
| 分层降级 | Embedding 不可用→退关键词路；Rerank 不可用→用原始检索结果；主 LLM 不可用→切备用模型或返回原文 | 部分：**精排失败 / 超时已降级**为向量距离排序并记事件（`rerank_results` / `RERANK_TIMEOUT`）；**LLM 失败 → 返回检索原文 + 告知**（**有意不做模型 fallback**，= ADR-0020 的直接应用，见 change `timeout-and-degradation-hardening` D9）；Embedding 失败退词法**待做**。**2026-10-16 更新（P2/P3 已落地）**：① **单路失败降级**（`search()` 的 `gather` 带 `return_exceptions`，任一路失败仍返回另一路，`hybrid done` 增 `dense_status`/`sparse_status` 三态把「失败」与「缺席」分开）；② **降级动作落各调用点** + 一条 `degraded` 事件留痕（`reason` 取自规格取值集合表）；③ **生成任务级 idle 兜底**（`_idle_guard`，两档阈值 × 思考档；档 1 **只记录不中止**） | ✅（P2/P3） |
| 不重试所有错误 | 401 / 403 / 400 与输入校验类不重试；rate limit 等待后重试；timeout 有限重试后走降级 | 部分：有界重试已实现（`with_retry`，`RETRY_MAX_ATTEMPTS=3` + 指数退避），但**未按异常类型分流**——输入校验类仍重试（`A-03` 2026-10-15 实测）；RAGAS 侧已按此修（ADR-0024）。**2026-10-16 更新（P1/P2 已落地）**：抽 `src/infra/retry_policy.py` 做**异常分类**（输入校验类 / 鉴权权限类 / 请求格式类**不重试**；超时 / 连接 / 429 / 5xx 有界重试；未列出的默认不重试），并新增 `classify_failure` 把失败映射到降级原因；`judge` 角色**排除**在本变更之外（其超时/重试真源归评测链，D17） | ✅（P1/P2） |
| 重试用指数退避 + jitter | 初始 100ms、倍数 2、最大 5s，加随机 jitter 防惊群 | 偏离：`RETRY_INITIAL_INTERVAL=1.0s` / `RETRY_BACKOFF_FACTOR=2.0`，**无 jitter**。**2026-10-16 更新（P1 已落地）**：重试原语自带 **±jitter 比例**（`jitter=0` 时退化为确定性值，供测试断言），并提供 **sync / async 两版**（异步版用 `await asyncio.sleep`，不阻塞事件循环） | ✅（P1） |
| 降级后告知用户 | 返回"服务降级中、结果可能不完整"，不返回通用 500 或空结果 | 部分：检索空由 abstain 文案承接；上游**可用性错误**（额度耗尽等）只给**通用**的"连接异常，请重试" —— 对"重试必然再失败"的错误属**误导向**，需按类别给可区分文案。**2026-10-16 更新（P2/P3 已落地）**：降级文案经**既有 `status` 事件 + 专用 stage**送达，取值一律取自规格取值集合表（**不得自造**）；`provider_unavailable` 类**不诱导重试**；**有检索结果时**一律附上**检索原文**（`reason` 记原因、文案记交付产物，二者解耦） | ✅（P2/P3） |
| Chaos Engineering | 测试环境主动注入故障（停 Embedding、让 LLM 超时），验证降级路径生效 | 无 | ❌ |

## 12. 知识图谱

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 先轻量后重量 | 先在 chunk 元数据存实体关联，确认有跨文档关系查询需求后再上图数据库 | **已落地**：实体抽取注入 chunk metadata，检索期由 `src/rag/context.py` 渲染 | ✅ 对齐 |
| 不要一步到位 | 报表问答场景向量检索已足够；图谱收益主要在**多跳推理与关系类问题** | **已落地**：未上图数据库（`F-03` 待前置条件确认） | ✅ 对齐 |
| 异步提取 | 图谱提取走后台异步队列，不阻塞文档上传流程 | 部分：实体抽取在文档处理链路内（LLM 兜底三态 `ENTITY_LLM_FALLBACK`），不在独立异步队列 | ⚠️ 部分 |

## 13. 记忆与上下文

> 记忆域的**现状对照**唯一归属 `docs/agents/selection-baseline.md`（对齐状态表），本份只保留其**业界做法**条目，不复制对照表。

| 实践点 | 业界最佳实践（出处） | 我们的现状 | 状态 |
|--------|--------------------|-----------|------|
| 分层架构 | 95% 项目采用三层（Working / Episodic / Semantic） | 见 `selection-baseline.md`（短期记忆 = Redis + PG，长期记忆规划中，ADR-0018） | ⚠️ 部分 |
| 提取异步化 | 同步 inline 提取是最常见错误；应 Buffer + 队列 + 批处理 | 无记忆提取层 | ❌（`A-07`） |
| 记忆衰退 | 长期记忆需衰减机制（指数衰减，按认知类型分率） | 无（长期记忆未落地） | ❌ |
| 去重合并 | 写入前做相似度检测，相同实体 / 事实合并而非追加 | 无（长期记忆未落地） | ❌ |
| 重要性过滤 | 设置信度阈值（如 0.7），不是所有对话都值得进长期记忆 | 无（长期记忆未落地） | ❌ |

---

## 维护

- 本表随相关能力域的变更同步更新（新增实践点 / 状态变化 / 新增业界基线做法）。
- 状态为 ⚠️ / ❌ 的行 **SHALL 指向一个已知待办**（需求池条目、change 或 ADR 的「不解决的问题」），不留悬空。
- **出处可核**：业界做法附 URL 或论文 / 厂商文档标识；本地现状附 `文件路径`。**引用本地代码得出的结论优先于转述**。
- 与 `selection-baseline.md` 的边界：本份管"**业界怎么做**"（跨域），那份管"**以谁为基线**"及"上下文治理与记忆域的逐维度对照"；同一事实只在一处维护。
