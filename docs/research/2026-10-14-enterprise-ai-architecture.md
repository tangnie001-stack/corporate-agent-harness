# 调研：企业级 AI 应用架构（含高并发视角）+ hermes-agent 架构实读

> 调研日期：2026-10-14 ｜ 深度：Thorough ｜ 方法：Firecrawl 联网检索（4 角度并行，20+ 次检索）+ 本地源码实读
> 目的：为 Corporate Agent Harness（FastAPI + LangGraph + PG/pgvector + Redis + MinIO + Langfuse，单 worker / 进程内状态 / SSE）判断"哪些该做、哪些是过度设计"。

## 1. Executive Summary

**一句话结论：检索不到"企业级"的可验证门槛，但能检索到一组收敛的参考架构；而真实生产案例的规模锚点比想象小得多（Uber 全公司 LLM 网关峰值 25 QPS），所以对本项目（千级文档、企业内部）而言，绝大多数"高并发企业级实践"是过度设计。**

三个反直觉的事实：

1. **"enterprise-grade" 在这些文章里是形容词，不是规范。** 本次检索的所有信源中，**没有任何一方**给出可验证的门槛（SLO、并发量、合规清单）；多家厂商的"AI 参考架构"实际是产品获客页（Bifrost / F5 / Tungsten）。真正有体系的是 AWS Prescriptive Guidance 与 Google Cloud Architecture Center 的官方图形 [S1][S3]。
2. **真实生产的规模很小。** Uber 的 GenAI Gateway 服务近 30 个团队、**1600 万次查询/月、峰值 25 QPS**（一手博客，已逐字核验）[S26]。这意味着"高并发"在 LLM 应用里是另一个量级的词——瓶颈几乎总在上游模型配额，而不是本机 CPU。
3. **横向扩展的机制收敛为一条：把状态从进程里挪出去。** 所有讨论多实例流式的信源都指向同一个杠杆"无状态实例 + 外置状态 + 事件扇出"；其余（K8s、服务网格、独立向量库微服务）都是这一条的包装 [S10][S31][S32]。

另外，本地 `hermes-agent`（Nous Research）**不是企业级高并发服务**，而是刻意的单用户自托管 agent：docker-compose 只起 2 个服务、`network_mode: host`、状态是单文件 SQLite，且源码文档里明确裁定"每 host 只允许一个 gateway"（已核验）。

## 2. Key Findings

1. **收敛的参考架构是三层 + 三核心服务 + 跨层关注**：应用层（含"业务系统把自己暴露为 agent 可调用工具"）/ Agent 层（LLM 接入、规划、工具调用、知识获取、记忆、agent 间通信）/ 核心服务三类（模型访问含策略与成本归集、工具含授权、知识库含 RBAC）；可观测/安全/可发现性被明确声明为**跨层**。来源为 AWS Prescriptive Guidance 官方文档 [S1]。
2. **唯一被反复验证的扩展杠杆：无状态化。** LLM Gateway 设计为无状态，故可直接挂 HPA 水平扩；换模型只改 ConfigMap 不需重部署 [S10]。会话状态、连接状态一律外推到 Redis/PG，而不是用 sticky session [S31][S32]。
3. **sticky session 是"推迟问题"而非解决方案**：故障、再均衡、滚动发布三种场景下都会一次性丢掉整机会话 [S31][S32]。
4. **流式多实例扇出的标准做法是"每实例订阅同一 channel，事件只写给本地连接"**；关键陷阱是 **Redis Pub/Sub 不持久**（客户端断连数秒内的事件永久丢失），需要可重放就得换 Redis Streams + 事件时间戳 [S33]。
5. **LLM 限流必须是 token 感知的多维限流**（RPM/TPM/并发/成本，按用户·团队·模型），用 RPS 限流 token 型负载是错的——相同请求量在不同模型上计算量可差 5 倍 [S34]。LiteLLM 官方提供 `max_parallel_requests` / `tpm_limit` / `rpm_limit` / 团队预算 [S35][S36]。
6. **语义缓存收益强依赖负载重复率**：FAQ/agent 场景命中率 30–70%，响应从 500–2000ms 降到 3–8ms；但对高 temperature 创意生成命中率 <5%，只白付 embedding 开销 [S37]。
7. **向量检索横向扩展性差**：pgvector 读副本会带**完整索引副本**，内存成本 ×N；第一道坎是 HNSW 索引装不进内存。省钱杠杆按序为 `halfvec`/binary → 调 `hnsw.m`/`ef_search` → `iterative_scan` → 拆独立 PG → pgvectorscale [S38]（该来源为 TigerData 利益相关方，须打折）。
8. **pgvector 官方事实（已核验原文）**：默认**精确**最近邻（perfect recall），加索引是"trades some recall for speed"；`vector` 类型 HNSW/IVFFlat 索引上限 **2000 维**；README 里 `100000 tuples` 的 NOTICE 是 **`maintenance_work_mem` 构建耗时提示，不是"该建索引"的推荐阈值** [S39]。
9. **成本治理的前提是"归因到团队/用例"**：Uber 的成本治理建在网关审计日志上；RouteLLM 类复杂度路由可达 75–85% 降本、语义缓存 30–50%、provider 原生 prompt caching 最高 90%（后三者为厂商/研究口径，须打折）[S26][S40]。
10. **评测的工程化程度是区分"玩具"与"生产"的分水岭**：DoorDash Vera 有 19 个域、**1900+ 条 eval**、LLM judge 按"工具调用/选表/答案正确"3 项打分（满分 3.0），并明确 **run-to-run 波动约 4 个百分点即视为噪声**；新知识源**默认 inactive**，过阈值才由域属主晋升 [S27]。
11. **hermes-agent 的定位是"$5 VPS 个人自托管"**，与企业多租户服务正交：`docker-compose.yml` 只有 `gateway` + `dashboard` 两个服务、均 `network_mode: host`，无 PG/Redis/MinIO/MQ；`gateway/host_rendezvous.py` 文档裁定"exactly ONE `hermes serve` and ONE `hermes gateway run` per host"——**多实例是设计禁止的**。
12. **hermes-agent 的 62.8 万行 Python 大部分是"边界与降级"而非"吞吐"**：根目录约 40 个 `hermes_state_*.py` 是同一个 SQLite `SessionDB` 的切片。它的复杂度来自"单进程兜住 24+ 平台的所有异常"，不是扛并发。

## 3. Detailed Analysis

### 3.1 参考架构：分层高度收敛，但缺乏一手数字

平台侧（AWS）给的是三层 + 三类核心服务 + 跨层关注 [S1]；应用侧（InfraSketch）给的是四层"客户端 / 编排 / 模型 / 数据"，并把流式（SSE 或 WebSocket）称为面向用户应用"几乎是必须的" [S5]；MLflow 把编排层称作"控制平面"，负责路由、编排、工具调用与状态 [S7]。中文侧可用信源很少（腾讯云"六层"、CSDN"七层"多为无来源的概念复述，[S16][S17]）。

**判断**：这些分层图彼此高度相似，疑似相互传染，**没有独立的一手案例数字支撑**。分层的价值在于"检查清单"，不在于"复杂度预算"。

范式上可粗分四种：
- **单体 LLM 应用**（UI+编排+模型+数据同进程）——默认形态，单团队适用 [S5]。
- **平台化：LLM Gateway + 微服务**——全部 LLM 流量收敛到一个无状态、OpenAI 兼容代理；密钥只在网关；带语义缓存/限流/fallback/token 记账 [S10]。
- **事件驱动 / agent 编排平台**——InfoQ 给出三级自主度与 5 个编排模式（Prompt Chaining / Routing / Parallelization / Evaluator-Optimizer / Orchestrator-Workers）[S13]。
- **Serverless 推理**——GCP 有"ADK + Cloud Run 跑单 agent"的参考架构 [S23]。

### 3.2 高并发：机制只有一套，阈值没有

把 4 个角度的材料并起来，可扩展性的机制是统一的：

| 问题 | 业界做法 | 代价 / 陷阱 |
|---|---|---|
| 会话/任务状态 | 外推到 Redis/PG，实例无状态 [S31][S32] | 一次网络往返；需要事件重放语义 |
| 流式扇出 | 每实例订阅同一 channel，事件只发本地连接 [S33] | Pub/Sub **不持久**，断连期事件永久丢失；要可靠须 Redis Streams |
| 断线续传 | `Last-Event-ID` 语义 + 补投缓冲 + 心跳 [S32][S33] | ALB 默认 60s、Cloudflare 约 100s 无流量即断 ⇒ 每 50s 心跳 |
| 限流 | token 感知多维限流（RPM/TPM/并发/成本）[S34] | 用 RPS 限 flow token 型负载会算错 |
| 长连接网关 | "集中式 poller + fan-out"：一个进程持上游连接，diff 后广播 [S41] | 多实例竞争上游连接需单活仲裁 |

**关于"什么时候才值得上"——没有硬阈值。** 检索未找到"几个实例才值得上 Redis 扇出"的权威判据。可操作的推断：一旦需要**滚动发布不中断**、或单机有 OOM 风险，就该做；纯 2 实例内测收益有限。

**一个容易被忽略的负载特征**：Ably 指出 AI agent 的长时静默（等工具调用/等模型）使"连接数≈负载"失效，应按活跃字节率或轮次状态路由，而不是按连接数 [S32]。

### 3.3 生产案例：规模小、治理重

| 公司 | 做了什么 | 规模 / 数字 | 来源 |
|---|---|---|---|
| Uber | GenAI Gateway：统一 OpenAI 兼容入口，内置 PII 脱敏、鉴权、指标、审计日志（用于**成本归因 + 安全审计 + 质量评估**） | 近 30 个团队、1600 万次/月、**峰值 25 QPS** | [S26] 一手，已核验 |
| Uber | MCP Gateway：托管 800+ MCP server、5000+ tool；工具**默认 disabled**，改描述→config diff→属主审批→可回滚 | — | [S28] 一手 |
| DoorDash | Vera 内部数据 agent：19 域 / 1900+ eval / LLM judge 3 项打分 / run-to-run **4pp 噪声带**；新数据源默认 inactive，域属主审核晋升 | 1 万+ 用户、20 万数据集、350PB | [S27] 一手，已核验 |
| Langfuse（方法） | 生产 trace 上**抽样 5%** 跑 judge + 代码评测器 + 隐式信号；judge 须人工标注校准；CI 分数低于阈值抛错阻断 PR | — | [S29] 厂商文档 |

**共性**：这些团队的重点不在"扛 QPS"，而在 **成本归因、评测治理、默认关闭 + 审批 + 可回滚**。这三件事对本项目是可直接借鉴的，且与并发量无关。

### 3.4 hermes-agent 架构实读（本地源码）

**定位**：Nous Research 出品的单用户、自托管、多通道个人 AI Agent（MIT）。证据：`README.md` 自述"Run it on a $5 VPS"；`docker-compose.yml:29-76` 只有 `gateway` 与 `dashboard` 两个服务、均 `network_mode: host`；`gateway/host_rendezvous.py:1-3` 明确"exactly ONE `hermes serve` and ONE `hermes gateway run` per host"。

**分层**（关键文件）：
- 入口/CLI：`cli.py`(80KB)、`hermes_cli/`、`hermes_bootstrap.py`
- Agent 层 `agent/`（~200 模块）：`agent/conversation_loop.py` 是主循环；`run_agent.py` 定义 `AIAgent`；`turn_*` 家族把一轮拆为 api_call / tool_round / finalizer / preflight / compression
- 工具层 `tools/`（~200 文件）：`delegate_tool.py`、`approval*`、browser/terminal 等
- 状态层：根目录约 40 个 `hermes_state_*.py`，都是同一个 SQLite `SessionDB` 的切片（schema/messages/sessions/fts/wal/readpool/repair）
- 通道层 `gateway/`（~150 文件）：`platform_registry.py` + `platforms/`（内置 signal/whatsapp/weixin/qqbot…，插件另有 discord/slack/telegram/feishu/dingtalk/wecom 等约 24 个）
- 内容层：`skills/`、`optional-skills/`、`optional-mcps/`、`plugins/`

**主循环**：`agent/conversation_loop.py` 的 `run_conversation()` 驱动一整轮：模型调用 → 工具派发 → 重试/降级 → 压缩 → 后置钩子。工具调用按"轮"处理，**在任何副作用前先持久化 tool-call 轮**。迭代有预算：`agent/iteration_budget.py:4` 载明父 `max_iterations` 默认 **500**、子代理 `delegation.max_iterations` 默认 **50**。

**Skills 与子代理**：
- Skill 定义 `skills/<类目>/<名>/SKILL.md` + frontmatter；`skills/AGENTS.md:28` 硬规则"**`description` ≤ 60 chars, one sentence, ends with a period**"（附可执行检查）。
- 自改进：`agent/curator.py` 在空闲时 fork 一个 `AIAgent`，通过 `skill_manage` 做 pin/archive/consolidate/patch；铁律是"只归档不删除、pinned 免疫、fork 用 auxiliary client 不污染主会话缓存"。
- 子代理：`tools/delegate_tool.py` 中，子代理是**全新会话的 AIAgent**（独立 task_id，工具集=父减子禁用），**父只看到委派调用与摘要**，看不到中间过程；支持单任务与 batch 并行。
- **上下文压缩**：`trajectory_compressor.py` 是**离线 CLI**（`import fire`），策略是**保护头部（system、首个 human、首个 gpt、首个 tool）与最后 N 轮**（`protect_last_n_turns` 默认 4），只摘要中间段，且**绝不拆开 `<tool_call>/<tool_response>` 配对**（`trajectory_compressor.py:4,97-117,285`）。在线压缩是另一套（`agent/context_compressor.py`、`conversation_compression.py`、`micro_compaction.py`）。

**记忆与状态**：单文件 SQLite（`hermes_state.py`）。FTS5 用自研可加载分词器 `cjk_unicode61`（`native/fts5_cjk/`）把 CJK 串重发为重叠 bigram——因为 trigram 对 1–2 字中文词退化成 LIKE 全表扫。并发模型：WAL，遇 NFS/SMB/ZFS/APFS/WSL1 等不兼容文件系统降级为 DELETE；`hermes_state_readpool.py` 给只读连接发 permit（单文件上限 8、单进程上限 24），用尽则退回写连接——"宁可慢，不可 EMFILE"。跨会话记忆是 `MEMORY.md`（环境事实）+ `USER.md`（用户画像）两个文件。

**多实例**：不是"可支持"，是**被设计禁止**（见上文 host_rendezvous 裁定）。被顶号有专门恢复逻辑（如 Telegram 适配器识别 `terminated by other getupdates request`）。

**可借鉴（≥3）**：
1. **上下文压缩的"配对不可拆"硬约束**——我们做跨轮摘要/裁剪时容易把 tool_call 与 tool_response 拆散，这是直接可用的规则。
2. **skill 作者标准 + 可执行检查**（description ≤ 60 字符）——低成本高收益的 skills 库质量闸门。
3. **curator 式后台维护的安全边界**（只归档不删除、pinned 免疫、用独立 client 不污染主会话缓存）。
4. **可观测降级**（WAL→DELETE、read pool→writer，且留可诊断日志）——与本仓 defensive-patterns 思路一致。

**不可借鉴（≥3）**：
1. **SQLite 单文件状态**——我们是 PG + 多 worker + SLB，整套并发模型不适用。
2. **单写者 / 单 gateway 裁定**——与企业多实例横向扩展直接冲突。
3. **把 `MEMORY.md`/`USER.md` 当事实源**——违反本仓 memory-model「记忆不得作为事实依据」。
4. **模块爆炸式拆分**（根目录 40 个 `hermes_state_*`、单文件上百 KB）——违反本仓 400 行红线。
5. **24+ 通道挤在一个进程**——无隔离、无背压层。

## 4. Contrarian Views And Risks

**反对过早分布式化**：AWS 官方对 monolith vs 微服务的论证指出，微服务的代价是部署与运维复杂度，收益是独立伸缩 [S21]；拆分时机判据被归纳为"部署协调成为瓶颈 / 团队 >8–10 人 / 各域伸缩需求不同" [S22]（Atlassian / DX，非一手研究）。

**"伪扩展"陷阱（逐条都有出处）**：
- 把 sticky session 当扩展方案——它是推迟问题，故障时一次性丢整机会话 [S31][S32]。
- 进程内任务注册表/事件缓冲直接换多实例——跨实例事件静默丢失 [S33]。
- 拿 Pub/Sub 当可靠队列——不持久、断连期不可恢复 [S33]。
- 加 PG 读副本扩向量检索——每副本全量索引，内存 ×N [S38]。
- 对高 temperature 生成上语义缓存——命中率 <5%，只白付 embedding 开销 [S37]。
- 用 RPS 限流 token 型负载 [S34]。

**真实失败模式**：
- **静默失败是主形态**：一份 arXiv 草稿（2606.14589，**单作者、单系统、n=1 的 v0.3 草稿，不可当定律**）称 fail-plausible 静默失败约占 70% 靠人肉看输出发现，单元测试覆盖≈0%；治理层事前预防 0%、事后回归拦截 87%。
- **无熔断的多代理互问**：二手来源称双子代理互问 11 天无人发现、周成本从 $127 涨到 $47,000（tianpan.co，二手转引，未核原文）。

**官方硬约束**：
- pgvector：默认精确检索；索引是 recall 换速度；`vector` 索引上限 2000 维；`100000 tuples` NOTICE 是构建耗时提示而非建索引阈值（**已核验原文** [S39]）。
- LangChain：官方 Standalone Agent Server 用 Docker/Compose/K8s + 自备 PG/Redis，**无需 control plane**——连官方运行时骨架都不要求 K8s。
- Anthropic：限额为**组织级、按模型**的 RPM/ITPM/OTPM，token bucket 突刺会触发（**本条未能核验原文**：官方文档对本区域返回 `app-unavailable-in-region`；且本项目主链路已切 DeepSeek/百炼，影响有限）。

## 5. 对本项目（Corporate Agent Harness）的映射

> 本节是"建议"，不是"决策"。若要落定为决策，应走 `docs/adr/`。

**现状**：单 worker；任务注册表与事件缓冲在进程内；SSE 流式；渠道为 Web 站点 + 企微长连接。

**A. 该补的（失败会静默发生，且不随用户量缩小）**
1. **agent 循环熔断 + 预算上限 + 同参工具调用 >2 次即停**——对应失败模式证据（互问死循环、无熔断）+ hermes 的 iteration budget 做法。
2. **提示词版本化 + 会话钉版本 + 可回滚**——多轮在途会话是热回滚的难点；Uber/DoorDash 都用"默认关闭 + 审批 + 可回滚"。
3. **成本归因加 team/case 标签**——Uber 的做法是建在网关审计日志上；我们已有 Langfuse trace，加维度成本低。
4. **发布门禁**：RAGAS 分数进 CI 阈值；新知识库/知识源默认 inactive，审核后晋升（DoorDash 模式）。

**B. 从 hermes-agent 借鉴的（低风险）**
5. 上下文裁剪/摘要**不得拆散 `<tool_call>`/`<tool_response>` 配对**。
6. skills 库加**可执行的 description 长度/格式检查**。
7. 清理/归档类后台任务遵循"只归档不删除"。

**C. 暂不做的（就当前规模判为过度设计）**
- 多 worker + 状态外置 + Redis 扇出、独立向量库微服务、K8s/服务网格、独立 LLM 网关。
- **前提判据**：先量化真实负载（峰值 QPS、429/retry-after 次数）。若瓶颈是上游模型配额，横向扩容不涨吞吐。

**D. 顺序建议**：先做 A（与并发无关、纯治理）→ 再补 B（低成本）→ C 只在打出真实瓶颈证据后再动。

## 6. Open Questions

1. **"几个实例才值得上 Redis 扇出"没有权威阈值**——需按本项目的滚动发布/内存风险自定。
2. **IM 长连接（企微）多实例的单活仲裁**：未找到直接信源；可参考 hermes 的"集中式 poller + flock 单活"与 Telegram 被顶号恢复，但无权威背书。
3. **本项目是否真的需要横向扩展**：缺实测——峰值 QPS、并发会话数、上游 429 频率均未测。
4. **Anthropic 组织级限流原文未核**（区域不可达），相关内容应降级使用。
5. **"企业级"合规/安全门槛**（审计、数据驻留、RBAC 细分）本次未覆盖——若要做面向客户/生产的宣传，需补 Gartner/Forrester 类一手行业报告而非厂商博客。
6. hermes-agent 的吞吐无仓库内证据；curator 自改进的实际触发频率与效果无实测。

## 7. Sources

### 一手官方文档 / 官方工程博客
- S1 https://docs.aws.amazon.com/prescriptive-guidance/latest/govern-architect-agentic-ai/enterprise-architecture.html — AWS 官方：企业 agentic AI 参考架构（三层 + 三核心服务 + 跨层）
- S3 https://docs.cloud.google.com/architecture/ai-ml — GCP 官方 AI/ML 架构指南
- S4 https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system — GCP：agentic 设计模式选型
- S23 https://docs.cloud.google.com/architecture/single-agent-ai-system-adk-cloud-run — GCP：单 agent + Cloud Run（serverless 范式）
- S21 https://aws.amazon.com/compare/the-difference-between-monolithic-and-microservices-architecture/ — monolith vs 微服务代价（通用论证）
- S26 https://www.uber.com/us/en/blog/genai-gateway/ — **Uber GenAI Gateway：30 团队 / 1600 万次/月 / 峰值 25 QPS**（已逐字核验）
- S28 https://www.uber.com/us/en/blog/designing-mcp-gateway/ — Uber MCP Gateway：800+ server、默认关闭、config diff 可回滚
- S27 https://careersatdoordash.com/blog/inside-vera-doordashs-data-agent/ — **DoorDash Vera：19 域 / 1900+ eval / 4pp 噪声带**（已核验）
- S35 https://docs.litellm.ai/docs/proxy/users — LiteLLM 并发/TPM/预算
- S36 https://docs.litellm.ai/docs/proxy/dynamic_rate_limit — 按饱和度共享、优先级预留
- S39 https://github.com/pgvector/pgvector — **pgvector 官方：默认精确检索、vector 索引上限 2000 维、100000 tuples NOTICE 语义**（已核验）

### 深度技术文 / 工程博客
- S5 https://infrasketch.net/blog/llm-system-design-architecture — 四层模型 + 参考架构图
- S7 https://mlflow.org/articles/llm-application-architecture-a-2026-engineers-guide/ — 编排层="控制平面"
- S9 https://medium.com/@udayansawant/the-orchestration-layer-in-production-llm-systems-ab61521fc317 — 编排层失败处理
- S10 https://www.freecodecamp.org/news/the-llm-gateway-pattern-why-every-kubernetes-based-ai-app-needs-one/ — LLM Gateway 模式 + 无状态→HPA
- S13 https://www.infoq.com/articles/agentic-ai-architecture-framework/ — 三级自主度 + 5 编排模式
- S31 https://websocket.org/guides/websockets-at-scale/ — 共享状态 vs sticky 的判据
- S32 https://ably.com/topic/the-challenge-of-scaling-websockets — 长连接扩展、sticky 之弊、AI agent 负载特征
- S33 https://hackernoon.com/sse-server-sent-events-emitter-with-redis-pubsub — Pub/Sub 扇出与"不记忆"限制
- S41 https://www.sportmonks.com/glossary/server-sent-events-sse/ — 集中式 poller、SSE 心跳
- S29 https://langfuse.com/blog/2025-11-12-evals — 在线/离线评测、judge 抽样、CI 回归门
- S22 https://getdx.com/blog/monolithic-vs-microservices/ · https://atlassian.com/microservices/microservices-architecture/microservices-vs-monolith — 拆分时机判据

### 厂商（有利益相关，须打折）
- S34 https://www.truefoundry.com/blog/rate-limiting-in-llm-gateway — token 感知多维限流
- S37 https://www.spheron.network/blog/semantic-cache-llm-inference-gpu-cloud/ — 语义缓存命中率与工具对比
- S38 https://aiml.qa/blog/qdrant-vs-pgvector/ — pgvector 扩展信号与基准（TigerData 关联）
- S40 https://www.truefoundry.com/blog/llm-cost-optimization — 路由/缓存降本口径
- S30 https://docs.vllm.ai/projects/production-stack/en/latest/ — vLLM 请求路由
- S2 https://agility-at-scale.com/ai/architecture/ — 五层模型（自称"2026 standard"，无一手依据）
- S11 https://www.getmaxim.ai/articles/what-is-an-llm-gateway-architecture-features-use-cases/ — Bifrost 获客文
- S20 https://www.f5.com/resources/reference-architectures/ai-overview — F5 产品页
- S14 https://www.tungstenautomation.com/blog/build-enterprise-grade-ai-agents-agentic-design-patterns — 厂商白皮书

### 中文（质量整体偏低）
- S15 https://aws.amazon.com/cn/blogs/china/enterprise-level-agentic-ai-architecture-design/ — AWS 中国官方博客（中文信源里唯一可用）
- S16 https://developer.cloud.tencent.com/article/2714893 — 腾讯云"六层架构"
- S17 https://adg.csdn.net/6a87b44b10ee7a33f29d5973.html — CSDN 七层（无来源）
- S18 https://cloud.baidu.com/product/ai-trends/community?id=9066238 — 百度云微服务拆分

### 本地源码实读
- hermes-agent 仓库快照：`/root/code/github/hermes-agent`（commit `d6ea4ded`，2026-10-14）
- 关键证据文件：`gateway/host_rendezvous.py:1`、`docker-compose.yml:29-76`、`agent/iteration_budget.py:4`、`skills/AGENTS.md:28`、`trajectory_compressor.py:4,97-117,285`、`agent/conversation_loop.py`、`agent/curator.py`、`tools/delegate_tool.py`、`hermes_state_fts.py`、`hermes_state_readpool.py`

## 8. Rerun Inputs

```
workflow: firecrawl-deep-research
topic: 企业级 AI 应用架构（高并发视角）+ 本地 hermes-agent 架构实读
depth: thorough
output: markdown
```
