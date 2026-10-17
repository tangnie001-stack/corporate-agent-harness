# 日志格式规范（唯一归属文档）

## 行模板
`[prefix] 事件名 key=value ...`；`retrieval_signal:` 行保留（见「已知例外」）。
示例：`[retrieval] search done kb_id=k1 query_len=12 result_count=8`

## 前缀主表（开放登记制）
| 前缀 | 归属 |
|---|---|
| `[retrieval]` | 检索层（rag_tools / web_tools / retrieval.py） |
| `[verify]` | 验证节点（verify 包） |
| `[agent]` | agent 主循环（agent_node / middleware / workflow） |
| `[session]` | 会话管理（chat manager / persistence） |
| `[db]` | DB 层（repo / engine） |
| `[llm]` | LLM 调用层 |
| `[cli]` | 离线工具（cli/） |
| `[app]` | 应用边界（main.py 生命周期 + 全局异常兜底） |
| `[delegate]` | fork 子代理委派（skills/delegate_task + executor；事件：delegate start / delegate model turn / delegate end） |
| `[wecom]` | 企业微信智能机器人通道（services/wecom_service + channels/wecom/{callback,long_connection,presenter,handler,session,bounded_map}）；启动锚点 `bots connected n=<认证就绪台数> total=<配置台数>` |
| `[clarify]` | 澄清答案解析（services/clarify_service；事件：resolve miss / resolve already done / resolve raced / resolved） |
| `[provider]` | 模型 provider 兼容层（infra/llm/provider；启动期定价 ensure 的无价告警与降级告警，均直调 logger，非 Event） |
| `[alert]` | 阿里云告警接入（services/alert_service；入口载荷非法/去重丢弃/未配目标/回合繁忙/启动失败/超时/消费失败/空或异常回答/推送失败，均直调 logger，非 Event） |
| `[degradation]` | 外部依赖降级留痕（事件：`degraded`；`reason` 取值集合见 spec 取值集合表，值域登记见下） |

新语义面前缀：先登记 `src/core/log_events.py` 的 `LOG_PREFIXES` + 本表加一行后启用，不预建。

## 事件命名
英文小写、空格分隔 ≤2~3 词（`search done` / `rerank skip`）；键 snake_case；禁类名/函数名/工具名下划线名作事件词。

## 值类型编码（helper 唯一实现）
- int/bool 裸写；时长整数毫秒
- 字符串 token 安全（`^[A-Za-z0-9_./:@-]+$`）裸写，否则双引号 + JSON 转义
- 数组/容器：紧凑 JSON 文本（无空格）。如 `prompt validated` / `prompt assembled`
  的 `section_chars` 为「段名 → 字符数」映射，走此编码（调用方传 dict，编码由 helper 负责）
- query/搜索词完整记录、不按固定长度截断
- 附加字段（含 retrieval_signal）同走此编码

## 级别语义
- debug — 诊断细节默认关；info — 正常里程碑；warning — 降级可恢复（禁"正常但少见"）；
  error — 单点失败已处理；exception — 透传带 traceback
- 事件级别由 EventSpec.level 登记，helper 路由；exception 直调不建 spec
- 噪声控制用**调用点守卫「不记」**，不用 debug 降级：`EventSpec.level` 只允许
  `info/warning/error` 且调用点不能逐次降级，故高频或无信息量的轮次直接不调 helper
  （非 debug）。当前守卫：`agent resolved` 在请求与绑定都为空时不记、`skill dispatch`
  在 `kind=plain` 时不记。

## trace_id / session_id / channel
由 `src/core/logging.py` patcher 自动注入日志行第 3/4/5 段（trace_id / session_id /
channel），业务不手写。channel 取值见 `src/config/const.py` 的 `Channel`
（`web` 站点 / `wecom` 企业微信 / `alert` 阿里云告警自动分析轮次 / `feishu` 预留），未设置时以 `none` 占位、**不留空段**
（下游按段位解析；第 3 段 trace_id 的口径见「症状指标」）。各渠道在跑一轮之前写入
`current_channel`，落点与排障用法见 `docs/agents/troubleshooting.md`。

## 事件全集
以 `src/core/log_events.py` 的 `Event` 枚举 + `EVENT_SPECS` 为准，本文件不抄录（防双维护）。

## 来源与 prompt 观测事件

每轮来源与 prompt 组装的观测事件。字段明细以 `src/core/log_event_specs.py` 的 `EVENT_SPECS`
为准（本文件不抄录），此处只登记事件/前缀/级别与非代码可读的值域：

- `agent resolved`（session / info）——每轮生效智能体与解析来源；`source` 取 bound
  （沿用绑定）/ new_bound（首次绑定）/ ignored（请求与会话绑定不一致，已忽略）/
  unregistered（请求名未注册，降级为空）/ none
- `kb domain fallback`（session / warning）——知识库领域无对应 base 模板，回落保留值
  general；**不阻断**。`kb_id` 为发生回退的知识库、`domain` 为落库的非法值
- `agent domain mismatch`（session / info）——会话选定了预设同时又绑定了非通用领域的
  知识库；三选一替换语义下领域方法此时不参与组装，属**明确接受的代价**，非缺陷。
  `agent` 为生效预设名、`domain` 为知识库领域
- `prompt assembled`（llm / info）——system prompt 组成；`persona_source` 取 preset
  （会话预设人设）/ domain（知识库领域 base）/ general（**仅当领域未知、回落到内置通用
  base** 时出现 —— `kb_domain` 为 `general` 且有对应模板时走的是 domain 分支）；`kb_domain`
  为本次生效的领域标识；`tool_count` 为本轮注册的工具数（**`rag_tools` 全量**，含
  `task_create` / `task_get` / `task_list` / `task_update` / `task_output` / `task_stop`
  六个任务工具，生产约 8~10，不是"RAG 工具数"）；`section_chars` 为**实际拼进 system 的
  各段**字符数（容器值，紧凑 JSON，键序 = 段组装顺序 `base → runtime_contract → sources
  → tools → output`，空段不出现）；口径**不含**日期行与态 A 第二条未绑定消息，故占比
  估算系统性偏低，属预期而非缺陷。
- `prompt section share high`（llm / warning）——system 段按字符数估算占 context window
  的比例超阈值；**仅告警不阻断**。`share` 为估算占比、`est_tokens` 为估算 token 数、
  `threshold` 为当前阈值。换算系数是跨模型借用值、阈值是推断值，均非契约（见
  `src/config/settings.py` 的 Prompt 组装观测段）
- `prompt messages`（agent / info）——首轮组装的消息三段条数（system / 注入 / 历史）
- `skill injected`（session / info）——技能正文成功注入；`mode` 取 inline（命令触发）/
  preload（预设预绑定首轮预加载），`source` 取 command / preset
- `skill dispatch`（session / info）——命令形态分派；`kind` 取 plain（普通文本，不记）/
  known（命中技能）/ unknown（未注册命令）；`context` 取 inline / fork（技能 frontmatter
  的形态）/ none（命令未命中技能，record 为空）
- `skill resolved`（session / info）——加载每个 skill 时记其承载方式；`skill` 为 skill 名，
  `context` 取生效形态 inline / fork（**非 frontmatter 原值**），`context_source` 取
  default（未声明→inline）/ explicit（frontmatter 显式）/ auto_oversize（未声明但正文超
  `INLINE_PROMPT_MAX_CHARS`，加载期自动改 fork），`body_chars` 为正文（frontmatter 之后）
  字符数
- `skill preload skip`（session / warning）——预设预绑定首轮预加载跳过某 skill；`reason` 取
  not_found（预设声明的 skill 名查不到）/ not_inline（命中的 skill 是 fork，正文属子代理
  prompt，不注入主 agent）
- `model turn` 扩 `temperature` / `temp_source`（explicit 逐轮直传档 / default 模型构造档）/
  `kb_bound`，不新增事件
- `rerank done`（retrieval / info）——精排完成；`scored` 取 rerank（正常精排分）/
  fallback（精排调用失败，分数来自 `1 - distance` 的降级回退）；数值字段明细以
  `log_event_specs.py` 为准
- `dedup done`（retrieval / info）——内容级去重丢弃量；字段 `dropped`/`kept`，明细以
  `log_event_specs.py` 为准
- `delegate skip`（app / warning）——委派未被装配或未发起；`reason` 取 registry_empty
  （skills 目录存在但无任何 skill）/ skills_dir_missing（skills 目录不存在，附 `skills_dir`）/
  budget_exhausted（会话级委派预算触顶，`delegate_task` 拒绝该次委派并返回可读原因）
- `history budget exceeded`（agent / warning）——历史裁剪后仍超预算（**最近 1 轮保留例外**，
  不截）；`budget` 为绝对预算、`used` 为裁剪后实际估算 token、`kept` 为保留的历史条数
- `context budget high`（agent / warning）——单轮内模型调用输入达上限；**仅告警不处置**
  （不截断、不改写）。`used` 为本轮输入估算 token、`limit` 为当前上限
- `summary done`（session / info）——跨轮历史摘要成功生成并落 Redis；`covered` 为本次
  摘要覆盖到的消息条数、`tokens` 为摘要文本 token 数
- `summary fallback`（session / warning）——摘要生成失败或未采用，回退纯裁剪；**不阻断
  用户流程**。`reason` 为降级原因、`err` 为底层错误信息
- `hybrid done` 增 `dense_status` / `sparse_status`（retrieval / info）——两路**本次调用**状态，
  取 ok（成功且有结果）/ empty（成功但空结果=缺席）/ failed（未成功=失败）；与 `dense_count` 配对
  使用（`count=0` 本身分不出失败与缺席）
- `degraded`（degradation / warning）——外部依赖降级留痕；`reason` 取 embedding_failed /
  vector_store_failed / llm_unavailable_with_context / llm_unavailable_no_context /
  llm_stream_interrupted / provider_unavailable / upstream_exhausted / generation_idle
  （唯一来源：`docs/openspec/specs/degradation-policy/spec.md` 的取值集合表）。仅
  `reason=generation_idle` 时另有 `idle_s`（静默秒数，整数）与 `tier`（取 first 首事件前档 /
  gap 首事件后档）

**不再扩 `iteration done`**：其「消息数拆分」意图已由 `prompt messages` 在组装点承载，
不重复记录同一事实（避免后人再提）。

**三条循环日志的产点已迁至 middleware**（`src/agents/graph/middleware.py`）：`iteration done`
（`AgentSpanMiddleware` 入口）/ `model turn`（`AgentSpanMiddleware` 出口）/
`iteration limit`（`AgentTurnBudget` 的 `after_model`）。**事件名与字段集不变**；
`prompt messages` 仍由 `agent_node` 的首轮组装点发出（见上）。

## 评测（RAGAS）观测事件

裁判调用与出题的事件。字段明细同样以 `EVENT_SPECS` 为准，这里只登记**非代码可读的口径**：

- `judge call`（cli / info）——裁判**每次尝试**记一条。`ok=false` 表示**该次尝试失败**，
  不等于该指标最终失败（`error` 列只在最终失败时非空）；`note` 在异常路径上为**异常链摘要**
  （`外层类<-根因类: 根因消息`，见 `_exc_chain_summary`），成功时为空串；桥把失败吞成 NaN 而无异常时 `note` 亦为空串（此时判据是 NaN，不是 `note`）；**请求级证据
  （状态码 / 响应体可读性 / `finish_reason`）由 `HookEvidence` 桶承载、仅失败时进日志**
  （provider hooks，见 `src/infra/llm/provider/http_observability.py`）。`trace_id` 与报告
  同名，用于把「报告行 → 该题日志 → 裁判调用」串起来（排障见 `troubleshooting.md` §8（现行口径；§10 为换桥前的历史案例））。
- `metric retry`（cli / info）——单指标**重试**发生时记一条；`reason` 为异常链摘要
  （`外层类<-根因类: 根因消息`），口径与 `judge call` 的 `note` 同源。
- `testset self-contained dropped`（cli / warning）——出题后自足性门**丢弃**样本时记一条；
  `kept` / `dropped` / `total` 为计数。仅在确有丢弃时出现（无丢弃不记）。

## 症状指标口径（度量层，非日志事件）

以下三个指标由 `src/cli/symptom_metrics.py` 从**既有事件**聚合，**不新增事件、不改格式**；
登记在此仅为避免后人重新发明口径。分组键是 `trace_id`（日志行第 3 段），即"每请求"。

| 指标 | 来源事件 | 分组口径 |
|---|---|---|
| `iteration limit` 触顶率 | `[agent] iteration limit` | **按 trace 计**：出现该事件的 trace 数 ÷ **有 agent 行为的 trace 数** |
| 每请求 `retrieve_kb` 调用次数 | `[retrieval] retrieve done` | **按 trace 计**：该 trace 下 `retrieve done` 的**行数** |
| `answer_len=0` 占比 | `[verify] completeness check` | **按事件计**：`answer_len=0` 的条数 ÷ 该事件的条数 |

⚠ **触顶率的分母不是"全部请求数"**：日志里没有"请求开始"的统一锚点，分母取"出现任一已知
agent 事件（触顶 ∪ 检索 ∪ 完整性检查）的 trace"。应读作"在有 agent 行为的请求里有多少触顶"，
纯闲聊类请求不进分母；三个指标共用这一分母以便互相参照。

⚠ **检索次数为什么不取 `retrieval_signal`**：eval 链路（`src/cli/eval_deepeval.py`）全程不设置
`RequestContext` → `retrieve_call_seq` 恒 0，`empty_result` / `reretrieve` 行为信号在 eval 请求下
**不产生**；而 `retrieve done` 是检索工具内无条件落的事件，生产与 eval 两条路径下都可用。

## 已知例外
- `retrieval_signal:` 为 P1 Change 2 既有契约保留前缀，检索域聚合需
  `[retrieval]` + `retrieval_signal:` 两条 grep 模式；待前缀体系重构时统一
- skill / 智能体预设的**加载期**问题（名称非法、废弃字段、双轴推导、死 skill）走 `warnings.warn`，
  不登记为 `Event`（一次性内容问题，非请求路径）
