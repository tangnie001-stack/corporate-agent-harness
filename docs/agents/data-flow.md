# 数据流链路

## 链路 1：文档上传 → 解析 → 入库 ★

```
用户上传 → MinIO 存储 → 文档解析(parse) → 策略检测
→ 智能分块(chunk) → 分块质量校验 → [可选]分块质量评估
→ embedding 计算(事务外) → PostgreSQL 分块入库(chunks 表：content + embedding)
  + 文档状态更新(ready)   ← 同一事务
```

入口: `POST /api/kbs/documents/upload` → 后台 `asyncio.create_task(_process_document_task)`
文件类型: `.pdf` / `.docx` / `.txt`，异步返回 `202` + `doc_id`

分块与向量同写一张 `chunks` 表（`kb_id` 列表达知识库归属，向量存 `embedding` 列）；
`VectorStore.add_chunks` 按 `(kb_id, doc_id, chunk_index)` 幂等覆盖并删尾部残留。
**写入的事务边界**：embedding 计算（慢的外网调用）在**事务外**完成，
`DocumentService._write_chunks_and_mark_ready` 再在**同一事务**内「写 chunks + 文档标记 ready」
—— 进程死在中间不会留下「有分块、文档未 ready」的孤儿。
检索底座的表结构、访问层与 ORM 归属见 code-map.md「关系型存储（PostgreSQL）」。

## 链路 2：用户问答 — 绑 KB 检索问答 与 未绑 KB 纯对话 ★

```
用户提问 → POST /api/chat/stream → SSE 建立 → agent 循环 → verify → format
→ 引用(citations) → 对话历史持久化(Redis + PostgreSQL)
```

入口: `POST /api/chat/stream`（body: `ChatStreamRequest`：session_id / kb_id / query / deep_thinking）
输出: SSE 事件流 `status → token → citation → model_info → done`（澄清/联网询问时含
`ask_user`，纯拒答时含 `abstention`，deep_thinking 时含 `reasoning`，delegate 委派 fork
时穿插 `delegate` 过程事件 start/增量/end）

每轮来源声明先于同轮澄清与委派：进入图事件循环前先回传一次 `agent_used`，再按条件推
`status`（`turn_agent` / `turn_skill`），顺序为 `agent_used` → `turn_agent` → `turn_skill`
→ 首个节点状态行，且先于同轮 `ask_user` / `delegate`；契约与文案见 api_contract.md
「`status.stage` 来源声明」。

用户问答在入口按 `kb_id` 是否为空分裂为两条实质不同的链路：**链路 2a（绑 KB 问答链，
RAG 检索 + 验证）** 与 **链路 2b（未绑 KB 纯对话链，联网兜底）**。`kb_id` 由前端绑定，
`chat_stream` 透传给 `agent_service.stream_chat`，后者写 `ctx.kb_bound = bool(kb_id)`
（src/api/chat.py:401、src/services/agent_service.py:592）。分裂并非两条独立图，而是同一
StateGraph 拓扑（`agent（内化循环）→ agent_finalize → verify → format → END`，
workflow.py:46-156）上的四处分叉：

1. **prompt**：未绑 KB 时在系统指令后追加未绑定提示（`src/config/prompts/templates/sources.yaml`
   的 `sources-kb-unbound`；联网句 `sources-kb-unbound-web` 仅在 `search_web` 已注册时追加），
   禁止调用检索（`src/agents/graph/agent_node.py` 的 `_split_initial_messages` 以
   `kb_bound=bool(state.kb_id)` 调 `build_prompt`；`src/rag/prompt.py` 的
   `_build_unbound_message` 注入）。组装点为
   `agent_node._split_initial_messages` → `build_prompt` → `build_system_prompt`
   （system 段 persona/条件注入 → 技能注入消息 → 历史），组装点记 `prompt messages`
   日志（见 logging-rules.md「来源与 prompt 观测事件」）
2. **retrieve_kb 内部**：`kb_id` 为空直接返回空结果，不检索（rag_tools.py:127-130 硬保证）
3. **verify 节点**：按 `state.kb_id` 分派态 A / 态 B 两套校验链（verify/node.py:35-84）
4. **SSE 状态事件**：检索阶段与联网阶段文案不同（agent_service.py `_convert_event`）

工具注册表两条链路相同：retrieve_kb + ask_user 常驻，`WEB_SEARCH_ENABLED` 开时才追加
search_web（rag_tools.py:226-235）。全局开关影响两条链路：`VERIFY_ENABLED=false` →
verify 直通（settings.py:68-72）；`WEB_SEARCH_ENABLED=false` → 无联网兜底工具
（settings.py:99-104）；`TEMPORAL_PARSE_ENABLED=false` → 不解析年份，2a 完整性校验无
数据源、直接跳过（settings.py:62-66）。

```
agent（外层节点：组装首轮 → invoke 装配产物；model↔tools 循环内化在产物内部）
  │ 外层图不再暴露 tools 节点与回边；循环内的模型/工具事件仍以 langgraph_node=="model" / "tools" 到达 SSE 转换层
  ▼
agent_finalize → verify ─(通过)→ format → END
                  └(_needs_regenerate)→ agent（重生成：外层 messages 非空，只回写新增段）
```

### delegate 分支（主从委派，两条链路通用）

agent 在循环内判定任务需要领域专家能力（深度分析/专用方法论）时，可调用
`delegate_task(task, skill)` 委派给对应 skill——skill 库（顶层 `skills/`，volume 挂载）
有内容才注册该工具（src/services/agent_service.py:571-593），注册表懒重载
（reload_if_changed）。按命中 skill 的 `context` 分发两种执行形态：

```
agent 判定需领域专家 → delegate_task(task, skill)
  ├─ inline：skill 方法论注入 agent 上下文 → agent 自己继续走 2a/2b
  └─ fork：子代理独立分析（默认继承执行者的只读工具面、可自行检索；材料获取按
       工具面判定 —— 无检索工具时由 agent 预检索后随 task 一并传入）
       → 纯文本结果回 agent → agent 整合进最终答案 → verify → format
```

- inline 执行无独立子代理，不推事件；fork 执行产生 `delegate` SSE 事件（start/过程增量/
  end，见 api_contract.md「delegate 事件详情」）
- fork 结果为纯文本，**无 [n] 引用**：引用仍只指向主 agent 自身检索来源（tool_contexts），
  不指向子代理产出
- delegate 轮放宽迭代上限：`AgentTurnBudget`（`src/agents/graph/middleware.py`）在
  `after_model` 检测本轮声明 `delegate_task` 后放宽上限 +`MAX_DELEGATE_BONUS`（整合余量，
  且标志跨轮保持）；回合上限经装配入口 `build_agent(max_turns=…)` 注入该 middleware
- 实现与术语：src/agents/skills/（SkillRecord/SkillLoader/SkillRegistry/
  SkillExecutor/make_delegate_task），术语见 glossary.md「技能委派」

#### delegate 事件双通道与 scope 隔离

fork 过程事件与主图事件分两条通道汇聚到同一 SSE 缓冲，共享转换逻辑仅以 `scope`
（`main` / `delegate`）区分归属（`agent_service._convert_event`，src/services/agent_service.py:162）：

- **主通道（graph 主循环）**：`_run_generation` 迭代主图 `astream_events`，事件按
  `scope=main` 转 `status`/`token`/`reasoning`/`citation`/`model_info` 等主语义事件
- **delegate 通道（clarify_channel）**：delegate_task fork 分支（start/end，
  delegate_task.py:93-151）与 executor（thinking/content 增量，executor.py `_flush_delegate`）
  把 `{"type": "delegate", action, delegate_id, skill, kind, delta, ok, reason}` 投进
  `ctx.clarify_channel`，由 `_drain_clarify_channel`（agent_service.py:393，与主图事件循环
  并行消费）转 `SSEDelegateEvent` 写入同一缓冲 → 前端收 `event: delegate`；ask_user /
  web_confirm 澄清卡同走此通道

**scope 隔离**：delegate dict 仅转 delegate 事件，**不写主 token 流 / `full_answer` /
落库内容**（防污染不变量，回归测试覆盖）；fork 子代理内部 LLM 事件不进主
`MODEL_TURN`/`status`（改记 `[delegate]` 轮次日志）。delegate 事件与主事件同入缓冲，
resume 经 `from_payload` 原样回放，保证过程/终态不丢；过程原文不落库，刷新/历史重载
不恢复过程区。

### 看板数据流（task registry / SSE task 事件 / 快照接口）

任务看板以进程内 `SessionTaskRegistry`（`src/chat/task_registry.py`，单例
`task_registry`，key 含 session_id）为状态源，与 streaming_manager 同生命周期假设
（不落库；delegate-hardening-observability core 之上，TTL 口径见
api_contract.md「task 事件详情」）：

```
写路径：create_task / update_task / mark_terminal（Task 工具 plan 项、delegate fork execution 项）
  → emit_task_event（action=created|updated|terminal）→ streaming buffer → SSE event: task 实时推前端
读路径：GET /api/sessions/tasks?session_id=（页面刷新/切会话）
  → task_registry.sweep_expired() → list_session() → data=任务快照列表（权威源，无任务=[]）
```

- **execution 条目自动登记**：delegate_task fork 分支执行时 `create_task(type=execution,
  task_id=delegate_id)` 建 execution 项，终态/中断由 `mark_terminal` 置 status + reason
  （与 delegate end 事件共用 DelegateStopReason 词表）；plan 条目由主 agent Task 工具
  create/update/output/stop 驱动
- **执行活动摘要由前端从 delegate 增量派生**：SSE `task` 事件只携带 coarse `stage`/
  `summary`（start/end/中断边界更新，不做逐 delta 写入）；实时"分析过程"由前端按
  delegate_id 从 delegate 事件（thinking/content 增量）渲染，两者按 delegate_id 关联
- **事件=增量提示、快照=权威**：注册表 TTL 30min 大于事件缓冲 TTL 5min——5min 外 task
  事件已不可达，刷新/切会话一律以快照接口为准，勿因缓冲被清误判任务消失
- **前端只读**：看板无写入口；`task_stop` 仅收敛置 cancelled，运行中 delegate 取消走
  cancel 端点（收敛语义与契约见 api_contract.md「task 事件详情」）

### 链路 2a：绑 KB 问答链（RAG）

- 触发条件：`kb_id` 非空（前端已绑定知识库），`ctx.kb_bound=True`。
- 与 2b 的差异：prompt 允许检索；`retrieve_kb` 真正执行 KB 混合检索；verify 走态 B 校验链
  （年份完整性 → 联网询问/决策 → KB 溯源护栏，无在线忠实度 judge）；SSE 主状态为
  `retrieve` 检索阶段，引用 `kind=kb`（检索不足联网补数据时混入 `kind=web`）。

```
agent（装配产物内：bind_tools 循环）
  ├ retrieve_kb：hybrid 混合检索 + rerank 精排 → ctx.tool_contexts（kind=kb）
  │   dense 路：chunks 表按 kb_id 过滤 + pgvector `<=>` 余弦距离 top-k
  │   词法路：PostgreSQL 全文检索（chunks.tsv @@ to_tsquery('simple', 词元:* | …)，
  │            按 ts_rank 降序；词元全被滤掉时降级为 content 子串匹配）
  │   query 含时间词且 TEMPORAL_PARSE_ENABLED 时先 parse_temporal →
  │   ctx.temporal_years / missing_years（完整性校验数据源，rag_tools.py:109-122）
  ├ search_web：KB 检索不达标时 agent 自主降级联网（web_guided=False → TO_WEB 信号）；
  │   下述 verify 指派补年份（ctx.web_guided=True）属正常步骤、不发缺陷信号
  ├ ask_user：关键实体缺失时澄清（SSEAskUserEvent，前端 composer 接管输入）
  └ 无 tool_calls → agent_finalize（提取末条 AIMessage → answer，读入 tool_contexts）

verify（态 B，verify/node.py:42-62）按序：
  A. completeness_check（checks.py:22）：required=state.verify_temporal_years 与答案实际年份比对
     （required 为空 = 时间解析未触发 → 跳过）
       缺失非空 → decide_missing_web（regen_decision.py:49）并返回其结果，本轮 verify 结束：
         _ask_web_confirm 询问"缺失年份是否联网补充"（SSE ask_user id=web_confirm）
           → 拒绝/超时/槽被占：答案追加"仅覆盖 XX 年"注记 → 直通 format
           → 确认：看 agent 上一轮 search_web queries —— 带全仍缺 → "网络已穷尽"注记直通；
             未调过/带漏 → 注入 VERIFY_GUIDANCE_PROMPT（带漏再加 VERIFY_HINT_PROMPT）→ regen
         （保险丝 _verify_regenerations ≥ MAX_VERIFY_REGENERATIONS=2 → 注记直通，防无限往返）
  B. 无缺失 → kb_citation_guardrail（guardrails.py:137）：有 KB context 但答案无 [n]
       且非拒答/知识库未覆盖 → 注入 KB 溯源指引 → regen 一次（不占 verify 保险丝）
  C. 护栏通过 → 直通 format（在线忠实度 judge 已移除，质量评估转离线另行规划）
→ 通过 → format（nodes.py，[n] → citations 去重，kind=kb/web）
```

- 两路同源于同一个 PostgreSQL 实例的 `chunks` 表（`pg_store.dense_search` / `lexical_search`）；
  融合（RRF）在应用层 `src/rag/fusion.py`，不下推数据库

关键代码：态 B 分派 src/agents/graph/verify/node.py:42-62；联网询问
src/agents/graph/verify/ask_confirm.py:17-72；决策化 src/agents/graph/verify/regen_decision.py:49-168；
KB 溯源护栏 src/agents/graph/verify/guardrails.py:137-179；检索空结果/reretrieve 缺陷信号
src/agents/tools/rag_tools.py:192-204；拒答 → SSEAbstentionEvent（含 abstain_after_retrieve
信号）src/services/agent_service.py:487-509。

### 链路 2b：未绑 KB 纯对话链

- 触发条件：`kb_id` 为空串（会话未绑定知识库），`ctx.kb_bound=False`。
- 与 2a 的差异：prompt 追加未绑定提示（`src/config/prompts/templates/sources.yaml` 的
  `sources-kb-unbound`，及仅在 `search_web` 已注册时追加的 `sources-kb-unbound-web`）明令禁止检索；
  `retrieve_kb` 即便被调也返回空，不产出 kind=kb 上下文；verify 仅走态 A 联网引用引导，
  无年份完整性/联网询问；SSE 主状态为 `web_search` 联网阶段，引用仅 `kind=web`
  （未联网的纯闲聊则无引用）。

```
agent（装配产物内，同一工具列表）
  ├ retrieve_kb：禁止调用（prompt 软引导 + kb_id 空返回空硬保证，双保险）
  ├ search_web：Tavily 并行搜索 + 正文抽取 → ctx.tool_contexts（kind=web），
  │   每轮最多 WEB_SEARCH_PER_TURN_LIMIT 次调用（web_tools.py:61-67）
  ├ ask_user：关键实体缺失时澄清（SSEAskUserEvent）
  └ 无 tool_calls → agent_finalize

verify（态 A，verify/node.py:35-41）：
  web_citation_guard（guardrails.py:68-104）：
    本轮调过 search_web 但答案无 [n] 引用（且未引导过 / 未达保险丝）
      → 注入"请为联网引用标注来源编号"SystemMessage → regen 一次
    其余（未联网 / 已带 [n] / 已引导过 / 保险丝耗尽）→ 直通 format
→ format（引用 kind=web）→ END
```

关键代码：态 A 分派 src/agents/graph/verify/node.py:35-41；联网引用引导
src/agents/graph/verify/guardrails.py:68-104；未绑 KB 禁检索指令由
`src/agents/graph/agent_node.py` 的 `_split_initial_messages` 与 `src/rag/prompt.py` 的
`_build_unbound_message` 承载（模板 `sources-kb-unbound` / `sources-kb-unbound-web` 见
`src/config/prompts/templates/sources.yaml`，联网句按 `search_web` 是否注册条件追加）；
空 kb_id 不检索 src/agents/tools/rag_tools.py:127-130；web 结果写入
src/agents/tools/web_tools.py:131-141。

### 答案校验与引用格式化链路（2a/2b 通用）

`verify → format` 是两条问答链的公共尾段：2a/2b 已按 KB 绑定状态分别描述两套校验分支，
本节给**环节级总览**（谁在什么条件下做什么、代码在哪）并补 format 段细节。

| # | 环节 | 职责 | 触发/条件 | 代码 |
|---|------|------|------|------|
| 1 | 态分派 | 按 `state.kb_id` 分派态 A（纯对话）/ 态 B（绑 KB）两套校验链 | 每次进 verify | `verify/node.py:35-62` |
| 2 | 完整性校验 | 正则 `\d{4}` 提取答案年份，与 `state.verify_temporal_years` 比对 | 态 B 且 required 非空 | `verify/checks.py:22` |
| 3 | 缺失年份联网询问 | 经 clarify_channel 问用户"是否联网补充"；会话内记住（`ctx.web_confirmed`） | 态 B 且有缺失 | `verify/ask_confirm.py` |
| 4 | 缺失年份决策化 | ①用户意愿优先 → ②看上一轮 `search_web` queries 是否带全缺失年份（带全=网络已穷尽→注记直通）→ ③保险丝 → ④注入指引/hint + regen | 态 B 且有缺失 | `verify/regen_decision.py` |
| 5 | KB 溯源护栏 | 有 KB context 但答案无 `[n]` 且非拒答 → 注入溯源指引 → regen 一次 | 态 B 完整性通过后 | `verify/guardrails.py` |
| 6 | 联网引用引导 | 态 A：调过 `search_web` 但答案无 `[n]` → 注入标注指引 → regen 一次 | 态 A | `verify/guardrails.py` |
| 7 | 拒答检测 | 命中 `ABSTENTION_MARKERS` 且无 `[n]` → citations 置空 | format | `nodes.py:66-74` |
| 8 | 引用编号提取/过滤 | 正则提取 `[n]`；非法编号（超出 context 范围）不进 citations，记 `INVALID_CITATION` 信号 | format | `nodes.py:76-98` |
| 9 | 引用去重 | 按 `(source, page)` 去重，保留原始编号 | format | `nodes.py:100-120` |
| 10 | 引用预览片段定位 | `_relevant_snippet`：以"回答与 chunk 的最长公共子串"为中心截 200 字，前缀 `…` | format 每条引用 | `nodes.py:21-58` |
| 11 | 事件输出与落库 | citations → SSE `citation` 事件；同时落 `conversation_history.sources` 供历史回放 | 生成后 | `agent_service.py` |

**几个易忽略的机制**：

- **regen = 一段全新主循环**：verify 指派 regen 时除注入 SystemMessage 外，还**复位**
  `ctx.web_count=0`——否则 regen 轮的 `search_web` 会因配额耗尽而返回限流文案、不真正执行，
  verify 据此误判"网络也没覆盖"。**回合计数无需复位**：`agent` 节点每次 invoke 都以字面
  初值 seed 子图（`_turn_count=0` / `_delegate_used=False`），本就每轮全新。
- **`ctx.web_guided`**：verify 指派联网时置 `True`，用于区分"正常补数据"与"agent 自主降级
  联网"（只有后者才发 TO_WEB 缺陷信号）。
- **两个护栏的保险丝不同**：`kb_citation_guardrail` 只复位 `search_web` 请求级配额
  （`ctx.web_count`）、**不占** verify 保险丝；
  `_verify_regenerations` 仅由决策化路径自增。
- **引用预览不是截开头**：`_relevant_snippet` 用最长公共子串定位"回答真正依据的那一段"，
  避免 parent-child 长 chunk（相关句在深处）的预览与回答无关、误导用户以为引用不支撑回答；
  无有效重叠（`< 15` 字符）时回退取开头 200 字。
- **直出轮判据来源随材料走**：`/xxx` 命中 fork skill 的直出轮主 ctx 无材料，`skill_direct`
  节点把子代理 ctx 的 `tool_contexts` / `temporal_years` 写入 `state.tool_contexts` /
  `state.verify_temporal_years`；verify 的引用护栏（读 `state.tool_contexts`）与完整性校验
  （读 `state.verify_temporal_years`）判据都来自 state 字段而非主 ctx，故直出轮校验不空转。

### 字段级生产-消费矩阵（StateGraph）

两条链路共享同一拓扑与节点实现，矩阵不区分链路。节点通过共享 `AgentState` 间接通信：
每个节点消费若干字段、生产若干字段，字段 key 定义于 `src/agents/graph/state.py`（`format`
节点名取 `LangGraphNode.Format.NAME`，其余节点在 `workflow.build_graph` 以字符串注册）。
字段名生产侧（`agent_node.py` / `nodes.py` / `verify/`）与消费侧（`agent_service.py`）
必须一致，否则运行期报错或静默取空。

| 节点 | 消费字段 | 生产字段 |
|---|---|---|
| `agent` | `messages`（非空 = 重生成轮，回写基准）、`_system_messages`（重生成轮复用）、`_history`、`kb_id`、`query`、`deep_thinking` | `messages`（首轮为组装全量、重生成轮为新增段；循环本体在装配产物内，模型输出含 tool_calls）、`_system_messages`、`answer` |
| `agent_finalize` | `messages` | `answer`, `tool_contexts`, `verify_temporal_years` |
| `skill_direct`（直出轮） | `direct_skill`, `query`, `messages`（查 verify 引用指引） | `answer`, `tool_contexts`, `verify_temporal_years`, `_needs_regenerate` |
| `verify` | `answer`, `kb_id`, `tool_contexts`, `verify_temporal_years`, `messages`（查上一轮 search_web 与指引查重） | `answer`, `_needs_regenerate`, `messages`（regen 指引 SystemMessage）, `_verify_regenerations` |
| `format` | `answer`, `tool_contexts` | `citations` |

> 外层图无独立 `tools` 节点：model↔tools 循环内化在 `agent` 节点调用的装配产物内部，
> 工具事件仍以 `langgraph_node == "tools"` 到达 SSE 转换层（见 §「SSE 消费侧」）。

图入口经 `route_entry(state)` 条件边分派：`state.direct_skill` 非空 → `skill_direct`
（`/xxx` 命中 fork skill 的直出轮，主 agent 零 LLM 轮），否则 → `agent`。

工具（retrieve_kb / search_web / ask_user）不写 state：检索上下文累积到
`RequestContext.tool_contexts`（contextvar），由 `agent_finalize` 读入 `state.tool_contexts`；
ask_count / verify_ask_count / web_count / 澄清通道 / abort 信号均经 contextvar 传递。

SSE 消费侧按事件类型接线（`agent_service._convert_event`，src/services/agent_service.py:159）：
`on_chat_model_start`（节点 `model`）→ "正在思考..."；`on_chat_model_stream`（节点 `model`）
→ `token`（chunk 带 reasoning_content 时另发 `reasoning` 增量）；`on_tool_start/end` 按
工具名映射：`retrieve_kb` → `retrieve` 阶段（"正在检索相关文档.../检索完成..."）、
`search_web` → `web_search` 阶段（"正在联网搜索.../联网搜索完成..."）、`ask_user` 不发状态
（澄清卡由 ask_user/verify 经 clarify_channel 直投）；`on_chain_end`（`format`）→ citations。

### trace_id 贯穿（一次对话请求）

同一条 `trace_id`（格式 `trace_<uuid>`）贯穿请求入口 → 后台任务 → Langfuse trace 根 → 每轮
generation；响应头 / 日志行 / SSE `done` / Langfuse 四处共用同一个值。渠道标识 `channel`
（`web` / `wecom` / `feishu`，见 `src/config/const.py` 的 `Channel`）走同一套 contextvar
机制，落日志行第 5 段与 Langfuse `metadata.channel`：

```
中间件 trace_id_middleware                      src/middleware/trace_id.py
  X-Trace-ID 头 → ?trace_id → new_trace_id()，逐候选经 is_valid_trace_id 白名单校验
  → request.state.trace_id + current_trace_id contextvar → 响应头 X-Trace-ID
→ 渠道入口写 current_channel
  站点 API chat.py set Channel.WEB；企微 handler._run_turn set/reset Channel.WECOM
→ API 层 chat.py：create_task 前捕获 current_trace_id / current_channel（后台任务不共享请求 context）
  → _run_with_finalize(trace_id=..., channel=...) 写 SSE done 终态事件
  → 任务入口 set current_trace_id / current_channel（finally reset），
    answer_builder 按调用时读取 current_trace_id 传入 _run_generation
→ trace 根 _run_generation（@observe name=chat_turn）    src/services/agent_service.py
  入参 langfuse_observation_id=<trace_id> → 根 observation id 即 trace id
  → update_current_trace 写 input / session_id / metadata（含 channel）
→ generation agent_turn（命令式 span，middleware 内）    src/agents/graph/middleware.py
  每轮推理一个 generation（trace 根的子 observation），显式回填 model / input / output / usage；
  `completion_start_time`（TTFB）在该层不可得，故不设置（见 ADR-0016）
```

日志行的段位（第 3/4/5 段 = trace_id / session_id / channel）见 `docs/agents/logging-rules.md`；
按 trace_id 分环境/分渠道排障的完整指导见 `docs/agents/troubleshooting.md`。

trace 产出受 `LANGFUSE_ENABLE` 开关治理；开关、flush 与 trace id 校验的唯一入口是
`src/infra/llm/tracing.py`（口径见 glossary.md「可观测后端」）。

## 链路 3：知识库与文档管理

```
创建知识库 → PostgreSQL get_or_create（名称去重）→ 返回 kb_id
列出知识库 → PostgreSQL 查询 + 文档计数
删除文档 → DocumentService.delete_document：校验（存在 / 属主 / 状态）→ 同一事务（删分块 + 软删文档）
删除知识库 → AppService.delete_knowledge_base：同一事务（软删文档 + 删分块 + 软删 KB）
```

入口: `POST /api/kbs` / `POST /api/kbs/list` / `POST /api/kbs/delete` /
`POST /api/kbs/documents/delete`

删除链路的跨表写以 `session_scope` / `Repo.transaction()` 打开唯一事务，参与者传 `session=`；
任一步失败整体回滚，**且不得吞异常**（历史缺陷：KB 删除时 `except Exception: logger.warning`
让孤儿行永久存在）。规则与失败模式见 `docs/agents/defensive-patterns.md`「派生写操作跨事务」；
删除端点的状态码契约见 `docs/agents/api_contract.md` §2.2.5。

## 链路 4：会话管理 ★

```
列出会话 → PostgreSQL 查询最近 50 条
查看消息 → PostgreSQL 查询 session 消息历史
删除会话 → Redis 清理 → PostgreSQL 事务删除 session + 消息
```

入口: `POST /api/sessions/list` / `POST /api/sessions/messages` / `POST /api/sessions/delete`

## 链路 5：认证

```
登录/注册 → PostgreSQL 查用户 → 密码校验/自动注册 → Redis 存 token
校验 → Redis 查 token → 返回 user_id
登出 → Redis 删 token
匿名 → 生成 UUID → Cookie 持久化
```

入口: `POST /api/auth/login` / `POST /api/auth/verify` / `POST /api/auth/logout` / `POST /api/auth/anonymous`

## 链路 6：DeepEval 质量评估（CLI）

**子链路 6a — 测试集准备**：出题模块已退役，评测直接加载固定版本测试集 JSON
（`data/deepeval/testset/testset_<kb_id>_v<N>.json`）；`--generate` 保留参数、命中即报错。

**子链路 6b — 评估执行**：
```
加载测试集 JSON → 初始化 RAGChain → 对每条 QA 检索+生成
→ DeepEval 原生四指标评分（串行）→ 诊断指标写 detail_json（不进主分）
→ 保存 CSV + Markdown → 写入 eval_report 表
```
命令: `python -m src.cli.eval_deepeval --kb-id xxx`（加 `--gate` 启用质量门禁；新口径首轮用 `--run-type baseline`，不判门禁）

## 链路 7：分块质量评估（嵌入在链路 1 中）★

```
文档分块后 → 结构完整性评分 → 语义断裂率(SBR) → 粒度变异系数(CV)
→ 综合评分 → 写入 meta_info.eval（仅记录，不阻塞）
```

由 `CHUNK_EVAL_ENABLED` 开关控制，`src/chunking/scorer.py` 的 `ChunkQualityScorer` 实现
（document_service.py 在分块后调用 evaluate）

## 链路 8：工具性链路

- **LLM 连通性测试**: `POST /api/llm/test` — 验证模型可用性
- **检索质量调试**: `python -m src.cli.check_retrieval --kb xxx --query "..."` — 纯检索+精排，不调 LLM
- **健康检查**: `GET /api/health` / `POST /api/config`

> ★ 标注的为关键链路

> 新增链路或修改节点字段矩阵（AgentState 生产-消费）时，同步更新本文件对应链路。
