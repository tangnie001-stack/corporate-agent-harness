# MCP 工具如何按 agent / 会话隔离：主流框架实读

> 调研日期：2026-11-05 ｜ 性质：外部事实冻结记录
> 问题：主流框架是「每个工具集编译一张图」，还是「一张图 + 运行时过滤」？不同智能体用不同 MCP server 时怎么做？

## 结论

1. **没有「专用图」这个业界概念**。LangGraph 里「一张图 ≈ 一个 agent」——所以「每个 agent 一套工具」在 LangGraph 里**表现出来就是每个 agent 一张图**，这是主流，不是偏门。
2. **工具的归属单位是 agent 实例，不是会话，也不是运行（Run）**。OpenAI Agents SDK 把 MCP server 挂在 `Agent(...)` 上；LangChain 把 MCP 工具转成普通工具后传给 `create_agent`。两者都是**构建期定工具集**。
3. **一个 agent 可以挂多个 MCP server**，工具名做命名空间前缀（Claude Code 是 `mcp__server__tool`，OpenAI SDK 有 `include_server_in_tool_names`）。
4. **运行时换工具不是一等能力**。各家的做法都是「换 agent 实例 / 换配置」，而不是「同一实例中途换工具」。
5. **权限限制的落点是工具清单本身**：Claude Code 的 subagent 用 `tools` 允许列表限定，写错的工具名直接启动失败——**不是"注册了再拒绝"**。

## 四家对照

| 框架 | 工具/MCP 挂在哪 | 多 MCP server | 不同 agent 用不同工具 | 动态换工具 |
|---|---|---|---|---|
| **OpenAI Agents SDK** | `Agent(mcp_servers=[...])` | ✅ `MCPServerManager` 可挂多个到一个 agent | ✅ 每 agent 各自配置 | ❌ 重建 agent |
| **LangChain / LangGraph** | MCPAdapter 发现 → 转 LangChain 工具 → `create_agent(tools=...)` | ✅ 多个 adapter 的产物拼成一个列表 | ✅ 每个 agent 图各自构建 | ❌ 工具列表在构建期固定 |
| **Claude Code** | 会话级 MCP 配置（local/project/user 三档作用域） | ✅ 一个会话连多个 server | ✅ subagent 用 `tools` 允许列表 | ❌ 启动期解析 |
| **MCP 规范** | server 暴露 tools；client 发现并调用 | ✅ client 可连多个 server | 规范不管，属于宿主实现 | 规范不管 |

## 原文摘录

### OpenAI Agents SDK

> "In addition to choosing a transport, you can tune how MCP tools are prepared by setting `Agent.mcp_config`."

```python
agent = Agent(
    name="Assistant",
    mcp_servers=[server],           # server 挂在 Agent 构造参数里
    mcp_config={"include_server_in_tool_names": True},
)
```

`Runner.run(agent, "...")` **没有** `mcp_servers` 参数 ⇒ MCP 是 Agent 的属性，不是 Run 的属性。

来源：https://openai.github.io/openai-agents-python/mcp/

### LangChain

> "`MCPAdapter` … discovers a server's tools and adapts them into LangChain tools you can pass straight to `create_agent`."

来源：https://docs.langchain.com/oss/python/langchain/mcp
（旧库 `langchain-mcp-adapters` 已停止维护，MCP 支持迁入 `langchain.mcp`：https://github.com/langchain-ai/langchain-mcp-adapters）

### Claude Code

作用域三档：

| 作用域 | 加载范围 | 共享 |
|---|---|---|
| Local（默认） | 仅当前项目 | 否 |
| Project（`.mcp.json`） | 仅当前项目 | ✅ 进版本库 |
| User | 全部项目 | 否 |

> "When the same server is defined in more than one place, Claude Code connects to it once, using the definition from the highest-precedence source."

工具命名：`mcp__server__tool`（插件内置 server 为 `mcp__plugin_<plugin>_<server>__<tool>`）。

来源：https://code.claude.com/docs/en/mcp

subagent 的工具限制：

> "`tools` … Tools the subagent can use … **Inherits every tool available to subagents if omitted.**"
> "If both are set, `disallowedTools` is applied first, then `tools` is resolved against the remaining pool."
> "When nothing in the `tools` list resolves to a tool … Claude Code usually refuses to launch the subagent."

来源：https://code.claude.com/docs/en/sub-agents

### MCP 规范

> "Each tool is uniquely identified by a name and includes metadata describing its schema."
> "Tools in MCP are designed to be **model-controlled**, meaning the language model can discover and invoke tools automatically."

规范只定义发现与调用，**没有**「server 按 client 过滤工具」的机制；隔离发生在宿主（客户端）侧。

来源：https://modelcontextprotocol.io/specification/2025-06-18/server/tools

## 对我们场景的启示

### 1. 「每个机器人一张图」不是必须，业界单位是「工具集」

我们 4 台机器人里只有 ops/dev 需要日志工具，finance/support 不需要。按工具集划：

```
工具集 profile：chat（KB+Web） / ops（chat + 日志工具）
                   │
        bot_key ──▶ profile ──▶ 选图
```

- 图的数量 = **profile 数 × clarify 两态**，不是机器人数
- 以后加第二个 MCP（比如支付数据），只是新增一个 profile，不新增机器人维度的图

### 2. 不要走「全局注册 + 运行时拒绝」

Claude Code 的 subagent 是**启动期允许列表**，不在列表里的工具根本不出现。这与我们现有 `_graph_no_clarify`（按轮选图）是同一思路。

反例（全局注册 + 工具内鉴权）会让模型看到不该用的工具、反复试错，答案里出现「无权查询」；也把权限判断散进每个工具实现。

### 3. 多 MCP 的工具名要带命名空间

MCP 规范不保证跨 server 唯一，Claude Code 用 `mcp__server__tool`，OpenAI SDK 有 `include_server_in_tool_names`。我们现在的 `sls_latency_stats` 已经自带前缀，但接入第二个 server 时应统一成 `<server>_<tool>` 或 `<server>__<tool>`，否则撞名是迟早的事。

### 4. 动态工具集不值得做

四家都不支持「同一 agent 中途换工具」。我们现有的「启动期编译两张图 + 按轮选」已经是业界同款，不需要为了「更灵活」改成运行时 bind。

## 未覆盖

- Vercel AI SDK / Dify / n8n 的 MCP 接入未实读（时间优先给了四家主线）
- LangGraph 是否有官方"multi-agent with per-agent tool sets"示例，未逐页核对

---

## 附：MCP 多起来之后怎么处理（官方最佳实践）

来源：https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices

> "Loading every tool definition into the model's context window upfront wastes tokens, increases latency, and degrades model performance."

官方给出的两个互补手段：**progressive discovery**（控制工具定义何时进上下文）与 **programmatic tool calling**（控制工具怎么被调用）。

### 1. 渐进式发现（工具多时）

- 启动仍可 `tools/list`，但**延迟注入**模型上下文
- 先给一个轻量 `search_tools` 元工具，按需再加载完整 schema
- **触发阈值**：工具定义占上下文 **1%–5%** 时开始切换
- 三层次：catalog（只回名字+一句话）→ inspect（取单个 schema）→ execute
- 官方给出的对比：全量加载约 **150,000 tokens**，渐进发现约 **2,000 tokens**
- 检索策略：keyword(BM25/regex) / embedding / 小模型选工具 / hybrid

### 2. 动态 server 管理

> "Rather than connecting to every configured server at startup, a host can: 1. Maintain a registry… 2. Connect to a server only when the model determines it needs… 3. Disconnect servers that are no longer relevant."

且提到 **skill 文件可以声明自己需要哪些 MCP server**，与 agent skills 配合。

### 3. 代码模式（programmatic tool calling）

把工具当代码 API 调用，中间结果留在代码里，只把最终结果回给模型——同时省掉「工具定义」和「工具结果」两份 token。

### 4. 命名冲突

OpenAI Agents SDK JS 文档：

> "If two local MCP servers expose the same tool name, the SDK raises a duplicate tool name error because the model cannot safely choose between them."

解决方式是给工具名加 server 前缀（`includeServerInToolNames`）；Claude Code 固定用 `mcp__server__tool`。

### 5. 工具清单缓存（OpenAI SDK）

> "each time an Agent runs it may call list_tools() … you can cache the results in memory by passing cacheToolsList: true … Only enable this if you're confident the tool list won't change."

并提供 `invalidateToolsCache()` / 按 server 名失效 / 自定义 cache key。

### 对我们场景的判断

| 指标 | 我们现在的量级 |
|---|---|
| MCP server 数 | 1（SLS），将来 +1（支付库） |
| MCP 工具数 | 3 |
| 图内工具总数 | 现有 10 个 + MCP 3 个 ≈ 13 |

远低于需要渐进发现的量级（官方阈值是"几十个 server、数百个工具"）。**现阶段直接全量加载即可**，把上面这些作为将来的扩展路线，不要在工具只有 3 个的时候引入 `search_tools`。
