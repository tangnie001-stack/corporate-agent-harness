# MCP 接入调研：工具面策略 / 传输选型 / 参考实现

- **调研日期**：2026-10-13
- **调研方式**：业界一手文档检索（Firecrawl，≈19 个来源）+ 本地 `../github/` 参考项目实读源码
- **范围**：在 corporate_rag harness（Python 3.12 / FastAPI / LangChain 1.3.11 / LangGraph 1.2.9）中接入 MCP 客户端，让 agent 调用外部 MCP server 的工具
- **本文件性质**：**事实与依据的冻结记录**，不替代决策（决策归 `docs/adr/`）。文中数字为 2026-10-13 的调研记录，不随后续实现回写

---

## 一、结论摘要

1. **业界主流不是"选一种工具面策略"，而是"按阈值切换"** —— 小工具集全量注入，工具定义占到上下文窗口约 1%–10% 时切换到**渐进发现（progressive discovery）**。这一形态已被 MCP 官方规范写成推荐做法，Anthropic / OpenAI / Claude Code / LangChain 全线实现。
2. **准确率的拐点比 token 更早出现**：官方口径是"**超过 30–50 个工具，选择准确率就开始下降**"。此前团队内部讨论中的"10 个以内没问题、30+ 撑爆"方向正确，但"撑爆"应改为"准确率下降"，token 只是次要因素。
3. **本仓 `langchain 1.3.11` 已自带两套现成中间件**（`LLMToolSelectorMiddleware`、`ProviderToolSearchMiddleware`，已逐行核实），A''/D/E 三条路线基本不必自研。这是本次调研最高价值的发现。
4. **传输层不需要纠结**：只用 stdio + Streamable HTTP 两条；SSE 已于 2025-03-26 废弃，且最新规范（2026-07-28）进一步删掉了 GET 流端点与协议级会话。
5. **阿里云百炼广场在协议层与普通 MCP 无差别**（标准 MCP over Streamable HTTP），区别只在"它是一组独立 endpoint + 统一鉴权 + 平台计费限流 + 控制台开通前置"。
6. **工具面感知的 `readonly` 有协议来源**：MCP 规范定义了工具注解 `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`，可直接对接本仓 `ToolRegistry` 的 readonly 双轴（注意默认值是"按危险处理"）。
7. **反向证据不可忽略**：有调查称数百个做 agent 集成的团队中"**不到 10 家把 MCP 留在生产**"；且 116 个官方 MCP server 里 88.6% 只是 REST API 的包装。接入 MCP 的价值在于"以后能挂别人维护的 server"，若只为自用少数几个能力，自定义工具的可靠性/成本更优。

---

## 二、项目现状：接入点盘点

**结论：本仓当前没有 MCP 客户端，但预留了三个接入位。**

| 事实 | 证据 |
|---|---|
| 无 MCP 客户端依赖 | `pyproject.toml` 无 `langchain-mcp-adapters`、无 `mcp` SDK；全仓 grep `MultiServerMCPClient` 无结果 |
| 工具注册入口已预留 | `src/agents/tools/registry.py:3` 模块 docstring 明写「为 MCP 接入与未来 subagent 工具预留统一入口」，`:5` 写「注册表存异构可调用对象（LangChain tool / **未来 MCP 工具**）」 |
| 观测层已预留扩展点 | `src/infra/llm/tool_trace.py` 有注释「扩展点（MCP 接入时在此加逻辑，本期不实现）」 |
| 工具装配唯一入口 | `src/agents/tools/rag_tools.py:255-266` 的 `make_rag_tools()` 组装并注册工具；`src/agents/graph/agent_factory.py:41` 的 `build_agent()` 是唯一调 `create_agent` 的地方 |
| 工具面当前规模 | 固定三件套 `retrieve_kb` / `ask_user`（+ `search_web` / `delegate_task` 按开关与 skill 库内容追加） |
| 已有的按需筛选机制 | `fork_tool_pool` + `SkillExecutor` 的 `tool_provider`（`src/services/agent_service.py:826`）——fork 子代理的工具按 skill 白名单筛 |
| 已有预设机制 | `src/agents/presets/`（智能体预设），`_resolve_session_agent` 绑定校验 |
| 已过工具只读性 | `ToolRegistry.register(..., readonly=...)` → `declare_readonly()` 进程级声明表（`src/agents/tools/readonly.py`） |

**含义**：A / A'' 两条路线都能直接接到现有 `ToolRegistry` 与 `ToolNode` 链路上，不需要新造工具抽象。

---

## 三、本仓已有可复用能力（已核实）

在 `/opt/venvs/corporate_rag/lib/python3.12/site-packages/langchain/agents/middleware/` 下实测存在以下中间件（**注意：这是"已装但本仓未启用"的能力**）：

| 中间件 | 文件:行号 | 做什么 |
|---|---|---|
| `LLMToolSelectorMiddleware` | `tool_selection.py:93` | 每轮让 LLM 用结构化输出选工具子集；构造参数 `max_tools` / `always_include`（`:127-128`）；`always_include` 的工具**不占 `max_tools` 配额**（`:201-207`），可表达"内置工具永远全量"；在 `wrap_model_call` 里 `request.override(tools=...)` |
| `ProviderToolSearchMiddleware` | `provider_tool_search.py:57` | 给工具标 `extras["defer_loading"]`，注入 provider 原生 `tool_search_tool_bm25_20251119`（`:50-51`），schema 由**服务端**按需检索 |
| `ContextEditingMiddleware` / `ClearToolUsesEdit` | `context_editing.py` | 清理历史里累积的旧工具结果（治的是"工具**结果**膨胀"，与"工具**定义**膨胀"是两件事） |
| `summarization.py` | 同目录 | 历史摘要 |

**关键限制（待实测）**：Claude Code 官方文档说明，当 `ANTHROPIC_BASE_URL` 指向非一方 host 时它会自动关闭 tool search，因为"**大多数代理不转发 `tool_reference` block**"。本仓走自建 provider 兼容层（`src/infra/llm/provider/`，当前主模型 DeepSeek），因此 `ProviderToolSearchMiddleware`（D 路线）**很可能不可用**——须实测后才能写进方案。

---

## 四、工具面策略谱系

### 4.1 七种形态

| 代号 | 形态 | 一句话原理 | 代表实现 |
|---|---|---|---|
| **A** | 全量物化 | 装配期把每个远端工具变成本地工具对象，全部注入 | 朴素 MCP host；`Qwen-Agent/qwen_agent/tools/mcp_manager.py:212`；**deepseek-harness** |
| **A''** | 检索裁剪（向量） | 同上，但每轮用向量相似度只注入 top-k 子集 | **`langgraph-bigtool`**（LangChain 官方库，`retrieve_tools_function`）；**CowAgent `tool_retrieval.py`** |
| **E** | 检索裁剪（LLM） | 同上，但用一个小模型/结构化输出选子集 | `LLMToolSelectorMiddleware`；MCP 官方称谓 subagent-based |
| **D** | provider 原生延迟加载 | 工具标 `defer_loading`，schema 不进初始 prompt，模型搜到才展开 | Anthropic Tool Search Tool；OpenAI `tool_search`；`ProviderToolSearchMiddleware` |
| **C** | 元工具（裸） | 只暴露 discover/call 两个工具，模型读清单后**手拼参数** | meta-tool 模式文章 |
| **G** | 分层暴露（C 的精修） | 提示里只放压缩清单，schema 与指令由元工具**懒取** | MCP 官方 Catalog / Inspect / Execute 三层；**openakita `mcp_catalog.py`** |
| **F** | 按专业分区 | 每个 agent 只绑自己的 server 子集 | 官方 "task-specific sub-agents"；`awesome-llm-apps-mcp_ai_agents/multi_mcp_agent_router`（关键词规则路由） |

**A 与 A''/D/E 的本质区别**：A/A''/D/E 下 **schema 在框架手里**（只是"进不进 prompt、进几个"的问题），参数校验、观测粒度、只读性声明全部保留；**C 下 schema 只在 discover 的文本结果里露脸**，模型盲填参数，观测塌陷成一个入参 JSON。

### 4.2 阈值切换才是主流落地形态

- **MCP 官方客户端最佳实践**（规范侧）：实现**按上下文窗口百分比**的阈值（建议 1%–5%），未达阈值就全量加载，达到后切换到渐进发现。检索策略四选一：keyword（BM25/regex）/ embedding / subagent-based / hybrid。并明确"**provider 已提供原生 tool search 时优先用它；provider 不提供、或需要特殊检索逻辑（如领域排序、访问控制过滤）时才自建**"。
- **Claude Code 的实测口径**：`ENABLE_TOOL_SEARCH` 默认 `auto` = 工具定义占上下文窗口 **10%** 时激活，`auto:N` 可自定义；激活后默认只把**最多 5 个**最相关工具加载进上下文；官方明说"**少于约 10 个工具时，全量加载通常更快**"；且"工具搜索每次会多一次 round-trip，但在大工具集下被更小的上下文抵消"。
- **官方推荐的三层检索结构**：Catalog（`search_tools` 返回名字 + 一句话）→ Inspect（`get_tool_details` 取单个完整 schema）→ Execute。

### 4.3 实证数据

| 数据 | 值 | 来源 |
|---|---|---|
| 工具定义开销 | 约 **200 tokens / 工具**；100 个候选 = 20K tokens | arXiv 2605.24660（Meta，2026-05-23） |
| 真实 server 组合开销 | GitHub 35 工具 ≈26K tokens；Slack 11 工具 ≈21K；Jira 单家 ≈17K；58 工具 ≈55K；**Anthropic 见过 134K tokens 的工具定义** | Anthropic「advanced tool use」（2025-11-24） |
| 准确率拐点 | 超过 **30–50 个**工具同时加载，选择准确率下降 | Claude Code 官方文档 |
| 渐进发现的收益 | 常驻 ~500 tokens + 按需 3–5 个工具 ≈3K → 总计 ~8.7K vs 传统 ~77K（**省 85%**，保住 95% 上下文） | 同上 |
| 准确率提升 | Opus 4：49% → 74%；Opus 4.5：79.5% → 88.1% | 同上 |
| 固定 K 在两个方向都错 | BFCL（370 工具）：自适应策略**平均只展示 7 个**即达到"恒定展示 50 个"的覆盖率（90.3% vs 90.8%）。ToolBench（3,251 工具）：固定 K=5 总分更高（64.7% vs 61.9%）但**难题（正确工具排 6–20 名）上一个都找不到**，自适应靠"搜得更深"捞回 16.7% | arXiv 2605.24660 |
| 短名单反而让模型选得更准 | 自适应 K=2.2 时下游选择准确率 **93.1% vs 87.1%**（恒定展示 5 个）；中等难度题 76.8% vs 60.9% | 同上 |
| prompt caching 不受影响 | 延迟加载的工具**完全不出现在初始 prompt**，故 system prompt 与核心工具定义仍可缓存 | Anthropic「advanced tool use」 |

---

## 五、本地参考项目实读对照

**方法说明**：以下均为**实读源码**结论，非 README 转述。

### 5.1 按形态归类

| 项目 | 形态 | 关键证据 |
|---|---|---|
| **LangBot**（Python，生产在跑） | **A + 官方 SDK + 异步常驻会话** | 依赖 `mcp>=1.25.0,<2.0.0`（`pyproject.toml:26`）；`src/langbot/pkg/provider/tools/loaders/mcp.py`（2711 行）为唯一客户端，`RuntimeMCPSession:243` 管全生命周期：`_lifecycle_loop:558` / `_lifecycle_loop_with_retry:669` / `_trigger_reconnect:992` / `_is_session_terminated:977` / SSE 回退 `_should_fallback_to_sse:956` / OAuth challenge `:935` / `MCP_TOOL_CALL_TIMEOUT_DEFAULT_SECONDS = 300.0`（`:66`）。**工具面靠绑定控制**：`_eligible_sessions_for_bound():2163` 按 query 里的 `_pipeline_bound_mcp_servers` 筛 server。stdio 另有独立特性闸门 `mcp_policy.py` 的 `stdio_mcp_enabled()`，**配置坏值时 fail closed** |
| **CowAgent**（Python agent harness） | **A + 自研 JSON-RPC + 检索裁剪** | `agent/tools/mcp/`：`mcp_tool.py:33` 每个工具包成 BaseTool 并支持 **`name_prefix`** 命名空间（"Prefix only the local name; the server still expects its original name"）；`mcp_client.py:944` `McpClientRegistry` 进程级单例（`shared_key(path, server_name, cfg)` 做键、boot lock、`start_all`/`shutdown_all`）；`service.py` 用标准 `mcpServers` JSON 配置 + **`probe_server()` 干跑**（握手 + `list_tools` 后立刻 shutdown，不落盘）；`tool_retrieval.py:1-21` 按向量相似度**按轮检索 MCP 工具**，三条不变量：内置工具全量注入、**只增不减**（若某工具已在历史里产生过 `tool_use`，撤掉会让模型侧抛消息格式错误）、失败**回退全量** |
| **deepseek-harness**（Python/TS，DeepSeek 官方） | **A（纯，无任何裁剪）** | `packages/mcp/mcp-client/`（1149 行 TS，`@modelcontextprotocol/sdk ^1.12.0`）；`tools.ts:127` `syncTools()` 全量 drain `tools/list` 分页 → 为**每个**工具建 `ToolDefinition` 注册进 `ctx.tools`。工程细节最严谨，见 5.2 |
| **WeKnora**（Go，`mark3labs/mcp-go`） | **G 型（两入口懒加载）** | `internal/agent/tools/mcp_tool.go:544-598` 只注册 `discover_mcp_tools` / `call_mcp_tool` 两个入口，**不暴露全量 schema**，按需 describe；`mcp_catalog.go:556` 目录预览限 2000 字符；`internal/mcp/manager.go` 常驻缓存 + 配置版本失效 + 每 5 分钟清断连（`:274`）+ 按 principal 分键；SSRF 校验 `security.go:15` |
| **Dify** | A（自研，**每次调用现开现关**） | `api/core/mcp/` + `api/core/tools/mcp_tool/`；自研协议实现，**无 stdio**，只有 Streamable HTTP + 旧 SSE；`mcp_tool/tool.py:325` 每次 `with MCPClientWithAuthRetry(...)`；只有 401 重试一次。可借鉴：**`list_tools` 结果落库**（`mcp_tools_manage_service.py:328`）把"拉工具列表"与"调用"解耦；404 → "Session terminated"（`client/streamable_client.py:304-317`） |
| **openakita** | **G 型（C 的正确形态）** | `src/openakita/tools/handlers/mcp.py:42` 三个网关元工具；`tools/mcp_catalog.py:161,388` 提示只注入**压缩清单**（服务器 + 工具名 + 一句话描述）；完整 `INSTRUCTIONS.md` 由 `get_mcp_instructions` 懒取（`:454`）、schema 由 `get_tool_schema` 懒取；远端 schema **不进** prompt；MCP 当 skill（`handler: mcp`）；按 agent profile 过滤 server（`agents/factory.py:398`） |
| **Qwen-Agent** | A（全量物化） | `qwen_agent/tools/mcp_manager.py:377` `connection_server` 取 `list_tools`；`:212` 为每个远端工具建 BaseTool，命名 `{server}-{tool}`；resources 也物化为 `list_resources`/`read_resource`（`:224`）；无任何筛选 |
| **financial_rag-main**（同领域） | ❌ **并非 MCP 协议** | `mcp_server/app/main.py:80,107,126` 是 FastAPI + 手写 JSON-RPC（路由 `/health` `/tools` `/mcp/call`），docstring 自称 FastMCP（`main.py:4`）但全仓无 `import mcp/fastmcp`。`rag_backend/app/mcp/langchain_adapter.py:61-114` 的 schema→Pydantic→`@tool` 转换思路可用，但该适配器与 client 已脱节（`example.py:76` 传的参数 client 没有），**真实 agent 未接线**。参见本目录 README「来源必须可核」条 |

### 5.2 deepseek-harness 值得照抄的四组契约（形态选 A 时必需）

1. **命名契约**（`tools.ts:96-113` `publicToolName()`）：公开名 `mcp__<serverName>__<rawName>`，规范化到函数名约束（**≤64 字符、仅 `[A-Za-z0-9_-]`**）；**有损规范化时追加 12 位 SHA-256 身份哈希**，保证不同 MCP 身份永不塌缩成同名。硬契约：**raw name 只在 wire 上发送（`tools/call`），public name 从不被反解回 raw name**。
2. **"代"（generation）+ 两阶段 swap + 整体回滚**（`tools.ts:120-160`）：Phase 1 先全量构建下一代定义，任何失败（网络错、server 列出重复工具名）→ **保持上一代原样不动**；Phase 2 才 dispose 旧代 + 注册新代，冲突则**整体回滚**——注释原文："the model sees either the full generation or none of it — never a partial set"。
3. **重连是有界指数退避，且退避 timer 调 `unref()`**（`connection.ts:219-224`）——防止等待中的重连 timer 把进程吊住不退出；放弃后**主动注销工具**并打可操作日志（`:210-213`）。
4. **主动绕过 SDK 的输出校验缓存**（`tools.ts:80-97` `listToolsUncached` / `callToolUncached`）：自己发原始 JSON-RPC 请求，避免 SDK 的 per-page output-validator 缓存用**另一套 contract** 预校验。另：模型可能返回任意 JSON（裸 string/number/null）→ 回退成 `{}`，让 server 报出模型能学习的"缺必填参数"；MCP `isError: true` → 抛异常交给错误路径。stdio 子进程用 `scrubbedParentEnv()` 剔除形如凭据的变量（`transport.ts:22-25`）。

---

## 六、传输选型

### 6.1 三种传输对照

| | stdio | HTTP+SSE（旧） | **Streamable HTTP（现行）** |
|---|---|---|---|
| 端点 | 进程内 stdio | **两个**：`GET /sse` 长连接 + `POST /messages` | **一个**：所有消息都是 `POST /mcp` |
| 响应形态 | 逐行 JSON-RPC | 只能走常驻 SSE 连接 | 单 JSON 对象 **或**该请求专属 SSE 流 |
| 鉴权 | 无（靠 OS 隔离） | 握手时校验一次，之后长连接持续放行 | 每个 POST 都过标准中间件 |
| 水平扩展 | 单客户端 | 需粘性会话，受限 | 可无状态、可水平扩展 |
| 可续传 | 否 | 否 | 是（SSE 事件带全局唯一 `id`） |
| 规范状态 | 现行 | **2025-03-26 起废弃** | 现行 |

**为何废弃 SSE**：常开连接使网关/反代/中间件**只在握手时检查一次身份**，之后门一直开着；且浏览器 SSE API 难以在握手时传自定义 header，实践中被迫把 token 塞进 URL query，导致凭据出现在**每个服务器日志与浏览器历史**里（Auth0 分析）。

### 6.2 最新规范的变化（2026-07-28 版，须注意）

官方原文：*"Revision 2026-07-28 changed the behavior of Streamable HTTP... Changes included: **Removal of the GET stream endpoint. Removal of protocol-level sessions.**"*

- **协议级会话被删除** → 旧版 `Mcp-Session-Id` + "404 → Session terminated"（Dify 在处理的）属 2025-03-26 形态；新版服务端应忽略该 header、不再签发或回显
- 长期变更通知改走 `subscriptions/listen` 请求的响应流
- 服务端到客户端的交互（sampling / elicitation / roots）内嵌为 **MRTR** input request，不再走独立流
- **取消语义**：Streamable HTTP 上**关闭 SSE 响应流本身就是取消信号**，不再发 `notifications/cancelled`（那条只在 stdio 用）
- 客户端 MUST 支持**两种响应形态**（`application/json` 与 `text/event-stream`），请求 MUST 带 `Accept: application/json, text/event-stream`
- 服务端 MUST 校验 `Origin` header（防 DNS rebinding，不合法回 403）；本地运行 SHOULD 只绑 `127.0.0.1`

### 6.3 本项目的传输结论

**只实现 stdio + Streamable HTTP 两条。**

- SLS 侧：`alibabacloud-sls-mcp-proxy` 默认 stdio（`uvx`），另有 `--transport http --port 8080` 产出 `http://127.0.0.1:8080/mcp`（Streamable HTTP）
- 百炼侧：只有远程 HTTP（Streamable HTTP，兼容旧 SSE）
- SSE 无任何采纳理由（已废弃）

---

## 七、阿里云百炼 MCP 广场 vs 一般 MCP

**协议层零区别**——它就是标准 MCP over Streamable HTTP，官方 SDK 示例即原生 `mcp` SDK：

```python
mcp_url = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
headers = {"Authorization": f"Bearer {api_key}"}
async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools_result = await session.list_tools()
```

**运行拓扑有七点区别**：

| # | 区别 | 影响 |
|---|---|---|
| 1 | **不是"一个 MCP"，是 N 个独立 endpoint**。URL 形如 `.../api/v1/mcps/<服务名>/mcp`，每个开通服务一个 URL。最硬证据：百炼自己的 CLI `bl mcp connect --server X --transport streamable-http --agent cursor` **把每个服务各写一条独立 server 配置**进 Agent 原生配置（支持 `cursor`/`claude-code`/`codex`/`qoder`/`all` 等），而非提供聚合端点 | 配置里是 N 条 server 记录；好处是天然对齐"绑定式筛选" |
| 2 | **无协议级命名空间** | 前缀须自己加。⚠️ **别照抄百炼 CLI 的 `<服务名>.<工具名>`**——点号只是 CLI 寻址约定；MCP 协议层没有 namespace 字段（那是 OpenAI `NamespaceTool` / Anthropic `mcp_toolset` 才有的）。且 DeepSeek 函数名只允许 `[A-Za-z0-9_-]`，**点号非法** |
| 3 | **只有远程 HTTP，无 stdio** | 省掉子进程管理（spawn / stderr drain / 凭据清洗 / 进程回收），但要处理 HTTP 侧问题。SLS 是 stdio，故管理层**两类都得支持** |
| 4 | **鉴权统一成一份 `DASHSCOPE_API_KEY`**（Bearer header），无需逐家申请密钥 | 凭证从 N 份降到 1 份；**但成单点**——一个 key 泄漏 = 所有已开通服务暴露；且本仓已在用百炼（embedding/rerank），该 key 已是高价值凭证 |
| 5 | **计费/限流由平台统一，且有账号级共享配额**。云部署 MCP"部署费用：限时免部署费用"；联网搜索 MCP **限流 15 QPS，主账号与 RAM 子账号共享**；超限返回 `11200051 MCP_HTTP_RATE_LIMIT`（HTTP 429），响应可能带 `Retry-After`；自定义部署另有按秒/按次计费模式 | 重试**不能无脑指数退避**，要尊重 `Retry-After`；需接熔断（对应 requirements_pool A-02） |
| 6 | **开通是控制台人工动作**。分 One Key（免开通、首调自动生效）与需手动开通两类；敏感服务信息走 KMS 凭据 | **运维前置**，代码解决不了；否则报错排查耗时（官方 FAQ 头号原因是"未开通或未升级"） |
| 7 | **百炼也能托管自建 MCP**（"自定义 MCP 服务"，三种部署方式，基于函数计算 FC 弹性调用） | 战略价值：将来的财税/法务自研 `mcp_server` 可部署到百炼，届时**只维护一条接入通道**（同一 key、同一传输） |

---

## 八、日志与金融两类目标 MCP 的现状

### 8.1 SLS 日志（用途：给智能机器人分析日志）

两套名字相近但不同的东西，**选前者**：

- **SLS MCP Server**（`alibabacloud-sls-mcp-proxy`，PyPI 0.3.3，Development Status **3 - Alpha**）：**11 个工具**，三类——资源发现（`sls_list_projects` / `sls_list_logstores` / `sls_list_metricstores`）、日志分析（`sls_get_logstore_index` / `sls_query_logs` / `sls_get_log_histogram` / `sls_get_context_logs`）、告警诊断（`sls_list_alerts` / `sls_get_alert` / `sls_list_alert_history` / `sls_diagnose_alert`，全为只读）。**认证两条路**：① 阿里云 CLI 的 OAuth profile（需 CLI ≥3.3.0 + RAM 管理员装 `official-cli` 应用，需浏览器）；② **省略 `--profile`，改用环境凭证** `ALIBABA_CLOUD_ACCESS_KEY_ID` / `_SECRET`（临时凭证加 `_SECURITY_TOKEN`）。传输：stdio 默认，可 `--transport http --port 8080`
  - **重要修正**：官方帮助页面只讲 OAuth，容易误判为"服务器上不可用"；PyPI README 明确支持环境凭证 → **无人值守场景可用**
- **可观测 MCP Server**（`alibabacloud-observability-mcp-server`，已用 Go 重构，Python 版在 `v1/`）：覆盖 SLS + CMS + ARMS，工具多得多（`umodel_*` 15 个 + Shared 3 个 + `sls_*`/`cms_*` 13 个）；1.x **已移除全部 `arms_*`**；4 个 AI 工具走 STAROps 付费。AccessKey 认证，支持 stdio / SSE / streamable-http

### 8.2 金融类：靠谱度的分水岭是"数据源"

| 档 | 代表 | 数据源 | 结论 |
|---|---|---|---|
| 爬网页 | `zwldarren/akshare-one-mcp`（9 工具，PyPI 有包，`uvx` 直跑）；`CharmYue/ashare-mcp`（30 工具，需 clone + `uv sync`，MIT）；`50955045/china--mcp` | **akshare 爬东财/新浪** | ⚠️ 免 token、上手快，但 akshare 自身被社区反复记录"越来越不稳定"；**适合个人验证，不适合生产** |
| 半正规 | `tushare-mcp-server`（PyPI）；`FinanceMCP`；`数金通` | Tushare Pro API（需 token、积分门槛） | 中；比 akshare 稳，仍属社区封装、质量参差 |
| **商业** | **百炼 MCP 广场 / 云市场订阅**：恒生聚源「金融综合问数」`cmapi00075042`、`cmapi00073529`（股票/基金/债券/宏观/工商数据） | 商业数据供应商 | ✅ 平台计费 + 统一鉴权 + 有 SLA 预期；且本仓已有 `DASHSCOPE_API_KEY`，接入成本最低 |

**注意**：`ashare-mcp` 的 README 主动标注了数据可靠性边界（北向资金自 2024-08-19 起官方取消盘中/日频明细披露、主力/大单资金流向是"按单笔成交金额机械分桶"的软指标、筹码分布是概率模型估算）——**这种诚实标注本身就说明该领域数据硬度差异大，用前必须逐工具确认口径**。

---

## 九、推荐形态（本仓）

基于以上，**修正了团队内部早期"A 打底 + A'' 演进 + C 兜底"的初判**：

1. **默认 = A + 阈值开关**（对齐 Claude Code 的 `auto` 语义）：工具定义占上下文低于阈值就全量注入，超阈值才启用渐进发现。
2. **超阈值后的检索优先用 provider 原生（D）；provider 不支持时退到 A''（`langgraph-bigtool` / 复用本仓 embedding + pgvector + rerank）或 E（`LLMToolSelectorMiddleware`）**——这三条都"schema 在框架手里"，参数校验与观测粒度不丢。
3. **C 只作为 provider 与 A''/E 都不可用时的兜底，且应采用 G 的形态**（压缩清单 + 懒取 schema），而非"裸元工具让模型盲拼参数"。
4. **`readonly` 双轴对接 MCP 工具注解**：以 `readOnlyHint` 为来源，**缺省按危险处理**（规范默认 `readOnlyHint: false` / `destructiveHint: true`）。须明确本地声明与远端注解冲突时的口径（覆盖 or 取交集）——见待确认项。
5. **工具命名**：采用 `mcp__<server>__<tool>` 形式并实现 deepseek-harness 的有损哈希兜底；**不要用点号**（DeepSeek 函数名约束）。
6. **单独留一手治"工具结果膨胀"**（`ContextEditingMiddleware` / `ClearToolUsesEdit`）——这是与工具定义膨胀不同的第二个黑洞。

---

## 十、反面意见与风险

1. **"生产环境别用 MCP"有数据支撑**：Nango 2025 秋季调查（数百个做 agent 集成的团队）——"**不到 10 家把 MCP 留在生产**"，理由是可靠性、token 成本、按用户鉴权三项自定义 tool calls 都更优；MCP 更适合原型/内部 agent/开发工具。同文引用 2025-07 一项研究：**116 个官方 MCP server 里 88.6% 只是 REST API 的包装**。
   - **对本仓的意义**：SLS 日志与金融行情若只为自用，包成自定义工具比接 MCP 更省心。接 MCP 的价值在"以后能挂别人维护的 server"——这点须在设计里讲清，否则是白付复杂度。
2. **渐进发现自带新失败模式：检索漏召**。对策（CowAgent）是"只增不减 + 失败回退全量"；Claude Code 的应对是已加载工具**留到 context 压缩为止**（有明确失效边界，不是无限累积）。
3. **provider 原生 tool_search 有硬依赖**（非一方 endpoint 会被代理吃掉 `tool_reference`），本仓自建 provider 兼容层下很可能不可用（见 §三）。
4. **工具注解是 hint 不是保证**：MCP 官方博客明说注解是风险词汇，默认值即"按危险处理"。
5. **本仓 `check_docs` 闸门会扫 `CLAUDE.md`**，登记本目录的表述须避开禁用词表（MySQL / Chroma / BM25 / financial-qa / create_react_agent / sse_utils / qwen-max / text-embedding-v3 / gte-rerank）。`docs/research/*.md` 本身不在扫描范围内。

---

## 十一、待确认项

| # | 待确认 | 为什么重要 | 怎么验 |
|---|---|---|---|
| 1 | `ProviderToolSearchMiddleware` 在本仓 provider 兼容层（DeepSeek / 百炼）上是否可用 | 决定 D 路线是"白拿"还是"不可用" | 实测一次 `defer_loading` 请求，观察请求/响应是否被 provider 接受 |
| 2 | 百炼广场实际开通了哪些服务、各自的工具定义 token 数 | 决定第一批配几个 server、阈值定多少 | `bl mcp list`，再对每个服务 `tools/list` 量一次 |
| 3 | 百炼服务端跟进到哪个 MCP 规范版本（是否已删协议级会话） | 决定要不要写 `Mcp-Session-Id` 相关逻辑 | 连上去看响应头是否回 `Mcp-Session-Id` |
| 4 | 本地声明的 `readonly` 与远端 `readOnlyHint` 冲突时的口径 | 影响 `declare_readonly` 的正确性与安全 | 设计阶段定；倾向"两者都只读才算只读"（取交集，安全侧） |
| 5 | 金融侧最终选百炼广场（商业，需订阅）还是 akshare 系（免费，仅验证） | 决定是否需要走控制台订阅流程 | 查账号在百炼 MCP 广场的订阅状态 |

---

## 十二、来源

### 一手官方文档

- [Anthropic — Introducing advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)（2025-11-24）：Tool Search Tool / Programmatic Tool Calling / Tool Use Examples；134K tokens、85% 削减、准确率提升数字
- [Anthropic — Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)：渐进发现概念源头；`search_tools` 带 detail level
- [Claude Code — Scale to many tools with tool search](https://code.claude.com/docs/en/agent-sdk/tool-search)：30–50 工具拐点、`auto:N` 阈值、<10 工具全量更快、provider 兼容性限制
- [MCP 官方 — Client Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)：阈值 1%–5%；四种检索策略；Catalog/Inspect/Execute 三层
- [MCP 规范 — Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)：2026-07-28 修订（删 GET 流端点、删协议级会话）、Origin 校验、两种响应形态
- [MCP 官方博客 — Tool Annotations as Risk Vocabulary](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/)：`readOnlyHint` / `destructiveHint` 及默认值
- [OpenAI — Tool search guide](https://developers.openai.com/api/docs/guides/tools-tool-search)：`defer_loading`、`NamespaceTool`、server/client 两种执行
- [OpenAI Agents SDK — Tools](https://openai.github.io/openai-agents-python/tools/)：`ToolSearchTool()`
- 阿里云百炼：[官方 MCP 服务](https://help.aliyun.com/zh/model-studio/official-and-third-party-mcp) / [外部调用](https://help.aliyun.com/zh/model-studio/mcp-external-calls) / [MCP 简介与计费](https://help.aliyun.com/zh/model-studio/mcp-introduction) / [MCP 常见问题](https://help.aliyun.com/zh/model-studio/mcp-faq) / [CLI MCP 编排](https://help.aliyun.com/zh/model-studio/cli/mcp-skill) / [自定义 MCP 服务](https://help.aliyun.com/zh/model-studio/custom-mcp)
- 阿里云 SLS：[SLS MCP Server](https://help.aliyun.com/zh/sls/best-practices-7)；[alibabacloud-sls-mcp-proxy (PyPI)](https://pypi.org/project/alibabacloud-sls-mcp-proxy/)

### 学术

- [arXiv 2605.24660 — How Many Tools Should an LLM Agent See? A Chance-Corrected Answer](https://arxiv.org/html/2605.24660v1)（Meta，2026-05-23）：自适应深度 vs 固定 K；200 tokens/工具；93.1% vs 87.1%
- [arXiv 2604.21816 — Dynamic Tool Gating and Lazy Schema Loading](https://arxiv.org/html/2604.21816v1)

### 工程实践与反面意见

- [nango.dev — MCP vs tool calls for AI agents](https://nango.dev/blog/mcp-vs-tool-calls-for-ai-agents/)：**反面意见主要来源**；"<10 家留在生产"；88.6% 为 REST 包装
- [gingerlabs — MCP Transport Comparison: stdio vs SSE vs Streamable HTTP](https://gingerlabs.ai/blog/mcp-transport-comparison)
- [Auth0 — Why MCP's Move Away from SSE Simplifies Security](https://auth0.com/blog/mcp-streamable-http/)：SSE 常开连接的安全风险
- [langgraph-bigtool](https://github.com/langchain-ai/langgraph-bigtool)：A'' 的官方实现
- [LangChain — Tools 文档（Dynamic tool selection）](https://docs.langchain.com/oss/python/langchain/tools)：`wrap_model_call` + `request.override(tools=...)`
- [dev.to — MCP Tool Overload](https://dev.to/thedailyagent/mcp-tool-overload-why-more-tools-make-your-agent-worse-5a49)：动态加载 / 命名空间 / 子代理四条策略
- [synapticlabs — The Meta-Tool Pattern](https://blog.synapticlabs.ai/bounded-context-packs-meta-tool-pattern)：C 的工程化描述
- [tessl.io — Anthropic brings MCP tool search to Claude Code](https://tessl.io/blog/anthropic-brings-mcp-tool-search-to-claude-code)

### 本地实读（`../github/` 与 venv）

- `LangBot/pyproject.toml:26`、`src/langbot/pkg/provider/tools/loaders/{mcp.py,mcp_policy.py,mcp_stdio.py}`、`.mcp.json`
- `CowAgent/agent/tools/mcp/{mcp_client.py,mcp_tool.py,service.py,tool_retrieval.py}`
- `deepseek-harness/packages/mcp/mcp-client/{package.json,src/{index,connection,tools,transport}.ts,tests/}`
- `WeKnora/internal/mcp/{client,manager,security}.go`、`internal/agent/tools/{mcp_tool,mcp_catalog,mcp_exposure,definitions}.go`、`internal/types/mcp.go`
- `dify-1.16.1/api/core/mcp/*`、`api/core/tools/mcp_tool/{tool,provider}.py`、`api/services/tools/mcp_tools_manage_service.py`
- `openakita/src/openakita/tools/{handlers/mcp.py,mcp_catalog.py}`、`agents/factory.py`
- `Qwen-Agent/qwen_agent/tools/mcp_manager.py`
- `financial_rag-main/mcp_server/app/main.py`、`rag_backend/app/mcp/*`（结论：非 MCP 协议）
- `awesome-llm-apps-main/mcp_ai_agents/multi_mcp_agent_router/agent_forge.py`
- `agency-agents/integrations/mcp-memory/`（结论：用 MCP 做长期记忆，与工具面管理无关）
- `/opt/venvs/corporate_rag/lib/python3.12/site-packages/langchain/agents/middleware/{tool_selection.py,provider_tool_search.py,context_editing.py}`
- `langgraph-1.2.10/libs/prebuilt/langgraph/prebuilt/tool_node.py`（结论：LangGraph 自身无工具检索能力，只有按名分发 `:1032` 与 `wrap_tool_call` 动态注入 `:1339-1356`）

### 本仓代码

- `src/agents/tools/registry.py`、`src/agents/tools/rag_tools.py`、`src/agents/tools/readonly.py`
- `src/agents/graph/agent_factory.py`、`src/infra/llm/tool_trace.py`
- `src/services/agent_service.py`（`fork_tool_pool` 与 preset/skill 绑定）
- `src/cli/check_docs.py`（闸门范围与禁用词表）
