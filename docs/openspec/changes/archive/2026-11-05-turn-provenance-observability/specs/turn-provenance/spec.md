# turn-provenance Specification (Delta)

## ADDED Requirements

### Requirement: 每轮声明生效智能体

系统 SHALL 在每轮生成开始时，通过 SSE `status` 事件声明本轮生效的智能体：`stage` 取专用值 `turn_agent`，`message` 使用用户可见文案模板（`当前使用了 {agent}`），其中 `{agent}` SHALL 为预设的展示名（`display_name`；缺失时回落 `name`）。声明 SHALL 早于图事件循环产出的任何状态行。

#### Scenario: 已绑定智能体时声明
- **WHEN** 会话已绑定智能体「财务专家」并开始一轮生成
- **THEN** 该轮 status 流中出现一条 `stage=turn_agent`、`message="当前使用了 财务专家"` 的事件

#### Scenario: 未绑定智能体时不声明
- **WHEN** 会话未绑定任何智能体并开始一轮生成
- **THEN** 该轮不产出 `turn_agent` 的 status 事件（默认人设不声明）

#### Scenario: 展示名缺失回落
- **WHEN** 预设未提供 `display_name`
- **THEN** `message` 使用预设 `name`（slug）作为兜底，不报错

### Requirement: 每轮声明本轮技能动作

系统 SHALL 在每轮生成开始时，通过 SSE `status` 事件声明本轮的技能动作：`stage` 取专用值 `turn_skill`。若无本轮技能动作（无 `/xxx`、非首轮、或命令失败），系统 SHALL NOT 产出 `turn_skill` 事件。声明表达**本轮触发**的技能动作（意图）；其执行结果（成功 / 中断 / 降级）SHALL NOT 由声明承载——fork 结果由委派区呈现，失败原因由既有兜底文案呈现，已发出的声明不撤回、不改写。

#### Scenario: inline 技能加载成功
- **WHEN** 用户以 `/<inline-skill-name> 任务` 触发命中 inline 技能并成功注入
- **THEN** 该轮产出 `stage=turn_skill`、`message="成功加载 skills：<inline-skill-name>"` 的事件

#### Scenario: 首轮多个技能一条列全
- **WHEN** 预设声明 `skills: [a, b, c]` 且三者在首轮均解析成功
- **THEN** 该轮只产出一条 `turn_skill` 事件，`message` 按声明顺序列出全部名称（顿号分隔），而非逐条产出

#### Scenario: fork 技能使用专门措辞
- **WHEN** 用户以 `/financial-statement-analyzer 任务` 触发命中 fork 技能并由子代理执行
- **THEN** 该轮产出 `stage=turn_skill`、`message="使用技能：/financial-statement-analyzer（子代理执行）"` 的事件（不称"加载"）

#### Scenario: 命令失败不声明
- **WHEN** 用户以未注册命令（如 `/ghost 任务`）开始一轮
- **THEN** 该轮不产出 `turn_skill` 事件（失败原因由既有兜底文案承载）

#### Scenario: 禁用技能不声明
- **WHEN** 用户以 `user-invocable:false` 的技能名开始一轮（该技能禁止用户调用，走"不存在"兜底）
- **THEN** 该轮不产出 `turn_skill` 事件

#### Scenario: 声明表意图，结果另述
- **WHEN** 命中 fork 技能并已产出 `turn_skill` 声明后，子代理超时 / 中断（或 executor 未装配而降级为不可直接执行）
- **THEN** 该轮仍保留已产出的 `turn_skill` 声明（表达"本轮触发了该技能"），其结果由委派区或既有兜底文案呈现；声明不撤回、不改写

#### Scenario: 跨轮不重复声明
- **WHEN** 某技能在上一轮已注入、本轮仍在上下文中生效
- **THEN** 本轮不因该技能产出 `turn_skill` 事件（只声明"本轮动作"）

### Requirement: 来源声明随历史回放保留

两条来源声明 SHALL 随本轮过程事件持久化，并在历史回放时重建：刷新页面或切换会话后，历史消息的过程区 SHALL 再次出现该轮的智能体与技能声明，且顺序与实时一致。

#### Scenario: 刷新后仍可见
- **WHEN** 一轮生成完成（声明了智能体与技能）后刷新页面或切换到该会话
- **THEN** 该轮过程区重建出同样的两条来源声明

#### Scenario: 无技能轮不产生空行
- **WHEN** 回放一轮未产生技能声明的历史消息
- **THEN** 该轮过程区不出现空的或占位的技能声明行

### Requirement: 声明一次、顺序固定、stage 专用

来源声明 SHALL 每轮各产出至多一次（不随 agent 多轮迭代或 verify 重生成重复产出）；产出顺序 SHALL 为：生效智能体声明 → 技能动作声明 → 既有节点状态行。两条声明 SHALL 使用专用于来源语义的 `stage` 取值，SHALL NOT 复用检索/联网等会改变正文与旁白判定口径的 `stage`。

#### Scenario: 多轮迭代不重复
- **WHEN** 一轮生成内 agent 循环迭代 3 次并触发 2 次工具调用
- **THEN** 来源声明仍各只出现一次

#### Scenario: 顺序稳定
- **WHEN** 一轮既声明智能体又声明技能
- **THEN** 智能体声明先于技能声明，二者均先于首个节点状态行

#### Scenario: 早于澄清与委派事件
- **WHEN** 一轮生成随后触发 ask_user 或 fork 委派
- **THEN** 两条来源声明 SHALL 先于这些事件进入缓冲（不得被并发推送的澄清/委派事件插到前面）

#### Scenario: stage 不复用检索态
- **WHEN** 检查来源声明的 `stage` 取值
- **THEN** 其取值不等于检索/联网等工具态取值，避免触发正文/旁白判定变化
