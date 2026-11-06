# agent-preset Specification

## Purpose
TBD - created by syncing change session-agent-and-skill-invocation. Update Purpose after archive.
## Requirements
### Requirement: 智能体预设文件结构

系统 SHALL 从项目根 `agents/<name>.md` 加载智能体预设。文件 SHALL 为 Markdown：YAML frontmatter 声明元数据，正文为智能体的 system prompt（人设）。

frontmatter 字段：`name`（缺省用文件名；**仅允许 ASCII slug** `^[A-Za-z0-9][A-Za-z0-9_-]*$`）、`display_name`（可选，选择器展示名；缺省 = `name`）、`description`（何时使用；缺省用正文首段）、`tools`（可选，该预设**作为 fork 执行者**时允许的工具名列表；缺省不收窄）、`skills`（可选，预加载的 skill 名列表）、`maxTurns`（可选，作为 fork 执行者时的最大执行轮次）、`hidden`（可选，布尔，默认 false；为 true 时不出现在 `/api/agents`）、`channel`（可选，`web` / `wecom`；缺省表示不限渠道）。**v1 不含 `model`**（本项目模型不可选，多模型留待单独设计）。命名风格用**驼峰**（`maxTurns`）。

名称以 `bot-` 开头的预设 SHALL 强制要求 `hidden: true` 且 `channel: wecom`；缺失或取值不符时加载期 SHALL 失败（不注册该预设）。

**名称约束的理由**：`name` 同时是传输值（`agent` 请求字段）、会话存储值（`sessions.agent`）与注册表 key；中文名会让 `/api/agents` 选项点了查不到，也会让下层的 `agent` 校验出现多套字符集规则。中文展示走 `display_name`。

#### Scenario: 名称非法

- **WHEN** 预设文件的 frontmatter `name` 或文件名含非 ASCII slug 字符（如中文、空格、`.`）
- **THEN** 加载期记 warning 并跳过该预设（不注册、不进入清单）

#### Scenario: 展示名缺省

- **WHEN** 预设未声明 `display_name`
- **THEN** 清单与选择器使用 `name` 作为展示名

#### Scenario: 文件识别与解析

- **WHEN** 扫描 `agents/` 目录
- **THEN** 每个 `.md` 文件识别为一个智能体预设
- **AND** 正文作为 system prompt，frontmatter 解析出 name/description/tools/skills/maxTurns/hidden/channel

#### Scenario: tools 缺省不收窄（仅 fork 路径）

- **WHEN** 预设未声明 `tools` 且作为 fork 执行者
- **THEN** 工具集以 skill 的 `allowed-tools` 为准，不因预设而额外收窄
- **AND** 该预设作为主 agent 人设时，主 agent 工具集不受影响

#### Scenario: bot- 前缀缺少必要声明

- **WHEN** 文件名为 `bot-xxx.md`，但 frontmatter 未声明 `hidden: true` 或 `channel: wecom`
- **THEN** 加载期失败并指出该文件，不注册该预设，不影响其他预设

#### Scenario: 目录缺失

- **WHEN** `agents/` 目录不存在或为空
- **THEN** 智能体选择器仅提供默认智能体（通用助手），不报错

### Requirement: AgentPreset 运行时对象

系统 SHALL 为每个加载的智能体预设生成 AgentPreset 对象，含 {name, display_name, description, system_prompt, tools, skills, max_turns, source_path, hidden, channel}（v1 不含 model）。

#### Scenario: 注册表按名索引

- **WHEN** 加载完成后按名查询
- **THEN** 返回对应 AgentPreset；不存在返回 None（调用方降级到默认智能体）

#### Scenario: 同名冲突

- **WHEN** 两个预设声明同名
- **THEN** 加载期 fail-fast 报错（禁止静默覆盖）

#### Scenario: 可见性字段可读取

- **WHEN** 读取一个声明了 `hidden: true` 的预设
- **THEN** 运行时对象暴露 `hidden=true` 与对应 `channel`，供清单过滤与渠道校验使用

### Requirement: 智能体的会话级选择与首次绑定

新建对话页 SHALL 提供智能体选择器；选定的智能体 SHALL 仅作用于本次会话，**首次绑定即固化**（之后以绑定值为准，不因请求再次改变，见 `agent-service`「会话智能体的绑定与沿用」）。未选择时 SHALL 使用默认智能体（通用助手）。

#### Scenario: 会话内以绑定值为准

- **WHEN** 用户在已开始的会话中尝试更换智能体
- **THEN** 选择器不可用（提示需新建对话），且即使请求携带不同值也不生效（不阻断，记 warning）

#### Scenario: 未选择使用默认

- **WHEN** 用户未选择智能体直接提问
- **THEN** 系统使用默认智能体（通用助手），行为与既有一致

#### Scenario: 会话记录回显

- **WHEN** 打开一个选定了智能体的历史会话
- **THEN** 顶栏或会话信息中回显该智能体名（预设已被删除时降级显示原始名）

### Requirement: 智能体作为会话主 agent 人设

选定的智能体 SHALL 作为本会话主 agent 的身份：其正文注入主 agent 的 system prompt 人设层（组装规则见 `prompt-composition`）；`skills` 中列出的 skill SHALL 在该会话首轮生成前注入一次（隐藏消息，与 `/xxx` 同路径，不进 system prompt）。主 agent 的编排骨架（循环 / 工具 / verify / format）不变，**其工具集也不因预设声明 `tools` 而改变**（`tools` 只作用于该预设作为 fork 执行者时，见 `delegate-task`）。**v1 不含 `model`**，预设不能覆盖主模型。

#### Scenario: 人设注入

- **WHEN** 会话选定智能体"财务专家"
- **THEN** 主 agent 以该预设正文作为 system prompt 人设层回复，直至会话结束

#### Scenario: 预加载 skill

- **WHEN** 预设声明 `skills: [a]`（`a` 为 inline skill）且为该会话首轮
- **THEN** 该 skill 的 **inline 正文**在首轮生成前以隐藏消息注入会话上下文（不进 system prompt）
- **AND** 后续轮次持续可见且不重复注入

#### Scenario: fork skill 不被预加载

- **WHEN** 预设声明的 `skills` 列表中含 `context: fork` 的 skill
- **THEN** 记 warning 并跳过该项（**不注入其 `fork_body`**），其余项正常预加载
- **AND** 理由：`fork_body` 是写给子代理的任务/方法论 prompt，注入主 agent 会造成语义错配，且与 fork 的"正文不进主 agent 上下文"直接冲突
- **AND** 需要使用 fork skill 时由主 agent 通过委派机制触发，不靠预加载

#### Scenario: 预加载声明的 skill 不存在

- **WHEN** 预设声明了 `skills: [ghost-skill]` 但注册表中无该 skill
- **THEN** 记 warning 并跳过该项（不中断会话、不影响其余预加载项）
- **AND** 预设本身仍正常加载（`skills` 是内容引用，不是加载期硬依赖）

#### Scenario: 预设 tools 不改主 agent 工具集

- **WHEN** 会话选定预设并声明了 `tools: [retrieve_kb]`
- **THEN** 主 agent 可用工具集与未选预设时一致（`tools` 仅在 fork 子代理路径生效）

#### Scenario: 默认智能体

- **WHEN** 会话未选定任何智能体
- **THEN** 主 agent 使用系统默认 system prompt（既有行为不变）

### Requirement: 智能体预设作为 fork 执行者来源

`context: fork` 的 skill SHALL 默认由本会话选定的智能体预设执行；会话未选定智能体且 skill 未声明 `agent:` 时 SHALL 使用默认执行者（`general-purpose`）。

**执行者优先级**：skill 的 `agent:` 声明 > 本会话选定智能体 > `general-purpose` 默认。

**fork 的 prompt 结构** SHALL 为：system prompt = 执行者人设（agent preset 正文 / 默认执行者的系统默认 prompt），user message = skill 内容（任务/方法论），tools = 执行者 tools ∩ skill allowed-tools。**人设来自 agent，任务来自 skill，二者不混。**

**`general-purpose` 默认在本项目 SHALL 指系统默认 system prompt**（即未选智能体时主 agent 使用的那份，由 `build_system_prompt(persona=None, …)` 组装），不得新造 prompt。

#### Scenario: 会话智能体作为执行者

- **WHEN** 会话选定"财务专家"且用户 `/xxx` 调用一个 `context: fork` 的 skill
- **THEN** fork 子代理以"财务专家"人设（system prompt）执行该 skill
- **AND** skill 内容作为 user message 传入子代理

#### Scenario: 无会话智能体时用默认执行者

- **WHEN** 会话未选定智能体，用户 `/xxx` 调用一个 `context: fork` 且未声明 `agent:` 的 skill
- **THEN** fork 子代理以系统默认 system prompt 作为人设执行，skill 内容作为 user message

#### Scenario: skill 覆盖执行者

- **WHEN** skill 显式声明 `agent: <名>`
- **THEN** 该 skill 的 fork 使用指定预设执行，覆盖会话选定值
