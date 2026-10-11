## Context

**现状**（`docs/tmp/deep-research-prompt-management.md` 第一部分穷举）：

prompt 有 7 类来源：静态常量（`src/config/prompts.py` 19 个）、Langfuse 远端（3 个，无版本固定）、`agents/*.md` 智能体预设（**整体替换** base）、`skills/*/SKILL.md`（叠加、跨轮持久）、运行时组装（`src/rag/prompt.py:26-77`）、工具 docstring、`cli/compare_rewrite.py` 的重复分支。

组装顺序（`src/rag/prompt.py`）：

```
if persona:            base = persona          （:48-53）★ 整体替换
else:                  base = 内置基础段
+ KB_BOUND_RETRIEVAL_DISCIPLINE  if kb_bound and persona   （:58）
+ DELEGATE_GUIDANCE_SECTION      if has_skills or not persona（:64）
+ KB_UNBOUND_SYSTEM_PROMPT       if not kb_bound          （:68）
+ INLINE_CITATION_INSTRUCTION    （_FALLBACK_SYSTEM_PROMPT 内）
```

**约束**：

- **没有归属规则**：`prompts.py:41` 的注释自述基础段含"一律先检索 / 含核心实体才算相关 / 二次检索加大候选 / 确认无法覆盖才联网兜底"，即把 role + 流程 + 工具使用习惯 + 输出规则混在一层。
- **同一条事实有两个 owner**：`KB_BOUND_RETRIEVAL_DISCIPLINE` 是规则 2 的部分拷贝，且拷贝时删掉了出口指引（"全部明显不相关则按第 4 条处理"）。
- **注释已记录当时的推理且该推理已被证伪**：`prompt.py:54-57` 写"persona 为空时人设层即 FINANCIAL_SYSTEM_PROMPT，其处理流程 2–9 已含先检索后作答；无条件注入会破坏默认行为逐字不变（端到端快照）需求"。
- **`tests/rag/test_prompt_layers.py:59` 断言 `"基础段正文" not in content`** —— ⚠ **原文档称该断言"保护的是缺陷、必然失败"，评审后确认该判断错误**：D3 与 spec 都**保留**"预设替换 base"语义，P1 后 `base` 即预设正文，"基础段正文"仍不会出现，该断言**继续成立**，不需要改。真正会打断该文件的是 `:7-11` 的模块级 `import KB_BOUND_RETRIEVAL_DISCIPLINE`（常量删除 → import 失败 → 整文件 9 条全红）与 `:24-33`/`:100-107` 构造真实 `PromptManager()`（真实网络请求）。
- **既有否决**：`docs/tmp/deep-research-prompt-management.md` 第四部分明确否决引入 Jinja2（dify 双语法并存的教训），占位符只用"仅标识符式替换、未知变量原样保留"。
- **既有立场**（`docs/agents/requirements_pool.md:143-146`）：主张"运行时管理优于 Git 管理"、"Langfuse 作为唯一权威来源"、"避免双轨制"。

**同域参照**（WeKnora，生产验证）：

- 载体：`config/prompt_templates/*.yaml`（11 个文件），模板含 `id` / `name` / `description` / `mode` / `default` / `i18n`（4 语言）/ `content`。
- 段模型：九段 —— `base` / `steering` / `runtime_contract` / `sources` / `tools` / `skills` / `output` / `memory` / `protocol`；唯一入口 `BuildSystemPromptSections`，空段丢弃，每段打 `[Agent][Prompt] section=... bytes=...`。
- 归属原则（其文档末节 + deepseek-harness 的 `every fact in the prompt has exactly one owner`）：角色与方法 → `base`；查什么 → `sources`；怎么调用 → 工具定义 / `tools`；输出形态 → `output`；引用编码 → `protocol`；**并明写"不要把同一条规则复制到多个模板中来'加强优先级'"**。
- 自定义正文**只替换 `base`**，运行时各段强制保留；条件注入按**实际注册的工具集合**判断（注释明说"不用配置开关"）。
- base 极薄：rag 模式只有 5 行（角色 + 4 条 KB 检索习惯 + 一句"以运行时来源规则为准"），**不含检索阶梯、不含联网规则、不含停止条件** —— 那些都在 `runtime_contract` / `sources`。

**架构定位澄清**：本项目是通用问答助手（现阶段偏财务），目标形态是"选定知识库 → 加载该领域 prompt；未选 → 通用回答"，后续可扩展人事等领域。

## Goals / Non-Goals

**Goals:**

- prompt 与 Python 代码解耦：文案改动不再需要改 `.py`（P0）。
- 建立段模型与归属表，使"一条事实只有一个 owner"可执行、可测试（P1）。
- 让 base 可被领域内容替换，而运行时契约恒定不被覆盖（P1）。
- 消除"检索协议被 persona 覆盖"与"补丁拷贝漂移"两个实证缺陷（P1）。
- 补齐三条本项目缺失、且直接命中已实证问题的运行时规则（P2）。
- 为终态的远端读取（产品维护）预留接口。

**Non-Goals:**

- 不引入 i18n（中文单语，结构预留即可）。
- 不实现远端 prompt 服务与产品侧编辑器。
- 不引入 Jinja2 或任何模板条件/循环语法。
- 不做完整 section registry + 命名 order 表（deepseek-harness 路线，P3，暂不建议）。
- 不改检索取数口径（另见 change `retrieval-fetch-and-dedup`）。
- 不做 steering / protocol / memory 三段。

## Decisions

### D1：载体选 Git 内 YAML，分两阶段；终态为远端读取

**候选**：

| 方案 | 代价 | 收益 |
|---|---|---|
| A 保留 Python 常量 | 产品无法编辑；文案改动走代码评审与发版 | 零迁移 |
| B 外置为 Markdown 文件 | md 无结构化字段（id/mode/段名），需靠文件名与前置约定 | 可读性好 |
| C **Git 内 YAML** | 引入一个加载器与文件契约 | 有结构化字段；与 WeKnora 同形态；可平滑迁到远端 |
| D 直接上 Langfuse / DB | 现在没有产品角色与远端服务；prompt 变运行时数据 → 编译期无检查、code review 看不到、测试难覆盖 | 一步到位 |

**选 C。** 理由：

- **本期要解决的是"与代码混杂"，不是"管理权归属"。** A 解决不了前者，D 现在没有使用方。
- **YAML 有结构**（`id` / `mode` / `段名` / `content`），是 B 缺的；而 WeKnora 已在生产上用同一形态，路径已被验证。
- **与 `requirements_pool:143-146` 不冲突，是它的第一阶段**：`Git 内 YAML（开发维护）→ 远端读取（产品维护）`，终态与该处主张一致。加载入口 SHALL 设计成可替换的数据源，使第二阶段只换实现、不动段模型。
- 取 D 的时机（复查触发条件）：出现"产品需要独立改文案且不能走发版"的实际需求时。

**占位符规则**：只做**仅标识符式**替换（`{identifier}`），未知变量原样保留 —— 沿用既有否决（不引入 Jinja2）。

### D2：6 段模型与归属表

| 段 | owner（内容边界） | 本项目现状 | WeKnora 对应 |
|---|---|---|---|
| `base` | 角色 / 领域方法（指标口径、报告期、同比等）/ **该模式默认检索方法** / 优先级指针句 | `agents/*.md` **替换** + `FINANCIAL_SYSTEM_PROMPT` 混装 | `base` |
| `runtime_contract` | 数据·指令边界、当前文档范围、**完成条件（完成即停调工具）** | 缺（`KB_BOUND_RETRIEVAL_DISCIPLINE` 只有半条） | `runtime_contract` |
| `sources` | **来源选择**：何时检索 / 何时换词再检 / 何时联网 / 何时如实说明 | **缺**（检索阶梯错位在 `base`） | `sources` |
| `tools` | 通用工具使用约定（具体参数留在工具定义里） | 工具 docstring + 散落 | `tools` |
| `skills` | 按需加载的方法论 | `skills/*/SKILL.md` 注入，但走 user message | `skills` |
| `output` | 输出形态、引用编码、完成检查 | `INLINE_CITATION_INSTRUCTION` | `output` + `protocol` |

**跳过的三段及理由**：

| 段 | 不做的理由 |
|---|---|
| `steering` | 用户插话（中途补充指令）的整体代码不存在 |
| `memory` | 已由消息层承担（`_truncate_history`，`agent_node.py:30-59`），再开一段是重复 owner |
| `protocol` | 引用编码沿用 `output` 段；WeKnora 的 `protocol` 服务于 handle 机制，本项目已决定不抄（`docs/tmp/kb-boundary-visibility-problem.md` 附录 D） |

**为什么必须是"6 段 + 无条件注入"，而不是"两层"**：检索阶梯被 persona 覆盖的根因不是"替换动作"，而是**阶梯住错了层**。同一条事实若住在会被覆盖的层，任何替换语义都会丢掉它。证据：`FORK_EXECUTION_CONTRACT` 一开始就放在"子代理段"（无条件追加），所以 fork 那条路从未出过这个问题。

**段的必要性（最小性判据，2026-09-21 评审后补）**：段数不是"照 WeKnora 取六段"，而是从"需要几种不同的可变性"推出来的。真正必要的区分只有三类：

| 类 | 特征 | 本项目的段 |
|---|---|---|
| 可替换 | 由用户选择或领域决定，允许整体替换 | `base` |
| 不可替换 + **逐条**条件渲染 | 内容随本轮能力变化，且每条规则的判据可能不同 | `sources`、`tools` |
| 不可替换 + 无条件 | 与"是否绑库""注册了哪些工具"无关，恒定注入 | `runtime_contract`、`output` |

由此回答三个必答问题：

1. **为什么 `sources` 与 `tools` 不能合并为一段**：条件渲染的**判据不同** —— `sources` 按"来源可用性 + 适用域（是否绑库）"逐条判，`tools` 按"工具是否注册"逐条判。合并后同一个列表里出现两类判据，只能退化成"整段开关"（D9 已否决，且会连坐删除不相关规则）。
2. **为什么 `runtime_contract` 与 `output` 不能合并**：前者是**权限与边界**（数据·指令边界、完成条件），后者是**呈现形态**。两者的复查触发条件完全不同（边界随安全要求变，输出随用户格式需求变），合并会让"边界收紧"与"输出改样式"互相绑架。
3. **`skills` 是载体格位而非 system 段**：它不参与拼接（spec 已明写）。计入段模型只是为了"将来改变承载通道时不必动段模型"。因此六段的准确读法是 **5 个 system 段 + 1 个非 system 载体格位**。

该判据同样要写进 `docs/agents/prompt-ownership.md`（task 2.1），避免 spec 与归属表两处说法不一。

**⚠ 与既有"三层"规范的关系（不是两套，是嵌套）**：在效 capability `prompt-composition`（2026-09-18 由 `session-agent-and-skill-invocation` 归档并入）已规定 system prompt 按**三层**组装。本变更的**六段与之嵌套**，不另立一套：

```
┌─ system prompt ─────────────────────────────────────────────┐
│  ① 人设层（可替换）                                          │
│    └─ base 段        ← 领域 base 或 preset 正文（三选一）      │
│  ② 环境约束层（系统强制，不可被 preset / 领域内容覆盖）        │
│    ├─ runtime_contract 段   数据·指令边界 / 完成条件           │
│    ├─ sources 段            来源选择 / 检索阶梯                │
│    ├─ tools 段              工具调用时机 / 委派                │
│    └─ output 段             输出形态 / 引用编码                │
└─────────────────────────────────────────────────────────────┘
（③ 运行时层 —— 不进 system prompt）
     ├─ skills 段            ← [[skill-injection]] 隐藏 user 消息
     └─ verify 指引          ← 运行期注入的 SystemMessage
```

本变更对 `prompt-composition` 的修改即：把"三层"细化为上表（② 内部再分四段）、把 base 解析写成显式的三选一（替换）、并补三条 ADDED（段归属 / 检索饱和与不重复读取分属两段 / 分段字符数日志）。

### D3：`base` 段按**三选一（替换）**解析

```
选定智能体预设        → preset 正文
否则知识库领域已知    → 该领域的默认 base
否则                 → 内置通用 base
```

**候选**：**三选一（替换）** / 领域 base + 预设叠加 / 预设优先而领域被忽略。

**选三选一（替换）。** 理由：

- **替换 + base 瘦身是成对的**：原缺陷不是"替换"这个动作，而是 **base 是杂物袋**（`FINANCIAL_SYSTEM_PROMPT` 十二条规则含唯一出口）。base 瘦身到"人设 + 领域方法"后，被替换掉的不再包含任何系统规则 —— 替换因此是安全的。Anchoring：WeKnora 自定义正文**只替换 `base`**，运行时各段强制保留（`internal/agent/prompts.go:400-426` 的 `BuildSystemPromptSections` 模板选择与段拼装；`docs/agent-prompt-assembly.md` 的场景表「用户自定义 Agent 正文」行）。⚠ 注意 `internal/config/agent_prompts.go:19` 的 `if body != "" || id == "" { return body }` **不是**"替换 base"的实现，它是 `ResolveCustomAgentPrompts` 的"显式正文 vs 模板 id"来源解析器。其 `base` 也只装"角色 + 领域工作流"。
- **叠加会把重复 owner 固化进 prompt**：领域 base 与 preset 可能就同一件事各说一遍（如"关注报告期口径"），叠加会把两份都留着，违反"一条事实只有一个 owner"（ADR-0003 原则）。
- **在效规范已定"替换"**：`prompt-composition`（2026-09-18 并入）原文"选定智能体后 SHALL **替换**为该 AgentPreset 的正文"。本决策与之一致，不构成规范冲突。

**⚠ 明确接受的代价**：选定预设后，**知识库领域方法不参与组装** —— "选了财务专家 + 绑定人事库"时人事领域方法消失。系统记日志但不阻断。这是替换语义的既定代价，**不是缺陷**（后人不应把它当 bug 去修）。

**数据来源**：`knowledge_base` 新增 `domain` 列（默认 `general`），作为"知识库 → 领域 base"的唯一事实源；理由：KB→领域是需要被管理页展示与编辑的一等关系，放配置映射会与 kb_id（UUID）耦合且不可查询。未绑定 KB 或领域未知 → 回退内置通用 base。

### D4：不提供"叠加/合并"开关

**候选**：(a) 只做替换；(b) 替换 + 显式"追加领域 base"开关；(c) 先 (a)，观察后定 (b)。

**选 (a)。** 理由：叠加方案的重复 owner 问题是（b）绕不开的固有代价；在没有实际"两者都要"的需求之前引入开关，属预判性扩展（违反项目「最小改动」）。若将来真出现"预设与领域都要保留"的场景，再引入显式开关并规定冲突时的裁决规则。

### D5：分三阶段落地，每阶段一道闸门

| 阶段 | 内容 | 闸门（必须绿才进下一阶段） |
|---|---|---|
| **P0 载体** | Python 常量 → YAML；加载器；**逐字搬运** | **最终 prompt 逐字不变**，由**新增的**本地 pin 闸门测试证明（预期值不得修改）；现有 prompt 层测试中依赖被删常量/远端的那几条本就该改，不在闸门范围 |
| **P1 归属** | 6 段拆分；检索阶梯搬到 `sources`；`base` 改**三选一（替换）**解析；`domain` 列 + 领域 base；verify 两段指引改条件渲染 | 契约测试绿 + 端到端 prompt 快照**重采**（人工确认） |
| **P2 内容对齐** | `sources` 补"证据足够即停止检索"；`runtime_contract` 补 §2 其余 4 条运行上下文与措辞对齐 WeKnora 原文；base 领域方法按 `data_analyst` 口径补全与措辞核对；`output` 补通用输出四条 | RAGAS eval 对比（此时结构已固定，质量变化可单独归因）**+ 三个原始症状指标**：`iteration limit` 触顶率、每请求 `retrieve_kb` 调用次数分布、`answer_len=0` 占比 |

⚠ **完成条件与数据·指令边界在 P1 就写入** `runtime_contract`（原措辞把它们列在 P2）——
P1 的契约测试要求遍历所有能力组合断言这两条都在，规则不在 P1 则该闸门无法通过。

**P0 的"逐字不变"闸门是硬要求**：它是唯一能证明"搬运无损"的手段。若 P0 与 P1 合并，就再也无法区分"文本变了"是搬运引入的还是结构改动引入的。

⚠ **该闸门测试目前不存在，必须先在 P0 建出来**：仓库内**没有**端到端 prompt 快照测试（唯一的候选 `tests/rag/test_prompt_layers.py:24-33` 断言的是 `messages[0].content == PromptManager().get_system_prompt()`，而 `get_system_prompt()` 是 **Langfuse 远端 + 本地兜底**、远端取 latest 不固定版本、默认 `LANGFUSE_ENABLE=true`，见 Risks「远端/本地双事实源」）。它既不可重复，又会在 CI 中发起真实网络请求。因此 P0 的第一步是新增一个**显式 pin 到本地模板**的载荷断言测试并先记录基线，之后才谈"搬运无损"。详见 spec `<prompt-carrier>` 的「载体替换的逐字不变量」。

**成功度量（I-10，2026-09-21 评审后补）**：三个闸门都是**结构闸门**，必须再挂"症状指标"，否则无法回答"模型是否还在盲试 / 是否仍然给不出答案"。分三层：

| 层 | 指标 | 手段 |
|---|---|---|
| 自动（CI 可测） | 选中预设 + 绑库时最终 system 仍含检索阶梯要素；**任何"绑库 × 工具集"组合下完成条件与数据·指令边界都在** | task 2.16 的契约测试（①②③④） |
| 观测（复用现成日志，不新建监控） | `[agent] iteration limit` 触顶率 / 每请求 `retrieve_kb` 调用次数分布 / `answer_len=0` 占比 | P2 用**同一批 eval 请求**附带产出（task 3.6） |
| 人工 | "绑 KB + 选预设 → 答案仍带 `[n]`" | change `e2e-playwright-regression` 已固化的回归项 |

### D6：`skills` 段保留段位、不改承载通道，且不参与 system prompt 组装

现状**目录与正文分家，共三条通道**：

| 承载对象 | 通道 | 出处 |
|---|---|---|
| **目录**（有哪些 skill、各自何时用） | `delegate_task` 的工具描述（`"<名>: <描述>"` 列表，500 字符预算截断） | `registry.py:88-107`、`delegate_task.py:57-64` |
| **正文**（`context: inline`） | 渲染后写成 `[[skill-injection]]` 前缀的隐藏 user 消息，落 Redis+MySQL、**跨轮持久**，主 agent 自行执行 | `agent_service.py:977-998`、`:1096-1117` |
| **正文**（`context: fork`） | **不进主 agent 上下文**；作为子代理的 user message（`fork_body`），主 agent 只见 `delegate_task` 的返回文本 | `loader.py:142-146`、`executor.py:91+` |

当前技能 `financial-statement-analyzer` 为 `context: fork`，且 `agents/finance-expert.md` 已去掉 `skills:` 预绑定 —— `_inject_skill_message` 的两个调用点（`/xxx` 且 context=inline、预设首轮预加载）**当前均不触发**，该通道实际处于闲置状态。

**本期不改**：改动任一通道都会影响 skill 的跨轮持久语义与既有测试，且与本变更目标（段归属）无关。

段模型里 SHALL 保留 `skills` 段位，并标明**承载通道 = 工具描述（目录）+ 消息层（正文两分支）**；该段 SHALL NOT 参与 system prompt 的拼接。

⚠ **与 WeKnora 的差异（记为复查触发条件）**：WeKnora 用 `skills` 段输出 Level-1 目录（name + description + `skill://` 路径）由模型**按需** `read_file` 读全文（`prompts.go:211-230`），即渐进披露；本项目是"委派时整体交付正文"。**要往按需加载靠，前提是先有 `read_file` 类工具** —— 否则无目录可"按需读"。

### D7：归属表落点 —— 新建 `docs/agents/prompt-ownership.md`

**候选**：a) `docs/agents/rules.md` 加一节；b) 新建专项档；c) 并入 `docs/agents/glossary.md`。

**选 b。** 理由：

- 它是**需要长期维护的规则表**（会被代码评审与实现反复引用），与 `logging-rules.md` 同级同性质 —— 后者正是"格式唯一归属"的专项档，是现成的先例。
- `rules.md` 已承载"异常处理 / 响应包装 / 日志约定 / 排查规范 / 代码注释标准"，再塞一段归属规则会让它继续膨胀（该文件已接近需要拆分）。
- `glossary.md` 是**术语**表，归属规则不是术语，放进去会让它变成杂物档。

按项目规约：新建归属文档必须同步登记进 `CLAUDE.md` 的「文档组织」表，否则视为未完成。

### D8：态 A（未绑定 KB）取"结构不变"，不取"逐字不变"

**冲突**：`runtime_contract`（含完成条件）与 `output` 段是**无条件注入**，因此"未绑定知识库"会话（态 A）的 system 段内容必然变化；而**在效主规格** `docs/openspec/specs/prompt-composition/spec.md` 的 Requirement「默认行为逐字不变（端到端快照）」要求"未选定智能体时，组装结果 SHALL 与重构前的 system prompt **端到端逐字一致**（含引用指令、委派引导、日期三段的顺序与内容）"。

⚠ **该 requirement 现在就生效于 `prompt-composition`，不在未来的"库边界"change 里** —— 本 delta 必须显式 `MODIFIED` 它。把冲突推给下游会得到"归档后主规格自相矛盾"的结果（两条要求互斥：一条要逐字一致，一条要无条件新增段）。

**候选**：

| 方案 | 做法 | 代价 |
|---|---|---|
| **甲** | 本 delta 显式 MODIFY 该 requirement 为"**结构不变**"：态 A 仍是两条 system 消息、角色序列不变，内容是新的 | 原 requirement 的措辞要改写 |
| 乙 | 不做 MODIFY，把基线管理推给下游 change | 归档后主规格自相矛盾（两条要求互斥），且下游被迫承担本变更的后果 |
| 丙 | 把 `runtime_contract` / `output` 也做成"仅绑定时注入" | **削掉两段的核心价值** —— 完成条件与数据·指令边界跟"是否绑库"无关，纯对话同样需要 |

**选甲。** 理由：原 requirement 的意图是"别把纯对话搞坏"，而不是"文本一个字节都不许动"。"结构不变"（两条 system 消息、角色序列不变）已经守住那个意图；丙会让"模型 5 轮全在调工具、`answer_len=0`"这类失败形态在纯对话下依然无解（缺的正是完成条件）。

**落地**：本 delta 增加一条 `MODIFIED Requirements: 默认行为逐字不变（端到端快照）`，改写为"**结构不变**"——态 A 仍产出两条 system 消息、角色序列与追加顺序不变，内容随段模型变化；并加一条断言防止"顺手把 `KB_UNBOUND_SYSTEM_PROMPT` 吃掉"。原 requirement 的「逐字一致」措辞由本变更取代，不再由下游 change 承担。

### D9：`sources` 段按"逐条规则条件渲染"，判据为「工具已注册 AND 适用域成立」

**候选**：整段开关（工具集变化 → 整段换一份文本）/ 逐条条件渲染（每条规则挂"依赖哪个工具"的标注，缺工具则该条不输出）。

**选逐条。** 理由：本项目的检索阶梯是**一条规则一个动作**（换词再检 / 联网 / 如实说明）。整段开关会导致"只因缺 `search_web`，就把'换词再检'和'如实说明'一起删掉" —— 而这两条与联网无关。WeKnora 的 `sources` 也是逐工具条件追加（`grounding_prompt.go:31-63`，`slices.Contains(names, tools.ToolWebSearch)` 等）。代价是每条规则要带工具依赖声明 —— 那正是归属表本来就要登记的东西。

**⚠ 判据必须是复合的**：只看"工具是否注册"不够。`retrieve_kb` 在**所有**会话都注册（`src/agents/tools/rag_tools.py:238`），因此"实质性提问先检索再作答""换一种问法重新检索"会在**未绑定知识库**的会话里出现，与同轮注入的 `KB_UNBOUND_SYSTEM_PROMPT`（`src/config/prompts.py:68-71`）直接矛盾。故判据为 **工具已注册 AND 适用域成立**；带适用域的规则见 spec 的映射表（检索阶梯与联网系列 = 已绑定知识库）。

### D10：领域粒度取"一库一域"

**候选**：一库一域（混装列为复查触发条件）/ 库内多域（`domain` 变数组或多对多）/ 取消 KB→域绑定（领域只由用户选定的预设决定）。

**选一库一域。** 理由：第三个候选会让"选定知识库 → 自动加载该领域 prompt"这个目标形态彻底落空（那正是本架构的目的）；第二个候选是为尚未出现的需求付数据模型复杂度，且**库内多域时"该加载哪个领域 base"本身没有唯一答案** —— 那是产品问题，不是建模问题。实测 `b9e74e82` 已混装东软+腾讯两种年报，但两者同属"财务"领域，不构成反例；"财务+人事同库"的出现是复查触发条件（见 Open Question 1）。

### D11：内容对齐以 WeKnora 原文为准，逐条对照落 `prompt-mapping.md`

**决策（2026-09-21）**：段骨架与规则条目**以 WeKnora 为主**，正文译成中文；本项目独有规则作为补充；**去重归并成一份自然正文**（不保留"补充块"边界）。可追溯性由 `prompt-mapping.md` 的逐条对照表承担（原先 → 更改后 → WeKnora 出处 → 处置）。

**候选**：甲 保留"本项目补充块"边界 / **乙 去重归并成一份正文** / 丙 只借骨架、正文全重写。

**选乙。** 理由：甲会让同一段里两种语气并存，且事实仍是两个 owner（违反归属原则）；丙弱化"以 WeKnora 为主"的既定要求。乙兼得甲的可见性（靠对照表）与丙的自然度。

**由此产生的三处语义反转（必须与 spec 同步）**：

| # | 项 | 原 | 改 | 依据 |
|---|---|---|---|---|
| 1 | 规则 9「不计算文档未直接给出的比率/汇总」 | 禁止计算 | **删除**该笼统禁令；允许对已提供材料推理/计算/汇总/翻译，只禁止虚构缺失的来源事实 | `system_prompt.yaml:23`（`default_kb`） |
| 2 | 「已返回的完整内容不必再读」 | 归 `sources` | 归 **`base`**；`sources` 只保留"证据足够即停止检索"（两件不同的事） | `agent_system_prompt.yaml:49` vs `grounding_prompt.go:78` |
| 3 | 完成条件措辞 | "完成即停调工具" | 原文口径：**"仅有进度更新不算完成任务"** | `prompts.go:519-520` |

**由此扩大的范围（原 change 未覆盖）**：

- `USER_PROMPT_TEMPLATE` 去策略化（其末尾出口句与 `sources` 段是同一事实的两个 owner，且把出口条件放在 user 侧比 system 侧更易被模型当作唯一依据。⚠ 原文档写的"出口条件永不触发"**已按 2026-09-21 评审降级**：`retrieve_kb` 空结果时返回 `""`（`src/agents/tools/rag_tools.py:230-233`），结构上可返空；"从不返空"是转述中的推断）
- 经典 RAG 路径与 agent 路径共用同一套段
- `KB_UNBOUND_SYSTEM_PROMPT` 里的联网句改条件渲染（原先漏在条件注入之外）
- 模板分类法：段模板 vs 独立任务模板
- 远端 3 个 prompt 出列（否则 P0 闸门靠 env 开关维持）
- `get_system_prompt()` 的追加职责收口（避免与新组装器双入口重复注入）

**明确不做**：`cli/compare_rewrite.py` 与 `eval_ragas_generate.py` 的重复 prompt、fork 段位重构、classifier 与 `ask_user` 的重复 owner 闭环、工具 docstring 文本改写、KB→domain 前端编辑口。

### D12：独立评审后的修正（2026-09-21）

一次独立架构评审（未参与撰写者）提出 5 条 Blocker 与 12 条 Important，经逐条复核后已落地。记录其中三个"决策级"结论：

**D12.1 模板加载失败语义（原 OQ-1，Blocking）** —— **选"启动期加载 + 校验，失败即启动失败"**。

- 校验项：`id` 唯一、`kind`/`section`/`domain` 合法、每个 `section` 至少一个模板、段模板正文非空、**system 段总字符数的"事故兜底"上限（默认 50000 字符）**。
- 失败处理：透传异常 + `logger.exception`（按 `docs/agents/rules.md` 的透传型），**不留请求期降级到内嵌正文的分支**。
- 理由：请求期降级与"唯一事实源"（D11/I-9）冲突 —— 降级目标只能是内嵌副本；且模板损坏会把故障放大到每个请求。
- 请求期唯一允许的省略：条件段为空不输出。

**D12.2 领域识别与写入口（原 OQ-2，Blocking）** —— 判据 = "是否存在对应的 `base` 模板"（模板 id 即领域名）；非法值在**写入前**拒绝，不是读取时静默回退；本期 SHALL 提供最小写入口（KB 创建/更新参数或管理脚本），否则领域 Scenario 无法端到端验收。

**D12.3 载体落点** —— `src/config/prompts/`（**不是**顶层 `config/prompts/`）：`Dockerfile:31-33` 只 `COPY src/ scripts/ deploy/`，compose 只挂 `src/ tests/ skills/ agents/`，顶层目录生产读不到。

**D12.4 ADR 层收口（I-11 + I-3）** —— 新增两条 ADR：

- **ADR-0009** 把 ADR-0003 里以**原地修订**记下的"base 三选一（替换）"正式化为独立决策 —— 恢复 `docs/adr/` 的"只追加"一致性（与 ADR-0008 先例一致），并在其中登记对 ADR-0003 该处三处记录的更正（`agent_prompts.go` 误引、`test_prompt_layers.py:59` 判断错误、"态 A 逐字不变"归属错误）。**ADR-0003 的正文与 `Status` 均不改动**，仅其决策 3 由 ADR-0009 取代。
- **ADR-0010** 记录远端 3 个 prompt 出列的决定、**接受的代价**（出列后线上 prompt 的回滚手段变成回滚镜像）与连锁范围。它与 ADR-0002 的关系是"同一方向内把单一事实源提前"，不是推翻。

**D12.5 分段长度日志并入既有事件（I-6 + Q8）** —— 不新增 `[Agent][Prompt] section=... bytes=...` 式的逐段日志行（WeKnora 做法），改为在既有 `Event.PROMPT_ASSEMBLED` 上增加容器值字段 `section_chars={"base":1234,"sources":890,...}`（紧凑 JSON 无空格，键序 = 组装顺序，空段不出现）。**单位取字符数**（`len(str)` 口径，与 `INLINE_PROMPT_MAX_CHARS` 一致），**不照搬** WeKnora 的 `bytes=`（那是 Go 的字符串语义）。理由：`docs/agents/logging-rules.md` 的级别语义只含 info / warning / error，无 debug 降级档；5 段 × 每请求的 info 行属噪声。这也是本项目相对 WeKnora 的**有意分歧**。

**D12.6 grill 第 1 轮的七项决定（2026-09-21）**：

| # | 决定 | 落点 |
|---|---|---|
| Q1 | 迁移范围 = **12 迁 / 7 留** —— `VERIFY_*`（4）与 `FORK_*`（3）**不模板化**，它们是行为键（查重 marker / 子代理执行契约） | spec `<prompt-carrier>`「迁移范围」、tasks 1.1 / 1.3、proposal Impact |
| Q2 | 段模板用 **`kind` / `section` / `domain` 字段**声明归属，`id` 仅作标识；**不从 `id` 字符串推断**段名或领域名 | spec `<prompt-carrier>`、tasks 1.11 |
| Q3 | **`general` 是保留值且必须有对应 YAML 模板** —— 通用 base 不得成为"唯一一个不在 YAML 的段正文" | spec `<prompt-composition>` 领域绑定、tasks 1.10 |
| Q4 | P0 闸门 = **golden 文本比较**（golden 在**迁移前**从常量路径生成、迁移后不得修改）。原措辞"pin 到本地模板"在时点上不可能（P0 前模板不存在） | spec `<prompt-carrier>`「载体替换的逐字不变量」 |
| Q5 | "完成"定义为 **证据足够 AND 已给出答案**；`sources` 相应写"不得以任务已完成为由跳过取证" —— 两条**互相约束**，不得只留一条 | spec `<prompt-composition>` 段归属 + 环境约束层 |
| Q6 | `base` 的"语义检索 vs 关键词检索""片段不全再补读"**判为 base 合法**（属"拿到材料后怎么读"）；判别线 = **是否涉及调用哪个工具 / 何时调用 / 失败后降级** | spec `<prompt-composition>` 段归属 |
| Q7 | `src/cli/compare_rewrite.py:27` 的 import 连带**纳入范围**（原先被误列为 Non-Goal） | proposal Non-Goals、tasks 1.12 |

其余修正（B1 在效主规格的 MODIFIED、B2 复合判据、B4 占位符与 `str.format`、B5 P0 闸门测试、I-1 测试面更正、I-2 引用校正、I-5 领域 base 与预设的定位、I-7 marker 不变量与联网询问短路、I-8 段数最小性、I-9 唯一事实源、I-10 症状指标）分散落在 spec / tasks / `prompt-mapping.md` 各处。

**D12.7 grill 第 2 轮的四项决定（2026-09-21）**：

| # | 决定 | 落点 |
|---|---|---|
| Q8 | 段体积用**字符数**（字段 `section_chars`，非字节 —— `len(str)` 口径，与 `INLINE_PROMPT_MAX_CHARS` 一致）。⚠ 阈值部分**已被 D12.8 修正**：原拟的 10000 硬上限是层级错配 | spec `<prompt-composition>`「分段字符数日志」、tasks 2.17 |
| Q9 | **显式记录与 Langfuse 官方建议的偏离**：采纳其"启动期预取"，**不采纳 fallback** —— 该建议的前提是远端源可能不可达，我们的源在镜像内，静默降级到会漂移的内嵌副本比启动失败更危险 | spec `<prompt-carrier>`「模板加载的失败语义」 |
| Q10 | `PromptManager` **收窄为加载入口的门面，或直接退役**；SHALL NOT 保留"两条路径并存" | spec `<prompt-carrier>`「读取路径唯一化」、tasks 2.22 |
| Q11 | **条件判据留代码、不由 YAML 声明** —— 模板只管正文（"能改文案"），挂载点由代码决定（"不能改挂载"）；理由与"`VERIFY_*`/`FORK_*` 不模板化"同源 | spec `<prompt-composition>`「判据的位置」、tasks 2.1 / 2.12 |

**外部经验调研的结论状态（2026-09-21）**：子代理第一次因 429 失败，重试后四个缺口均有结论 ——

- **占比建议：未找到**。厂商只有定性主张（Anthropic 官方博文 *Effective context engineering for AI agents* 的 "find the smallest possible set of high-signal tokens"，无百分比）；Gemini 官方只给英文换算 "1 token ≈ 4 characters"。**没有任何厂商/cookbook 给出 "system : tools : history" 占比**。
- **同类项目实测体积（社区源码，字符数）**：codex 6,621–24,026（`codex-rs/core/gpt_5_codex_prompt.md` 等，且用 `include_str!` **编进二进制**）；claude-code **无 system prompt 上限**（相关的只有记忆 `MAX_MEMORY_CHARACTER_COUNT=40000`、工具结果 `DEFAULT_MAX_RESULT_SIZE_CHARS=50000`、autocompact 预留 `AUTOCOMPACT_BUFFER_TOKENS=13000`）；WeKnora 单模板 **18,120 字符**、无上限（另有 `contextSafetyTokens=4096`）；dify / ragflow 均无上限。
- **中文换算（一手）**：DeepSeek 官方文档给 **1 个中文字符 ≈ 0.6 token**（英文 ≈ 0.3）。⚠ 这是 **DeepSeek 分词器口径**，本项目跑 DashScope/Qwen，属**跨模型借用**，只能作量级估算。
- **镜像内模板 fail-fast vs 内嵌 fallback：未找到任何一手讨论**。唯一相关一手是 Langfuse（前提是远端源不可达，不适用）。→ 本项目的 fail-fast 是**自定取舍，无一手背书也无反例**；已按 Q9 把它显式记录。

**D12.8 grill 第 3 轮的两项决定（2026-09-21，由上述调研触发）**：

| # | 决定 | 落点 |
|---|---|---|
| Q12 | **修正 Q8 的阈值**：硬上限由 10000 抬为 **50000 字符的"事故兜底"**（只拦"整篇文档被误粘贴进模板"这类明显损坏，**不作为预算闸门**）；新增「system 段占比观测」软告警（超阈只 warning 不阻断） | spec `<prompt-carrier>` 失败语义、spec `<prompt-composition>`「system 段占比观测」、tasks 1.10 / 1.10b / 3.7 |
| Q13 | token 粗估**不进日志字段**（避免跨模型系数被当成精确值），只在 P2 复核时算一次并**注明口径来源与局限** | spec `<prompt-composition>`「system 段占比观测」、tasks 1.10b / 3.7 |

⚠ **对 Q8 的自我更正（记账）**：原引 `claude-code/src/components/agents/validateAgent.ts:98-100` 的 `length > 10000` 作为 system 段上限依据是**层级错配** —— 那是 Claude Code 校验**用户自定义 agent 表单输入**的上限，不是它自身 system prompt 的体积（它自身是"上万字符且无上限"）。单位（字符数）与机制（启动期校验、fail-fast）保留，只有阈值被修正。

**另记一条"记账但不采纳"的参考做法**：codex 用 `include_str!` 把 prompt **编译进二进制**，从根本上不存在"镜像里漏 COPY 模板文件"的问题（即 B3 那类）。但它需要构建/代码生成步骤，且改文案仍要重建镜像，与本变更"文案与代码解耦、改数据文件即生效"的目标相悖。



**仍待定（不阻塞 P0）**：OQ-3「默认检索方法（`base`）vs 来源选择（`sources`）」的判别例 → 记入 `prompt-ownership.md`（task 2.1）；OQ-4 领域回退/冲突的日志事件与字段 → task 2.25；OQ-5 与 `retrieval-fetch-and-dedup` 的文档竞争边界 → 约定本变更先落；OQ-6 system 段体积阈值 → 已列为启动校验项（task 1.10），具体阈值在 P2 复核后定。

## Risks / Trade-offs

- **[测试面：原先的清单既多又少，且一处判断被撤回]** → 真正会被打断的是：`tests/rag/test_prompt_layers.py`（`:7-11` 模块级 import 被删常量 → **整文件**失败；`:24-33`/`:100-107` 构造真实 `PromptManager()` → 网络）、`tests/config/test_prompt_web_search.py`（9 个短语断言在常量上）、`tests/config/test_prompt_delegate.py:15`、`tests/infra/llm/test_prompt_manager_fallback.py:16`、`tests/infra/test_prompt_manager.py`（受 task 2.22 影响）、5 个 `PromptManager` 桩测试（签名变化）。**不需要改**：`test_fork_sub_agent_contract.py`、`test_first_batch_skills.py`。原文档称 `:59` "必然失败"**是错的**（D3 保留替换语义，该断言继续成立）。**改测试不是"为了让测试过"，是契约本身变了** —— 必须在提交信息里写清，不可静默改断言。
- **[`pm.get_system_prompt()` 非确定性]** → 该方法是 Langfuse 远端 + 本地兜底，远端侧取 latest。任何以它为准的闸门测试都必须显式走本地兜底，否则不可重复（见 D5 的 pin 说明）。
- **[`prompt.py:54-57` 的注释理由已证伪]** → 该注释的前提（"persona 非空时由预设自己承载等价指引"）与 `trace_c54ce259` 的实测相反。P1 时同步重写注释（按项目规约：注释写当前机制，不写变更历史）。
- **[端到端快照重采是人工步骤]** → P1 完成后必须人工确认基线；这是本变更唯一无法自动化的验收点。
- **[`domain` 列迁移]** → 新增列默认 `general`，存量 KB 全部落入"通用领域"，不破坏现状；回滚即忽略该列。
- **[条件注入需要工具集传到 verify 注入点]** → `VERIFY_GUIDANCE_PROMPT` / `VERIFY_HINT_PROMPT` 由 `regen_decision.py` 在运行期注入，是 `build_system_prompt` 之外的路径。P1 必须把启用工具集合传到这里，否则"下掉 `search_web` 但指引仍在"的问题只修了一半。
- **[完成条件与 verify regen 可能互相震荡]** → 完成条件让模型更早停止调用工具；而 verify 在判定"缺失年份"时会 regen，并**复位** `_agent_iterations` 与 `ctx.web_count`（`src/agents/graph/verify/regen_decision.py:135-166`）。存在"早停 → 判定缺失 → regen → 再早停"的循环可能。本轮**不新增机制**（YAGNI），采取两条低成本措施：① spec 已把"完成"定义为"证据足够且已给出答案"，并要求 `sources` 写"不得以任务已完成为由跳过取证"——两条互相约束；② P2 的观测指标含 `iteration limit` 触顶率与 regen 次数，若震荡属实会在指标上显现。若显现，再评估"regen 期间禁止提前收工"的指令或校验轮次上限。
- **[远端/本地双事实源 —— 已实测确认，直接决定 P0 闸门的覆盖面]** 事实：走远端的是**恰好 3 个** prompt（`financial-system-prompt` / `user-prompt-template` / `classifier-prompt`，`src/infra/llm/prompt_manager.py:69-73`）；**无版本固定**，URL 只拼 name 取 latest（`:117-123`），返回的 version 只进日志不参与请求（`:126`）；按 name 缓存 **60s**（`:77,:103`）；且 **`LANGFUSE_ENABLE=true` 且远端有同名文本时远端恒优先于本地兜底**（`:156-160`）—— 即"改了本地 YAML 不生效，日志也不会告诉你 YAML 没被用"。

  **当前实际状态**：`src/config/settings.py:276` 默认 `"true"`，但 `.env:63` 设为 `false`；运行时实测 `LANGFUSE_ENABLE=False`、日志出现 `[llm] prompt fallback name=financial-system-prompt` → **这 3 个 prompt 实际走本地兜底，本地是当前的事实源**。

  **结论**：P0 的"逐字不变"闸门在本部署下成立，**但它是靠一个 env 开关维持的，不是靠机制**。这使"3 个远端 prompt 的归属"从"独立问题"变成**决定 P0 覆盖面的问题** —— 见 Open Question 2。
- **[token 与段长度]** → 5 个 system 段无条件注入会净增 system 段体积（`_truncate_history` 的预算只作用于历史，不含 system）。缓解：① `section_chars` 字段（task 2.17）记各段字符数；② **软告警**：估算 system 段占 context window 的比例，超阈值记 warning（task 1.10b，阈值是推断值、不写进契约）；③ 启动期只有 **50000 字符的"事故兜底"**，不是预算闸门（D12.8 —— 同类项目实测 6K–24K 字符且普遍无硬上限）。判断"净增是否可接受"放到 P2（task 3.7）。
- **[`docs/agents/requirements_pool.md:143-146` 可能被读成被推翻]** → ADR-0002 说明"分两阶段实现同一方向"，ADR-0010 说明"出列只是把单一事实源提前、终态不变"，并在该文档对应条目加一句阶段说明，避免后人误判路线反复。⚠ 另需记明 ADR-0010 的**代价**：出列后线上 prompt 的回滚手段是回滚镜像，不再是"在 Langfuse 改回旧版本"。

## Migration Plan

**与 change `retrieval-fetch-and-dedup` 的顺序**：**对端先落地**；本变更的 **P0（YAML 载体搬运）可并行开工**（零行为差异、零文件交集）。**P1/P2 必须排在对端之后** —— P1 与 P2 各要重采一次基线（prompt 快照 + RAGAS eval），而基线所测的 context 内容正是对端在改的东西，先采会直接作废。

按 D5 的三阶段推进，每阶段独立可回滚：

1. **P0**：新建 `src/config/prompts/*.yaml`，把 19 个常量逐字搬入并分好段名；加载器读 YAML 产出与原常量**逐字节相同**的字符串；跑端到端快照，预期零 diff。
2. **P1**：拆分 base；搬检索阶梯到 `sources`；`base` 改三选一（替换）解析；`knowledge_base.domain` 迁移 + 解析层；verify 注入点接工具集；改写两条契约测试；重采快照。
3. **P2**：base 瘦身 + 补三条规则；跑 RAGAS eval 对比 P1 后的基线。

**回滚**：P0 回滚 = `git revert` 该次提交（常量已删除，不存在"加载器切回常量"的第二事实源）；P1 回滚 = 恢复 `if persona: base = persona` 与旧段布置（快照基线需一并回退）；P2 回滚 = 恢复 base 文本。`domain` 列可保留不用。

## Open Questions

1. **检索阶梯的版本**：完整保留规则 4–8（含"第二次检索显式传 top_k=10"这类具体动作），还是精简为 3 条（何时重试 / 何时联网 / 何时如实说明）？⚠ **依赖 change `retrieval-fetch-and-dedup` 的第 5 节产出**（它给出"库内有无内容"的双形态判据与分数区间）—— 该结论未出之前不应定稿 `sources` 段的措辞。`prompt-mapping.md` 第 3 节已按"完整保留"给出初稿，待该结论后再定稿。

   > **复核记录（2026-09-22，T5b）**：因 `retrieval-fetch-and-dedup` **优先级调整**，本项已**提前定稿** —— 采用"完整保留"形态（即上文的初稿方向），未等该对端第 5 节产出。定稿与差异登记见 `prompt-mapping.md` §3 的勘误 4/5 与"检索阶梯定稿记录"；接受的代价是"库空 vs 未命中"仍为粗粒度。该对端结论落地后**回头复核**本项，必要时补入"库内有无内容"的双形态判据。⚠ 本行只**追加**，不改上文原措辞。
2. ~~Langfuse 侧 3 个 prompt 的归属~~ **已决定（2026-09-21）：出列**，本地模板为唯一事实源，落 task 2.23。
