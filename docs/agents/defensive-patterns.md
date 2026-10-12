# 防御性模式

> 真实发生/差点发生的缺陷类别，写成防复发规则。写并发、进程级注册表、SSE 流式、精排、实体、prompt、接口契约、前端、数据库、部署、开发期闸门相关代码前先读。

## 并发

### 共享资源并发访问必须串行化

**现象**：并行检索时多个线程/协程同时操作同一共享资源，而该资源本身不可并发安全，导致崩溃或数据错乱。

**规则**：对不可并发安全的共享资源，用锁（`threading.Lock` / `asyncio.Lock`）串行化访问；新增共享客户端时先判断是否线程安全。向量存储现为 PostgreSQL 连接池（并发安全），其 IO 方法为 `async`，并发检索直接 `asyncio.gather` 调用即可，回归用例在 `tests/infra/db/test_vector_store.py`。

### 每请求上下文的活跃状态必须按调用分槽

**现象**：一轮内多个 `delegate_task` 被 `asyncio.gather` 并发调度，若把 `delegate_id` / 停止原因写在共享 `RequestContext` 的单值字段上，多次委派互相覆盖，导致 SSE 增量、任务看板与终态判定串号。

**规则**：每次调用一个独立实例（`DelegateRun`，`src/agents/skills/delegate_run.py`）并由调用方逐层传递；若必须落到请求上下文，落到**该次调用的独立子 ctx**（`RequestContext.child()`），不挂共享单值字段。

## 进程级注册表

### 进程级注册表不得在构造期快照

**现象**：`SkillLoader` 在构造时读取并缓存 `readonly_map()`，而生产环境构造 loader 早于工具注册，快照到的只读表为空 → 双轴调用控制的 fail-safe 永不触发（Plan 1 最终评审发现）。

**规则**：需要进程级事实（工具只读表、注册表等）时，在**解析/使用期**惰性读取当前状态（`src/agents/skills/loader.py` 每次解析调用 `readonly_map()`），不在构造期缓存；构造器只保存可注入的覆盖值。

## SSE 流式

### 流事件必须按节点元数据匹配，不用顺序假设

**现象**：流式输出 token 丢失，因为 SSE 事件解析匹配不到正确的生成节点。

**规则**：SSE 事件解析通过 `metadata.langgraph_node` 精确匹配节点，不依赖事件顺序或索引。新增节点/事件类型时，同步检查流解析逻辑。

### 流式过程行的 DOM 挂载点必须先于气泡锚点固定

**现象**：思考块/状态行渲染到 AI 回答气泡的下方（上下错位），或多轮对话后各轮思考块跨轮堆叠无法区分归属（2026-09-08 chat.html 排版缺陷）。

**规则**：流式事件驱动的 DOM 追加必须挂到**本轮过程容器**（创建于任何气泡之前，惰性创建 + 每轮复位指针），禁止直接挂容器根级；禁止在流中途提前创建气泡等"未来锚点"（锚点创建后，容器级追加会全部落到锚点之后）。同轮的过程行生命周期保持一致——要么都随容器持久保留，要么都清理，不做单类型清理。

### 投影层保活不得对上游在途取值施加超时取消
- **症状**：长静默期（澄清等待 / 出网调用）收不到任何事件；想靠 `asyncio.wait_for(anext(gen), t)` 加保活，结果超时点把上游生成器终结，后续取值抛 `StopAsyncIteration` —— 恰在需要保活时把长流静默截断。
- **根因**：`_subscribe_events` 是异步生成器，体内不捕 `CancelledError`；取消其 `anext` 会关掉生成器。
- **正确形态**：事件流以 `asyncio.Queue` 暴露（后台 pump 入队），消费侧只对 `queue.get()` 施加超时。
- **落点**：`src/channels/wecom/presenter.py`（`run` / `_pump` / `_consume`）。

## 精排（rerank）

### 复杂合并结果必须 cap 到 TOP_K_RERANK

**现象**：复杂查询的多次检索结果合并后超出 `TOP_K_RERANK`，输出被截断或逻辑异常。

**规则**：任何 rerank 合并逻辑最终必须裁剪到 `TOP_K_RERANK` 上限。新增合并策略时检查上限约束。

### 降级路径的词法专属 chunk 可能被截断出窗口（已接受）

**现象**：父块级去重按 `.score` 降序排序。两条降级路径（精排调用失败、精排超时）下，词法专属 chunk 的 `distance is None`，回退分固定为 `0.0`，排序落在所有 dense chunk 之后，可能被截断出窗口；改前它保留的是 RRF 位次。

**规则**：这是**接受而非修复**的边界。降级回退**有意**只用 `1 - distance` 打分（`scored=fallback` 刻意不混入精排分分布）；再掺入 `lexical_score` 会引入第二个未归一化的分数域。

## 词法检索（lexical）

### 分词口径漂移（写入与查询两侧不一致）

**形态**：写入侧（`chunks.content_seg`）与查询侧（tsquery 词元）用了不同的分词器、
不同的词典，或同一分词器的不同版本。**不报错，只静默降召回**；最隐蔽的一种是
**版本漂移** —— 分词结果随 `tsv` 生成列固化落库，升级 jieba 后存量与新的查询侧不一致，
而"同一进程内两个函数比较"的守卫测试抓不到它。

**防复发**：
1. 两侧只调 `tokenizer.tokenize()` 一个函数（守卫测试断言两侧词元逐字相等）；
2. `jieba` pin 精确版本；
3. 变更分词器配置/版本后**必须**跑 `scripts/rewrite_content_seg.py --apply`，
   并用 `--check`（退出码非 0 即存量已过期）作为验收动作；
4. 用户原文不得直接进 `to_tsquery` —— 含空格会抛错、`a:` 会被静默吞字符；
   查询串一律经 `lexical_query.build_lexical_query` 构造；
5. 词元全被滤掉时兜底走**正文子串**（不是"回退为不过滤"——写入侧没有单字 lexeme）。

## 实体提取

### 入库前过滤 null / 非标量实体值

**现象**：null 或 list/dict 等非标量实体值写入分块 metadata（`chunks.metadata` jsonb 列）时，写入或读取失败。

**规则**：实体值写入分块 metadata（`chunks.metadata` jsonb 列）前，过滤 null 与非标量值；抽取侧也要容错（抽取失败时优雅降级，不阻塞入库）。

### 标题/实体解析必须有 fallback 链

**现象**：文件名中的拉丁名无法匹配中文公司名，导致标题归属错误。

**规则**：标题/实体解析建立 fallback 链（如 heading → 文件名），单一路径失败时回退，不静默产出空值。

## Prompt

### 日期注入必须用北京时区

**现象**：提示词注入的日期用了服务器默认时区，与业务语义不符。

**规则**：prompt 中的日期/时间注入统一使用 `Asia/Shanghai` 时区。

### 无条件引用条件注册的工具

**症状**：prompt 里出现"调用 `search_web` 联网搜索"这类句子，但本轮该工具**并未注册**
（`settings.WEB_SEARCH_ENABLED=false`，或 `delegate_task` 因 skill 库为空而为 `None`）。
模型照做 → 报错或被拒 → 白耗一轮。

**根因**：文案的"挂载点"是**无条件**的，而工具是**条件注册**的。

**规则**：凡引用某个工具的规则，其判据 SHALL 是"该工具已注册"（适用域另计）。
判据住代码、不由 YAML 声明；逐条对照表见 `docs/agents/prompt-ownership.md` §3。

**历史实例**：
1. `KB_UNBOUND_SYSTEM_PROMPT` 无条件提及 `search_web`（后者受 `settings.WEB_SEARCH_ENABLED`
   条件注册，`rag_tools.py:240`）。
2. `tools-delegate-guidance` 无条件提及 `delegate_task`（skill 库为空时为 `None`）。

### 渲染器切换残留格式转义

**现象**：模板换了渲染器，旧渲染器时代的 `{{` / `}}` 转义没跟着去掉。`str.format`
会把双花括号还原成单花括号，而 `loader.render` **不做还原** —— JSON 示例于是原样输出
`{{...}}`，下游 `json.loads` 失败又被 `except` 吞掉、静默回退。

**规则**：切换渲染器 SHALL 在同一提交内去掉该渲染器特有的转义；消费点的 `except`
兜底 SHALL NOT 让解析失败无声通过。`loader.render` 的调用方 SHALL 配一条"渲染产物
无残留转义"的守卫测试。

**历史实例**：classifier / rewrite / entity 三个远端模板各有一条（提交 `0aab2e9`、`005c572`）。

## agent 循环与 LangChain 中间件

### 自定义 middleware 必须声明 state_schema，否则自定义键被静默丢弃

**现象**：给 `create_agent` 写自定义 `AgentMiddleware` 时，**只有声明在 `state_schema` 里的键才会进入图状态**。未声明的键读写都不报错、直接被丢——写进去的值读回来就是缺失的（`state.get("_n", 0)` 恒为 0），据此判定的护栏**静默失效**，而日志、异常、返回结构全都正常。

2026-09-28 调研 `one-loop-two-roles` 时**连踩四次**，其中一次据此得出「`after_model` 的 `jump_to: "end"` 不生效」的**错误结论**——真因是计数键未声明、判定条件恒为假，与 API 行为无关。

**规则**：自定义 middleware 承载任何计数或携带数据时——(1) MUST 定义 `state_schema`（`AgentState` 子类），把**所有**自定义键声明进去并给全默认值；(2) MUST 有断言覆盖该键（改键名时测试必须失败）；(3) 排查「middleware 逻辑不生效」时，**先确认键在 schema 内**，再怀疑 API 行为。

### middleware 实例跨请求共享，per-request 状态不得存在实例属性上

**现象**：`create_agent` 的 middleware 实例随图**构造一次**、跨请求复用（生产单 worker 下即全进程共用）。把本轮计数、本轮参数等存在实例属性上，会在并发请求间**串号**，且不报错、日志上看不出来。

**规则**：middleware 实例只保存**进程级常量**（上限值、模型名、标志位等）；per-request 数据一律经 `state_schema` 或请求上下文（ContextVar）传递。

### per-call `extra_body` 会整体覆盖构造时的 `extra_body`，别把参数配在 `LLM_KWARGS`

**现象**：langchain-openai 在 `_get_request_payload` 里对 `extra_body` 做**浅合并**——per-call 传入的 `extra_body` 会**整体覆盖**模型构造时配置的那一份。若把某个 `extra_body` 参数配在 `LLM_KWARGS`（构造期），而调用点又按需传了 `extra_body`（例如 `{"enable_thinking": …}`），构造期那份会**静默丢失**：配了没效果，且没有任何告警。

**规则**：`extra_body` 只允许**在一个地方**给全——或构造期、或 per-call，不要两处各配一半。调用点/中间件需要 per-call 传 `extra_body` 时，MUST 在原位保留这条约束的注释（该注释的承载代码可能随重构迁移，注释必须跟着走）。判定档位（如按会话是否绑 KB）时，判据 MUST 取**请求级上下文**而非图状态——图状态里的同类值可能未随调用链传入而**静默为空**，导致档位反判（`one-loop-two-roles` D15 即此坑）。

## 接口契约

### 前端消费的响应形状必须以契约文档为准，且形状漂移只能降级不能抛错

**现象**：知识库管理页文档行的「详情」按钮点了没反应 —— `showEvalModal` 按**扁平**键读（`data.table_score`、`data.granularity_cv.toFixed(2)`），而后端 `eval_detail` 是**嵌套**结构（`structure_integrity.table.score`、`sbr.broken_boundaries[]`，且 `granularity_cv` 是对象不是数字）。`data.granularity_cv` 命中的是模块对象，`.toFixed` 抛 `TypeError`；该行位于 `sections` 构建期，**早于** `body.innerHTML` 与 `classList.add('show')` ⇒ 弹窗永不打开，且除控制台外无任何迹象（列表行上的 `分块评分 0.90 ✓` 反而正常，因为 `overall_score` 恰好在顶层）。缺陷存活 71 天（2026-07-12 引入 → 2026-09-22 发现）；同源的二级弹窗 `showEvalBroken` 三个分支全错，一级弹窗的断裂链接还传错键（传 `sec.key='structure'`，而查询表的键是 `'table'`）。

**规则**：前端读任何接口返回值前，形状以 `docs/agents/api_contract.md` 对应小节为准，**不靠猜字段名**（尤其别把后端局部变量名当 JSON 键）；改动消费同一字段的前端代码后，按 CLAUDE.md「契约同步」同时过 `tests/` 与 `deploy/nginx/html/` 两条消费链。另外对"取到的值再调数值方法"处加类型守卫（`typeof v === 'number' ? v.toFixed(2) : '—'`）—— **形状漂移只该降级成占位符，不该让整个组件不渲染**。

**历史实例**：`deploy/nginx/html/index.html` 的 `showEvalModal` / `showEvalBroken`（提交 `e503375` 引入；修复后同一契约形状见 `api_contract.md` §2.2.1）。

## 前端（浏览器 API）

### 明文 HTTP 部署下不得裸用安全上下文专属 API

**现象**：demo 部署到 `http://8.133.218.201/` 后，登录页点「登录」报 `crypto.randomUUID is not a function`。根因：`crypto.randomUUID` 与 `crypto.subtle` / `navigator.clipboard` / `navigator.serviceWorker` / `navigator.share` / `Notification` / `geolocation` 一样，**只在安全上下文（HTTPS / `http://localhost` / `file://`）暴露**；走公网明文 HTTP 时该属性是 `undefined`，裸调用即 `TypeError`。三处生成 trace id 的代码里，`deploy/nginx/html/login.html` 是唯一没写兜底的（`js/api.js` 的 `generateTraceId`、`chat.html` 都有 `?.` 或三元兜底），于是**只有登录页崩，别处正常**。

**为什么本地恒绿**：本地冒烟一律访问 `http://localhost`，而 **localhost 本身算安全上下文** ⇒ 这一类缺陷在本地永远不复现，只有换成公网 IP 明文访问才现形。

**规则**：
1. 前端调用安全上下文专属 API 时 MUST 带能力检测 + 兜底，禁止裸调用；同一能力的兜底表达式全站保持一致 —— canonical 实现是 `js/api.js::generateTraceId`，内联处（`chat.html`、`login.html`）照抄其形态。
2. 目标环境是明文 HTTP 时，冒烟/验收 MUST 走**公网地址**或模拟非安全上下文，不能只测 `localhost`，否则整类缺陷恒绿。
3. 长期明文 HTTP 部署应评估上 TLS；未上 TLS 前，前端按「永不假设安全上下文」编写。

**历史实例**：2026-10-02 demo 部署（`http://8.133.218.201/`）。`login.html` 的 `X-Trace-ID` 原为 `crypto.randomUUID()` 裸调用；修复为与另两处同形：`'trace_'+(crypto.randomUUID?crypto.randomUUID():Date.now()+'_'+Math.random().toString(36).slice(2,10))`。

## 数据库

### UUID 列必须用 String(36)

**现象**：UUID（36 字符）存入 `String(32)` 列被截断，产生脏数据。

**规则**：主键/UUID 外键列用 `String(36)`（`src/infra/db/base.py`）。新增模型时遵循此约定。

### 派生写操作跨事务

**现象**：一次业务动作要写多张表（写分块 + 更新文档状态、删分块 + 软删文档/知识库），若各表各自提交，进程死在中间就产生**孤儿**：分块已删而状态已变，或分块未写而文档已 ready。更隐蔽的一种是删除路径**吞掉异常** —— 分块删除失败只 `warning` 后继续软删文档/知识库，孤儿永久存在且不可观测（历史缺陷：KB 删除的 `except Exception: logger.warning`）。

**规则**：跨表写用 `session_scope` / `Repo.transaction()` 打开**唯一**事务，参与者方法传 `session=`（传入时不提交，契约见 api_contract.md §4），失败即整体回滚，**且不得吞异常** —— 删除失败必须向上抛，由调用方决定呈现。实现点：`_write_chunks_and_mark_ready`、`DocumentService.delete_document`、`AppService.delete_knowledge_base`。

## 存储迁移

### 派生副本与权威来源分离时，读取侧必须显式重建契约键

**现象**：字段从 blob/jsonb 升为独立列，或从「每库一集合」改为「单表 + 归属列」后，读取侧若直接把存储行原样返回，依赖契约键的下游会**静默失效** —— 不报错，只是取空或丢键。P2 把 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page` 从 metadata 升为 `chunks` 列即属此类：`_dedup_by_parent` 读 `doc_id`/`parent_content`、引用渲染读 `source`/`page`、实体透传读自定义键，列里没有副本就断链（`rag/retrieval.py`、`agents/tools/rag_tools.py`）。

**规则**：跨存储 / 跨表示的字段搬迁必须有一个**唯一**的映射函数：写入侧用它把契约键从源 metadata 剔除（`split_metadata()`），读取侧用它把权威列值 + jsonb 平铺合并回对外契约对象（`src/infra/db/vector_store/mapping.py::row_to_chunk_result()`，冲突以列为准）。该函数必须有单测钉住全部契约键与 jsonb 自定义键的原样透传；禁止各读取路径各自拼装 metadata。

### 存储替换的验收必须与算法变更分离

**现象**：把「换存储」和「改算法/参数」混在一次变更里，验收时无法区分行为变化来自哪一侧 —— 「新实现自己跑得通」不能证明替换前后结果一致。

**规则**：存储替换**不应改变行为**，判据必须是**差分**：同一份语料 + 同一批固定查询，比对替换前后 top-k 重合率（P2 用 ≥0.9）；替换期间不得顺带改融合权重、top-k 口径或分词，两侧共用同一 embedder 以隔离存储变量；查询集与脚本落盘可复现。⚠ 差分闸门要同时说明**灵敏度上限** —— P2 的 k=30 / 阈值 0.9 取均值口径，允许单条约 3 个位次偏差、最多容忍 2 条查询完全错位，且单位化向量下 cosine / L2 / 内积排序等价 → 不能据此宣称「迁移零风险」。

## 依赖

### 删依赖前先查它是不是别人的传递提供者

**现象**：P4 退役 Chroma 时删除 `chromadb` 依赖，而 `bcrypt` 此前**仅由 `chromadb` 传递提供**、并未显式声明，但 `src/utils/auth_crypto.py` 直接 `import bcrypt` —— 依赖一删，应用启动即 `ModuleNotFoundError` crash-loop。整个仓库只有 `pyproject.toml:43-45` 注释记下了这条链路，文档与依赖列表本身看不出风险。

**规则**：删除一个依赖前，先确认本仓库代码或其它依赖没有在隐式消费它传递提供的包 —— 全仓检索对它的 `import`、并核对被删依赖的依赖树；被直接 `import` 的传递依赖必须提升为**显式运行时依赖**并加注释说明来源（`pyproject.toml:43-45` 的 `bcrypt` 即此形态，标注不可移除），删除后重建镜像并跑一次启动自检。

## 部署

### 进程退出必须优雅停机

**现象**：服务重启时正在处理的请求被强行中断，连接未释放。

**规则**：uvicorn 配置 graceful shutdown 超时，退出前等待在途请求完成。

### 流式生成状态必须留在进程内（单 worker 部署）

**现象**：prod 曾配 `--workers 4`。若流式生成依赖进程内状态（任务注册表、事件缓冲），多 worker 下续接/取消请求可能落到别的进程，导致状态读不到、任务找不到。

**规则**：生产环境以单 worker 部署（uvicorn 不配 `--workers`）。流式生成的任务注册表、事件缓冲等状态留在进程内，不引入 Redis 共享/多 worker 支持。新增流式或部署相关设计时遵循此约束；确需横向扩展时先评估，不得默认多 worker。

### 单 worker 下的阻塞调用

**现象**：生产单 worker（见上一条），任何同步阻塞调用都会冻住整个进程 —— 所有请求与 SSE 一起卡住。两类已识别的阻塞面：① 同步外网调用（DashScope embedding / rerank、解析）；② CPU 密集的 jieba 分词（首次加载词典约 0.5–1 s）。二者在 `PgVectorStore.add_chunks` 等处已用 `asyncio.to_thread` offload 到线程池。

**规则**：阻塞调用不得放在事件循环线程上 —— 同步外网 / CPU 工作一律走 `asyncio.to_thread`。**事务内不得包含外网调用**：embedding 等慢调用必须在**进入事务之前**算好，事务只接收已算好的向量（`design.md` D7）—— 否则事务长占连接与锁，单 worker 下把连接池拖垮。

### 重建上游容器后必须让 nginx 重解析

**现象**：`deploy/nginx/nginx.conf` 用 `proxy_pass http://app:8000;`（**主机名**，非变量）—— nginx 在**配置加载时解析一次并缓存在 master 进程里**。`app` 容器重建后会拿到**新 IP**，nginx 仍往旧 IP 发 ⇒ `connect() failed (111: Connection refused)` ⇒ **502**，而且应用日志里**一条都没有**（请求根本没到应用）。

2026-10-12 实测：nginx 缓存 `172.18.0.3`，`app` 重建后为 `172.18.0.7`，`POST /api/auth/verify` / `POST /api/auth/login` 全部 502。⚠️ **同一天再次重建却复用了同一 IP**（未出现 502）⇒ 这个坑**间歇出现**，别因为"上次重建没事"就忽略。

**规则**：任何重建 / 替换 `app` 容器的操作之后，都要让 nginx 重新加载配置：
```bash
docker compose exec -T nginx nginx -s reload     # 或：docker compose restart nginx
```

- **走 `deploy.sh` 的路径已内置**：`scripts/deploy/deploy.sh` 在 `compose up -d` 之后固定执行 `nginx -s reload`（`6/8 启动服务` 段），所以流水线发布与 `deploy.sh start` 都有防护。
- **手工 `docker compose up -d --force-recreate app` 不在覆盖范围内** —— 这正是本地/临时排障的常见形态，必须自己补这一句。
- **未实测**：`nginx -s reload` 是否足以重解析静态 upstream（按 nginx 语义应当可以 —— reload 会让 master 重读配置并重解析）。当天实测到能修的是 `docker compose restart nginx`。

### compose bind mount 不写绝对路径

**现象**：`docker-compose.override.yml` 曾把 6 条开发期挂载写成主工作区绝对路径（`/mnt/.../corporate_rag/{src,tests,skills,agents}` 与 `deploy/nginx/{nginx.conf,html}`）。危害两层，都是静默的：① 仓库 clone / 挪到别的路径后 bind 源不存在（Docker 可能报错，也可能静默建成**空目录**），容器看不到 `src/` 与 nginx 内容；② 在 `git worktree` 里跑 compose 时挂的仍是**主工作区**的文件 —— worktree 的改动静默失效，且看起来像生效了。

**规则**：compose 的 bind mount 一律写**相对路径**（`./src:/app/src`）。相对 bind 按「compose 文件所在目录」解析，**与调用时的 cwd 无关**，因此主工作区与各 worktree 各自解析到自己。主 compose（`./data/ragas`、`./deploy/postgres/init`）原本就是此写法，override 曾偏离。

## 开发期闸门（pre-commit）

### 闸门的失败面要按"输入类"界定，不能只看当前仓库可不可达

**现象**：`src/cli/check_docs.py` 的代码快照只 `except OSError`，**不捕获 `UnicodeDecodeError`**（它是 `ValueError` 子类）。而该钩子 `doc anti-rot (all docs)` 是 `always_run` 且无 `files` 过滤 —— **每次提交都全量跑**。因此只要 `src/` 下出现**一个**非 UTF-8 的 `.py`，闸门就会抛异常 ⇒ **全员的每一次提交都被拦死**。本仓当前无非 UTF-8 文件，故不可达 —— 但"不可达"靠的是输入集恰好干净，不是代码保证。

**规则**：给"每次提交都跑"的闸门写文件读取时，异常覆盖面要按**输入类**界定，而不是按"当前仓库里有没有这种文件"。① 遍历全树时，单文件失败必须**跳过该文件**，不能崩掉整轮（`OSError` 之外还要想到 `UnicodeDecodeError` 这类解码失败）；② **不得把"捕获范围保留、但触发面被放大"的改动说成"行为不变"** —— 触发面本身就是行为的一部分（"命中即短路"可能永远读不到坏文件，"必读全树"则每次都会撞上）。

**历史实例**：2026-09-23 change `check-docs-symbol-lookup-perf`。把"逐符号重读（命中即短路）"换成"单次快照（必读全树）"时，捕获范围逐字保留，但**触发面被扩大**（存在坏文件时每次查询都抛）。当时先在 `design.md` D6 写成"保留现状"，被代码评审指出不准确后才改准。待修项登记在 `requirements_pool.md` D-08。

### 文档防腐的扫描范围必须覆盖对外入口文档

**现象**：`src/cli/check_docs.py` 原先只扫 `docs/agents/*.md`（非递归），根 `README.md` / `CLAUDE.md` **不在任何闸门内**。于是存储栈换了三代（关系库、向量库、词法检索全换）、agent 循环换到 `create_agent`、Langfuse 从 v3 降到 v2，而 README 的架构图、技术栈表、服务表、项目结构树仍写着旧世界 —— 漂移潜行了约两个月才被人眼发现。更糟的是：**即便把根文档加进扫描范围，原有四类锚点也拦不住这类漂移** —— 「技术栈换了但文档没换」既不是 `src/` 路径、不是 `METHOD /api/...`、也不符反引号符号的命名形态（`MySQL` 在 Python 里当然"不存在"，但那是必然的，不构成信号）。

**规则**：
1. 新增/改动**对外入口文档**（根 README、CLAUDE.md、子系统 README）时，登记进 `pyproject.toml` 的 `[tool.doc_anchors].extra_docs`，否则它永远不被校验。
2. 退役 / 改名的技术名加进 `banned_terms`（大小写不敏感子串，命中即 error），下次换栈时旧名再也进不来。
3. **台账类文档**（术语表 / 接口契约的历史踩坑表 / 操作记录 / 缺陷档案）的职责就是记"曾经用什么" —— 登记进 `banned_term_exempt_docs` **整篇豁免**，不要为了过闸门删掉溯源。
4. 现行机制文档里偶有一处必须保留的历史注记（如"原历史包名已改名"），在该行加行内标记 `doc-anchors-allow` 单行豁免，不要把整篇移出保护。

**别踩的坑**：
- 禁用词是**子串**匹配：列 `Chroma` 即覆盖 ChromaDB / chromadb，列 `BM25` 即覆盖 rank_bm25 / bm25_score。重复列不会更安全，只会让报错信息词不达意。
- 扫描范围必须**只有一处定义**（`_iter_doc_paths`）：CLI 与测试各写一套范围，正是本次漏检的成因 —— 测试自己拼 `_DOCS_DIR.glob(...)`，于是"测试全绿"与"根文档无人管"可以同时成立。
- 反向验证要真做：加完闸门后用 `tmp_path` 造一份含禁用词的样本，断言它**确实报 error**；只验"现状 0 error"证明不了闸门有判别力。

**历史实例**：2026-09-29（`one-loop-two-roles` 归档后回头审计根文档时发现）。同批补上 `extra_docs` / `banned_terms` / 豁免名单 / 行内标记四项机制。

## 接入通道

### SDK `connect()` 不等认证——不得以返回值判定就绪

**现象**：`WSClient.connect()` 返回只代表 WebSocket 已建连并发出认证帧。实测它与 SDK 的 `authenticated` 事件相差约 0.17s，且**认证失败时它照样正常返回**（失败只以 `error` 事件呈现，`errcode=853000`）。

**规则**：就绪一律以 SDK 的 `authenticated` 事件为准（`LongConnectionDriver.is_ready`，见 `src/channels/wecom/long_connection.py`），等待加上界 5s；超时只降级、**不主动断开**（交 SDK 自愈）；凭证类失败（错误信息以 `Authentication failed` 开头）才主动 `disconnect()` 并置致命。反面代价：以返回值判定会让"逐台 try/except"变成死代码、启动锚点 `n=total` **恒真**——排障时无法区分"三台都在线"与"三台都认证失败"。

### 投影层：终态帧只能发一次——"出错回一句兜底"不得覆盖已有终态

**现象**：`WeComPresenter.run` 消费事件流时若抛非取消异常，会**先 `finalize()`（发出终态帧）再抛出**。若把 `presenter.run` 放进带 `except Exception` 的回退块（本意是"出错时回一句脱敏文案"），同一条流上会出现**两帧 `finish=True`**，后一帧把用户**已经看到的正确终态覆盖成错误文案**。

**规则**：面向用户的兜底**只作用于回合启动阶段**（`start_turn` 之前）；投影阶段自成收尾契约，其异常**向上传播**（由驱动层吞掉）即可，**不得在 `except` 里再回一帧**。见 `src/channels/wecom/handler.py`。

### 投影层：任何"跳过 finalize"的判据必须显式排除错误态

**现象**：为满足"澄清等待期不结束流式回复"而加的守卫（"已有澄清问题且正文为空 ⇒ 不 finalize"）若不加 `_error is None`，会命中**上游异常收尾**路径 ⇒ 吞掉脱敏错误文案、**不发终态帧**、气泡永久停在"流式中"。

**规则**：凡"跳过 finalize"的判据，一律**显式排除错误态**（`self._error is None`）；**"不留悬挂流"是投影层硬不变量**——上游异常时必须 finalize（脱敏文案 + `finish=true`）。

## 如何更新

修复非平凡 bug 且属于"可复发缺陷类别"时，按"现象 → 规则"格式追加条目到对应分区。
判断标准：这个坑换个地方还会踩吗？会 → 记；不会 → 不记。
分块相关的坑进 chunking-issues.md，不进本文。
