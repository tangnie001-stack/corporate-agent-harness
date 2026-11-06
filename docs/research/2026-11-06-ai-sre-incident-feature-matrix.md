# AI SRE / 线上错误分析产品功能调研

> 调研日期：2026-11-06 ｜ 对象：Datadog Bits、PagerDuty AIOps、New Relic AI、Azure SRE Agent、Rootly AI SRE、incident.io Investigations、HolmesGPT、OpenRCA ｜ 对照物：`docs/openspec/changes/trace-diagnosis/`（proposal / design / tasks）
>
> **性质**：外部事实的冻结记录，不随本仓代码回写。文中「未核实」= 官方文档未写明，不做推断。

## 1. 结论先行：我们缺的三件事

1. **变更事件没有进证据链。** 8 个产品里 6 个把「部署 / 配置 / 开关 / 代码变更」作为一等证据（Datadog 的 Change Tracking、Azure 的 GitHub 部署关联、Rootly 的 deploys/commits/config、PagerDuty 的 Recent Changes、incident.io 的 code changes、HolmesGPT 的 Git 工具面）。我们的 `root_cause` 里有 `kb_stale` 这个枚举，但证据源只有 trace + 日志 + 重检，**判不了"是不是刚重建过 KB / 刚换过 prompt / 刚切过模型"**。
2. **诊断结论不回流。** Datadog 的 feedback→memories、Azure 的 retained context、Rootly 的 Memory 都形成「上次怎么判、这次先查什么」的复用层；Rootly 还明确了边界——**记忆只作线索，必须用实时证据复核，不能当证据**。我们的报告落库 180 天、有 `confirmed` 字段，但没有任何回灌路径，第 100 次诊断和第 1 次一样从零开始。
3. **只验"根因说得对"，不验"修了有没有用"。** 我们的闭环是离线 15~20 条回归集、一致率 ≥80%（`tasks.md:69`）；线上没有「建议被执行→回读指标/同类 trace→标记有效/无效」。incident.io 每日对历史事件回测、HolmesGPT 的 deployment verification、Azure 自主模式动作后回报状态，都是这一环。

**我们的相对优势**：证据引用是硬约束（`evidence_id` + validator + 校验失败降级，`design.md:108`），且把「无法判定」做成真实出口（`design.md:127`、`design.md:140`）。这两点比多数厂商文档里写的更严——Datadog、incident.io 有过程可视与来源链接，但未见"断言必须引用存在 id，否则降级"的机制。

## 2. 调研口径与限制

- 只取厂商官方文档 / 官方产品页 / 开源 README / 论文；营销页（incident.io、Rootly 产品页）只用于确认功能存在，不用于确认效果数字。
- 四个必答维度：**输入是什么 / 输出是什么 / 是否自动执行 / 是否可回归验证**。产品未提供回归手段的，写"未核实"，不把"我们能拿它做评测"混同为"它支持回归"。
- 产品能力随版本变化快；本表是 2026-11-06 的文档快照。

## 3. 产品事实卡（输入 → 输出 → 自动执行 → 可回归）

| 产品 | 输入 | 输出 | 自动执行 | 可回归验证 |
|---|---|---|---|---|
| **Datadog Bits Investigation** | 告警（monitor 进入 alert 可自动触发；warn/no data/renotify 不触发）、Slack 消息（线程即上下文）、Error Tracking / RUM / Synthetic 详情页、自由提问 | 证据支撑的结论或显式 inconclusive；Investigation Steps / Hypothesis Tree 两种过程视图；影响面摘要 | 调查全自动；修复侧为建议命令、一键 K8s 动作（Preview）、自动生成修复代码并由人提 PR 审阅，Guardrails 按动作/环境/服务设审批档 | 结论对错可反馈并沉淀为 memory；官方另有评测平台文章，但**修复后效果回读未在文档中说明** |
| **PagerDuty AIOps** | Events API 告警流（默认 120 事件/分钟/键，可提至 10,000） | Noise Reduction；Triage & RCA 四件套：Probable Origin（最多 3 个候选 + 百分比可能性）、Related / Outlier / Past Incidents、Recent Changes | 分组、抑制、富化、路由、动态升级自动执行；**不做修复执行** | 无评测/回归机制说明（未核实）；计费按事件消费，50/80/90/100/150% 阈值告警 |
| **New Relic AI** | 平台内提问、NRQL 控制台、日志/错误详情页的 Explain this error、ServiceNow Now Assist、MCP | 自然语言解释、图表、NRQL、错误摘要 | 未见执行动作（未核实） | 未见回归机制（未核实） |
| **Azure SRE Agent** | 告警（response plan）、定时任务、自然语言提问；Azure Monitor / App Insights / Log Analytics / GitHub / Azure DevOps / Kusto / MCP | probable cause、缓解建议、预填工单（ServiceNow/PagerDuty/Teams）；grounded、source-cited 的回答 | 有：Review（默认，仅 SRE Agent Administrator 可 Approve/Deny）与 Autonomous 两档，按 response plan / task 配置；工具级 allow/ask/deny；hooks 做治理检查点 | 自主模式示例回报动作后状态（"memory usage is now normal"）；是否有独立回归集未核实 |
| **Rootly AI SRE** | 告警/事件上的 Start investigation、Slack 请求、auto-run 规则命中；Atlas：知识图谱 + 连接器 + 私有 agent + Memory | 五种结局：根因（高/中/低置信）、contributing factor、inconclusive—needs human（可给 suspected area）、blocked、failed；每条 claim 引用证据，并列出被排除的解释 | 调查自动；**文档只描述"给 responder 审阅的结果"，未见生产变更执行通道** | 复盘与 action item 跟踪；诊断效果的回归机制未核实 |
| **incident.io Investigations** | 告警/事件声明（声明即已跑完第一遍）；日志、代码、部署、历史事件、文档 | 根因假设（每条带置信度）、证据链、blast radius、建议下一步；调查时间线 | 可起草 PR、交接给编码 agent / MCP 工具；**未见自动改生产** | 官方称每日对真实历史事件回测并在退化时告警；证据链可审计 |
| **HolmesGPT（CNCF）** | AlertManager / PagerDuty / OpsGenie / Jira 拉取、CLI、Operator 7×24 巡检；40+ toolset（K8s、云、DB、SaaS、可观测） | 根因分析 + 工具调用过程；可回写 Slack/工单 | 设计上只读并遵守 RBAC；Operator 可开 PR；Kubernetes Remediation MCP 为可选执行面 | Operator 支持 deployment verification（随应用部署健康检查）与定时健康检查 |
| **OpenRCA（论文/基准）** | 335 个故障事件、3 域（Telecom 51 / Bank 136 / Market 148）、metrics+logs+traces，68.5 GB / 5.23 亿行 | 故障组件 + 故障原因；Controller-Executor agent 产出工具调用轨迹 | 无（离线基准） | 有：精确/部分正确两档计分；复跑基线精确命中 3.9%~12.5%，部分正确 12.2%~22.4% |

**OpenRCA 的补充证据**：OpenRCA 2.0（500 实例，带逐步因果标注）报告 11 个前沿模型平均只有 20.7% 能还原精确根因集合；76.0% 至少找对一个服务，但只有 61.5% 能把它落到一条可验证的因果传播路径上。另一篇过程级失败分析（1,675 次 agent 运行）把失败归为 12 类，最主要的两类是**编造数据解释**与**探索不完整**，且换模型不消失。这直接支持我们 D1 的选择：窄域、确定性路径比开放式 agent 更可复现。

## 4. 功能矩阵

图例：✅ 有 ｜ ◐ 部分/受限 ｜ ✗ 无 ｜ ? 未核实

| 能力域 | Datadog | PagerDuty | New Relic | Azure | Rootly | incident.io | HolmesGPT | OpenRCA | 本仓 trace-diagnosis |
|---|---|---|---|---|---|---|---|---|---|
| **触发方式** | ✅ 告警自动/手动、Slack、ErrorTracking、RUM、Synthetic；API/Terraform 不支持自动调查 | ✅ 告警摄入即算 | ◐ 手动 chat / 页面按钮 | ✅ 告警 response plan、定时、chat | ✅ 手动、Slack、auto-run 规则 | ✅ 事件声明即预跑 | ✅ 告警拉取、CLI、Operator 常驻 | ✗ 离线跑批 | ✅ 人工 trace_id + 告警定位器（proposal「两个入口」） |
| **上下文采集** | ✅ 15+ 自有产品（metrics/traces/logs/events/change/RUM/profiler/代码）+ 6 三方 | ◐ 近 6 个月历史事件与变更，不采原始遥测 | ◐ 仅 NR 平台内数据 | ✅ Azure Monitor/AppInsights/LogAnalytics/GitHub/ADO/Kusto/MCP | ✅ 知识图谱+连接器+私有 agent+记忆 | ✅ 遥测/代码/部署/历史事件/文档 | ✅ 40+ toolset | ✅ 三模态大样本 | ◐ 单 trace + 7 天应用日志 + 检索快照 |
| **根因判定** | ✅ LLM 假设-验证循环，收敛或显式 inconclusive | ◐ ML 历史共现排序（非 LLM），最多 3 候选+概率 | ◐ LLM 问答解释，无结构化 RCA | ✅ Agent 采集+跨系统关联+probable cause | ✅ 证据优先 5 段：建上下文→量信号→测多因→攻击主因→写裁决；模型不能自行宣布根因 | ✅ Nexus 模型 + adversarial agent 压测假设 | ✅ Agentic loop 多源查询 | ✅ Controller-Executor；准确率低 | ✅ 确定性五段 + 6 枚举 + 三条件判据（D1/D5） |
| **证据与引用** | ✅ 步骤/假设树可视；逐条引用校验未见 | ◐ 概率% + 关联事件链接，无引用约束 | ? | ✅ source-cited answers | ✅ 每条 claim 引证据 + 自动 checker | ✅ 每个假设链回来源 + 时间线 | ◐ 工具轨迹可见，逐条引用未核实 | ◐ 轨迹含工具输出，无引用校验 | ✅ evidence_id + validator + 失败降级（D6） |
| **修复动作** | ✅ 命令建议、一键 K8s（Preview）、生成修复代码提 PR、建事件/派单/工单、Guardrails 审批 | ◐ 事件编排（富化/路由/抑制），非修复 | ? | ✅ Azure CLI 等写操作，Review/Autonomous | ◐ 给建议、复盘 action item；执行通道未见 | ◐ 起草 PR / 交接 agent | ◐ 默认只读；Operator 开 PR；K8s 修复 MCP 可选 | ✗ | ✗ 只出命令文本（D7、proposal 非目标） |
| **验证闭环** | ◐ 结论反馈→记忆；修复后回读未见 | ✗ | ? | ◐ 动作后回报状态；独立回归集未核实 | ◐ 复盘闭环；效果验证未核实 | ✅ 每日历史回测 + 审计时间线 | ✅ 部署验证 + 定时健康检查 | ✅ 基准回归 | ◐ 离线 15~20 条、≥80%；无修复后验证 |
| **知识沉淀** | ✅ bits.md + runbook + feedback→memories（可审可删） | ◐ 历史事件库（被动） | ? | ✅ 保留上下文、技能市场、复盘复用 | ✅ 知识图谱 + Memory（人工复核，只作线索） | ✅ 复盘 + 模式学习 | ◐ runbook/Confluence 可查，无记忆层 | ✗ | ◐ 报告落库 180 天 + confirmed；无回灌 |
| **协作与人机分工** | ✅ Slack 线程上下文、建事件、召唤、审批 | ✅ 值班/升级/事件生命周期 | ◐ chat + ServiceNow | ✅ Teams/工单预填/管理员审批/工具 ACL | ✅ Slack/Teams + 结果审阅 | ✅ Slack 原生 + Scribe 会议记录 + 草稿 | ✅ Slack/Teams 回写（经 Robusta） | ✗ | ◐ 企微卡片 + 登录详情页；无派单/事件 |
| **成本与配额** | ✅ AI Credits 统一计量 | ✅ 按事件计费；速率 120→10k/min | ? | ? | ? | ? | ✅ 自托管，LLM 自付；per-tool 内存/输出预算 | ✗ | ◐ 3 trace/2 轮上限 + cost_ms；无预算熔断 |
| **多服务拓扑** | ✅ APM service map、Network Path、DB Monitoring | ◐ 由历史共现推断服务关系 | ◐ 平台内实体关系 | ◐ 资源与保留的"环境拓扑" | ✅ 知识图谱（服务/基础设施/关系） | ✅ blast radius | ◐ 按 toolset 查资源，无统一图 | ✅ 跨服务分布式 trace | ✗ 单应用单 trace；LLM/精排/向量库不在图内 |
| **不确定性与降级** | ✅ inconclusive 显式 | ? | ? | ◐ 建议+审批，置信表达未核实 | ✅ 5 类结局 + 3 档置信 | ✅ 每假设带置信度 | ◐ "未找到"表达未核实 | ✅ 精确/部分正确分开计分 | ✅ undetermined + 缺失证据清单（D6/D9/D11） |

## 5. 与 trace-diagnosis 的逐项对照

### 5.1 已覆盖（且多数达到业界强度）

| 能力 | 我们的实现 | 对照结论 |
|---|---|---|
| 双入口（人工 + 告警） | 人工 `trace_id`（企微）、告警定位器扫窗口聚类 | 与 Datadog/Azure/Rootly 的"手动+自动"同构；我们多一层"空窗口=未落到应用层"的语义（D9） |
| 三态降级与数据源边界 | `SourceEmptyError` / `SourceFailedError`，failed 不得并入 empty（D9 补充、`tasks.md:50`） | 比多数产品文档更明确；它们通常只讲"调查失败" |
| 根因枚举 + 量纲门控 | 6 类 `root_cause`，`score_kind != rerank` 禁入 `low_relevance`（`tasks.md:26`） | 业界少有把"分数来源"作为判定前置；这是我们的领域特异性优势 |
| 断言引用证据 | `evidence_id` + 校验失败降级（D6、`tasks.md:35`） | 强于 Datadog/PagerDuty/New Relic 的文档口径；与 Rootly 的"模型不能自行宣布根因"同级 |
| 无法判定即出口 | `undetermined` + 缺失证据项清单（`tasks.md:30`） | 与 Rootly 的 inconclusive / Datadog 的 inconclusive 对齐 |
| 只读 | D7：不执行，只出命令文本 | 与 HolmesGPT 默认只读一致；比 Azure/Datadog 保守，是刻意的 |
| 成本上限 | 最多 3 个代表 trace（D4）、最多回溯 2 轮（D3）、`cost_ms` 落库 | 有"条数上限"，缺"金额/ token 预算" |
| 落库与权限 | 报告表 180 天、卡片 + 登录详情页、原文展开留痕（`tasks.md:59`） | 与 incident.io 的审计时间线、Datadog 的详情视图同思路 |

### 5.2 部分覆盖

| 能力 | 现状 | 差在哪 |
|---|---|---|
| 上下文采集 | 单 trace + 7 天日志 + 检索快照 | 缺变更事件、依赖健康、历史同类事件；三家以上产品把这三类当一等证据 |
| 多 trace 关联 | 按 (错误类型, 接口, 错误码) 聚类，取最早代表，上限 3（D4） | 只做"同告警内去重"，没有跨告警/跨时间的同因合并；PagerDuty 的 Related/Past Incidents 是跨事件检索 |
| 知识沉淀 | 报告表 + `confirmed` 字段 | 没有"已确认结论→下次先验"的读取路径；也没有 runbook 注入 |
| 验证闭环 | 离线回归集一致率 | 没有线上持续回测，也没有修复后效果回读 |
| 协作 | 群卡片 + 详情页 | 无派单/认领/事件升级；只读阶段可接受，但"谁在处理"无处记录 |
| 拓扑 | 无 | 单应用可接受，但"上游 LLM 超时"与"检索无相关内容"在报告里无法区分 |

### 5.3 缺失

- **变更关联**：没有 deploy / 配置 / prompt 版本 / 模型版本 / KB 重建事件源。
- **竞争假设与置信度**：只输出单个枚举，边界样本无法表达"两个都像"。
- **修复后效果回流**：`next_actions` 执行后无回读。
- **成本预算/熔断**：只有条数上限，没有 token/金额预算。
- **跨 KB / 跨环境聚合**：proposal 明确列非目标，本期不算缺口。

## 6. 差距清单

### 6.1 值得补

**P0-1 变更事件源（ChangeSource）**
- 输入：部署记录、配置发布、prompt 版本切换、模型切换、KB 重建完成事件（含时间戳、影响对象、操作者）。
- 输出：一条可引用的证据「变更 X 发生在症状前 N 分钟，影响对象与本次 trace 的 KB/模型一致」。
- 自动执行：否，只作证据与建议。
- 回归：固定 trace 集补"变更窗口"标注，验证新增证据是否提升一致率、是否引入假阳性。
- 理由：这是 8 家中 6 家的标配，也是我们 `kb_stale` 目前"有名字没判据"的直接原因；RAG 答错的高频外因就是 prompt/模型/KB 变更。改动只在证据源与枚举，不动 D1 的确定性内核。

**P0-2 诊断知识沉淀（lead-only 记忆层）**
- 输入：`confirmed=true` 的报告（症状特征、root_cause、next_actions、有效期）。
- 输出：相似症状出现时，报告里多一段「历史同类：上次判 X，动作 Y，结果有效/无效」。
- 自动执行：否；只影响"先查什么"，不改变判定。
- 回归：A/B 跑同一回归集，比较一致率与平均耗时。
- 理由：Datadog memories、Rootly Memory、Azure retained context 都证明这是提效主线。**必须照 Rootly 的边界**：记忆是线索，必须用实时证据复核，不能当证据——否则会污染我们最值钱的引用约束。

**P1-3 修复后效果回流**
- 输入：人工标记"已执行/未执行"+ T+1 的同类 trace 成功率、精排分数、点踩率。
- 输出：报告新增 `outcome: effective | ineffective | unknown`，并进入下一轮记忆。
- 自动执行：否，先人工两按钮，不做自动推断。
- 回归：按月统计"建议有效率"，替代只看根因一致率。
- 理由：incident.io 每日回测、HolmesGPT deployment verification、Azure 动作后回读都指向同一件事——**根因对不等于修复有效**。

**P1-4 竞争假设 + 三档置信度**
- 输入：现有 judge 的规则得分与证据完整度。
- 输出：top-2 候选 + 置信档（高/中/低），证据不足仍是 `undetermined`。
- 自动执行：否。
- 回归：回归集加"第二候选是否正确"的标注。
- 理由：Rootly、incident.io、PagerDuty 都给候选排序与置信度；单枚举在"检索降级 + 上游超时"并存时会过曝。可在 D1 内实现，不需要放开 agent 自由探索。

**P2-5 依赖级观测（非服务图）**
- 输入：本 trace 内 LLM 调用、精排、向量库、DB 的 span/耗时/错误。
- 输出：把"上游超时导致答错"与"检索无相关内容"分开的证据。
- 理由：现在 6 类枚举里没有"上游故障"，容易被误判成 `low_relevance`。只做单 trace 的依赖调用，不做全量服务图，成本可控。

**P2-6 成本预算与熔断**
- 输入：每轮诊断的 token/调用数上限。
- 输出：超限时降级为"证据清单 + 未判定"，并记录 `budget_exceeded`。
- 理由：Datadog 按 AI Credits、PagerDuty 按事件计费，说明成本是产品级约束；我们只有条数上限，告警风暴时仍可能超支。

**P2-7 报告行动化（建工单/派单）**
- 理由：Datadog triage actions、Azure 预填工单、Rootly/incident.io 在 Slack 内闭环。但我们已有告警通道与工单体系，重复度高，放最后。

### 6.2 不必补

| 不做 | 理由 |
|---|---|
| **自动执行修复** | D7 与 proposal 非目标已定；诊断价值 90% 在定位，执行代价是二次事故。业界即便能做（Datadog 一键、Azure Autonomous）也都配了 Guardrails/审批，我们没有人机审批基建，先不碰。 |
| **开放式 agent 自由探索** | D1 已论证不可复现；OpenRCA 的 335 例上精确命中仅 3.9%~12.5%，失败主因是编造数据解释与探索不完整。窄域确定性流水线是对的选择，不要为了"像 agent"而放弃可回归。 |
| **全量多服务服务图** | 本系统是单体应用 + 单 trace；建图成本高、收益低。依赖级观测（P2-5）足够覆盖"上游超时"这一类。 |
| **7×24 主动巡检 / Operator** | 告警侧已有 `alert-triage` 与告警通道，重复建设；HolmesGPT Operator 的前提是 K8s 多服务环境，我们不适用。 |
| **会议记录 / 沟通自动化（Scribe 类）** | 属协作工具职责，不在本系统数据域。 |
| **大规模 MCP 数据源接入** | D12 已给触发条件（>7 天窗口 / 多副本 / 集中审计）；现在接是过度设计。 |
| **自然语言入口** | proposal 二期；先稳 `trace_id` 与证据链。 |
| **跨 KB 聚合统计 / 同类聚类** | 明确非目标；当前样本量不足以支撑统计结论。 |

## 7. 待确认项

1. Datadog Guardrails 的完整档位枚举（文档在 "Ask" 处截断，Allow/Deny 的具体命名未核实）。
2. New Relic 是否有独立于「观测助手」的结构化事件 RCA / 执行能力（现有官方文档只覆盖 chat、NRQL、错误解释、MCP）。
3. Rootly / incident.io / Azure 是否存在可对外复核的诊断准确率数据（现只有产品页自述与"每日回测"说法）。
4. 各产品的单次调查成本上限（除 Datadog AI Credits、PagerDuty 事件计费外均未核实）。

## 8. 来源

| # | 来源 | 一句话 |
|---|---|---|
| 1 | https://docs.datadoghq.com/bits_ai/bits_investigation.md | Bits Investigation 总览：自主假设-验证、证据支撑结论或 inconclusive |
| 2 | https://docs.datadoghq.com/bits_ai/bits_investigation/investigate_issues.md | 触发入口、Auto-Investigate 限制、支持的数据源、过程视图 |
| 3 | https://docs.datadoghq.com/bits_ai/bits_investigation/improve_accuracy.md | bits.md / runbook / feedback→memories 的调优路径 |
| 4 | https://docs.datadoghq.com/bits_ai/bits_investigation/knowledge_sources.md | 四类知识源与 memory 的选择性复用 |
| 5 | https://docs.datadoghq.com/bits_ai/bits_remediation.md | 修复建议、一键 K8s（Preview）、生成修复代码提 PR、Guardrails 审批 |
| 6 | https://docs.datadoghq.com/account_management/billing/ai_credits.md | AI Credits 计量口径与计费产品清单 |
| 7 | https://docs.pagerduty.com/ai-automation/aiops | AIOps 五大能力集、事件计费与速率上限 |
| 8 | https://support.pagerduty.com/main/docs/probable-origin | Probable Origin：近 6 月历史模式、最多 3 候选、百分比可能性 |
| 9 | https://docs.newrelic.com/docs/agentic-ai/new-relic-ai/ | New Relic AI 官方定位：chat / NRQL / 错误解释 / MCP |
| 10 | https://learn.microsoft.com/en-us/azure/sre-agent/overview | Azure SRE Agent 的上下文、五类扩展点、治理与保留上下文 |
| 11 | https://learn.microsoft.com/en-us/azure/sre-agent/run-modes | Review 与 Autonomous 两档、按 response plan 配置、管理员审批 |
| 12 | https://docs.rootly.com/ai/ai-sre/overview | Rootly 调查五段、五类结局、三档置信、模型不能自行宣布根因 |
| 13 | https://docs.rootly.com/ai/atlas/overview | Atlas 上下文层：知识图谱、Memory、连接器、私有 agent |
| 14 | https://incident.io/investigations | incident.io Investigations：adversarial agent、证据链、blast radius、每日回测 |
| 15 | https://github.com/robusta-dev/holmesgpt | HolmesGPT README：agentic loop、40+ toolset、只读设计、Operator 模式 |
| 16 | https://arxiv.org/abs/2606.27154 | OpenRCA 2.0：500 实例、20.7% 精确还原、76.0%/61.5% 的 grounded 差距 |
| 17 | https://arxiv.org/abs/2602.09937 | OpenRCA 基准细节（335 例/68.5GB）与 12 类 agent 失败模式、基线准确率 |
