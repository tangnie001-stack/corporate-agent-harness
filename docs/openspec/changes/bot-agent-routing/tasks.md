## 1. 预设 schema 与四个机器人预设

- [ ] 1.1 `AgentPreset` 增加 `hidden: bool = False` 与 `channel: str = ""`；加载器解析同名 frontmatter 字段
- [ ] 1.2 加载期校验：`bot-` 前缀必须 `hidden=true` 且 `channel=wecom`，否则该预设加载失败（不注册、指出文件名）
- [ ] 1.3 新增 `agents/bot-ops.md`（运维值班人设：先看证据、给处置动作，要求调用日志工具前说明查询目的）
- [ ] 1.4 新增 `agents/bot-dev.md`（代码工程师人设：给可疑代码路径与验证方法）
- [ ] 1.5 新增 `agents/bot-support.md`（客服人设，无工具差异）
- [ ] 1.6 新增 `agents/bot-finance.md`（财务人设，无工具差异）
- [ ] 1.7 为 1.1–1.2 补单测：字段解析、前缀校验失败、目录内其他预设不受影响

## 2. 可见性与渠道约束

- [ ] 2.1 `CapabilityService.list_agents` 过滤 `hidden=true`；`/api/agents` 断言不含 `bot-*`
- [ ] 2.2 生成入口校验：请求来源渠道与预设 `channel` 不符时忽略该 agent 并记 `AGENT_BIND_IGNORED`（沿用现有事件）
- [ ] 2.3 补测试：站点清单过滤；站点请求 `agent=bot-ops` 被忽略且不报错

## 3. 企微路由接入

- [ ] 3.1 `WECOM_BOTS` 条目解析增加可选 `profile`（缺省为空 = 通用人设），校验分两段：
  - **解析期（`load_wecom_bots`，结构）**：非空字符串、字符集合法
  - **lifespan 启动企微驱动前（存在性）**：预设注册表此时**尚未构建**（在 `AgentService.__init__`，且 skills 为空则不建；AppService 首次请求才创建），因此用 `AgentPresetLoader(agents_dir).load_all()` **直载取名集合**做校验（与运行时注册表同源、不依赖 skills 门控），把该判定注入 `wecom_service.start()`；缺失 → `RuntimeError` 指出 `key`
  - `agents/` 目录缺失时跳过存在性校验并记 warning（此时站点预设同样不可用，属既有降级路径）
- [ ] 3.2 handler 在确定 `bot_key` 处解析 `profile`，并作为 `start_turn(agent=...)` 传入；日志增加 `profile=` 维度
- [ ] 3.3 新增开关 `WECOM_AGENT_ROUTING_ENABLED`（默认 true）。关闭时在 `_resolve_session_agent` 增加 override，**两个条件同时满足才生效**：开关关闭 **且** `current_channel.get() == Channel.WECOM`（站点 `Channel.WEB` / 告警 `Channel.ALERT` 路径不受影响）。override 语义 = 把 `bound`/`requested` 都当空、返回 `effective=""`（通用人设），**不调用** `bind_session_agent_async`（不写库、回滚可逆）
- [ ] 3.4 补测试：有 profile 时传入 agent；无 profile 时回落通用人设；开关关闭 + 企微通道回落通用人设；**开关关闭时站点已绑定预设的会话仍沿用绑定**；**override 后 `sessions.agent` 未被改写**；未知 profile 启动失败

## 4. Agent 注册表化

- [ ] 4.1 `AgentState` 增加 `profile: str = ""`，`make_initial_state` 透传
- [ ] 4.2 `AgentService` 启动期按**能力档**装配 Agent 实例：`default`（基础工具、5 轮）与 `log`（基础工具 + 两个日志工具、6 轮），存入字典；`bot-ops`/`bot-dev` → `log`，**其余（含站点预设、`bot-support`、`bot-finance`、通用人设）→ `default`**。`alert` 不作为档，复用 `default` 的无澄清图
- [ ] 4.3 `agent` 节点按 `state.profile` 查档取 bundle；未命中（未注册 profile）回落 `default` 并记 warning
- [ ] 4.4 轮次预算随档：`default` 5、`log` 6，经 `build_agent(max_turns=...)` 提供（与 2 次重生成叠加后仍可控）
- [ ] 4.5 补测试：两个档走同一外图（节点序列一致）；未知 profile 降级；预算生效；`bot-support`/`bot-finance` 声明 profile 后**能正常启动**（不因未单独装配而失败）
- [ ] 4.6 按档位化三处单值依赖：`workflow.build_graph` 持有 `{(能力档, clarify): AgentLoopBundle}`（替换现在单参 `tools` 的做法）、`tool_sink`（工具面快照，`agent_service.py:1074/1104`）与 `ctx.tool_names`（verify/regen 判据，`:1221`）。**注意载体**：prompt 的工具段读 `bundle.tool_names`（`agent_node.py:282` → `build_prompt`，编译期 `workflow.py:117` 固定），不是 `ctx.tool_names`（只喂 `regen_decision.py:72`）；补断言：`log` 档的 bundle.tool_names 含两个日志工具、`default` 档不含

## 5. 日志工具（仅 ops/dev）

- [ ] 5.0 启动期（lifespan）**仅当有机器人引用日志类 profile（`bot-ops`/`bot-dev`）时**才连 MCP 并 `tools/list`：连接失败 → warning 并让日志档工具进入降级态（`McpLogSource` 不可用，工具返回「查询失败：日志服务不可用」，**不阻断启动**）；连接成功但缺少期望工具 → 启动失败（契约不符）。无引用时不连、不校验（`AgentService.__init__` 是同步函数，发现逻辑只能放 lifespan）
- [ ] 5.1 新增 `src/agents/tools/log_tools.py`：`sls_query_latency(uri, minutes=15, slow_ms=1000)` → 5 行摘要文本
- [ ] 5.2 同文件：`sls_query_requests(uri, minutes=15, limit=20)` → 最多 20 行、6 字段、节点别名
- [ ] 5.3 `uri` 精确白名单校验（读 `RCA_TOOL_URI_WHITELIST`），非法返回错误并列出可选接口；**当有机器人引用日志类 profile 时**，白名单为空即启动失败（避免所有查询被静默拒绝）；无引用的部署不受该变量约束，可缺省
- [ ] 5.4 空结果与失败返回不同文案（「近 N 分钟无请求记录」 vs 「查询失败：…」）
- [ ] 5.5 单次 8 秒超时、不重试；超时返回失败文案
- [ ] 5.6 工具注册进 `ToolRegistry`（readonly），只出现在 `log` 档的 Agent 工具面中（`bot-ops`/`bot-dev`），`default` 档不含
- [ ] 5.7 日志：`[tool] mcp call bot=… chatid=… tool=… uri=… minutes=… elapsed_ms=… rows=…`
- [ ] 5.8 补测试：白名单内外、参数越界、空 vs 失败、脱敏（无 IPv4/UA）、行数上限

## 6. trace 约定

- [ ] 6.0 **spike**：本地起实例发一条假 webhook，验证 `alert_turn` 根方案是否得到「一条 trace + 根早于首个取数 span」。**回退分支要整组收口**：若 `langfuse_observation_id` 嵌套撞 id，则取消 6.1，同时删除/改写 6.4 的「起点不晚于首个 span」断言、8.2 与 8.3 的 `alert_turn` 验收、llm-tracing delta 的「告警轮根开在告警入口」条款、design Risks 同名条目，以及 **proposal 的 BREAKING 句**（trace 名 `chat_turn`→`alert_turn`）——只取消 6.1 会让 8.2/8.3 验收与 BREAKING 声明双双失真
- [ ] 6.1 `_analyze_turn` 加 `@observe(name="alert_turn", capture_input=False)`，传 `langfuse_observation_id=trace_id`（依赖 6.0 通过）
- [ ] 6.2 标签改为三类：`kb|no_kb`、渠道、`pf:<profile>`；移除 `chat` 写入
- [ ] 6.3 metadata 增加 `profile`、`tools_used`；告警轮增加 `rca_tier` / `rca_reason` / `rca_evidence` / `rca_elapsed_ms`
- [ ] 6.4 补测试：标签组合；tools_used 记录；告警轮 metadata 可读；trace 起点不晚于首个 span（**仅 6.0 通过时**；回退时该断言改为「trace 名为 `chat_turn`、起点为生成开始」）

## 7. 文档登记

- [ ] 7.1 `docs/agents/logging-rules.md`：前缀主表新增 `[tool]` 及字段说明
- [ ] 7.2 `docs/agents/troubleshooting.md`：新增「trace 维度约定」（tag 值域表、metadata 字段表、为什么工具不进 tag、`chat` 已废弃）
- [ ] 7.3 `docs/agents/glossary.md`：登记 Agent 实例 / profile / preset 三个词及区别
- [ ] 7.4 `.env.template` / `.env.example`：补 `WECOM_AGENT_ROUTING_ENABLED`、`RCA_TOOL_URI_WHITELIST`（标注「有机器人引用 `bot-ops`/`bot-dev` 时必填，缺省启动失败；否则可缺省」）与 `WECOM_BOTS` 的 `profile` 示例

## 8. 验证与发布

- [ ] 8.1 全量测试：`POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff check .` 无错误；无新增 pyright error
- [ ] 8.2 本地端到端：起本地实例（仅 ops），@ 一次确认 `pf:ops` + 日志工具可用；再发假 webhook 确认告警 trace 名变为 `alert_turn` 且起点不晚于取数 span（后半句**仅 6.0 通过时**；回退时改为确认 trace 名仍为 `chat_turn`）
- [ ] 8.3 发布后验收：测试群 @ops 问「最近 15 分钟 createOrder 正常吗」→ 回答含真实数字；日志有 `[tool] mcp call`；Langfuse trace 有 `pf:ops` 与 `tools_used`（告警 trace 名同理受 6.0 结论约束）
- [ ] 8.4 回滚演练：`WECOM_AGENT_ROUTING_ENABLED=false` + 重建，确认回落通用人设且不影响站点
