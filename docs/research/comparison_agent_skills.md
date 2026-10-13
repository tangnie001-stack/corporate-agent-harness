# 技能对比：research vs firecrawl-deep-research vs architecture-review

生成日期：2026-09-15

## 0. 阅读范围（一手来源）

| 技能 | 来源文件 | 规模 |
|------|---------|------|
| `research` | `~/.agents/skills/research/SKILL.md`（+ `agents/openai.yaml`） | 12 行 / 794 B |
| `firecrawl-deep-research` | `~/.codebuddy/skills/firecrawl-deep-research/SKILL.md` | 3,474 B |
| `architecture-review` | `github.com/owainlewis/blueprint` → `skills/architecture-review/SKILL.md` | 4,770 B（无附件） |

结论均以 SKILL.md 正文为准，不采信技能市场（skills.sh）上的描述文案。

## 1. 核心结论

**三者不是竞品，是三件不同层的事：**

| 技能 | 本质 | 回答的问题 |
|------|------|-----------|
| `research` | **检索纪律** | 怎么查、查到什么程度算够、结果落到哪 |
| `firecrawl-deep-research` | **交付物规格 + 采集计划** | 报告长什么样、要采多少源 |
| `architecture-review` | **评审流程** | 一个已有方案能不能上、有什么必须先答的问题 |

所以"哪个更好"是伪命题；该问的是"我要做的事，缺的是哪一层"。

## 2. 逐项对比

| 维度 | research | firecrawl-deep-research | architecture-review |
|------|----------|------------------------|---------------------|
| 联网 | ✅ 未指定工具 | ✅ 需 `FIRECRAWL_API_KEY` | ❌ **不联网** |
| 一手来源要求 | **强制**：primary sources，"Follow every claim back to the source that owns it" | **偏好**："Prefer primary sources **when available**"（允许降级） | **强制**（针对内部现状）：claims "unverified until code, tests, schemas, configuration, infrastructure, or relevant runtime evidence supports them" |
| 子代理用途 | **并行**："so you keep working while it reads" | 并行，**按角度分工**（overview / technical / market / contrarian / primary sources） | **独立性**："a fresh subagent that **did not write** the proposal" |
| 独立性失败时 | 无规定 | 无规定 | ⭐ **fail-closed**：无法起独立子代理则 "stop and report that independent review is **blocked**" |
| 反方观点 | 未提 | ⭐ 专门章节 `Contrarian Views And Risks` | ⭐ 要求主动找"更简单的方案"，且只读、不重写提案 |
| 输出结构 | **不规定**（只要求：单一 MD + 每 claim 标源 + 放对位置） | ⭐ 固定 7 段（Exec Summary / Key Findings / Detailed Analysis / Contrarian Views / Open Questions / Sources / Rerun Inputs） | findings + open questions + assessment + **verdict** |
| 结论判定 | 无 | 无 | ⭐ 三档 Verdict：`Approve` / `Request changes` / `Blocked`；并明确"不要因为文档详尽或模板齐全就批准" |
| 强制交互 | 无 | ⭐ 强制先问一个问题（运行时长 → Quick/Thorough/Exhaustive 三档深度） | 无（但要求给足完整上下文） |
| 成本 | 低（1 个背景代理） | 高（Quick 5-10 源 / Thorough 15-25 / Exhaustive 25+） | 低（一次评审） |
| 外部依赖 | 无 | ⚠️ **API key 必填** | 无 |
| 明确不适用 | 无 | ⭐ 有：product picks / top-N 列表 / quick lookup / 任何短搜索能答的 | 有：不审实现代码、不重写提案、不规划工作 |

## 3. 三个值得单独记的差异

### 3.1 同样用子代理，目的相反

- `research` 的背景代理是为了**并行**——"你继续干活，它在读"，是**效率机制**
- `architecture-review` 的 fresh subagent 是为了**独立**——"没有写过提案的人来审"，是**认知机制**

同名机制、相反意图。评估任何"用子代理"的技能时，先看它想解决的是速度还是偏见。

### 3.2 一手来源要求有强弱之分

三档：`require`（research）> `prefer when available`（firecrawl）> 未提。
`architecture-review` 是特例——它不要求外部来源，而是要求**对内部现状的证据**（代码/测试/schema/配置/运行证据），这正好治"AI 说得头头是道但没查过"。

### 3.3 只有 architecture-review 有失败语义

`research` 和 `firecrawl` 都假设过程会成功，**没有失败分支**。
`architecture-review` 明确：拿不到独立评审就**停**，并把它记成 `Blocked` 结论。这是三个里唯一"宁可不给结论"的。

## 4. 映射到「讨论技术架构 / 方向 / 框架 / 业界趋势」的需求

| 想做的事 | 用哪个 | 说明 |
|---------|--------|------|
| 业界情况 / 技术趋势 / 全景 | **firecrawl-deep-research** | 唯一能产出正式引用报告的；⚠️ 需 API key |
| 技术架构 / 方向 / 方案的独立评审 | **architecture-review** | 唯一有独立机制 + verdict 的；但**不联网**，审的是你已写好的方案 |
| 查具体事实 / API / 规范 | **research** | 最轻、纪律最纯、零依赖 |
| 挖技术缺陷 / 找盲区 | 三者都不直接覆盖 | 需另配 `devils-advocate`（见 `reference-projects.md` 附录方向） |

### 关键缺口

**"业界趋势"只有 firecrawl 能做，且依赖 API key。**

- `research` 能联网，但**不规定报告结构与多角度分工**——它只保证"查得实"，不保证"看得全"
- `architecture-review` 完全不涉及（不联网、不评外部生态）
- 已装技能里也没有对口的（`tech-trend-watch` 只覆盖 JS 生态，数据源是 State of JS/CSS）

若无法使用 Firecrawl API，这个缺口需要自己写技能补齐。

## 5. 建议的组合用法

| 场景 | 组合 |
|------|------|
| 日常事实核查 | `research` |
| 重大方向的业界调研 | `firecrawl-deep-research`（选 Thorough 档） |
| 方案定稿前 | `architecture-review` |
| 完整链路 | `research`/`firecrawl` 收集 → 写方案 → `architecture-review` 审 → `devils-advocate` 挑盲区 |

**注意顺序不可颠倒**：`architecture-review` 的输入是**已成文的方案**，不能用来做探索式调研。

## 6. 方法说明

本轮为直接读取三个 SKILL.md 全文（合计约 9 KB），**未派背景代理**：`research` 技能的背景代理设计目标是"边读边干"，在 9 KB 场景下派代理的固定开销大于读取成本。该技能的纪律（一手来源、单一文件、按仓库约定落盘）已遵守——本文件落在 `docs/research/`，与既有 `comparison_qyznkf_vs_financial_rag.md` 同构。
