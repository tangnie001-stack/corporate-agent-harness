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

- [ ] 3.1 `WECOM_BOTS` 条目解析增加可选 `profile`（默认 `chat`），校验值必须存在于已装配 profile，否则启动失败并指出 `key`
- [ ] 3.2 handler 在确定 `bot_key` 处解析 `profile`，并作为 `start_turn(agent=...)` 传入；日志增加 `profile=` 维度
- [ ] 3.3 新增开关 `WECOM_AGENT_ROUTING_ENABLED`（默认 true）；关闭时 profile 一律按 `chat` 处理
- [ ] 3.4 补测试：有 profile 时传入 agent；无 profile 时回落 chat；开关关闭时回落 chat；未知 profile 启动失败

## 4. Agent 注册表化

- [ ] 4.1 `AgentState` 增加 `profile: str = ""`，`make_initial_state` 透传
- [ ] 4.2 `AgentService` 启动期按 profile 装配 Agent 实例（chat / bot-ops / bot-dev），存入字典
- [ ] 4.3 `agent` 节点按 `state.profile` 取实例；未命中回落 `chat` 并记 warning
- [ ] 4.4 profile 轮次预算：chat 5、bot-ops 8、bot-dev 8，经 `build_agent(max_turns=...)` 提供
- [ ] 4.5 补测试：两个 profile 走同一外图；未知 profile 降级；预算生效

## 5. 日志工具（仅 ops/dev）

- [ ] 5.1 新增 `src/agents/tools/log_tools.py`：`sls_query_latency(uri, minutes=15, slow_ms=1000)` → 5 行摘要文本
- [ ] 5.2 同文件：`sls_query_requests(uri, minutes=15, limit=20)` → 最多 20 行、6 字段、节点别名
- [ ] 5.3 `uri` 精确白名单校验（读 `RCA_TOOL_URI_WHITELIST`），非法返回错误并列出可选接口
- [ ] 5.4 空结果与失败返回不同文案（「近 N 分钟无请求记录」 vs 「查询失败：…」）
- [ ] 5.5 单次 8 秒超时、不重试；超时返回失败文案
- [ ] 5.6 工具注册进 `ToolRegistry`（readonly）并仅在 `bot-ops` / `bot-dev` 的 Agent 工具面中出现
- [ ] 5.7 日志：`[tool] mcp call bot=… chatid=… tool=… uri=… minutes=… elapsed_ms=… rows=…`
- [ ] 5.8 补测试：白名单内外、参数越界、空 vs 失败、脱敏（无 IPv4/UA）、行数上限

## 6. trace 约定

- [ ] 6.1 `_analyze_turn` 加 `@observe(name="alert_turn", capture_input=False)`，传 `langfuse_observation_id=trace_id`
- [ ] 6.2 标签改为三类：`kb|no_kb`、渠道、`pf:<profile>`；移除 `chat` 写入
- [ ] 6.3 metadata 增加 `profile`、`tools_used`；告警轮增加 `rca_tier` / `rca_reason` / `rca_evidence` / `rca_elapsed_ms`
- [ ] 6.4 补测试：标签组合；tools_used 记录；告警轮 metadata 可读；trace 起点不晚于首个 span

## 7. 文档登记

- [ ] 7.1 `docs/agents/logging-rules.md`：前缀主表新增 `[tool]` 及字段说明
- [ ] 7.2 `docs/agents/troubleshooting.md`：新增「trace 维度约定」（tag 值域表、metadata 字段表、为什么工具不进 tag、`chat` 已废弃）
- [ ] 7.3 `docs/agents/glossary.md`：登记 Agent 实例 / profile / preset 三个词及区别
- [ ] 7.4 `.env.template` / `.env.example`：补 `WECOM_AGENT_ROUTING_ENABLED`、`RCA_TOOL_URI_WHITELIST` 与 `WECOM_BOTS` 的 `profile` 示例

## 8. 验证与发布

- [ ] 8.1 全量测试：`POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff check .` 无错误；无新增 pyright error
- [ ] 8.2 本地端到端：起本地实例（仅 ops），@ 一次确认 `pf:ops` + 日志工具可用；再发假 webhook 确认告警 trace 名变为 `alert_turn` 且起点不晚于取数 span
- [ ] 8.3 发布后验收：测试群 @ops 问「最近 15 分钟 createOrder 正常吗」→ 回答含真实数字；日志有 `[tool] mcp call`；Langfuse trace 有 `pf:ops` 与 `tools_used`
- [ ] 8.4 回滚演练：`WECOM_AGENT_ROUTING_ENABLED=false` + 重建，确认回落通用人设且不影响站点
