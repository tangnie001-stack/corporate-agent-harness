# prompt 段归属规则

本文件是 prompt 段**归属规则**与**逐条条件判据表**的唯一归属文档。
规则依据见 `docs/openspec/changes/prompt-layering-and-domain-binding/design.md`（D2 / D9 / D12.6 Q6 / D12.7 Q11），
逐条 before/after 对照见同目录的 `prompt-mapping.md`。本文件不复制二者正文。

## 1. 五段归属表

| 段 | owner（内容边界） | 承载 | 可替换性 |
|---|---|---|---|
| `base` | 角色、领域方法（指标口径 / 报告期 / 同比等）、**该模式默认检索方法**、以优先级指针句收尾 | YAML `section: base` | **可替换**（三选一） |
| `runtime_contract` | 数据·指令边界、运行上下文、完成条件、默认语言 | YAML `section: runtime_contract` | 不可替换，**无条件**注入 |
| `sources` | **来源选择与降级**：何时检索 / 何时换词再检 / 何时联网 / 何时如实说明；证据充分性判据 | YAML `section: sources` | 不可替换，**逐条**条件渲染 |
| `tools` | 通用工具使用约定 + 单工具**调用时机**（具体参数留在工具 `description`） | YAML `section: tools` | 不可替换，**逐条**条件渲染 |
| `output` | **通用输出形态**（格式 / 图片 / URL 保真 / 完成前自检）、引用编码、委派返回内容的引用规则 | YAML `section: output` | 不可替换，**无条件**（委派条为条件） |
| `skills` | 按需加载的方法论 | **工具描述（目录）+ 消息层（正文两分支）** | **不参与 system prompt 组装**（D6） |

> **system 段的组装与下发分属两处**：段的**组装**（拼接、条件注入）仍在 `build_system_prompt`
> / `build_prompt`，由 `agent` 节点首轮调用；组装结果的**下发**（把 system 段施加到每次模型
> 调用）经 `src/agents/graph/middleware.py` 的 `SystemMessagesMiddleware`（读图状态
> `_system_messages`，首条进 `system_message`、其余前插）。两条 system 消息（主 system +
> 未绑定提示）的通道不变。

## 2. 段数的最小性（为什么不合并）

段数不是照搬外部方案，是从"需要几种不同的可变性"推出来的。真正必要的区分只有三类：

| 类 | 特征 | 本项目的段 |
|---|---|---|
| 可替换 | 由用户选择或领域决定，允许整体替换 | `base` |
| 不可替换 + **逐条**条件渲染 | 内容随本轮能力变化，且每条规则的判据可能不同 | `sources`、`tools` |
| 不可替换 + 无条件 | 与"是否绑库""注册了哪些工具"无关，恒定注入 | `runtime_contract`、`output` |

三个必答问题：

1. **`sources` 与 `tools` 为什么不合并**：条件渲染的**判据不同** —— `sources` 按"来源可用性 + 适用域（是否绑库）"判，`tools` 按"工具是否注册"判。合并后同一列表里出现两类判据，只能退化成整段开关，且会连坐删除不相关规则。
2. **`runtime_contract` 与 `output` 为什么不合并**：前者是**权限与边界**，后者是**呈现形态**。两者复查触发条件不同（边界随安全要求变，输出随用户格式需求变），合并会让"边界收紧"与"输出改样式"互相绑架。
3. **`skills` 为什么是格位而不是 system 段**：它不参与拼接。计入段模型只为"将来改承载通道时不必动段模型"。**六段的准确读法是 5 个 system 段 + 1 个非 system 载体格位。**

## 3. 逐条条件判据表（代码与文案之间的唯一对照）

判据**留代码**（`src/rag/prompt.py` 的规则表），**不由 YAML 声明** —— 模板只管正文（"能改文案"），挂载点由代码决定（"不能改挂载"）。理由：判据是**行为契约**，挂错依赖会让规则在不该出现的时候出现（例如把"越界→联网"挂到 `retrieve_kb` 上，缺 `search_web` 时就永远不输出）。这与"`VERIFY_*` / `FORK_*` 不模板化"同源。

判据是**复合的**：`retrieve_kb` 在**所有**会话都注册（`src/agents/tools/rag_tools.py:238`），只看工具名会让"实质性提问先检索再作答"出现在**未绑定知识库**的会话里，与同轮注入的未绑定提示直接矛盾。故凡带适用域者，SHALL 同时判定**工具已注册**与**适用域成立**。

| 规则短语 | 模板 id | 工具依赖 | 适用域 |
|---|---|---|---|
| 证据需求判断 / 来源限制 / Skill 不验证事实 / 相关但不足 / 不虚构来源 / 无须检索的场景 / **不得以"任务已完成"为由跳过取证** | `sources-general` | 无 | 无 |
| 先检索再作答、不预判范围、含核心实体才算相关；换一种问法重新检索（第二次 `top_k=10`） | `sources-kb-ladder` | `retrieve_kb` | 已绑定知识库 |
| 越界声明 → 联网并保留网络来源；联网也无 → 最后才说明；KB 能答不联网 | `sources-kb-web-rules` | `search_web` | 已绑定知识库 |
| 态 A 第二条 system 消息 · 核心句（未绑定提示） | `sources-kb-unbound` | 无 | 未绑定知识库 |
| 态 A 第二条 system 消息 · 联网句 | `sources-kb-unbound-web` | `search_web` | 未绑定知识库 |
| 通用执行约定（只使用已提供工具 / 规划 / 并行与顺序 / 完成前检查结果 / 失败重试条件） | `tools-execution` | 无 | 无 |
| 缺关键信息先澄清 | `tools-ask-user` | `ask_user` | 无 |
| 何时委派、传什么材料 | `tools-delegate` | `delegate_task` | 无 |
| 数据·指令边界、运行上下文、完成条件、默认语言 | `runtime-contract` | 无 | 无 |
| 通用输出形态（格式 / 图片 / URL 保真 / 完成前自检） | `output-presentation` | 无 | 无 |
| `[n]` 引用编码 | `output-citation` | 无 | 无 |
| 委派返回内容的引用规则 | `output-delegate-citation` | `delegate_task` | 无 |

> `output-delegate-citation` 管的是**主 agent 侧**的委派引用规则。fork 子代理侧的 `[n]`
> 指示按**入口路径**分派（委派路径子代理不标、直出路径子代理自标），载体 `DelegateRun.via`；
> 术语与语义归属见 glossary.md「按路径的引用编号策略」，本文件不复制其正文。

> 上表 `sources-kb-unbound` / `sources-kb-unbound-web` 两条不属 `_SECTION_RULES`（`src/rag/prompt.py`），
> 其判据在 `_build_unbound_message`（同文件，读 `search_web` 是否注册决定是否追加联网句）；
> 它们产出的是**态 A 第二条 system 消息**，而非五段之一。

## 4. 判别例：默认检索方法（`base`）vs 来源选择（`sources`）

| 规则 | 归哪段 | 为什么 |
|---|---|---|
| 概念与改写表述用语义检索；精确术语、错误信息或编号用关键词检索 | `base` | 属"**拿到材料后怎么读**"，不涉及调用哪个工具 |
| 返回片段不完整或不足以支撑结论时再补读上下文 | `base` | 同上 |
| 同一任务中已完整返回过的内容不必再读一遍 | `base` | 属该模式的默认检索工作流 |
| 检索结果为空或全部明显不相关时换一种问法重新检索 | `sources` | 涉及"**何时再次调用工具 / 失败后降级**" |
| 再次检索仍无果时联网 / KB 能答不联网 / 联网也无则说明未找到 | `sources` | 同上 |
| **证据足够即停止检索** | `sources` | 属来源选择与证据充分性（**P2 才写**，见计划 P1/P2 边界裁定） |

判别线：**是否涉及"调用哪个工具 / 何时调用 / 失败后降级"** —— 涉及才归 `sources`。

⚠ 上表最后一行与 `base` 的"已完整返回过的内容不必再读"是**两件不同的事**，分属两段，SHALL NOT 合并或互为拷贝。

## 5. 领域 base 与智能体预设的定位差异

二者是**同一段位（`base`）的互斥候选** —— 三选一替换语义下任一时刻只有其一生效，因此内容重叠 SHALL NOT 被判为"同一事实两个 owner"。

| | 定位 | 触发 | 内容倾向 |
|---|---|---|---|
| 智能体预设（`agents/*.md`） | 用户**显式选定**的专家角色 | 会话选定预设 | 角色 + 工作原则 + 输出骨架 |
| 领域 base（`section: base` + `domain: <值>`） | 未选预设时该知识库的**默认视角** | 未选预设且知识库 `domain` 已知 | 角色 + 领域方法 + 默认检索方法 + 指针句 |

⚠ **明确接受的代价**：选定预设后，知识库领域方法不参与组装（"选了财务专家 + 绑定人事库"时人事领域方法消失）。系统记日志但不阻断。这是替换语义的既定代价，**不是缺陷** —— 后人不应把它当 bug 去修。

## 6. 已知重复（登记 / 已处置）

| 重复项 | 位置 | 处置 |
|---|---|---|
| classifier 的 `missing_entities` 与 `ask_user` 的澄清是同一事实的两个 owner | `task-classifier-*` 模板 vs `tools-ask-user` | 记入本表为已知重复，另开 change |
| `src/cli/compare_rewrite.py` 的 4 处重复 prompt | 该文件 | 属"副本清理"，与本变更段模型无关 |
| `retrieve_kb` docstring 的"何时调用"（事实性内容才调、闲聊不调）与 `sources` 段的证据需求判断是同一事实的两个 owner | `src/agents/tools/rag_tools.py:88-91` vs `sources-general` / `sources-kb-ladder` | 记入本表为已知重复，另开 change |
| `search_web` docstring 的"何时调用"（检索空/不相关才联网、KB 能答不调）与 `sources` 段的联网规则逐条重复 | `src/agents/tools/web_tools.py:43-45` vs `sources-kb-web-rules` | 记入本表为已知重复，另开 change |
| `ask_user` docstring 的"何时调用"（缺关键实体且无法推断才问）与 `tools` 段的澄清规则逐条重复 | `src/agents/tools/ask_tools.py:66-67` vs `tools-ask-user` | 记入本表为已知重复，另开 change |
| `delegate_task` 的 `description` 的"何时调用"（需领域专家能力才委派、轻量自答）与 `tools` 段的委派规则逐条重复 | `src/agents/skills/delegate_task.py:59-63` vs `tools-delegate` | 记入本表为已知重复，另开 change |
| `finance-analyst` skill 正文 4 条与 fork 子代理**实际接收的 prompt** 逐条重复 | `skills/finance-analyst/SKILL.md`（已删）vs `agents/finance-expert.md:9-12` 原则 1/2/3（第 1–3 条；第 3 条近乎逐字）+ `src/config/prompts/__init__.py:83` 执行者默认人设（第 4 条） | **2026-09-22 已删除**：fork 子代理的 system prompt 只有「执行者人设 + 执行契约」（`src/agents/skills/executor.py:279-295`），父级组装的 `base` / `output` / `runtime-contract` 段**从不进入**子代理上下文；4 条在上述两处均有对应、无独占内容。第 4 条"每个事实标注数据来源编号"与 `:83` 明确要求子代理**不**标注 `[n]`（`[n]` 由主 agent 按 `output-delegate-citation` 补）**语义相反**，删除即消除矛盾。另与存活 fork skill `financial-statement-analyzer` 功能重叠（同删 `finance-qa` 的判据）。连带处置依赖它的测试 |

> **2026-09-21 审计**：`retrieve_kb` / `search_web` / `ask_user` / `delegate_task` 四个工具的 description 已逐条比对 `sources` / `tools` 段，四条均含"何时调用"表述重复（上表四行）。本期只审计、不改文案（工具 docstring 文本改写属 `design.md` 的明确不做项）。
