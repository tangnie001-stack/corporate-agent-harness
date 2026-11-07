# 告警运维闭环与服务可用性/扩展性调研

> 成文日期：2026-11-07
> 范围：本仓告警链路（`src/services/alert_service.py`、`src/rca/`、`src/channels/wecom/`）的闭环完整度、高可用/可观测/可扩展缺口，以及企微长连接的扩容边界。
> 方法：本仓事实逐条读代码与规格并标行号；外部事实只取官方文档、官方仓库文档或厂商官方博客，逐条标来源编号（见 §6）；访问日期均为 2026-11-07。无法从一手来源确认的写「未验证」。
> 与既有文档的关系：上一份对标报告 `docs/research/2026-11-06-aiops-alert-triage-gap-analysis.md` 已给出能力对照与 P0/P1 清单，本文**不重复其结论**，只在 §3 引用其编号（P0-1/P0-2/P0-3）；需求池 H-14~H-20 已登记的事项同样只引用不重述。
> 边界：本文是外部事实与可执行清单，不是选型决定；决定应另落 ADR / change。

## ① 结论摘要

1. **闭环不是六段都从零建**。本仓已完整拥有「检测（Grafana 侧）→ 接入 → 去重 → 分诊（三档 + 卡片）」；**诊断只有单源证据**；**处置为零**；**验证只是"恢复通知"而非"处置后验证"**；**学习为零**。
2. **runbook 不是闭环拓扑上的独立段，而是跨两段的工件**：在**诊断**段它是证据/知识源（incident.io 会读 runbooks，Datadog 明确把 runbook 列为知识源）；在**处置**段它是可执行步骤（PagerDuty Runbook Automation / Rundeck、Rootly workflows）。所以「runbook 属哪一环」的准确答法是**诊断 + 处置**，不是单独一环。
3. **runbook 是否闭环必需**：**不是形式必需，是质量必需**。没有 runbook，detect→triage→diagnose→人肉处置→verify→learn 仍可成立；但诊断拿不到组织知识、处置不可复制，闭环每轮从零开始。对我们这种"一个告警一个 skill"的结构，runbook 是成本最低的知识注入点（§2.3）。
4. **badcase→评测集→回归→规则/阈值/prompt 修正属于「学习」段**。经典 SRE 对学习段的**必需项**是"有 owner、有期限、有可验证终态的行动项"（S4/S5）；**评测集是 AI 系统实现"可验证终态"的载体**（HolmesGPT 用 150+ evals 在每次提交跑回归，S8）。对我们的"代码定档"设计，**评测集是必需而非加分项**——否则改 `judge` 阈值无法证明没有回归。
5. **badcase 采集渠道我们 4 条全无**：告警卡片走 `send_message`，不带 feedback（`src/services/wecom_service.py:258` → `long_connection.py` 的 `send_message` 无 feedback 参数）；无"判无需处理后 24h 复燃"回看；无抽检；无静默能力（因此"静默后仍告警"这条无从谈起）。当前唯一的 badcase 来源是群里有人不满意——不可统计。
6. **入口没有闸门**：每条通过去重的告警直接 `asyncio.create_task`（`src/services/alert_service.py:383`），无信号量、无有界队列、无丢弃计数；去重器是进程内单例（`:288`），重启即清零、多实例各算各的。
7. **可扩展边界已经很清楚**：无状态的是"解析/判定/渲染"；有状态的是"企微长连接（每 bot 一条）、SSE 事件缓冲/任务注册表（进程内）、去重窗（进程内）"。前三者决定：**webhook 可以水平扩，企微通道不能按 bot 双活，推送必须由持有连接的那个进程执行**。
8. **企微长连接官方约束已核实**：**每个智能机器人同一时间只能保持一个有效长连接；新连接完成订阅会踢掉旧连接；官方建议高可用用主备切换而非同时多连接**（S1）。因此扩容只能**按 bot 分片**，单 bot 提吞吐只能**连接内异步化（队列 + worker 池）**，不能加连接。故障转移靠**租约 + 心跳 + 接管**，并必须假定"两台同时连 = 互踢"。

## ② Q1 主流「闭环」长什么样

### 2.1 六段的工具归属

> 说明：「检测→响应→学习」是厂商官方叙述的 incident lifecycle（Rootly 文档把 AI 覆盖写成 "triage, root cause analysis, response, comms, retros"，S13；Google 的 incident management guide 用 "Remediate and learn from incidents"，S6）。下表把它工程化为六段，是本文的对齐口径，不是某个标准的原文。

| 段 | 目标 | 主流做法 | 代表实现 | 来源 |
|---|---|---|---|---|
| 检测 detect | 从指标/日志/链路产生告警实例 | 规则周期评估；每条规则可产生多实例；只有 firing/resolved 进入通知 | Grafana Alerting 规则评估、告警实例 | S2 |
| 分诊 triage | 不淹人：去重、分组、抑制、静默、路由、升级 | 去重/分组/路由/静默/抑制是告警网关的核心职责；按 label 分组；抑制"已知更大故障"下的从属告警 | Prometheus Alertmanager、Grafana notification policies、PagerDuty AIOps | S3、S2、S22（转引） |
| 诊断 diagnose | 形成根因假设并用证据验证 | agentic 循环：多源取数 → 假设 → 反证 → 结论 + 证据 + 下一步；runbook/历史事故作为知识源 | HolmesGPT、incident.io Investigations、Rootly AI SRE、Datadog Bits | S22（转引）、S10、S13、S14 |
| 处置 act | 执行修复/缓解 | 触发器 + 条件 + 步骤的 workflow；或 runbook automation（诊断 + 修复）；人工触发或条件触发 | PagerDuty Incident Workflows、Rundeck、Rootly workflows、incident.io workflows | S15、S16、S17、S12、S11 |
| 验证 verify | 确认处置有效、故障真的消失 | 部署后健康检查并 gate CI/CD；行动项必须有"可验证终态" | HolmesGPT Deployment Verification；SRE 行动项标准 | S9、S5 |
| 学习 learn | 把一次事故变成系统改进 | 无责复盘 + 行动项（owner/优先级/可验证终态）→ 回灌 backlog；AI 侧：反馈→memories、评测集回归 | Google SRE postmortem、Datadog feedback & memories、HolmesGPT evals、Rootly AI in Retrospectives | S4、S5、S6、S14、S8、S13 |

### 2.2 逐段要点（只写与我们判断相关的）

**检测**：Grafana 的规则评估是"周期执行查询 → 条件满足则实例 firing → firing/resolved 才发通知"（S2）。Silence / mute timings 只暂停通知，**不停止规则评估**（S2）——这条对"静默后仍告警"这类 badcase 渠道很重要：静默只能从通知侧取证，不能从规则侧取证。

**分诊**：Alertmanager 的职责原文是 "deduplicating, grouping, and routing them to the correct receiver… It also takes care of silencing and inhibition"（S3）。分组的存在理由是"一次大故障会产生成百上千条告警，人只需要一条 page，但仍要看 affected instances"（S3）。抑制是"某些告警已在 firing 时，压掉另一些告警的通知"（S3）。这三件事（去重/分组/抑制）+ 静默，构成"事件收敛"层；我们目前只有最窄的一件（同指纹同状态短窗去重）。

**诊断**：incident.io 的 Investigations 在事故声明时收集"告警与栈、相似历史事故、频道讨论与变更事件、**你的 runbooks**、最近代码变更、telemetry"，形成假设后"读代码、查 dashboard 以确认或排除"，最后发根因 + 证据 + 下一步（S10）。Datadog Bits 的知识源原文列三条："Runbooks: Step-by-step troubleshooting guidance / bits.md: Context about your environment / Feedback and memories: Learnings from investigations"（S14）。Rootly AI SRE 的官方描述是"across your connected evidence, tests likely causes, and recommends next steps"（S13）。共同点：**诊断不是"再调一次模型"，而是"证据面 × 知识源 × 可证伪的假设"**。

**处置**：PagerDuty 的 Incident Workflows 是 trigger / conditions / actions 三段式，支持自动触发（Incident Type、Conditional）与人工触发（S15）；其 Runbook Automation 文档已外链到 Rundeck（S16），Rundeck 的定位是 "Automated Incident Response — Diagnostics and Remediation"（S17）。Rootly 的 workflows 同样按 trigger → run conditions → ordered actions 执行，且能"create or update retrospective documents and templates after resolution"（S12）。**处置与学习可以共用同一套 workflow 引擎**，这是主流产品的做法。

**验证**：HolmesGPT Operator 的 Deployment Verification 原文是"Deploy a HealthCheck alongside your app to verify the new version is healthy — and gate CI/CD on the result"（S9）。SRE 对行动项的要求是"measurable：有可验证的终态"（S5）。注意：**Grafana 的 resolved 通知只是"条件不再满足"（S2 的告警状态语义），不是"处置有效"**；两者不能混为一谈。

**学习**：SRE 的定义是"postmortem 是事故的书面记录：影响、缓解/解决动作、根因、以及防止复发的后续动作"（S4）；工作簿进一步给出行动项的四条标准：有 owner、有优先级、**可测量（verifiable end state）**、且分 Prevent/Mitigate 两类（S5）。Datadog 把学习做成产品机制：调查结束后让人标注结论是否正确，**所有正反馈 + 带细节的负反馈会生成 memory**，后续调查动态选用（S14）。HolmesGPT 把学习做成工程闸门：150+ evals，"we also use the evals as regression tests on every commit"（S8）。

### 2.3 专项回答：runbook 与 badcase 评测属于哪一环？是否必需？

**（1）runbook：属「诊断 + 处置」，不是独立段。**

- 诊断侧：incident.io 明确把 runbooks 列入调查输入（S10）；Datadog 明确把 runbook 当知识源，并说明"Bits reads the page, extracts telemetry links, follows documented troubleshooting steps where possible, and incorporates remediation guidance into its recommendations"（S14）。它提升的是**假设质量**与**下一步的可执行性**。
- 处置侧：PagerDuty Runbook Automation / Rundeck 把 runbook 变成可执行自动化（S16/S17）；Rootly/PagerDuty 的 workflow 把步骤编排成 trigger→action（S12/S15）。
- **是否必需**：不是拓扑必需。闭环的六段可以没有 runbook 而成立（人靠经验处置）。但它是**质量必需**：没有它，诊断缺组织知识、处置不可复制、值班换人即失忆。判断标准很简单——**同一告警第二次发生时，系统是否比第一次知道得更多**；runbook 是让答案变成"是"的最低成本手段。
- 对我们的含义：`skills/rca-*/SKILL.md` 目前只回答"怎么判"（证据清单 + 判定顺序 + 禁止项，`skills/rca-latency/SKILL.md`），**不回答"怎么修"**；把它当 runbook 会混淆职责（详见 §3.4）。

**（2）badcase→评测集→回归→规则/阈值/prompt 修正：属「学习」段，且是我们的必需项。**

- 经典 SRE 的必需项不是"评测集"，而是"**可验证终态的行动项**"（S5）。行动项可以是改代码、改配置、改流程；只要能被验证。
- AI 诊断系统把"可验证终态"落在**评测集 + 回归**上：HolmesGPT 150+ evals、每次提交跑回归、周度全量（S8）。这是目前唯一能从一手来源确认"AI 诊断质量被持续度量"的公开做法。
- 对我们：档位由代码算（`src/rca/judge.py:126`），模型只写解释（`src/rca/explain.py:94`），这意味着**阈值是行为的一部分**。改 `severe_latency_ms`、`business_drop_ratio`、`MIN_SAMPLES` 都会改变档位分布；没有标注集就无法回答"这次改动让多少条从『无需处理』变成『建议观察』"。因此**评测集是必需**。
- 但顺序不能倒：**先有结构化记录与人工标注，才谈评测集**。现在两者都没有（§3.3），直接建 evals 只会得到一堆无法复现的样本。

**一张表收口**：

| 事项 | 属哪一环 | 是否闭环必需 | 判据 |
|---|---|---|---|
| runbook / 处置手册 | 诊断（知识源）+ 处置（步骤） | **不是形式必需，是质量必需** | 同一告警第二次发生时，系统是否比第一次知道得更多 |
| badcase → 评测集 → 回归 | 学习 | **是（对我们的代码定档设计）** | 改阈值/改 prompt 后，能否用同一批样本证明档位分布没有非预期变化 |

## ③ Q2 我们缺哪些环节

### 3.1 差距表

状态口径：**有** = 端到端可用且有代码/规格；**部分** = 有实现但覆盖面或语义不完整；**没有** = 无实现。

| 环节 | 状态 | 本仓证据 | 缺什么 |
|---|---|---|---|
| 检测 | 有（在 Grafana 侧） | 本仓只接收 webhook：`src/api/alerts.py:21`；规则评估不在本仓 | 无需自建；但 No Data/Error 语义未区分（H-18） |
| 接入/鉴权/解析 | 有 | `src/services/alert_service.py:336`（token、解析、202）；`:73` 起解析 Alertmanager 载荷 | 无请求体大小上限、无限流 |
| 去重 | 部分 | `src/services/alert_service.py:191`（指纹+状态）、`:288`（进程内单例）、`src/config/alert.py:16`（默认 300s） | 跨实例不共享；重启清零；只压短时重复，不解决 repeat_interval / 抖动（H-16） |
| 分组/抑制/静默 | 没有 | 全仓无 correlation key / silence 实现；H-14（通知节流）、H-16（事件状态机）已登记 | 一次 DB 抖动 = N 条 firing + N 条 resolved 各发一条 |
| 分诊（三档） | 有 | `src/rca/judge.py:126`；卡片 `src/rca/render.py:129` | 档位只进卡片文本，无结构化落库（P0-1、H-13） |
| 诊断（证据面） | 部分 | 仅 SLS 访问日志三方法：`src/rca/sources/base.py`；采集 `src/rca/collector.py:228` | 无指标、无 trace、无变更/发布事件（P0-2）；业务失败返回 200 时看不见 |
| 诊断（独立 trace 诊断） | 部分 | `src/rca/diagnose/pipeline.py:23` + `src/cli/diagnose.py`（离线按 trace_id 出报告） | 只在 CLI，未接告警入口；五类根因闭合但依赖 trace 字段上线时间 |
| 处置 act | 没有 | 设计 Non-Goals：不查业务库、不改规则、不自动静默、不自动建单（P0 报告 §1.3） | 无 runbook、无 workflow、无审批边界 |
| 验证 verify | 弱 | 只有 Grafana `status != firing` 时推「已恢复」模板：`src/services/alert_service.py:389` 起 | 恢复通知 ≠ 处置验证；无"修完之后指标回到基线"的回读 |
| 学习 learn | 没有 | 无 `alert_triage` 表；无回灌；无评测集（`tests/rca` 只有单测） | 见 §3.3 四处断点 |
| runbook | 没有 | 全仓只有 `docs/agents/deploy-runbook.md`（发布流程），无告警处置 runbook | 见 §3.4 |
| 自监控 | 没有 | `/api/health` 恒返回 `status=ok`（`src/api/health.py:16-22`）；无 metrics 端点（H-12） | 告警链路自己坏了没人知道；无丢弃/未送达计数 |
| 高可用 | 没有 | 单 `app` 容器、单 uvicorn（`docker-compose.image.yml:208`，无 `--workers`）；去重/缓冲在进程内 | 重启丢去重窗与在途分析；无法滚动发布不丢告警 |
| 水平扩展 | 没有 | 企微每 bot 一个驱动、集中于本进程（`src/services/wecom_service.py:42`）；SSE 缓冲进程内（`src/chat/streaming.py:96-102`） | 见 §4 扩展边界表 |

### 3.2 badcase 采集渠道盘点：4 条，可用 0 条

| 渠道 | 是否有 | 证据 | 说明 |
|---|---|---|---|
| 人工反馈（点踩/纠正） | 无 | 告警推送走 `wecom_service.send_to_session`（`src/services/wecom_service.py:258`）→ `LongConnectionDriver.send_message`，该方法无 `feedback` 参数；`feedback` 只在 `reply_stream` 首帧可挂（`docs/agents/wecom-sdk-facts.md` E10）。站点 `/feedback` 与告警无关（`src/api/feedback.py`） | 卡片没有 👍/👎，也没有"结论不对"的入口 |
| 复燃（判无需处理后再次 firing） | 无 | `src/rca/pipeline.py:57` 的 `collect_history` 是**告警发生前**看近 7 天；没有"判定之后 24h 回看同指纹/同 URI"的作业 | 无法计算"无需处理"的复燃率 = 无法度量核心承诺 |
| 抽检 | 无 | 无结构化档位落库；卡片文本未与 alarm_id 建索引 | 无法按告警名/URI 分层抽样 |
| 静默后仍告警 | 无 | 无 silence/抑制能力（§3.1） | 连"静默期"这个概念都还不存在 |

结论：当前唯一的 badcase 通道是**群里的人开口**，不可统计、不可回归。P0-1 的"复燃率报表"正是补这条。

### 3.3 链路断在哪：四处断点

1. **记录断点**：判定后只把卡片文本当正文落库（`persist_text`，`src/services/alert_service.py:496` 起），没有 tier / reason / 每条证据三态 / 耗时的结构化行；H-13 要求的 trace metadata 也未写。
2. **标注断点**：没有"这条判得对不对"的入口。H-15 指出诊断报告表的 `confirmed` 字段没有读取路径；告警侧连这个字段都没有。
3. **回归断点**：`FixtureLogSource`（`src/rca/sources/fixture.py`）只被单测使用（`tests/rca/test_fixture_source.py:4`），没有"50–100 条历史告警 + gold tier"的回放集；`data/deepeval` 是问答 RAG 评测，与告警无关。
4. **修正断点**：阈值在 `src/config/rca.py`（`severe_latency_ms`、`business_drop_ratio`）与 `src/rca/judge.py:13`（`MIN_SAMPLES`），prompt 在 `src/config/prompts`；改动没有任何"必须附回放对比"的闸门。

**最小闭环（按成本排序，5 步）**：

1. 建 `alert_triage` 表并写一行（alarm_id、trace_id、alertname、folder、uri、tier、reason、evidence_status_json、created_at）。
2. 给告警卡片加"纠正"入口（企微模板卡片按钮，`template_card_event` 的 `event_key`/`task_id` 已实测可用，见 `wecom-sdk-facts.md` E5）或退化为站点表单；写入 `confirmed` 与纠正后的 tier。
3. 出周报：三档分布、"无需处理"后 24h 同指纹复燃率、同 URI 1h 5xx 率。
4. 从周报里冻结 50–100 条样本（含 gold tier），用 `FixtureLogSource` 回放，输出三档混淆矩阵。
5. 把"改 `judge` 阈值 / 改 `explain` prompt 必须附一次回放对比"做成闸门（脚本或 CI）。

### 3.4 runbook 与 `skills/*/SKILL.md` 是什么关系：分层，不合并

| 维度 | `skills/rca-*/SKILL.md` | runbook |
|---|---|---|
| 回答的问题 | **怎么判**（取哪些证据、按什么顺序判、禁止什么） | **怎么修**（步骤、命令、回滚、升级联系人） |
| 形态 | YAML frontmatter（`match` / `evidence`，机器严格解析）+ 短正文 | prose + 命令/链接，面向人 |
| 谁消费 | `src/rca/loader.py` 加载，代码执行 evidence；模型只读 body 作为判定说明 | 值班人；或诊断 agent 作为知识源（S10/S14） |
| 变更影响 | 改它会改判定行为，需要回归 | 改它只改处置指引，不应改判定 |
| 安全边界 | 只读、无凭证、无外部副作用 | 可能含内网地址、命令、账号操作，**不应直接进模型 prompt** |

**结论：分层，不合并。** 合并的三个具体代价：① `loader.py` 对 frontmatter 是严格解析（缺 `match`/`evidence` 即跳过整个 skill，`src/rca/loader.py:80-90` 起），runbook 的散文会污染这个契约；② 改处置步骤会顺带改判定行为，回归归因变难；③ runbook 里的命令/地址进模型上下文，既有注入风险也有泄露风险。

**建议形态**（只定关系，不落实现）：`SKILL.md` 保持判定契约；runbook 独立存放（如 `docs/runbooks/<alertname>.md` 或 `skills/rca-*/RUNBOOK.md`），frontmatter 加可选 `runbook: <id>` 指针；卡片只渲染"有处置手册 + 标题 + 链接"，**不渲染命令原文**；模型只拿到"存在 runbook 及其摘要"，用于生成 `next_actions`。

**与业务侧 `skills/*/SKILL.md` 的关系**：rca-* 是 `user-invocable: false` / `disable-model-invocation: true` 的内部 skill（`src/rca/loader.py:4`），只被 RCA 加载器读取，不出现在问答技能列表；业务 skill 是问答能力。两者语义不同，**不应合并**。

## ④ Q3 高可用 / 可观测 / 可扩展：可执行清单

### 4.1 入口与背压（webhook 侧）

| # | 做什么 | 为什么（本仓证据） | 代价 / 注意 |
|---|---|---|---|
| H1 | **共享幂等**：`SET NX` 键 `fingerprint + status` 到 Redis，TTL ≥ 事件窗（建议 24h，覆盖 repeat 与恢复）；命中返回 202 | 当前去重是进程内单例（`src/services/alert_service.py:288`），重启清零、多实例各算各的；默认窗只有 300s（`src/config/alert.py:16`） | 每条一次 Redis 往返；Redis 不可用时必须定策略（建议 fail-open 并记 warning，不能因 Redis 挂了丢告警） |
| H2 | **入口限流**：nginx `limit_req`（按 token/IP 分 zone）+ 应用侧按 alertname 的令牌桶 | 当前 nginx 对 `/api/alerts` 无限流（`deploy/nginx/nginx.conf` 无 `limit_req`）；一次风暴可打满进程 | nginx 的 leaky bucket 超额会直接报错（S20）；限流会丢告警，必须给恢复通知/高等级告警留旁路 |
| H3 | **有界队列 + worker 池**：`asyncio.Queue(maxsize=N)` + 3~5 个 worker；满则丢弃并记 `[alert] dropped reason=overload` + 计数 | `handle_webhook` 直接 `create_task`（`src/services/alert_service.py:383`），`_pending_tasks` 无上限（`:292`）；每条告警 = 1 轮 Agent + N 个 SLS 查询 | 队列要有优先级：恢复通知 > 高等级 > 低等级（过载时按 criticality 取舍，S7）；丢弃必须可观测，否则等于静默丢单 |
| H4 | **事件状态机**（open/closed）替代纯时间窗去重：稳定键（alertname+service）、抖动需连续 N 次稳定、无对应 open 的恢复忽略 | 去重键是「指纹+状态」，解决不了 repeat_interval 到期再发、恢复早于触发、firing/resolved 交替（H-16） | 需要持久状态（Redis）；"部分恢复"口径已在 H-16 补充中定，照它实现 |
| H5 | **请求体上限 + 解析失败分类计数** | 解析失败仅记 warning + 截断日志（`_log_rejected`），无计数、无上限 | 上限要大于合法载荷（当前 Grafana 载荷较小）；不能把合法大载荷误杀 |

**背压闭环**：入口限流（H2）→ 队列上限（H3）→ 满则丢弃并计数（H10）→ 丢弃率触发自监控告警（H11）。四步缺一不可；只做限流不做计数，等于把「丢了多少」变成未知。

参考基线：Alertmanager 对同类风暴的做法是"按 alertname 限制活跃告警数，超限丢弃新告警、已存在告警的心跳照常处理"，并暴露 `alertmanager_alerts_limited_total`（S3）。这比"无上限 + 全丢"更可取：**保住已存在的事件，只丢新增**。

### 4.2 多实例下的锁与状态外置

| # | 做什么 | 为什么（本仓证据） | 代价 / 注意 |
|---|---|---|---|
| H6 | 去重/事件状态迁 Redis（见 H1/H4） | 同上 | 需要 key 设计（TTL、容量淘汰）；Redis 已是既有依赖 |
| H7 | SSE 事件缓冲/任务注册表二选一：**粘性会话** 或 **Redis Streams 共享** | `src/chat/streaming.py:96-102` 的 `_session_tasks`/`_stream_buffers` 是进程内字典；`src/chat/task_registry.py` 也明确"单 worker 进程内状态" | 粘性会话最简单（按 session_id `ip_hash`），但实例故障即断流；Streams 要重写订阅路径，延迟 +1 RTT，但支持断线重放 |
| H8 | **推送归属**：告警推送必须由持有目标 bot 连接的实例执行（内部 RPC/队列回传），不能任意实例直接 `send_to_session` | `send_to_session` 取本进程 `_drivers`（`src/services/wecom_service.py:258`），驱动不在本进程就返回 False | 分片后这是硬约束；需要一条"执行结果 → 连接器 → reply_stream"的回程通道 |
| H9 | **per-session 锁**：保留现有 Redis SETNX 兜底，但要在共享去重之后 | `src/services/turn_runner.py:227-256`：进程内 `try_reserve` 是权威，Redis 锁只是兜底；同 alarm_id 的第二个实例会拿到 `TurnBusy` 并走兜底转发（`src/services/alert_service.py` 的 `TurnBusy` 分支） | 共享去重做好后，这条基本不会触发；否则群里会出现"分析繁忙"的兜底消息 |

补充：Redis Pub/Sub 是 **at-most-once**，订阅端断线期间的消息永久丢失（S18）；Streams 是持久化、支持 at-least-once（S19）。所以**SSE 跨实例用 Streams 不用 Pub/Sub**，除非能接受断流丢帧。

### 4.3 自监控与可观测

| # | 做什么 | 为什么（本仓证据） | 代价 / 注意 |
|---|---|---|---|
| H10 | **告警链路自身的计数器**：`alert_webhook_total{result}`、`alert_dedup_dropped_total`、`alert_queue_depth`、`alert_dropped_total{reason}`、`alert_analysis_total{result}`、`alert_push_failed_total{reason}`、`alert_analysis_seconds`、`wecom_bot_ready{bot}`、`wecom_displaced_total` | 当前只有日志；`_safe_send` 失败只 warning（`src/services/alert_service.py:594`），无计数；H-12 已确认无 metrics 出口 | 没有 Prometheus 时，最务实的是把这些打成**结构化日志**，由 SLS 做日志告警；等 H-12 解决再接 metrics |
| H11 | **Dead man's switch**：一条"永远在 firing 的合成告警"或外部定时心跳；**心跳缺失**才告警 | 告警链路自己坏了（进程崩、token 错、bot 被顶）时，所有告警都发不出去；Grafana 的 metamonitoring 用同一思路："if anything in the Prometheus → Alertmanager → PagerDuty chain breaks completely, we won't get notified"，用 dead-man's-switch-like 机制兜底（S21） | 合成告警会消耗模型额度；建议 L1 只测 webhook 202 + 日志，L2 每周一次端到端（真的推一张卡） |
| H12 | **依赖感知健康检查**：`/api/health` 至少区分 liveness / readiness（PG、Redis、Grafana、目标 bot 就绪） | 现在恒返回 `status=ok`（`src/api/health.py:16-22`）：DB 挂了、bot 被顶了，健康检查仍绿 | 外部探测频率与超时要定；readiness 不能依赖 LLM（否则抖动导致整站摘流） |
| H13 | **trace 贯穿**：入口 trace 与分析 trace 用同一个 id（或显式父子）；`alert_id`/`alarm_id`/`tier` 写进每行日志与 trace metadata；卡片页脚给 trace_id | `_run_analysis` 用 `new_trace_id()` **覆盖**了请求上下文里的 trace（`src/services/alert_service.py:460-461`）⇒ 受理日志与分析日志是两个 id，无法一条 id 查到底；H-13 的结构化 metadata 也未写 | 改动很小；注意别再新造第三个 id |
| H14 | **RED/USE 映射**：webhook 的 rate/errors/duration；队列与 worker 的 utilization/saturation；bot 连接的 ready/displaced | 当前没有指标口径，排障只能 grep 日志 | 与 H10 同源，别重复埋点 |

**"谁监控监控者"的具体答法**（三层，缺一不可）：
1. **进程内自检**：H10 的计数器 + H12 的 readiness。
2. **外部心跳**：H11 的 dead-man switch；监控方与被监控方**不能是同一个进程**。
3. **独立通道**：告警目标当前是单 bot（`AlertTarget`，`src/config/alert.py:23`），如果这个 bot 被顶/凭证失效，自监控也发不出去——所以自监控必须走**另一个 bot 或另一条通道**（短信/邮件/云监控），不能复用告警 bot。

### 4.4 可扩展边界：哪些能扩、哪些不能

| 组件 | 状态 | 能否水平扩 | 前置条件 |
|---|---|---|---|
| webhook 接收 / 解析 / 三档判定 | 无状态（除去重） | 能 | 共享去重（H1/H6）；入口限流（H2） |
| Agent 生成 | 无状态（LLM 外部） | 能 | per-session 锁已在 Redis 有兜底（H9）；队列与并发上限（H3） |
| SSE 事件缓冲 / 任务注册表 | 进程内 | 不能直接扩 | 粘性会话或 Redis Streams（H7） |
| 企微长连接 | **每 bot 一条，绑定进程** | **单 bot 不能扩**；只能按 bot 分片 | 租约 + 主备（§5） |
| 告警推送 | 跟随连接器 | 不能独立扩 | 推送回程通道（H8） |
| 去重 / 事件状态 | 进程内 | 不能直接扩 | Redis 外置（H1/H4） |

**一句话**：先把"无状态执行"与"有状态连接"拆开，再谈扩。现在的代码把两者放在同一个进程里（`wecom_service` 直接在 app 进程建连），所以任何扩容都会先撞到"连接归属"这堵墙。

## ⑤ Q4 企微长连接的扩容

### 5.1 官方约束（已核实，不是推测）

企业微信官方文档原文：

> **每个智能机器人同一时间只能保持一个有效的长连接。当同一个机器人发起新的连接请求并完成订阅（aibot_subscribe）时，新连接会踢掉旧连接，旧连接将被服务端主动断开。**
>
> 注意：
> - 开发者需要在业务层面避免同一机器人建立多个长连接
> - **如果需要实现高可用，建议采用主备切换模式而非同时多连接**
> - 旧连接被踢掉时，开发者会收到连接断开事件

（S1，2026-11-07 访问）

同一页还写明：订阅请求"有频率保护，订阅成功后应避免反复请求，否则可能触发系统限制"；心跳建议 30 秒（S1）。

本仓实测与官方一致且更细（`docs/agents/wecom-sdk-facts.md` E4，2026-10-08）：被顶时服务端**先推 `event.disconnected_event`**，旧 WS 此刻无异常；随后才关闭旧连接（实测 313s / 2.2s，不确定）；关闭触发 SDK 自动重连，**重连成功会反过来顶掉对方 ⇒ 互踢循环**。驱动已按"收到即主动断开、不抢回"处理（`src/channels/wecom/long_connection.py:35`、`:233`）。

**结论：不能双活。** 任何"两台都连同一个 bot 以提高可用性"的方案都必然互踢；官方给的路只有主备。

### 5.2 扩容模型

1. **按 bot 分片（横向）**：bot 集合是天然分片键。N 台实例各持一部分 bot，每台对每个 bot 仍只有 1 条连接。告警目标当前只配 1 个 bot（`src/config/alert.py:23`），这个 bot 所在分片就是告警链路的吞吐上限——所以要么给告警单独分一个"专用 bot 分片"，要么接受它和该分片的其他 bot 共享进程。
2. **单 bot 内异步化（纵向）**：连接数不能加，只能让"收帧"与"跑 Agent"解耦。SDK 收到帧后经 pyee 的 `ensure_future` 并发派发（已读源码：`pyee/asyncio.py:72-90`），当前 handler 直接 `await` 整轮（`src/channels/wecom/long_connection.py:251` 起）⇒ **同一 bot 的并发轮次无上限**。应改为：收帧 → 入有界队列（按 bot/会话）→ worker 池消费；队列满时立刻回一条"繁忙"流式消息（用入站帧的 `req_id`，`reply_stream` 可用），而不是无限堆积。
3. **不要在连接内做"同步等待"**：`reply_stream` 的 ack RTT 约 0.26s/帧（`wecom-sdk-facts.md` E6），是天然节流；但一轮生成可能持续数分钟（`turn_timeout_seconds` 默认 240s，`src/config/alert.py:18`），所以必须靠队列而不是靠"发帧慢"来限流。

### 5.3 故障转移：租约 + 心跳 + 接管

- **租约**：`SET bot:<id> <instance> NX PX <ttl>`，持有者才允许建连；每 `ttl/3` 续约。key 的 TTL 是"我活着"的唯一凭证。
- **备用不连**：standby 只续约竞争，不建连；拿到租约后才建连。这样任意时刻同一 bot 的连接数 ≤ 1。
- **接管**：主失联 → 租约过期 → standby 抢到 → 建连；旧连接即使还活着，也会被平台踢掉（S1），天然收敛。
- **fencing**：租约里带递增 token（或 instance id + epoch），推送/回复前校验 token，避免"旧主恢复后又写一条"。
- **重连策略要收归 supervisor**：SDK 默认 `max_reconnect_attempts=10`（`aibot/types.py:46`），收到 `disconnected_event` 时我们主动 `disconnect()` 会置 `_is_manual_close`，从而停掉自动重连（`ws.py:544-553`）；但**网络分区导致的关闭**仍会自动重连（`ws.py:338` 起）。建议把重连次数设为有限小值或 0，由"租约仍在我手上"决定是否重连——否则旧实例会在失去租约后继续抢连，制造互踢。注意 `connect()` 会把 `_is_manual_close` 与重连计数复位（`ws.py:116-137`），所以 supervisor 驱动的重连是可行的，但**必须由租约校验来调用**，不能交给 SDK 自动完成。
- **滚动发布**：必须先"释放租约 → 断开 → 新实例抢到租约再连"，不能新旧同时连；否则发布会触发一次互踢（平台会踢旧，但日志会出现 `displaced` 告警，且短时消息可能丢）。已有 `displaced_total` 计数可用于验收（§4.3 H10）。

### 5.4 与我们现状匹配的最小演进路径

**阶段 1：单机加固（不改部署形态，1 个进程）**

- 做：每 bot 有界队列 + worker 池（H3 的 bot 侧版本）；队列满回"繁忙"；`max_reconnect_attempts` 收敛；`wecom_bot_ready`/`displaced_total` 计数；去重窗从 300s 提到覆盖事件窗（H1 的进程内版本）。
- **判据**：100 条告警回放时，峰值并发轮次 ≤ 配置上限、进程内存不持续增长；所有丢弃都有 `dropped` 日志；同一 bot 不再出现"无日志的静默不回复"。

**阶段 2：连接器与执行器分离（仍单机，可多进程）**

- 做：connector 进程只负责"持连 + 收发帧 + 保活"，把入站帧写入队列（进程内队列或 Redis Stream），executor 进程池跑 Agent；回复帧经回程通道回到**持有该帧的 connector**（因为 `reply_stream` 必须用入站帧的 `req_id`，跨进程不能直接调）。
- **判据**：executor 从 1 个扩到 N 个时，企微连接数不变（仍每 bot 1 条）；connector 重启不影响已建立的其他 bot 连接；p95 首帧延迟不因排队而超过约定值；重复回复数为 0（按 `msgid` 幂等）。

**阶段 3：多机分片 + 主备（真正的水平扩展）**

- 做：bot → 实例的分配表（配置或注册中心）+ Redis 租约（§5.3）；告警推送按 H8 走"执行器 → 连接器"回程；入口按 H1/H2 共享去重与限流。
- **判据**：`kill -9` 主实例后，该分片 bot 在 30s 内恢复就绪（租约 TTL + 建连 + 认证）；任意时刻同一 bot 的连接数 = 1（用平台 `displaced_event` 日志与 `wecom_bot_ready` 交叉核验）；一次滚动发布中 `displaced` 次数 = 预期（理想为 0，最多等于发布批次数）；告警端到端时延 p95 不因分片劣化。

## ⑥ 来源清单

### 6.1 外部来源（访问日期均为 2026-11-07）

| 编号 | 来源 | URL | 用途 |
|---|---|---|---|
| S1 | 企业微信开发者中心：智能机器人长连接 | https://developer.work.weixin.qq.com/document/path/101463 | 单 bot 单连接、新踢旧、主备建议、心跳、订阅频率保护 |
| S2 | Grafana：Introduction to Grafana Alerting | https://grafana.com/docs/grafana/latest/alerting/fundamentals/ | 规则评估、告警实例、通知策略、分组、silence/mute 不停止评估 |
| S3 | Prometheus：Alertmanager | https://prometheus.io/docs/alerting/latest/alertmanager/ | 去重/分组/路由/静默/抑制、HA 集群、按 alertname 限流与丢弃计数 |
| S4 | Google SRE Book：Postmortem Culture | https://sre.google/sre-book/postmortem-culture/ | postmortem 定义、触发条件、防止复发的后续动作 |
| S5 | Google SRE Workbook：Postmortem Culture | https://sre.google/workbook/postmortem-culture/ | 行动项四标准：owner、优先级、可测量、Prevent/Mitigate |
| S6 | Google SRE：Incident Management Guide | https://sre.google/resources/practices-and-processes/incident-management-guide | "Remediate and learn from incidents"、行动项回灌 backlog |
| S7 | Google SRE Book：Handling Overload | https://sre.google/sre-book/handling-overload | 按 criticality 分级拒绝（过载时的取舍依据） |
| S8 | HolmesGPT：Evaluations | https://holmesgpt.dev/latest/development/evaluations/ | 150+ evals、每次提交回归、周度全量 |
| S9 | HolmesGPT：Operator Overview | https://holmesgpt.dev/latest/operator/ | Deployment Verification：部署后验证并 gate CI/CD |
| S10 | incident.io：Investigations | https://docs.incident.io/investigations | 证据源含 runbooks、假设-验证、根因+证据+下一步 |
| S11 | incident.io：Workflows | https://docs.incident.io/workflows | 触发器/条件/步骤、测试与运行历史 |
| S12 | Rootly：Workflows | https://docs.rootly.com/workflows/workflows | trigger/conditions/actions、可创建 retrospective 文档 |
| S13 | Rootly：AI | https://docs.rootly.com/ai/ai | 覆盖 triage、RCA、response、retros；AI SRE 调查与建议 |
| S14 | Datadog：Bits Investigation Knowledge Sources | https://docs.datadoghq.com/bits_ai/bits_investigation/knowledge_sources.md | 知识源三件套：runbooks / bits.md / feedback→memories |
| S15 | PagerDuty：Incident Workflows | https://docs.pagerduty.com/ai-automation/automation/incident-workflows.md | 四类触发器、人工/条件触发、动作顺序 |
| S16 | PagerDuty：Runbook Automation | https://docs.pagerduty.com/ai-automation/runbook-automation.md | 该能力文档外链 Rundeck |
| S17 | Rundeck Docs | https://docs.rundeck.com/ | Runbook Automation 定位：自动化事故响应（诊断与修复） |
| S18 | Redis：Pub/Sub | https://redis.io/docs/latest/develop/interact/pubsub/ | at-most-once、断线丢消息 |
| S19 | Redis：Streams | https://redis.io/docs/latest/develop/data-types/streams/ | 持久化、at-least-once、消费组与 PEL |
| S20 | nginx：ngx_http_limit_req_module | https://nginx.org/en/docs/http/ngx_http_limit_req_module.html | leaky bucket、burst、超额报错 |
| S21 | Grafana Labs：metamonitoring Prometheus | https://grafana.com/blog/how-we-use-metamonitoring-prometheus-servers-to-monitor-all-other-prometheus-servers-at-grafana-labs/ | "谁监控监控者"、dead-man's-switch 机制 |
| S22 | 上一份本仓调研（转引） | docs/research/2026-11-06-aiops-alert-triage-gap-analysis.md §6 | Keep / 阿里云 SLS·ARMS / PagerDuty AIOps / HolmesGPT 等产品事实，本文不重复核验 |

### 6.2 本仓来源

- `src/services/alert_service.py`：入口、去重、编排、恢复、兜底、推送。
- `src/rca/pipeline.py`、`judge.py`、`render.py`、`explain.py`、`collector.py`、`loader.py`、`sources/`：判定链路与证据面。
- `src/rca/diagnose/` 与 `src/cli/diagnose.py`：离线 trace 诊断（未接告警入口）。
- `src/channels/wecom/long_connection.py`、`src/services/wecom_service.py`、`docs/agents/wecom-sdk-facts.md`：长连接驱动、被顶处理、SDK 实测。
- `src/chat/streaming.py`、`src/chat/task_registry.py`、`src/services/turn_runner.py`：进程内状态与 per-session 锁。
- `src/config/alert.py`、`src/config/rca.py`、`src/api/health.py`、`deploy/nginx/nginx.conf`、`docker-compose.image.yml`：配置与部署形态。
- `docs/openspec/specs/alert-triage/spec.md`、`alert-notify/spec.md`：既有契约。
- `docs/agents/requirements_pool.md` H-12~H-20：已登记的通知治理、状态机、No Data、trace、DB 证据。

### 6.3 未验证清单

- **单个智能机器人"同时最多 3 条消息交互"**：仅见于企业微信开发者社区的技术支持回答，官方长连接文档未写；标「未验证」。若为真，会直接影响 §5.2 的 worker 池上限，落地前需实测。
- **Grafana contact point 在收到非 2xx 时是否重试、重试几次**：本文未从官方文档确认；只按"上游 Webhook 有 5 秒硬超时"的既有实现假设（`alert_service.py` 注释）。限流/丢弃设计若依赖重试行为，需先核实。
- **PagerDuty / Rootly 的自动处置审批边界**：本文只确认它们有 workflow/自动化能力，未确认"哪些动作必须人工批准"；标「未验证」，不作设计依据。
- **incident.io 的 Investigations 是否对每次结论做反馈学习**：页面只描述调查与证据，未确认 feedback 回灌；标「未验证」。
