## Why

Langfuse 接线（change `langfuse-trace-wiring`，2026-09-24 归档）已让每一轮对话落一条 trace、每轮 LLM 推理落一条 `agent_turn` generation。但实测（dev 库 `trace_19e8e472-…`）显示：trace 的信息量仍然只到「每轮 LLM 的输入与输出」。

- **工具调用完全不可见**：没有任何工具类观测；工具名与入参不在任何载荷里（`_messages_payload` 只取 `{role, content}`）；工具轮的 `output` 是 **NULL**。RAG 排障最需要的「哪一轮、调了哪个工具、传了什么 query、返回了什么、各花了多久」一样拿不到。
- **trace 无法按人与业务维度切分**：`user_id`、`tags` 为空，`metadata` 基本 `{}`。
- **成本为空**：`models` 表的内置定价（87 行，含 OpenAI / Anthropic / Google 等）**不含 qwen 系列**，故 `qwen3.8-flash` 匹配不到模型定义，`calculated_*_cost` / `totalCost` 全空（dev 库 8 条 generation 全为 NULL）。

关键在于**这些缺口都有现成且免费的数据源**：LangGraph 的 `graph.astream_events` 已经在为每个工具发 `on_tool_start` / `on_tool_end` / `on_tool_error`（SSE 的「检索中…」状态就在消费它），agent/skill 事实也早已躺在 `RequestContext` 上。所以补齐是「多挂一个消费者 + 多写几个字段」，不是重新埋点。

**为什么现在做**：工具观测是 RAG 排障的刚需；且**越晚越难补** —— MCP 接入后工具数量会膨胀，若那时才为每个工具单独埋点，成本与遗漏都会放大（现有 requirements_pool 的 G-01「MCP 工具卡片」也依赖同一批数据）。

## What Changes

- **工具调用成为一等观测**：每条工具调用产出一条 Langfuse span，记录工具名、入参、返回值、起止时间、错误态；同一轮的多次调用归到该轮的 `tools` 父 span 之下（形成真正的树）。
- **实现方式为「事件流驱动」，不侵入图与工具**：在 `_run_generation` 既有的 `graph.astream_events(...)` 循环里新增一个请求内私有的消费者 `ToolTraceCollector`，按 `run_id` 配对 `on_tool_start` / `on_tool_end`；父 span 的开关以节点级 `on_chain_start/end`（`name == "tools"`）为锚点；事件筛选只用 `metadata.langgraph_node == "tools"`。
- **generation 载荷补全**：工具轮的 `output` 不再为空（模型只发 `tool_calls` 时写入该结构，文本非空时仍写文本）；`input` 的消息 `role` 由 LangChain 类型名规范化成 OpenAI 形态（`ai`→`assistant`、`human`→`user`），并补上 assistant 的 `tool_calls` 与 tool 消息的 `name`。
- **trace 富化**：写入 `user_id`（在请求内捕获后显式传入、空值转 `None`，不依赖 contextvar 继承）、低基数 `tags`（`chat` + `kb`|`no_kb`）、业务 `metadata`（生效智能体、本轮加载的技能、kb_id、kb_domain、deep_thinking、direct_skill 等）。全部**只读 `RequestContext`，不新增取数**。
- **成本可见**：实际使用模型的输入/输出单价进 `src/config/settings.py`（env 驱动，**默认 0**；**单位是 USD / 单个 token**，$3 per 1M → 填 `0.000003`）；**0 视为「未配置」，CLI 跳过 seed**。模型名取 `settings.LLM_MODEL`（不硬编码），`match_pattern` 用 `"(?i)^" + re.escape(model_name) + "$"` 构造（锚定正则，防误配到形近模型），`unit=TOKENS`；**seed 前先用只读查询比对运行时 provider 名与配置名**，不等则拒绝 seed。落地为 repo 内幂等 CLI（`langfuse_context.client_instance.api.models` 的 `list` + `create`，dev / prod 各跑一次）。
- **新增第二种 Langfuse 接入方式**：装饰器无法表达「start / end 分离」的 span，工具观测改用命令式 `langfuse_context.client_instance.span(trace_id=…)` / `.end()` 实现（**不调 `client.trace()`**、**不 new `Langfuse()`**）—— 需一条 ADR 记录与既有「纯装饰器」决策的取舍。
- **MCP 兼容**：采集器对工具实现**无感**（不按工具名分支、`data.output` 原样透传），MCP 工具按 `tool-registry` 规格从统一入口进 ToolNode 即自动覆盖；扩展点由 `_on_start` / `_on_end` 两个方法天然提供，**不预先创建空函数或占位参数**。
- **不做什么（Non-Goals）**：scores 回写（`feedback → score`、RAGAS → score）另案；CLI（`eval_ragas`）本期不接（它走 `graph.ainvoke`，无事件流）；**fork 子代理的工具调用**本期不覆盖 —— 其**真实原因是回调传播被显式切断**（`src/agents/skills/executor.py:189` 的 `var_child_runnable_config.set(None)`，为防 SSE token 污染），子代理事件根本不进主图事件流（**不是**被 `scope` 过滤）；工具**内部**子步骤（embed / 向量检索 / rerank）不是 LangChain Runnable，事件流里没有它们的事件。

## Capabilities

### New Capabilities

（无 —— 本变更是对既有 `llm-tracing` 能力的扩展，不新开重叠能力。）

### Modified Capabilities

- `llm-tracing`: 新增「工具调用记为 span」「trace 的用户·标签·业务元数据」「模型成本可见」三类要求；修改「主 agent 每轮推理记为 generation」（输出须含 `tool_calls`、输入消息须为 OpenAI 形态的 role）与「trace 内容记录范围」（明确新增 span 与载荷字段的边界）。

## Impact

- **代码**
  - `src/services/agent_service.py` —— 事件循环内挂 `ToolTraceCollector`；`update_current_trace` 扩参（`user_id` / `tags` / `metadata`）
  - `src/api/chat.py` —— 捕获并显式传递 `user_id`（空值转 `None`）；订正第 42-43 行「任务与请求不共享 context」的误注释
  - `src/agents/graph/agent_node.py` —— 工具轮 generation 的 `output` 补 `tool_calls`
  - `src/agents/graph/message_payload.py` —— `role` 规范化 + 补 `tool_calls` / tool `name`
  - `src/infra/llm/tool_trace.py`（新增）—— `ToolTraceCollector`（跨事件记账 + 兜底关闭）
  - `src/config/settings.py` —— 模型单价字段（env 驱动，默认 0；注释注明「USD / 单 token」）
  - `src/cli/seed_langfuse_models.py`（新增）—— 幂等 seed 模型定价（模型名取配置；seed 前比对运行时模型名）
- **依赖**：Langfuse v2.95.11 + `langfuse==2.60.10`（命令式 span API 已实测可用）；**不新增第三方依赖**
- **文档**：新增 ADR（命令式接入 + client 实例口径）、`docs/agents/code-map.md`（新模块落点）、`docs/agents/cookbook.md`（seed 定价的操作步骤）
- **测试**：既有 4 个 tracing 测试（`tests/services/test_run_generation_tracing.py`、`tests/agents/graph/test_agent_node_tracing.py` 等）与 `_messages_payload` 载荷断言需同步；新增工具 span 的 `run_id` 配对、并行多工具、错误分支、取消兜底 `close()`、禁用态零产出，以及 `user_id` 非空 / 不写空串的集成断言
- **部署**：dev 侧 seed 一次模型定价（USD / 单 token 口径）；prod 侧随既有遗留（`requirements_pool` H-03 prod 侧 Langfuse 落地）一并处理
