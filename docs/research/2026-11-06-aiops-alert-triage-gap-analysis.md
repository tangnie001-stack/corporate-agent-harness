# AI 告警与智能运维对标：能力差距分析

> 成文日期：2026-11-06
> 范围：本仓告警接入、三档判定、RCA 取证、企微推送与只读日志工具；对标 11 个（组）业界产品/项目。
> 方法：只读本仓代码与规格；外部事实只取官方文档、官方仓库 README 或源码，逐条标注来源编号（见 §6）；访问日期均为 2026-11-06。无法从一手来源确认的写「未验证」。
> 边界：本文是外部事实与差距分析，不是选型决定；决定应另落 ADR / change。

## 结论摘要

- 我们的差异化在「判定确定性」：档位由代码计算、模型只写解释（`src/rca/judge.py:126`、`src/rca/explain.py:94`），这比 HolmesGPT / Rootly / incident.io 的纯 agent 判定更可复现；代价是根因调查深度受限于 skill 预先声明的证据。
- 最大缺口不是「再加一个模型」，而是**判定质量不可度量**：档位只存在于卡片文本，没有结构化落库、没有人工纠正回灌、没有标注回归集；「无需处理可直接关闭」这一核心价值目前无法被证明或校准。
- 证据面只有 SLS 访问日志 + 昨日同时段业务量（`src/rca/sources/base.py:16`），没有指标、链路、变更/发布事件；访问日志对业务失败常返回 200，存在把真故障判成「无需处理」的路径。
- 入口只有「同指纹 + 同状态」去重（`src/services/alert_service.py:191`），没有跨告警聚合/抑制；同一根因的多条告警会各自起一轮 Agent。且非延迟类告警在无匹配 skill 时静默退回自由分析（`src/rca/pipeline.py:142`），与 `alert-triage` 规格要求的「通用兜底清单」不符。
- 业界已经把「聚合/抑制 → 关联 → 根因 agent → 处置 → 复盘」做成产品闭环；我们目前只完成「单告警 → 单轮判定 → 群卡片」这一段。

## 一、我们现状

### 1.1 链路全景

| 环节 | 现役实现 | 代码 / 规格 |
|---|---|---|
| 接入 | Grafana contact point → POST /api/alerts/webhook/{token}；URL 路径 token 常数时间比较；立即 202，后台分析 | `src/services/alert_service.py:336`；`docs/openspec/specs/alert-notify/spec.md` |
| 解析 | 原生 Alertmanager 载荷：commonLabels.alertname、alerts[].fingerprint、status、values、grafana_folder | `src/services/alert_service.py:73` |
| 去重 | 每条指纹生成「指纹+状态」键；全部命中 TTL 窗口才丢弃；有界 BoundedTtlMap | `src/services/alert_service.py:191`、`:242`、`:267` |
| 恢复 | status 非 firing 直接推「[已恢复]」，不跑 Agent | `src/services/alert_service.py:178`、`:389` |
| 判定开关 | RCA_ENABLED 默认 false + 告警名白名单；未命中直接返回原 query | `src/rca/pipeline.py:48`、`:104` |
| 上下文 | 从 Grafana ruler API 按 alertname + folder 反查规则，解析 uri / threshold / duration / logstore / refId 含义，带 TTL 缓存 | `src/rca/context.py:139`、`:160` |
| 历史 | Grafana annotations（每分钟快照）→ 近 7 天出现分钟数 | `src/rca/pipeline.py:57`、`src/rca/context.py:202` |
| 取证 | skill frontmatter 声明 evidence；两波并行、单源超时、总预算、required 短路、三态归一 | `src/rca/collector.py:228`、`:170`；`src/rca/models.py` |
| 取数 | Grafana /api/ds/query 代理 SLS（GrafanaLogSource）或 MCP SLS（McpLogSource） | `src/rca/sources/grafana.py:92`、`:141`、`:175`；`src/rca/sources/mcp.py` |
| 判定 | 代码确定性三档：无需处理 / 建议观察 / 需要处理；5xx、明细、慢请求、最大耗时、业务量跌幅 | `src/rca/judge.py:126` |
| 解释 | 模型只产出 summary / hypotheses / counter_evidence / next_actions / confidence；失败退化为空 | `src/rca/explain.py:30`、`:94` |
| 渲染 | 定量字段由证据填充，模型数字不进入卡片；IP 脱敏、节点别名、限长 | `src/rca/render.py:129` |
| 推送 | 受理占位 + 结论两段；异常/超时/空回答/繁忙均兜底转发 | `src/services/alert_service.py:433`、`:496` |
| 会话 | 一个报警身份一个会话；固定 user_id；不挂 KB、不绑定预设；不澄清 | `src/services/alert_service.py:151`；`docs/openspec/specs/alert-notify/spec.md` |
| 运维工具 | ops/dev 两个只读日志工具：sls_query_latency / sls_query_requests；接口白名单、行数上限、脱敏 | `src/agents/tools/log_tools.py:208`、`:264`；`docs/openspec/specs/ops-log-tools/spec.md` |
| 反馈 | 站点答案 feedback（positive/negative），与告警档位无关联 | `src/api/feedback.py:72` |

### 1.2 已具备的能力

1. **告警生命周期入口完整**：鉴权、解析、去重、恢复、两段推送、兜底都有明确契约（`alert-notify`）。
2. **判定与解释分离**：这是本仓最有价值的设计约束。档位可复现，模型不能改档；卡片数字由证据注入（`alert-triage` 规格「判定档位由代码确定，模型只产出解释」「定量字段由代码注入」）。
3. **证据三态**：ok / empty / failed 可区分，required 失败时不给根因假设（`src/rca/models.py`、`src/rca/collector.py`）。
4. **证据采集有预算与短路**：两波并行、单源超时、总预算、取消在途（`src/rca/collector.py:170`）。
5. **skill 可扩展**：新增告警类型只需加 `skills/rca-*/SKILL.md`，不改代码（`src/rca/loader.py:67`、`:95`）。
6. **告警轮与问答轮隔离**：独立会话、固定 user_id、不澄清、渠道标签 alert（`alert-notify`、`bot-agent-routing`）。
7. **只读日志工具**：ops/dev 可在群里直接查白名单接口的聚合与明细，带脱敏与失败语义（`ops-log-tools`）。

### 1.3 明确的非目标（来自 change 设计，不是缺陷）

- 不查支付业务库；不修改 Grafana 告警规则；不自动静默；不自动建单；不做多轮追问（`docs/openspec/changes/archive/2026-11-04-alert-triage/design.md`「Non-Goals」）。
- 告警会话不挂 KB、不绑定智能体预设（`alert-notify`）。
- 日志取数不直连 SLS SDK，统一经 Grafana 或 MCP 代理（`alert-triage` D2）。

### 1.4 规格与实现的偏差（本次实读发现）

| 规格要求 | 实现现状 | 证据 |
|---|---|---|
| 未匹配 skill 时使用通用兜底清单，并标注「该告警类型未覆盖」 | 直接返回原 query、verdict=None，退回自由分析，无任何标注 | `docs/openspec/specs/alert-triage/spec.md`；`src/rca/pipeline.py:140` |
| 反查失败或解析不出 URI 时判「建议观察」并列出缺失项 | context.uri 为 None 时直接不判定，退回自由分析 | `docs/openspec/specs/alert-triage/spec.md`；`src/rca/pipeline.py:133` |
| 落库时在结构化字段附带判定档位与证据摘要 | 只用卡片文本覆盖正文；conversation_history 无档位/证据列 | `docs/openspec/specs/alert-triage/spec.md`；`src/services/turn_runner.py:185`；`src/infra/db/models/chat.py:33` |
| 业务结果不可见时卡片标注「业务成功率未知」 | 渲染层无该文案 | `docs/openspec/specs/alert-triage/spec.md`；`src/rca/render.py:129` |

前三条不是「能力不足」，而是**已写进规格但未落地的保守降级语义**，共同后果是把本该显式可见的「证据不足」静默还原成变更前的自由分析；第四条则让「业务结果不可见」这一事实在卡片上消失。

## 二、对标对象

### 2.1 Grafana Alerting / IRM（原 OnCall）

**定位**：告警规则与通知路由的基础设施；OnCall 负责值班、升级与 Slack 协作。

- Grafana Alerting 在 notification policy 里做分组：`Group by` 决定哪些告警实例合并；默认按 `alertname + grafana_folder`；`group_wait` 默认 30s、`group_interval` 默认 5m、`repeat_interval` 默认 4h；还提供 mute timings / silences，只抑制通知、不停止规则评估（[S2][S3]）。
- Grafana OnCall OSS 已于 2025-03-11 进入维护模式、2026-03-24 归档，官方引导迁移到 Grafana Cloud IRM；IRM 提供 on-call 排班、升级、分组路由、事故时间线与复盘（[S1]）。
- **边界**：强在「通知不淹人」和值班协作，不是根因调查 agent；IRM 页面只说明可在事故中查看同一批 metrics/logs/traces，未见自动 RCA 描述（是否有 AI RCA 未验证）。

### 2.2 Keep（keephq/keep）

**定位**：开源 AIOps 与告警管理平台，单窗格 + 去重 + 富化 + 关联 + 工作流（[S4]）。

- 去重分 partial / full：partial 按 provider 预置的 fingerprint fields 把「同告警不同状态」合并；full 在忽略字段外全字段相同则丢弃；处理顺序是 **ingestion → enrichment → deduplication**（[S5]）。
- 关联有两套：手工 correlation rules（属性条件 + AND/OR，可生成 incident-candidate 或 incident）与 AI correlation（用租户历史告警/事故训练，5–15 分钟一轮，置信度超阈值才归入 incident）；**AI correlation 只在 Cloud / Enterprise 提供，开源版没有**（[S6][S7]）。
- 富化支持正则抽取（Extraction）：按属性定义 regex，首次命中即停止（[S10]）。
- 工作流支持 trigger / action / step，可做 auto-remediation、富化、多通道通知；AI 可作为 workflow 的 step/action，接第三方 LLM（[S8][S9]）。
- **边界**：平台能力强，但 AI 能力主要在关联、workflow 内的摘要/路由，未见 HolmesGPT 式的多轮调查 agent；开源版缺少 AI correlation。

### 2.3 HolmesGPT（robusta-dev/holmesgpt）

**定位**：CNCF Sandbox 的 SRE agent，面向生产事故的根因调查。

- 核心是 agentic loop：从多个可观测源迭代取数、形成假设、找根因；内置 toolset 覆盖 Prometheus、Grafana、Datadog、Loki、Tempo、Elasticsearch、Kubernetes、云厂商等（[S11][S12]）。
- 双向告警集成：可从 AlertManager / PagerDuty / OpsGenie / Jira 拉告警，并把结论写回源或 Slack（[S11]）。
- Operator mode：7×24 后台巡检，支持 deployment verification 与 scheduled health checks；连 GitHub 后可开 PR（[S11][S12]）。
- 评测：150+ evals，分 regression / benchmark / easy / medium / hard，每次提交跑回归，周度全量（[S13]）。
- **边界**：判定由模型在 agentic loop 中完成，未见代码定档机制；适合「已经决定要查」的场景，未见告警聚合/抑制能力。

### 2.4 k8sgpt（k8sgpt-ai/k8sgpt）

**定位**：Kubernetes 集群扫描与诊断工具，把 SRE 经验固化成 analyzers。

- 内置 + 自定义 analyzers 扫描集群对象，`analyze --explain` 用 LLM 解释；`--with-doc` 拉官方 Kubernetes 文档；支持 anonymization、远程缓存、MCP（[S14]）。
- 持续监控靠独立的 k8sgpt-operator，可对接 Prometheus / Alertmanager（[S14]）。
- **边界**：只诊断、给建议，不做自动处置；本次只读 README，未发现告警接入/去重/聚合能力；不是通用 AIOps 平台。

### 2.5 阿里云 SLS / ARMS / CloudMonitor

**定位**：阿里云原生的日志告警、应用智能巡检与云资源告警。

- SLS 告警支持智能合并：用 MinJoin 文本聚类 + 向量相似度聚类，把重复/冗余/相关告警合并，一个分组一段时间只通知一次；官方示例称 16,000 次告警经「智能合并、去重、静默、路由」后只通知 374 次，降噪比近 98%（[S15]）。
- ARMS Intelligent Insights 是 AIOps 巡检：6 个预置模块（错误率突增、平均响应时间突增、流量突增、流量分布不均、新异常、实例级指标离群），异常时生成事件并做根因分析与建议；支持按 Top N 接口、黑名单、检测阈值/异常阈值配置（[S16]）。
- CloudMonitor 报警规则支持静默期（mute period）、生效时间段、报警联系人组、报警回调 URL，以及触发 Auto Scaling / 写入 SLS 等动作（[S17]）。
- **边界**：降噪与巡检成熟，但根因分析是平台内置算法/规则，不是可插拔的 agent；本次官方文档未展示跨云、跨 SaaS 的统一取证，能力主要围绕阿里云数据源。

### 2.6 PagerDuty AIOps

**定位**：事件智能与值班编排，强项是降噪、分组与「可能原因」信号。

- 分组：Global / Intelligent / Content-Based / Time-Based / Unified Alert Grouping；还有 Auto-Pause Incident Notifications、Event Orchestration 的 Alert Suppression（[S18]）。
- 根因信号：Probable Origin、Past Incidents、Related Incidents、Outlier Incident、Recent Changes；ML 模型「learn and adapt based on user behavior」（[S18]）。
- 编排：Event Orchestration 支持跨服务富化与复杂逻辑、Dynamic Routing、Dynamic Escalation Policies（[S18]）。
- **边界**：强在「告警进来之后怎么少打扰人、怎么给值班人上下文」；不是开放式调查 agent；自动处置需依赖其编排/第三方动作，审批边界未在文档中说明（未验证）。

### 2.7 incident.io / Rootly

**定位**：事故管理平台，正在把「根因调查」做成 agent。

- incident.io：40+ 告警源、按团队/服务路由；Investigations 是 agentic RCA，会随新信号重新评估、用证据构建 finding，并用 adversarial agent 压测假设；会查 telemetry、docs、past incidents（[S19][S20]）。
- Rootly：AI SRE 在告警触发时即开始调查，关联 live telemetry、recent deploys、commits、config changes、similar past incidents，给出带置信度的 probable root cause；还能从完整事故时间线生成复盘并跟踪 action items（[S21]）。
- **边界**：事故生命周期与复盘强；告警去重/分组/抑制是否内建，官方页面未明确（未验证）。

### 2.8 Datadog Bits / Sentry Seer

**定位**：可观测平台内的 AI 调查与修复 agent。

- Datadog Bits Investigation：自动对 monitor alert 发起端到端调查，迭代形成假设、取 telemetry、给根因；支持 monitor 自动触发、Slack/Teams 入口；知识源包括 `bits.md`、monitor messages/runbooks、past feedback；另有 Bits Remediation / Workflow Automation（[S22][S23]）。
- Sentry Seer：AI debugging agent，用 issue、trace、log、profile、code 做根因分析；Autofix 可生成修复并开 PR，也可把代码生成交给外部 coding agent（Claude Code / Cursor / Copilot）（[S24]）。
- **边界**：能力围绕各自平台的数据模型；Sentry 的「处置」是改代码/开 PR，不是运维重启/扩容/回滚；Datadog 的处置能力在 Bits Remediation / Workflow Automation，审批边界未在本次页面确认（未验证）。

## 三、能力对照表

**取值**：有 = 官方文档明确提供且为主要能力；部分 = 提供但受限（付费版 / 需集成 / 仅特定对象）；无 = 官方文档明确没有或产品不涉及；未验证 = 本次未从一手来源确认。每格来源见 §6。

| 能力 | 我们 | Grafana Alerting/IRM | Keep | HolmesGPT | k8sgpt | 阿里云 SLS+ARMS+CM | PagerDuty AIOps | incident.io | Rootly | Datadog Bits | Sentry Seer |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 多源告警接入 | 部分（仅 Grafana webhook） | 有 | 有 | 有 | 无 | 有 | 有 | 有 | 有 | 有 | 部分（错误/性能问题） |
| 同指纹去重 | 有 | 部分 | 有 | 无 | 无 | 有 | 有 | 未验证 | 未验证 | 部分 | 有（issue 分组） |
| 跨告警聚合/分组 | 无 | 有 | 有 | 无 | 无 | 有 | 有 | 未验证 | 未验证 | 有 | 有（issue 分组） |
| 抑制/静默 | 无 | 有 | 部分 | 无 | 无 | 有 | 有 | 未验证 | 未验证 | 部分 | 部分 |
| 拓扑/服务关联 | 无 | 部分（label） | 部分 | 部分（K8s） | 部分（K8s） | 部分（ARMS） | 部分（ML） | 部分 | 部分（Catalog） | 部分（service map） | 部分（trace） |
| 历史相似事件 | 部分（近 7 天同告警分钟数） | 未验证 | 部分 | 未验证 | 无 | 部分（ARMS 基线） | 有（Past/Related） | 有 | 有 | 部分 | 部分 |
| 变更/发布事件证据 | 无 | 未验证 | 部分（可集成） | 部分（GitHub） | 无 | 无（未验证） | 有（Recent Changes） | 有 | 有 | 部分（deploy tracking） | 部分（releases） |
| 日志/指标/链路多源取证 | 无（仅日志单源） | 部分 | 部分 | 有 | 部分 | 部分 | 部分 | 有 | 有 | 有 | 有 |
| 根因调查 Agent | 部分（只解释，不自主取证） | 无 | 部分 | 有 | 有 | 部分（内置 RCA） | 部分（信号） | 有 | 有 | 有 | 有 |
| 判定确定性（代码定档） | 有 | 有 | 部分 | 无 | 部分 | 有 | 部分 | 无 | 部分（置信度） | 部分 | 部分 |
| 周期巡检/趋势 | 无 | 部分（持续评估） | 未验证 | 有（operator） | 部分（operator） | 有（ARMS 6 模块） | 未验证 | 未验证 | 未验证 | 有（Watchdog） | 未验证 |
| 自动处置 | 无 | 部分（webhook） | 有（workflow） | 部分（开 PR） | 无 | 部分（Auto Scaling/SLS） | 部分（Orchestration） | 未验证 | 未验证 | 部分（Bits Remediation） | 部分（开 PR） |
| 人工审批边界 | 无 | 未验证 | 部分（incident-candidate） | 部分（CLI 人在环） | 不适用 | 部分（动作配置） | 未验证 | 未验证 | 未验证 | 未验证 | 部分（人审 PR） |
| 人工纠正回灌 | 无 | 未验证 | 部分（历史训练） | 未验证 | 无 | 未验证 | 有（用户行为学习） | 部分（复盘） | 部分（复盘/action items） | 有（past feedback） | 未验证 |
| 判定质量回归集 | 无（仅单测） | 未验证 | 未验证 | 有（150+ evals） | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 |
| 告警→事故生命周期 | 部分（按 alarm_id 聚合会话） | 有 | 部分 | 部分（写回源） | 无 | 部分（事件/动作） | 有 | 有 | 有 | 有 | 部分（issue） |

## 四、缺口清单

### P0（不做会误判 / 漏判）

#### P0-1 判定质量不可度量：无结构化档位、无回访、无回归集

- **缺失**：判定档位只存在于卡片文本；`conversation_history` 没有 tier / reason / evidence 摘要列（`src/infra/db/models/chat.py:33`）；落库只覆盖正文（`src/services/turn_runner.py:185`）；站点 feedback 与告警无关（`src/api/feedback.py:72`）；没有「判无需处理后是否复燃」的统计；没有标注告警集与回归脚本。
- **影响**：这是最危险的一条。核心承诺是「判无需处理即可关闭」，但当前既不能算准确率，也不能在阈值改动后回归；误判会静默发生，且没有任何机制把它变成下一轮的规则/阈值改进。规格里「结构化字段附带判定档位与证据摘要」也未落地。
- **补齐的最小代价**：
  1. 新增 `alert_triage` 表（alarm_id、trace_id、alertname、folder、uri、tier、reason、evidence_status_json、created_at），在 `_analyze_turn` 判定成功后写一行，与卡片同事务或同轮落库。
  2. 加一个 CLI 报表：三档分布、按告警名/URI 的分布、「无需处理」后 24h 内同指纹再次 firing 的比例、同 URI 1h 内出现 5xx 的比例。
  3. 用现有 `fixture` 源（`src/rca/sources/fixture.py`）做回放：50–100 条历史告警标注 gold tier，跑 `pytest` 或独立脚本输出混淆矩阵。
- **验收**：任意一次判定都能按 alarm_id 追到档位与证据三态；回放集能产出「无需处理」的 precision / recall；改 judge 阈值必须跑回放。

#### P0-2 证据面单源：只有访问日志，业务结果不可见

- **缺失**：`LogSource` 只定义 latency_stats / request_detail / business_impact 三个方法，全部来自 SLS 访问日志（`src/rca/sources/base.py:16`）；没有 Prometheus 指标、没有 trace、没有变更/发布事件。业务失败常返回 HTTP 200，日志看不出成败；规格要求「业务成功率未知」的标注，渲染层也没有（`src/rca/render.py:129`）。
- **影响**：会漏判。典型路径：应用日志里没有 5xx、耗时也正常，但指标侧错误率已上升，或业务成功率已掉；judge 仍可能判「无需处理」。反过来，日志断流时只能落「建议观察」，无法区分「服务挂了」与「日志管道坏了」。
- **补齐的最小代价**：
  1. 给 `LogSource` 加 `metrics_snapshot(uri, start_ts, end_ts)` 与 `recent_changes(start_ts, end_ts)` 两个只读方法；Grafana 侧复用现有 `/api/ds/query` 查 Prometheus，变更事件先读 Grafana annotations（`src/rca/context.py:202` 已有 annotations 读取经验），CI/发布侧后续再补 webhook 写入。
  2. 在 `skills/rca-latency/SKILL.md` 增加 `metrics` 与 `changes` 两条 evidence；新增一个通用 skill 承接非延迟类告警。
  3. judge 增加两条保守规则：指标 5xx > 0 或 p95 超严重阈值 → 至少「建议观察」；无业务结果证据 → 永不判「无需处理」，并在卡片标注「业务成功率未知」。
- **验收**：至少 2 个独立证据源；构造「日志正常、指标 5xx 10%」的样本，judge 不得输出「无需处理」；所有证据的 ok/empty/failed 在卡片可见。

#### P0-3 告警风暴与覆盖漏洞：无聚合/抑制，非延迟告警静默退回自由分析

- **缺失**：
  - 入口只按「指纹+状态」去重（`src/services/alert_service.py:191`），不同告警（如同一 URI 的错误率告警 + 耗时告警）会各自起一轮 Agent；没有 correlation key、没有分组、没有抑制。
  - 无匹配 skill 时直接返回原 query（`src/rca/pipeline.py:142`）；规则反查不出 URI 时也不判定（`src/rca/pipeline.py:133`）。这两条都与 `alert-triage` 规格的「通用兜底清单 / 判建议观察」相反。
  - 目前只有一个 `skills/rca-latency/SKILL.md`，覆盖范围实际上只有延迟类告警。
- **影响**：告警风暴下会重复消耗模型、产生多张互相矛盾的卡片；非延迟类告警则完全回到变更前的自由分析——正是这次 change 想消除的「看似合理但不可据此行动」的结论。
- **补齐的最小代价**：
  1. 把 `AlertDedup` 扩成 `AlertGrouper`：增加 correlation key（建议 `folder + uri + 5 分钟桶`，无 uri 时退化为 `alertname`），同 key 的首条触发判定，后续只追加到同一会话/同一卡片，不再起新 Agent 轮；同一 key 已有「需要处理」卡片时，窗口内抑制重复推送。
  2. 新增 `skills/rca-generic/SKILL.md`：evidence 用 stats / detail / history，判定正文明确「该告警类型未覆盖，仅按日志证据判档」。
  3. 按规格改两个分支：无 skill → 走 generic skill；context.uri 为 None → 输出「建议观察」卡片并列出缺失项，而不是 verdict=None。
- **验收**：100 条风暴回放中 Agent 轮次显著少于告警条数（目标 ≤ 20 轮，可按实际调）；同一 correlation key 不出现两条档位矛盾的卡片；非延迟类告警 0 例静默走自由分析。

### P1（不补会限制价值，但不直接造成误判）

#### P1-1 变更/发布事件与 runbook 证据

- **缺失**：RCA 不读部署、发布、配置变更、feature flag 事件；告警会话按规格不挂 KB，runbook 也没有进入证据包。
- **影响**：最常见的根因之一（「刚发版」）只能靠模型猜；「下一步」建议缺少组织内已沉淀的处置步骤。PagerDuty 的 Recent Changes、Rootly 的 deploys/commits/config changes 都说明这是标配信号（[S18][S21]）。
- **最小代价**：先读 Grafana annotations 中的 deploy/change 标记，作为 `changes` evidence 注入 digest 与 judge 的反证；runbook 先落本地 markdown/YAML，按 alertname 匹配，不进通用 KB。
- **验收**：构造「发版后 5 分钟告警」样本，卡片能列出变更事件；runbook 命中时「下一步」引用其步骤。

#### P1-2 周期巡检与趋势

- **缺失**：只有被动告警；没有「没告警但指标在恶化」的巡检，也没有周/日趋势报告。
- **影响**：只能救火，不能提前发现慢漂移；ARMS 的 6 个巡检模块、Holmes 的 operator scheduled health checks、Datadog Watchdog 都把这块作为独立能力（[S16][S12][S22]）。
- **最小代价**：复用现有 `summary_scheduler` 的调度骨架，先做「每日对白名单 URI 跑同一套证据，无告警也出趋势摘要」，推送到同一目标群；不引入新调度框架。
- **验收**：连续 N 天产出巡检摘要；能指出「样本量/慢请求/p95 的日环比」；巡检结果与告警卡片使用同一套证据三态。

#### P1-3 阈值校准与样本策略

- **缺失**：严重耗时 3000ms、业务跌幅 50%、最小样本 20 都是初始值；change 的 Open Questions 明确提出严重阈值需真实故障样本校准、慢请求阈值是否改为相对基线（`docs/openspec/changes/archive/2026-11-04-alert-triage/design.md`）。
- **影响**：阈值偏松会漏判，偏紧会误报；没有回放集就无法系统调参。
- **最小代价**：在 P0-1 的回放集上做网格搜索，记录每组阈值的混淆矩阵；把最终值写进 `src/config/rca.py` 默认值与 `docs/agents/eval-ledger.md`。
- **验收**：每次阈值变更都有回放对比；默认值有样本量、误判率、漏判率支撑。

#### P1-4 事故对象、责任人与处置动作

- **缺失**：没有 incident/group 对象，没有 owner、没有升级、没有「建议动作 → 人工确认 → 执行」的边界；当前只有群卡片与会话。
- **影响**：多告警无法收敛为一个事故；值班人只能看卡片，不能在系统内认领、升级、记录处置。PagerDuty/incident.io/Rootly 的核心价值就在这层（[S18][S19][S21]）。
- **最小代价**：先在告警卡片上增加 group_id / owner / 建议动作（命令或链接，不执行）；再把「执行」做成 allowlist + 人工确认的两步动作，默认关闭。
- **验收**：同一 group 的告警共享一个 group_id；卡片能显示 owner；任何执行类动作都有审批记录，默认不可自动执行。

#### P1-5 告警知识库与复盘自动生成

- **缺失**：告警结论不沉淀为可检索的案例；没有自动复盘。
- **影响**：同类告警下次仍从零调查；Rootly 的 retrospective + action items、Datadog 的 past feedback 都说明「复盘→下次调查」是闭环（[S21][S23]）。
- **最小代价**：P0-1 的 `alert_triage` 表加「人工结论/纠正」字段；结案时生成一段结构化复盘（现象/证据/档位/处置/待办），进独立案例表，不进通用 KB。
- **验收**：每次结案有复盘记录；下次同 alertname 能检索到最近 N 条案例。

### P2（能力补全，不影响当前正确性）

- **P2-1 拓扑/服务图关联**：用服务依赖图把「同一上游」的多告警归并；当前只有 label 级关联。
- **P2-2 自动处置**：重启/扩容/回滚的自动执行；必须先有 P1-4 的审批边界与 allowlist，否则不做。
- **P2-3 多租户/成本/权限**：按机器人、群、告警源隔离数据与模型配额；当前是单租户假设。
- **P2-4 告警判定可解释性审计**：把每次判定的证据快照、模型解释、人工纠正串成可审计时间线；与 P0-1 的表可合并实施。

## 五、路线图：下一步最值的 3 件事

### 第 1 件：让判定可度量（P0-1）

先做表、写一行、出报表、建回放集。没有这一步，后面所有阈值和模型改动都是盲调。

**验收判据**：

- 100% 的判定轮次有结构化记录，可按 alarm_id 追到 tier、reason、每条证据的三态。
- 回放集 ≥ 50 条标注样本，能输出三档混淆矩阵。
- 报表能回答：「无需处理」后 24h 同指纹复燃率、同 URI 1h 5xx 率。
- 改 judge 阈值必须附一次回放对比。

### 第 2 件：堵住风暴与覆盖漏洞（P0-3）

先把「同一根因的多条告警」收敛，再把「没有 skill」从静默自由分析改成显式兜底。

**验收判据**：

- 100 条风暴回放中 Agent 轮次 ≤ 20（可按实际基线调整，但必须可测）。
- 同一 correlation key 不出现两条档位矛盾的卡片。
- 非延迟类告警 0 例静默走自由分析；未覆盖时卡片明确写「该告警类型未覆盖」。
- 规则反查失败时输出「建议观察」卡片，而不是退回自由分析。

### 第 3 件：加第二类证据（P0-2）

优先指标（Prometheus 5xx / p95 / QPS），其次变更事件；两者都能复用现有 Grafana 通道，不需要新基础设施。

**验收判据**：

- 至少 2 个独立证据源（日志 + 指标）。
- 构造「日志正常、指标 5xx 10%」样本，judge 不得输出「无需处理」。
- 无业务结果证据时，卡片必须出现「业务成功率未知」。
- 每个证据源的 ok/empty/failed 在卡片与结构化记录中都可区分。

## 六、来源清单

### 6.1 外部来源

| 编号 | 来源 | URL | 访问日期 | 用途 |
|---|---|---|---|---|
| S1 | Grafana OnCall README（OSS 归档说明） | https://github.com/grafana/oncall/blob/dev/README.md | 2026-11-06 | OnCall 归档时间、IRM 迁移 |
| S2 | Grafana Alerting：Group alert notifications | https://grafana.com/docs/grafana/latest/alerting/fundamentals/notifications/group-alert-notifications/ | 2026-11-06 | Group by、group_wait/interval/repeat |
| S3 | Grafana Alerting：Mute timings | https://grafana.com/docs/grafana/latest/alerting/configure-notifications/mute-timings/ | 2026-11-06 | 静默只抑制通知、不停止评估 |
| S4 | Keep README | https://github.com/keephq/keep/blob/main/README.md | 2026-11-06 | 平台定位与能力清单 |
| S5 | Keep：Deduplication | https://docs.keephq.dev/overview/deduplication | 2026-11-06 | partial/full 去重、fingerprint fields |
| S6 | Keep：AI Correlation | https://docs.keephq.dev/overview/ai-correlation | 2026-11-06 | 5–15 分钟周期、Cloud/Enterprise 限定 |
| S7 | Keep：Manual Correlation Rules | https://docs.keephq.dev/overview/correlation-rules | 2026-11-06 | 规则关联、incident-candidate |
| S8 | Keep：Workflows | https://docs.keephq.dev/overview/workflow-automation | 2026-11-06 | trigger/action/step、auto-remediation |
| S9 | Keep：AI in Workflows | https://docs.keephq.dev/overview/ai-in-workflows | 2026-11-06 | AI 作为 workflow step、第三方 LLM |
| S10 | Keep：Extraction enrichment | https://docs.keephq.dev/overview/enrichment/extraction | 2026-11-06 | 正则抽取富化 |
| S11 | HolmesGPT README | https://github.com/robusta-dev/holmesgpt/blob/master/README.md | 2026-11-06 | agentic loop、告警集成、operator |
| S12 | HolmesGPT Docs / Built-in Toolsets | https://holmesgpt.dev/latest/data-sources/builtin-toolsets/ | 2026-11-06 | 工具源清单 |
| S13 | HolmesGPT Evaluations | https://holmesgpt.dev/latest/development/evaluations/ | 2026-11-06 | 150+ evals、回归与周度全量 |
| S14 | k8sgpt README | https://github.com/k8sgpt-ai/k8sgpt/blob/main/README.md | 2026-11-06 | analyzers、operator、无自动处置 |
| S15 | 阿里云 SLS：基于智能合并的告警降噪管理 | https://help.aliyun.com/document_detail/461732.html | 2026-11-06 | MinJoin/向量聚类、98% 降噪示例 |
| S16 | 阿里云 ARMS：Inspection configuration | https://help.aliyun.com/en/arms/application-monitoring/user-guide/inspection-configurations | 2026-11-06 | 6 个巡检模块、根因分析 |
| S17 | 阿里云 CloudMonitor：Configure a basic alert rule（文档页位于 CPFS 产品下） | https://help.aliyun.com/en/cpfs/cpfsnextgen/user-guide/create-alarm-rules | 2026-11-06 | 静默期、联系人组、Auto Scaling/SLS 动作 |
| S18 | PagerDuty AIOps | https://support.pagerduty.com/docs/aiops | 2026-11-06 | 分组类型、Probable Origin、Recent Changes、行为学习 |
| S19 | incident.io：Investigations | https://incident.io/ai | 2026-11-06 | agentic RCA、adversarial agent、past incidents |
| S20 | incident.io：On-call | https://incident.io/on-call | 2026-11-06 | 40+ 告警源、按团队/服务路由 |
| S21 | Rootly AI SRE | https://rootly.com/ai-sre | 2026-11-06 | telemetry/deploy/commit/config 关联、置信度、复盘 |
| S22 | Datadog Bits Investigation | https://docs.datadoghq.com/bits_ai/bits_investigation.md | 2026-11-06 | 自动调查、假设迭代、自动触发 |
| S23 | Datadog：Improve Bits Investigation Accuracy | https://docs.datadoghq.com/bits_ai/bits_investigation/improve_accuracy.md | 2026-11-06 | bits.md/runbook/past feedback 作为知识源 |
| S24 | Sentry Seer | https://docs.sentry.io/product/ai-in-sentry/seer/ | 2026-11-06 | Autofix、RCA、PR 创建、外部 coding agent |

### 6.2 本仓来源

- `docs/openspec/specs/alert-triage/spec.md`：三档判定、证据三态、上下文反查、业务影响基线。
- `docs/openspec/specs/alert-notify/spec.md`：webhook 鉴权、去重、两段推送、恢复、兜底、会话隔离。
- `docs/openspec/specs/ops-log-tools/spec.md`：只读日志工具的参数、白名单、脱敏。
- `docs/openspec/specs/bot-agent-routing/spec.md`：入口路由、能力档、渠道可见性。
- `docs/openspec/changes/archive/2026-11-04-alert-triage/design.md`：Goals/Non-Goals、D1–D18、Risks、Open Questions。
- `docs/openspec/changes/archive/2026-11-06-bot-agent-routing/design.md`：profile / 能力档 / MCP 启动期校验。
- 代码路径与行号已在 §1、§4 内逐条标注。

### 6.3 未验证清单

- **incident.io 的告警去重/分组/抑制**：官方页面只确认 alert routing 与 Investigations，未找到 grouping 文档，标「未验证」。
- **Rootly 的告警去重/分组/抑制**：官方页面只确认 AI SRE 与事故生命周期，未找到分组/抑制文档，标「未验证」。
- **Grafana IRM 的 AI RCA 与自动处置**：本次只读到 on-call/分组/事故时间线，未确认 AI RCA，标「未验证」。
- **Datadog Bits 的审批边界**：只确认 Bits Remediation / Workflow Automation 产品名，未确认审批机制，标「未验证」。
- **Keep 的周期巡检**：文档确认告警/关联/工作流，未确认定时巡检，标「未验证」。
- **k8sgpt 的评测集**：README 未提，标「未验证」。
- **阿里云 SLS 的「去重/静默/路由」细节**：来自降噪文章中的链路描述，未逐项读各功能文档，标「部分」。
