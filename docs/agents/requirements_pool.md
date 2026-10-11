# qyznkf 意向需求池

> 这是 qyznkf 项目的意向需求池，对比 financial_rag 产出的潜在改进项。
> 按领域分类，每个需求标注来源、优先级、预估成本、依赖关系。
> 决策流程：从池中取需求 → 审批 → 纳入开发计划。

## 使用方式

1. 从池中选择需求
2. 确认优先级和依赖
3. 审批通过后进入开发

---

## A. 成本控制与可靠性（高价值）

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| A-01 | LLM 限流中间件 | 防止 LLM API 调用超预算 | P0 | 低 | 无 |
| A-02 | 熔断器实现 | 对 LLM API、下游服务实现熔断。**细化**：Embedding/Rerank/LLM 各一个独立熔断器，配置不同阈值。Embedding 失败→跳过检索告知用户；Rerank 失败→用原始检索结果；LLM 失败→切备用模型或返回检索原文。连续失败 3-5 次打开熔断器，5 分钟后尝试半开恢复 | P0 | 低 | 无 |
| A-03 | **降级策略（细化）** | LLM 不可用时切到备用模型（如 Qwen-max → Qwen-turbo）；Rerank 不可用时跳过 rerank，直接用检索结果；Embedding 不可用时退化为 BM25 关键词检索（需先建倒排索引）；Langfuse 不可用时用本地 prompt 兜底。**降级矩阵**：Embedding 失败→"检索服务暂不可用"；向量库失败→"知识库暂不可用"；Rerank 失败→用原始检索顺序；LLM 失败→切备用模型或返回原文。**原则**：每个外部依赖都要有明确的降级路径，降级后告知用户"服务降级中"。不重试 401/403/400；rate limit 等待后重试；timeout 重试 1-2 次后走降级。指数退避加随机 jitter 防止惊群效应 | **P0** | 中 | A-02 |
| A-04 | Token 用量追踪与告警 | 记录每次 LLM 调用的 token 消耗，超出阈值告警 | P1 | 低 | 无 |
| A-05 | 请求超时与重试策略优化 | 对 LLM/Embedding/Reranker 调用的指数退避重试参数化 | P1 | 低 | 无 |
| A-06 | **工具调用前优化（Tool Selector）** | 调用 LLM 前从全量工具中过滤出最相关的 2-3 个再注入 Prompt，解决 Prompt 膨胀和 Token 浪费。**实现方案**：三阶段过滤——①规则优先：关键词/正则精准命中；②向量化兜底：MCP Client 启动时对所有工具 name+description 做 embedding 缓存，运行时对用户查询做 embedding 检索 Top-K；③小模型分类最终兜底。只把命中的工具注入 Prompt。适用于 Function Calling 和 MCP 两种方式 | P1 | 中 | 无 |
| A-07 | **记忆提取异步化** | 当前记忆提取（facts/preferences/corrections）是同步 inline 调 LLM，改为异步队列。对话完成后将提取任务发到 Redis List，后台 Worker 攒够阈值后批量提取并写入 SemanticMemory。**收益**：用户不感知提取延迟；批处理节省 40-50% LLM 调用成本；提取失败不影响主流程 | P1 | 低 | 无 |
| A-08 | **Langfuse 升级到 v3.x** | 当前使用 v2.95（2025.11），目标升级到 v3.x。**收益**：获得 LLM-as-Judge 评估器（代码化管理评估 prompt）、Code evaluators（Python/TS 写自动化评估）、CI/CD 集成（GitHub Actions 自动跑评估）、Experiments 实验管理。**风险**：数据库 schema 变更需迁移，自部署要规划停机窗口 | P2 | 中 | 无 |
| A-09 | **引入传统小模型（L1）** | 项目中当前没有使用 L1 级别模型（BERT 分类器/NER），纯靠规则 + L2 Embedding + L3 LLM。补充 L1 模型用于低成本的文本分类、实体提取、意图路由等场景。**收益**：替代部分 LLM 调用，降低成本；比规则更灵活。**选型建议**：BERT-base 级别的文本分类器 / NER 模型（~100MB，CPU 可跑，10-50ms/次） | P2 | 低 | 无 |

## B. 认证与安全

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| B-01 | JWT 认证 | 登录注册 + Token 鉴权，替代无认证状态 | P0 | 中 | 无 |
| B-02 | 租户隔离 | 知识库/文档/会话按租户隔离 | P0 | 高 | B-01 |
| B-03 | CORS 加固 | `allow_origins=["*"]` 改为白名单 | P1 | 低 | 无 |
| B-04 | API 请求参数校验 | 引入 Pydantic 参数校验，替换手写校验 | P1 | 低 | 无 |
| B-05 | 操作审计日志 | 记录关键操作（上传、删除、查询） | P2 | 中 | 无 |
| B-06 | **系统安全与审计（后期整体考虑）** | 上线后期统一评估。**审计**：全链路审计（LLM 调用/检索/生成 span 记录）、审计日志保留策略与合规留存（如 180 天）、用户操作可追溯；**安全**：Prompt Injection 防护（用户输入注入检测）、敏感信息脱敏（文档/回答中的身份证号/账号等）、知识库与文档级访问控制（结合 B-02 租户隔离）、模型输出内容安全过滤（有害/违规内容拦截）。**原则**：审计与安全能力应在核心链路稳定后补齐，避免早期过度设计；对外暴露前至少完成操作审计（B-05）与敏感信息脱敏 | P2 | 高 | B-01, B-02, B-05 |

## C. 数据库与持久层

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| C-01 | SQLAlchemy ORM | 替换手写 PyMySQL，减少 SQL 注入风险 | P1 | 中 | 无 |
| C-02 | Alembic 数据库迁移 | 自动化 schema 版本管理，支持回滚。**现状核实（2026-09-21）**：迁移链**已归一** —— 仅根 `alembic/versions/0001_pg_baseline.py` 一条（从零建 8 张表），`alembic.ini` 的 `script_location` 指向根 `alembic/`；此前分叉的 `src/infra/db/mysql_db/alembic/versions/`（3 个版本，不在生效链上）已随 P4 归并删除。原并存的两个手工 SQL（`scripts/migrations/`）也已在 ADR-0008（2026-09-21）删除：MySQL 方言、执行命令指向已退役容器，且改动已被 baseline 覆盖 | P1 | 中 | 无 |
| C-03 | 数据库连接池 | 生产环境连接池管理 | P1 | 低 | C-01 |
| C-04 | 查询语句集中管理 | 目前 SQL 分散在各模块 | P1 | 低 | 无 |

## D. 代码质量与工程化

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| D-01 | Python type hints 全覆盖 | 当前几乎无类型标注 | P1 | 中 | 无 |
| D-02 | CI/CD 流水线 | GitHub Actions 自动运行测试 + lint | P1 | 低 | 无 |
| D-03 | 服务分层重构 | 引入 repository 模式，拆分 service 层 | P2 | 高 | 无 |
| D-04 | 配置管理规范化 | 环境变量集中管理，不散落在代码中 | P1 | 低 | 无 |
| D-05 | 日志结构化 | 统一日志格式，支持 request_id 串联 | P1 | 低 | 无 |
| D-06 | DDD 领域驱动重写业务层 | 按限界上下文拆分模块（知识管理/对话/检索），引入聚合根和领域事件 | P3 | 高 | D-03 |
| D-07 | **dev 库迁移无人应用、也无版本守卫** | `alembic heads` = `0002`，而 dev 库长期停在 `0001`（`0002_kb_domain.py` 2026-09-21 就已写好、从未 apply）。容器**不跑**迁移（`/app` 下没有 `alembic/`，迁移是宿主侧手工操作），也没有任何启动期检查 ⇒ 2026-09-22 并行会话重建 app 容器后，新代码 `SELECT knowledge_base.domain` 而列不存在，`/api/kbs/list` 直接 500、知识库页整页不可用（旧容器已跑 41 小时、进程内仍是旧代码，故此前一直"看起来正常"）。**已处置**：宿主侧 `alembic upgrade head` → `0002`。**建议**：加一条启动/部署期 `alembic current == heads` 的守卫（或容器 entrypoint 自动 upgrade），让"代码前进了、库没迁移"当场暴露，而不是等下一次重建容器 | 2026-09-22 恢复「详情」缺陷验证时发现 | P1 | 低 | 无 |
| D-08 | **文档锚点闸门自身的两处边界缺陷**（本次有意保留、待修） | ① **`UnicodeDecodeError` 不捕获 → 可挡死全员提交**：`src/cli/check_docs.py` 的代码快照只 `except OSError`，而 `UnicodeDecodeError` 是 `ValueError` 子类。该钩子 `always_run`、每次提交全量跑 ⇒ `src/` 下出现**任一**非 UTF-8 的 `.py` 就让**每次**提交崩掉。**注意触发面**：改前"命中即短路"（可能根本读不到坏文件），改成单次快照后**必读全树** ⇒ 触发面被**扩大**（存在坏文件时每次符号查询都抛）。修法约 1 行（`except (OSError, UnicodeDecodeError): continue`）；本次不改是因为它会破坏 change `check-docs-symbol-lookup-perf` 的"改前/改后 0 差异"等价性证明。② **`#` 截断 → 只产生 warn 噪声**：`line.split("#", 1)[0]` 把**字符串字面量里的 `#`** 也当注释起点、该行后半被丢弃 ⇒ 若某符号只出现在其后，会被误报为"未检索到"（失败方向单一，只能 `True → False`，不影响退出码）。缺陷类别已登记 `docs/agents/defensive-patterns.md`「开发期闸门（pre-commit）」 | 2026-09-23 check_docs 提速时发现（⑥ 代码评审指出 `(b)` 的触发面被扩大，原"保留现状"表述不准确） | P2 | 低 | 无 |

| D-09 | **四个已核实的死字段**：`AgentState.timings` / `_token_usage` / `model_used` / `is_fallback` | 四字段在 `src/agents/graph/state.py:43/44/49/52` **只有声明、全仓零读写**（`tests/infra/llm/test_token_usage.py` 测的是另一个模块 `token_usage`，与本字段无关；`agent_service` 里的 `model_used` / `is_fallback` 是 `_StreamCapture` 的**同名属性**，与 state 字段无关）。核实于 2026-09-28（`one-loop-two-roles` 立项调研，当日复核时把清单由两个修正为四个）；`one-loop-two-roles` 已落地（2026-09-29）但**有意未清理**该四字段（手术刀原则），故仍单列 | `one-loop-two-roles` 立项调研 + 复核 | P3 | 低 | 无 |
| D-10 | **`is_fallback` 恒为 `False`（既有失真）**：`agent_service.py:766` 构造 `SSEModelInfoEvent` 时**硬编码** `is_fallback=False`，前端「回退模型」标示永远不会出现 | 与 D-09 的 `AgentState.is_fallback` 是两个不同位置；此处是**事件构造处写死**。需先确认产品是否要暴露 fallback 标示，再决定是补齐判定还是删字段 | 2026-09-28 `one-loop-two-roles` 死字段复核时发现 | P3 | 低 | 无 |
| D-11 | **临时取证埋点清理待办**：「长时间静默」定位用的 4 组 `[retrieval]/[agent] TIMING` 埋点尚未清理 | 分组：① `src/agents/graph/agent_node.py`（TTFB 捕获 + `TIMING` 日志）**已随 `one-loop-two-roles` 删除**（2026-09-29，埋点连同承载它的旧循环函数一并消失；原计划保留的 `first_chunk_at` 因观测层不可得而未保留，`completion_start_time` 退化见 `docs/adr/0016`）；② `src/agents/tools/web_tools.py`（2 处）；③ `src/infra/search/tavily_client.py`（4 处）；④ `src/services/agent_service.py`（静默看门狗）。②③④ **不在 `one-loop-two-roles` 范围**，待定位确认后整块删除 | 2026-09-28 `one-loop-two-roles` 迁移影响面审计 | P3 | 低 | 无 |
| D-12 | **`QueryRouter` 处于「生产死、CLI 活」灰区**：`src/infra/search/query_router.py` 的 `QueryRouter` 类 + `tests/infra/search/test_query_router.py` 目前**只有 CLI** `src/cli/check_abstain.py:160` 在用；生产图内**无** `classify`/`query_router` 节点（`grep classify_node src/agents/graph/` 无结果，one-loop-two-roles 重构后意图判定已并入主 agent）。⚠️ 同模块的 `aggregate_kb_entities` / `SUGGESTIONS_MAP` **仍被生产使用**（`rag_tools.py:31` / `ask_tools.py:21`），删整个文件会误伤。**处理**：不擅自删，先登记；后续若碰到意图路由/分类相关的 change，评估是否把 `QueryRouter` 并入或退役。相关陈旧引用（`models.py:181` docstring、`state.py:92` 注释）已由 `provider-compat-layer` 修正 | 2026-10-11 `provider-compat-layer` 立项调研（核查 `classify` 角色用途时发现） | P3 | 低 | 无 |

## E. 文档处理增强

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| E-01 | 专用财务文档 chunker | 对财报、招股书等优化分块策略 | P2 | 低 | 无 |
| E-02 | **表格结构化输出** | 当前表格解析为纯文本，行列关系丢失。改为解析为 Markdown 表格格式（保留行列结构）或结构化 JSON（数值型报表），并确保不跨块切分表格。**收益**：LLM 能正确理解表格行列关系，数值检索和计算更准确 | P2 | 低 | 无 |
| E-03 | OCR 支持 | 扫描版 PDF 的 OCR 识别 | P3 | 高 | 无 |
| E-04 | **轻量实体关联** | 文档入库时用规则+LLM提取实体（公司名、人名、指标），存入 chunk 元数据。检索时从候选 chunk 提取实体→去重→拼到上下文中。不依赖 Neo4j，利用 MySQL 或元数据字段即可。**适用边界**：跨文档实体关联查询（如"这家公司对外投资了哪些子公司"）；不支持多跳图遍历，不支持关系链推理 | P2 | 中 | 无 |
| E-05 | **Token 计数分块** | 当前 CHUNK_SIZE 按字符数设置，改为按 Token 数（接入 DashScope tokenizer）。中文 512 字符约 300-400 tokens，导致 chunk 偏小。与嵌入模型对齐后分块更准确 | **P0** | 低 | 无 |
| E-06 | **分块质量自动化监控** | 入库前嵌入分块质量检查：统计块大小分布、最小块 <50 tokens 告警、乱码率 <5% 检测。当前 check_chunks.py 是 CLI 手动运行，需改为入库自动触发，碎片块自动过滤 | **P0** | 低 | 无 |
| E-07 | **Hybrid Search（BM25 + Dense 混合检索）** | 当前纯 Dense 语义检索，数值/时间/代码类精确查询匹配差。增加 BM25 稀疏检索，双路结果通过 RRF (k=60) 融合。轻量方案：rank_bm25 纯 Python 库；进阶方案：Qdrant 原生 sparse+dense。**业界 2026 年共识：Hybrid Search 是生产必备** | **P0** | 中 | 无 |
| E-08 | **Parent-Child 分块策略** | 当前单一粒度分块（512 chars）。改为 child chunks（256 tokens）精准检索 + parent chunks（1024 tokens）完整上下文。child 命中时同时返回 parent 作为 LLM 上下文。**2026 年已成为生产默认策略**，PwC 论文验证 65% 胜率、+0.2s 额外延迟 | **P0** | 中 | E-05 |
| E-09 | **文档后台处理重试机制** | 当前 `asyncio.create_task()` fire-and-forget，向量化失败（DashScope 超时/PostgreSQL 写入失败）直接标记 `failed`，无重试。增加指数退避重试（初始 100ms，倍数 2，最大 5s，加随机 jitter），最多重试 2-3 次后标记 `failed`。**不重试**：解析失败（文件损坏）、扫描件检测（不可恢复）。**只重试**：网络超时、API 限流（429）、向量库临时不可用等可恢复错误 | P1 | 低 | 无 |
| E-10 | **分块质量评估的口径漂移与规格缺口** | ① **权重不一致**：归档 spec 写 `0.40 × structure_integrity.score + 0.30 × sbr.score + 0.30 × granularity_cv.score`（`docs/openspec/changes/archive/2026-07-17-retrieval-and-chunking/chunk-evaluation-system/specs/chunk-quality-scorer/spec.md:83`），代码是 `0.45 / 0.45 / 0.10`（`src/chunking/scorer.py:410-414`），且**无任何测试断言权重** —— 改哪边都会改变 `overall_score` 数值、进而影响历史「分块评分」的可比性，须先裁定以哪边为准。② **主规格缺失**：`docs/openspec/specs/` 下**没有** `chunk-quality-scorer`，该 change 归档时未把 delta 同步进主规格，所以三个模块的字段形状至今只存在于归档里（`eval_detail` 的实际形状已补进 `docs/agents/api_contract.md` §2.2.1，但那是接口契约、不是能力规格） | 2026-09-22 排查「详情」弹窗缺陷时顺带发现 | P2 | 低 | 无 |

## F. AI / Agent 能力

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| F-01 | MCP 工具集成 | 接入 `mcp_server`，获取财务/税务/法律工具能力 | P1 | 低 | 无（mcp_server 已独立部署） |
| F-02 | LangGraph Agentic RAG | 从单链 RAG 升级为 Agent 编排 | P2 | 高 | F-01 |
| F-03 | **知识图谱（Neo4j）** | 搭建完整的 GraphRAG：实体提取→关系提取→ Neo4j 存储→图遍历检索。**适用边界**：需要多跳推理的场景（如"这个条款违反哪条法规""该政策影响哪些企业""同一控制下的关联方有哪些"），支持深度2-3的图遍历。**不做**：高频实时数据查询、纯数值指标查询。前置条件：需要业务中出现足量的跨文档关系查询需求，否则投入产出比不高 | P3 | 高 | E-04 |
| F-04 | **Query Rewriting（查询改写）** | 对用户原始提问改写为检索友好形式，在向量检索前增加改写节点。**改写策略按查询特征而非字数决定**：模糊短查询（<5 有效词，如"净利润"）→从对话历史/默认上下文做语义扩展；口语化长查询（如"帮我看看去年那个芯片公司赚了多少"）→语义凝练提取核心实体（"2023年 芯片公司 净利润"）；复合查询（含多个条件/对比）→查询分解为独立子查询分别检索；表述清晰的查询直接原词检索，免去改写开销。**业界效果**：MedQA +5.9% 准确率（HCQR），FinSage +24% 准确率 | **P0** | 低 | 无 |
| F-05 | **Cross-Encoder Reranker 参数调优** | 当前 TOP_K_RETRIEVAL=10, TOP_K_RERANK=5。调整：TOP_K_RETRIEVAL=**50~150**（HNSW 索引亚线性增长，扩大候选几乎不增加耗时），TOP_K_RERANK=5（Cross-Encoder 精度极高）。PwC 论文验证：候选从 10→50 后 MRR@5: 0.16→0.75。**同时注意 Lost-in-the-Middle 效应**：LLM 对 context 中间部分关注度最低，最高分 chunk 应放在 prompt 首尾位置，不要盲目增大 TOP_K_RERANK | P1 | 低 | 无 |
| F-06 | **查询分类路由（Adaptive Retrieval）** | 按查询复杂度路由：简单→直接 LLM 回答（5-10×更快）；中等→单步 RAG；复杂→多步 RAG。用规则或小模型分类器区分。**收益**：降低 30-40% 平均成本，因为大部分查询是简单的 | P1 | 低 | 无 |
| F-07 | **Retrieval 质量自评估（Self-RAG 轻量版）** | Rerank 后增加一步：用 LLM 检查检索结果是否覆盖了用户问题的所有方面。发现缺失则触发第二轮改写+检索。**边界**：最多 2 轮，防止无限循环。2026 消融研究：2 轮已捕获 95% 的 5 轮增益 | P2 | 中 | F-04 |
| F-08 | **在线 RAGAS 采样评估** | 当前 RAGAS 仅离线 CLI 运行。改为生产流量采样 5-10%，通过 Langfuse 记录后定时批量跑 RAGAS 评分，监控线上质量退化，触发告警 | P2 | 低 | 无 |
| F-09 | **双路径检索升级为多变体生成** | 当前改写采用"约束补全"（medium 单条 standalone_query / complex 子查询分解）+ 双路径检索（改写 query + 原 query，RRF 融合），解决多轮省略缺实体与表述鸿沟。**升级方向**：在约束补全基础上叠加多变体生成（MultiQueryRetriever / RAG-Fusion 模式）——对补全后的完整查询生成 N 个同义变体，连同原 query（include_original）做 N+1 路检索 + RRF 融合。**触发条件**：用户问法口语化多样（"腾讯赚了多少" vs "腾讯盈利如何"）导致措辞差异检索 miss 时启用。**注意**：双路径是"多路径"的 2 路特例，多变体上线后双路径被泛化吞并（原 query 兜底由 include_original 保留），无需两套逻辑并存；"打分用原 query"策略与多路径正交，保留不变 | P2 | 中 | F-04 |
| F-10 | **检索止损：判据接控制流** | change `retrieval-fetch-and-dedup` 只产出"库内有无内容"的**判据结论**（`rerank` 分数的双形态：库内有 → 双峰断层，如 top1=0.83；库内无 → 平滑无拐点，如 top1=0.20~0.29），**不接控制流**。本条承接该结论，决定：① **拦截层**（工具内算证据 / 路由层决策 / 循环外节点）；② **动作**（告诉模型 / 路由短路 / 问用户 / 直接联网）。⚠ **动作不应放工具内** —— `retrieve_kb` 在 ToolNode 里并行执行，多个协程同时 ask 会撞单槽保护（`src/agents/graph/verify/ask_confirm.py:34`）。目标：解决"确实没数据时耗尽 5 轮迭代才问是否联网"（`trace_c54ce259`）。**触发条件**：change `retrieval-fetch-and-dedup` 的第 5 节产出判据结论后 | P1 | 中 | change `retrieval-fetch-and-dedup` |
| F-11 | **skill 注入的生效边界与预算** | 2026-09-18 实测发现两个问题，均**未修**：① **注入消息被历史截断静默丢弃** —— `_truncate_history`（`src/agents/graph/agent_node.py:30-59`）在**抽取注入消息之前**执行（`:79` 截断 → `:91-95` 抽取），参数 `HISTORY_MAX_TURNS=10`、预算 `8000 × 0.3 = 2400 token`，故会话超 10 轮后注入的 skill 正文作为"最旧的"被丢弃，且**无任何日志**；而产品语义承诺"触发粒度=消息级、生效范围=会话级"（`skill-invocation` spec）。② **一条 inline skill 占掉 60%+ 历史预算**（实测 `financial-statement-analyzer` 注入约 3002 字符 ≈ 1500 token），挤压普通历史并**加速自身被截**（负反馈）。**根因**：系统**没有上下文压缩机制**（`_truncate_history` 是丢弃，不是压缩；WeKnora 的压缩是独立包 `agent/compaction/`）。**候选方向**：把注入消息排除在历史预算外 / 提高预算 / 引入真压缩。**触发条件**：出现"长会话中 skill 失效"的实际反馈，或做上下文压缩时一并处理 | P2 | 中 | 无（独立缺陷） |
| F-12 | **skill 渐进披露（目录 + 按需读）** | 当前 skill 正文只能**全量注入**（隐藏 user 消息）或不注入，缺中间态。WeKnora 的 `skills` 段是 **Level 1 渐进披露**：system prompt 只放 `<skill name path><description>` 目录（description 截 600 字符），模型用 `read_file(skill://<name>/SKILL.md)` 按需拉正文。**我们抄不了的原因**：该段有**硬前置** —— `internal/agent/prompts.go:459` 仅在 `read_file` 已注册时才输出 skills 段；而本项目的工具只有 `retrieve_kb` / `ask_user` / `search_web` / `delegate_task`，**没有任何文件读取类工具**。**结论：缺的不是 prompt 里的一个段，而是"按需读 skill 正文"的能力**（涉及文件访问面 / 路径白名单 / 权限边界，属新工具范畴）。**触发条件**：出现"skill 数量增长到全量注入不可接受"或"需要外部 skill 源"时一并设计 | P2 | 高 | 无（独立能力） |
| F-13 | **inline skill 超限：`financial-statement-analyzer` 应为 fork** | 2026-09-18 实测（删 `finance-qa` 时被 `test_all_inline_skills_within_budget` 暴露，此前测试只检查 `finance-qa` 498 字符恰好通过）：`financial-statement-analyzer` 的 `frontmatter` **未声明 `context`** → 默认 `inline`，其 `inline_prompt` = **3002 字符**，而 `INLINE_PROMPT_MAX_CHARS = 500`（`src/config/const.py:94`）—— **超限 6 倍**。按 `skill-invocation` spec 的规则"长文领域内容应建模为 **fork** skill（独立子代理上下文，不占主对话）"，它应为 `context: fork`。**后果**（与 F-11 同源）：3002 字符 ≈ 1500 token，占掉 `_truncate_history` 2400 token 历史预算的 **62%**，挤压普通对话历史并加速该 skill 自身被截。**修正方向二选一**：① 改该 skill 的 `context: fork`（符合 spec 规则，且顺带解决 F-11 的预算问题）；② 调高 `INLINE_PROMPT_MAX_CHARS` 并同步改 spec 的"建议 ≤500 字"——**不推荐**，spec 的理由（防上下文累积膨胀）正是成立的那个。**已修（2026-09-18 取方向 ①）**：`context` 改为 `fork`，`description` 补"材料须由主 agent 预检索一并传入 task"（与 `finance-analyst` 同形态）；测试去 `strict xfail`，改为遍历全部 fork skill，`test_all_inline_skills_within_budget` 保留为守卫。**遗留**：正文引用的 `references/analysis-methodology.md` 与 `references/output-template.md`（共 13.6KB）在本 harness 无文件读取工具，子代理读不到 —— 归 F-12（渐进披露）。**收口（2026-09-28，`skill-execution-and-delegation` P1）**：`context` 默认值已由该 P1 改为 **inline**，长文由加载期超限（正文超 `INLINE_PROMPT_MAX_CHARS`）自动改 fork 承载，**不再依赖作者手写 `context: fork`** | 已修 | — | F-11 |
| F-14 | **spec 措辞与硬约束不一致**：`skill-invocation` spec 写"inline_prompt 控制在短方法论规模（**建议** ≤500 字）"，用"建议"；而 `INLINE_PROMPT_MAX_CHARS = 500` 是**硬**阈值，测试按硬约束断言。二者措辞与语义不一致，导致 `financial-statement-analyzer` 长期超限而无人处置 | **处置决定**：spec 的"建议 ≤500 字"改为"**SHALL ≤ 500 字符**"（与测试的硬断言一致；单位按字符计数，非"字"）。**落点**：`skill-external-sources` change（其 delta 已覆盖 `skill-registry`），不直接改在效 spec。F-13 修复后已无实际违反，本项降为纯文档一致性 | P3 | 低 | F-13 |
| F-15 | **【已失效，组件已退役】BM25 检索每次调用全量重载索引**：`BM25Index.search`（`src/infra/search/bm25_index.py:86-90`）在**每次检索调用**都读文件并重建内存结构，无进程内缓存。2026-09-18 容器内实测（`b9e74e82…`，51 chunk / 内容 31881 字符 / pkl 236KB）：`pickle.load` = **58.2 ms**，`BM25Okapi` 从原文重建 = 5.6 ms，`get_scores` = 0.2 ms。`retrieve_kb` 单请求最多调用 5 次 → 单请求固定开销约 0.3 s，且随 chunk 数增长（按千文档 × 25–40 chunk/doc 估算，单次加载会到秒级）。这是**既有缺陷**，`bm25-index-durability` 不制造也不修（它改 JSON 后实测降到 5.6 ms 级，属顺带改善） | **已失效**（P3/P4）：`bm25_index.py` 与 `rank_bm25` 均已退役，词法路改走 PostgreSQL 全文检索，本项不再适用 | P2 | 中 | 无 |
| F-16 | **（L1+L2）RDS 托管化切换**：把 PostgreSQL 从本地实例切到 RDS 托管实例。清单：① 确认 RDS 侧扩展清单与 `CREATE EXTENSION` 的账号/权限（本地已实测 `vector` **不是** trusted 扩展，应用账号会被拒、须超级用户建，托管侧须先确认同项权限）；② 在 RDS 上预建应用库与最小权限账号；③ 改 `docker-compose.prod.yml` 指向 RDS；④ prod 安装与部署验证。**背景**：`postgres-storage-consolidation` 的 P1（2026-09-19 用户决定）本轮只做本地，远程 RDS 不处理、prod 不安装 | postgres-storage-consolidation P1 遗留项 L1+L2 | P1 | 中 | postgres-storage-consolidation（P1 已完成） |
| F-17 | **【已修，2026-09-23】prod `--workers 4` 与「生产单 worker」规则冲突** —— 原状：`docker-compose.prod.yml` 的 `app.command` 用 `--workers 4`，与 `CLAUDE.md`「生产环境单 worker（流式生成状态在进程内）」及 `streaming-decouple` 早已作出的设计决议（改 4→1）冲突。**已修**：prod `app.command` 去掉 `--workers`（单 worker）并对齐 `--timeout-graceful-shutdown 30`；同日顺带修掉 `Dockerfile` 的 `CMD` 指向不存在的 `src.api.main:app`（改为 `src.main:app`）。**连带效果**：连接预算由 4 × (pool_size 10 + max_overflow 10) ≈ **80 降为 20**，放宽 F-16 对 RDS `max_connections` 的约束（ADR-0004「连接数是共享预算」的结论仍成立，仅量级下降） | postgres-storage-consolidation P1 遗留项 L3 | P1 | 低 | F-16（RDS 规格） |
| F-18 | **【已修，P4 收尾】`src/infra/db/mysql_db/` 包改名 → `src/infra/db/repos/`**：P1 之后该包内全是 PostgreSQL repo（chat/document/eval/kb/user），`mysql_db` 包名会误导 `code-map.md` 与后续读者。P1 未做是因为它触及约 20 个 import 点，属纯命名变更、不在已评审的 change 范围内 | **已修**（本次改名提交 `7324bfa`）：`git mv` 迁移 6 个 repo 模块与 `__init__.py`，26 个 `.py` 文件共 42 处 `mysql_db` 引用机械替换为 `repos`；包 `__init__` docstring、`code-map.md`、`README.md` 同步更正 | postgres-storage-consolidation P1 遗留项 L4 | P2 | 低 | 无 |
| F-19 | **【已基本完成，P4 Task 6/7】Chroma 残留清理**：P2 只删了 `chromadb` 的**代码路径**，`pyproject.toml` 的 `chromadb` 依赖、`data/chroma_persist` 数据目录、`deploy/chroma/` 仍在；`scripts/migrate_chroma_to_pg.py` / `scripts/dense_equivalence_check.py` / `scripts/clean_all_data.py` 仍用 Chroma 客户端与 `CHROMA_PERSIST_DIR` / `CHROMA_COLLECTION_PREFIX` | P4 Task 6/7（`8dd145d` / `1dffee8`）已退役 `chromadb` 依赖、`deploy/chroma/`、搬迁/等价性脚本、4 个 `CHROMA_*` 常量、compose 卷/挂载与 dotfiles 键，`clean_all_data.py` 的 Chroma 分支已删；`data/chroma_persist` / `data/chroma` / `data/bm25_index` 三个数据目录已随 P4 Task 11 退役（从磁盘删除） | postgres-storage-consolidation P2 遗留项 | P2 | 中 | postgres-storage-consolidation（P2 已完成） |
| F-20 | **【已修，P4 Task 6】死日志事件 `CHROMA_CLIENT_READY`**：`src/core/log_events.py:89` 的 Event 枚举项与 `src/core/log_event_specs.py:150-151` 的 EventSpec 已无发出点（`_warmup_chromadb` 随 P2 删除），但成对保留。⚠ 删除必须**两处同删** —— `log_events.py` 的 import 期 `_validate_registry` 会因多余 spec 抛 `AssertionError`，只删一处会让 import 直接崩 | **已修**（P4 Task 6，`8dd145d`）：枚举项与 EventSpec **两处同删**，`log_events` import 期校验仍通过（枚举 115 → 114），未只删一处 | postgres-storage-consolidation P2 遗留项 | P2 | 低 | F-19 |
| F-21 | **【已修，P4 Task 6】死配置 `CHROMA_HOST` / `CHROMA_PORT`**：`src/config/settings.py:186-188` 定义后全仓零引用（含测试与脚本）。同文件的 `CHROMA_COLLECTION_PREFIX` / `CHROMA_PERSIST_DIR` **不是**死配置 —— 三个脚本仍依赖（见 F-19） | **已修**（P4 Task 6，`8dd145d`）：四个 `CHROMA_*` 常量全部从 `settings.py` 删除；`.env` / `.env.example` / `.env.template` 的同名键一并清空，守卫 `test_no_chroma_leftovers_in_root_dotfiles` 强制该不变量（`.env` 不受版本控制，属**工作区契约**） | postgres-storage-consolidation P2 遗留项 | P2 | 低 | F-19 |
| F-22 | **（P2 遗留，门禁盲区）`alembic/env.py` 未传 `compare_server_default=True`**：Alembic 该参数默认 `False` → autogenerate **根本不比较**任何表的 `server_default`，**所有表**的默认值漂移都不会被发现（全局盲区，不只 `chunks`）。P2 仅对 `chunks` 的 `source`/`page`/`metadata` 三列用只读断言旁路覆盖（Ruling P2-R11），`alembic/env.py` 本身未改 | 单独变更打开开关；打开后可能一次性暴露既有表的默认值漂移，需先评估影响面再决定修漂移还是调模型。**P4 明确不做，属独立变更**（打开后可能一次性暴露既有表的默认值漂移）——P4 终审把「有归属 P4 的门禁项」显式登记后**结清**，本条不再作为本变更的门禁项（原 F-26 已并入本条） | postgres-storage-consolidation P2 遗留项 | P1 | 中 | 无 |
| F-23 | **【已修，P4 Task 3】`src/api/documents.py:246` 的 api→infra 越层调用**：`api/` 直接调 `svc.vector_store.delete_document(...)`（`AppService` 暴露的 `vector_store` 句柄），绕过 `services/`，违反 `CLAUDE.md`「api/ 不得直接调用 infra/」的规约。P2 只补了 `await`（正确性），未动越层（Ruling P2-R7）——属**既有**结构问题 | **已修**（P4 Task 3）：删除编排已下沉到 `services/`（见 F-25），api 只转调，不再直取 `vector_store` / `_doc_repo` | postgres-storage-consolidation P2 遗留项 | P2 | 低 | 无 |
| F-24 | **（P2 遗留）`upsert_chunks` 的 `ON CONFLICT` 不覆盖主键 `id`**：`ChunkRepo.upsert_chunks` 的 `on_conflict_do_update` 只指定唯一约束 `uq_chunks_kb_doc_idx`，不处理 PK `id` 冲突 → 隐含前提「同一 `doc_id` 不得跨 `kb_id` 出现」（否则 `id = {doc_id}:{index}` 撞 PK 抛 `IntegrityError`）。当前语料不触发（176 chunk / 9 doc_id / 5 kb，搬迁实跑成功） | 在 `upsert_chunks` docstring 注明该前提，或按需补 `ON CONFLICT (id)`；与 P4 的事务化一并考虑 | postgres-storage-consolidation P2 遗留项 | P2 | 低 | 无 |
| F-25 | **【已修，P4 Task 3】双删除路径收编为一条**：API 端点 `src/api/documents.py:244-246` 走「先软删文档 → 再删分块」（旧序）——分块删除失败时文档已软删、分块成**永久孤儿**，且重试因 `ok=False` 不再删分块；而语义正确的 `DocumentService.delete_document`（**先删分块、抛错则不软删**）全仓**无 API 路由调用、是死代码**。P2 把分块删除从「吞异常」改为「传播」后，生产实际走的那条路的失败会暴露成「用户看到 500、文档却已删」。P4 的「入库/删除同事务」应同时收编两条为一条：API 端点改走 service，并去掉 `svc.document._doc_repo` 的私有属性直取（越层面见 F-23） | **已修**（P4 Task 3）：API 端点改走 service，两条删除路径收编为一条（删分块 + 软删文档同事务；403/409 契约见 api_contract.md §2.2.5）。来源：postgres-storage-consolidation P2 终审遗留（Ruling P2-R17） | P1 | 低 | postgres-storage-consolidation（P4） |
| F-27 | **【已修】存量 Chroma 字样清理（docstring 层）**：`src/chunking/validator.py:14`/`:21` 仍有「Chroma id」措辞（本轮修复波只改了 `src/infra/search/bm25_index.py`（P3 已删除）的 `rebuild_from_results` docstring）；随 F-19 的依赖/数据目录清理同批收口。`src/rag/retrieval.py:77` 的存量乱码属另一既有问题，不在此列 | **已随 F-19 收口**：`src/chunking/validator.py` 已无「Chroma id」措辞（P4 Task 6 守卫 `test_no_chroma_leftovers_in_code` 覆盖）。`retrieval.py` 乱码另计 | postgres-storage-consolidation P2 终审遗留（Ruling P2-R17） | P2 | 低 | F-19 |
| F-28 | **【已修，P4 Task 6】`rank_bm25` 依赖未删**：P3 删除了 `bm25_index.py` 与全部装配链，但 `pyproject.toml` 的 `rank_bm25` 仍在（`scripts/lexical_probe.py` 的分组 A 仍用 `BM25Okapi` 做基线对照 —— 那是探针需要，不是生产依赖）。同批还有 `data/bm25_index` 目录（词法路的回滚依据，P3 刻意保留） | **已修**（P4 Task 6，`8dd145d`）：`rank_bm25` 依赖已从 `pyproject.toml` 删除，镜像经 `docker compose build --no-cache app` 重建；探针脚本一并退役。`data/bm25_index` 目录已随 P4 Task 11 退役（从磁盘删除）。来源：postgres-storage-consolidation P3 遗留项 | P3 | 低 | F-19 |
| F-29 | **【已解决，P4 Task 10】`multi-query-retrieval` 的 requirement 正文曾引用已不存在的组件**：原名正文写「`retrieve_node` 并行遍历 `rewritten_queries` 列表逐条执行 dense + 词法两路检索」，但 harness 改造后检索是 `retrieve_kb` 工具 + 单条 query，`grep -rn "rewritten_queries\|retrieve_node" src/` **零命中**；`rrf_fusion_multi` 也随之全仓无生产调用方（仅测试）。该 change 的 delta 会把这句写进在效规格 | **已解决**（P4 Task 10）：delta 已如实化，明确写出 `retrieve_node` / `rewritten_queries` 已不存在、生产链路当前不并行发起多条改写查询，并把 `rrf_fusion_multi` 的真实状态写实（**能力存在**、由 `tests/rag/test_fusion.py` 覆盖，但**全仓无生产调用方**）。归档后该正文即成为在效 `multi-query-retrieval` spec 正文，「delta 把假陈述写进在效规格」的风险消除。剩余的**结构性重写**（把"多查询合并检索"接回生产形态）仍与 `retrieval-fetch-and-dedup` 的重定基一并处理。来源：2026-09-19 P3 执行期发现 | P2 | 中 | retrieval-fetch-and-dedup |
| F-30 | **（P3 遗留）词法路质量的判据缺口**：词项命中探针（`docs/tmp/p3-lexical-probe-2026-09-19.md`）的判据是**集合成员**（"含该词项的 chunk 是否进 top-k"），而过滤臂的候选集恒 ≤ k ⇒ 该口径**结构上测不了排序质量**（C 组 `ts_rank`/`ts_rank_cd` 的零差是必然），未过滤的 BM25 臂的差值**可归因于分词造成的排序差异**，但命中判据是原始正文包含、不含排序质量 ⇒ **不能读作质量差**。因此"生产形态 `jieba-tsrank` 在 A 组最低"这一实测事实**不可归因于分词质量**，"替换有收益/无收益"均未被本探针建立 | 需要 k ≪ 池规模的新判据，或等语料到位后的端到端 RAGAS 评估；在此之前**不得**把探针数字当质量基线。来源：postgres-storage-consolidation P3 遗留项（探针结论） | P2 | 中 | 无 |
| F-31 | **【已修，P4 Task 5】测试污染真实 PG**：`tests/infra/db/test_db.py` 用 `user_id="test-user"` + 每次唯一名**直写真实 PostgreSQL**，**没有任何 teardown** → 每跑一次全量 pytest 就累积约 5 个知识库 / 6 个文档（P3 收尾实测 dev 库 `90|102|176|1`，而语料本应 `5|0|176|1`）。同一类问题还包括 `tests/infra/db/test_chunk_repo*.py` 等真实 PG 测试（它们**有** teardown，可作范式） | **已修**（`d87fa8b`，P4 Task 5）：给真实 PG 测试补 fixture 级 teardown，**跑完全量门禁不再污染 dev 库**（收尾计数恒为 `5|0|176|1`）。原建议的"统一走 `tests/reset_data.py` 的复位 / 写进 `cookbook.md`"未采用 —— teardown 已足够，无需依赖人工复位。⚠ 影响面提示保留：teardown 若被绕过，dev 界面仍会出现 `test-user` 垃圾知识库。来源：2026-09-19 P3 Task 11 验收期发现 | P2 | 低 | 无 |
| F-32 | **（P2 交出，独立缺陷）RAGAS 的 context 类指标在 eval 链路下恒失效**：eval 端到端跑完，6/6 题日志显示 `[cli] question answer done … contexts=0` → `retrieved_contexts` 全空 → `context_precision` / `context_recall` 恒 0、`faithfulness` 无法计算（nan）。**根因**：取上下文只有一条路 —— `agent_finalize` 从 `RequestContext.tool_contexts` 读（`src/agents/graph/agent_node.py:310-316`），而 `RequestContext` 仅由生产入口 `src/api/chat.py:80` 与 fork executor（`src/agents/skills/executor.py:192`）设置；`src/cli/eval_ragas.py` **从不设置** `current_request_ctx` → 检索工具把上下文追加进一个**即弃的空列表**（`src/agents/tools/rag_tools.py:223-229`）。**回归于 2026-08-26**（`request_context.py` 最早提交 `8b4b67c`、`agent_node.py` 该段出自 `5b52e6f`），**早于 `prompt-layering` 的 P2**；8 月历史报告（contexts 非空，实测 10 份 CSV 跨度 347–9140 字符）产生于该基建之前，故与本次不可比。**影响面**：自该日起 eval 链路的质量度量**测不到检索质量** —— 任何"改动是否损害检索"的评估都失去 context 维度（`answer_relevancy` 仍可用，但它不反映 context 是否被取到）。**建议方向**：在 `eval_ragas` 每题调用前设置 `current_request_ctx`（或在 `agent_finalize` 增一条从 state / 消息回落取上下文的路），并补一条 `contexts > 0` 的守卫断言 | 2026-09-22 `prompt-layering` P2 的 T7 度量采集期发现 | P1 | 低 | 无 |
| F-33 | **（P2 遗留）`base` 段缺「允许对已提供材料推理 / 计算 / 汇总 / 翻译」的正向句**：本变更 delta 有 ADDED Requirement「允许基于已检索材料推理计算」（`specs/prompt-composition/spec.md:345`），但落盘的 `base-financial.yaml` / `base-general.yaml` **没有**对应正向句（`grep "推理\|计算\|汇总\|虚构" src/config/prompts/` 只命中 `sources.yaml` 的"不要虚构来源"）。同时 `prompt-mapping.md:119` 写的是"须在 spec 的 **MODIFIED** 要求中显式改写"，而实际落成了 **ADDED**。**结果**：语义反转（删掉"不计算"禁句）在 spec 侧成立、在 prompt 侧只体现为"没有禁令"，未正面声明"允许"。P2 收口时**刻意不改** —— 改模板会让同期采集的 P2 度量失效，改 spec 需独立评估 | 2026-09-22 `prompt-layering` P2 的 T6 复核期发现 | P2 | 低 | 无 |
| F-34 | **（skill 改造回退残留）3 个外部 skill 的 2 项"零工具语义"改造**：2026-09-26 装进来的 `competitive-landscape` / `financial-statement-analyzer` / `market-sizing-analysis` 已于 2026-09-27 回退为**上游原样**（`references/`、`examples/` 已拷回，正文不再内联；description 语言、`version` 字段均还原）。**仅剩 2 项依赖"fork 子代理零工具"语义而保留**：① 三份 description 尾句「材料须由主 agent 预检索一并传入 task」（`registry.py:88` 会把它写进 `delegate_task` 的 schema，是当前唯一能告知主 agent"先检索再委派"的 skill 侧载体）；② 三份均未声明 `allowed-tools`（刻意零工具，被守卫 `tests/agents/skills/test_first_batch_skills.py::test_fork_prompt_must_not_mention_tool_names` 锁死）。**这两项在"fork 子代理工具面默认继承执行者"落地后即失去依据**，须一并移除（① ）与重定（② 连同守卫）。⚠ 附带证据：base 段 `tools.yaml` 的 `tools-delegate` 已写「委派深度分析任务时，先把已检索到的材料随 task 一并传入」，故 ① 与 base 段**语义重叠**，若不想等新 change 也可提前按"一事一档"去重。**2026-09-27 与另一会话（主从 Agent 架构 / 契约通则）约定**：另开 change `fork-tool-face-by-inheritance` 承载这 2 项（排在 F-12 之前，占 ADR-0016）；对方保留 `delegate-task` spec 的「委派工具形态」requirement，我方只改工具面 delta；文件读取工具仍归 F-12，继承工具不覆盖 | 2026-09-27 回退外部 skill 改造时登记 | P3 | 低 | change `fork-tool-face-by-inheritance`（待建；ADR-0016） |
| F-35 | **`context_source` 是否暴露到 `/skills` 面板（待定）** | skill 的 `context` 取值来源当前只写进运行时对象 `SkillRecord`（`context_source` 字段）与加载期 `skill resolved` 日志；`/skills` 面板看不到，「未声明但正文超限、加载期自动改 fork」这一判定对作者不可见。**待定**：是否把 `context_source`（含 `auto_oversize`）暴露到面板，让作者知道自己写的 skill 被自动改了承载方式 | `skill-execution-and-delegation` P1（Task 8 收口时登记） | P3 | 低 | 无 |

| F-36 | **工具经 `InjectedState` 取图状态时注入的是 `dict`（属性访问会崩）——已关闭** | 本仓工具经 `InjectedState` 取图状态：外层自建图 + `ToolNode` 注入 `AgentState` **实例**（属性可用），`create_agent` 注入 **`dict`**（实测：`state_schema=<dataclass>` 与默认 schema **都**注入 dict）⇒ 直接属性访问会 `AttributeError`，被错误回喂吞成普通 `ToolMessage` 错误 ⇒ **检索恒空、澄清恒失败**，图照常跑完、日志看不出异常。**修复历程**：① `rag_tools.py` 的 `kb_id` 由 `skill-execution-and-delegation`（2026-09-28，`8014d64`）先修为「注入状态优先、`current_request_ctx` 回退」；② 余下全部（`ask_tools.py` 的 `query` / 迭代序号 / `kb_id`、`rag_tools.py` 的迭代序号守卫）由 `one-loop-two-roles`（2026-09-29）收敛为**显式形状判定**（`isinstance(state, AgentState)` / `isinstance(state, dict)` 两分支，缺失键记 warning），并含不建 ctx 的 CLI 入口用例。**通道分类（两轮修正后）**：`RequestContext` 里**只有 `kb_id` / `deep_thinking`**、**没有 `query`**；且「字段有 ctx」≠「ctx 存在」——`src/cli/check_abstain.py:127` 与 `eval_ragas.py:129` 只把参数放进**图输入**、不设 ctx；未 seed 的键在子图状态里根本不存在（只传 `messages` 时状态只有 `messages` 一个键，schema 默认值不填充）⇒ `kb_id` / `query` / 迭代序号 / `deep_thinking` **都必须由装配带入图状态**；工具侧的 `ctx` 回退只能作补充 | 2026-09-28 `one-loop-two-roles` 独立架构评审 + 对照探针复现；2026-09-29 修复收口 | P2 | 低 | 无 |

| F-37 | **迭代触顶 → 空回答且状态 `complete`（既有缺陷，用户可见）** | 宽泛问题在已绑 KB 的会话里触发主循环触顶：`iteration limit`(WARNING) → `completeness check answer_len=0` → `format done citations=0`，assistant 行落库 **`status=complete` 但 `content=""`**，前端"正在思考…"**永久停留**。实测会话 `sess_1790296132490_t5k03t`（trace `trace_25ceadc2-…`，2026-09-25 冒烟）；全库 29 条 assistant 中 3 条空 `content` 且 `status` 全是 `complete` ⇒ **`status` 不足以表达"这轮到底有没有产出回答"**。**根因**：触顶时末条是含 `tool_calls` 的 `AIMessage`（正文为空），答案提取得到空串，且**无任何兜底文案**。**归属**：兜底与状态表达的修复归 `agent-round-budget`（其 apply 排在 `one-loop-two-roles` 之后）；`one-loop-two-roles`（2026-09-29 已落地）只负责**把该语义钉住**（`agent-loop-observability` 要求 + 断言"末条含 tool_calls、answer 可为空"），不修。触发条件很日常：宽泛/多轮追问会让模型反复 `reretrieve` 到触顶，非边缘 case | 2026-09-25 前端冒烟；2026-09-28 `one-loop-two-roles` 立项时核对 | P1 | 中 | `agent-round-budget`（在其之后 apply） |
| F-38 | **历史摘要的「按需回读原文」工具（Phase D 候选）** | 跨轮历史摘要（change `cross-turn-history-summary`，2026-10-08）把模型侧上下文压缩为摘要，而**用户回放仍是完整原文**（摘要刻意不落 MySQL）。由此留下能力缺口：当用户引用「你上次说的 X」而 X 已被摘要掉时，模型**无法回读原文**，只能否认或请用户复述（当前仅靠摘要段的让位声明兜底）。**候选方向**：给主 agent 一个「按会话历史检索 / 回读原文」的工具，与 Phase D 的「工具结果外置、可回读」同源。 | `cross-turn-history-summary` design「已闭问题 3」（2026-10-08） | P3 | 中 | Phase D |
| F-39 | **`covered` 是 Phase D「按需回读原文」的切点锚点（约束型需求）** | 跨轮历史摘要写进 Redis 的 `covered` = 「摘要已覆盖到的**消息条数**」（实现为被丢弃段长度，即累计前缀长度）。它今天没有被任何消费者使用（注入只取摘要正文），但 Phase D 的「按需回读原文」工具要拿它当**从第几条开始回读**的切点。⇒ **该字段的语义是上游契约，不得漂移**：任何让 `covered` 与"摘要实际覆盖范围"不一致的改动，都会让回读切错位置。**历史教训（2026-10-08）**：首版误写为 `previous_covered + len(discarded)`（把累计前缀当增量），写进 Redis 的值会随会话逐轮膨胀；整支终审抓出并修正为 `len(discarded)`，同时删掉了变 dead 的 `previous_covered` 形参。落地 Phase D 时应把「`covered` 的语义」写进规格而非只留在代码注释里。 | `cross-turn-history-summary` 整支终审（2026-10-08） | P2 | 低 | F-38 / Phase D |
| F-40 | **「被丢弃段」的 token 量宜增量维护（每轮 O(n) 重数）** | 跨轮历史摘要在**每回合正常完成**后都会：① `get_history_async` 读**全量**会话历史；② `should_schedule` 再对**整个被丢弃段**逐条 `count_tokens`。会话越长这两步越贵（被丢弃段随会话单调增长）。可改为在会话维度缓存「已累计的被丢弃段 token」，新回合只对**新增**的溢出部分计数 ⇒ 摊还 O(1)。**当前不紧迫**（单 worker、文档规模千级上限），但属可预期的线性退化点，且它与 F-39 共用一个"累计边界"概念，宜一并设计。 | `cross-turn-history-summary` 整支终审 Recommendation（2026-10-08） | P3 | 中 | F-39 |

## G. 前端与交互体验

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| G-01 | **MCP 工具卡片（工具调用可视化）** | 模型调用工具时，对话流内以卡片展示工具调用过程：工具名、生命周期状态（running/success/failed/interrupted）、参数、结果折叠、按工具类型区分内容（检索/搜索/阅读）。参考 deepseek-harness `ui-tool`（ToolCallTree，经 `conversation.chat.node` 槽位注入对话流）。**现状**：已有雏形——`search_web` 发 `TOOL_START/TOOL_END` SSE 状态事件，前端 `renderStatusTag` 显示"正在联网搜索"。**接入时机**：接 MCP 时是刚需（工具数量爆炸、带参数与结构化结果）；当前仅 3 个 native 工具，状态标签够用，卡片收益在工具多后体现，可随 MCP 一起做。纯 HTML 折叠面板可实现，不强制框架化 | deepseek-harness 对比 | P2 | 中 | F-01 |
| G-02 | **多 Agent 工作流可视化** | 引入多 Agent/子任务编排后，对话流内以可折叠面板展示 run → phase → member 执行树：run 行（状态点+状态文本+chevron）、phase 阶段分组（折叠行+成员数）、member 成员行（状态列）。参考 deepseek-harness `ui-workflow-run`（`WorkflowRunPanel`）。**接入时机**：必须等多 Agent 编排落地后——当前单 agent 循环（kb_router → agent → tools → format）无子任务/成员概念，无数据源，现在加是空壳 | deepseek-harness 对比 | P3 | 中 | F-02 |
| G-03 | **记忆系统展示** | 三层记忆（Working/Episodic/Semantic）落地后，对话界面提供记忆查看/管理 UI：展示当前记忆条目、来源、置信度/衰退状态。参考 financial_rag 三层记忆体系与 deepseek-harness context/spill 机制。**接入时机**：记忆系统本身落地后才有数据可展示，当前项目无记忆层 | financial_rag / deepseek-harness 对比 | P3 | 中 | A-07、记忆系统落地 |
| G-04 | **代码语法高亮** | chat-markdown-rendering 后置项。助手消息代码块语法高亮：Prism（轻量、适合实时高亮）或 Shiki（质量高但整块 re-highlight 重）。**流式策略**：流式中保持纯文本/轻量样式，代码块闭合后一次性高亮，避免每帧重高亮卡顿 | deepseek-harness 对比 | P2 | 低 | chat-markdown-rendering |
| G-05 | **LaTeX 数学公式** | chat-markdown-rendering 后置项。助手消息中 KaTeX 渲染数学公式（dsh 用 micromark 数学扩展原生支持） | deepseek-harness 对比 | P3 | 低 | chat-markdown-rendering |
| G-06 | **前端框架化评估（架构方向）** | 当前静态 HTML + 节流渲染可承载 G-01 工具卡片与简单工作流面板。**触发信号**：①需 AST 级增量渲染（流式中选中/复制文本、交互式代码块）——react-markdown/remark 的优势，Vue v-html 与 innerHTML 无异；②UI 复杂度超出静态页面可维护性（多组件状态/路由）；③G-01/G-02/G-03 同时上线。**原则**：最小改动，每次用当前架构渐进承载，信号出现再单独立 change 评估并连同构建链/部署一起规划 | 2026-08-31 调研 | P3 | 高 | G-01, G-02, G-03 |
| G-07 | **`/xxx` 直出轮的委派折叠区（delegate start/end 缺失）** | `/xxx` 命中 fork 技能走直出轮时，直出节点（`src/agents/graph/skill_direct.py`）只投递 `delegate action=delta`，从不投 `start`/`end`（`start`/`end` 仅由 `src/agents/skills/delegate_task.py` 投递），导致前端拿不到 open section、把整包 delta 丢弃（console 报 `delegate delta without open section`），直出轮没有委派折叠区。**建议**：直出节点补投 `start`/`end`（与 `delegate_task` 同构），或前端容忍无 `start` 的 delta。**注**：存量缺陷，非 turn-provenance-observability change 引入，实机验证时发现 | turn-provenance-observability 实机验证 | P2 | 低 | 无 |
| G-08 | **【已修】知识库页「详情」弹窗 71 天不可用** | `deploy/nginx/html/index.html` 的 `showEvalModal` 按**扁平**键读 `eval_detail`（`data.table_score` / `data.granularity_cv.toFixed(2)`），而后端返回的是**嵌套**结构（`structure_integrity.table.score` 等）⇒ `granularity_cv` 是对象，`.toFixed` 抛 `TypeError`，弹窗在渲染前即中断（`e503375` 2026-07-12 引入 → 2026-09-22 前端冒烟才发现）。同源错误还波及二级弹窗 `showEvalBroken` 三个分支，以及一级弹窗断裂链接的键名（传 `sec.key='structure'`、查询表的键却是 `'table'`）。**根因不是"没读文档"**：写前端当日（2026-07-12）`docs/agents/api_contract.md` **尚不存在**（2026-08-25 才创建），字段级契约当时为零。**已修**：契约补全（`api_contract.md` §2.2.1 加完整形状 + 逐键类型表 + 历史踩坑警示）、两级弹窗改按嵌套取值、`fmt`/`meterCls` 加类型守卫（漂移只降级成 `—`、不再让组件不渲染）；规则同步补进 `CLAUDE.md` 验证项 2、`code-map.md` 落点速查、`defensive-patterns.md`「接口契约」 | 2026-09-22 前端冒烟（langfuse-v2 合并后） | P2 | 低 | 无 |
| G-09 | **前端契约消费无自动化护栏** | G-08 那类"后端形状变了、前端按旧形状读"的缺陷，现有四层闸门**一层都抓不到**：契约测试只测后端、doc 闸门只查文档、RAGAS 测质量、pyright 不跨语言。唯一能覆盖的是 `e2e-playwright-regression`，而它**0/30 尚未实施**。**建议**：该 change 落地时把「知识库页详情弹窗可打开且渲染出评分」列为首批用例之一（它正是"端到端最终形态"类断言） | 2026-09-22 修复 G-08 时确认 | P2 | 低 | e2e-playwright-regression |

## H. 可观测性与运维

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| H-01 | **Langfuse 接线归档遗留：CLI trace 落库未实跑** | `langfuse-trace-wiring` 的 DoD ⑧「CLI 的 trace 在 `--gate` / 异常退出路径下也能落库」**未实跑**，归档时仅有结构证据（`flush_tracing()` 位于 CLI 的 `finally`；`tests/test_tracing_order.py` 断言 CLI 路径的 `configure → flush` 调用序）。⚠ **根因是独立的、且影响面更大的一条**：`src/config/settings.py:159` 的**硬编码文档白名单**指向 `neusoft_2025_q1.pdf`（`d5d72d1a-…`），该文档已不在 dev 库，`data/ragas/` 下也没有任何测试集 ⇒ `python -m src.cli.eval_ragas --generate --size 1` 直接 exit 1。**不修这条，dev 侧整个 RAGAS 评测链路都启动不了**，可独立修、优先级高于本条的 trace 验证 | langfuse-trace-wiring 归档遗留（2026-09-24） | P2 | 低 | 无 |
| H-02 | **Langfuse purge 定时接入（dev + prod）** | 删除后端已实现（直连 Langfuse PG 跑 SQL，ADR-0013；`src/cli/purge_langfuse_traces.py` + `src/infra/llm/langfuse_purge.py`，真删已验），但**定时任务未接入** —— 宿主 cron / systemd timer / compose 定时服务三选一，且 **dev 与 prod 各自落地**。ADR-0011 复查条件③要求「清理与接线一并落地」，属接线变更的欠账 | langfuse-trace-wiring 计划外部署动作 ② | P1 | 低 | 无 |
| H-03 | **prod 侧 Langfuse 落地** | 「改模板 ≠ 改到机器」：需在 prod 机上同步 `.env`（含 `COMPOSE_PROFILES=langfuse` 与三个 `LANGFUSE_INIT_PROJECT_*`；`INIT_*` 与 `LANGFUSE_*` 两对键须成对相等），确认 `langfuse-web` 在跑，随后验证 trace 可查 | langfuse-trace-wiring 计划外部署动作 ④ | P1 | 低 | 无 |
| H-04 | **部署档 `data/ragas` 挂载无消费方（可清理）** | `docker-compose.image.yml` 的 `./data/ragas:/app/data/ragas` 在部署机上**没有任何消费方**：①生成链路 `python -m src.cli.eval_ragas` 需要 ragas，而**瘦身后镜像里没有它**、也不在部署机跑；②读链路（前端 KB 徽标 → `POST /api/kbs/eval/latest`）走 **DB 表 `eval_report`**（`app_service.get_latest_eval_report` → `_eval_repo`），完全不碰文件；③`report_path` 字段只被**存**进 DB、只被**回显**，全仓无人按它打开文件（已 grep 核实）。唯一可能写它的是 `src/api/ragas_generate.py:58`，而它自己调不动（见 H-05）。⇒ 该挂载与 `deploy.sh` 里的 `mkdir -p data/ragas` 均可删，宿主必须提供的文件从 5 项收敛到 4 项。**2026-09-30 决定：暂不清理，仅登记** | 部署链路审计（2026-09-30） | P3 | 低 | 无 |
| H-05 | **`POST /api/ragas/generate` 是孤儿端点，且瘦身后不可用** | 该端点在 `main.py:201` 注册、`src/api/README.md:23` 有文档，但**全仓无调用方**（已核实）：CLI 走**进程内**调用 `python -m src.cli.eval_ragas --generate`，不经过 HTTP；前端唯一的 RAGAS 调用是 `POST /api/kbs/eval/latest`（读）。而其实现在 `run_generate` 里是**惰性** `import ragas`（`src/cli/eval_ragas_generate.py:283、346`），瘦身后镜像里没有 ragas ⇒ **一旦被调用必然 500**（模块级导入不受影响，故 `import src.main` 正常、服务照常起）。三个选项：①**保留不管**（当前决定，对外是一扇打不开的门）②从生产路由摘掉（要动路由装配，且引入"生产/开发路由差异"概念）③把 ragas 装回镜像（+300MB，不划算）。**若将来要清理，与 H-04 属同一批** | 部署链路审计（2026-09-30） | P3 | 低 | 无 |
| H-06 | **HTTPS 选型未定** | 目标机 nginx 只有 80（另有 Langfuse `:3000` 对外，见 H-07）、安全组放行 **22+80+3000**，demo 当前为**纯 HTTP** 口径。待定：nginx 443 + 证书（自签 / Let's Encrypt）vs SLB 终止 TLS。⚠ 明文 HTTP 下前端**安全上下文专属 API 不可用**（已踩：登录页 `crypto.randomUUID` 报错，见 `defensive-patterns.md`「前端（浏览器 API）」）| demo-deploy-2c8g 未决项（2026-10-04） | P2 | 中 | 无 |
| H-07 | **Langfuse 去留未定** | 目标机栈内含 `langfuse-web`（`docker-compose.image.yml`，限额 256m）。**2026-10-09 起改为对外暴露 `:3000`**（ADR-0019：端口由 `127.0.0.1:3000:3000` 改为 `3000:3000` + 安全组放行 3000；对外地址 `NEXTAUTH_URL` 与自助注册关闭均**在部署档里硬编码**，不走 `.env`）。待定：长期是否保留；是否再加网络层认证（nginx `auth_basic` / IP 白名单）或改走 nginx 反代 + HTTPS | demo-deploy-2c8g 未决项（2026-10-04），2026-10-09 更新 | P3 | 低 | H-06 |
| H-08 | **`docker-compose.prod.yml` 与目标形态的关系未定** | 目标机实际运行 `docker-compose.image.yml`；`docker-compose.prod.yml` 从未在目标机验证、档位（合计 14.25 GB）会 OOM。待定：废弃 / 删除 / 与目标形态对齐 | demo-deploy-2c8g 未决项（2026-10-04） | P3 | 低 | 无 |
| H-09 | **备份口径未定** | MinIO 里的原始上传文件是**唯一不可再生源头**、`postgres_data` 卷承载元数据与向量 —— 二者当前**均无备份**。待定：频率、保留期、存放位置（是否上 OSS）| demo-deploy-2c8g 未决项（2026-10-04） | P1 | 中 | 无 |
| H-10 | **托管化未定** | 当前为单机自建（PG / Redis / MinIO 与 app 同机，单点）。待定：是否迁 RDS / Redis / OSS / SLB | demo-deploy-2c8g 未决项（2026-10-04） | P3 | 高 | H-09 |
| H-11 | **版本回滚未生效（demo，2026-10-05 起搁置待查）** | 现象：从版本页回滚到旧版本后，页面 `brand-version` **没退回**（`v0.1.1` 应回 `v0.1.0`）。**已确认的机理（避免下次重走弯路）**：① 回滚 = 用**旧部署单** → 下发**旧制品** → 制品 compose 里烘着**旧 tag**；② `deploy.sh:342` 从**制品里的 compose** 解析 tag（**不从环境读 `BUILD_NUMBER`**）—— 这是正确设计，若改成"从环境读"会让回滚**永久拉最新版**；③ ACR 是 **Docker Registry v2**，镜像**不能 curl**（实测 `https://…/v2/` 返回 `401` + `WWW-Authenticate: Bearer`；`http://` 的 80 口直接超时）—— 曾试图"传镜像下载链接"失败。**待查点（按性价比排序）**：① 部署页「启动槽位」是否真的消费 `${package_download_path}`，是否残留"自己下载 / 写死 `/package.tgz`"；② 对比**首次发布 v0.1.1** 与**回滚**两次执行的日志里下载 URL（`artifactName` / `version`）是否不同 —— 相同 ⇒ 平台没换制品（问题在部署单/回滚配置）；不同 ⇒ 脚本没消费；③ 回滚后目标机上 `deploy/nginx/html/chat.html` 的 `brand-version` 实际值（区分"旧制品没落地" vs "落地了但浏览器/nginx 缓存"）；④ 解压目标路径是否与 `deploy.sh` 的 `ROOT`（`/opt/www/corporate_rag`）一致 | demo 部署回滚验证（2026-10-05） | P2 | 中 | 无 |

## 标签说明

**优先级：**
- P0 — 必要，缺失会影响核心使用
- P1 — 重要，有明确收益
- P2 — 有价值，但可等待
- P3 — 长期规划

**预估成本：**
- 低 — 1-3 天
- 中 — 1-2 周
- 高 — 2-4 周以上

---

## 最佳实践参考（2026 年业界共识）

以下为调研 19 个主流记忆系统项目（MemGPT/mem0/MemOS/SimpleMem/Memobase/OpenMemory 等）和 Agent 架构实践后整理的共识。

### 记忆系统

| 实践 | 说明 |
|------|------|
| **分层架构** | 95% 项目采用三层（Working/Episodic/Semantic），financial_rag 的设计符合业界主流 |
| **提取异步化** | 同步 inline 提取是最常见的错误。应使用 Buffer + 队列 + 批处理，N 条消息固定 3 次 LLM 调用，减少 40-50% 成本 |
| **记忆衰退** | 长期记忆需要衰减机制（指数衰减模型），不同认知类型不同衰减率（如事实 0.005/天，事件 0.015/天） |
| **去重合并** | 写入前做相似度检测，相同实体/事实合并而非追加 |
| **重要性过滤** | 不是所有对话内容都值得放进长期记忆，设置信度阈值（如 0.7） |

### Agent 架构

| 实践 | 说明 |
|------|------|
| **工具选择前置** | 无论用 Function Calling 还是 MCP，都应在调用 LLM 前做工具过滤，避免全量工具定义撑爆 Prompt |
| **熔断优先于限流** | 内部系统限流价值有限，熔断（快速失败）比限流（排队等）更适合 AI 场景 |
| **混合部署** | MVP 用 LangChain/Function Calling 快速验证，核心链路逐步自研 |
| **MCP 独立部署** | 工具需要跨系统复用时封装成 MCP Server，否则直接 Function Calling 写代码里 |
| **Agentic RAG 的前提条件** | 业界共识不是反对 Agentic RAG，而是强调**顺序**：①**基础检索质量达标是前提**——Hybrid Search + Good Chunking + Cross-Encoder Reranker 必须先做好。在这些没做之前上 Agentic RAG，等于在垃圾数据上多花 5-10x token 反复检索，结果仍是错的；②**使用 adaptive routing**——简单查询走单步 RAG（低成本），复杂/多跳查询才走 Agentic 多步推理（按需付费），不需要所有查询都扛 Agentic 的 overhead；③**硬工程约束不可少**——循环上限（建议 max 3 轮）、上下文裁剪（≈6 docs × 800 tokens）、每步可观测；④**场景边界**：用户接受 30s+ 延迟的场景（金融研究/法律分析）适用；需要实时交互的场景（客服/IDE 助手）不适用 |

### Prompt 管理

| 实践 | 说明 |
|------|------|
| **运行时管理优于 Git 管理** | Prompt 不是代码，不应和代码混在 Git 中。回退 prompt 会牵连代码，给产品开 Git 权限是安全红线。用 Langfuse 做统一管理更合适 |
| **Langfuse 作为唯一权威来源** | prompt 的编辑、版本管理、A/B 测试、操作审计全部走 Langfuse。开发、产品、运营各角色在各自权限内操作，互不干扰 |
| **本地兜底只做离线保护** | 代码中的兜底 prompt 只应对 Langfuse 宕机场景，不参与日常迭代。版本号与 Langfuse 同步，标注最后同步时间 |
| **避免双轨制** | 不需要 Git 也存一份 prompt。当 Langfuse 和 Git 不一致时会产生混淆，最终没人知道哪个是"对的" |

**当前阶段说明（2026-09-21）**：上表的"Langfuse 作为唯一权威来源"是**终态意向**，不是现状。
现阶段的唯一事实源是 **Git 内的 `src/config/prompts/templates/*.yaml`** —— 远端 3 个 prompt
（`financial-system-prompt` / `user-prompt-template` / `classifier-prompt`）已出列。

Git 管理只是**第一阶段**，终态切回远端读取：远端读取实现保留未删，届时是"加回名单 + 固定
label/版本"，而非重写。出列理由（终态门槛与闸门可重复性）见 `docs/adr/0010-delist-langfuse-prompts.md`。

### 可靠性（限流/熔断/降级）

RAG 管线中需要防护的关键位置：

```
用户请求 → 网关限流 → Embedding → 向量库 → Rerank → LLM 生成 → 返回
                         ↓熔断降级   ↓超时降级 ↓熔断跳过  ↓熔断+降级
```

| 实践 | 说明 |
|------|------|
| **每层都有超时** | Embedding（500ms）、向量库查询（5s）、Rerank（5s）、LLM（30s）。超时是熔断的前置条件 |
| **限流用在 API 入口和 LLM 调用** | API 入口限流防滥用，LLM 调用限流控成本。内部系统限流价值有限，熔断比限流更重要 |
| **熔断用在外部依赖** | LLM 服务、Rerank 服务、Embedding 服务分别设置熔断器。连续失败 3-5 次打开熔断器，5 分钟后尝试半开恢复 |
| **降级需要分层策略** | Embedding 不可用→退 BM25；Rerank 不可用→跳过；主 LLM 不可用→切备用模型或返回原文 |
| **不重试所有错误** | 401/403/400 不重试；rate limit 等待后重试；timeout 重试 1-2 次后走降级 |
| **重试用指数退避 + jitter** | 初始 100ms，倍数 2，最大 5s。加随机 jitter 防止惊群效应 |
| **降级后告知用户** | "检索服务暂时异常，结果可能不完整"。不要返回通用的 500 或空结果 |
| **Chaos Engineering** | 在测试环境主动注入故障（停掉 Embedding、让 LLM 超时），验证降级路径生效 |

**当前 qyznkf 现状**：
- ✅ ChatManager：Redis→内存降级
- ✅ PromptManager：Langfuse→本地兜底
- ✅ 模型调用：@with_retry 指数退避重试
- ❌ LLM 熔断（A-02，P0）
- ❌ LLM 限流（A-01，P0）
- ❌ Rerank/Embedding 降级（A-03，P1）

### 知识图谱

| 实践 | 说明 |
|------|------|
| **先轻量后重量** | 先在 chunk 元数据存实体关联（方案 A），确认有跨文档关系查询需求后再上 Neo4j |
| **不要一步到位** | 财报问答场景向量检索已足够，知识图谱收益主要体现在多跳推理和关系类问题 |
| **异步提取** | 图谱提取同样应走后台异步队列，避免阻塞文档上传流程 |

### RAG 全链路增强（新增 — 2026 年业界共识）

#### 检索前（Pre-Retrieval）

| 实践 | 说明 |
|------|------|
| **Query Rewriting（查询改写）** | 用 LLM 或小模型将用户口语化查询转写为检索友好形式。生产验证中改写后命中率提升 10-20%。HyDE（Hypothetical Document Embeddings）是特殊形式的改写——先生成假设性理想回答再检索，zero-shot 场景 recall +15-20% |
| **Query Decomposition（查询分解）** | 将复杂多面问题拆解为独立子查询（如"公司2023年营收和利润"→"2023年营收"+"2023年利润"），分别检索后合并。FinSage 系统（CIKM 2025）使用此技术，Recall 达 92.51% |
| **Multi-HyDE（多样化假设检索）** | 生成多个不等价的假设查询（非同义词变体），覆盖不同信息维度。EMNLP 2025 论文验证：准确率 +11.2%，幻觉 -15% |
| **Metadata Filtering（元数据过滤）** | 检索前按文档类型、日期、安全级别等过滤，大幅减少噪声。Red Hat OGX 方案推荐三级过滤层：向量库级 / 文档级 / 分块级 |
| **查询分类路由** | 按复杂度路由查询：简单→直接 LLM 回答（5-10×更便宜），中等→单步 RAG，复杂→多步 RAG。可降低 30-40% 平均成本 |

#### 检索（Retrieval）

| 实践 | 说明 |
|------|------|
| **Hybrid Search（混合检索）** | BM25 稀疏检索 + Dense 向量语义检索，通过 RRF（Reciprocal Rank Fusion, k=60）融合。PwC 2025 论文验证：金融文档 68% 胜率。**2026 年生产必备** |
| **Parent-Child 分块（Small-to-Big）** | **2026 年生产默认策略**。Child chunks（128-256 tokens）精准检索 + Parent chunks（512-1024 tokens）完整上下文。PwC 论文验证 65% 胜率，只增加 0.2s 延迟。**NVIDIA 实测：accuracy 从 61% 提升至 89%**。LangChain 的 ParentDocumentRetriever 和 LlamaIndex 的 HierarchicalNodeParser 都有原生支持 |
| **Semantic Chunking（语义分块）** | 按句子/段落边界切分而非固定字符数，保持语义完整性。与固定分块比效果提升 18-27%（QASC 论文，Apr 2026） |
| **Multi-Path Retrieval（多路径检索）** | BM25 + Dense + Metadata + HyDE 四路并行检索，结果融合。FinSage 系统以此实现 92.51% Recall |
| **Contextual Retrieval（上下文检索）** | 索引时用 LLM 为每个 chunk 生成 50-100 token 摘要拼接到 chunk 上再 embedding。**Anthropic 验证：结合 reranker 可减少 67% 检索失败**。EACL 2026 基准一致验证提升检索质量 |

#### 检索后（Post-Retrieval）

| 实践 | 说明 |
|------|------|
| **Cross-Encoder Reranking** | **生产必备，不可跳过**。对 top-50~150 候选做交叉编码精排后保留 top-5~20。PwC 论文验证 MRR@5: 0.16→0.75 |
| **Lost-in-the-Middle 防御** | LLM 对 context 中间部分 recall 最低，首尾最高。将最高分 chunk 放在 prompt 首尾位置，次要 chunk 放中间。不要盲目增大 TOP_K_RERANK——更多 chunk 不一定更好，反而可能稀释核心信息 |
| **Adaptive Context Expansion** | 非无脑宽窗口，而是选择性扩展相邻 chunk。SCAR（June 2026）：Recall 92.8%，上下文 tokens 减少 27.1% |
| **Self-RAG / Corrective RAG** | 自评估检索结果相关性——正确则生成，错误则 web 搜索 fallback。Self-RAG 使用 [Retrieve]/[IsRel]/[IsSup]/[IsUse] 反射 token 做自评估。CRAG 使用 Retrieval Evaluator 做三分类（Correct/Incorrect/Ambiguous） |
| **Post-Retrieval Cascade** | "最便宜优先"级联策略：按成本升序执行流程步，只有需要时才升级到 LLM。Coverage Illusion 论文（May 2026）验证：72.2% 查询无需 LLM 增强，延迟降低 31.8% |
| **Context Compression** | 压缩/摘要检索结果后送入 LLM，节省 30-50% context tokens |

#### 评估与监控

| 实践 | 说明 |
|------|------|
| **三层独立评估体系** | 检索层（Recall@k, nDCG, MRR, Hit Rate）→生成层（Faithfulness, Answer Relevancy, Groundedness）→归因层（ChunkAttribution 回答是否引用了chunk, ChunkUtilization 模型对chunk的使用效率）。**不要只评估最终回答**，三层指标反映不同维度的失败模式 |
| **RAGAS 四指标** | Faithfulness（忠实度）、Answer Relevancy（回答相关性）、Context Precision（上下文精度）、Context Recall（上下文召回率）。**不要聚合为单一分数**——每个指标反映不同维度的失败模式 |
| **生产监控黄金信号** | TTFT（首 token 延迟）、TPOT（输出 token 速率）、单次请求成本、保卫命中率（Guardrail）、用户隐式反馈（编辑距离/会话放弃率） |
| **Span 级追踪** | 每个 RAG 请求展开为检索 span（chunks + rerank score + latency）→ 生成 span（model + tokens + cost）→ 保卫 span → 路由 span。工具：Langfuse（OSS，自部署）、Arize Phoenix |
| **CI/CD 门禁** | Golden set（100-500 条，按意图分层）每次 PR 跑 RAGAS，按指标阈值卡口（如 faithfulness ≥ 0.90）。每季度轮换 10-20% 防止分布漂移 |
| **Embedding Drift（嵌入漂移）检测** | 三种漂移：换 embedding 模型未重索引（model drift）；新文档类型改变向量空间（corpus drift）；用户查询词汇变化（query drift）。版本化所有组件，监控 MRR 随时间变化，指标下降超过阈值触发告警 |
| **Stale Context（上下文过期）** | 索引数据可能随时间过期或不一致。需要 corpus 版本化（hash + date + chunk count），每次更新知识库后自动重新评估检索质量 |
| **在线采样评估** | 5-20% 生产流量在线评分（100% 采样异常请求）。用蒸馏小模型做 judge（~50ms 级）替代 LLM judge 降低成本 |
| **Judge 模型校准** | **永远不要信任未校准的 LLM judge**。在 ≥50-100 样本上与人工标注校准，固定 judge prompt 和模型版本，使用与生成模型不同的模型族避免 self-bias |

### RAGAS 评估实操最佳实践（2026）

以下为调研多篇 RAGAS 最佳实践指南后整理的实操建议，直接指导 `eval_ragas.py` 工具的使用和配置。

| 实践 | 说明 |
|------|------|
| **评估 LLM Temperature 必须为 0** | 评估是测量行为，不是生成行为。`temperature=0` 确保多次运行同一条 QA 对得到一致分数，否则无法区分"系统改好了还是 randomness 波动"。生产 LLM 和评估 LLM 建议用不同模型族（如生产用 deepseek-chat，评估用 qwen-max），避免评估者自我偏好（self-bias）。注意 RAGAS 内部 faithfulness/context_recall 等指标需要调 LLM 做 claim 提取和判断，temperature 不设为 0 时这些判断会随机波动 |
| **黄金测试集（Golden Dataset）** | 从真实用户问题精选 50-200 条，覆盖常见问题类型，人工标注高质量 ground_truth。固定版本作为基线，每次系统变更（chunking 参数、rerank 参数、prompt 修改）时在同一测试集上重新评估，对比分数变化。建议每季度轮换 10-20% 防止分布漂移 |
| **指标解读定位瓶颈** | 四个指标分别对应不同的优化方向，不要只看平均分：`context_precision` 低 → 检索 Top-K 太大或缺少重排序；`faithfulness` 低 → LLM 未受约束"只从上下文回答"；`context_recall` 低 → 分块策略不合理（信息被切碎）；`answer_relevancy` 低 → 模型回答太宽泛或答非所问 |
| **不要合成单一综合分** | 一个高平均分可能掩盖关键维度的缺陷。权重（如 faithfulness 0.3 + recall 0.3 + precision 0.2 + relevancy 0.2）是客观分数，推荐在报告中保留各指标单独值而不做 PASS/FAIL 判定。`--gate` 功能可保留作为参考，但不建议作为唯一的发布门禁 |
| **结构化输出便于 CI 集成** | 评估结果应以结构化格式（CSV + Markdown）持久化，每行包含 question / 各指标得分 / chunk_size / retrieval_k 等上下文信息，便于 `compare_retrieval.py` 解析和在 CI 中做回归对比 |
| **评估时机策略** | 不需要每次生产请求都跑完整评估（消耗 3-4 次 LLM 调用）。推荐：每次参数改动时跑全量黄金测试集（CI/CD）；生产环境定期抽样 1-5%；用户反馈触发时对单条做深度评估 |

### 文档解析

| 实践 | 说明 |
|------|------|
| **80% 的 RAG 失败源于 ingestion 层（非 LLM）** | PremAI/ DigitalOcean 2026 调研结论：**"80% of RAG failures trace back to the ingestion and chunking layer, not the LLM"**。解析质量直接决定 RAG 系统的**上限**，检索策略决定**下限**。投入精力优化解析管线的性价比远高于调 LLM prompt |
| **解析是 RAG 的瓶颈** | 错误解析（表格结构破坏、乱码、语义截断）会直接污染知识库 |
| **表格不跨块切分** | 表格应作为独立整体处理，绝不跨块切分。基础做法：解析为 Markdown 表格；进阶做法：解析为结构化 JSON（数值型报表） |
| **解析管线容错** | 解析失败≠拒绝入库。应有降级策略：优先解析器失败后改用备用解析器或全文文本兜底，让更多有效数据进入知识库 |
| **解析质量采样校验** | 在 Chunking 阶段做采样校验：检查乱码率（<5%）、块大小分布、最小块大小（>50 Token）。提前发现低质量数据，避免批量入库污染 |
| **扫描件用 OCR 兜底** | 检测为扫描件后启用神经网络 OCR（Tesseract 4.x+ / PaddleOCR）。关键文档建议双 OCR 引擎交叉校验，数值型文档做数值一致性校验 |
| **文档类型专用策略** | PDF→Layout-aware Parser；Markdown→按标题层级切分；Word→按样式重建文档树；Excel→按行/区域提取为 JSON |

### 表格提取

主流 PDF 表格提取库对比（基于 2026 年实测金融财报数据）：

| 工具 | 方式 | 复杂表格能力 | 财务报告表现 | 速度 | 成本 |
|------|------|------------|------------|------|------|
| **pdfplumber** | Python 库 | ⭐⭐⭐⭐ | 高准确率 | 中等 | 免费 |
| **PyMuPDF** (fitz) | Python 库 | ⭐⭐⭐ | 中等 | 最快 | 免费 |
| **Camelot** | Python 库 | ⭐⭐ | 复杂报表直接失败 | 中等 | 免费 |
| **Tabula** | Java 包装 | ⭐⭐ | 格式问题多 | 慢 | 免费 |
| **MinerU** | 独立服务 | ⭐⭐⭐⭐⭐ | 高（含 OCR+VLM） | 慢 | 免费+GPU |
| **Unstructured** | 企业服务 | ⭐⭐⭐⭐ | 好 | 中等 | 免费/付费 |
| **LLMWhisperer** | 云端 API | ⭐⭐⭐⭐⭐ | 最高 | 快 | 付费 |

**实践建议：**
| 实践 | 说明 |
|------|------|
| **主用 pdfplumber** | 财务类复杂表格效果最好，纯代码替换，不需要额外部署服务 |
| **MinerU 兜底** | pdfplumber 提取失败的扫描件或极端复杂排版时用，需 GPU |
| **表格不跨块切分** | 表格应作为独立整体处理，绝不跨块。先转为 Markdown 表格或 JSON 再入库 |
| **数值校验** | 财务报表的数字提取后做一致性校验（合计=分项之和），发现不一致标记为解析异常 |

### 分块策略

| 实践 | 说明 |
|------|------|
| **Token 计数优于字符计数** | 中文 1 字符约 1-2 tokens，用字符数设 chunk_size 会导致与嵌入模型 Token 限制不匹配。必须接入 tokenizer 做精确计数 |
| **chunk_size 甜点区间 256-512 tokens** | 过小（<50）导致语义碎片化，过大（>2500）导致信息稀释。财报等分析型场景可偏大（512-1024）。2026 年多个基准测试中 512-token 分块以 69% 准确率排名第一 |
| **分块策略选择 = 文档类型 + 查询类型** | 事实查询→偏小（64-256）；分析推理→偏大（512-1024）；技术文档→按标题层级；代码→按函数块 |
| **文档类型特异性分块** | 不同类型文档需要不同策略：合同/法规→**clause/章节级**（语义单元是条款）；论文/长文→**语义分块**（主题边界优于 markup）；FAQ/产品文档→**固定 512 + 重叠**（段落均匀）；代码→**late-interaction**（ColBERT 级 token 匹配保留标识符）；多语言→**late-interaction**（跨语言 token 级匹配） |
| **分块质量采样校验** | 入库前自动检查：块大小分布是否均匀、最小块是否 <50 tokens（碎片告警）、乱码率 <5% |
| **Parent-Child 分块是生产升级路径** | 小块精准检索 + 大块完整上下文，兼顾精度与生成质量。**2026 年已成为生产默认策略**，当 RAGAS Context Recall <80% 时优先考虑 |
| **语义分块** | 按句子/段落边界而非固定字符数切分。QASC 论文（Apr 2026）验证对比固定分块 F1 提升 18-27% |
| **Late Chunking（延迟分块）** | 2025-2026 主流技术：先对整个文档做 embedding，再按分块范围 pooling，保留跨块语义。需专用 embedding 支持（如 jina-embeddings-v3） |
| **上下文关联索引** | 索引时将 LLM 生成的摘要/doc 级信息拼接到每个 chunk 上，使每个 chunk 拥有文档级上下文。EACL 2026 基准：一致提升检索质量 |

---

---

## 实施路线图

基于 2025-2026 年业界调研和投入产出比分析，建议分四个阶段推进：

### 阶段 1：检索质量奠基（P0，1-2 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 1 | E-05 Token 计数分块 | 基础对齐，后续所有检索优化的前提 |
| 2 | E-07 Hybrid Search（BM25 + Dense） | 最大单点收益，精确查询命中率显著提升 |
| 3 | E-06 分块质量自动化监控 | 不让坏数据进知识库 |
| 4 | E-08 Parent-Child 分块 | 兼顾检索精度与生成上下文质量 |
| 5 | A-02/A-03 熔断+降级 | 生产可靠性的基础保障 |
| 6 | F-04 Query Rewriting | 提升口语化查询匹配率 |

### 阶段 2：核心能力增强（P1，2-4 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 7 | F-05 Cross-Encoder Rerank 参数调优 | 极低成本获精度提升（MRR 0.16→0.75） |
| 8 | F-06 查询分类路由 | 降本增效，简单查询不走完整 RAG |
| 9 | A-04 Token 用量追踪 | 成本可观测 |
| 10 | A-06 Tool Selector | 工具调用前过滤，降低 Prompt 膨胀 |
| 11 | A-07 记忆提取异步化 | 减少用户感知延迟，降低 40-50% 成本 |
| 12 | E-02 表格结构化输出 | 金融场景高频需求 |
| 13 | E-04 轻量实体关联 | 跨文档关系查询基础 |

### 阶段 3：数据驱动闭环（P2，4-6 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 14 | F-07 Retrieval 质量自评估 | 自动修复检索失败 |
| 15 | F-08 在线 RAGAS 采样评估 | 数据驱动的 RAG 优化闭环 |
| 16 | A-08 Langfuse v3.x 升级 | 获得 LLM-as-Judge 评估器等高级功能 |
| 17 | E-01 专用财务文档 chunker | 按文档类型优化分块 |
| 18 | A-09 引入传统小模型（L1） | 降低 LLM 调用依赖 |

### 阶段 4：智能 Agent 化（P3，6 周+）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 19 | F-01 MCP 工具集成 | 接入 mcp_server 获取专业工具 |
| 20 | F-02 LangGraph Agentic RAG | 从单链升级为 Agent 编排。**前提条件**：阶段 1-2 的基础检索质量已达标（Hybrid Search + Parent-Child Chunking + Reranker 就位），且经评估确认有足量需要多跳推理的复杂查询。Agentic RAG 不修复坏检索——只会用更多 token 在同样差的检索结果上反复折腾。配合 adaptive routing 使用：简单查询走单步 RAG，复杂查询才走 Agentic 多步推理 |
| 21 | F-03 知识图谱（Neo4j） | 深层多跳推理（需前置条件确认） |
| 22 | E-03 OCR 支持 | 扫描件 PDF 支持 |

---

## 项目涉及模型分类参考

以下梳理本项目（qyznkf + financial_rag）和对话中涉及的所有模型，按层级分类。

### 四层模型体系

| 层级 | 代表 | 参数量 | 成本 | 推理位置 | 适合做什么 |
|------|------|-------|------|---------|----------|
| **L0 规则/字典** | 正则表达式、jieba 分词、关键词匹配、已知公司名单 | 无 | 免费 | 本地 CPU | 日期/金额/百分数提取、公司名匹配、意图关键词匹配 |
| **L1 传统小模型** | BERT 分类器、NER 模型、LightGBM 分类器 | 10-500 MB | 极低（毫秒级，免费） | CPU 可跑 | 文本分类（工具选择）、命名实体识别（公司/人名提取）、意图路由 |
| **L2 Embedding/Rerank** | text-embedding-v3 (DashScope)、bge-reranker (SiliconFlow) | 几百 MB | 低（按 API 调用计费，远低于 LLM） | CPU/GPU | 语义相似度、工具向量检索、Rerank 排序 |
| **L3 大语言模型（LLM）** | 见下表 | 7B ~ 几百 B | 高（按 Token 计费） | GPU | 推理、生成、复杂理解 |

### L3 大语言模型细分

| 子类 | 参数量 | 代表 | 项目中使用 | 适合 |
|------|-------|------|----------|------|
| **小参数 LLM**（你的理解） | 7B-14B | Qwen2.5-7B, DeepSeek-7B, GLM-4-9B | 可以用作本地部署的 fallback | 基础推理、简单 RAG |
| **中参数 LLM** | 32B-72B | Qwen2.5-72B, DeepSeek-V2 | 可本地部署，需多 GPU | 复杂推理、Agent 场景 |
| **大参数 LLM（云端）** | 数百 B ~ T 级 | Qwen-max（DashScope）、GPT-4o、DeepSeek-V3 | **qyznkf 当前主力：Qwen-max via DashScope** | 主 RAG 链路、Agent 编排、评估 |

### 本项目中各场景的模型选型

| 场景 | 当前使用的模型（qyznkf） | 可用的更优方案 |
|------|------------------------|--------------|
| **主 RAG 推理** | DashScope Qwen-max（云端大参数 LLM） | 保持不动，这是主力 |
| **Embedding（向量化）** | DashScope text-embedding-v3（L2 Embedding） | 保持不动 |
| **Rerank（重排序）** | DashScope gte-rerank-v2（L2 Rerank） | 保持不动 |
| **工具/模式选择（A-06）** | 无（计划做：规则→L2 Embedding→L1 BERT 分类器） | 三阶段过滤 |
| **实体提取（E-04）** | 无（计划做：规则提取 + 可选 L1 BERT NER） | 先规则，不够再加 BERT NER |
| **文档分块 Token 计数（E-05）** | 无（计划做：接入 DashScope tokenizer） | tokenizer 本身就是字典查找，免费 |
| **RAGAS 评估** | DashScope Qwen-max（云端大参数 LLM） | RAGAS 内部调 LLM 算 faithfulness 等指标，保持不动 |
| **Langfuse LLM-as-Judge（A-08）** | 无（计划做：升级后使用） | 建议用小参数的 Judge 模型（如 GPT-4o-mini 级别），降低成本 |

### 关键结论

- **L0 规则**：能做的就不要上模型，免费且精确
- **L1 传统小模型**：当前项目中**没有用到**，如果后续需要（如 NER 实体提取、文本分类器），建议用 BERT 级别的（~100MB），CPU 可跑
- **L2 Embedding/Rerank**：你已经用上了（DashScope text-embedding-v3 / gte-rerank-v2），这是正确的选择
- **L3 LLM 小参数（7B-14B）**：项目中**没有用到**，主要因为 DashScope 的 Qwen-max 效果更好。后续如果需要本地 fallback 可以考���
- **L3 LLM 大参数（云端）**：你一直在用（Qwen-max），项目的核心依赖
