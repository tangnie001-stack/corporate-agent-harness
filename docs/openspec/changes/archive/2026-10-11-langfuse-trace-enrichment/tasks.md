## 1. 模型单价配置与定价写入

- [x] 1.1 `src/config/settings.py` 新增模型输入 / 输出单价字段（env 驱动，默认 0 表示「未配置」），并同步 `.env.template` / `.env.example` 的注释 —— **注释必须写明「USD / 单个 token」并给出换算例**（$3 per 1M tokens → 填 `0.000003`；写成「每 1M」会让成本差 1e6 倍）
- [x] 1.2 新增 `src/cli/seed_langfuse_models.py`：用 `langfuse_context.client_instance.api.models` 的 `list`（**翻页遍历**）+ `create` 幂等写入；**`model_name` 取 `settings.LLM_MODEL`（不硬编码）**，`match_pattern` 由它构造为 `"(?i)^" + re.escape(model_name) + "$"`，`unit=TOKENS`；**单价为 0 时跳过并打印提示**，不写入零价模型定义
- [x] 1.3 **seed 前的一致性检查**：先只读查询 `SELECT DISTINCT model FROM observations WHERE type='GENERATION'`，与 `settings.LLM_MODEL` 比对 —— **不等则先改配置、不执行 seed**（pattern 锚定的必须是**运行时 provider 名**，它来自 `response_metadata["model_name"]`，与配置值当前恰好相等但无全链保证）。不要为兼容后缀而放宽 pattern（会重新引入误配）
- [x] 1.4 CLI 单测：未配置时跳过；重复执行不产生重复模型定义；**断言 pattern 匹配目标模型**，并断言**不匹配**形近名（带版本后缀 / 形近写法）—— 注意 Postgres 的 `~` 是**子串匹配**，写宽了的后果是**误配**（把单价套到别的模型上），不是匹配不到；若断言"未锚定就匹配不到"会直接失败

## 2. 工具观测（核心）

- [x] 2.1 新增 `src/infra/llm/tool_trace.py`：`ToolTraceCollector`（`_enabled` 守卫、`{run_id: span}` 账本、当前 `tools` 父 span、`consume(item)` 三分支、`close()` 兜底）
- [x] 2.2 父 span 的开关接节点级 `on_chain_start/end`（`name == "tools"`）；工具 span 以 `parent_observation_id` 挂在其下
- [x] 2.3 过滤判据**只用 `metadata.langgraph_node == "tools"`**；**不得**用 `checkpoint_ns` 判空 —— 实测 `on_tool_*` 的 `checkpoint_ns` 是 `tools:<uuid>`（非空），误用会**丢掉全部工具事件**
- [x] 2.4 工具 span 一律用 `langfuse_context.client_instance.span(trace_id=…)` 创建（**不调用 `client.trace()`**；**不 new `Langfuse()`**，否则绕过开关与 flush）；`_on_end` 从 `ToolMessage` 显式取 `.content` / `.tool_call_id` / `.name` 后再写入
- [x] 2.5 `src/services/agent_service.py` 的事件循环内挂 `tool_trace.consume(item)`，并在 `finally` 调用 `close()`
- [x] 2.6 采集器单测：`run_id` 配对；`on_tool_error` 错误态；取消路径 `close()` 关闭未结束 span；`LANGFUSE_ENABLE=false` 时零产出；**同一轮并行多个工具时各自成 span 且都挂在同一条父 span 下**

## 3. generation 载荷与输出

- [x] 3.1 `src/agents/graph/message_payload.py`：`role` 由 LangChain 类型名规范化为 OpenAI 形态（`ai`→`assistant`、`human`→`user`，`system`/`tool` 原样），assistant 条目补 `tool_calls`、tool 条目补 `name`
- [x] 3.2 `src/agents/graph/agent_node.py`：generation 的 `output` 改为文本优先 —— 文本非空写文本，文本为空且存在 `tool_calls` 时写 `{"tool_calls": […]}`；仍保持 `capture_input=False` + 显式写入
- [x] 3.3 同步既有断言：`tests/agents/graph/test_agent_node_tracing.py:11,24`（断言 `"human"` 与键集 `{role, content}`）、`tests/services/test_run_generation_tracing.py`、以及 `message_payload.py` 的返回类型注解

## 4. trace 富化

- [x] 4.1 `_run_generation` 的 `update_current_trace` 扩参：`tags`（`chat` + `kb`|`no_kb`）、`metadata`（agent / agent_display_name / skill_action / loaded_skills / kb_id / kb_domain / deep_thinking / direct_skill / has_skills，**只读 `RequestContext`**）；`user_id` **在请求内捕获后显式传入**（与 `trace_id` 同做法，不依赖 contextvar 继承），**落点写 `user_id or None`**（`update_current_trace` 只过滤 `None`、**不过滤空串**），并**订正 `src/api/chat.py:42-43` 那条「任务与请求不共享 context」的误注释**
- [x] 4.2 单测：字段取值正确；**高基数取值（kb_id / agent / skill）进 metadata 而非 tags**；**集成断言：后台任务内 `user_id` 非空**；**且空值时不得把空串写进 trace**（防「静默变空 / 写成空串」回归）

## 5. 文档与 ADR

- [x] 5.1 新增 ADR：引入命令式 Langfuse client 接入（含「client 实例统一取 `langfuse_context.client_instance`」与「工具 span 路径不触碰 trace 行」的口径，与 `llm-tracing` 既有「纯装饰器」决策的取舍），并在 `docs/adr/README.md` 索引表登记
- [x] 5.2 `docs/agents/code-map.md` 登记 `src/infra/llm/tool_trace.py` 与 seed CLI 的落点；`docs/agents/cookbook.md` 补「seed 模型定价」的操作步骤（含 **USD / 单 token** 口径与换算例、`match_pattern` 由 `re.escape` 构造的说明、以及**先比对运行时模型名**这一步）
- [x] 5.3 trace 记录范围的说明**落在 `docs/agents/` 的常驻档或本次新增的 ADR 里**；**不得原地修改已接受的 ADR（如 ADR-0012）正文**，若确需改其口径，按既有约定走「追加新 ADR + 旧 ADR 加 Status 反向指针 + 索引表登记」。内容须含：新增工具 span 与载荷字段，以及「fork 子代理工具（回调被 `executor.py:189` 切断）与工具内部子步骤不在主事件流内」这一已知边界

## 6. 验证

- [x] 6.1 质量门禁全绿：`pytest`（宿主侧带 `POSTGRES_HOST=localhost`）/ `ruff check .` / `pyright src/` / `check_docs` / `check_adr`
- [x] 6.2 关闭态回归：`LANGFUSE_ENABLE=false` 跑一轮对话 → Langfuse 零新增、SSE 事件序列与开启态一致
- [x] 6.3 dev E2E：一轮含 ≥3 次迭代、≥2 次工具调用 → **工具 span 数量 == 实际工具调用次数**（防过滤判据写错导致零 span）、**所有工具 span 与 generation 的 `trace_id` 均等于本轮 trace id**、每轮一条 `tools` 父 span + 逐工具子 span（含工具名 / 入参 / 返回 / 耗时）、工具轮 `output` 非空、`user_id` / `tags` / `metadata` 落库
- [x] 6.4 取消路径 E2E：生成中取消 → 已开启的工具 span 均被关闭，无悬空节点
- [x] 6.5 **必过闸门**（不得降级为可选）：**先确认 Langfuse 真会算成本**（容器内没有 langfuse-worker 进程，摄入路径含 `event.model ~ match_pattern`，判断为 in-process 但未实跑）—— 填单价（USD / 单 token）并 seed 后，≥1 条 generation 的 `calculated_total_cost > 0`，且与「已知用量 × 已知单价」抽算对账一致（防 1e6 量纲错误照样过闸）；未配置时确认成本为空且无零价模型定义
- [x] 6.6 `openspec validate --changes langfuse-trace-enrichment` 通过

### 实测记录（2026-09-24，worktree `corporate_rag-langfuse-enrich`）

**执行环境**：全部实测跑在 worktree 的**本地服务**上（宿主 `uvicorn`），未使用主工作区的 docker compose 栈。宿主进程必须覆盖 `LANGFUSE_HOST=http://127.0.0.1:3000` —— `.env` 里的 `langfuse-web` 是 compose 内网名，宿主不可解析（沿用会让上报静默失败）。`.env` 未被写入。

**6.1 质量门禁** — 全绿

| 命令 | 结果 |
|---|---|
| `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q` | `1253 passed, 31 warnings` RC=0（160.37s） |
| `.venv/bin/ruff check .` | `All checks passed!` RC=0 |
| `.venv/bin/ruff format --check src/ tests/` | `352 files already formatted` RC=0 |
| `.venv/bin/pyright src/` | `0 errors, 0 warnings, 0 informations` RC=0 |
| `.venv/bin/python -m src.cli.check_adr` | `14 条 ADR，0 error` RC=0 |
| `.venv/bin/python -m src.cli.check_docs` | `13 篇文档，0 error, 31 warn` RC=0 |

（31 warn 是存量噪声档：文档里反引号包裹的内置名 / 环境变量触发的符号告警；error 档才是硬要求。）

**6.2 关闭态回归** — 通过
- `LANGFUSE_ENABLE=false` 起独立后端（端口 8002）跑一轮真实对话（`kb_id=4a1dcb8b…`）：SSE 序列 `agent_used → status → token×6 → status×20 → token×265 → citation×3 → model_info → done`，curl RC=0。
- Langfuse trace 计数：对话前 **8** → 对话后 **8**（零新增）。
- 与开启态（6.3）序列同形（同为首事件 `agent_used`、末事件 `done`、含 `model_info` 与 `citation`）。

**6.3 dev E2E** — 通过（`trace_458da702-9186-45c1-a54f-388006be68e2`）
- 规模：**5 轮迭代、6 次工具调用**（均为 `retrieve_kb`），分布在 **4 个 `tools` 父 span** 下。
- 嵌套：6/6 工具 span 的 `parent_observation_id` 均等于同轮 `tools` 父 span 的 id；第 3、4 轮各有两个**并行工具共用同一父 span**（`811e2242` / `a8e201f7`）。
- trace 归属：该 trace **15 条 observation 全部** `trace_id == X-Trace-ID`。
- 工具 span 字段：带 `name`、入参（`{"query": …}`）与返回值（output 落成 `{"name":"retrieve_kb","content":"[1] 来源: …"}`），起止时间齐全。
- trace 富化：`user_id = 06648346-0a34-46a0-823b-31b5e231cf6a`（非空）；`tags = {chat, kb}`；`metadata = {"agent":"", "kb_id":"ea84fb7235a941f9b64bcf4f5fa4b7f2", "kb_domain":"general", "skill_action":"none", "deep_thinking":false, "loaded_skills":[], "agent_display_name":""}`。
- **两处判据按实测订正**：
  1. 6.3 原文「工具 span 数量 == 实际工具调用次数」在**父 span** 这一层不成立：每轮只有一条 `tools` 父 span（4 条），工具调用有 6 次。该条的真实意图是「**不是 0**」；与工具调用次数相等的是**逐工具子 span 数**（6）。
  2. 6.3 原文「工具轮 `output` 非空（含 `tool_calls`）」：本轮 5 个 generation 都写了模型的**前导文本**（文本优先规则生效），`{"tool_calls": …}` 分支在 E2E 中未出现（该模型每轮都带文本）。该分支由单测 `tests/agents/graph/test_agent_node_tracing.py::test_empty_text_output_does_not_fall_back_to_state_dict` 覆盖。
- 附带确认：流尾的 `ask_user` 是**图谱层联网确认卡**（"缺失 [2023,2024,2025]，是否联网搜索补充？"，经 clarify 通道投递），**不是** ToolNode 执行的工具，故无 span —— 属正确行为，非观测缺口。

**6.4 取消路径 E2E** — 通过（`trace_3ccf40e1-6d45-4df0-8b9c-41cb5335bea0`）
- `POST /api/sessions/cancel` → `{"cancelled": true, "session_id": "e2e-cancel-1"}`；SSE 终态 `done {"cancelled": true}`。
- 该 trace **17 条 observation 的 `end_time IS NULL` 计数 = 0**（无悬空节点）。
- 操作注记：cancel 端点先校验 DB 会话归属；对**已存在**的会话取消即成功。首轮生成期间若会话行尚未落库会 404 —— 本次实测中的那次 404 已定位为**请求 harness** 的 cookie jar 写入时机问题（`curl -c` 在进程退出时才落盘，故取消请求未带 cookie），非产品缺陷。

**6.5 成本闸门** — 通过
- 前置一致性：`SELECT DISTINCT model FROM observations WHERE type='GENERATION'` → 仅 `qwen3.8-flash`，与 `settings.LLM_MODEL` 相等，故未放宽 `match_pattern`。
- `seed --dry-run` → `{'model_name': 'qwen3.8-flash', 'match_pattern': '(?i)^qwen3\\.8\\-flash$', 'unit': 'TOKENS', 'input_price': 8e-07, 'output_price': 2.7e-06}`。
- seed 实跑 → `[created] qwen3.8-flash … unit=TOKENS in=8e-07 out=2.7e-06 (USD/token)`，RC=0；复跑 → `[exists] 模型定义已存在，跳过`，RC=0（幂等）；`models` 表恰一行。
- **单价口径（需写进 `.env` 注释）**：人民币原值直填 —— 输入 0.8 元/百万 → `0.0000008`，输出 2.7 元/百万 → `0.0000027`（取**缓存未命中**价）。Langfuse 的 UI 币种标签固定为 USD，此数值实为人民币元。
- seed 后新对话（`trace_472d913c-f443-4c9f-9c6b-d5147ce74c07`）3 条 generation **全部 `calculated_total_cost > 0`**，且与「用量 × 单价」手算逐条吻合（无 1e6 量纲错误）：

| prompt_tokens | completion_tokens | 手算 | `calculated_total_cost` |
|---|---|---|---|
| 3627 | 47 | 3627×8e-7 + 47×2.7e-6 = 0.0030285 | 0.0030285 ✓ |
| 4925 | 57 | 4925×8e-7 + 57×2.7e-6 = 0.0040939 | 0.0040939 ✓ |
| 6209 | 759 | 6209×8e-7 + 759×2.7e-6 = 0.0070165 | 0.0070165 ✓ |

- 未配置态确认（seed 之前）：8 条 trace 中 `calculated_total_cost > 0` 的 generation 为 **0**，且 `models` 表无 qwen 行（未留下零价模型定义）。

**6.6 openspec 校验** — `openspec validate --changes langfuse-trace-enrichment` → **6 passed, 0 failed**
