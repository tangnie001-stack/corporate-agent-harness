# Corporate Agent Harness

## Claude 角色
你是资深 Python 后端与 AI 应用架构师，平常习惯是用中文，文档，注释都是用中文的，负责企业智能助手 harness（聊天底座 + 可插拔工具/知识库）的设计、实现与优化；RAG 知识库是其中一个能力模块。

## 原则
1. **需求对齐** — 需求不清晰时先列出假设和不确定点，确认后再动手，不做猜测性实现
2. **最小改动** — 写达成目标的最小代码，不做未请求的抽象或预判性扩展
3. **手术刀修改** — 只动必须改的，匹配已有风格，不碰周围代码和文件
4. **验证闭环** — 明确完成标准，循环：改 → 验证通过 → 修复 → 直到达标
5. **规则对照** — 改代码前先扫描 claude.md 的「代码注释标准」和「规则」章节，确保改动符合规范

## 技术栈
Python 3.11+ / FastAPI / LangChain 1.x + LangGraph / PostgreSQL 15 + pgvector / Redis 7 / MinIO / Langfuse 2.x / Nginx / Docker Compose（可选 LiteLLM Proxy 网关）

完整技术栈与版本、各服务的容器名与端口见 `README.md`「技术栈」「部署指南」。

## 文档组织（一事一档）
每个事实只有一个归属文档，别处一律链接，不复制内容；新增内容先找归属文档，找不到再建新档。

- **归属判定**："这条规则不在上下文里就会犯错吗？" 会 → 常驻 CLAUDE.md（一句话 + 链接）；不会 → 进归属文档并在表中登记。
- **表保鲜**：新建归属文档时，必须同步登记进上表（含归属内容与何时查阅），否则视为未完成。
- **"复制"定义**：指把某文档的正文搬进另一文档；一句话的指针引用不算复制。

| 文档 | 归属内容 | 何时查阅 |
|------|---------|---------|
| docs/agents/rules.md | 架构规约：异常处理 / 响应包装 / 日志约定 / 排查规范 / 代码注释标准 | 写代码前 |
| docs/agents/logging-rules.md | 日志格式唯一归属：行模板 / 前缀主表(开放登记制) / 事件命名 / 值类型编码(token 字符集) / 级别语义 / 已知例外(retrieval_signal) | 写任何日志、登记事件/前缀前 |
| docs/agents/prompt-ownership.md | prompt 段归属规则唯一归属：五段归属表 / 段数最小性判据 / 逐条条件判据表 / base-vs-sources 判别例 / 领域 base 与预设的定位差异 | 改段模板、加条件规则、判定某条文案该住哪段前 |
| docs/agents/api_contract.md | 接口契约：参数语义、返回值格式、历史踩坑 | 改 API / 公共方法签名前；前端页面对接接口时 |
| docs/agents/code-map.md | 代码结构唯一归属：顶层目录 / 后端 `src/` 分层与模块职责 / 前端 `deploy/nginx/html` 页面与 SSE 消费 / 常见改动落点速查 | **改动代码前定位文件时必读** |
| docs/agents/knowledge-graph.md | 知识图谱（`.ua/knowledge-graph.json`）的查询配方 / 新鲜度判据 / 自动更新行为 / 已知坑 | **查代码关系、调用方、改动影响面前**（用 `/understand-chat` 或按 id grep + 1-hop 取子图，**勿整包读图**）；图谱疑似过期或需刷新时 |
| docs/agents/data-flow.md | 数据流链路 | 排查问题、理解系统流程 |
| docs/agents/troubleshooting.md | **以 trace_id 为中心的排障指导唯一归属**：trace-first 原则（缺观测就补日志）/ 环境（本地·生产）× 渠道（站点·企微·飞书）矩阵 / 本地与生产 SOP / 渠道差异 / 症状→该查哪个事件 / "查不到→加日志"落点清单 | **排查任何问题、查 Bug 前必读**；既有观测不足以定位、要决定往哪加 trace 日志时 |
| docs/agents/glossary.md | 领域词汇表：核心标识符 / 响应信封 / RAG 流水线 / RAGAS 指标等规范术语 | 术语含义不确定、写文档或命名时查阅 |
| docs/agents/memory-model.md | 记忆模型唯一归属：对话历史 vs Agent 记忆的区分 / 记忆的四个用途 / 注入形态 / 记忆边界（不得作为事实依据） | **设计或评审任何"记忆"能力前**；判断某数据该进历史还是记忆时 |
| docs/agents/selection-baseline.md | 选型对齐基线唯一归属：原则（生产验证 > 领域契合 > 可迁移深度）/ 本域基线四家 / **对齐状态表**（对齐·偏离·未做）/ 怎么用与维护 | **做技术选型、或要偏离既有做法前**；评审方案时判断"这是不是成熟做法" |
| docs/agents/chunking-issues.md | 分块问题排查与修复记录 | 遇到分块问题优先查阅 |
| docs/agents/defensive-patterns.md | 防御性模式：并发 / SSE / 精排 / 实体 / prompt / DB / 部署的防复发规则 | 写相关领域代码前 |
| docs/agents/ui-design-flow.md | UI 设计流程与产物路径：全局基线 `docs/design/MASTER.md` / 页面规格 `docs/design/pages/<name>.md` / 效果预览 `docs/design/<name>-mockup.html` | **改 UI / 新增组件前必读**；产出按此流程落 `docs/design/`，改完用 playwright-cli 验证 |
| docs/agents/dev-flow.md | 开发流程与 skill 选用：六环节（需求/bug → 完善 → 方案 → 验证 → 生成文件 → 执行）的主 skill、支撑项与必配闸门路由表；**worktree 开工前置**（③④ 工件留主分支并提交；**进入 ⑤ 生成文件前**问是否建隔离 worktree；先提交再建；判据与代价）；**派 subagent 的上下文预算与「卡死」诊断**（大 diff 与"自己去读原文"二选一、怎么判断它是死了还是慢） | **开工前**决定走哪套 skill 流程时；到评审、验证环节前查；派 subagent 前查 |
| docs/agents/cookbook.md | 操作记录协议：什么该记、怎么记；条目按协议追加 | 遇到可复用操作流程时按协议记录；需要操作步骤时查阅 |
| docs/agents/requirements_pool.md | 需求池（意向清单，非已确认需求） | 规划/排期时参考；不作为功能实现依据 |
| docs/agents/reference-projects.md | 参考资源：本地 github 镜像仓库（按域分组、评分排序、何时查阅）+ 附录「本地已安装技能」（`~/.agents/skills/`） | 写对应领域代码前、选型/排期时参考；找 agent/skill 范例时 |
| docs/agents/deploy-runbook.md | **发布/部署流程的唯一步骤来源**：运行形态（`docker-compose.image.yml` + ACR）/ 云效流水线三段与变量（`PIP_REPO_*`）/ 首次上机 / 迁移 / 冒烟 / 日常更新 / 回滚与备份 / 待定项 | **执行发布、排查部署问题前**；改流水线配置时 |
| docs/agents/wecom-sdk-facts.md | 企微官方 `aibot` SDK 的**实测行为唯一来源**：快照语义 / 认证等待与失败判据（`errcode=853000`）/ **被顶号机理（`disconnected_event`）** / ack 节奏 / Markdown 渲染能力 / `feedback` 承载字段路径 / 其它实测事实与 SDK 常量 | 动企微通道或投影层**前**；排障"连上了却收不到消息""回复失败""被顶后失联"时 |
| docs/adr/ | 架构决策记录：一条决策一文件（背景/候选/决策/理由/后果/复查条件），**只追加不可变**；与 `superpowers/specs`（设计）、`openspec/changes`（执行）的分工见目录 README | 做不可逆的技术选型/取舍**前**查有无既有决策（避免重开已决问题）；写下新决策时按 README 模板 |
| docs/research/ | 调研文档：**外部事实的冻结记录**（业界做法 / 多候选对照 / 参考项目实读 / 实测记录），命名 `YYYY-MM-DD-<主题>.md`；冻结语义、来源可核、与 adr（决策）· specs（设计）· changes（执行）· tmp（草稿）的分工见目录 README | 做选型需要外部事实时**先查有无既有调研**（避免重跑）；④「验证可行性」环节产出的调研结论落此 |

## 代码目录结构（修改代码前必读）

**完整代码结构、模块职责与「常见改动落点速查」见 `docs/agents/code-map.md`（唯一归属文档）。**

速览（改代码前仍须查 code-map.md 定位文件）：

- **后端** `src/`：分层 `api/ → services/ → agents/ + rag/ + chat/ + chunking/ + infra/`；另有 `parsers/`（文档解析）、`core/`（日志/事件）、`config/`（设置与提示词）、`middleware/`（HTTP 中间件）、`cli/`（命令行工具与防腐闸门）、`utils/`、`tools/`。入口 `src/main.py`，模型工厂 `src/models.py`，agent 装配入口 `src/agents/graph/agent_factory.py`，主从委派 `src/agents/skills/`，智能体预设 `src/agents/presets/`
- **前端** `deploy/nginx/html/`：`chat.html`（对话页，自包含）/ `index.html`（知识库管理页）/ `login.html`；Nginx 静态托管、无构建步骤，路由见 `deploy/nginx/nginx.conf`
- **运行时内容库** 顶层 `skills/<name>/SKILL.md`（业务侧管理，**随 app 镜像发布** —— `Dockerfile` COPY 进 `/app/skills`）与 `agents/<name>.md`（智能体预设内容库，同样 COPY 进镜像）；`.claude/skills/` 为开发期工具链 skill（如 openspec），两者语义不同。实现与术语见 glossary.md「技能委派」「智能体预设」
- **测试** `tests/`：与 `src/` 模块一一对应

### 层间调用规则
- ❌ `api/` 不得直接调用 `infra/` 或 `config/`（必须通过 `services/`）
- ❌ `api/chat.py` 不包含 SSE 格式化函数（在 `src/utils/sse.py`）
- ✅ `services/` 可调用 `infra/`、`rag/`、`chat/`

### 文件大小红线
- 单文件超过 400 行 → 必须拆分为模块包
- 单函数超过 80 行 → 必须拆分子函数

## 常用命令
```bash
uvicorn src.main:app --reload          # 启动（热重载）
POSTGRES_HOST=localhost pytest tests/ -v   # 测试（宿主侧必加该前缀；容器内不加，用 .env 的服务名）
ruff format . && ruff check . --fix    # 格式化 + lint 修复
docker compose up -d --build           # 部署
docker compose restart app             # 改 .py 后重启（override 挂载 src/，无需 --build；详见 cookbook.md「部署」）
docker compose up -d --force-recreate app  # 改环境配置后重创
docker compose build --no-cache app    # 改依赖后重建
```

## TraceID
- trace_id 的格式 `trace_<uuid>`，生成优先级：请求头 `X-Trace-ID` → 查询参数 `trace_id` → 自动生成。所有响应头均返回 `X-Trace-ID`（含 401/500）。
- 容器日志内 `/data/logs/`，按天轮转，trace_id 在日志行第三个 `|` 分隔段：

## 日志简则

- 事件消息英文 k=v + `[层名]` 前缀；中文仅限用户可见文案（SSEInteractionTexts）
- 分级：debug 诊断 / info 里程碑 / warning 降级可恢复 / error 单点 / exception 透传
- 检索行为信号用 `retrieval_signal:` helper，不手拼
- 完整规范见 docs/agents/logging-rules.md

## 验证
改完代码后自检以下清单：
1. **质量门禁**：`POSTGRES_HOST=localhost pytest tests/ -v` 全部通过（宿主侧必须前置该前缀，容器内不加；见 docs/agents/cookbook.md）、`ruff check .` 无错误、`pyright src/` 不引入新 error（存量多为第三方库误报，以不新增为准）、无遗留 `print()`/TODO/调试代码
2. **契约同步**：改了 API 响应结构 / 请求体 / 公共方法签名时，同步搜索并更新**两条消费链**——① `tests/` 断言（硬编码结构如 `["data"]["x"]` 常因响应包装等全局变更而失联）；② **前端 `deploy/nginx/html/` 的取值代码**（同一字段按错层级或错键名读会静默 `undefined` 或抛错，页面只表现为"点了没反应"）。字段形状以 `docs/agents/api_contract.md` 为准，改响应结构须同步该文档
3. **结构检查**：新增/修改的代码位置正确吗？api/ 是否只做参数校验和路由转发？有无违反层间调用规则的 import（如 api/ import infra/）？
4. **一事一档自检**：改文档后检查是否复制了别处内容？是则改成链接；新建归属文档是否已登记进「文档组织」表？
5. **文档登记自检**：本次改动是否产生——新术语（→ glossary.md）、新可复发缺陷类别（分块→chunking-issues.md，其他→defensive-patterns.md）、新可复用操作流程（→ cookbook.md）？有则按对应协议登记。

## 规则
- 架构规约（异常处理 / 响应包装 / 日志约定 / 排查规范）详见 docs/agents/rules.md
- API 路由 handler 必须标注请求体和返回类型（Pydantic BaseModel / StreamingResponse），详细标准见 docs/agents/rules.md
- API Key 和 Token 通过 `.env` 加载，日志中脱敏；连接串不记录到日志
- 测试 mock 外部依赖，不发起真实网络调用
- **部署形态**：生产环境单 worker，流式生成状态（任务注册表/事件缓冲）在进程内，不假设多 worker；详见 docs/agents/defensive-patterns.md
- **进入 ⑤ 生成文件前先问 worktree**：③④ 的 change 工件（`proposal.md` / `design.md` / `specs/`）留在主工作区（`dev-wsl`）并提交；**进入 ⑤（写 plan 或 tasks）之前**确认是否建隔离 worktree，不得默认就地开工；建 worktree 一律**先提交、再建**（未提交的文件在新工作区看不到）；判据与操作步骤见 docs/agents/dev-flow.md「⑤ 开工前置」
- **派 subagent 前先压小它的上下文**：大 diff 与「自己去读全部原文」二选一，>100 KB 的输入按文件拆派；它长时间没动静时，先看 `<session>/subagents/<agent-id>.jsonl` 最后一条记录的时间戳再决定是否 kill；判据与实测代价见 docs/agents/dev-flow.md「派 subagent 的上下文预算」
- 需求池文档在 docs/agents/requirements_pool.md
- **接口契约**：API 参数、返回值、历史踩坑记录详见 docs/agents/api_contract.md，修改公共方法签名**或响应结构**时，同步更新契约文档与受影响测试的断言
- **代码风格**：不用三元表达式（`a if cond else b`），写完整的 if/else 结构，保持可读性
- **显式类型检查**：类型不确定的值不用 `getattr(x, "attr", default)` 隐式兜底，用 `x.attr if x is not None else default` 或 `isinstance` 显式判断
- **硬编码集中管理**：新增的常量/文案/阈值不得散落在业务代码中，统一放入 `src/config/`，按用途分工：
  - `settings.py` — 环境变量/运行参数（需可配置的阈值、开关等，走 `os.getenv`）
  - `prompts.py` — 仅 LLM 提示词（系统指令、消息模板）
  - `const.py` — 事件/节点常量、状态映射、固定阈值；SSE 交互文案统一放 `SSEInteractionTexts` class
  - 业务模块内如已存在模块级常量，迁移归位到 `src/config/`（存量清理可单独变更，不混入功能改造）


## 代码注释标准

- 所有函数必须写 docstring，详细标准见 docs/agents/rules.md 的"代码注释标准"章节。
- 所有 dataclass 的每个字段必须加行内注释，说明来源、范围和用途。
- 结构化数据优先使用 dataclass 而非 dict，避免 `.get("key")` 散落各处。
- **写当前状态，不写变更历史**：注释/文档描述"现在的机制"，不写"之前是 X，后来改成 Y"；变更历史留给 git 提交或变更记录。
- **注释陈述契约，不写推理记录**：保留做什么、行为、失败、时序、后果；删除推理过程、测试讲解、代码复述。

## 参考项目
写对应领域代码前优先参考（评分排序与架构详情见 docs/agents/reference-projects.md）：

**选型纪律**：技术选型 SHALL 优先对齐「**生产在跑 + 领域契合**」的实现；凡有意偏离基线，SHALL 写下偏离理由与代价。基线与当前对齐状态见 docs/agents/selection-baseline.md。

- `../github/fastapi-0.141.1` — API 路由、中间件、依赖注入、异常处理
- `../github/langgraph-1.2.10` — StateGraph、流式事件、子图、Checkpoint、Human-in-the-loop
- `../github/financial_rag-main` — 同领域财税法务 RAG：多智能体、混合检索、答案校验（FaithfulnessChecker/ReflectAgent）
- `../github/claude-code` — 框架层混合编排最完整参考（coordinator 多 agent/Plan mode/hooks/子代理/压缩）
- `../github/fastapi-langgraph-agent-production-ready-template-master` — FastAPI+LangGraph 生产化：LLM 容错、限流、Eval、监控
- `../github/dify-1.16.1` — 工作流引擎、RAG 管道、插件体系
- `../github/deepseek-harness` — 事件钩子横切、工具内嵌子循环、会话事件溯源
- `../github/codex` — 编排工具化、Rust 执行/沙箱/压缩
- `../github/ragflow-0.26.4` — 文档解析、模板化分块、引用溯源
- `../github/Qwen-Agent` — Python 薄层多智能体（react_chat/router/group_chat）
- `../github/awesome-llm-apps-main` — AI Agent/RAG 模板集（理念参考）
- `../github/full-stack-fastapi-template-0.10.0` — 全栈模板（含前端，本项目后端为主）
- `../github/fastapi-best-architecture-1.15.0` — 社区最佳实践（与本项目 rules.md 重叠）
