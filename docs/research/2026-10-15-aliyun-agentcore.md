# 调研：阿里云 AgentCore / 百炼 Agent Studio —— 企业级 Agent 平台的能力分层与可借鉴点

> 调研日期：2026-10-15 ｜ 深度：Thorough ｜ 方法：官方文档逐页实读（AgentCore 帮助中心 10 页）+ 本项目源码核对
> 目的：判断这两份阿里云资料对 Corporate Agent Harness（FastAPI + LangGraph + PG/pgvector + Redis + MinIO + Langfuse）是否有参考价值 —— 哪些概念可对齐、哪些设计可照搬、哪些明确不适用。

## 1. Executive Summary

**一句话：有参考价值，但它不是"选型候选"，而是"命名体系 + 六个可照搬的具体设计"。**

三条要点：

1. **AgentCore 是"构建与治理平台"（托管），我们是"自建 harness"** —— 两者不在同一个决策轴上，不构成"要不要迁过去"的选择题。它的价值在于把我们 roadmap 里散落的想法**命名并分层**了（尤其 Agent 的四种形态，见 §4.1）。
2. **六个具体设计可以借鉴**，其中「**凭证的 Key 就是环境变量名**」最即时 —— 我们刚因为缺这一层，把 `ALERT_WEBHOOK_TOKEN` 硬编码进了 `docker-compose.image.yml:224`（见 §4.2）。
3. **它相当一部分能力对本项目是过度设计**（扩缩容、Team 编排、Workspace 隔离），与"千级文档 / 单 worker / 2 台机"的规模不符 —— 与 `./2026-10-14-enterprise-ai-architecture.md` 的结论一致。

**第二份资料（电子书 8609）无法评估**：它是《阿里云百炼 Agent Studio 产品手册》的**下载落地页**，正文与目录均不在网页上，只提供一个需登录的下载入口（已核实，见 §2）。

## 2. 两份资料是什么

| 资料 | 实际是什么 | 可读性 |
|---|---|---|
| `https://developer.aliyun.com/ebook/8609` | 《阿里云百炼 **Agent Studio** 产品手册》，发布 2026-09-16，作者为百炼产品运营团队。官方简介：围绕企业级 Agent「开发、运行、**进化、商业化**」的完整生命周期，呈现 Agent Studio 全栈服务平台的能力全景与落地路径 | ❌ **网页无正文、无目录**；页面只有简介 + `/download/8609`（需登录）。**本次未能读到实质内容** |
| `https://help.aliyun.com/zh/agentcore/` | **AgentCore**「智能体构建和**治理**平台」帮助中心。定位：以「构建与运行、协作与治理、观测与评估」三大能力覆盖 Agent 全生命周期，把自研 / 开源 Harness / 商业 SaaS Agent **多来源统管于一个平台** | ✅ 10 页实读（见 §8 来源） |

两者是同一厂商两条产品线：AgentCore 偏**平台与治理**（能读到设计），Agent Studio 偏**产品化落地叙事**（读不到）。

## 3. AgentCore 能力全景（实读）

### 3.1 概念骨架

```
Workspace（资源隔离单元：模型 / Agent / Team / MCP 工具各自独立）
   └─ Agent（执行单元）── 关联 1 个模型，绑定 Skill 与 MCP
        ├─ Skill     专业技能（SKILL.md 定义）
        ├─ MCP       外部工具（协议级集成）
        ├─ 知识库     检索资产
        ├─ 凭证       Key-Value 密钥（Key 是环境变量名）
        └─ SubAgent  把复杂任务拆给子 Agent，由当前 Agent 调度
   └─ Team（多个 Agent 组成的组织，在 Agent 之上再加一层）
```

### 3.2 模块清单与对本项目的映射

| AgentCore 模块 | 它做什么 | 我们的对应物 | 差距 |
|---|---|---|---|
| **Agent 四形态** | 托管 Harness / 高代码 Agent（容器镜像）/ 纳管 Agent（纳管本地已部署）/ 注册 Agent（待上线） | 自建 harness（代码级） | 它把"平台代管"与"代码自建"分成并列形态；我们只有一种 |
| **Skill 管理** | SKILL.md（`name`/`description`）；ZIP / Nacos CLI / 预置模板三种导入；**草稿→提交审核（安全检测）→发布**；版本管理（版本号+变更说明+版本标签）；启用/禁用；可见性（私有/公开）；可下载 | `SKILL.md`（`.agents/skills/`、`.claude/skills/`）+ 目录共享结构 + 可见性三层（源库/链接/`disable-model-invocation`） | **缺版本与审核**（我们没有草稿态、审核闸门、版本号） |
| **MCP 工具** | 类型：MCP / 虚拟 MCP（「将已有 MCP 工具重新组装」，即将上线）；创建方式：**连接远程 MCP 服务 / HTTP 转 MCP**；Transport：SSE 或 Streamable HTTP；认证：启用认证 + 认证方式 + **凭证来源（如自定义 HTTP header）+ Header 名称 + API Key** | `src/` 内 MCP **零命中**（roadmap P2） | 我们尚无 MCP；它的 **HTTP→MCP** 说明"不必先造 MCP server" |
| **凭证管理** | KV 集中保存，**Key 为环境变量名**（原文例 `GITHUB_ACCESS_TOKEN`），Value 创建后隐藏，供模型连接与工具**按 Key 引用** | `src/infra/llm/provider/spec.py:37` 的 `api_key_env`（**同一 pattern，仅用于 LLM**） | 同一做法**未推广到业务工具/通道** |
| **记忆空间** | **长期记忆**（从输入提取的偏好/事实/需求，语义检索）与**会话历史**（消息记录，按会话回看）**两分、两套 API**；Scope（`userId`/`agentId`/`sessionId`，指定的参与精确过滤、未指定=不限制）+ Metadata（KV 业务标记，如 `category`/`source`） | `sessions` 表有 `user_id` / `agent` / `kb_id`（`src/infra/db/models/chat.py:14-20`）；记忆层**尚未做**（见 `docs/context-memory-research.md`） | 两分法与 Scope/Metadata 可直接作为我们记忆层 Phase 的隔离维度参考 |
| **观测大盘** | 请求概览（总数/错误率/请求与错误趋势/平均耗时）、Token 分析（各模型消耗分布与趋势）、模型分析（调用量分布与趋势）、**工具调用分析（各工具调用量分布与趋势）**；2 分钟粒度、分钟级聚合延迟 | Langfuse trace + 成本归因 | 它单列了「按工具维度聚合」；值得核我们是否有 |
| **模型连接 / 模型** | 连接侧：供应商、API 端点、协议、凭证；模型侧：上下文长度、单次最大输出、流式、**工具调用 / 视觉 / 音频等能力项** | `spec.py` 的 `ProviderSpec`（`thinking_style`、`reasoning_field`、`supports_tools`）+ `ModelFacts`（上下文、embedding 维度/批量等） | 方向一致；我们**没有配额**，能力项也不全（无 vision/audio） |
| **Token 配额** | 开关 + 上限 + **计量周期（天/周/月）** | **无**（`quota` 在 `src/` 零命中） | 我们有成本**观测**，没有成本**管控** |
| **Team 协作 / 定时任务** | 多 Agent 组队、成员权限、Team 级定时任务 | 一条循环两角色（`agent-assembly`） | 路线不同，见 §5 |
| **连接器** | 目前仅 **Qoder CLI**（站点/组织 ID/API 密钥/服务账号 + 连通性校验） | 无 | 价值低（且仅此一个连接器） |
| **资源配置** | 规格（CPU/内存）、实例最大 Session 数、**Session TTL**、自动扩缩容 | 自建部署（云效→ACR→ECS） | 扩缩容不适用；**Session TTL** 概念值得单独想 |
| **网络配置** | 允许默认网卡访问公网 / 允许访问 VPC | 已有 nginx 反代 + 内网 PG/Redis/MinIO | 不适用 |
| **IM 配置** | 钉钉/微信/飞书账号集成 | 企微通道（已上线） | 不适用 |
| **观测依赖** | Tracing 写入 **CMS（云监控）**；另依赖 PrivateLink、SchedulerX3 | Langfuse（自托管） | 不适用（引入 = 引三条云上依赖） |

## 4. 值得借鉴的六条

### 4.1 Agent 的四种形态 —— 回答我们 harness 的定位问题

它把 Agent 分成四种（§3.2 首行）。两个用处：

1. **术语对齐**：在它的语境里 **Harness = "平台提供运行框架、控制台配置即可创建"**（配置项原文：名称、模型、所属 Team、`SOUL.MD` 与 `AGENT.MD`、技能绑定、MCP 绑定、SubAgent、资源配置）。我们自建的 harness 是**同一层**的东西，只是自己实现 —— 引用时必须说清，否则"harness"这个词会在两份语境里漂移。
2. **"纳管 Agent"这条最有启发**：把**已部署在客户本地的 Agent 纳管进平台，无需改造即可参与编排与协作**。它证明"自研 harness"与"平台"不是二选一 —— 这对 roadmap P2（subagent / MCP / skill）的形态选择是现成的命名法。
3. 附带：`SOUL.MD` + `AGENT.MD` 双文件承载系统提示词，与我们 `prompt-layering`（分层 + `prompt-carrier`）是同题不同解 —— 它分两文件，我们分节。可作对照物，不必照搬。

### 4.2 凭证管理：Key 就是环境变量名 ⭐（最即时）

它的凭证是 KV 集中存，**Key 为环境变量名**（如 `GITHUB_ACCESS_TOKEN`），Value 创建后默认隐藏；模型连接与工具**按 Key 引用**，值在运行时注入。

对我们的意义有两层：

- **它正是我们刚踩的坑的解**：生产 `ALERT_WEBHOOK_TOKEN` 因为"目标机只读 + `.env` 靠人工拷"，最终只能硬编码进 `docker-compose.image.yml`。这是**缺一层凭证抽象**的直接后果。
- **我们已有这个 pattern**：`src/infra/llm/provider/spec.py:37` 的 `api_key_env` 就是"存环境变量名而非值"。**所以这不是引入新概念，而是把已有做法从 LLM 推广到业务工具与通道。**
- 且它正好补全需求池 **F-43**（只读 Grafana 工具）的实现约束 —— 现在写的是"凭据不进 prompt"，应补上"**工具拿到的是环境变量名，值在运行时注入**"。

### 4.3 HTTP 转 MCP —— 不必先造 MCP server

它的 MCP 工具有两类创建方式：**连接远程 MCP 服务** 与 **HTTP 转 MCP**（把普通 HTTP 接口直接转成 MCP 工具）。

对 F-43：一个 Grafana 只读查询工具，用"HTTP 工具 + 路径白名单 + 只读 token"即可，**不需要先引入 MCP 整套**。这也让 roadmap P2 的 MCP 可以分两步走：先 HTTP 工具，真需要多工具生态时再上 MCP。

### 4.4 Skill 的治理流程 —— 我们缺的那半

我们有目录共享 + 可见性三层，但**没有版本与审核**。它的完整流程是：

```
草稿 → 提交审核（安全检测）→ 通过才能发布 → 版本管理（基于此版本创建 / 新版本号 / 变更说明 / 版本标签）
     → 启用 / 禁用 → 可见性（私有 / 公开）→ 可下载
```

对照我们的实际痛点：外部 skill 的"上游原样 vs 本地改动"如何区分（见需求池 F-34）。它的「**基于此版本创建 + 变更说明 + 版本标签**」可直接借命名 —— 版本号表达"与上游的关系"，标签表达"本地状态"，正好命中我们的问题。

### 4.5 记忆的两分法 + Scope/Metadata

- **长期记忆 vs 会话历史，两分、两套 API**（`AddMemories`/`SearchMemories`/`ListMemories`/`GetMemory`/`UpdateMemory`/`DeleteMemory` vs `ListMemorySessions`/`ListMemorySessionMessages`）。这与 `docs/context-memory-research.md` 定的 **episodic + user-semantic** 几乎一一对应，且它替我们回答了一个隐含问题：**两者不合成一个 store**。
- **Scope**（`userId`/`agentId`/`sessionId`，指定即过滤、未指定即不限制）**+ Metadata**（分类/来源等业务标记）。我们的 `sessions` 已有 `user_id` / `agent` / `kb_id` 三个维度（`src/infra/db/models/chat.py:14-20`），但**尚未把它们用作记忆检索的过滤维度** —— 这是记忆层落地时的现成设计。
- **一条能省我们踩坑的原文**：「输入内容与生成的记忆**不一定一一对应** —— 提交一段文本或一组消息后，可能生成多条记忆，也可能不生成新记忆。」自建时最容易写错的假设就是这个。
- **一条边界**：创建记忆空间**不会自动收集** Agent 的对话；写入与检索的时机由应用决定。这印证 `ADR-0018`（不用框架持久化、真源留应用自有存储）的取向没错。

### 4.6 模型能力项 + Token 配额

- 它在**模型侧**声明上下文长度、单次最大输出、流式、工具调用、视觉、音频。我们有雏形（`ProviderSpec` / `ModelFacts`），但**配额完全没有**（`quota` 在 `src/` 零命中）。
- 我们踩过的坑几乎都是这类信号：`qwen3.8-2.4t-a95b` 只接受 `enable_thinking=True`（能力项未建模）、上游 403 额度耗尽导致空回答（配额未建模）。**"能力项"与"配额"是两个正交的治理面，我们都缺。**

## 5. 明确不适用

| 它的能力 | 为什么不适用 |
|---|---|
| 托管运行时、规格（CPU/内存）、实例最大 Session 数、自动扩缩容 | 我们自有部署链（云效→ACR→ECS），单点 2 台机 + 千级文档 |
| **Team 协作**（多 Agent 组队 + 成员权限 + Team 级定时任务） | 我们 `agent-assembly` 已定"一条循环两角色"，是另一条路线。但「**Team 是在 Agent 之上再加一层组织**」这个分层说法可留作将来 |
| Workspace 隔离 | 规模不到（多 bot 已够） |
| 观测大盘（作为工具） | Langfuse 已覆盖 trace / 成本 / 工具调用。**唯一值得一查**的是「按工具维度聚合」我们有没有 |
| IM 配置（钉钉/微信/飞书） | 企微通道已上线 |
| 依赖：CMS（Tracing）/ PrivateLink / SchedulerX3 | 引入平台即引入三条云上依赖 |

## 6. 反面与风险

1. **大量细节不可核**。文档里多处标注「即将上线」（虚拟 MCP、快速创建、注册 Agent），且 MCP 页给了认证配置**字段**却未给完整 schema；Skill 的 Markdown 定义只说 `name`/`description` 对应控制台，**未说明加载与触发机制**（是否渐进式披露、如何按需加载）。⇒ 这些只能当**概念参考**，不能当实现依据。
2. **"治理"叙事里含产品语言**。Agent Studio 那套"开发 / 运行 / 进化 / 商业化"生命周期、以及 AgentCore 的"多来源统管于一个平台"，属产品定位话术而非规范 —— 与 `./2026-10-14-enterprise-ai-architecture.md` 对"enterprise-grade 是形容词不是规范"的判断同源。取设计，不取叙事。
3. **托管 = 调用与数据出企业边界**，并引入 CMS / PrivateLink / SchedulerX3 三条依赖；另有独立计费说明与限免公告（成本模型与自建不同）。对内部数据场景，这是要单独评估的一类风险。
4. **术语同形不同义，须说清**：AgentCore 的 "Harness" 是"平台代管的运行框架"，我们的 "harness" 是"代码级自建运行框架"。同名不同层 —— 跨语境引用必须带限定语，否则会漂移（呼应 `docs/adr/` 里 glossary 的术语纪律）。
5. **不要把它的 API 清单当我们的实现方案**。记忆空间的 `AddMemories`/`SearchMemories` 等是**它家产品的接口**，对我们只是"两分法存在且可行"的证据；照搬会绑定到一套我们不需要的托管语义。

## 7. 待确认项

| # | 待确认 | 为什么重要 | 怎么确认 |
|---|---|---|---|
| 1 | 电子书 PDF 正文（Agent Studio 手册）是否值得读 | 只读到简介，可能含"进化 / 商业化"阶段的具体做法（如 skill 运营、成本归因） | 登录下载 `/download/8609` 后读 |
| 2 | Skill 的加载/触发机制（是否渐进式披露） | 决定「它的 skill 治理」是否与我们按需加载同构，能否直接借版本模型 | 读 PDF 或实测 |
| 3 | Token 配额超限的**行为**（硬拒绝 or 仅告警） | 决定我们补 F-44（配额）时是"闸门"还是"计量" | 文档未写，需查计费说明或实测 |
| 4 | Team 内部的编排协议（A2A / AG-UI / 自有） | 影响我们对"多 Agent 组织"的分层取舍 | 文档只在"开发参考"列了 AG-UI 协议，未说明 Team 内部用什么 |
| 5 | 我们的 Langfuse 是否有「按工具维度」聚合 | 观测大盘唯一值得对照的维度 | 查 Langfuse dashboard / 聚合代码 |

## 8. 来源

**外部（本次实读，2026-10-15）**

| 页面 | URL |
|---|---|
| AgentCore 产品概述（核心功能 / 应用场景 / 依赖产品 / 支持地域） | `https://help.aliyun.com/zh/agentcore/agentcore-product-overview` |
| AgentCore 基本概念（Workspace / Agent 四形态 / Skill / MCP / 凭证 / Team） | `https://help.aliyun.com/zh/agentcore/agentcore-basic-concepts` |
| 管理 Agent（创建方式 / Token 配额 / 资产五类页签 / 资源配置 / 网络配置） | `https://help.aliyun.com/zh/agentcore/agent-management/` |
| 管理记忆空间（长期记忆 vs 会话历史 / Scope / Metadata / API 清单） | `https://help.aliyun.com/zh/agentcore/memory-space-management/` |
| 管理 Skill（草稿→审核→发布 / 版本管理 / 可见性） | `https://help.aliyun.com/zh/agentcore/management-skills/` |
| 管理 MCP 工具（连接远程 / HTTP 转 MCP / Transport / 认证配置） | `https://help.aliyun.com/zh/agentcore/managing-the-mcp-server` |
| 管理连接器（Qoder CLI） | `https://help.aliyun.com/zh/agentcore/manage-connectors` |
| 查看观测大盘（请求概览 / Token / 模型 / 工具调用分析） | `https://help.aliyun.com/zh/agentcore/view-observation-dashboard` |
| 管理 Agent（旧路径，内容同上） | `https://help.aliyun.com/zh/agentcore/manage-agents` |
| 电子书 8609（百炼 Agent Studio 产品手册，仅简介可读） | `https://developer.aliyun.com/ebook/8609` |

**本地（本项目）**

- `src/infra/llm/provider/spec.py:37`（`api_key_env`）、`:42`（`supports_tools`）—— 凭证/能力项已有雏形
- `src/infra/db/models/chat.py:14-20` —— `sessions` 的隔离维度（`user_id` / `agent` / `kb_id`）
- `docker-compose.image.yml:224` —— `ALERT_WEBHOOK_TOKEN` 硬编码（§4.2 的实例）
- `docs/context-memory-research.md`、`docs/adr/0018` —— 记忆与持久化的既有结论
- `docs/research/2026-10-14-enterprise-ai-architecture.md` —— 规模判断的同源结论
