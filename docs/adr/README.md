# 架构决策记录（ADR）

> 本目录存放**架构决策记录**（Architecture Decision Record）：一条决策一份文件，记录**为什么这么定**。
> 与 `docs/superpowers/specs/`（设计文档）、`docs/openspec/changes/`（变更编排）是**三种不同的东西**，边界见「与既有文档的分工」。

## 什么时候写 ADR

**写**（满足任一）：

- 决策**不可逆或回退成本高**（选型、数据模型、接口契约、部署形态）
- 存在**多个合理候选**，需要一个记录说明为什么选 A 不选 B
- 决策**违反直觉或与社区主流不同**，后人容易"改回去"
- 决策是**取舍**（牺牲了某个明确的代价换来另一个收益）
- 后人**可能重新争论**同一件事（写下来即终止重复争论）

**不写**：

- 实现细节、代码风格、命名偏好（改进代码即完成）
- 一次性的临时方案（如调试埋点）
- 可以由代码/测试自证的事实
- 已经写在 `CLAUDE.md` / `docs/agents/rules.md` 里的规约（那类属**规则**，不是**决策**）

## 与既有文档的分工

| 文档 | 回答什么 | 生命周期 | 粒度 |
|---|---|---|---|
| `docs/adr/` | **为什么当初这么定** | ✅ **不可变**：只追加；要改就写新 ADR **取代**旧的，旧的不删 | 一条决策 |
| `docs/superpowers/specs/` | **这次变更怎么设计**（问题→方案→影响面→验证→风险） | 可随设计演进修改 | 一次变更 |
| `docs/openspec/changes/` | **这次变更怎么执行**（proposal / design / specs / tasks） | 实施完成后归档 | 一次变更 |
| `docs/agents/` | **当前生效的规则与契约**（怎么写代码、接口长什么样） | 持续维护，只描述**现状** | 一条规则 |

**关系**：一次变更（spec/change）可能产出**多条** ADR；ADR 记录其中**不可逆的那几条决策**，spec 记录完整的方案。

**ADR 不写"变更历史"**：`docs/agents/` 的规则文档写"现在的机制"；ADR 写"为什么在某个时点选了这条路"。前者会被后续变更改写，后者不改。

## 命名与编号

```
docs/adr/NNNN-<kebab-case-短标题>.md
```

- `NNNN`：四位递增序号，从 `0001` 开始，**不跳号、不复用**
- 短标题：**英文** kebab-case（便于检索与跨工具引用），如 `0007-drop-classify-node.md`
- 正文用**中文**（与项目文档一致）

## 取代关系与头部字段

头部字段固定四个，取值口径如下：

| 字段 | 何时填 | 取值 |
|---|---|---|
| `Supersedes` | 本 ADR 取代了在先的决策 | `ADR-NNNN`（完全）；局部写 `ADR-NNNN 的「<哪一条>」（局部）` |
| `关系` | 与在先 ADR 有关联但**不构成取代** | 一句话说明关联 |
| `修订记录` | 仅「原地修订」例外（见下）时填 | 改了什么 + 为什么允许原地改 |
| `Status` | 恒填 | 见「状态取值」；**反向指针写在 `Status` 上**（不另设 `Superseded by` 字段） |

反向指针只有两种形态，按取代范围二选一：

| 取代范围 | 旧 ADR 的 `Status` |
|---|---|
| **完全取代** | `Superseded by 0009` |
| **局部取代** | `Accepted（**<哪一条>已被 ADR-0009 取代**，其余仍有效）` |

**完全取代**：新 ADR 头部填 `Supersedes：ADR-0003`；旧 ADR 的 `Status` 改为 `Superseded by 0009` —— 该 Status 本身即反向指针。

**局部取代**（本仓最常见的形态）：只推翻旧 ADR 的**某一条**决策或某一段陈述，其余继续有效。

- 新 ADR 填 `Supersedes：ADR-0003 的「决策 3」（局部）`
- 旧 ADR 的 `Status` 写明是哪一条：

  ```markdown
  - **Status**：Accepted（**决策 3 已被 ADR-0009 取代**，其余仍有效）
  ```

> 反向指针**必填，不是可选**。它只动 `Status` 一行，属导航元数据，**不违反「正文不可变」**（正文一字不改）。缺了它，后人单读旧 ADR 会把已被推翻的那条当成仍然成立 —— 这是本目录最容易误导人的失效方式。

**原地修订（例外，须满足条件）**：**仅当该 ADR 尚未落地任何代码**时，允许直接改正文，并在头部 `修订记录` 留痕（含原内容与改写理由）。一旦已落地代码，一律改为「新开 ADR + 反向指针」。先例见 ADR-0003。

**为什么不把修订追加在同一文件末尾**：ADR 的价值是「某个时点为什么这么定」的快照。同文件追加会让读者仍需通读全文才知道哪条被改，文件也退化为滚动的现状文档 —— 那是 `docs/agents/` 的职责。取代关系必须**跨文件可导航**，靠头部字段 + 索引表解决。

**怎么保证后续不走偏**：以上是规则，靠人记会漂（本目录已有四种写法并存，见索引表）。机械校验在 `python -m src.cli.check_adr`，随 pre-commit 的 `check-adr` hook 全量运行，缺字段 / 指针不对称 / 索引漏登记都会以 error 拦截提交。

## 状态取值

| Status | 含义 |
|---|---|
| `Proposed` | 已写下、尚未定论（评审中） |
| `Accepted` | 已决定并生效 |
| `Rejected` | 考虑过但没采纳（**也值得记**，避免以后重复讨论） |
| `Deprecated` | 曾经生效，现已不再推荐（但未被取代） |
| `Superseded by NNNN` | 已被更新的决策取代 |

## 索引

**这是本目录的导航入口** —— 想知道"哪条还有效"，看这里，不必逐个打开文件。新增 ADR 时必须同步在此登记一行（`check-adr` 会校验覆盖完整性）。

| # | 决策（一句话） | Status | 取代关系 |
|---|---|---|---|
| [0001](0001-retrieval-fetch-and-dedup-scope.md) | 检索取数取消每文档配额，改为大候选池 + 内容级去重 | Accepted | **「候选池成本收益」「多库路径 not kb_id 分支」两段陈述已被 0014 更正** |
| [0002](0002-prompt-carrier-yaml-two-phase.md) | prompt 载体迁到 Git 内 YAML，终态为远端读取 | Accepted | 0010 为其第一阶段内部的子决策（不取代） |
| [0003](0003-prompt-layering-six-sections.md) | prompt 六段模型，运行时各段恒定、base 三选一 | Accepted | **决策 3 已被 0009 取代**；0010 为其子决策 |
| [0004](0004-storage-consolidation-single-postgres.md) | 三套存储收敛到单个 PostgreSQL 实例 | Accepted | **「trace 在 ClickHouse」一句已被 0011 推翻** |
| [0005](0005-hybrid-fusion-in-application-layer.md) | 混合检索的融合留在应用层 | Accepted | — |
| [0006](0006-chinese-lexical-retrieval-shape.md) | 中文词法检索 = jieba 预分词 + tsvector('simple') + 前缀 OR | Accepted | — |
| [0007](0007-retire-mysql-rollback-basis.md) | 退役 MySQL 回滚基座（容器 / 卷 / 镜像全部清理） | Accepted | **「历史迁移脚本保留」一条已被 0008 取代** |
| [0008](0008-delete-historical-migration-sql.md) | 删除历史手工 SQL 迁移文件 | Accepted | 局部取代 0007 的一条 |
| [0009](0009-prompt-base-three-way-replacement.md) | `base` 段按三选一（替换）解析 | Accepted | 局部取代 0003 的决策 3 |
| [0010](0010-delist-langfuse-prompts.md) | Langfuse 侧 3 个 prompt 出列，本地模板为唯一事实源 | Accepted | 不取代 0002；0002 内部的子决策 |
| [0011](0011-langfuse-v2-downgrade.md) | 自托管 Langfuse 由 v3 降至 v2，去掉 ClickHouse 与 worker | Accepted | 局部推翻 0004 的一条附带陈述 |
| [0012](0012-langfuse-trace-content-and-retention.md) | trace 记录请求/回答原文并保留 30 天 | Accepted | 承载 0011 复查条件②的复评（确认，不取代） |
| [0013](0013-trace-retention-purge-via-langfuse-sql.md) | trace 保留期清理的后端定为直连 Langfuse PG 的 SQL | Accepted | 为 0012 的 30 天保留期提供可工作的删除机制（不取代） |
| [0014](0014-candidate-pool-not-rerank-input-bound.md) | 更正 ADR-0001 的两段陈述（候选池非精排输入上界；多库路径死因） | Accepted | 局部更正 0001 的两段陈述（决策不变） |
| [0015](0015-langfuse-tool-spans-imperative-api.md) | 工具 span 用命令式 Langfuse client 创建（client 实例取 decorators 单例） | Accepted | 为 `llm-tracing` 扩展观测面；与「纯装饰器」并存，不取代任何在先 ADR |
| [0016](0016-unify-agent-loop-to-create-agent.md) | 统一 agent 循环到 `create_agent`（唯一装配入口 + 四件套 middleware，领域阶段留外层图） | Accepted | 沿用 0015 的命令式 span 接入方式；代价是 `agent_turn` 的 TTFB（`completion_start_time`）不再设置 |
| [0017](0017-context-ladder-and-memory-scope.md) | 上下文治理采用四层阶梯（L0 写入侧→L1 瘦身→L2 摘要→L3 记忆）；窗口按 per-model 解析；长期记忆限 episodic + user-semantic，事实仍走 RAG | Accepted | **决策 3 记忆底座已被 0018 取代**；L0/L1 复用 0016 的 middleware 机制；记忆底座沿用 0004 的单 PostgreSQL（pgvector） |
| [0018](0018-history-vs-memory-keep-own-truth-source.md) | 对话历史与 Agent 记忆分轴；短期记忆真源不迁 LangGraph checkpointer，长期记忆底座自建、不用 Store | Accepted | 局部取代 0017 的决策 3 |
| [0019](0019-langfuse-public-exposure.md) | Langfuse 放开公网访问（`:3000` 对外），接受 EOL 服务的暴露风险 | Accepted | 局部取代 0011 的「暴露面收敛到仅回环」 |
| [0020](0020-no-model-gateway-direct-provider.md) | 不启用 LiteLLM 模型网关，三类模型直连 Provider | Accepted | 与 `model-config` / `model-gateway` 规格相关；不取代任何在先 ADR；**0021 在其『不引入替代网关』边界内引入应用内 provider 兼容层** |
| [0021](0021-provider-compat-layer.md) | 引入应用内 provider 兼容层（registry 数据面 + 思考形状归一化 + 角色解析），拒绝网关 | Accepted | 在 0020 之上构建，非网关；不取代任何在先 ADR |

## 模板

复制以下内容到新文件：

```markdown
# ADR-NNNN：<一句话决策标题>

- **Status**：Proposed
- **Date**：YYYY-MM-DD
- **Deciders**：<谁定的>
- **Supersedes**：<无则省略；完全写 `ADR-NNNN`，局部写 `ADR-NNNN 的「<哪一条>」（局部）`>
- **关系**：<与在先 ADR 有关联但不构成取代时填，一句话；无则省略>
- **修订记录**：<仅「落地前原地修订」时填（含原内容与理由）；无则省略>

## 背景与问题

<是什么触发了这个决策？把约束、现状、触发事件摆出来。>
<引用可复核的证据：trace_id、日志、代码位置、实测数字。不要写"感觉""大概"。>

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A | | | |
| B | | | |

## 决策

**选 <X>。**

## 理由

<为什么是 X 而不是其他。要能回答"如果只给我一行，哪一行最关键"。>

## 后果

**正面**：

**负面 / 接受的代价**：

**不解决的问题**（明确列出，避免后人误以为这条 ADR 管了它）：

## 复查触发条件

<什么情况下应该重新审视这个决策？（如"模型能力变化""流量涨到 X""某个依赖废弃"）>
<没有则写"无"——但大多数决策都有。>
```

## 谁引用本目录

- `improve-codebase-architecture`（mattpocock 技能集）：读 `docs/adr/` 作为"**不应重新争论的决定**"来源
- `architecture-review`：ADR 是其合法输入类型之一
- `grill-with-docs`：默认产出物之一（若启用，会把 ADR 落到本目录）
