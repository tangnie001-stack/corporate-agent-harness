# Design: trace-diagnosis

## Context

**现状**：告警侧由 `alert-triage` 承担"要不要处理"的三档判定；"为什么"仍靠人肉——拿 trace_id 翻 Langfuse、grep 日志、心算，结论不入库、不可回归。同一条 trace 换个人查可能得出不同根因。

**2026-11-04 本地活体实测**（基准 trace `trace_afe90f74`，KB 含 161 chunk）：

| 事实 | 数值 / 结论 |
|---|---|
| 日志 trace_id 与 Langfuse trace id | **一致**：同一次请求 25 行日志、trace 命中 1 条 |
| `metadata.retrieval_contexts` | 存在，含 `chunk_id/doc_id/page/score/order_in_prompt`；4 条命中约 1.8 KB |
| 候选规模 | `result_count=46`（融合后）→ `context_count=4`（进 prompt） |
| 相关 vs 无关的分数 | 0.761 / 0.737 / 0.734 / 0.604 vs 0.201 / 0.194 / 0.193 / 0.192 |
| 零召回 | **几乎不存在**：混合检索 + 精排恒返回 top_k；"量子纠缠"仍召回 32 条 |
| 未检索形态 | 离题问题被路由到 `search_web`，trace 里无 `retrieve_kb` span |
| ERROR 日志的 trace 覆盖 | 1428 行中 1314 行带 trace_id（**92%**） |
| 错误密度 | 单小时最多 51 条错误、22 个不同 trace |
| 保留期 | 应用日志 7 天；trace 30 天 |

**前置依赖**：

- `alert-triage`（**已合并**）：提供 `src/rca/` 的 `models` / `collector` / `loader` / 三态异常 / frames 解析辅助。本变更**不复刻**这套骨架，但 `LogSource` 协议、`judge()`、`render_card` 均为延迟专用，不复用（见 D2）。
- `bot-agent-routing`（实施中）：提供 `bot-ops` 人设、`profile` 路由、两个只读日志工具、告警轮 `alert_turn` trace 与 `rca_*` metadata。本变更的诊断入口挂在 ops 档，日志查询复用其 MCP 客户端（见 D12）。
- `turn-provenance-observability` 组 1+4（7 项，未开始）：提供温度、生效智能体、prompt 组成、消息构成等生成侧事实。缺失时本变更降级（见 D11）。

## Goals / Non-Goals

**Goals:**

- 同一份证据在同样输入下产出同样的根因（可回归、可统计）。
- 判定只用能被证据证伪的类别；证据不足一律"未判定"。
- 报告的任何断言都能点回原始证据。
- 人工入口与告警入口共用一次诊断内核。

**Non-Goals:**

- 不做修复执行（只出命令文本）。
- 不做自然语言入口（先只收 trace_id）。
- 不做同类问题聚类、跨 KB 聚合。
- 不引入独立 MCP 服务（出现第二个消费方再议）。
- 不覆盖支付投诉（二期，且不在本系统数据域内）。

## Decisions

### D1 诊断内核是确定性流水线，模型不决定路径

- **选**：`diagnose(trace_id)` 由代码固定五段——取 trace → 取日志 → 对齐证据 → 逐项判定 → 生成报告。模型只做归纳与措辞，不决定"下一步查什么"。
- **弃**：agent 自由探索（工具由模型挑）。理由：同一输入两次跑可能走不同路径，结论会漂，回归集失效；且"要不要再查一下"这类判断无法单测。
- **理由**：本变更的安全属性是"结论可复现"。档位（`tier`）与根因都在代码里算，模型只在最后一步把证据翻译成中文。
- **边界**：入口解析（自然语言 → 候选 trace）可以留给模型，但一旦拿到 `trace_id`，后续禁止模型改道。

### D2 复用采集骨架，但判定与渲染必须另写

`alert-triage` 合并后按其真实代码核对，复用边界如下（**不是"整套复用"**）：

| 组件 | 是否复用 | 依据 |
|---|---|---|
| `models`（`Tier` / `EvidenceStatus` / `EvidenceSpec` / `EvidenceResult` / `TriageVerdict`） | ✅ | 与延迟无关 |
| `collector`（波次执行、per-source 超时、`required` 短路、三态） | ✅ | `collector.py:123` 按 `getattr(source, spec.tool)` 分发，新协议零改动接入 |
| `loader`（skill frontmatter 解析） | ✅ | 只认 `tool/params/required/timeout/when` |
| `sources/base.py` 的 `SourceEmptyError` / `SourceFailedError` | ✅ | 三态语义通用 |
| `sources/grafana.py` 的 `ensure_query_ok` / `parse_frame_column` | ✅ | 查数解析经验（成功判定不看 status） |
| `sources/mcp.py` 的 MCP 客户端 | ✅ | 日志查询统一走 MCP，不另建 SLS SDK |
| `bot-agent-routing` 的 `sls_query_latency` / `sls_query_requests` | ❌ | 两者 `uri` 必填、只查白名单接口，是"接口维度"；诊断需要"错误维度"与"trace 维度"（见 D12） |
| `LogSource` 协议本身 | ❌ | 三个方法都是延迟专用：`latency_stats` / `request_detail` / `business_impact`；`request_detail` 也不含 `trace_id` |
| `judge()` | ❌ | 入参是 `severe_latency_ms` / `stats` / `history`，判定逻辑与延迟强绑定 |
| `render_card` | ❌ | 直接读 `results["stats"]`，卡片结构面向延迟 |

- **选**：新建窄协议 `ErrorEvidenceSource`（`trace_fetch` / `error_logs` / `retrieval_probe`），与 `LogSource` 并列而非扩展——扩展会让 `fixture` / `grafana` / `mcp` 三个实现被迫实现用不到的方法。
- **选**：另写 `diagnose/judge.py` 与 `diagnose/render.py`，只复用 `Tier` 枚举与 `TriageVerdict` 值对象，保证两边的档位语义一致。
- **弃**：把错误类的判定塞进现有 `judge()`。理由：会引入 `if is_error: ...` 分叉，两套判定逻辑互相污染，且现有单测全部面向延迟。
- **落点**：`src/rca/sources/{trace,error_log,probe}.py`、`src/rca/diagnose/{judge,render}.py`、`skills/rca-error/SKILL.md`。

### D3 诊断粒度：单 trace 起步 + 有界向前追溯 2 轮

- **选**：默认只诊断单个 trace；仅在以下情形向前拉取，且**最多 2 轮**：
  - 本 trace 出现 `history budget exceeded` 或 `context budget high`；
  - 本轮 `result_count = 0` 且问题含指代（"它 / 这个 / 上面说的"）。
- **弃**：全量 session 诊断。理由：每轮 3~6 次模型调用，5 轮就是 15~30 次，成本不可控且结论会随轮次漂移。
- **弃**：完全不追溯。理由："第三轮答错、根因在第二轮"是常见形态，纯单 trace 必然漏。
- **约束**：追溯只取前序轮次的**消息构成、是否被裁、当时模型**，不重新判定；追溯不再触发追溯（禁链式）；报告必须标注"结论基于前 N 轮上下文"。

### D4 一个告警 → N 个 trace：聚类取代表，上限 3

- **选**：按 `(错误类型, 接口, 错误码)` 聚类；每类只诊断**最早出现**的一个代表 trace；最多诊断 3 类，其余只计数。
- **弃**：每个 trace 一张卡。理由：实测单小时 22 个 trace，会刷屏且成本 100+ 次调用。
- **弃**：只诊断第一个 trace。理由：同一窗口可能并存多种根因（检索失败 + 上游超时），取一个会漏。
- **强制分离**：`tier`（要不要处理，告警维度）与 `root_cause`（为什么，trace 维度）是两个字段。一个告警可以是 `tier=需要处理 + root_cause=未判定`，也可以是 `tier=建议观察 + root_cause=精排降级`。合并会导致统计口径失真。
- **卡片结构**：告警级 `tier` + 代表 trace 的 `root_cause` + 同类次数 + 其他类别清单。

### D5 分数判据：相对为主、三条件联判、量纲门控

- **选**：判"无相关内容"必须**同时**满足：

  | 条件 | 作用 |
  |---|---|
  | `score_max < SCORE_FLOOR`（首版 0.35） | 没有任何一条像样的 |
  | `score_max - score_p50 < 0.05` | 不是"一条好 + 一堆平庸"，而是整体平庸 |
  | `score_kind == "rerank"` | 分数是真精排分，非降级回退值 |

  任一不满足 → 不下"覆盖缺失"结论，只呈现分数。
- **弃**：单一绝对阈值。理由：`relevance_score` 是模型打分，分布随查询长度、文档分布、KB 领域变化；实测 0.76 vs 0.20 只能证明**同一次查询内可比**。
- **弃**：按 KB 分别标定。理由：KB 会新增与重建，阈值维护成本高于收益。
- **理由**：三条件同时成立时，"整体平庸且没有尖峰"才成立；缺任一条都可能把"精排降级"或"一条能用的加上四条噪声"误判为覆盖缺失。
- **配置化**：阈值入 `src/config/settings.py`，首版标注"未标定"；标定方法为 `feedback` 点踩样本回放，取"有内容 / 无内容"两组的 `score_max` 分布中位切分（二期任务，不阻塞首版）。

### D6 断言必须引用证据，校验失败即降级

- **选**：每条证据分配 `evidence_id`（`log:<行号>` / `span:<id>` / `chunk:<id>`）；报告中每条断言引用至少一个存在的 id；生成后跑校验器，未引用或引用不存在 → 报告降级为"证据清单 + 未判定"。
- **弃**：只在模板里提示"请引用证据"。理由：提示不是约束，模型仍然会编造"日志显示…"。
- **弃**：校验失败后无限重试。理由：必须有出口，出口就是降级，不是放行。
- **理由**：没有这一层，"只用硬证据"只是口号；有了这一层，报告的每条结论都能被人复核。

### D7 只读：不执行任何修复动作

- **选**：诊断只读；修复建议以**可复制的命令文本**输出，由人执行。
- **弃**：白名单动作自动执行（重放、重入库、改绑定）。理由：诊断的价值 90% 在定位，10% 在执行；执行的代价是二次事故。
- **弃**：调用 `replay_trace` 自动复现。理由：重放用**当前**配置，结果与当时不一致是常态，容易误导；仅在"怀疑配置漂移"时由人手动执行。

### D8 `messages` 表补 `trace_id`，存量不回填

- **选**：新增可空列 + 索引；写入时从请求上下文取；存量行保持 `NULL`。
- **弃**：从 Langfuse 反查回填。理由：trace 侧没有"消息 → trace"的反向索引，回填成本高且只能覆盖 30 天。
- **理由**：入口的第二公里是"客服指着一轮回答要 trace"，没有列就只能靠点踩或 7 天日志。代价是**老会话永远不可诊断**，可接受。

### D9 定位器：`empty` 在错误类里可以是结论

- **选**：窗口内 0 条 ERROR、或有 ERROR 但无 trace_id 时，输出"未落到应用层 / 无请求上下文"，这**不是**"证据不足"。
- **弃**：沿用 `alert-triage` 把 `empty` 一律当缺证据。理由：错误类的空窗口本身就是有价值的事实（可能是 Nginx、容器被杀、根本无关应用）。
- **补充**：与 `alert-triage` 的三态不冲突——三态描述**数据源**是否取到，本决策描述**结论**如何解释，两者分层，实施时须在 `judge` 层显式分支。

### D10 投递：异步卡片 + 登录可见的详情页

- **选**：群内一条摘要卡片（归属建议 + 根因 + 同类计数 + 详情链接）；证据链放详情页，需登录。
- **弃**：把完整证据贴群里。理由：一次诊断 5~10 行日志 + 3~5 条 chunk，群里刷屏且含用户原文。
- **弃**：链接免登录可看。理由：链接会被转发到任意群，等于把敏感原文开放。

### D11 生成侧事实缺失时降级，不猜

- **选**：若 `turn-provenance` 的字段尚未落地，报告中"温度 / 生效智能体 / prompt 组成"三项标注为"不可用"，**不推断**。检索侧判定不受影响。
- **理由**：检索侧证据来自 trace，生成侧来自日志字段；两者可独立降级。把"不知道"写成"可能是人设没生效"，是误判的主要来源。

### D12 与 `bot-agent-routing` 的边界：对话式值班 vs 结构化归因

- **事实**：`bot-agent-routing` 提供的是**对话式值班**——人问"今天 createOrder 正常吗"，`bot-ops` 选工具查日志并直接作答；无固定流程、无落库、无引用约束、不可回归。本变更提供的是**结构化归因**——给定 `trace_id`，代码定路径、枚举根因、报告可复核。
- **选**：两者并存，不合并 change。诊断工具挂在**日志能力档**（`LOG_PROFILES = {bot-ops, bot-dev}`）的工具面上，作为"第二个能力"而非另一个机器人；接受 `bot-dev` 同享并在 spec 写明。
- **弃**：把 `sls_query_latency` / `sls_query_requests` 当诊断证据源。两者 `uri` 必填、精确白名单、明细不含 `trace_id`（`src/agents/tools/log_tools.py:37/41`、`log_backend.py` 无 trace 字段），只能回答"某个接口最近怎么样"。
- **数据源决策（A+B）**：
  - **A**：定位器读**应用日志**（容器 volume `/data/logs`，保留 7 天），按 `ERROR` 行第 3 段取 `trace_id`；解析复用 `src/cli/replay_trace.py` 的 `parse_trace_logs`。
  - **B**：在**两台 nginx** 的 `log_format` 增加 `$upstream_http_x_trace_id`（入口机 `deploy/nginx/edge.conf` 与应用机 `deploy/nginx/nginx.conf`）。应用已在响应头返回该值（`src/main.py:363`），因此网关的 status 可与 trace 关联；**502/504 时上游无响应头 → 该字段为空**，直接作为 D9「未落到应用层」的判据。
- **延后（C）**：MCP 服务端提供 trace / error 维度查询。**不是**"观测 MCP 自身流量"，而是"由 MCP 作为通道去查同一份网关/应用日志"。触发条件（满足其一才做）：需要超过 7 天窗口；应用扩到多副本/多机，本地文件不可达；需要把日志查询集中做鉴权与审计。届时只替换 locator 的下层实现，不改上层。
- **约束**：trace 名为 `chat_turn`（`alert_turn` 已在归档变更里 spike 证伪并回退，见 `archive/2026-11-06-bot-agent-routing/tasks.md:50`）；告警轮靠 tags `pf:alert` 区分。**没有** `rca_*` metadata，`tier` 由本变更自算。
- **约束**：脱敏与输出裁剪必须与 `ops-log-tools` 同源（`_mask` 与行数上限抽公共函数），否则同一份日志在两条链路会得到两种脱敏结果。

## 模块划分

```
src/rca/
├── sources/
│   ├── trace.py          # Langfuse 观测 + metadata.retrieval_contexts
│   ├── error_log.py      # 窗口内 ERROR 行 → trace_id（复用 Grafana 的 frames 判空经验）
│   └── probe.py          # 改写后重检：区分"表述问题"与"覆盖缺失"
├── diagnose/
│   ├── pipeline.py       # 五段编排（代码定路径）
│   ├── judge.py          # root_cause 枚举判定（D5 三条件、D3 追溯）
│   ├── report.py         # 模板与卡片渲染
│   └── validator.py      # evidence_id 校验（D6 降级）
└── locator.py            # 告警症状 + 时间窗 → 候选 trace（D4 聚类）
```

复用既有读取器：`src/cli/eval_trace_io.py` 的 `fetch_trace` / `parse_trace`。

## Risks

- **误判被当真**：根因一旦给错，值班会照着改配置。缓解：只读、证据可点开、"未判定"必须真实出现（验收项）、高风险建议（改 prompt / 重入库）需二次确认。
- **阈值未标定**：0.35 是首版默认值，可能在某些 KB 上过松或过紧。缓解：配置化 + 报告里回显使用的是哪个阈值 + 二期用点踩样本标定。
- **7 天日志窗口**：超出窗口的 trace 只能判到"哪份文档"，判不到"精排为什么砍"。缓解：检索侧判定以 trace 为主源，日志只补过程性事实。
- **定位器误报**：把"未落到应用层"说成"应用有问题"。缓解：D9 的显式分支 + 卡片上明示"证据来自应用日志窗口"。
- **报告落库引入新的敏感数据面**：报告含客户原文摘要，保留 180 天。缓解：与出域审计同一口径（脱敏摘要入库、原文只在详情页按需展开并留痕）。

## 待验证（实施前）

- `RERANK_OUTER_TIMEOUT` 真实触发时 `score_kind=timeout` 的落库形态（目前只有单元级验证）。
- 多轮检索 trace 的 metadata 体积上限（实测 4 条 1.8 KB，5 轮 × 10 条预估 20 KB 量级，未实测截断点）。
- 告警 `details` 的实际字段丰富度（当前载荷只有 `alertname / level / details / fingerprints`，接口名与错误码是否可提取待确认）。
