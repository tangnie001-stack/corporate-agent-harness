# 领域词汇表

> 项目规范术语。每个概念只有一个标准叫法，文档/代码/对话统一用此表词汇，避免歧义。

## 核心标识符

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `kb_id` | 知识库唯一标识，UUID 字符串；`""` 表示"不检索"（未绑定 KB，纯对话） | ❌ 传 `kb_name` |
| `doc_id` | 文档唯一标识，UUID | ❌ 传数据库自增 ID（doc_id 是 UUID） |
| `session_id` | 会话标识，用于关联对话历史 | ❌ 传空字符串 |
| `chunk_id` | 分块 ID，`chunks` 表主键，格式 `"{doc_id}:{chunk_index}"` | ❌ 与 `doc_id` 混用（同一文档有多个 chunk） |
| `trace_id` | 请求追踪 ID，格式 `trace_<uuid>`；**同时是 Langfuse 的 trace id**。由 `X-Trace-ID` 头或 `?trace_id` 决定，**属不可信入站输入**，须经白名单校验后才使用 | — |
| `channel` | 会话来源渠道，取值 `web` / `wecom` / `feishu`（见 `src/config/const.py` 的 `Channel`）。写入日志行第 5 段与 Langfuse trace 的 `metadata.channel`；未设置时日志以 `none` 占位 | ❌ 与 `trace_id`/`session_id` 混用 —— 它用于**分渠道筛选**，不标识单次请求 |

## 响应与追踪

- **响应信封**：统一响应包装 `{"code", "message", "data"}`。成功响应由各 handler 显式 `return ResponseModel(data=...)` 产生（`src/api/schema.py` 的 `ResponseModel`）；错误响应由 `src/main.py` 异常处理器（AppError / HTTPException / 兜底）产出。业务层只 `raise` 异常或返回 `ResponseModel`，不 `return JSONResponse`
- **SSE 事件流**：聊天流式输出的事件序列，`status → token → citation → done`

## 日志规范

- **分层前缀**：日志 message 以 `[层名]` 开头标识事件归属，完整前缀集（9 前缀开放登记制）见 docs/agents/logging-rules.md「前缀主表」
- **retrieval_signal**：检索行为信号日志前缀（独立于 `[层]` 前缀的已知例外），类型集与行格式由 `src/core/log_events.py` 的 `Signal` 枚举与 helper 定义；保留原因与两条 grep 模式见 docs/agents/logging-rules.md「已知例外」
- **五级级别语义**：debug/info/warning/error/exception 各语义见 docs/agents/logging-rules.md「级别语义」
- EventSpec 注册表：src/core/log_event_specs.py 中 name/prefix/level/fields 的事件数据表（经 src/core/log_events.py re-export），helper 渲染/路由级别的事实源；Event 枚举与注册表 import 期一致性校验
- token 安全字符集：`^[A-Za-z0-9_./:@-]+$`，命中则日志字段裸写，否则引号 + JSON 转义
- replay drift 对照：replay_trace CLI 用当前配置重放，事件行记录的"当时参数"并排对比并标注差异

## RAG 流水线

- **chunk**：文档切分后的最小检索单元，由 `src/chunking/` 分块策略产生
- **retrieval**：检索，从 `chunks` 表按 `kb_id` 召回相关 chunk（dense 路走 pgvector 余弦距离）
- **rerank**：精排，对召回结果重排，产出 `contexts` 进入 LLM
- **contexts**：精排后拼入 LLM prompt 的上下文片段
- **kb**：知识库（knowledge base），文档与向量的隔离单位
- **dense 路 / 词法路（sparse）**：混合检索的两条支路；dense 路按向量余弦距离召回（PostgreSQL + pgvector），词法路按词项命中召回（PostgreSQL 全文检索：`chunks.tsv @@ to_tsquery('simple', …)`，按 `ts_rank` 降序）。两路结果由 RRF 融合后携带各自名次
- **`lexical_score`**：`ChunkResult` 的词法路得分字段，**与引擎无关的命名**（取代 `bm25_score`）——它来自 PostgreSQL 全文检索的 `ts_rank`（子串兜底时为 0.0），不是 BM25；dense 检索与分页查询时为 None。字段契约见 api_contract.md §4.4
- **`dense_rank` / `sparse_rank`**：结果在 dense 路 / 词法路的排名（0 起），未出现在该路时为 None；融合后仍按路保留（`hybrid-retrieval` 要求「融合结果的来源可辨」）
- **`metadata 回填契约`**：`ChunkResult.metadata` 由「列值 + jsonb 平铺合并」得到（冲突以列为准），至少含 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page` 五个契约键，jsonb 侧原样承载 chunker 全部自定义键（如 `parent_content`）。唯一实现是 `src/infra/db/vector_store/mapping.py::row_to_chunk_result()`；防复发规则见 defensive-patterns.md「派生副本与权威来源分离」
- **父块级去重（parent-level dedup）**：`src/rag/retrieval.py::_dedup_by_parent` 在**精排之后**对候选按 `(doc_id, parent_content 哈希)` 折叠，同一文档内同一父块只保留 `.score` 最高的一条；`parent_content` 缺失的 chunk 按自身保留。目的是让精排窗口不被"同一父块正文的重复渲染"占满。
- **请求级内容折叠（request-level content collapsing）**：在**单个请求**范围内（同一轮对话的多次工具调用之间）把已出现过的来源条目折叠掉，不让同一来源因多次检索而重复占用上下文。命中键为 `(kind, source, page)`——与引用重建（`src/agents/graph/nodes.py`）所用的去重键一致，故折叠后的池与最终引用列表口径闭环。与「父块级去重」的区别在**作用域与目的**：父块级去重是**单次检索调用内**、保护精排窗口；内容折叠是**请求级（跨调用）**、省上下文 token 并让引用编号保持稠密。命中时**不静默跳过**，而是放一行占位（告知"本次亦命中同一来源"），避免模型误判"这次没搜到"而继续换词重试；若原内容已被裁剪出上下文，则重放完整内容但**复用原编号**。

### 词法检索（lexical retrieval）

与 dense 路并列的第二路取数，由 PostgreSQL 全文检索承担：`chunks.tsv`（`content_seg` 的生成列）
用 `@@ to_tsquery('simple', …)` 匹配，按 `ts_rank` 降序取 top-k。
**不叫 BM25** —— `ts_rank` 是 cover-density 排名，不是 BM25（沿用 `bm25_score` 的命名会误导）。
本地与托管的 PostgreSQL 都能用 `simple` 配置，不依赖任何中文分词扩展。

### 分词口径（tokenization contract）

写入侧（`content_seg`）与查询侧（tsquery 词元）必须调用同一个 `tokenize()`
（`src/infra/search/tokenizer.py`），并过滤长度 < 2 的词项。两侧不一致**不会报错**，
只会静默降召回；分词结果随 `tsv` 生成列固化落库，因此 **jieba 版本或词典变更必须触发
存量全量重写**（`scripts/rewrite_content_seg.py --apply`，`--check` 是那条不变量的检查）。

### RRF 融合（Reciprocal Rank Fusion）

把两路已排序结果按 `1/(k+rank+1)` 累加后重排，融合在**应用层**（`src/rag/fusion.py`），
参数 `RRF_K` / `RRF_TOP_N` 来自配置，**两路等权**（不引入权重）。融合只重排，
不得抹掉任一结果的 `dense_rank` / `sparse_rank` —— 那正是"某一路其实没有贡献"的观测手段。

## Agent 状态图（LangGraph）

- **AgentState**：节点间共享的状态对象，字段名以 `LangGraphNode.*` 常量作为 key（`src/agents/graph/state.py`）
- **LangGraphNode.\***：节点名常量，字段生产-消费矩阵的 key
- **LangGraphEvent.\*** / **LangGraphKey.\***：SSE 流解析用的事件类型 / 事件 dict key
- **装配入口（assembly entry）**：`src/agents/graph/agent_factory.py` 的 `build_agent`，主角色与 fork 子代理**共用**的唯一 `create_agent` 调用点（`src/agents/` 下唯一允许调 `create_agent(` 的地方）；装配差异仅由参数（`system` / `max_turns` / `middleware_extra`）提供
- **循环 middleware 四件套**：`src/agents/graph/middleware.py` 的四个 `AgentMiddleware` —— `SystemMessagesMiddleware`（施加 system 段）/ `ModelParamsMiddleware`（温度分档 + 思考开关）/ `AgentTurnBudget`（回合上限，仅主角色）/ `AgentSpanMiddleware`（观测，**必须在最内层**，由 `build_agent` 装配期守卫）
- **回合预算 middleware（`AgentTurnBudget`）**：循环的回合上限承载件，`after_model` 自增 `_turn_count` 并按有效上限判定（委派轮放宽 `MAX_DELEGATE_BONUS`），命中时记 `iteration limit` 并声明 `jump_to=end`；**回合上限的唯一来源**是 `build_agent(max_turns=…)`（非 `None` 时由装配入口追加本件，仅主角色装配）
- **历史预算（`HISTORY_TOKEN_BUDGET`）**：跨轮历史注入的 token 上限，**绝对值**，集中 `src/config/const.py`，与模型窗口解耦（弃用「窗口 × 比例」口径）。`agent_node` 的 `_truncate_history` 在轮数粗筛（`HISTORY_MAX_TURNS`）后按最旧逐条弹出至达标，**最近 1 轮始终不截**；仍超预算时记 `history budget exceeded`
- **轮内累积上限（`TURN_INPUT_TOKEN_LIMIT`）**：单轮内模型调用输入 token 的**告警阈值**；`AgentSpanMiddleware` 在模型调用前经 `count_messages_tokens` 度量，达上限记 `context budget high`。本阶段**只告警、不处置**（压缩属摘要层）
- **跨轮摘要（cross-turn summary）**：把超出历史预算的更旧对话压成结构化摘要（用户目标 / 已达成的决定 / 未解约束 / 关键事实与数字 / 下一步），持久化在 Redis `chat_summary:{session_id}`，供后续轮次在 seed 点只读注入；触发判据与「历史预算」同口径（落在"将被丢弃的那一段"）
- **摘要覆盖边界（`covered`）**：摘要已覆盖到的消息条数（按条数计，与 Redis List 索引对应），用于增量更新与避免重复摘要
- **system 提供方式**：`build_agent` 的 `system` 参数两条通道 —— **静态串**（子角色，`system=<str>`，人设 + 执行契约直接进 `create_agent`）与**经运行态携带**（主角色，`system=None`，system 段由 `agent` 节点组装写入图状态 `_system_messages`，运行时由 `SystemMessagesMiddleware` 施加到每次模型调用）
- **循环后阶段（post-loop stage）**：外层图在 `agent` 节点之后、循环本体之外的节点（`agent_finalize` / `verify` / `format`）。领域逻辑（答案提取、完整性校验、引用格式化）留在此处，**不进**装配产物；model↔tools 循环本体则内化在装配产物内部

## 对话行为

- **abstention**（拒答）：检索无达标 context 时直接返回拒答文案，不回 LLM
- **clarification**（追问）：分类器发现缺失实体时向用户发起追问（`CLARIFICATION_ENABLED` 开关控制）

## 记忆与对话历史

- **对话历史（transcript）**：本次会话逐条消息的记录（user / assistant / tool），跟随会话生命周期、**不可有损**（是回放与审计的真相）。真源 = Redis `chat_history:{session_id}`（供上下文组装）+ MySQL `conversation_history`（审计/回放）
- **Agent 记忆（memory）**：从多次会话中提炼出的、**跨会话**跟随用户的知识（画像 / 偏好 / 经历 / 规则），**可错、可遗忘、可重建**。**本项目当前尚未实现**（属 Phase C，底座见 `docs/adr/0018-history-vs-memory-keep-own-truth-source.md`）
- **记忆边界**：长期记忆**只承载 episodic + user-semantic**；**事实与知识类问题仍强制走 RAG 取证**，记忆不替代证据检索（依据 ADR-0017 决策 4，ADR-0018 沿用）
- **`episodic` / `user-semantic`**：记忆的两类内容——前者是"经历过什么"（事件/任务），后者是"用户是谁"（画像 / 偏好 / 规则）
- 二者的完整区分（含用途与注入形态）见 `docs/agents/memory-model.md`（**唯一归属文档**）

## Web 搜索兜底

- **kind**：引用来源类型，取值 `kb`（知识库）/ `web`（网络搜索），默认 `kb`；承载于 `SSEInteractionTexts.CITATION_KIND_KB` / `CITATION_KIND_WEB`，贯穿 `RAGContext.kind` 与 citation 事件
- **search_web**：联网搜索工具，KB 检索不达标时经 Tavily 兜底检索网页，结果与 retrieve_kb 共用 `tool_contexts` 编号（kind=web 区分来源）；受 `WEB_SEARCH_ENABLED` 开关与 `WEB_SEARCH_PER_TURN_LIMIT` 限次控制
- **引用编号（citation index）**：回答正文的内联标记与引用抽屉条目的 `[n]`，既是"该来源在本次请求材料池中的位置"，也是正文内联引用与抽屉条目的对应键。当前编号按材料池位置推导（`RAGContext` 尚无编号字段）；「请求级内容折叠」要求改为由材料池条目**显式携带**（新增 `RAGContext.index`），以便重放已折叠来源时**复用其原编号**。最终引用列表按正文实际引用的编号、以 `(source, page)` 去重后生成（`src/agents/graph/nodes.py`）
- **web_search**：SSE 状态阶段（`SSEStatusEvent.stage` 取值），联网搜索开始/完成状态提示（"正在联网搜索..." / "联网搜索完成，正在分析..."）
- **来源等级（source tier）**：引用来源的权威等级，取值 T0（内部文档，KB 固定）/ T1（官方一手）/ T2（权威媒体）/ T3（一般，未命中默认中性档）/ T4（UGC），由 `SOURCE_TIER_RULES` 域名规则表确定性定档（`.gov.cn`/`.edu.cn` 模式升 T1），模型判断不改写已定档位；以徽标形式透明呈现在引用抽屉条目，系统不裁决可信度；字段语义与标签权威见 api_contract.md「citation.tier」
- **候选规则信号**：种子清单（`SOURCE_TIER_RULES`）的成长机制——离线 SQL 从 `conversation_history.sources` 聚合全部域名引用次数，达阈值者经人工审核（对照规则表与拒绝清单、核对样本引用上下文）后加入规则表，被拒域名记入文档化拒绝清单（negative cache）；不使用 LLM 定档或自动升级；操作步骤见 cookbook.md「候选规则审核」

## 接入通道（channel）

- **接入通道 / 通道驱动（ChannelDriver）**：屏蔽企业微信「URL 回调」与「长连接」两种接入方式的传输差异的抽象；业务只依赖其入站事件（`InboundMessage`）与回复出口（`ReplySink`）。见 `src/channels/base.py`。
- **回调驱动（CallbackDriver）**：接入通道的实现之一，负责企微 URL 回调的验签、AES 加解密、入站解析与加密回包；智能机器人场景 `ReceiveId` 为 `""`。见 `src/channels/wecom/callback.py`。
- **长连接驱动（LongConnectionDriver）**：接入通道的另一种实现，由本服务主动连企微 WebSocket（封装官方 `aibot` SDK），无需公网回调地址与加解密；与回调驱动由 `WECOM_BOT_MODE` 二选一。见 `src/channels/wecom/long_connection.py`。
- **多机器人配置（`WECOM_BOTS`）**：企微智能机器人的多台配置，JSON 数组（**必须单行**），每台 `{key, bot_id, secret}`；解析与校验见 `src/config/wecom_bots.py`，由 `wecom_service.start()` 在受 `WECOM_BOT_ENABLED` 门控下调用。
- **保留键 `callback`**：`callback` 是长连接机器人 `key` 的保留字；回调 legacy 驱动以该键存入驱动注册表。
- **澄清回填（clarify backfill）**：企微无站点那样的作答 UI，用户以**文本回复**作答；通道把文本解析成站点同构 `answers` 并注入挂起的 `ask_user` 回合。**仅触发者**：只有登记在案（`_triggers`）的触发者其回复才被当作答案。
- **澄清呈现**：把 `SSEAskUserEvent` 渲染成气泡内容（问题 + 可选项 + 提示）；**不 finalize**，因为原回合仍在挂起等待。

## Harness（本项目定位）

> 行业对本词**无统一口径**（LangChain / HuggingFace / 社区三方分法互不兼容），本节只固化**本项目口径**，供内部文档与命名统一。

**两个主流口径**

| 口径 | 公式 | 出处 | harness 的范围 |
|------|------|------|---------------|
| 宽 | `Agent = Model + Harness` | LangChain《The Anatomy of an Agent Harness》(2026-03) | 除模型外的**一切**代码、配置与执行逻辑（"If you're not the model, you're the harness"） |
| 窄 | `Agent = Model + Scaffolding + Harness` | HuggingFace `agent-glossary` (2026-05) | 仅**模型不可见**的执行层；`Scaffolding` 另指模型可见的规则层 |

**本项目采用宽口径**：harness = 模型之外、让模型能真正干活的那套工程系统。判定依据（三问，任一命中即属 harness）：改它会改变 Agent 的**行为/知识**（非仅性能）→ 属 harness 而非 runtime；产出是**内容**（提示词/检索结果/答案）而非**进程**；删掉它 Agent 会**变笨或幻觉**，而非**跑不起来**。

**边界限定（勿与 coding harness 混淆）**：本项目 harness 的"环境"是**知识库 + 联网检索**，**不含文件系统 / Shell / Sandbox**。与 Claude Code、Codex 那类"给模型一台计算机"的 coding harness 不同源——听到"harness"就预期能跑命令，是**预期错**而非定义错。

**与相邻术语的分界**

| 术语 | 位置 | 本项目对应 |
|------|------|-----------|
| `framework`（框架） | 你调它、它替你搭的库 | LangGraph、FastAPI——**本项目不是 framework** |
| `runtime`（运行时） | 执行层：沙箱隔离 / 资源配额 / 网络与凭证 / 容器调度 / 进程级恢复 | 不适用（无沙箱；流式状态在单 worker 进程内） |
| `scaffolding`（脚手架） | 模型**可见**的规则层 | 宽口径下属于 harness 的一部分 |
| `harness` | 见上 | 本项目自称 |

**本项目模块归属**（写作 spec / 分模块时按此归类，勿混用两套口径）

| 层 | 落点 |
|----|------|
| Scaffolding（模型可见） | `src/config/prompts/`、工具描述、`SKILL.md` 的 name/description、`agents/*.md` 预设正文、输出格式约束 |
| Harness（模型不可见） | LangGraph 图与循环、工具注册表路由、迭代预算与停止条件、verify 与护栏强制、SSE 事件发射、会话状态装载与持久化、trace |
| Model | DashScope 侧的模型本体，非本项目所有 |

**命名结论**：本项目自称 `Corporate Agent Harness` 成立（宽口径），既有文件与 spec 命名（`chat-harness-ui`、`agent-harness-foundation`）沿用不改。

**常见错误**

- ❌ 把 LangGraph 说成"本项目 harness"——它是 **framework**，本项目是**用它搭出的 harness**
- ❌ 以为 harness 必含沙箱/Bash——那是 **coding harness** 的语感，本项目无
- ❌ 与 `agent` 混用——agent 是**执行主体**（`src/agents/graph/` 的循环），harness 是**包住它的整套系统**

## Agent / Skill / Tool 三概念（易混，先分清）

| 术语 | 一句话 | 本项目对应 | 常见错误 |
|------|------|------|---------|
| `agent`（智能体） | **执行主体**：LLM + 循环 + 工具 + 上下文，自己判断、多轮干活 | 主 agent = `src/agents/graph/` 的 LangGraph 循环 | ❌ 与 skill / tool 混为一谈 |
| `subagent`（子代理） | 由主 agent 派生的另一个 agent | 仅 fork skill 执行时由 `create_agent` **运行时生成** | ❌ 说成"skill 在跑"——跑的是 agent |
| `skill`（技能） | **静态说明书**（"这类事怎么做"），内容/配置，自己不会执行 | `skills/<name>/SKILL.md`（定义见「技能委派」） | ❌ 当成执行主体 |
| `tool`（工具） | agent 可调用的**函数**（"做什么"） | retrieve_kb / search_web / ask_user / delegate_task | ❌ 与 skill 混（做什么 vs 怎么做） |
| `/xxx`（斜杠命令） | 用户**触发方式**，不是一类能力（像"叫电梯的按钮"） | 待实现（见 change `skill-invocation-alignment`） | ❌ 当成 skill 本身 |

**关系**：agent 拿着 skill（说明书）调用 tool（工具）干活。skill 是静态文本，离开 agent 无意义；tool 是动作，skill 是方法。

**关键坑：fork skill 会"长出"一个 agent**——`skills/financial-statement-analyzer/SKILL.md` 是 **skill**（静态文件），被执行时 `SkillExecutor` 以执行者人设作 system prompt、skill 正文（任务已注入）作初始 user message，经 `create_agent` 生成一个 **subagent**（执行体）。同一名字，文件是 skill、跑起来的是 agent；inline skill 不生成新 agent（正文注入主 agent）。

**参考项目辨析**：`github/agency-agents` 的 319 个 `.md` 是 **agent 人设定义**（frontmatter：name/description/color/emoji/vibe/tools），**不是 skill**；其 `scripts/convert.sh` 生成 SKILL.md 时只保留 `name` + `description`（标准最小集）。

## 智能体预设与调用控制

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `智能体预设（agent preset）` | 磁盘声明式智能体定义（`agents/<name>.md`，frontmatter 驼峰键 + 正文 system prompt 人设）；运行时对象 `AgentPreset` 由 `AgentPresetLoader` 产出、`AgentPresetRegistry` 按名索引（`src/agents/presets/`） | ❌ 与 skill 混为一谈（身份 vs 手册） |
| 会话级 vs 消息级 | 预设是**会话级身份**：选定后整通会话稳定（回答"谁在干活"）；skill 是**消息级内容**：按命中注入或 fork 执行（回答"按哪本手册干"） | ❌ 把 skill 当身份、把预设当一次性指令 |
| 双轴调用控制 | skill 的两条正交开关：`user-invocable`（允许用户 `/xxx` 调用）与 `disable-model-invocation`（禁止模型自动 `delegate_task` 调用）；未显式声明时按 `allowed-tools` 的工具只读性推导（含写类工具默认锁模型端，fail-safe；只读表为空 fail-open 不锁）；两侧皆关 = 死 skill 记 warning | ❌ 以为一个开关管两边 |

## 技能委派（主从委派）

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `skill` | 磁盘声明式能力文件（`skills/<name>/SKILL.md`，frontmatter 声明元数据 + 正文按 context 存方法论/子代理 prompt）；skill 是内容/配置而非代码 | ❌ 与开发期 `.claude/skills/` 工具链 skill 混为一谈 |
| `SkillRecord` | skill 文件解析后的运行时对象（name/description/context/inline_prompt/fork_body/双轴等），由 `SkillLoader` 产出（`src/agents/skills/models.py`） | ❌ 直接用 SKILL.md 原文当结构体 |
| inline 执行 | skill `context=inline`：方法论注入主 agent 上下文，主 agent 自己执行 | — |
| fork 执行 | skill `context=fork`：`SkillExecutor` 生成持有只读工具面的子代理独立深度分析，结果纯文本回主 agent | — |
| 按路径的引用编号策略 | fork 子代理的 `[n]` 指示按**入口路径**分派（design D16，载体 `DelegateRun.via`）：**委派路径**（`delegate_task` → `DELEGATE_VIA_DELEGATE`）子代理检索落子池、`[n]` 由主 agent 按自身检索来源统一补标，子代理**不标**；**直出路径**（`/xxx` → `DELEGATE_VIA_DIRECT`）无上游补标，子代理须自检索并自标 `[n]`（子池即本轮引用池）。两条策略**不同且有意为之**，不得"统一" | ❌ 以为两条路径共用同一执行器就该共用 `[n]` 策略；在委派路径让子代理自标 |
| `context_source` | skill 的 `context` 取值来源：`default`（未声明→inline）/ `explicit`（frontmatter 显式）/ `auto_oversize`（未声明但正文超 `INLINE_PROMPT_MAX_CHARS`，加载期自动改 fork） | ❌ 以为"未声明"就是固定某一侧；长文 skill 的承载方式随正文长度自动变化 |
| `skill_direct`（直出节点） | `/xxx` 命中 fork skill 时主 agent 零 LLM 轮：直接跑 fork 子代理，把结果与"本轮材料"（引用池 + 要求覆盖年份）搬进 `AgentState` 交 verify/format；入口分派与节点契约见 api_contract.md「5.5 fork 执行层契约」 | — |
| 执行者选择 | fork 子代理的执行者人设按 `skill.agent` > 会话绑定智能体 > 系统默认人设的优先级选取（`src/agents/skills/executor.py`）；通用委派无 `skill.agent` 可查，跳过该档直接用会话智能体 | ❌ 以为 fork 恒用系统默认人设 |
| `delegate_task` | 主 agent 委派工具 `delegate_task(task, skill=None)`：`skill` 省略即通用委派（不查注册表、不加载正文，`task` 直接作子代理输入）；`skill` 非空按命中 skill 的 context 分发 inline/fork，unknown 返回"skill 不存在 + 可用列表" | 引用会指向子代理产出（实际引用仍只指向主 agent 自身检索来源） |
| 通用委派 | `delegate_task` 省略 `skill` 的委派形态（design D6）：无 skill 正文、无 `allowed-tools` 收窄（仅继承只读面）、无 `skill.agent`/`model` 声明；与定点 fork **共用同一预算闸门与 `_run_fork` 执行路径**（超时/轮次/取消/引用池隔离）。事件 `skill` 字段与看板标题用用户可见标签 `SSEInteractionTexts.DELEGATE_GENERIC_LABEL`（`"通用分析"`，看板标题经 `DELEGATE_TASK_TITLE_TMPL` 渲染为「通用分析 · 领域专家分析」） | ❌ 以为通用委派可绕过预算闸门；另起一条执行路径 |
| `delegate_id` | 单次 delegate_task fork 的唯一标识（短 uuid），随该次委派的 start/增量/end 与 task execution 条目贯穿；同一次回答内多次委派互不相同 | ❌ 各事件各自随机生成导致无法关联 |
| `DelegateStopReason` | fork 结束原因统一枚举：`normal / idle / total / turn / failed / cancelled`（const 定义）；delegate end `ok/reason` 与 task 注册表终态共用同一词表 | ❌ 各层另起一套原因词 |
| delegate end `ok/reason` | 委派结束事件语义：`ok=true` 文案"领域专家分析完成"；`ok=false` 携带 reason，文案"分析中断·原因" | ❌ 无条件推"完成"（原实现缺陷） |
| task `type=plan|execution` | 任务看板条目类型：`plan`=主 agent 经 Task 工具建的跟踪项；`execution`=delegate 自动登记的执行追踪（`task_id=delegate_id`） | ❌ 两类混排不区分、互相覆盖 |

## Provider 兼容层

> 代码落点 `src/infra/llm/provider/`；决策见 `docs/adr/0021-provider-compat-layer.md`。

- **Provider 兼容层（provider compat layer）**：`src/infra/llm/provider/` 单一包，把各家 provider 的差异（思考参数形状 / reasoning 字段名 / 端点 / 密钥 / 定价 / 窗口）收敛到一层，调用侧只认 `build_chat` / `build_embeddings` / `build_rerank` 三个门面。**它不是网关**：不做多 provider 路由 / fallback / 限流 / 计费聚合（那是 ADR-0020 否决的网关职责）。provider 事实收进数据面 `registry.yaml`，切换 provider = 改 `.env`、新增 provider = 加一条数据。
- **角色（role）**：兼容层里「一类用途的模型绑定」的抽象，取值 `main`（主对话）/ `classify`（轻量分类）/ `summary`（跨轮摘要）/ `judge`（评测裁判）/ `embedding` / `rerank`。每个角色解析出 provider + 模型 + 端点 + 密钥；缺省分两族 —— `main` / `classify` / `summary` 跟随 `LLM_PROVIDER`，`embedding` / `rerank` / `judge` **独立缺省 `dashscope`**（DeepSeek 无 embedding/rerank；judge 是离线评测角色）。
- **思考形状（thinking shape / `ThinkingStyle`）**：同一「思考开关」在不同 provider 落成的请求参数形状。本仓归一化为 `thinking: bool`（on/off），由纯函数 `thinking_extra_body(spec, enabled)` 翻译成目标形状（dashscope → `{"enable_thinking": X}`；deepseek → `{"thinking": {"type": "enabled"/"disabled"}}`），并是**唯一**产生思考参数的地方。**始终显式发开关**（provider 默认值相反，不显式即静默变贵）。
- **reasoning 字段名（`reasoning_field`）**：provider 声明思考文本落在哪个 delta 字段（多为 `reasoning_content`，OpenRouter 等用 `reasoning`）；为空则整条回传链路跳过（不给不认该字段的 provider 塞多余字段）。

## 推理思考文本

- **reasoning_content**：模型的流式思考文本（chain-of-thought）。DashScope 等第三方把思考增量放在 `delta.reasoning_content`，OpenRouter 等用 `delta.reasoning`；`ReasoningPreservingChat` 统一累积到 `AIMessageChunk.additional_kwargs["reasoning_content"]`，供上层读取与展示

## 过程回放

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `旁白（preamble）` | 最后一次工具调用之前模型输出的 content 流（分拣时由 token 帧固化，payload 为 `{"text": ...}`），渲染为过程容器内的弱化块；深度思考开启时消失（规划内容走 reasoning_content） | ❌ 与 answer 正文混排（正文由 content 列承载，token 帧不入 process） |
| `过程轨迹（process）` | assistant 消息持久化的有序事件数组（`conversation_history.process` 列，`{"format_version": 1, "events": [...]}`），历史回放据此重建过程容器；D7「历史重载不重建」已反转 | ❌ 把 model_info/abstention/done/error/citation 五类也计入事件 |

## 评估指标（DeepEval 原生）

| 指标 | 含义 |
|------|------|
| `faithfulness` | 忠实度：答案是否忠于检索上下文 |
| `answer_relevancy` | 答案相关性：是否切题 |
| `context_precision` | 上下文精确率：召回的 chunk 有多相关 |
| `context_recall` | 上下文召回率：相关信息有多少被召回 |
| `contextual_relevancy` | **诊断指标**（不进主分）：检索上下文与问题的相关程度，仅判前 8 条 |
| `tool_correctness` | **诊断指标**（不进主分/门禁/Langfuse）：命中的期望工具数 / 去重后的期望工具数（0.0–1.0）。只比较工具名与类型（类型固定 `FUNCTION`），不比较参数、顺序、调用次数；`expected_tools=[]` 表示期望不调工具；字段缺失或跑图失败/空回答为 N/A |

### 出题题集字段（v6）

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `hop_type` | 题目跳数类型，取值 `single_hop` / `multi_hop`；按 DeepEval evolution 是否含 `Multi-context` 判定 | ❌ 当作已证明的跨块推理 |
| `hop_basis` | `hop_type` 的判定依据，当前恒为 `evolution`（代理标签，不宣称已证明跨块） | ❌ 读成人工标注 |
| `multi_hop` | 多上下文题（evolution 含 `Multi-context`），题集内占比上限 1/3，超出按题面哈希确定性截断 | ❌ 与"多次工具调用"混为一谈 |
| `expected_tools` | 该题期望调用的工具名列表；v6 恒为 `["retrieve_kb"]`（一次检索返回 top-k，多跳不重复标） | ❌ 按跳数重复标 `retrieve_kb` |
| `numeric_risk` | 参考答案是否含需单位/口径判定的数值事实：同时命中数字与金额/百分比/倍数等单位词 | ❌ 当作"答案错误"标记 |

## 基础设施

- **PostgreSQL**：关系型存储后端（用户 / 知识库 / 文档 / 会话 / 消息 / 反馈 / 评估报告 7 张表，外加检索底座表 `chunks`），经 `postgresql+asyncpg` 访问。结构、引擎归属与迁移链见 `docs/agents/code-map.md`「关系型存储（PostgreSQL）」
- **ChromaDB**：**已退役向量库**，dense 检索由 PostgreSQL + pgvector 承载。P4 已删除 `chromadb` 依赖、`deploy/chroma/`、其配置项与 compose 卷/挂载，搬迁与等价性脚本一并退役；`data/chroma_persist`（连同 `data/chroma` / `data/bm25_index`）三个数据目录已随 P4 Task 11 从磁盘删除，而读取路径更早已（Task 6）移除，**回滚到 Chroma 不再可能**（语料重建只能从 MinIO 的原始文件重新上传）
- **MinIO**：文档对象存储
- **Langfuse**：自托管可观测后端（**现为 v2 线**：仅 `langfuse-web` + 复用既有 PostgreSQL）；术语与部署契约见下节「可观测后端（Langfuse）」
- **LiteLLM**：可选的 LLM 代理网关（compose profile `disabled`，**默认不启用**）。`LLM_BASE_URL` 默认**直连** Provider，仅在显式启用网关时才指向 `http://litellm-proxy:4000`。不采用它的决策见 `docs/adr/0020-no-model-gateway-direct-provider.md`
- **DashScope**：通义千问系列模型的提供商（Embedding / LLM / Rerank）

## 关系型存储（PostgreSQL）

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `存储收敛（storage consolidation）` | 把存储从 MySQL + ChromaDB 收敛到 PostgreSQL 的变更方向。P1 换关系型后端；P2 把 dense 向量检索换到 PostgreSQL + pgvector；P3 把词法检索从进程内 `rank_bm25` 换到 PostgreSQL 全文检索（`tsv @@ to_tsquery('simple', …)` + `ts_rank`，query 侧由 jieba 预分词）。替换不改**融合参数**与 **dense 路**的行为；**词法侧的算法与分词替换是独立事项**（必然改变行为，见 `design.md` D6）。**P4 终局**：跨表写收口到 `session_scope`（事务边界），Chroma / BM25 的依赖、配置、compose 卷与挂载、`deploy/chroma/`、一次性脚本全部退役，**回滚到 Chroma 不再可能**（`data/chroma_persist` 连同 `data/chroma` / `data/bm25_index` 三个数据目录已随 P4 Task 11 从磁盘删除，读取路径更早已（Task 6）移除） | ❌ 以为存储替换必然改变 dense 路行为，或以为词法路换算法也算"只改存储" |
| `DSN 单一来源` | 应用 DSN 只由 `src/config/settings.py:build_postgres_dsn()` 产出（`postgresql+asyncpg://`，`POSTGRES_PASSWORD` 缺失即抛 `RuntimeError`），`src/infra/db/engine.py` 在模块级消费它。宿主侧跑 alembic / pytest 时用 `POSTGRES_HOST=localhost` 覆盖 `.env` 里的 compose 服务名（`python-dotenv` 默认 `override=False`，已存在的环境变量优先） | ❌ 各处自行拼连接串；❌ 宿主侧忘了加 `POSTGRES_HOST=localhost` |
| `chunks 表` | 单一张分块表，以 `kb_id` 列表达知识库归属（取代「每库一 collection」）。由 `ChunkModel` 映射（`src/infra/db/models/chunk.py`，ORM 属性名 `extra` → 列名 `metadata`），SQL 访问层是 `ChunkRepo`。`content_seg` 是词法检索文本列（P2 写正文原值作占位，P3 起为 jieba 分词输出并全量重写）；`tsv` 是 `to_tsvector('simple', content_seg)` 的持久化生成列（GIN 索引）；`embedding` 为 `vector(1024)`。列清单、索引与迁移链见 code-map.md「关系型存储（PostgreSQL）」 | ❌ 以为 `chunks` 无 ORM 模型；❌ 把 `content_seg`/`tsv` 当成应用层字段名；❌ 以为还按知识库分 collection |
| `事务边界（session_scope）` | 跨表原子提交的唯一入口：`src/infra/db/transaction.py` 的 `session_scope(session_factory, session=None)`，每个 Repo 以其为基础暴露 `transaction()`。参与者方法接受 `session=` 且**传入时不提交**，提交/回滚由持有该会话的边界决定。入库路径「写 chunks + 标记 ready」、文档删除「删分块 + 软删文档」、KB 删除「软删文档 + 删分块 + 软删 KB」均同事务，**删除路径失败不得吞异常**；embedding 等外网调用必须在事务外完成 | ❌ 把外网调用放进事务（长占连接与锁）；❌ 参与者自行 commit 破坏边界；❌ `except Exception` 吞掉删除失败 |
| `一次性验收产物（已退役）` | P2 dense 等价性与 P3 词项命中探针的一次性脚本（`scripts/migrate_chroma_to_pg.py` / `scripts/dense_equivalence_check.py` / `scripts/lexical_probe*.py`）已随 P4 退役；其测量文档（`docs/tmp/p2-dense-equivalence-*.md` / `p3-lexical-probe-*.md` / `p3-acceptance-*.md`）作为「那次对比的冻结记录」保留，**不可重跑、不得当质量基线**（判据缺口见需求池 F-30） | ❌ 以为还能跑搬迁/探针脚本重建语料；❌ 回写其测量数字或把它读作质量结论 |

## 可观测后端（Langfuse）

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `可观测后端（observability backend）` | 本项目自托管的 Langfuse 后端及其部署形态。**现为 v2 线**（`langfuse/langfuse:2.95.11`）：只有 `langfuse-web` 一个容器 + 复用既有 PostgreSQL 的独立 database，**不含** ClickHouse / `langfuse-worker` / S3 事件存储。决策与代价见 `docs/adr/0011-langfuse-v2-downgrade.md`；部署契约见 change 的 `docs/openspec/changes/langfuse-v2-downgrade/specs/observability-backend/spec.md` | ❌ 以为还需要 ClickHouse 或 `langfuse-worker`；❌ 按 v3 的六组件形态排查问题 |
| `凭据播种（credential seeding）` | 用 `LANGFUSE_INIT_ORG_*` / `LANGFUSE_INIT_PROJECT_*` / `LANGFUSE_INIT_USER_*` 在**空库首次启动**时创建组织、项目、API Key 与管理员。**`LANGFUSE_INIT_PROJECT_ID` 是开关** —— 官方 initialize 逻辑把 project 与 key 的创建整体嵌在 `if (env.LANGFUSE_INIT_PROJECT_ID)` 内，缺它时二者都不建**且不报错**。本项目用它与 `_PUBLIC_KEY` / `_SECRET_KEY` 播种**既有** key 对，使库重建后 `.env` 凭据仍有效 | ❌ 只配 `_PUBLIC_KEY` / `_SECRET_KEY` 而漏 `_PROJECT_ID`（静默不播种，凭据失效）；❌ 在 compose 里给这些值加双引号（官方 gotcha） |
| `trace 保留期` | Langfuse 的 Data Retention 在自托管下属企业版功能，OSS v2 无内置 retention/cleanup 开关，故清理由 `src/cli/purge_langfuse_traces.py` 自建：默认保留期 **30 天**（`--retention-days` 可覆写，下界 **1 天**），单次删除上限 **1000 条**（超限即中止、不删任何数据），非 dry-run 须显式 `--yes` 且环境变量 `LANGFUSE_PURGE_ALLOW=1` 武装。**删除后端是直连 Langfuse 的 PostgreSQL 库跑 SQL**，不走 Langfuse 的公开删除 API —— 该 API 在镜像 `2.95.11`（v2 线）上一律返回 **405**，**v3 才有**；v2 里真正 enterprise-gated 的是 Data Retention。删除面 = `observations` / `scores` / `trace_media` / `observation_media` / `traces` + 已无 trace 的 `trace_sessions`，作用域限**单 project**（库内 project 数 ≠ 1 即拒绝），真删输出形如 `[info] 级联删除 observations=2 scores=0 trace_media=0 observation_media=0 traces=6 sessions=1`；决策与理由见 `docs/adr/0013-trace-retention-purge-via-langfuse-sql.md`。因清理直连数据库，**须在 compose 网络内执行**（PG 主机名 `postgres` 在宿主不可解析） | ❌ 以为 v2 有内置 retention 配置可开；❌ 在宿主机上直跑该 CLI；❌ 以为删除走的是 Langfuse 的公开 API |

**`LANGFUSE_ENABLE` 开关口径**：同一开关在代码里既接 tracing（`src/infra/llm/tracing.py`），也接 `PromptManager` 的远端 prompt 读取（`PromptManager.__init__` 读它）。按 `docs/adr/0010-delist-langfuse-prompts.md`，远端名单当前已出列为空，名单为空时不发起网络请求 —— 故**当前实际只影响 trace 产出**；待名单加回并固定 label/版本（终态，见 `docs/adr/0002-prompt-carrier-yaml-two-phase.md`）后，两者同受该开关治理。

## prompt 组装

### 六段模型
system prompt 的段划分：`base`（人设层，可替换）/ `runtime_contract` / `sources` / `tools` / `output`（环境约束层，不可替换）/ `skills`（运行时层，不参与 system 组装）。
准确读法是 **5 个 system 段 + 1 个非 system 载体格位**。与既有「三层组装」是**嵌套**关系，不是两套规范。
归属规则见 `docs/agents/prompt-ownership.md`。

### 领域 base
`section: base` 且带 `domain` 字段的模板，作为"知识库 → 领域默认视角"的载体。
与智能体预设是**同一段位的互斥候选**（三选一替换），不是叠加。
通用领域用保留值 `general`，且它必须有对应模板。

### 判据留代码
条件注入的**判据**（哪条规则依赖哪个工具、适用域是什么）住在 `src/rag/prompt.py` 的规则表里，
YAML 模板只装正文、不声明 `requires_tools` / `applies_when`。
即"**能改文案、不能改挂载**"。逐条对照表见 `docs/agents/prompt-ownership.md`。

### 领域回退
知识库的 `domain` 在落库时已按"存在对应 base 模板"校验，故读取期回退只兜
"模板被删或改名后存量库指向了不存在的领域"这类**跨版本**情形。
回退目标为保留值 `general`，记 `kb domain fallback` / warning，**不阻断请求**。
与写入期校验的区别：写入期是**拒绝**，读取期是**降级**。

### 无条件规则的恒定注入
`runtime_contract` 与 `output` 两段的判据是"无条件"，即与"是否绑库""注册了哪些工具"
无关。契约测试 SHALL 遍历"是否绑库 × 已注册工具集合"的组合，断言每组都含完成条件与
数据·指令边界 —— 它们是 `answer_len=0` 类失败的直接修法，最该被钉死。

### 预设替换（三选一）
`base` 段的解析规则，按序取第一个命中项：**选定智能体预设 → 预设正文；否则知识库领域已知
→ 该领域的默认 base；否则 → 内置通用 base**。是**替换**而非叠加，任一时刻只有其一生效。

代价（明确接受、不视为缺陷）：选定预设后知识库领域方法不再参与组装，系统记
`agent domain mismatch` 日志但**不阻断**（属用户自选行为）。与「领域 base」是同一段位的互斥候选。

### 段模板与独立任务模板
`src/config/prompts/templates/*.yaml` 下的模板按 `kind` 字段分两类：**段模板**
（`kind: section`，即 `base` / `runtime_contract` / `sources` / `tools` / `output` 各段的条目）
参与 system 组装；**独立任务模板**（`kind: task`，classifier / rewrite / entity / 用户请求模板）
各自单独调用，**不进** system 组装。

分类依据 SHALL 是**字段**，不是文件名或目录位置；两类共用同一加载入口。

### 优先级指针句
可替换的 `base` 段以一句指针收尾：

> 先遵循运行时来源选择规则，再套用上述默认检索流程；用户要求的其他来源与交付物属于任务本身。

作用：领域 base 的默认检索流程与本轮实际来源选择冲突时，以**运行时段**为准。
`runtime_contract` 段相应地声明"可编辑的 base 段定义角色与工作流，本轮的来源选择与
工具可用性决定该工作流如何执行"。

### 读取路径唯一化
prompt 模板只经加载入口（`src/config/prompts/loader.py`）读取，**不存在第二条读取路径**。
`PromptManager` 因此收窄为**门面**，只转发 `get_user_template` / `get_classifier_prompt`。

原先的三项职责分别处置：远端拉取（Langfuse）3 个 name **出列**（见 `docs/adr/0010-delist-langfuse-prompts.md`）；
本地兜底正文（`_FALLBACK_*`）归加载入口，门面不再另存副本；系统追加段（引用指令 / 委派引导 / 日期）
移交段组装器，`_with_current_date` 保留在原定位。

保留"两条路径并存"的形态是本条要禁的 —— 那是双入口重复注入的来源。

## 如何更新

- 新增概念/术语（新模块、新事件类型、新指标、新配置项）且会被多处使用时，登记进对应分区。
- 同一概念已有标准叫法时，沿用本表词汇，不另造新词。
- 文档、代码、命名中的术语一律与本表一致；发现不一致时以本表为准并修正别处。
