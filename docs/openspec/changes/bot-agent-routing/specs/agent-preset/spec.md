## MODIFIED Requirements

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
