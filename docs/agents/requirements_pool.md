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
| A-03 | **降级策略（细化）** | LLM 不可用时**返回检索原文 + 明确告知**（**不切备用模型**，见文末裁定）；Rerank 不可用时跳过 rerank，直接用检索结果；Embedding 不可用时退化为**仅走词法路**（PG 全文检索已在，无需新建倒排索引）；Langfuse 不可用时用本地 prompt 兜底。**降级矩阵**：Embedding 失败→"检索服务暂不可用"；向量库失败→"知识库暂不可用"；Rerank 失败→用原始检索顺序；LLM 失败→返回原文 + 告知（**不切备用模型**，见文末裁定）。**原则**：每个外部依赖都要有明确的降级路径，降级后告知用户"服务降级中"。不重试 401/403/400；rate limit 等待后重试；timeout 重试 1-2 次后走降级。指数退避加随机 jitter 防止惊群效应。**补充（2026-10-15 实测）**：**输入校验类错误同样不重试** —— 评测侧实测「检索上下文为空」时裁判指标 **0 秒**失败（`ValueError: retrieved_contexts cannot be empty`），但我们的有界重试仍跑了 3 次（×3 个上下文类指标 = **9 次纯浪费**）。判据：**失败耗时 ≈0 秒且异常为输入校验类**（`ValueError`）⇒ 重试必然失败，应直接记错误。**裁定（2026-10-16，change `timeout-and-degradation-hardening` design D9）**：**不做进程内模型 fallback**。理由：① 流式下 fallback 只在**首 token 之前**可用（首 token 后不可换模型），而首 token 前失败多为连接/限流类（重试可解），"模型整体不可用"时换模型多半同样不可用；② 换更弱模型兜同一套 prompt 会产出"看起来正常但更差"的答案，对有溯源要求的产品比"明确告知失败"更危险（已踩过换模型致契约变化）；③ ADR-0020 拒绝 LiteLLM 的三条理由（链路耗时 / 只覆盖 2/3 模型 / ~1GB 内存）在进程内 fallback 上同样成立而收益更小。**分两个尺度**：① **抖动（秒~分钟）** → 重试，不 fallback（fallback 只在首 token 之前可用，而首 token 前的失败多为连接/限流类，重试可解）；② **长时不可用（小时~天）** → 选 **(A) 快速失败 + 明确告知（检索原文兜底）+ 告警 + 人工部署级切换**，**不做自愈** —— 依据（用户 2026-10-16 拍板）：降级产物可为「检索原文 + 告知」（保住溯源、零编造），且分钟级可人工完成部署级切换（改 `.env` 的 `LLM_PROVIDER`）。**(B) 进程内换 provider 已否决**；其可接受形态 **(C)**（只在可用性类错误 + 首 token 前触发，且须**透出在用备选模型**以免静默降质）留作将来 SLO 收紧时的路径。**归属**：抖动那半是 ADR-0020 的直接应用；长时不可用那半是**带代价的负向决策**，建议落一条新 ADR（接 dev-wsl 当前最大 `0024` ⇒ `0025`）。**另注（2026-10-16 按代码复核）**：上游**可用性类错误（403 额度耗尽 / 429 穷尽重试）未被识别**（全仓 401/403 字样均属我方 API 鉴权）。这类错误走 `turn_runner.py:136` 的 `except` 分支 ⇒ **不落 assistant 行** + 发 `error` 事件（**不发 `done`**），前端（`chat.html:2352`）显示**通用文案"连接异常，请重试"** —— 对"重试必然再失败"的错误属**误导向**，须按错误类别给可区分文案。且**运维侧无告警触发源**（无 metrics 端点、应用侧不发告警；`src/api/alerts.py` 是**入站**）⇒ (A) 档"分钟级可介入"的**前提待补**（最小做法：把 `error_*.log` 接进 SLS 日志告警）。⚠ 2026-09-29 冒烟所记"403 时前端永久正在思考、无提示"与**今日**前端代码的 `error` 分支不符，**须重跑 403 实测确认** | **P0** | 中 | A-02（**非硬前置** —— 无熔断只是"每次失败各付一次超时"，见 change design D8） |
| A-04 | Token 用量追踪与告警 | 记录每次 LLM 调用的 token 消耗，超出阈值告警 | P1 | 低 | 无 |
| A-05 | **请求超时与重试策略优化（含「细粒度超时」）** | 对 LLM/Embedding/Reranker 调用做**显式**的超时与重试配置。**现状核实（2026-10-15）**：生产链路三处均**零配置**、吃 SDK 默认 —— `ChatOpenAI`（`build_chat`）`request_timeout=None` / `max_retries=None`；`OpenAIEmbeddings`（`build_embeddings`）`None` / **2**；`DashScopeRerank`（`build_rerank`）**无该字段** ⇒ 实际等价于 **read 600s 平铺 + SDK 重试 2 次**，一次挂死最坏 **1800s（30 分钟）**。**做法**：把超时/重试提升为 **provider 层一等配置**（三个 `build_*` 全部显式传入），**先取与现状等值的默认值**（纯"隐式变显式"、零行为变化），再按场景重新定值。**⚠ 不要照搬 RAGAS 评测的数值** —— 评测是离线批量（`read=150s` 可接受），生产是用户**在线等待**且**流式**（超时语义是"两次字节的间隔"，应拆成「首 token 超时」与「流间隔超时」，取值须远小于 150s；且重试会让用户多等一份，须谨慎）。**触发条件**：任何时候都可做（低风险）；流式超时定值需先定 SLO。**裁定（2026-10-16）**：转入 change `timeout-and-degradation-hardening`（超时按角色 + 进 settings 不进 registry / D4 流式三分语义「首 token · 流间隔 · 禁整请求」/ D5 异常分类 + jitter + async 版 / D11 首版取保守值 + 完整可观测，用线上分位收敛） | 2026-10-15 RAGAS 裁判超时排查后的**推广审计**（裁判侧已修：细粒度 `connect10/read150/write30/pool10` + SDK `max_retries=0` + 本层有界重试，见 ADR-0024；生产三处**未修**） | P1 | 低 | 无 |
| A-06 | **工具调用前优化（Tool Selector）** | 调用 LLM 前从全量工具中过滤出最相关的 2-3 个再注入 Prompt，解决 Prompt 膨胀和 Token 浪费。**实现方案**：三阶段过滤——①规则优先：关键词/正则精准命中；②向量化兜底：MCP Client 启动时对所有工具 name+description 做 embedding 缓存，运行时对用户查询做 embedding 检索 Top-K；③小模型分类最终兜底。只把命中的工具注入 Prompt。适用于 Function Calling 和 MCP 两种方式 | P1 | 中 | 无 |
| A-07 | **记忆提取异步化** | 当前记忆提取（facts/preferences/corrections）是同步 inline 调 LLM，改为异步队列。对话完成后将提取任务发到 Redis List，后台 Worker 攒够阈值后批量提取并写入 SemanticMemory。**收益**：用户不感知提取延迟；批处理节省 40-50% LLM 调用成本；提取失败不影响主流程 | P1 | 低 | 无 |
| A-08 | **Langfuse 升级到 v3.x** | 当前使用 v2.95（2025.11），目标升级到 v3.x。**收益**：获得 LLM-as-Judge 评估器（代码化管理评估 prompt）、Code evaluators（Python/TS 写自动化评估）、CI/CD 集成（GitHub Actions 自动跑评估）、Experiments 实验管理。**风险**：数据库 schema 变更需迁移，自部署要规划停机窗口 | P2 | 中 | 无 |
| A-09 | **引入传统小模型（L1）** | 项目中当前没有使用 L1 级别模型（BERT 分类器/NER），纯靠规则 + L2 Embedding + L3 LLM。补充 L1 模型用于低成本的文本分类、实体提取、意图路由等场景。**收益**：替代部分 LLM 调用，降低成本；比规则更灵活。**选型建议**：BERT-base 级别的文本分类器 / NER 模型（~100MB，CPU 可跑，10-50ms/次） | P2 | 低 | 无 |

## B. 认证与安全

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| B-01 | JWT 认证 | 登录注册 + Token 鉴权，替代无认证状态 | P0 | 中 | 无 |
| B-03 | CORS 加固 | `allow_origins=["*"]` 改为白名单 | P1 | 低 | 无 |
| B-05 | 操作审计日志 | 记录关键操作（上传、删除、查询） | P2 | 中 | 无 |
| B-06 | **系统安全与审计（后期整体考虑）** | 上线后期统一评估。**审计**：全链路审计（LLM 调用/检索/生成 span 记录）、审计日志保留策略与合规留存（如 180 天）、用户操作可追溯；**安全**：Prompt Injection 防护（用户输入注入检测）、敏感信息脱敏（文档/回答中的身份证号/账号等）、知识库与文档级访问控制（结合 B-02 租户隔离）、模型输出内容安全过滤（有害/违规内容拦截）。**原则**：审计与安全能力应在核心链路稳定后补齐，避免早期过度设计；对外暴露前至少完成操作审计（B-05）与敏感信息脱敏 | P2 | 高 | B-01, B-02, B-05 |

## C. 数据库与持久层

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|

## D. 代码质量与工程化

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| D-01 | Python type hints 全覆盖 | 当前几乎无类型标注 | P1 | 中 | 无 |
| D-02 | CI/CD 流水线 | GitHub Actions 自动运行测试 + lint | P1 | 低 | 无 |
| D-06 | DDD 领域驱动重写业务层 | 按限界上下文拆分模块（知识管理/对话/检索），引入聚合根和领域事件 | P3 | 高 | 无（D-03 分层已完成） |
| D-08 | **文档锚点闸门自身的两处边界缺陷**（本次有意保留、待修） | ① **`UnicodeDecodeError` 不捕获 → 可挡死全员提交**：`src/cli/check_docs.py` 的代码快照只 `except OSError`，而 `UnicodeDecodeError` 是 `ValueError` 子类。该钩子 `always_run`、每次提交全量跑 ⇒ `src/` 下出现**任一**非 UTF-8 的 `.py` 就让**每次**提交崩掉。**注意触发面**：改前"命中即短路"（可能根本读不到坏文件），改成单次快照后**必读全树** ⇒ 触发面被**扩大**（存在坏文件时每次符号查询都抛）。修法约 1 行（`except (OSError, UnicodeDecodeError): continue`）；本次不改是因为它会破坏 change `check-docs-symbol-lookup-perf` 的"改前/改后 0 差异"等价性证明。② **`#` 截断 → 只产生 warn 噪声**：`line.split("#", 1)[0]` 把**字符串字面量里的 `#`** 也当注释起点、该行后半被丢弃 ⇒ 若某符号只出现在其后，会被误报为"未检索到"（失败方向单一，只能 `True → False`，不影响退出码）。缺陷类别已登记 `docs/agents/defensive-patterns.md`「开发期闸门（pre-commit）」 | 2026-09-23 check_docs 提速时发现（⑥ 代码评审指出 `(b)` 的触发面被扩大，原"保留现状"表述不准确） | P2 | 低 | 无 |

| D-09 | **四个已核实的死字段**：`AgentState.timings` / `_token_usage` / `model_used` / `is_fallback` | 四字段在 `src/agents/graph/state.py:43/44/49/52` **只有声明、全仓零读写**（`tests/infra/llm/test_token_usage.py` 测的是另一个模块 `token_usage`，与本字段无关；`agent_service` 里的 `model_used` / `is_fallback` 是 `_StreamCapture` 的**同名属性**，与 state 字段无关）。核实于 2026-09-28（`one-loop-two-roles` 立项调研，当日复核时把清单由两个修正为四个）；`one-loop-two-roles` 已落地（2026-09-29）但**有意未清理**该四字段（手术刀原则），故仍单列 | `one-loop-two-roles` 立项调研 + 复核 | P3 | 低 | 无 |
| D-10 | **`is_fallback` 恒为 `False`（既有失真）**：`agent_service.py:766` 构造 `SSEModelInfoEvent` 时**硬编码** `is_fallback=False`，前端「回退模型」标示永远不会出现 | 与 D-09 的 `AgentState.is_fallback` 是两个不同位置；此处是**事件构造处写死**。需先确认产品是否要暴露 fallback 标示，再决定是补齐判定还是删字段 | 2026-09-28 `one-loop-two-roles` 死字段复核时发现 | P3 | 低 | 无 |
| D-11 | **临时取证埋点清理待办**：「长时间静默」定位用的 4 组 `[retrieval]/[agent] TIMING` 埋点尚未清理 | 分组：① `src/agents/graph/agent_node.py`（TTFB 捕获 + `TIMING` 日志）**已随 `one-loop-two-roles` 删除**（2026-09-29，埋点连同承载它的旧循环函数一并消失；原计划保留的 `first_chunk_at` 因观测层不可得而未保留，`completion_start_time` 退化见 `docs/adr/0016`）；② `src/agents/tools/web_tools.py`（2 处）；③ `src/infra/search/tavily_client.py`（4 处）；④ `src/services/agent_service.py`（静默看门狗）。②③④ **不在 `one-loop-two-roles` 范围**，待定位确认后整块删除 | 2026-09-28 `one-loop-two-roles` 迁移影响面审计 | P3 | 低 | 无 |
| D-12 | **`QueryRouter` 处于「生产死、CLI 活」灰区**：`src/infra/search/query_router.py` 的 `QueryRouter` 类 + `tests/infra/search/test_query_router.py` 目前**只有 CLI** `src/cli/check_abstain.py:160` 在用；生产图内**无** `classify`/`query_router` 节点（`grep classify_node src/agents/graph/` 无结果，one-loop-two-roles 重构后意图判定已并入主 agent）。⚠️ 同模块的 `aggregate_kb_entities` / `SUGGESTIONS_MAP` **仍被生产使用**（`rag_tools.py:31` / `ask_tools.py:21`），删整个文件会误伤。**处理**：不擅自删，先登记；后续若碰到意图路由/分类相关的 change，评估是否把 `QueryRouter` 并入或退役。相关陈旧引用（`models.py:181` docstring、`state.py:92` 注释）已由 `provider-compat-layer` 修正 | 2026-10-11 `provider-compat-layer` 立项调研（核查 `classify` 角色用途时发现） | P3 | 低 | 无 |

| D-13 | **`prompt-layering-and-domain-binding` 的「人工层回归项」未跑（阻塞于 `e2e-playwright-regression`）** | 该 change 的 §3 要求把「绑 KB + 选预设 → 答案仍带 `[n]`」作为**端到端症状级回归**（三层验收里唯一的"最终形态"层）。**实情**：`e2e-playwright-regression` 尚未实施（0/30，`e2e/` 目录**不存在**），其所称"已固化"只是 `proposal.md` 的文字登记、**无测试文件**。故本 change 归档时该项**未跑**，转登记于此：待 `e2e-playwright-regression` 落地后补跑。另两条非阻塞遗留见 `archive/2026-10-12-prompt-layering-and-domain-binding/` 内 tasks 的说明 | `prompt-layering-and-domain-binding` 归档遗留（2026-10-12） | P3 | 低 | change `e2e-playwright-regression` |
| D-14 | **评测逃生门的替代实现** | 桥不可用时启用：DeepEval 只做采样与用例构造、项目侧直调 ragas 0.3 线评分。**边界已预留**（`eval_deepeval._bridge_test_case` / `eval_scoring.build_metrics` / `eval_deepeval.score_rows`，见 `deepeval-bridge-eval` design D10）；**首次启用时**才实现并冒烟。触发条件：桥产出 NaN 比例 > 阈值且重试无效，或桥在目标环境无法稳定运行 | `deepeval-bridge-eval` D10 / spec「A bounded escape hatch」 | P2 | 中 | 桥不可用且需继续评测时 |

## E. 文档处理增强

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| E-01 | 专用财务文档 chunker | 对财报、招股书等优化分块策略 | P2 | 低 | 无 |
| E-03 | OCR 支持 | 扫描版 PDF 的 OCR 识别 | P3 | 高 | 无 |
| E-05 | **Token 计数分块** | 当前 CHUNK_SIZE 按字符数设置，改为按 Token 数（接入 DashScope tokenizer）。中文 512 字符约 300-400 tokens，导致 chunk 偏小。与嵌入模型对齐后分块更准确 | **P0** | 低 | 无 |
| E-09 | **文档后台处理重试机制** | 当前 `asyncio.create_task()` fire-and-forget，向量化失败（DashScope 超时/PostgreSQL 写入失败）直接标记 `failed`，无重试。增加指数退避重试（初始 100ms，倍数 2，最大 5s，加随机 jitter），最多重试 2-3 次后标记 `failed`。**不重试**：解析失败（文件损坏）、扫描件检测（不可恢复）。**只重试**：网络超时、API 限流（429）、向量库临时不可用等可恢复错误 | P1 | 低 | 无 |
| E-10 | **分块质量评估的口径漂移与规格缺口** | ① **权重不一致**：归档 spec 写 `0.40 × structure_integrity.score + 0.30 × sbr.score + 0.30 × granularity_cv.score`（`docs/openspec/changes/archive/2026-07-17-retrieval-and-chunking/chunk-evaluation-system/specs/chunk-quality-scorer/spec.md:83`），代码是 `0.45 / 0.45 / 0.10`（`src/chunking/scorer.py:410-414`），且**无任何测试断言权重** —— 改哪边都会改变 `overall_score` 数值、进而影响历史「分块评分」的可比性，须先裁定以哪边为准。② **主规格缺失**：`docs/openspec/specs/` 下**没有** `chunk-quality-scorer`，该 change 归档时未把 delta 同步进主规格，所以三个模块的字段形状至今只存在于归档里（`eval_detail` 的实际形状已补进 `docs/agents/api_contract.md` §2.2.1，但那是接口契约、不是能力规格） | 2026-09-22 排查「详情」弹窗缺陷时顺带发现 | P2 | 低 | 无 |

## F. AI / Agent 能力

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| F-01 | MCP 工具集成 | 接入 `mcp_server`，获取财务/税务/法律工具能力 | P1 | 低 | 无（mcp_server 已独立部署） |
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
| F-16 | **（L1+L2）RDS 托管化切换**：把 PostgreSQL 从本地实例切到 RDS 托管实例。清单：① 确认 RDS 侧扩展清单与 `CREATE EXTENSION` 的账号/权限（本地已实测 `vector` **不是** trusted 扩展，应用账号会被拒、须超级用户建，托管侧须先确认同项权限）；② 在 RDS 上预建应用库与最小权限账号；③ 改 `docker-compose.prod.yml` 指向 RDS；④ prod 安装与部署验证。**背景**：`postgres-storage-consolidation` 的 P1（2026-09-19 用户决定）本轮只做本地，远程 RDS 不处理、prod 不安装 | postgres-storage-consolidation P1 遗留项 L1+L2 | P1 | 中 | postgres-storage-consolidation（P1 已完成） |
| F-22 | **（P2 遗留，门禁盲区）`alembic/env.py` 未传 `compare_server_default=True`**：Alembic 该参数默认 `False` → autogenerate **根本不比较**任何表的 `server_default`，**所有表**的默认值漂移都不会被发现（全局盲区，不只 `chunks`）。P2 仅对 `chunks` 的 `source`/`page`/`metadata` 三列用只读断言旁路覆盖（Ruling P2-R11），`alembic/env.py` 本身未改 | 单独变更打开开关；打开后可能一次性暴露既有表的默认值漂移，需先评估影响面再决定修漂移还是调模型。**P4 明确不做，属独立变更**（打开后可能一次性暴露既有表的默认值漂移）——P4 终审把「有归属 P4 的门禁项」显式登记后**结清**，本条不再作为本变更的门禁项（原 F-26 已并入本条） | postgres-storage-consolidation P2 遗留项 | P1 | 中 | 无 |
| F-30 | **（P3 遗留）词法路质量的判据缺口**：词项命中探针（`docs/tmp/p3-lexical-probe-2026-09-19.md`）的判据是**集合成员**（"含该词项的 chunk 是否进 top-k"），而过滤臂的候选集恒 ≤ k ⇒ 该口径**结构上测不了排序质量**（C 组 `ts_rank`/`ts_rank_cd` 的零差是必然），未过滤的 BM25 臂的差值**可归因于分词造成的排序差异**，但命中判据是原始正文包含、不含排序质量 ⇒ **不能读作质量差**。因此"生产形态 `jieba-tsrank` 在 A 组最低"这一实测事实**不可归因于分词质量**，"替换有收益/无收益"均未被本探针建立 | 需要 k ≪ 池规模的新判据，或等语料到位后的端到端 RAGAS 评估；在此之前**不得**把探针数字当质量基线。来源：postgres-storage-consolidation P3 遗留项（探针结论） | P2 | 中 | 无 |
| F-33 | **（P2 遗留）`base` 段缺「允许对已提供材料推理 / 计算 / 汇总 / 翻译」的正向句**：本变更 delta 有 ADDED Requirement「允许基于已检索材料推理计算」（`specs/prompt-composition/spec.md:345`），但落盘的 `base-financial.yaml` / `base-general.yaml` **没有**对应正向句（`grep "推理\|计算\|汇总\|虚构" src/config/prompts/` 只命中 `sources.yaml` 的"不要虚构来源"）。同时 `prompt-mapping.md:119` 写的是"须在 spec 的 **MODIFIED** 要求中显式改写"，而实际落成了 **ADDED**。**结果**：语义反转（删掉"不计算"禁句）在 spec 侧成立、在 prompt 侧只体现为"没有禁令"，未正面声明"允许"。P2 收口时**刻意不改** —— 改模板会让同期采集的 P2 度量失效，改 spec 需独立评估 | 2026-09-22 `prompt-layering` P2 的 T6 复核期发现 | P2 | 低 | 无 |
| F-34 | **（skill 改造回退残留）3 个外部 skill 的 2 项"零工具语义"改造**：2026-09-26 装进来的 `competitive-landscape` / `financial-statement-analyzer` / `market-sizing-analysis` 已于 2026-09-27 回退为**上游原样**（`references/`、`examples/` 已拷回，正文不再内联；description 语言、`version` 字段均还原）。**仅剩 2 项依赖"fork 子代理零工具"语义而保留**：① 三份 description 尾句「材料须由主 agent 预检索一并传入 task」（`registry.py:88` 会把它写进 `delegate_task` 的 schema，是当前唯一能告知主 agent"先检索再委派"的 skill 侧载体）；② 三份均未声明 `allowed-tools`（刻意零工具，被守卫 `tests/agents/skills/test_first_batch_skills.py::test_fork_prompt_must_not_mention_tool_names` 锁死）。**这两项在"fork 子代理工具面默认继承执行者"落地后即失去依据**，须一并移除（① ）与重定（② 连同守卫）。⚠ 附带证据：base 段 `tools.yaml` 的 `tools-delegate` 已写「委派深度分析任务时，先把已检索到的材料随 task 一并传入」，故 ① 与 base 段**语义重叠**，若不想等新 change 也可提前按"一事一档"去重。**2026-09-27 与另一会话（主从 Agent 架构 / 契约通则）约定**：另开 change `fork-tool-face-by-inheritance` 承载这 2 项（排在 F-12 之前，占 ADR-0016）；对方保留 `delegate-task` spec 的「委派工具形态」requirement，我方只改工具面 delta；文件读取工具仍归 F-12，继承工具不覆盖 | 2026-09-27 回退外部 skill 改造时登记 | P3 | 低 | change `fork-tool-face-by-inheritance`（待建；ADR-0016） |
| F-35 | **`context_source` 是否暴露到 `/skills` 面板（待定）** | skill 的 `context` 取值来源当前只写进运行时对象 `SkillRecord`（`context_source` 字段）与加载期 `skill resolved` 日志；`/skills` 面板看不到，「未声明但正文超限、加载期自动改 fork」这一判定对作者不可见。**待定**：是否把 `context_source`（含 `auto_oversize`）暴露到面板，让作者知道自己写的 skill 被自动改了承载方式 | `skill-execution-and-delegation` P1（Task 8 收口时登记） | P3 | 低 | 无 |


| F-37 | **迭代触顶 → 空回答且状态 `complete`（既有缺陷，用户可见）** | 宽泛问题在已绑 KB 的会话里触发主循环触顶：`iteration limit`(WARNING) → `completeness check answer_len=0` → `format done citations=0`，assistant 行落库 **`status=complete` 但 `content=""`**，前端"正在思考…"**永久停留**。实测会话 `sess_1790296132490_t5k03t`（trace `trace_25ceadc2-…`，2026-09-25 冒烟）；全库 29 条 assistant 中 3 条空 `content` 且 `status` 全是 `complete` ⇒ **`status` 不足以表达"这轮到底有没有产出回答"**。**根因**：触顶时末条是含 `tool_calls` 的 `AIMessage`（正文为空），答案提取得到空串，且**无任何兜底文案**。**归属**：兜底与状态表达的修复归 `agent-round-budget`（其 apply 排在 `one-loop-two-roles` 之后）；`one-loop-two-roles`（2026-09-29 已落地）只负责**把该语义钉住**（`agent-loop-observability` 要求 + 断言"末条含 tool_calls、answer 可为空"），不修。触发条件很日常：宽泛/多轮追问会让模型反复 `reretrieve` 到触顶，非边缘 case | 2026-09-25 前端冒烟；2026-09-28 `one-loop-two-roles` 立项时核对 | P1 | 中 | `agent-round-budget`（在其之后 apply） |
| F-38 | **历史摘要的「按需回读原文」工具（Phase D 候选）** | 跨轮历史摘要（change `cross-turn-history-summary`，2026-10-08）把模型侧上下文压缩为摘要，而**用户回放仍是完整原文**（摘要刻意不落 MySQL）。由此留下能力缺口：当用户引用「你上次说的 X」而 X 已被摘要掉时，模型**无法回读原文**，只能否认或请用户复述（当前仅靠摘要段的让位声明兜底）。**候选方向**：给主 agent 一个「按会话历史检索 / 回读原文」的工具，与 Phase D 的「工具结果外置、可回读」同源。 | `cross-turn-history-summary` design「已闭问题 3」（2026-10-08） | P3 | 中 | Phase D |
| F-39 | **`covered` 是 Phase D「按需回读原文」的切点锚点（约束型需求）** | 跨轮历史摘要写进 Redis 的 `covered` = 「摘要已覆盖到的**消息条数**」（实现为被丢弃段长度，即累计前缀长度）。它今天没有被任何消费者使用（注入只取摘要正文），但 Phase D 的「按需回读原文」工具要拿它当**从第几条开始回读**的切点。⇒ **该字段的语义是上游契约，不得漂移**：任何让 `covered` 与"摘要实际覆盖范围"不一致的改动，都会让回读切错位置。**历史教训（2026-10-08）**：首版误写为 `previous_covered + len(discarded)`（把累计前缀当增量），写进 Redis 的值会随会话逐轮膨胀；整支终审抓出并修正为 `len(discarded)`，同时删掉了变 dead 的 `previous_covered` 形参。落地 Phase D 时应把「`covered` 的语义」写进规格而非只留在代码注释里。 | `cross-turn-history-summary` 整支终审（2026-10-08） | P2 | 低 | F-38 / Phase D |
| F-40 | **「被丢弃段」的 token 量宜增量维护（每轮 O(n) 重数）** | 跨轮历史摘要在**每回合正常完成**后都会：① `get_history_async` 读**全量**会话历史；② `should_schedule` 再对**整个被丢弃段**逐条 `count_tokens`。会话越长这两步越贵（被丢弃段随会话单调增长）。可改为在会话维度缓存「已累计的被丢弃段 token」，新回合只对**新增**的溢出部分计数 ⇒ 摊还 O(1)。**当前不紧迫**（单 worker、文档规模千级上限），但属可预期的线性退化点，且它与 F-39 共用一个"累计边界"概念，宜一并设计。 | `cross-turn-history-summary` 整支终审 Recommendation（2026-10-08） | P3 | 中 | F-39 |
| F-41 | **报警 prompt 未喂"指标值"** | 报警轮喂给模型的输入只含**告警名 / 等级 / `annotations` 文本**；而 Grafana 原生载荷里的**指标数值**（`alerts[].valueString`、`alerts[].values`、以及顶层 `message` 里的 `Value: F=1835, G=1`）**一个都没进 prompt** ⇒ 模型只知道"某量上升"，不知道具体值、更谈不上量级判断，结论只能泛泛而谈。改法：`AlertTexts.QUERY_TEMPLATE` 增加一行「指标值：{values}」（`AlertEvent` 增 `values` 字段；`valueString` 优先、缺失时由 `values` 字典渲染；两者都无则填占位文案，保持模板行数固定）。**2026-10-13 曾试做一版并按要求回退**（当时只讨论、不动代码）。 | alibaba-alert-notify 上线实测（2026-10-13） | P1 | 低 | 无 |
| F-42 | **报警的"指标含义"注入到哪：skill / 预设智能体 / 输入模板** | 模型看到 `F=1835` 但不知道 `F` 是什么指标。三种载体的取舍：**① 预设智能体**——最确定（绑上即在 system prompt 里），但报警轮**当前根本没绑**（`_run_analysis` 调 `start_turn` 时未传 `agent=`，design D11 明确"不绑定预设"），要用得先把它传进去；且指标字典塞进 system prompt 会让预设臃肿、并连带其工具面。**② skill**——机制上可行（报警轮会自动加载 skill，日志可见 `skill resolved`），但靠**描述匹配**、不保证命中，且以 **fork 子代理**展开（占上下文、多一次子代理）。**③ 输入模板 / 提示词段**——报警轮是固定场景，输入模板是唯一入口 ⇒ **必然生效、零匹配风险**，推荐。**待定**：指标清单（`F`、`G` 等各自含义/单位/阈值）需用户提供。 | alibaba-alert-notify 上线实测讨论（2026-10-13） | P2 | 低 | F-41（模型得先看得到指标值） |
| F-43 | **只读 Grafana 查询工具（让报警分析取实时数据）** | 想让模型"去看 Grafana 的数据"：**打开页面不可行** —— dashboard / alert 页是 JS 渲染的 SPA，抓回 HTML 是空壳；要走 **API**：`/api/v1/rules`（规则与阈值）、datasource proxy 的 **PromQL**（最近曲线 / 当前值）。三条硬约束：① 只做**白名单路径 + 只读**，既防 SSRF 也防越权；② 凭据用 **Grafana Service Account Token（只读角色）**，**不用账号密码**（后者等于把某人的登录态交给容器：无法单独撤销、能登控制台改东西、审计上算作真人操作）；③ 凭据只进**工具闭包**（`src/config/` 读入），**绝不进 prompt** —— 否则模型会把它复述到群里（与 token 进日志是同一类问题，见 D3 的脱敏）。前置待定：到底要拿哪种数据（"指标还在涨吗"→PromQL；"规则阈值是什么"→rules；"相关日志"→那是 Loki/SLS，不属 Grafana）。 | alibaba-alert-notify 上线实测讨论（2026-10-13） | P2 | 中 | 无（独立新工具，宜单开 change） |
| F-44 | **共享知识库缺「公司/文档过滤」+ 精排分数扁平 ⇒ 跨公司噪声混入 top-5** | **实测（2026-10-15，评测 Q14 / trace `eval_83e85e1ec7c3`，腾讯题）**：喂给裁判的 20 条 context 里，第 `[9]` 条来自 **东软**（`neusoft_2024_q3.pdf`，精排分 **0.4534**，在其所属那次检索里排 **第 4/5**）——不是高分误召，是 `top_k=5` **硬凑**出来的。**两条根因**：① **无过滤** —— `retrieve_kb`（`src/agents/tools/rag_tools.py`）只吃 `query`/`top_k`/`kb_id` 做 hybrid + 精排，**不按 company/doc 过滤**；而知识库是**多家混装**（`tencent_2024_annual.pdf` / `neusoft_2024_q3.pdf` / `neusoft_2025_q1.pdf` / 灿坤年报）。② **精排区分度低** —— 该题四次检索 `score_max` 仅 **0.590 / 0.525 / 0.609 / 0.554**，且**同一次内分数差仅 ~0.09**（如 0.525→0.438，其余皆 0.39~0.53）⇒ top-5 的取舍近乎任意。**影响**：无关块占位直接压低 `context_precision`；与「同题连搜 4 次、query 越写越长」的低效**同源**。**候选修法**：① 按问题实体做 doc/company 过滤 —— **能力已在仓库但检索路径未接线**（`src/infra/search/document_entity_extractor.py` 抽 company、`src/infra/search/query_router.py` 有 company 路由；后者现状「生产死、CLI 活」，见 D-12）；② 分数扁平时不硬凑 `top_k` / 提精排阈值（见 F-05、F-10）；③ 跨轮去重（评测侧另记于台账）。⚠ **先判性质**：若「多公司共享同一 KB」是有意设计，过滤应走**租户/文档维度**而非硬编码公司名 —— 需产品确认 | 2026-10-15 RAGAS 评测 context 溯源（Q14 trace 与 `retrieval_details` 双证） | P1 | 中 | 无（与 D-12 强相关） |

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
| G-09 | **前端契约消费无自动化护栏** | 「后端形状变了、前端按旧形状读」那类缺陷，现有四层闸门**一层都抓不到**：契约测试只测后端、doc 闸门只查文档、RAGAS 测质量、pyright 不跨语言。唯一能覆盖的是 `e2e-playwright-regression`，而它**0/30 尚未实施**。**建议**：该 change 落地时把「知识库页详情弹窗可打开且渲染出评分」列为首批用例之一（它正是"端到端最终形态"类断言） | 2026-09-22 修复 G-08 时确认 | P2 | 低 | e2e-playwright-regression |

## H. 可观测性与运维

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| H-01 | **Langfuse 接线归档遗留：CLI trace 落库未实跑** | `langfuse-trace-wiring` 的 DoD ⑧「CLI 的 trace 在 `--gate` / 异常退出路径下也能落库」**未实跑**，归档时仅有结构证据（`flush_tracing()` 位于 CLI 的 `finally`；`tests/test_tracing_order.py` 断言 CLI 路径的 `configure → flush` 调用序）。**要做**：跑一次 `--gate` 与一次异常退出的 CLI 评测，确认 Langfuse 端能查到 trace。（2026-10-15 订正：原文附带的「根因」——白名单指向已不在库的文档、`data/deepeval/` 无测试集 ⇒ `--generate` exit 1——**已不成立**，该段已删；现状见 `data/deepeval/testset/`（`RAGAS_DOC_WHITELIST` 已随出题路删除）） | langfuse-trace-wiring 归档遗留（2026-09-24） | P2 | 低 | 无 |
| H-02 | **Langfuse purge 定时接入（dev + prod）** | 删除后端已实现（直连 Langfuse PG 跑 SQL，ADR-0013；`src/cli/purge_langfuse_traces.py` + `src/infra/llm/langfuse_purge.py`，真删已验），但**定时任务未接入** —— 宿主 cron / systemd timer / compose 定时服务三选一，且 **dev 与 prod 各自落地**。ADR-0011 复查条件③要求「清理与接线一并落地」，属接线变更的欠账 | langfuse-trace-wiring 计划外部署动作 ② | P1 | 低 | 无 |
| H-03 | **prod 侧 Langfuse 落地** | 「改模板 ≠ 改到机器」：需在 prod 机上同步 `.env`（含 `COMPOSE_PROFILES=langfuse` 与三个 `LANGFUSE_INIT_PROJECT_*`；`INIT_*` 与 `LANGFUSE_*` 两对键须成对相等），确认 `langfuse-web` 在跑，随后验证 trace 可查 | langfuse-trace-wiring 计划外部署动作 ④ | P1 | 低 | 无 |
| H-04 | **部署档 `data/deepeval` 挂载无消费方（可清理，历史登记）** | `docker-compose.image.yml` 的 `./data/deepeval:/app/data/deepeval` 在部署机上**没有任何消费方**：①评测 CLI 只在开发/宿主侧跑，不在部署机跑（主分已改 DeepEval 原生四指标，镜像里不再有 RAGAS 依赖）；②读链路（前端 KB 徽标 → `POST /api/kbs/eval/latest`）走 **DB 表 `eval_report`**（`app_service.get_latest_eval_report` → `_eval_repo`），完全不碰文件；③`report_path` 字段只被**存**进 DB、只被**回显**，全仓无人按它打开文件（已 grep 核实）。⇒ 该挂载与 `deploy.sh` 里的 `mkdir -p data/deepeval` 均可删，宿主必须提供的文件从 5 项收敛到 4 项。**2026-09-30 决定：暂不清理，仅登记；2026-10-17 复核：评测已不依赖 ragas，挂载仍无消费方** | 部署链路审计（2026-09-30） | P3 | 低 | 无 |
| H-05 | **`POST /api/ragas/generate` 是孤儿端点（已关闭：恒 410）** | 历史问题：该端点在 `main.py:201` 注册、`src/api/README.md:23` 有文档，但**全仓无调用方**；出题实现已随 RAGAS 退役整文件删除，镜像里不再有 ragas 依赖。**2026-10-17 起**：路由保留、实现已删，调用恒返回 **410**（不再 500，也不存在 ragas 懒加载）。剩余选项只有「保留恒 410」或「从生产路由摘掉」；**若将来要清理，与 H-04 属同一批** | 部署链路审计（2026-09-30），2026-10-17 关闭 | P3 | 低 | 无 |
| H-06 | **HTTPS 选型未定** | 目标机 nginx 只有 80（另有 Langfuse `:3000` 对外，见 H-07）、安全组放行 **22+80+3000**，demo 当前为**纯 HTTP** 口径。待定：nginx 443 + 证书（自签 / Let's Encrypt）vs SLB 终止 TLS。⚠ 明文 HTTP 下前端**安全上下文专属 API 不可用**（已踩：登录页 `crypto.randomUUID` 报错，见 `defensive-patterns.md`「前端（浏览器 API）」）| demo-deploy-2c8g 未决项（2026-10-04） | P2 | 中 | 无 |
| H-07 | **Langfuse 去留未定** | 目标机栈内含 `langfuse-web`（`docker-compose.image.yml`，限额 256m）。**2026-10-09 起改为对外暴露 `:3000`**（ADR-0019：端口由 `127.0.0.1:3000:3000` 改为 `3000:3000` + 安全组放行 3000；对外地址 `NEXTAUTH_URL` 与自助注册关闭均**在部署档里硬编码**，不走 `.env`）。待定：长期是否保留；是否再加网络层认证（nginx `auth_basic` / IP 白名单）或改走 nginx 反代 + HTTPS | demo-deploy-2c8g 未决项（2026-10-04），2026-10-09 更新 | P3 | 低 | H-06 |
| H-08 | **`docker-compose.prod.yml` 与目标形态的关系未定** | 目标机实际运行 `docker-compose.image.yml`；`docker-compose.prod.yml` 从未在目标机验证、档位（合计 14.25 GB）会 OOM。待定：废弃 / 删除 / 与目标形态对齐 | demo-deploy-2c8g 未决项（2026-10-04） | P3 | 低 | 无 |
| H-09 | **备份口径未定** | MinIO 里的原始上传文件是**唯一不可再生源头**、`postgres_data` 卷承载元数据与向量 —— 二者当前**均无备份**。待定：频率、保留期、存放位置（是否上 OSS）| demo-deploy-2c8g 未决项（2026-10-04） | P1 | 中 | 无 |
| H-10 | **托管化未定** | 当前为单机自建（PG / Redis / MinIO 与 app 同机，单点）。待定：是否迁 RDS / Redis / OSS / SLB | demo-deploy-2c8g 未决项（2026-10-04） | P3 | 高 | H-09 |
| H-11 | **版本回滚未生效（demo，2026-10-05 起搁置待查）** | 现象：从版本页回滚到旧版本后，页面 `brand-version` **没退回**（`v0.1.1` 应回 `v0.1.0`）。**已确认的机理（避免下次重走弯路）**：① 回滚 = 用**旧部署单** → 下发**旧制品** → 制品 compose 里烘着**旧 tag**；② `deploy.sh:342` 从**制品里的 compose** 解析 tag（**不从环境读 `BUILD_NUMBER`**）—— 这是正确设计，若改成"从环境读"会让回滚**永久拉最新版**；③ ACR 是 **Docker Registry v2**，镜像**不能 curl**（实测 `https://…/v2/` 返回 `401` + `WWW-Authenticate: Bearer`；`http://` 的 80 口直接超时）—— 曾试图"传镜像下载链接"失败。**待查点（按性价比排序）**：① 部署页「启动槽位」是否真的消费 `${package_download_path}`，是否残留"自己下载 / 写死 `/package.tgz`"；② 对比**首次发布 v0.1.1** 与**回滚**两次执行的日志里下载 URL（`artifactName` / `version`）是否不同 —— 相同 ⇒ 平台没换制品（问题在部署单/回滚配置）；不同 ⇒ 脚本没消费；③ 回滚后目标机上 `deploy/nginx/html/chat.html` 的 `brand-version` 实际值（区分"旧制品没落地" vs "落地了但浏览器/nginx 缓存"）；④ 解压目标路径是否与 `deploy.sh` 的 `ROOT`（`/opt/www/corporate_rag`）一致 | demo 部署回滚验证（2026-10-05） | P2 | 中 | 无 |
| H-12 | **无指标/告警出口：Langfuse v2 无 Prometheus 端点，应用侧也不暴露 metrics** | **为什么需要**：上游可用性故障（403 额度耗尽 / 429 穷尽）要"分钟级人工介入切换"，但**没有叫醒人的通道**。**实测（2026-10-16，本地 `langfuse/langfuse:2.95.11` 容器直连，逐路径校准 401=存在需鉴权 / 404=不存在）**：① **`/metrics` = 404** ⇒ **无 Prometheus 端点**，不能直接接 Grafana 的 Prometheus 数据源；② **`/api/public/metrics/daily` 存在**（需 Basic auth），返回**日粒度**聚合（`date / countTraces / countObservations / totalCost / usage[]{model,inputUsage,outputUsage,totalCost,...}`，实测有 12 天数据、`meta.totalItems=12`）⇒ 只能做**成本/用量趋势**，**天粒度不能用于分钟级告警**；③ `/api/public/health` → `{"status":"OK","version":"2.95.11"}`（**无需鉴权**，近实时，但**只表示 Langfuse 自身活着**，**不反映模型调用失败**）；④ `/api/public/metrics`（旧路径）、`/api/public/v2/metrics`、`/api/metrics` 均 **404**（不存在）。**应用侧**同样无 metrics 端点（`grep prometheus\|/metrics` 无结果）；`src/api/alerts.py` 是**入站**（接收阿里云/Grafana 告警并跑 Agent 分析），**不是出站**。**方向（用户 2026-10-16 告知）**：后续接入**阿里云 Grafana** + **阿里云日志（SLS）**；**最小可行出站源** = 把已有的 `logger.exception`（`logs/error_*.log`）接进 **SLS 日志告警**（近乎纯配置），或暴露一个健康/指标端点由云监控打点。**依赖方**：change `timeout-and-degradation-hardening` 的 **D9-(A) 档**（"快速失败 + 明确告知 + 人工切换"）**依赖本项** —— 没有触发源，"分钟级可介入"不成立 | 2026-10-16 排查 A-03/A-05 的"长时不可用"方案时实测 | P1 | 低 | 无 |

## I. 评测题集

- **v7 题集扩文档**：v6 冻结在 v5 的两份文档（50/51 块），v7 再扩到全量 4 份（161 块），保持同出题器以单独归因。

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

## 最佳实践参考

> **已迁出为独立归属文档 → `docs/agents/best-practices.md`**（业界做法 ↔ 我们的现状对照表，按能力域分节）。
> 本节不再承载正文；各 ❌/⚠️ 状态在本池中以 `A-xx` / `E-xx` / `F-xx` / `H-xx` 条目承接。

---

> **A-03 / A-05 收尾（2026-10-16，change `timeout-and-degradation-hardening` P1+P2+P3 已落地）**：
> **A-05（超时/重试）** —— 显式超时（按角色、env 可覆盖）、关 SDK 重试、本层有界重试 + 留痕、
> 精排三层超时嵌套 + 启动期 fail-fast、**本地 30 次真实调用取分位并回填 D11**（结论：维持现值，余量 2.2×~42×）
> 均已完成；**余项**：`classify`/`summary` 与 `embedding` 入库档**仍无实测分位**（前者 `model turn` 不带 role、
> 后者本机未入库）⇒ 保留保守值、生产分位仍待。
> **A-03（降级）** —— 降级动作矩阵、单一 `degraded` 事件留痕、取值集合表约束、
> 生成任务级 idle 兜底（档 1）均已完成；**余项**：idle **档 2（取消/降级）**、
> **熔断 A-02**、**限流 A-01**、**告警出口 H-12** 仍为 follow-up（见 `tasks.md` §5）。
> ⚠ **本变更仍在途**：§4.4 新 ADR 阻塞在 `deepeval-bridge-eval`（其新 ADR 编号未落）。

## 实施路线图

基于 2025-2026 年业界调研和投入产出比分析，建议分四个阶段推进：

### 阶段 1：检索质量奠基（P0，1-2 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 1 | E-05 Token 计数分块 | 基础对齐，后续所有检索优化的前提 |
| 2 | A-02/A-03 熔断+降级 | 生产可靠性的基础保障 |
| 3 | F-04 Query Rewriting | 提升口语化查询匹配率 |

> 2026-10-15 订正：原阶段 1 的 `E-07 Hybrid Search` / `E-06 分块质量监控` / `E-08 Parent-Child 分块`
> 三项**均已落地**（`src/rag/retrieval.py` + `src/rag/fusion.py`；`document_service.validate_chunks`；
> `src/chunking/strategies/parent_child.py`），故从路线图移除。

### 阶段 2：核心能力增强（P1，2-4 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 4 | F-05 Cross-Encoder Rerank 参数调优 | 极低成本获精度提升（MRR 0.16→0.75） |
| 5 | F-06 查询分类路由 | 降本增效，简单查询不走完整 RAG |
| 6 | A-04 Token 用量追踪 | 成本可观测 |
| 7 | A-06 Tool Selector | 工具调用前过滤，降低 Prompt 膨胀 |
| 8 | A-07 记忆提取异步化 | 减少用户感知延迟，降低 40-50% 成本 |

> 2026-10-15 订正：原阶段 2 的 `E-02 表格结构化输出` / `E-04 轻量实体关联` **均已落地**
> （`src/chunking/strategies/table_preserving.py`；`document_service` 抽实体注入 chunk metadata），故移除。

### 阶段 3：数据驱动闭环（P2，4-6 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 9 | F-07 Retrieval 质量自评估 | 自动修复检索失败 |
| 10 | F-08 在线 RAGAS 采样评估 | 数据驱动的 RAG 优化闭环 |
| 11 | A-08 Langfuse v3.x 升级 | 获得 LLM-as-Judge 评估器等高级功能 |
| 12 | E-01 专用财务文档 chunker | 按文档类型优化分块 |
| 13 | A-09 引入传统小模型（L1） | 降低 LLM 调用依赖 |

### 阶段 4：智能 Agent 化（P3，6 周+）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 14 | F-01 MCP 工具集成 | 接入 mcp_server 获取专业工具 |
| 15 | F-03 知识图谱（Neo4j） | 深层多跳推理（需前置条件确认） |
| 16 | E-03 OCR 支持 | 扫描件 PDF 支持 |

> 2026-10-15 订正：原阶段 4 的 `F-02 LangGraph Agentic RAG` **已落地**（`src/agents/graph/workflow.py`），故移除。

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
| **实体提取（E-04）** | ✅ 规则提取已落地（`document_service` 抽实体注入 chunk metadata，检索期由 `rag/context.py` 渲染）；可选 BERT NER 未做 | 先规则，不够再加 BERT NER |
| **文档分块 Token 计数（E-05）** | 无（计划做：接入 DashScope tokenizer） | tokenizer 本身就是字典查找，免费 |
| **RAGAS 评估** | DashScope Qwen-max（云端大参数 LLM） | RAGAS 内部调 LLM 算 faithfulness 等指标，保持不动 |
| **Langfuse LLM-as-Judge（A-08）** | 无（计划做：升级后使用） | 建议用小参数的 Judge 模型（如 GPT-4o-mini 级别），降低成本 |

### 关键结论

- **L0 规则**：能做的就不要上模型，免费且精确
- **L1 传统小模型**：当前项目中**没有用到**，如果后续需要（如 NER 实体提取、文本分类器），建议用 BERT 级别的（~100MB），CPU 可跑
- **L2 Embedding/Rerank**：你已经用上了（DashScope text-embedding-v3 / gte-rerank-v2），这是正确的选择
- **L3 LLM 小参数（7B-14B）**：项目中**没有用到**，主要因为 DashScope 的 Qwen-max 效果更好。后续如果需要本地 fallback 可以考���
- **L3 LLM 大参数（云端）**：你一直在用（Qwen-max），项目的核心依赖
