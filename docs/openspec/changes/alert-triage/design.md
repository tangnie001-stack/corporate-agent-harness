## Context

`alert-notify` 已实现完整链路：接收端点（URL token 鉴权）→ 原生 Alertmanager 载荷解析 → 按
`<fingerprint>|<status>` 去重 → 独立报警会话 → 后台分析（整轮超时、无澄清追问）→ 两段推送
（受理占位 + 结论）→ 失败兜底转发 → 恢复通知不走分析。**链路不缺，缺的是"分析这一轮做什么"。**

现状是自由分析：按固定模板渲染告警文本喂给 Agent，模型自由发挥。实测暴露三个问题：

- `/welife-pay/pay/createOrder` 每分钟约 **2 单**（近 6 小时 90 个分钟点），分钟级 `avg` 在 647–695ms 波动
- 告警条件是「连续 5 次评估、每次取最近 1 分钟 `avg(upstream_response_time)`、均 > 1000ms」——
  该量级下两单同时偏慢即触发
- 同告警 1 天前已报过、系统自动关闭；模型仍会产出"看似合理"的原因，值班人无法据此跳过排查

实测确认的外部事实（详见 `docs/research/2026-11-04-grafana-arms-api-capabilities.md`）：

- Grafana 10.0.9，**只开放 80 端口**（`https://<host>:80`）
- 规则定义可读（ruler API，Viewer 即可），含 SQL 查询、阈值、`for` 时长
- 告警历史可读（annotations API），形态是每分钟快照
- 日志查询可经 `/api/ds/query` 代理，**返回的 `status` 恒为 500，数据在 `frames` 里**

约束：日志无 trace_id；`upstream_response_time` 单位为秒；业务失败也返回 HTTP 200（业务码在响应体）。

## Goals / Non-Goals

**Goals:**

- 报警轮产出**可判定的三档结论**：`无需处理` / `建议观察` / `需要处理`
- 让"无需处理"成为可交付价值：值班人看到即可关闭
- 证据采集与判断逻辑**可配置、可回归**，新增告警类型不改代码
- 任何降级都**显式可见**

**Non-Goals:**

- 不查支付业务库（失败订单不落表，收益低于成本）
- 不做 `sls-mcp-service`（对外复用由另一变更承担）
- 不修改 Grafana 告警规则、不做自动静默、不自动建单
- 不提供多轮追问（报警轮仍为非交互轮次）

## Decisions

### D1 分层：skill 声明「查什么」，代码决定「怎么查」

skill frontmatter 的 `evidence` 清单由代码解析并执行；判定正文给模型读。

弃：全交给模型调度（每步一轮 LLM，30 秒预算撑不住，Step 0 可能被跳过）；全写死在 Python（新增告警类型要发版）。

### D2 取数走 Grafana API，不走 SLS SDK

- **选**：`POST /api/ds/query`（一个 Viewer token 覆盖 SLS 与 Prometheus），规则走 ruler API，历史走 annotations API。
- **弃**：① SLS SDK 直连——每个阿里云账号一套 AK，多账号时配置成倍增长；② 把工具注册进 `ToolRegistry`——实测该 registry 是 `make_rag_tools` 内的局部对象、用完即弃，且注册后会让日志查询对所有会话的模型可见，与"不由模型决定查什么"冲突，并扩大日志访问面。
- **代价**：多一跳、返回为 DataFrame 列式结构、受 Grafana 可用性影响。

### D3 证据采集两波并行

先并行执行无 `when` 的条目；`when` 成立（依赖前一波结果）的再并行一批。`when` 由代码求值，不是模型判断。

### D4 三态 + required 短路

`ok` / `empty` / `failed` 必须可区分；`empty` 不得表述为"未取到"，`failed` 不得表述为"无异常"。
`required` 失败 → 只出"证据不足"，不出根因。每个 skill 的 `required` 不超过 1–2 条。

### D5 档位由代码确定性计算，模型只产解释

- **选**：`judge.py` 依据证据计算 `tier`；模型只输出 `summary / hypotheses / counter_evidence / next_actions / confidence`。
- **弃**：模型定档 + 代码下调（两套判定逻辑会互相打架，且模型档位无法回归）。
- **理由**：档位是本变更唯一的安全属性，必须可复现、可单测。模型一旦参与定档，同一证据两次跑出不同档位，直接击穿"可回归"。

### D6 定量字段由代码注入，模型只写定性

时间、影响量、分布、业务影响等数值由渲染层从 `EvidenceResult` 填充模板槽位；模型不转写数字。
**理由**：让模型把证据里的数字重写一遍，等于给幻觉一次转录机会，且"数值可追溯"无法验收。

### D7 保守判定：说"无需处理"必须同时满足四条

1. 观测窗口样本量 `n ≥ 1`（`n = 0` 判"日志断流/服务全挂"，一律 `建议观察`）；
2. 窗口内 `5xx = 0`；
3. 慢请求数 `≤ 1`；
4. 窗口内最大耗时 < **严重阈值**（按接口配置，独立于告警阈值）。

另需至少一条正向证据：与近 7 天同告警形态一致且当时自愈 / 当前规则状态已恢复 / 样本量低于告警口径的最小样本量。

**禁止**以"未找到原因""证据不足"推断 `无需处理`。证据不足（含 `required` 失败）一律 `建议观察`。

严重阈值与告警阈值的区别很关键：告警阈值 1000ms 只说明"比平时慢"，严重阈值（如 3000ms）才说明"用户可感知"。

### D8 业务影响基线取「昨日同时段」

弃"前一小时"：凌晨低峰会被判成暴跌。日志无法反映业务成败时明确标注"业务成功率未知"，不以接口层 2xx 比例代替。

### D9 阈值与窗口从规则定义解析，不写死

`latency_stats` 的慢请求阈值按接口配置；告警阈值、`for` 时长、聚合方式从 ruler API 的规则定义读取
（`C.model.conditions[0].evaluator.params`、`duration`、`A.model.query`）。

四个实测接口的告警阈值各不相同（100 / 300 / 1000 / 1000ms），全局默认值必然错。

### D10 skill 由代码按 alertname 匹配

弃：让模型读 skill 列表自选（多一轮调用、结果不可复现）。未匹配走通用兜底清单，并在结论标注"该告警类型未覆盖"。

### D11 告警上下文由规则反查补齐

现有载荷只有 `alertname / grafana_folder / severity`，缺 `uri`、阈值、窗口。

- 按 `alertname + grafana_folder` 查 ruler API（该接口不返回 UID），从 `A.model.query` 正则提取 `request_uri = '...'` 与 `logstore`；
- 缓存 10 分钟，键为 `alertname|grafana_folder`；
- 解析失败或 API 不可用 → 保留原始字段，标记"语义未知"，且**禁止引用其数值做结论**。

### D12 `ds/query` 的 `status` 不可信

实测该接口对成功查询也返回 `status: 500`，真实数据在 `frames[].data.values`（列式）。

客户端判定成功的条件：`frames` 非空 **且** `data.values` 非空；HTTP 200 只说明调用送达。
禁止用 `status` 判成败。

### D13 能力开关与灰度

- 全局开关 `RCA_ENABLED`，默认 false；关闭时行为与变更前完全一致。
- 灰度粒度：`RCA_ALERT_WHITELIST`（alertname 列表）。未在白名单的告警走通用兜底或直接走现状，由配置决定。
- **关闭态下的会话历史**：新格式轮次（证据包 + JSON）不写入 Redis 会话历史，避免关闭开关后同一 `alarm_id` 读到脏历史。

### D14 结论落库契约

- 正常完成 → 落**渲染后的卡片文本**（与推送同源），`process_json` 附带结构化 verdict 供回访；
- 结构校验失败 → 落 `interrupted`，不落 `complete`；
- 下一轮历史注入的是渲染文本，不是 JSON。

### D15 字段白名单与脱敏

- 进入 prompt 的日志字段只取白名单：`ts / upstream_addr / request_time / status / upstream_status / method / uri`；
- **不取** `http_user_agent / http_referer / 原始 URI 查询串`（外部可构造注入文本）；
- 字段值做控制字符清洗 + 长度截断，并与指令段落显式分隔；
- 卡片与日志中的客户端 IP、节点内网地址脱敏（卡片只显示节点别名，如 `node-1`）。

### D16 fixture 仅用于测试

`FixtureLogSource` 只在测试与非生产环境启用。生产未配置 Grafana 凭据时直接 `failed`，
**不得退回假数据**（生产镜像不含 `tests/`，退回逻辑只会变成 FileNotFoundError）。

### D17 `rca-*` skill 不对外暴露

`skills/rca-*/SKILL.md` 必须声明 `user-invocable: false` 与 `disable-model-invocation: true`，
避免被现有 `SkillLoader` 当成普通技能注入问答会话。skill 解析由 `src/rca/loader.py` 负责，与
`src/agents/skills/loader.py` 并存但互不影响。

### D18 超时取值与叠加

| 项 | 取值 | 依据 |
|---|---|---|
| 规则反查 | 5s | 单次 HTTP，缓存命中后近似 0 |
| 单个取数工具 | 8s | ds/query 实测 0.2s（小窗口），留 40 倍余量 |
| 告警历史 | 5s | annotations 返回 55KB / 100 条 |
| 采集总预算 | 25s | 两波：8 + 8 + 规则反查，留并发余量 |
| 并发上限 | 4 | 避免对 Grafana 与 SLS 造成压力 |
| 整轮 | 沿用现有 240s | 采集先于 `start_turn` 完全收口，生成占剩余预算 |

采集超时后对所有在途查询 `cancel()`，未完成项标 `failed/total_timeout`，不阻塞判定。
**采集与生成串行**，不并行——避免"轮次已超时但采集仍在跑"。

## Risks / Trade-offs

- **[误判无需处理，漏掉真故障]** → D7 四条同时满足 + 严重阈值独立于告警阈值 + 上线后按 D14 落库回访，统计"判无需处理后 T 小时内同指纹再次 firing / 同 URI 出现 5xx"的疑似误拦，超阈值自动收紧。
- **[规则解析失败]** → 依赖 `request_uri = '...'` 写法，网关类 PromQL 规则解析不出 → 标记"语义未知"，降级为 `建议观察`，不猜。
- **[`status:500` 被误判为失败]** → D12 写进 spec 与单测。
- **[证据包撑爆上下文]** → 每源行数/字符上限；明细只在 `n < 20` 时取。
- **[时区/单位换算错]** → 严格占位符 + 秒→毫秒单测 + UTC→CST 单测。
- **[Grafana 不可用]** → 全部证据 `failed` → `建议观察` + 兜底转发；不出现空白卡片。
- **[采集段抛错导致占位后无下文]** → 占位之后整段包进统一 try/except → 兜底转发（现状该段无兜底）。

## Migration Plan

1. 新增 `src/rca/` 与取数实现，开关默认关闭，线上行为不变。
2. fixture 模式跑通判定与渲染，补单测与契约测试。
3. 打开开关，白名单只放 `支付单createOrder响应时间上升` 一条，观察 2 周。
4. 统计三档分布、误拦回访、端到端耗时；达标后扩白名单。
5. 回滚：关闭开关即回到自由分析；新格式历史不写入，无需数据清理。

## Open Questions

- 严重阈值取值：createOrder 用 3000ms 是否合适（需真实故障样本校准）。
- 慢请求阈值是否改为相对基线（`max(昨日同时段 p95 × 倍数, 兜底下限)`）。
- 误拦回访的统计口径与告警阈值（分子/分母/观察窗）。
- 网关类（Prometheus）告警是否纳入本期白名单——其规则是 PromQL，解析路径不同。
