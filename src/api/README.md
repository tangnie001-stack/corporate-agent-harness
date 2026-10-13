# API 路由层

提供 harness 的 HTTP 接口，基于 FastAPI 实现，支持 SSE 流式推送。

## 文件说明

| 文件 | 职责 |
|---|---|
| `__init__.py` | 路由注册枢纽；`src/main.py` 统一 `include_router(prefix="/api")` |
| `model/` | Pydantic 请求体（`request.py`）/ 响应体（`response.py`） |
| `schema.py` | 路由共用 schema |
| `dependencies.py` | 依赖注入（服务单例 / 当前用户） |
| `health.py` | 健康检查 `GET /api/health`、`POST /api/config` |
| `auth.py` | 认证：`POST /api/auth/login` / `POST /api/auth/verify` / `POST /api/auth/logout` / `POST /api/auth/anonymous` |
| `knowledge_base.py` | 知识库 CRUD：`POST /api/kbs/list`、`POST /api/kbs`、`POST /api/kbs/delete`、`POST /api/kbs/domain` |
| `documents.py` | 文档管理：`POST /api/kbs/documents/list`、`POST /api/kbs/documents/upload`、`POST /api/kbs/documents/status`、`POST /api/kbs/documents/chunks`、`POST /api/kbs/documents/delete` |
| `kb_eval.py` | 最近一次评估报告 `POST /api/kbs/eval/latest` |
| `chat.py` | 流式问答 `POST /api/chat/stream`（SSE），推送 status / token / citation / done 等事件 |
| `clarify.py` | 澄清应答 `POST /api/chat/clarify-answer` |
| `sessions.py` | 会话：`POST /api/sessions/list`、`POST /api/sessions/messages`、`POST /api/sessions/delete`、`POST /api/sessions/cancel`、`GET /api/sessions/events`、`GET /api/sessions/tasks`、`GET /api/sessions/task-status` |
| `feedback.py` | 回答反馈 `POST /api/feedback` |
| `llm_test.py` | 连通性自检 `POST /api/llm/test` |
| `ragas_generate.py` | 评估测试集生成 `POST /api/ragas/generate` |
| `capabilities.py` | 能力清单 `GET /api/skills`、`GET /api/agents` |
| `wecom.py` | 企微智能机器人回调 `GET /api/wecom/callback`（URL 验证）、`POST /api/wecom/callback`（收消息/事件）；受 `WECOM_BOT_ENABLED` 控制，关闭时 404；且仅在 `WECOM_BOT_MODE=callback` 时可用，`long_connection` 模式下 404（长连接不经 HTTP 回调） |
| `alerts.py` | 告警接入 `POST /api/alerts/webhook/{token}`（Grafana contact point 直连；token 在**路径**里，未配置或不匹配 `401`，路径缺 token 段 `404`） |

## 数据流

```
请求 → FastAPI router → AppService（编排层）→ infra（DB/LLM/Vector）→ SSE 响应
```

> SSE 序列化**不在本层**：`src/utils/sse.py`（本层只调 `to_sse`，不得内联格式化）。
> 分层调用规则（`api/` 不得直调 `infra/` / `config/`）见 `CLAUDE.md`「层间调用规则」。
