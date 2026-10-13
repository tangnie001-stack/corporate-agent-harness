# 接口契约

> 记录模块间接口的参数语义、返回值格式和历史踩坑记录。
> 修改任一模块的公共方法签名时，同步更新此文件。

---

## 1. 核心标识符

| 标识符 | 格式 | 说明 | 常见错误 |
|--------|------|------|----------|
| `kb_id` | UUID 字符串（如 `53890512-f252-45bf-9485-25b4253cb4f1`）或空字符串 `""` | 知识库唯一标识。`""` 表示"不检索"（未绑定 KB，纯对话） | ❌ 传了 `kb_name`（"2024年报"） |
| `doc_id` | UUID 字符串 | 文档唯一标识 | ❌ 传了数据库自增 ID（doc_id 是 UUID） |
| `session_id` | 任意字符串 | 会话标识，用于关联对话历史 | ❌ 传了空字符串 |
| `chunk_id` | `"{doc_id}:{chunk_index}"` 格式 | `chunks` 表主键 | - |

---

## 2. 接口层：FastAPI Routes ↔ AppService

路由统一挂载在 `/api` 前缀下。
除以下 GET 端点外，其余端点使用 POST 方法：`/api/health`（健康检查）、
`/api/sessions/events`（SSE 断点续接）、`/api/sessions/tasks`（任务快照）、
`/api/sessions/task-status`（任务状态查询）。
请求头 `X-Trace-ID` 可选传，响应头含 `X-Trace-ID`。

### 2.1 知识库

#### 2.1.1 `POST /api/kbs/list → list[dict]`

列出所有知识库。Body: `{}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {"id": "uuid", "name": "库名称", "doc_count": 0}
]}
```

#### 2.1.2 `POST /api/kbs → 201, CreateKBResponse`

创建知识库（名称重复时返回已有库）。

Body:
```json
{"name": "库名称", "description": "可选描述"}
```

Response:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"id": "uuid", "created": true}}
```

#### 2.1.3 `POST /api/kbs/delete → 200 | 404`

删除知识库及其向量数据。Body: `{"kb_id": "uuid"}`

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"success": true, "message": "知识库已删除"}}
```

404:
```json
{"code": "NOT_FOUND", "message": "知识库不存在", "data": null}
```

### 2.2 文档管理

#### 2.2.1 `POST /api/kbs/documents/list → list[dict]`

列出知识库中的所有文档。Body: `{"kb_id": "uuid"}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {
    "id": "uuid",
    "filename": "name.pdf",
    "file_type": "pdf",
    "file_size": 1234,
    "status": "ready",
    "chunk_count": 10,
    "error_msg": null,
    "created_at": "2026-07-03T12:00:00"
  }
]}
```

| 键 | 类型 | 说明 |
|----|------|------|
| `id` | str | 文档 UUID |
| `filename` | str | 原始文件名 |
| `file_type` | str | `pdf` / `docx` / `txt` |
| `file_size` | int | 字节数 |
| `status` | str | 当前处理状态 |
| `chunk_count` | int | 入库分块数 |
| `error_msg` | str\|null | 失败原因 |
| `created_at` | str | 上传时间 |
| `eval_score` | float\|null | 分块质量综合评分（0-1，需开启 `CHUNK_EVAL_ENABLED`） |
| `eval_passed` | bool\|null | 质量是否达标（阈值 ≥ 0.70） |
| `eval_detail` | dict\|null | 评估详情 JSON，含 structure_integrity / sbr / granularity_cv 三个模块的分数和断裂明细（完整形状见下） |

**`eval_detail` 完整形状**（`meta_info.eval` 原样直通，产出方 `src/chunking/scorer.py`，**中间无映射层**）：

```json
{
  "version": 1,
  "enabled": true,
  "overall_score": 0.8975,
  "passed": true,
  "structure_integrity": {
    "score": 0.9048,
    "table": {"score": 0.7142857142857143, "total": 7, "broken": [
      {"index": 0, "chunks": [32, 33], "pages": ["?"],
       "break_position": "chunk 32: 14 行 / chunk 33: 12 行", "preview": "|  |  |  | 已报告 |"}
    ]},
    "heading": {"score": 1.0, "total": 9, "broken": []},
    "clause": {"score": 1.0, "total": 17, "broken": []}
  },
  "sbr": {
    "score": 0.963, "total_boundaries": 27,
    "broken_boundaries": [
      {"index": 3, "similarity": 0.3392,
       "preview_before": "…前一段前 50 字…", "preview_after": "…后一段前 50 字…",
       "page_before": 1, "page_after": 2}
    ]
  },
  "granularity_cv": {
    "score": 0.5696, "cv": 0.8607, "min_tokens": 77, "max_tokens": 987,
    "extreme_chunks": [{"index": 32, "tokens": 971, "type": "oversized"}]
  }
}
```

| 键 | 类型 | 说明 |
|----|------|------|
| `version` / `enabled` | int / bool | 结构版本号；评估是否启用 |
| `overall_score` | float\|null | 综合分（结构·SBR·粒度三重加权）；三项全失败时为 `null` |
| `passed` | bool | `overall_score >= 0.70` |
| `structure_integrity.score` | float\|null | 结构完整性总评 |
| `structure_integrity.table` / `.heading` / `.clause` | dict | 三个子项，各含 `score` / `total` / `broken[]` |
| `structure_integrity.*.broken[]` | list[dict] | 断裂项：`index` / `chunks`（跨块下标对）/ `pages` / `break_position` / `preview`（正文前 50 字） |
| `sbr.score` | float\|null | 语义断裂率得分（`1 - 断裂边界数 / 边界总数`） |
| `sbr.total_boundaries` | int | 参与计算的相邻块边界数 |
| `sbr.broken_boundaries[]` | list[dict] | 断裂边界：`index` / `similarity` / `preview_before` / `preview_after` / `page_before` / `page_after` |
| `granularity_cv.score` | float\|null | 粒度均匀性得分（`1 - min(cv, 1)`） |
| `granularity_cv.cv` | float\|null | 原始变异系数（token 数的 `std/mean`） |
| `granularity_cv.min_tokens` / `.max_tokens` | int | 分块 token 数极值 |
| `granularity_cv.extreme_chunks[]` | list[dict] | 极端分块：`index` / `tokens` / `type`（`tiny` \| `oversized`） |

⚠️ **消费方注意（历史踩坑）**：该字段**没有扁平化** —— 不存在 `table_score` / `sbr_score` / `granularity_tiny` 这类一级键，且 `structure_integrity` / `sbr` / `granularity_cv` 的值是**对象不是数字**。2026-07-12 前端曾按扁平键读，`data.granularity_cv.toFixed()` 抛 `TypeError` 使弹窗渲染中断（`deploy/nginx/html/index.html` 的 `showEvalModal`）。另外任一模块整体失败时会被降级为 `{"score": null, "error": "..."}`（`_safe_call`），其下子键**缺失**，消费方须按可选链取值。

#### 2.2.2 `POST /api/kbs/documents/upload → 202`

异步上传文档。立即返回，后台处理。

Content-Type: `multipart/form-data`
Fields: `file`（PDF/DOCX/TXT，最大 10MB）, `kb_id`（uuid）

Response:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"doc_id": "uuid", "status": "processing", "filename": "name.pdf"}}
```

| 错误 | 状态码 |
|------|--------|
| 文件超过 10MB | 413 |
| 不支持的文件类型 | 400 |

后台处理流程：**extracting → completed / failed**

#### 2.2.3 `POST /api/kbs/documents/status → dict`

轮询文档处理进度。Body: `{"kb_id": "uuid", "doc_id": "uuid"}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": {"status": "ready", "chunk_count": 42, "progress": 100, "error": ""}}
```

| 状态值 | 含义 |
|--------|------|
| `processing` | 正在处理 |
| `ready` | 处理完成 |
| `failed` | 处理失败 |
| `not_found` | 文档不存在 |

#### 2.2.4 `POST /api/kbs/documents/chunks → dict`

预览已处理文档的分块内容。Body: `{"kb_id": "uuid", "doc_id": "uuid", "page": 1, "page_size": 50}`

Response:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"items": [...], "total": 42, "page": 1, "page_size": 50}}
```

#### 2.2.5 `POST /api/kbs/documents/delete → 200 | 404 | 403 | 409`

删除文档：软删文档 + 删除其全部分块，**同一事务**内完成。端点只转调
`DocumentService.delete_document`（不再直取 `_doc_repo` / `vector_store`），请求体不变。

Body: `{"kb_id": "uuid", "doc_id": "uuid"}`

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"success": true}}
```

| 状态码 | code | message | 触发条件 |
|--------|------|---------|---------|
| 404 | `DOC_NOT_FOUND` | 文档不存在 | 文档不存在 / 已软删，或不属于该 `kb_id` |
| 403 | `DOC_DELETE_NOT_ALLOWED` | 只能删除自己上传的文档 | 调用者非文档属主 |
| 409 | `DOC_STATUS_CONFLICT` | 文档当前状态不允许删除，仅 ready 和 failed 状态的文档可删除 | 文档状态非 `ready` / `failed`（处理中不可删） |

> 分块删除与软删文档同事务：任一步失败整体回滚，**不得**留下「文档已软删、分块成孤儿」
> 的中间态（旧实现先软删文档再删分块，且吞掉删除异常）。事务边界见 §4 与
> `docs/agents/data-flow.md`「链路 3」。

### 2.3 SSE 流式问答

#### 2.3.1 `POST /api/chat/stream`

Content-Type: `text/event-stream`

请求体 `ChatStreamRequest`（`src/api/model/request.py`）：

| 字段 | 说明 |
|------|------|
| `session_id` | 会话 ID |
| `kb_id` | 知识库 UUID（空字符串不检索，未绑定 KB） |
| `query` | 用户问题 |
| `deep_thinking` | 深度思考开关（可选，默认 `false`）：`true` 时 agent 主 LLM 以思考模式调用（`enable_thinking=true`）；`false` 显式关闭。来自 `chat-thinking-toggle` capability |

> **落库时机（streaming-decouple M1）**：请求开始（per-session Redis 锁获取后）即同步写 user 消息到 PostgreSQL（`svc.save_user_async`），session 创建幂等。端点方法自 streaming-decouple M3.4 起为 **POST**（GET→POST 迁移与前端传输层替换 EventSource 一同落地），前端经 `fetchStream`（fetch + getReader 手动解析 SSE）调用，不再依赖原生 EventSource。Redis 的 user 写入仍保留在 `agent_service.stream_chat` 内（发生在 `get_history_async()` 之后，避免当前 query 作为历史进 prompt）。

事件流（按推送顺序，不含追问路径）：

```json
event: status
data: {"stage": "agent", "message": "正在思考..."}

event: status
data: {"stage": "retrieve", "message": "正在检索相关文档..."}

event: status
data: {"stage": "retrieve", "message": "检索完成，正在分析..."}

event: status
data: {"stage": "web_search", "message": "正在联网搜索..."}

event: status
data: {"stage": "web_search", "message": "联网搜索完成，正在分析..."}

event: token
data: {"token": "回答文本片段"}

event: citation
data: {"source": "文件名.pdf", "page": 15, "snippet": "内容摘要...", "score": 0.95, "highlighted_snippet": "<mark>高亮</mark>内容", "kind": "kb"}

event: model_info
data: {"model": "模型名", "is_fallback": false}

event: done
data: {"trace_id": "trace_xxx"}

event: error
data: {"error": "错误消息"}
```

> `status` 事件 data 可选字段 `detail?: string`：工具调用入参明细，仅非空时序列化（向后兼容），
> 由 `_convert_event` 的 TOOL_START 分支填充——`retrieve_kb` 为 `query=...`、`search_web` 为 `queries=[...]`，
> 与实时流同构写入事件缓冲，历史回放（2.4.4）原样带出，供前端展示工具调用明细。

### `delegate` 事件详情

独立 SSE 事件 `event: delegate`，仅**主 agent 调用 `delegate_task` 委派子代理**时产生
（**inline 命中不推**——方法论注入主 agent，无独立子代理）；定点 fork skill 与通用委派
（省略 `skill`）都推，通用委派的 `skill` 字段填用户可见标签 `SSEInteractionTexts.DELEGATE_GENERIC_LABEL`
（`"通用分析"`）。事件由 delegate_task fork
分支与 executor 投递到 `ctx.clarify_channel`，经 `_drain_clarify_channel` 并行消费转
`SSEDelegateEvent`（`src/utils/sse.py`），**不经外层 astream_events 映射**。action 三态
`start` / `delta` / `end`，均携带 `delegate_id`（短 uuid，贯穿该次委派）+ `skill`：

```json
event: delegate
data: {"delegate_id": "a1b2c3d4", "action": "start", "skill": "财务建模专家", "kind": "", "delta": "", "ok": true, "reason": ""}

event: delegate
data: {"delegate_id": "a1b2c3d4", "action": "delta", "skill": "财务建模专家", "kind": "thinking", "delta": "先按营收口径拆解…", "ok": true, "reason": ""}

event: delegate
data: {"delegate_id": "a1b2c3d4", "action": "delta", "skill": "财务建模专家", "kind": "content", "delta": "2024 年营收同比…", "ok": true, "reason": ""}

event: delegate
data: {"delegate_id": "a1b2c3d4", "action": "end", "skill": "财务建模专家", "kind": "", "delta": "", "ok": true, "reason": ""}
```

中断时 end 帧示例（`ok=false` + reason 取 `DelegateStopReason` 值）：

```json
event: delegate
data: {"delegate_id": "a1b2c3d4", "action": "end", "skill": "财务建模专家", "kind": "", "delta": "", "ok": false, "reason": "total"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `delegate_id` | str | 本次委派唯一 id（短 uuid，贯穿该次委派所有 start/增量/end 与 task execution 条目）；同一次回答内多次委派互不相同 |
| `action` | str | `start`（委派开始）\| `delta`（过程增量）\| `end`（委派结束） |
| `skill` | str | 命中的 skill 名；通用委派（省略 `skill`）填 `SSEInteractionTexts.DELEGATE_GENERIC_LABEL`（`"通用分析"`） |
| `kind` | str | 仅 delta 用：`thinking`（思考增量）\| `content`（正文增量）；start/end 为空串 |
| `delta` | str | 仅 delta 用：增量文本（executor 聚合/节流后投递）；start/end 为空串 |
| `ok` | bool | end 用：`true`=正常完成；`false`=中断/失败 |
| `reason` | str | end 用：正常 `ok=true` → `""`；中断 `ok=false` → 取 `DelegateStopReason` 值（`idle`/`total`/`turn`/`failed`/`cancelled`，词表唯一，见 glossary.md） |

**委派终态文案（前端渲染，`deploy/nginx/html/chat.html`）**：收到 `action=end` 时
`ok=true` → "领域专家分析完成"；`ok=false` → "分析中断 · <原因中文短词>"（原因映射同
`SSEInteractionTexts.DELEGATE_REASON_TEXT`：idle=空闲超时 / total=超时 / turn=轮次上限 /
failed=失败 / cancelled=已取消）——不再无条件推"完成"。

**scope 隔离与展示边界**：delegate 事件独立于主 token 流——增量**不写入主回答气泡 /
`full_answer` / 主 token 事件 / 落库内容**（防污染不变量，回归测试覆盖）；fork 子代理
内部 LLM 调用**不产生主 `MODEL_TURN`/`status` 事件**（过程观测走 `[delegate]` 日志，
见 logging-rules.md）。前端在回答内按 `delegate_id` 渲染"领域专家分析过程"折叠区
（thinking/content 分列、默认折叠），一次回答多次委派按 id 分节不串流。fork 最终结果
作为工具返回**纯文本**回主 agent 整合（不带 [n] 引用，引用只指向主 agent 自身检索来源），
不出现在 delegate 事件载荷中。

### `task` 事件详情

任务/进度看板增量事件（task-board change）。由进程内 `SessionTaskRegistry`
（`src/chat/task_registry.py`，模块级单例 `task_registry`）在 create/update/mark_terminal
变更后经 `emit_task_event` 写该 session 事件缓冲，SSE 推前端；同入缓冲，resume 经
`from_payload` 原样回放。两类条目来源：`type=plan`=主 agent 经 Task 工具建项；
`type=execution`=delegate_task 子代理（定点 fork / 通用委派）自动登记（`task_id=delegate_id`，与 delegate 事件
贯穿）。前端看板只读，无写入口。

```json
event: task
data: {"action": "created", "task": {"task_id": "a1b2c3d4", "title": "财务建模专家", "type": "execution", "status": "running", "stage": "分析中", "summary": "正在分析 2024 营收结构…", "delegate_id": "a1b2c3d4", "dependencies": [], "reason": "", "created_at": 1780000000.0, "updated_at": 1780000000.0}}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `action` | str | `created`（建项）\| `updated`（非终态字段更新）\| `terminal`（进入终态集合 done/failed/timeout/cancelled；由 `update_task` 判定，cancelled 也属终态） |
| `task` | dict | 任务快照（`TaskItem.to_dict()`），字段同下 |

`task` 快照字段（与 `GET /api/sessions/tasks` 返回项一致，见 2.4.7）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | str | 任务 id：plan=工具生成短 uuid；execution=delegate_id |
| `title` | str | 标题（LLM 提供或 skill 名；通用委派 execution 用 `DELEGATE_TASK_TITLE_TMPL.format(skill=SSEInteractionTexts.DELEGATE_GENERIC_LABEL)`=「通用分析 · 领域专家分析」） |
| `type` | str | `plan`（主 agent Task 工具建项）/ `execution`（delegate 自动登记） |
| `status` | str | `pending` / `running` / `done` / `failed` / `timeout` / `cancelled`（后四者为终态） |
| `stage` | str | coarse 阶段（delegate start/end/中断边界更新，不做逐 delta 写入） |
| `summary` | str | 摘要文本（plan 进展 / execution 活动说明） |
| `delegate_id` | str | execution 专属（= task_id，关联"分析过程"折叠区）；plan 为空串 |
| `dependencies` | list[str] | plan 依赖任务 id 列表 |
| `reason` | str | 中断原因（`DelegateStopReason` 值；normal 为空串） |
| `created_at` / `updated_at` | float | unix 时间戳（updated_at 供 TTL 清理排序） |

> **TTL 口径（事件=增量提示、快照=权威）**：注册表条目 TTL 30min（`TASK_TTL_SECONDS`，
> 惰性清理）大于事件缓冲 TTL 5min（streaming buffer 300s）——SSE `task` 事件只保证 5min
> 内可达，更长窗口内权威数据源是 `GET /api/sessions/tasks` 快照接口（拉取前先 sweep）。
> 前端 resume/刷新以快照校正，勿因缓冲被清（无 task 事件回放）而误判任务消失。
>
> **收敛语义（前端只读）**：`task_stop`（Task 工具）不置 `ctx.abort_signal`——阻塞式 fork
> 下主 agent 无法在 delegate 运行期间并行停任务，跨请求/竞态 set abort 会误取消整个主
> 请求；故 task_stop 仅对非终态条目（含残留 running/pending）置 cancelled。运行中
> delegate 的真实取消收敛到 `POST /api/sessions/cancel`（core：fork 响应同一
> abort_signal，中断原因=cancelled，delegate end 与注册表终态一致）。

追问路径（~~当 classify 检测到缺失实体时~~ ⚠️ 已退役，agent 化后由 `ask_user` 事件 + `POST /chat/clarify-answer` 接管，见下文 2.3.2 与 ask_user 事件详情）：

```json
event: status
data: {"stage": "classify", "message": "正在分析查询类型..."}

event: clarification
data: {"type": "entity_completion", "question": "请问您想查询哪一年的数据？", "missing_entities": [{"type": "year", "question": "请问您想查询哪一年的数据？"}], "suggestions": ["2023年", "2024年", "其他"]}

event: done
data: {}
```

| 事件 | 触发条件 | 说明 |
|------|---------|------|
| `status` | agent 循环按事件类型接线 | stage 取值：`agent`（on_chat_model_start "正在思考..."）、`retrieve`（on_tool_start/end "正在检索相关文档..." / "检索完成，正在分析..."）、`web_search`（on_tool_start/end "正在联网搜索..." / "联网搜索完成，正在分析..."，KB 不达标时走 search_web 兜底才出现）、`turn_agent` / `turn_skill`（每轮来源声明，见下文「status.stage 来源声明」） |
| `token` | LLM 生成中 | LLM 生成文本片段，前端逐段追加 |
| **`reasoning`** | **agent 节点 LLM 流式输出思考增量（enable_thinking=true 且模型返回 reasoning_content，经 `ReasoningPreservingChat` 提取）** | **思考过程增量（data: {"delta": "..."}），前端累积渲染 Think 折叠行；每轮 LLM 调用一个，默认收起；收到正文 token/状态/ask_user/abstention/done 时定型** |
| **`delegate`** | **delegate_task 委派 fork skill 时产生（inline 命中不推）** | **fork 子代理过程事件：`action=start`（开始）\| `delta`（过程增量，`kind=thinking`\|`content`、`delta`=增量文本）\| `end`（结束，`ok` 区分完成/中断）。前端按 delegate_id 分节渲染"分析过程"折叠区，增量不进主 token 流/full_answer（详见「delegate 事件详情」）** |
| **`task`** | **任务注册表 create/update/mark_terminal 变更（src/chat/task_registry.py 的 `emit_task_event`）** | **任务/进度看板增量事件：`action=created`（建项）\| `updated`（非终态更新）\| `terminal`（进入终态）；`task`=任务快照 dict（task_id/title/type/status/stage/summary/delegate_id/dependencies/reason/created_at/updated_at，`TaskItem.to_dict()`）。前端据此增量渲染看板（详见「task 事件详情」）** |
| `citation` | format 节点完成 | 引用来源，按 source+page 去重；data 含 `kind`（`kb` 知识库 / `web` 网络搜索，默认 `kb`），前端按来源类型区分展示 |
| **`clarification`** | ~~classify 检测到缺失实体~~ | **已退役**：classify 已删，无预判来源，不再生产，前端已由 `ask_user` 接管 |
| **`ask_user`** | **ask_user 工具被调用（agent 需要用户补充信息）** | **问题卡片事件，前端 composer 接管输入区；提交答案后同流续答** |
| **`abstention`** | **模型输出命中拒答标记（如"未在文档中找到"）** | **abstention 标识 + 转人工提示文案，前端展示转人工入口；判定只看 answer 文案，与是否触发检索无关** |
| `model_info` | agent 循环末次 LLM 调用完成（on_chat_model_end 捕获） | 实际使用的模型名和 fallback 状态 |
| `done` | 流结束 | 流结束标记；携带 `trace_id`（当前请求全链路追踪 ID），前端记录后随答案反馈回传 |
| `error` | 异常 | 异常时推送，无 retry 机制 |

##### citation.tier（source-tier-labeling）

SSECitationEvent payload 与落库 sources 的新增字段 `tier: int | null`：

| tier | 标签 | 语义 |
|------|------|------|
| 0 | 内部文档 | KB 知识库文档（不走域名分级） |
| 1 | 官方一手 | T1 官方域名 / .gov.cn / .edu.cn |
| 2 | 权威媒体 | T2 财经媒体清单 |
| 3 | 一般 | T3 未命中默认中性档 |
| 4 | UGC | T4 用户生成内容清单 |
| null | （无徽标） | 存量消息或未定档 |

**标签文案以 `src/config/const.py SOURCE_TIER_LABELS` 为唯一权威，本表与前端 `chat.html TIER_LABELS` 为照抄副本；改动动线：const.py → api_contract.md → chat.html 三步走完才算改完。**徽标仅渲染在引用抽屉条目，引用横条保持既有形态（见 chat-harness-ui spec）。

##### `status.stage` 来源声明：`turn_agent` / `turn_skill`

每轮进入图事件循环前，先回传一次 `agent_used`，再按条件推送两条来源声明 `status` 事件
（`stage` 取 `turn_agent` / `turn_skill`）。顺序为「`agent_used` → `turn_agent` →
`turn_skill` → 首个节点状态行」，且**先于同轮的澄清（`ask_user`）与委派（`delegate`）事件**
——声明写入须早于 clarify drain 任务启动，否则并发 drain 会把澄清/委派事件插到前面。
两条声明同写两个 sink：manager 缓冲（实时 SSE）与 `capture.events_log`（随 `process` 持久化、
历史回放原样带出）。

| stage | 文案模板 | 产出条件 |
|-------|---------|---------|
| `turn_agent` | `当前使用了 {agent}`（`{agent}` = 预设展示名 `display_name`，缺失回落 `name`） | 本轮解析出生效智能体（`agent_display_name` 非空）；未绑定智能体则无此条 |
| `turn_skill` | inline / preload：`成功加载 skills：{顿号连接的名单}`；fork：`使用技能：/{skill}（子代理执行）` | inline / preload 需名单非空；fork 需 `direct_skill` 非空；本轮无技能动作或 `/xxx` 命令失败（未注册）则无此条 |

- 每轮**至多各一条**；条件不满足即不产出，**不得输出空行**（预加载正文为空时不置 preload，
  不声明「成功加载 skills」而名单为空）。
- 语义：声明表达**本轮触发**（意图）；结果（fork 中断/降级、失败原因）由既有通道呈现，
  **声明不撤回、不改写**。
- **文案不是结构化真源**：消费端取生效智能体必须用 `agent_used`，**不得**解析来源声明文本；
  前端顶栏继续走 `agent_used`（评审约束）。

#### 2.3.2 `POST /api/chat/clarify-answer → 200 | 404`

提交 ask_user 问题答案，解析挂起的澄清 Future，使 agent 继续执行。

```json
{"session_id": "sid", "answers": [{"id": "q1", "selected": ["2024年"], "custom": ""}]}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | str | 会话 ID，定位挂起的 ask_user Future |
| `answers` | list | 用户答案列表，每条含 id/selected（可含 custom），原样写入 Future 作为 ask_user 返回值 |

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": true}
```

**404 语义**：该澄清问题已超时或不存在（查无 Future 或 Future 已结束），
detail 为 `CLARIFY_ANSWER_NOT_FOUND_TEXT`（"澄清问题已过期或不存在"）。`pop` 保证单次消费。

> ⚠️ `POST /chat/clarify-answer` 与 SSE 是独立 HTTP 请求，contextvar 不跨请求，
> 答案经进程级 `pending_asks` 注册表（session_id → asyncio.Future）送达。
> 解析成功后，答案文本还会作为 user 消息写入 Redis 对话历史（chat_manager.add_message_async），
> 与 `stream_chat` 入口写入原始 query 的轨道并存，保证跨 turn 上下文不丢。

### `clarification` 事件详情

> ⚠️ **已退役**：classify 已删（agent 化改造），无预判来源，不再生产；
> 类定义与序列化逻辑已从 `src/utils/sse.py` 删除。以下内容仅供历史追溯。

```python
# 数据结构（src/utils/sse.py: SSEClarificationEvent）
{
  "type": "entity_completion" | "intent_clarification" | "no_data_guidance",
  "question": str,              # 追问文本，如 "请问您想查询哪一年的数据？"
  "missing_entities": [dict],   # [{"type": "year", "question": "请问您想查询哪一年的数据？"}]
  "suggestions": [str],         # 快捷选项，如 ["2023年", "2024年", "其他"]
  "questions": [dict]           # 批量追问列表 [{"type", "question", "suggestions"}, ...]；空则省略，兼容旧前端
}
```

`suggestions` 根据缺失实体类型从 `SUGGESTIONS_MAP` 映射：

| 实体类型 | 快捷选项 |
|---------|---------|
| `year` | `["2023年", "2024年", "其他"]` |
| `quarter` | `["一季度", "二季度", "三季度", "四季度"]` |
| `month` | `["1月", "12月", "其他"]` |
| `company` | `["腾讯", "阿里巴巴", "其他"]` |
| `metric` | `["营收", "利润", "毛利率", "其他"]` |
| `default` | `["请补充说明", "其他"]` |

### `ask_user` 事件详情

```json
event: ask_user
data: {"type": "ask_user", "questions": [{"id": "q1", "question": "您想查询哪一年的数据？", "options": ["2024年", "2023年", "其他"], "multi_select": false}]}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | 固定 `"ask_user"`，前端据此路由到追问卡片 |
| `questions` | list | 问题卡片列表；每条含 `id`（答案回显）/`question`（问题文本）/`options`（候选）/`multi_select`（是否多选）。`options` 来源随 `ASK_USER_MODE_DSH`：**默认 `true`（dsh 模式）系统不注入 KB dimension 候选，直接用模型自带的 options（可为空 = 纯文本问题）**；`false`（dual 模式）模型自带优先，无 options 时按 dimension 从 KB 聚合真实候选注入（防编造），KB 无候选时兜底 `SUGGESTIONS_MAP` |

> 提交答案走 `POST /api/chat/clarify-answer`，提交后 SSE 保持连接、同流续答；
> 单 turn 最多 `MAX_ASK_PER_TURN`（2）次调用，超时（`ASK_USER_TIMEOUT` 120s）返回超时文案继续。

### `abstention` 事件详情

```json
event: abstention
data: {"type": "abstention", "message": "未在文档中找到相关数据，可尝试转人工咨询"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | 固定 `"abstention"`，前端据此路由到转人工提示 |
| `message` | str | 转人工提示文案（`SSEAbstentionEvent` 默认值） |

> 触发：agent 迭代结束后检索上下文为空或答案命中拒答标记（`_is_abstention`）；
> 文案固定为转人工引导，与拒答检测（`SSEInteractionTexts.ABSTENTION_MARKERS`）配套。

### 追问流程关键约束

- 追问后用户回答**必须复用同一 `session_id`**，graph 通过 `_history` 获取上轮实体信息
- 追问期间前端不应显示"回答结束"或"done"相关提示
- 追问路径不产生 `token`、`citation` 事件

### 2.4 会话管理

#### 2.4.1 `POST /api/sessions/list → list[dict]`

列出最近 50 个会话。始终返回 200。Body: `{}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {
    "id": "session_id",
    "title": "首条消息前20字",
    "kb_id": "uuid",
    "kb_name": "库名称",
    "message_count": 3,
    "created_at": "2026-07-03T12:00:00",
    "updated_at": "2026-07-03T12:05:00"
  }
]}
```

#### 2.4.2 `POST /api/sessions/messages → list[dict] | 404`

获取会话消息历史。Body: `{"session_id": "sid"}`

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {
    "role": "user",
    "content": "问题文本",
    "sources": null,
    "status": "complete",
    "created_at": "2026-07-03T12:00:00"
  },
  {
    "role": "assistant",
    "content": "回答文本",
    "sources": [
      {
        "source": "文件名.pdf",
        "page": 3,
        "snippet": "原文片段（最多 200 字符）",
        "kind": "kb",
        "index": 1
      },
      {
        "source": "https://example.com/aliyun",
        "page": 0,
        "snippet": "阿里云轻量应用服务器适合个人博客",
        "kind": "web",
        "index": 2
      }
    ],
    "status": "interrupted",
    "created_at": "2026-07-03T12:00:05",
    "model_name": "qwen3-32b",
    "process": {
      "format_version": 1,
      "events": [
        {"seq": 1, "type": "status", "payload": {"stage": "retrieve", "message": "正在检索相关文档..."}},
        {"seq": 2, "type": "preamble", "payload": {"text": "先看一下营收口径…"}}
      ]
    }
  }
]}
```

`status` 取值 `complete` / `interrupted`，标识消息是否完整生成（前端据此标记被中断的回答）。

`sources` 字段：assistant 消息携带的引用来源列表，新数据为结构化对象数组（每项含 `source` 文件名/URL、`page` 页码、`snippet` 原文片段、`kind` 来源类型 `kb`/`web`、`index` 引用编号、`tier` 来源等级 `int 0-4` 或 `null`，语义见上文「citation.tier」）；前端历史回放路径根据此字段重建「来源」横条与右侧抽屉（`tier` 仅用于抽屉条目徽标，横条不渲染）。`kind` 与 SSE `citation` 事件 `data.kind` 同源。**存量旧数据**为扁平字符串数组（每项形如 `"文件名.pdf (第3页)"`），由 `get_messages` 反序列化时循环解包兼容（最多解 4 层剥离历史双重 JSON 转义）；前端对字符串项降级——横条照显、抽屉 `snippet` 留空。

`process` 字段（assistant 消息）：持久化的过程轨迹对象，`{"format_version": 1, "events": [{"seq", "type", "payload"}, ...]}`，seq 从 1 递增；前端历史回放据此重建过程容器，回答正文由 `content` 列承载、不重复入列。事件 `type` 实际枚举：`status` / `reasoning` / `preamble` / `delegate` / `ask_user`——`token` 帧不直接入列，正文前的旁白段固化为 `preamble`（见 glossary.md「旁白」），末轮 answer 正文 token 与 `model_info` / `abstention` / `done` / `error` / `citation` 五类排除（分别由 content / 既有列承载）。存量消息为 `null`；脏数据（非法 JSON / 非 dict）降级为 `null` 不阻断消息返回。

`model_name` 字段（assistant 消息）：实际回答模型名，与 SSE `model_info` 事件同源；存量消息为 `null`。前端历史回放据此重建模型标注，为空/缺失时不渲染。

404:
```json
{"code": "NOT_FOUND", "message": "会话不存在", "data": null}
```

#### 2.4.3 `POST /api/sessions/delete → 200 | 404`

删除会话及其所有消息。Body: `{"session_id": "sid"}`

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"success": true}}
```

404:
```json
{"code": "NOT_FOUND", "message": "会话不存在", "data": null}
```

#### 2.4.4 `GET /api/sessions/events?session_id={sid}&after_seq={seq}`

SSE 断点续接：前端刷新页面后重放进行中生成的未消费事件，再 tail 新事件直到终态。

| 参数 | 说明 |
|------|------|
| `session_id` | 会话 ID |
| `after_seq` | 起始 seq（已消费的最大 seq，默认 0 = 从头回放） |

Content-Type: `text/event-stream`。事件格式与实时流（2.3.1）一致：
先回放缓冲中 `seq > after_seq` 的事件，随后 tail 新事件直到 `done` / `error`
终态；tail 期间无新事件超过 180s（`_subscribe_buffer` 默认 `max_idle`）
推送续传超时 `error` 事件后结束。缓冲不存在时立即返回单个 `done` 事件。

> **主 POST 流与 resume 的续接语义（delegate-hardening-observability）**：
> 主 POST 流（2.3.1 `/chat/stream`）订阅 `max_idle=None`（`src/services/agent_service.py`
> `stream_chat` 经 `_subscribe_events` 订阅），**不按 180s 空闲收流**——流终态由任务生命周期
> 提供（后台任务正常/异常/取消全路径均补 `done`/`error`），合法静默（ask_user 澄清等待、
> fork 长跑无事件但任务存活）不断流。本 resume 端点**保留 `max_idle=180` 空闲兜底**：
> 无活跃任务且持续无事件时仍按空闲阈值返回"续传超时" `error`（防"无任务僵尸续接"）。
> 前端 `fetchStream` 收到干净 EOF 且本轮未收 `done`/`error` 终态时，`onClose` 按已消费
> 最大 `seq`（`lastSeq`）自动调 `resumeStream(after_seq=lastSeq)` 接回事件流；已收终态的
> 正常 EOF 不触发续接。delegate 过程事件与主事件同入缓冲，resume 经 `from_payload` 原样回放。

```json
event: token
data: {"token": "回答文本片段"}

event: done
data: {"trace_id": "trace_xxx"}
```

会话不存在或无权访问返回 404（`SESSION_NOT_FOUND`），与 2.4.2/2.4.3 一致。

#### 2.4.5 `GET /api/sessions/task-status?session_id={sid}`

查询会话生成任务状态，供前端判断是否在途生成、是否已完成。

| 参数 | 说明 |
|------|------|
| `session_id` | 会话 ID |

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"status": "generating", "buffer_seq": 3}}
```

| 键 | 类型 | 说明 |
|----|------|------|
| `status` | str | `generating`（缓冲存在且无终态，生成中）/ `completed`（缓冲有 done/error 终态，或 PostgreSQL 已有 assistant 消息）/ `idle`（无缓冲且无 assistant 消息） |
| `buffer_seq` | int\|null | 当前缓冲最大事件序号，仅缓冲存在时返回 |

判定顺序：先查进程内缓冲（`streaming_manager`），无缓冲时回退查 PostgreSQL 消息
（`svc.get_messages` 是否存在 `role=assistant`）。会话不存在或无权访问返回 404
（`SESSION_NOT_FOUND`），与 2.4.2/2.4.3 一致。

#### 2.4.6 `POST /api/sessions/cancel`

主动停止某会话正在进行的生成（前端停止按钮）：置位该 session 的 abort_signal。
Body: `{"session_id": "sid"}`

有活跃任务（进程内注册表存在 abort 信号）时置位并返回 `cancelled=true`：

```json
{"code": "SUCCESS", "message": "操作成功", "data": {"cancelled": true, "session_id": "sid"}}
```

无活跃任务时返回 `cancelled=false`（`reason=no_active_task`）：

```json
{"code": "SUCCESS", "message": "操作成功", "data": {"cancelled": false, "session_id": "sid", "reason": "no_active_task"}}
```

会话不存在或无权访问返回 404（`SESSION_NOT_FOUND`），与 2.4.2/2.4.3 一致。

> ⚠️ 本端点只置位 abort_signal，后台任务真正中断还需生成路径消费该信号
> （M4 接线）。信号置位后由 `_run_with_finalize` 的取消分支写 interrupted
> 部分回答与 `done(cancelled)` 终态。

#### 2.4.7 `GET /api/sessions/tasks?session_id={sid}`

任务看板快照读取：页面刷新/切换会话后初始化看板。权限与 sessions/events 一致
（会话存在且属于当前用户，无权返回 404 `SESSION_NOT_FOUND`）。仅读运行期进程内
注册表（`task_registry`），不落库；**无任务返回空列表 `[]`**。拉取前先 `sweep_expired`
（惰性清理仅在 create_task 时触发，只读快照需主动清理超 TTL 条目）。

| 参数 | 说明 |
|------|------|
| `session_id` | 会话 ID |

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {"task_id": "a1b2c3d4", "title": "财务建模专家", "type": "execution", "status": "done", "stage": "", "summary": "分析完成", "delegate_id": "a1b2c3d4", "dependencies": [], "reason": "", "created_at": 1780000000.0, "updated_at": 1780000000.0}
]}
```

`data` 为该会话全部任务条目的快照 dict 列表（`TaskItem.to_dict()`，按 `updated_at`
降序），每项字段与 SSE `task` 事件 payload 的 `task` 一致（见「task 事件详情」）。
作为**权威数据源**补齐 SSE `task` 事件 5min 缓冲窗口之外的看板状态（事件=增量提示、
快照=权威，TTL 口径见「task 事件详情」）。

### 2.5 评估报告

#### 2.5.1 `POST /api/kbs/eval/latest → dict | null`

获取知识库最新的 RAGAS 评估报告。Body: `{"kb_id": "uuid"}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": {
  "eval_date": "2026-07-12T18:00:00",
  "faithfulness": 0.92,
  "answer_relevancy": 0.88,
  "context_precision": 0.85,
  "context_recall": 0.87,
  "overall_score": 0.89,
  "passed": true,
  "qa_count": 22,
  "run_type": "manual"
}}
```

无评估记录时返回 `{"code": "SUCCESS", "data": null}`。

### 2.6 健康检查

#### 2.5.1 `POST /api/config → AppConfigResponse`

获取前端配置（如上传文件大小限制）。Body: `{}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": {"max_upload_size": 10485760}}
```

| 键 | 类型 | 说明 |
|----|------|------|
| `max_upload_size` | int | 单文件上传上限（字节），默认 10MB，可通过 `MAX_FILE_SIZE` 环境变量覆盖 |

#### 2.5.2 `GET /api/health → dict`

```json
{"status": "ok"}
```

### 2.7 答案反馈

#### 2.7.1 `POST /api/feedback → 200`

保存用户对单条答案的评分与可选评论。Body:

```json
{"session_id": "sid", "message_index": 2, "rating": "positive", "comment": "回答准确", "trace_id": "trace_xxx"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | str | 会话 ID |
| `message_index` | int | 会话内消息序号（前端消息数组索引，从 0 起），非数据库 ID |
| `rating` | str | `positive` / `negative`，非法值返回 422 |
| `comment` | str | 用户评论，可选，默认空串 |
| `trace_id` | str | 全链路追踪 ID（前端从 SSE `done` 事件记录并回传，用于还原该答案的生成链路），可选，默认空串 |

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": true}
```

⚠️ 落库失败只记日志不报错（容错），前端始终收到成功响应。
⚠️ `message_index` 是前端消息数组的序号语义，存到 `feedback` 表原样保留，
不解析到 `conversation_history` 行。同一 (session_id, message_index) 可重复反馈（追加记录）。

### 2.8 企微智能机器人回调（`/api/wecom/callback`）

外部平台回调端点，非前端消费；受 `WECOM_BOT_ENABLED` 控制（关闭时 `404`）。

> 该端点仅在 `WECOM_BOT_MODE=callback` 时可用；`long_connection` 模式下返回 `404`（长连接不经 HTTP 回调）。

| 方法 | 入参 | 成功响应 | 失败 |
|---|---|---|---|
| `GET` | query `msg_signature`/`timestamp`/`nonce`/`echostr` | `text/plain`：解密后的 echostr 明文（1 秒内） | 验签失败 `403` |
| `POST` | query `msg_signature`/`timestamp`/`nonce`；body `{"encrypt":"..."}` | `application/json`：`{"encrypt","msgsignature","timestamp","nonce"}`；**无回包时 0 字节空体 + 200** | 验签失败 `403`；body 非 JSON / 解密或解析失败 `400` |

约定：`msgsignature = sha1(sort(token, 请求timestamp, 请求nonce, 回包encrypt))`；普通消息只支持 `stream`/`template_card` 回复。

### 2.9 阿里云告警 Webhook（`/api/alerts/webhook`）

阿里云 ARMS 告警接入端点，非前端消费；受 `ALERT_WEBHOOK_TOKEN` 控制（未配置或不匹配 → `404`）。
路由在 `src/api/alerts.py`，解析 / 去重 / 编排在 `src/services/alert_service.py`。

| 方法 | 鉴权 | 请求体 | 成功响应 | 失败 |
|---|---|---|---|---|
| `POST` | 请求头 `X-Alert-Token`（**不放 URL**，避免被访问日志记录） | ARMS 自定义模板渲染的 JSON | `202`（已受理，含去重丢弃） | 鉴权失败 `404`；载荷非法 `400` |

请求体字段（ARMS 侧按本项目规定的自定义模板渲染，**字段名即解析契约**；模板示例与接入步骤见 `docs/agents/deploy-runbook.md`「告警接入」）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `alarmId` | str | 报警标识（派生报警会话 key，**必填**；缺失或空串 → `400`） |
| `commonLabels.alertname` | str | 告警名称（**必填**；缺失或空串 → `400`） |
| `level` | str | 告警等级（可选；缺失或空串回落 `-`） |
| `alerts[]` | list | 告警明细列表（可选；非列表按空处理） |
| `alerts[].fingerprint` | str | 逐条告警指纹（**入口去重键**；全批无有效项 → `400`） |
| `alerts[].annotations.message` | str | 该条告警描述（可选；多条以换行合并为分析正文） |

约束：**必须在 5 秒内返回**（ARMS Webhook 硬超时，超时触发上游重试）；校验与去重通过后立即
`asyncio.create_task` 起后台任务（占位 → 跑 Agent（禁澄清）→ 结论 / 兜底），**分析不在响应路径上**。
去重为逐条指纹的时间窗判定：仅当全部指纹都命中窗口才丢弃（同样返回 `202`），否则登记整批并正常处理。

---

## 3. 接口层：AppService ↔ Repo 层

### 3.1 `KbRepo.get_all_kb() → list[KbModel]`

| 属性 | 类型 | 说明 |
|------|------|------|
| `id` | str | 知识库 UUID |
| `user_id` | str | 所属用户 UUID |
| `name` | str | 知识库名称 |
| `description` | str | 描述，可为空字符串 |
| `doc_count` | int | 未删除文档数；**每次查询由子查询实时统计并覆盖**，不读静态列（该列无维护逻辑） |
| `is_deleted` | int | 软删除标志：`0` 活跃 / `1` 已删 |
| `created_at` | datetime | 创建时间（UTC） |
| `updated_at` | datetime | 更新时间（UTC） |

⚠️ 返回顺序按 `created_at DESC`。调用方不要假设按名称排序。

### 3.2 `DocumentRepo.get_documents(kb_id) → list[dict]`

| 键 | 类型 | 说明 |
|----|------|------|
| `id` | str | 文档 UUID（`doc_id`） |
| `filename` | str | 原始文件名 |
| `file_type` | str | 扩展名（`pdf` / `docx` / `txt`） |
| `file_size` | int | 文件大小（字节） |
| `status` | str | `pending` / `parsing` / `chunking` / `indexing` / `ready` / `failed` |
| `chunk_count` | int | 分块数 |
| `error_msg` | str \| None | 处理失败时的错误信息 |
| `meta_info` | str \| None | JSON 字符串，含 `eval` 评估数据 |

### 3.3 `KbRepo.delete_kb(kb_id) → bool`

CASCADE 级联删除：知识库 → 文档 → 对话历史。

⚠️ 调用方必须同时调用 `VectorStore.delete_collection()` 清理分块数据
（方法名为兼容保留，语义 = 按 `kb_id` 删 `chunks` 行，见 §4.6），Repo 层不感知向量存储。

### 3.4 `DocumentRepo.update_document_status(doc_id, status, chunk_count=0, error_msg="") → None`

更新文档处理状态。由 `_process_document` 后台任务调用。

### 3.5 `DocumentRepo.update_document_meta_info(doc_id, meta_info) → None`

更新文档的 `meta_info` JSON 列（存储分块评估结果）。由 `_process_document_task` 在分块质量评估后调用。

| 参数 | 类型 | 说明 |
|------|------|------|
| `meta_info` | dict | 写入 JSON 列的字典，评估结果放在 `{"eval": {...}}` 下 |

### 3.6 `EvalRepo.insert_report(report) → None`

插入一条 RAGAS 评估报告。表结构由 alembic baseline 统一创建（`alembic/versions/0001_pg_baseline.py`），插入路径不再自动建表。

| 参数 | 类型 | 说明 |
|------|------|------|
| `report.kb_id` | str | 知识库 UUID |
| `report.run_type` | str | `manual` / `sampling` / `ci_gate` |
| `report.qa_count` | int | QA 对数量 |
| `report.faithfulness` | float | 忠实度 |
| `report.answer_relevancy` | float | 答案相关性 |
| `report.context_precision` | float | 上下文精确率 |
| `report.context_recall` | float | 上下文召回率 |
| `report.overall_score` | float | 加权综合分（0.3×faith + 0.3×recall + 0.2×precision + 0.2×relevancy）；口径为**全量（未按样本分流）**，见下注 |
| `report.passed` | bool | 是否达标（≥ 0.70） |
| `report.report_path` | str\|null | CSV 报告路径 |
| `report.detail_json` | list\|null | 逐条 QA 得分；每条含 `q_index`、**全部 `RAGAS_METRIC_COLUMNS`**（`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall` / `answer_correctness`）与可选 `retrieval_details`（该 query 精排后 `[{source, score, kind}, ...]`）；**全量（未掩码）口径**，见下注。`NaN` **仅在 ragas 自身对该题该指标产出 NaN 时出现**（本路径不主动置 NaN）。注意 `NaN` 是 Python `json.dumps` 的非标准扩展，**严格 JSON 解析器（如浏览器 `JSON.parse`）会拒绝**；见 §8.4 |

> **口径注（全量 vs 主分）**：本表落库路径读的是**未掩码**的 `result.to_pandas()`
> 全样本，故 `detail_json` / `faithfulness` 等四列 / `overall_score` 均为
> **全量（未按样本分流）**口径——`web`、`empty` 样本也计入。这与 CLI
> 报告 / stdout / 门禁的「**主分**」（仅 `pure_kb` 样本，见 §8.4）**不是同一个数**。
> 前端按 `overall_score` 解读时须知道二者存在口径差异。原「跳过值一律 `NaN`」的
> 表述不准确：只有 ragas 自身返回 `NaN`（如指标超时/额度耗尽）时该键才为 `NaN`。

### 3.7 `EvalRepo.get_latest_report(kb_id) → EvalReportModel | None`

获取知识库最新的 RAGAS 评估报告。按 `created_at DESC LIMIT 1` 查询。

---

## 4. 接口层：AppService ↔ VectorStore

后端是 PostgreSQL + pgvector（`chunks` 表 + `kb_id` 列，无 collection 概念）。
公开入口名仍是 `VectorStore`，实现是 `src/infra/db/vector_store/pg_store.py` 的 `PgVectorStore`。
**所有 IO 方法都是 `async`，调用方必须 `await`。**

> **事务边界**：跨表写操作由调用方用 `session_scope` / `Repo.transaction()` 打开唯一事务，
> 把 `session=` 透传给 `VectorStore` 的写方法（`add_chunks` / `delete_document` /
> `delete_collection`）。`session` 传入时这些方法**只执行语句、不提交** —— 提交与回滚由
> 持有该会话的外层边界决定；`session=None` 时下层自开会话并提交（老调用点行为不变）。
> 规则与历史缺陷见 `docs/agents/defensive-patterns.md`「派生写操作跨事务」。

### 4.1 分块 ID

分块 ID = `f"{doc_id}:{chunk_index}"`，与 PG 行 `(kb_id, doc_id, chunk_index)` 一一对应，用于按文档删除。

### 4.2 VectorStore 初始化

`VectorStore(chunk_repo=None, embed_fn=None)`：`chunk_repo` 缺省用应用 `session_factory` 构造 `ChunkRepo`；
`embed_fn` 缺省为 DashScope 的 `QueryEmbedder`（`embed_query` / `embed_documents`）。

### 4.3 `async VectorStore.add_chunks(kb_id, chunks, doc_id, embeddings=None, session=None) → int`

| 参数 | 类型 | 说明 |
|------|------|------|
| `chunks` | `list[ChunkData]` | 解析器产出的分块数据（Parent-Child 格式） |
| `doc_id` | `str` | 文档 UUID |
| `embeddings` | `list[list[float]] \| None` | 预计算向量；`document_service` 恒预计算，None 分支仅为兼容保留 |
| `session` | `AsyncSession \| None` | 外部事务边界提供的会话；**传入时不提交**（提交/回滚由调用方边界决定），`None` 时下层自开会话并提交 |

按 `(kb_id, doc_id, chunk_index)` 幂等覆盖，并删除尾部残留。返回实际写入行数。
传入 `session` 时 embedding 必须已在事务外算好 —— 向量化是慢的外网调用，不应占用事务。

### 4.4 `async VectorStore.dense_search(kb_id, query, k=5) → list[ChunkResult]`

dense 路 top-k，按余弦距离（pgvector `<=>`）升序。`similarity_search` 是它的别名（保留既有方法名与语义）。

`ChunkResult` 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 分块 ID（`doc_id:chunk_index`） |
| `content` | str | 分块原文 |
| `metadata` | dict | 含 `source`、`page`、`doc_id`、`chunk_index`、`chunk_total` 及 chunker 自定义键 |
| `distance` | float \| None | 余弦距离，仅 dense 路有值（越小越相似） |
| `lexical_score` | float \| None | 词法路得分，仅词法检索时有值 |
| `dense_rank` | int \| None | dense 路排名（0 起）；未出现在 dense 路时为 None |
| `sparse_rank` | int \| None | 词法路排名（0 起）；未出现在词法路时为 None |

**已知限制：** `k` 最大 100（`src/config/const.py` 的 `MAX_QUERY_K`；`pg_store` 为导入方）。`lexical_search` 同样按其截断。

> **全局检索路径已移除。** `rag_tools` 在 `kb_id` 为空时直接返回空结果，不再有「不指定知识库」的检索入口。

### 4.5 `async VectorStore.lexical_search(kb_id, query, k=5) → list[ChunkResult]`

词法路取 top-k，按 `ts_rank` 降序。与 `dense_search` 对称：

| 字段 | 值 |
|---|---|
| `distance` | `None`（词法路无距离） |
| `lexical_score` | 词法得分（`ts_rank`；子串兜底时为 `0.0`） |
| `sparse_rank` | 该结果在词法路的排名（0 起） |
| `dense_rank` | `None` |

**查询条件在应用层构造**（`src/infra/db/lexical_query.py`）：每个词元先按安全字符集（中日韩字符 / 字母 / 数字 / 下划线）剔除，再拼成 `词元:*` 并以 ` | ` 连接（前缀 OR：任一词元命中即召回，精度由下游 RRF/rerank 承担）；词元全被滤掉时降级为 `content LIKE '%原文%'`（LIKE 通配符已转义）。**用户原文不得直接交给 `to_tsquery`** —— 含空格会抛语法错误。

### 4.6 `async VectorStore.delete_collection(kb_id, session=None) → bool`

删除知识库的全部分块；有删除行返回 True，否则 False。`session` 语义同 §4.3
（传入时不提交，由调用方的事务边界决定）。

### 4.7 `async VectorStore.get_chunks_by_doc_id(doc_id, kb_id) → list[ChunkResult]`

按文档 ID 查询所有分块。由分块预览端点调用。

### 4.8 `async VectorStore.get_chunks_paginated(doc_id, kb_id, page=1, page_size=50) → ChunkQueryResult`

分页查询文档分块，`page` 为 **1-based**。`ChunkQueryResult` 含 `items` / `total` / `page` / `page_size`。

### 4.9 `async VectorStore.get_all_chunks(kb_id) → list[ChunkResult]`

取整个知识库的全部分块（空库检查 / 全量读取用）。调用方：`src/cli/eval_ragas.py`（空库检查）、`scripts/rebuild_kb_data.py`（清空前的存在性读取）。

### 4.10 `async VectorStore.delete_document(kb_id, doc_id, session=None) → int`

删除某文档的全部分块，返回删除行数。`session` 语义同 §4.3（传入时不提交，由调用方的事务边界决定）。

### 4.11 `async VectorStore.list_collections() → list[str]`

语义为「**含分块的知识库 ID**」列表（PG 无 collection），只读、不创建任何东西。

### 4.12 `async VectorStore.get_or_create_collection(kb_id) → str`

PG 无 collection 概念：无副作用，直接返回 `kb_id`，仅作既有方法名的兼容入口。

---

## 5. 接口层：LangGraph 图内部

### 5.1 节点定义与输出字段

节点注册名在 `workflow.build_graph` 以字符串给出（agent / agent_finalize / verify / format），
`LangGraphNode.Format.NAME` 为 format 节点的常量名；`skill_direct` 为 `/xxx` 直出节点。
外层图**无独立 `tools` 节点**：model↔tools 循环内化在 `agent` 节点调用的装配产物内部。

| 节点 | 节点名 (NAME) | 输出字段 | 说明 |
|------|--------------|---------|------|
| **agent** | `"agent"` | `messages`, `_system_messages`, `answer` | 图入口（`entry_point`）。组装首轮消息 → 按类型拆 system / 非 system 两半 → seed 装配产物（`build_agent` 产物）→ `ainvoke` → 按**外层已有条数**回写新增段 |
| **agent_finalize** | `"agent_finalize"` | `answer`, `tool_contexts`, `verify_temporal_years` | 循环结束提取末次 AIMessage content → `answer`，读入 `tool_contexts` / 年份 |
| **verify** | `"verify"` | `answer`, `_needs_regenerate` | 验证节点，按会话 KB 绑定分派两态：态 A（未绑定 KB，纯对话）仅走联网引用标注引导；态 B（绑定 KB）走年份完整性 → 缺失按 agent 上轮 `search_web` queries 决策（未调过/带漏 → 注入指引 regen；带全仍缺 → 标注"知识库与网络均未覆盖"直通）→ KB 溯源护栏（有 kb context 无 [n] → 引导补标 regen）；完整性 + 护栏通过即直通 format（在线忠实度 judge 已移除，质量评估转离线另行规划） |
| **format** | `"format"` | `citations: list[dict]` | 去重引用列表 |
| **skill_direct** | `"skill_direct"` | `answer`, `tool_contexts`, `verify_temporal_years`, `_needs_regenerate` | `/xxx` 命中 fork skill 的直出轮（主 agent 零 LLM 轮），契约见 §5.5 |

### 5.2 agent 循环（内化于装配产物）

外层图结构为 `agent → agent_finalize → verify → (format | agent)`（无独立路由节点，
KB 绑定在请求层已定：`kb_id` 非空检索该库，空串 = 未绑定纯对话不检索）。`build_graph`
**参数面不变**（签名见 `workflow.py:46-55`），仅内部结构变——model↔tools 循环本体由
`build_agent` 装配的 `create_agent` 产物承载；循环内的模型 / 工具事件仍以
`langgraph_node == "model"` / `"tools"` 到达 SSE 转换层（外层图不再暴露 `tools` 节点与回边）：

```
agent（外层节点：组装首轮 → seed 装配产物 → ainvoke）
      ▼
 装配产物内部：model ←→ tools（ToolNode 执行 retrieve_kb / ask_user / search_web）
      │ 无 tool_calls / 达回合上限（AgentTurnBudget）
      ▼
 agent_finalize（提取 answer + tool_contexts）
      │ verify：态 A 联网引用引导 / 态 B 完整性+KB 溯源（无在线忠实度 judge）
      │ 校验通过 → format；_needs_regenerate=True → 回 agent
      ▼
    format（引用去重）
```

- 回合上限 `MAX_AGENT_ITERATIONS`（`src/config/const.py`），经 `build_agent(max_turns=…)`
  注入 `AgentTurnBudget` middleware；超限时记 `iteration limit` 并强制收尾
- 工具不能写 state：检索上下文累积到 `RequestContext.tool_contexts`（contextvar），由 `agent_finalize` 读入 `state.tool_contexts`
- per-request 对象（澄清通道 queue / abort 信号 / ask_count）经 contextvar（`current_request_ctx`）传递，并发 session 天然隔离
- 终止条件：装配产物内 model 末条消息无 `tool_calls` 或 `AgentTurnBudget` 判定达回合上限 → 返回外层图交 `agent_finalize`
- **主 agent 采样温度分档（chat-temperature-policy）**：由 `ModelParamsMiddleware` 施加——绑定 KB（`kb_id` 非空）不传 `temperature`，沿用模型构造温度 `LLM_TEMPERATURE`（默认 0.1）；未绑定 KB 逐轮直传 settings 档 `NON_KB_MAIN_TEMPERATURE`（默认 0.6，settings 可调）。档位判据取**请求级上下文**（ctx 缺失时回退图状态 `kb_id`）。同请求内各 agent 轮次档位一致；分档仅作用主 agent，fork 子代理采样不受影响

#### 装配入口 `build_agent`（`src/agents/graph/agent_factory.py`）

主角色与 fork 子代理共用同一装配入口，是 `src/agents/` 下唯一调用 `create_agent` 的地方
（由 `tests/agents/graph/test_agent_factory.py` 的静态扫描断言守住）。

| 参数 | 语义 |
|------|------|
| `model` | 已解析的 LLM 或模型名 |
| `tools` | 工具面（主角色=全量；子角色=只读面筛选结果） |
| `system`（关键字） | system 提供方式——`None`=经运行态携带（主角色，由 `SystemMessagesMiddleware` 施加）；`str`=静态串（子角色，人设 + 执行契约） |
| `max_turns`（关键字） | 主循环回合上限，**回合上限的唯一来源**；非 `None` 时本函数自行追加为最前一项 `AgentTurnBudget(limit=max_turns)`（已显式传入则不重复追加）；`None`=不装配回合预算 middleware（子角色路径） |
| `middleware_extra`（关键字） | 额外 middleware 集合；子角色传空列表 |

返回 `create_agent` 的编译产物（可直接 `ainvoke` / 作为子图节点）；不产出 `graph compiled`
日志（该事件由 `build_graph` 发一次）。装配期校验**唯一顺序硬约束**：若含
`AgentSpanMiddleware`，它必须是列表最后一项（最内层），否则抛 `ValueError`。

循环四件套 middleware（`src/agents/graph/middleware.py`）：`SystemMessagesMiddleware`
（施加 system 段）/ `ModelParamsMiddleware`（温度分档 + 思考开关）/
`AgentTurnBudget`（回合上限，仅主角色）/ `AgentSpanMiddleware`（观测，必须在最内层）。

### 5.3 AgentState 关键字段

`AgentState`（`src/agents/graph/state.py`）为 agent 循环状态：

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` / `kb_id` / `query` | str | 输入：会话 / 知识库 / 用户问题 |
| `messages` | `list[BaseMessage]` | 模型可见消息（`add_messages` 追加语义） |
| `_system_messages` | `list[BaseMessage]` | system 段（来源：agent 节点首轮组装后写入；范围：整轮执行，跨 regen invoke 持久；**不带 reducer**，写入即替换）；由 `SystemMessagesMiddleware` 施加到模型调用 |
| `tool_contexts` | `list[RAGContext]` | retrieve_kb 累积检索上下文（引用溯源） |
| `_verify_regenerations` | int | verify 重生成保险丝计数（上限 `MAX_VERIFY_REGENERATIONS`，正常被决策化提前终止） |
| `answer` | str | LLM 生成的完整回答 |
| `citations` | list[dict] | 去重引用列表 |
| `_history` | list[ChatMessage] | 对话历史（初始注入数据源，agent 节点入口截断） |

> 子图状态（`LoopState`，`src/agents/graph/agent_factory.py`）是 `create_agent` 的子图 schema：
> 默认值**不填充**，消费者依赖的键必须由 `agent` 节点显式 seed。

### 5.4 `RAGContext` 数据类

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | str | 分块原文 |
| `source` | str | 文件名（用户上传时的原始名称） |
| `page` | int | 所在页码（TXT 固定为 1） |
| `doc_id` | str | 文档 UUID |
| `chunk_id` | str | 分块 ID（`chunks` 表主键，格式 `{doc_id}:{chunk_index}`） |
| `score` | float | Reranker 相关性分数（越高越相关） |
| `entities` | dict | 业务实体（来自 chunk.metadata，如 `{"company": "xx", "report_period": "2024"}`） |

`to_prompt_text()` 渲染喂给生成模型的上下文文本，格式：

```
来源: {source} (第{page}页)
{实体标签}: {值} {实体标签}: {值}   ← 仅当实体非空时渲染，按 ENTITY_RENDER_ORDER 顺序
内容: {content}
```

实体锚点（如文件名/期间）随上下文一并喂给生成模型，帮助对齐事实；生产 prompt 与 RAGAS 评估的 NLI 上下文共用此格式（见 `src/rag/context.py`），评估时保证与线上生成看到完全一致的上下文。

### 5.5 fork 执行层契约

模块见 `src/agents/skills/`（`executor` / `delegate_run` / `fork_tools` / `fork_stream`）与 `src/agents/graph/skill_direct.py`。

#### `SkillExecutor.execute(record, task, run=None) -> str`

| 参数 | 语义 |
|------|------|
| `record` | 命中的 `SkillRecord`；`context=inline` 返回渲染后方法论，`context=fork` 跑子代理。`None` = 通用委派（design D6）：不加载 skill 正文、不查 `record.agent`/`model`/`allowed_tools`，`task` 原样作子代理输入，直接走 fork |
| `task` | 主 agent 委托的任务文本；fork 时作子代理初始消息 + `$ARGUMENTS`/`{task}` 占位替换值，inline 时填入 `inline_prompt` 占位；通用委派时即子代理初始消息原文 |
| `run` | 本次 fork 委派运行态；`None` = 用当前主 ctx 不隔离（inline / 无 ctx 路径）；非 `None` = 切到 `run.ctx` 子上下文执行，工具检索与引用编号落子池；`run.skill_name` 为本次委派 skill 标签（通用委派填 `SSEInteractionTexts.DELEGATE_GENERIC_LABEL`） |

返回值：inline 为渲染后方法论文本；fork 为子代理聚合纯文本（超 `DELEGATE_RESULT_LIMIT` 截断；idle/total/turn 中断返回超时文案并写 `fork_stop_reason`）。请求取消（`abort_signal` 置位）抛 `asyncio.CancelledError`，由调用方按取消路径收尾。

#### `DelegateRun` 字段（`src/agents/skills/delegate_run.py`，每次调用新建、不共享）

| 字段 | 类型 | 语义 |
|------|------|------|
| `delegate_id` | str | 本次委派短 id（事件/看板/日志贯穿） |
| `skill_name` | str | 本次委派 skill 标签（来源：定点=`record.name`、通用=`SSEInteractionTexts.DELEGATE_GENERIC_LABEL`；用于事件 skill 字段与 trace span） |
| `ctx` | `RequestContext` | 子代理独立上下文（主 `ctx.child()`；隔离引用池与计数） |
| `stop_reason` | `str \| None` | 停止原因；`None` = 正常完成或未执行，否则取 `DelegateStopReason` 值 |
| `result_text` | str | 子代理最终文本（含 idle/total/turn 中断文案） |

#### `select_fork_tools(allowed, available, executor_tools=None, tool_readonly=None) -> list`

按 design D7/D8 口径筛选可下发子代理的工具，**四步顺序即语义（勿调换）**：

1. **先减禁用集**：`FORK_FORBIDDEN_TOOLS`（`ask_user` / `delegate_task`，防交互泄漏与递归）与 `FORK_EXCLUSIVE_TOOL_PREFIXES`（`task_*`，主 agent 执行面）永不下发，**优先于白名单**（即使 `allowed` 显式声明也不放行）。
2. **再按只读性筛**：非只读工具默认不下发；仅当 `allowed` **显式声明**该工具时才放行（白名单即"写权限的例外通道"）。`tool_readonly` 表中**缺项**按非只读处理（fail-safe）。
3. **`allowed` 非空才收窄为交集**（**空 = 不收窄**，继承只读面）。
4. `executor_tools` 非空时再收窄为交集。

`tool_readonly`（`readonly_map()`）为空（工具尚未注册）→ **fail-closed（不下发任何工具）**。这与 `invocation.derive_invocation_flags` 对空表的 **fail-open** 极性**相反且都是有意为之**：同一张表的两个消费者失败代价不同——fork 侧空表时"按只读放行"会把写权限下发给子代理。名字对不上的白名单项忽略（不抛，避免笔误打断整次委派）。返回值恒不含禁用集，保持 `available` 原顺序。

#### `make_skill_direct_node(skill_registry, executor)`

入参：`skill_registry`（`SkillRegistry`，懒重载后按名解析 `SkillRecord`）、`executor`（`SkillExecutor`）。返回图节点函数：命中 fork skill 时以独立 `DelegateRun` 跑子代理，把结果写入 `answer`、把本轮材料（引用池 → `tool_contexts`、要求覆盖年份 → `verify_temporal_years`）搬进 `AgentState`，再交 verify/format；不改写主 ctx。配套 `route_entry(state)` 入口分派：`state.direct_skill` 非空 → `skill_direct`，否则 → `agent`。未装配 executor 时用 `unavailable_skill_direct` 兜底。

### 5.6 会话智能体与 `/xxx` 契约

本节为 Plan 3（change `session-agent-and-skill-invocation`）新增接口契约；语义依据见该 change 的 design D1/D19/D20/D22/D24/D25/D26。

#### `POST /chat/stream` 请求体新增 `agent: str = ""`

| 项 | 语义 |
|----|------|
| 值域 | 预设 `name`（ASCII slug，`^[A-Za-z0-9][A-Za-z0-9_-]*$`），**不是 id**；空串 = 沿用会话已绑定值 |
| 绑定 | bind-once：未绑定 + 合法 → 固化；已绑定 → 一律以绑定值为准 |
| 不一致 | 已绑定 + 传入非空且不同 → **服务端忽略传入值 + warning，无 400**（D1/D20） |
| 未注册 | 传入名未注册 → 忽略 + warning；未绑定时降级系统默认 prompt |

#### `/xxx` 前缀解析与清洗（D22/D25）

- 命令判定：只在**行首**且 `/` 后形如 ASCII slug 才按命令解析；不以 `/` 开头或 `/` 后不构成命令形态 → 按普通文本。
- 未注册：`/` 开头且形如命令但未注册 → 返回「skill 不存在 + 可用列表」（**不静默**）；`user-invocable:false` → 提示「只能由模型调用」。
- **落库与 Redis 历史保留原文**：当前轮 `query` 与历史 user 消息保留 `/name` 原文，仅在**组装 prompt 时**剥离（当前轮与历史都剥）。
- 踩坑：不要在 `add_message_async` / `save_user_async` 前改写 `query`——清洗只发生在组装 prompt 的 `_split_initial_messages`，落库必须用原文。

#### `POST /sessions/list` 新增 `agent` 字段

`data` 数组每项新增 `"agent": "finance-expert"`（会话绑定智能体名，未绑定为空串）；**`sessions/messages` 契约不变，`data` 仍为数组**。

#### 流事件 `agent_used`

载荷 `{"agent": string}`；与 `model_used` 同层回传；**语义 = 本会话绑定智能体名**（空 = 未绑定），**不含 fork 执行者**（skill 声明 `agent:` 覆盖执行者时，`agent_used` 仍是会话绑定值，D20）。

#### `GET /api/skills` / `GET /api/agents`

统一信封 `data.skills` / `data.agents`（前端按 `body.data` 解析）。skills 服务端过滤 `user-invocable: false`（只列 `user_visible()`）；读取失败 fail-open 返回空列表 + warning（不 500）。

---

## 6. 数据流全貌

### 6.1 正常回答路径（agent 循环）

```
用户提问 "2024年公司营收多少" (query)
  → POST /api/chat/stream（body: ChatStreamRequest，kb_id = 前端绑定；空 kb_id → 纯对话不检索）
    → agent 循环（图 entry 直连 agent，无路由节点）:
        1. agent: LLM 思考 → 调用 retrieve_kb
        2. tools: 检索（dense + 词法两路同源于 PostgreSQL；RRF 融合 → rerank 精排 → format_context）
        3. agent: 基于检索上下文生成回答（含引用编号 [n]）
           （检索不达标时可在循环内调用 search_web 联网兜底，产出 kind=web 引用）
           （信息不足时可在循环内调用 ask_user 追问，见 6.2）
    → agent_finalize: 提取 answer + tool_contexts
    → verify: 态 A（未绑定 KB）联网引用引导；态 B（绑定 KB）完整性 → 缺失决策化（未调 search_web/带漏 → regen；带全仍缺 → 标注直通）→ KB 溯源护栏；_needs_regenerate=True → 回 agent
    → format_node: 去重引用列表
    → SSE 事件流推送至前端:
        event: status (agent, "正在思考...")
        event: status (retrieve, "正在检索相关文档...")
        event: status (retrieve, "检索完成，正在分析...")
        event: token (逐片推送)
        event: citation (去重)
        event: model_info (模型名 + fallback 状态)
        event: done
    → 后台任务收尾（_run_with_finalize）落库 assistant 到 PostgreSQL（best-effort，失败仅记日志）
      → chat_manager.save_assistant_async()（仅写 assistant 消息，status=complete/interrupted；session + user 消息已在请求开始时写入，见上文「落库时机」）
```

### 6.2 澄清路径（ask_user 工具）

```
用户提问 "营收多少"（缺关键信息，agent 判断需澄清）
  → POST /api/chat/stream（body: ChatStreamRequest）
    → agent 循环:
        1. agent: LLM 判断信息不足 → 调用 ask_user
        2. tools: ask_user 推送问题 → SSEAskUserEvent，挂起等待（ASK_USER_TIMEOUT）
        3. 前端 composer 渲染问题表单（输入区接管），用户提交 POST /api/chat/clarify-answer
        4. 答案 resolve 挂起 Future → 作为工具结果回喂 → 同一 turn 继续
    → agent: 基于答案 + 检索上下文生成回答
    → SSE 事件流:
        event: status (agent)
        event: ask_user (问题卡片；options 来源随 ASK_USER_MODE_DSH：默认 true 用模型自带，
              不做 KB dimension 注入；false 时由 KB 实体注入真实候选)
        event: status (retrieve, 用户回答后继续检索)
        event: token / citation / model_info / done
```

- 用户答案到达即写 Redis 历史（`POST /api/chat/clarify-answer`，见 2.3.2）
- 超时/断连：ask_user Future 以超时/取消 resolve，agent 收尾；用户超时后提交返回 404
- 澄清答案在模型上下文是 ToolMessage（同 turn），在历史是 user 消息（跨 turn 上下文），两轨并存

### 6.3 文档上传路径
  → POST /api/kbs/documents/upload (multipart)
    → 返回 202 {doc_id, status: "parsing"}
    → 后台 asyncio.create_task(_process_document):
      1. parsing — 调用 parser 提取文本
      2. chunking — ParentChildChunker 分层切分 + 质量校验
      3. indexing — 写 chunks 表（`await VectorStore.add_chunks()`，embedding 列）
      4. ready — 更新 PostgreSQL 状态
    → 前端轮询 POST /api/kbs/documents/status 直至 ready/failed
```

---

## 7. 接口事故档案

| 日期 | 问题 | 根因 | 修复 |
|------|------|------|------|
| 2026-06-25 | chat 时传了 `kb_name` 而非 `kb_id` | `handle_chat` 拿到 dropdown value，`create_kb` 返回的是 name，调用方混用 | 修复参数传递链路，统一使用 `kb_id` |
| 2026-06-25 | 容器重启后 embedding 维度不匹配 | ChromaDB `get_collection()` 不返回创建时设置的 `embedding_function` | 改用 `get_or_create_collection(name, embedding_function=...)` |
| 2026-06-25 | 重置数据工具 hanging | `reset_data.py` 创建新 `AppService()` 时触发了 DashScope 网络调用 | 改用 `docker exec` 子进程模式 |
| 2026-07-03 | ChromaDB 服务器未启动导致文件上传一直卡在 processing | docker-compose 缺少 chroma 服务，HttpClient 无法连接 | 改为 PersistentClient 内嵌模式，用 volume 缓存 ONNX 模型 |
| 2026-07-03 | text-embedding-v3 免费额度用尽导致 embedding 失败 | .env 配置为 v3 模型，免费额度过期 | 切回 text-embedding-v2 |

---

## 8. CLI 接口：eval_ragas 命令行

### 8.1 `python -m src.cli.eval_ragas`

RAGAS 评估与测试集生成命令行。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--kb-id` | str | 是* | 知识库 UUID（与 `--list-kbs` 互斥） |
| `--generate` | flag | 否 | 生成测试集模式（从文档自动生成 QA 对） |
| `--size` | int | 否 | 生成测试集的 QA 对数（默认: `RAGAS_TEST_SIZE`） |
| `--model` | str | 否 | 生成测试集用的 LLM 模型名 |
| `--testset-version` | int | 否 | 指定测试集版本号（默认取最新版本） |
| `--gate` | flag | 否 | 评估后检查质量门禁指标 |
| `--output` | str | 否 | CSV 输出路径（默认: `data/reports/ragas_eval_<timestamp>.csv`） |
| `--session-id` | str | 否 | 评估用的会话 ID（默认: `ragas_eval_session`） |
| `--list-kbs` | flag | 否 | 列出所有可用知识库后退出 |

> `*` `--kb-id` 除 `--list-kbs` 模式外为必填。

使用示例：
```bash
# 生成测试集
python -m src.cli.eval_ragas --kb-id <uuid> --generate --size 20

# 评估（最新测试集）
python -m src.cli.eval_ragas --kb-id <uuid>

# 评估（指定测试集版本）
python -m src.cli.eval_ragas --kb-id <uuid> --testset-version 4

# 评估 + 质量门禁
python -m src.cli.eval_ragas --kb-id <uuid> --gate

# 列出知识库
python -m src.cli.eval_ragas --list-kbs
```

### 8.2 测试集 JSON 格式

位置：`data/ragas/testset_{kb_id}_v<N>.json`

```json
{
  "metadata": {
    "kb_id": "知识库UUID",
    "kb_name": "知识库名称",
    "doc_names": ["文档名.pdf"],
    "version": 1,
    "generated_at": "ISO 8601",
    "llm_model": "模型名",
    "testset_size": 20,
    "ragas_version": "x.y.z",
    "doc_ids": ["文档UUID"]
  },
  "samples": [
    {
      "user_input": "问题",
      "reference": "参考答案",
      "reference_contexts": ["上下文1", "上下文2"],
      "synthesizer_name": "合成器名"
    }
  ]
}
```

### 8.3 质量门禁阈值

| 指标 | 阈值 |
|------|------|
| faithfulness | 0.85 |
| answer_relevancy | 0.85 |
| context_precision | 0.80 |
| context_recall | 0.70 |

综合加权分 = 0.3×faithfulness + 0.3×context_recall + 0.2×context_precision + 0.2×answer_relevancy
综合分 ≥ 0.70 视为通过。

门禁（`--gate`）判据在阈值之外还有两条前置：**纯 KB 样本数为 0** 或
**检索覆盖率 < `EVAL_RETRIEVAL_COVERAGE_MIN`（0.5）** 直接判失败（杜绝"不检索
即跳空 context 指标"的规避）。覆盖率 = 非 `empty` 样本占比，见 §8.4。

### 8.4 评测报告产物契约（CSV / MD / stdout / 落库）

**样本三类**（`const.SAMPLE_CLASS_*`，由每题工具收集到的材料来源判定）：

| 类别 | 判据 | 是否进主分 |
|------|------|-----------|
| `pure_kb` | 全部材料来自知识库 | ✅ 进 |
| `web` | 材料中含联网块（降级联网） | ❌ 不进（但 context 指标不置空） |
| `empty` | 未检索到任何材料 | ❌ 不进，且三项 context 指标置 `NaN` 并记 `skipped` |

**CSV**（`--output`，默认 `{RAGAS_REPORT_DIR}/ragas_eval_<timestamp>.csv`）列
（**按列名消费，勿按位解析**——`df.to_csv(index=True)` 使**文件首列为 pandas 索引
列**，故 `question` 起为**第 2 列**，其后依次 `ground_truth`（第 3 列）、
`trace_id`（第 4 列））：

| 列 | 说明 |
|----|------|
| `question` / `ground_truth` | 题面与参考答案 |
| `trace_id` | 该题 trace，可按 trace 回溯 / 定位回写的分数 |
| 五个指标列 | `faithfulness` / `answer_relevancy` / `context_precision` / `context_recall` / `answer_correctness`；**CSV 保持 ragas 原生列序**（实测为 `context_precision`→`context_recall`→`faithfulness`→`answer_relevancy`→`answer_correctness`），**与 `const.RAGAS_METRIC_COLUMNS` 的顺序不同**——只有 **MD / stdout / 落库**按 `RAGAS_METRIC_COLUMNS` 排序 |
| `sample_class` | 该题样本类别（上表三类之一） |
| `skipped` | 被掩码跳过的指标名，`;` 分隔；未跳过为空串 |
| `trajectory` | Agent 轨迹诊断摘要 JSON：`{"tool_calls":[{"name","args"}, ...], "iterations":N}`，**不含工具输出正文** |
| 其余既有列 | `user_input` / `retrieved_contexts` / `response` / `reference` 为 ragas 原始字段，沿用不改 |

**MD**（与 CSV 同路径的 `.md`）：含新增列（`trace_id` / `sample_class` / `skipped` /
`trajectory`）与 CSV 同名同义，**不含** CSV 的 `ground_truth` / `user_input` /
`retrieved_contexts` / `response` / `reference`；指标列按 `RAGAS_METRIC_COLUMNS`
排序。末尾一行 `**Main scores (pure-KB):** <指标=值, ...>`，与 stdout / 门禁同口径。

**stdout 口径 = 主分（契约，勿改）**：主分 = **仅 `pure_kb` 样本**的指标均值
（`web` / `empty` 不计入），每指标**只打一行**，行形如
`  <指标名>: <数值4位小数>`，指标取 `RAGAS_METRIC_COLUMNS` 白名单（防止额外
数值列混入），行序即白名单顺序。`--gate` 模式下改由 `check_gate` 打印
`  <指标名>: <值> PASS|FAIL`（仅 `GATE_THRESHOLDS` 子集的四个指标）及覆盖率 /
纯 KB 前置判据行。`src/cli/compare_retrieval.py` 以子进程调用本脚本、按指标名
从 stdout 解析取值做 A/B 对比（`parse_metrics`），**故"口径=主分"与"每指标一行"
是跨进程契约，改动会静默改变 A/B 结论**。

**落库（`eval_report` 表）**：`_build_eval_detail` 产出的逐题 `detail` 与各指标
`avg` 均含**全部** `RAGAS_METRIC_COLUMNS`（含新增 `answer_correctness`）；表内
四列指标、`overall_score` 加权公式与 `passed` 阈值沿用 §3.6 不变 ——
`answer_correctness` **不进加权综合分**（表结构未扩展）。

**注意 DB 与 stdout 口径分叉**：落库读的是**未掩码**的 `result.to_pandas()` 全
样本，故 DB 的 `detail_json` / 四列指标 / `overall_score` 为**全量（未按样本分流）**
口径（`web` / `empty` 样本也计入），**与 CSV / MD / stdout / 门禁的「主分」（仅
`pure_kb`）不是同一个数**。二者同源于一次运行但分母不同；MD 报告在均值行下会
输出一行 Note 标注此分叉。属已知取舍（Non-Goal：本次不改 `eval_report` 表语义），
消费方勿把 `overall_score` 与 stdout 主分直接对比。

### 8.5 宿主侧运行前提

`.env` 内的 `POSTGRES_HOST=postgres` 与 `LANGFUSE_HOST=http://langfuse-web:3000`
是**容器内**服务名，宿主（WSL）侧直接跑 CLI 必须覆写为宿主可达地址：

```bash
POSTGRES_HOST=localhost LANGFUSE_HOST=http://localhost:3000 python -m src.cli.eval_ragas --kb-id <uuid>
```

`RAGAS_TESTSET_DIR` / `RAGAS_REPORT_DIR` 默认是 cwd 相对路径（`data/ragas/...`）；
在 git worktree 里 `data/` 不入库、可能不存在，需用这两个环境变量指向已有测试集 /
期望的报告输出目录。
