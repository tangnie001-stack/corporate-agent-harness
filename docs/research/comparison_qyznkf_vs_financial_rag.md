# qyznkf vs financial_rag 对比分析

生成日期：2026-07-01

## 1. 前端

| 维度 | qyznkf | financial_rag |
|------|--------|---------------|
| 框架 | 原生 HTML + CDN Tailwind + marked.js | Vue 3 + TypeScript + Vite + Element Plus |
| 构建 | 无构建步骤 | Vite 正式构建流程 |
| UI 组件 | 手写 CSS/JS | Element Plus 组件库 + TailwindCSS |
| 状态管理 | 全局变量 | Pinia |
| 路由 | 无（页面跳转） | Vue Router |
| API 层 | 手写 fetch 封装 | Axios + 30+ 模块化 API client |
| 类型安全 | 无 | 全量 TypeScript 类型定义 |
| i18n | 无 | 完整国际化方案 |
| 页面数 | 2 页（KB 管理 + 聊天） | 多视图（聊天/文档/税务/策略/Agent/监控仪表盘等） |

## 2. 后端架构

| 维度 | qyznkf | financial_rag |
|------|--------|---------------|
| API 端点 | ~10 个 | 30+ 个 |
| 认证鉴权 | 无 | JWT auth + RBAC + 租户隔离 |
| 数据库迁移 | 手动 SQL 脚本 | Alembic |
| 中间件 | 无 | CORS/日志/限流/租户（FastAPI middleware） |
| 服务分层 | AppService 直连 | api → service → repository → ORM |
| API 请求校验 | 手写校验（dict 取值判断） | Pydantic schema 自动校验（422 + 错误信息） |
| 接口文档 | 无 | FastAPI 自动生成 OpenAPI（`/docs`） |
| 数据库 ORM | PyMySQL 手写 SQL | SQLAlchemy 2.0 + ORM 模型 |

## 3. AI / Agent 架构

| 维度 | qyznkf | financial_rag |
|------|--------|---------------|
| Agent 编排 | 无（单链 RAG） | LangGraph agentic RAG |
| 多 Agent 系统 | 无 | 完整多 Agent 编排（路由/流水线） |
| Agent-to-Agent 协议 | 无 | A2A protocol 实现 |
| MCP 集成 | 无 | MCP client + server 双端支持 |
| Agent 框架 | 无 | 抽象框架（组件/LLM/路由/token/工具） |
| 知识图谱 | 无 | Neo4j 知识图谱（实体/关系/共指消解） |
| 记忆系统 | 简单 chat history | 专用 memory 模块 |
| Prompt 管理 | **Langfuse 动态管理（版本管理 + A/B 测试）** → 本地兜底 | 按类别文件目录化管理 |
| 工具系统 | 无 | 大量内置工具（财务/税务/法律等） |

## 4. 安全

| 维度 | qyznkf | financial_rag |
|------|--------|---------------|
| 认证 | 无 | JWT + OAuth2 |
| 权限控制 | 无 | RBAC |
| 租户隔离 | 无 | 完整多租户 |
| 速率限制 | 无 | 有 |
| API Key 管理 | 无 | 有 |

## 5. 文档处理

| 维度 | qyznkf | financial_rag |
|------|--------|---------------|
| 解析器 | 3 种 | 多种（financial/legal/tax/adaptive 等） |
| 分块策略 | 单一 RecursiveCharacter | 多种专用 chunker |
| OCR | 无 | 集成 |
| 表格处理 | 基础 | 更完善的表格提取 |

## 6. qyznkf 当前技术债务

1. 无类型检查 — 全 Python 无 type hints，前端无 TypeScript
2. 手写 SQL — PyMySQL + 字符串拼接，有 SQL 注入风险
3. CORS 全开 — `allow_origins=["*"]`
4. 无数据库迁移 — schema 变更靠手动改 SQL 文件
5. 前端无构建 — CDN 加载依赖，无法 tree-shaking
6. 无认证 — 部署后任何人都可调用 API

## 7. 成本与可靠性

| 维度 | qyznkf | financial_rag |
|------|--------|---------------|
| 限流（成本控制） | 无 | 滑动窗口/令牌桶/固定窗口，含 API Key/User/Tenant/IP 多级 |
| 熔断（服务保护） | 无 | 通用熔断器 + Specialist Agent 熔断（3次熔断5分钟） |
| 降级（兜底策略） | Langfuse 不可用时兜底本地 prompt | 熔断期返回降级响应、fail-open 限流策略 |
| 成本可观测 | 无 | 有 Token 用量追踪、Agent 调用统计 |

### 关键区别

- **限流**：qyznkf 无需外部调用，但 LLM API 调用有成本，加限流能防止误操作烧钱
- **熔断**：LLM API 可能超时或不可用，熔断比等待超时更高效、用户体验更好
- **降级**：qyznkf 在 prompt 管理器上有兜底，但整体缺乏系统性降级策略

## 8. 改善优先级建议

按投入产出比排序：

1. **认证与安全** — API Key + 速率限制（结合熔断降级，从成本+可靠性角度更有价值）
2. **Agent 化 RAG** — 引入 LangGraph / MCP，从单链升级为 Agentic RAG
3. **数据库完善** — SQLAlchemy + Alembic
4. **知识图谱** — 引入 Neo4j 处理实体关系
