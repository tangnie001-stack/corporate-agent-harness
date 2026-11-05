# streaming-run Specification (Delta)

## ADDED Requirements

### Requirement: 来源声明复用 status 事件的流契约

系统 SHALL 复用既有 `status` 事件承载每轮来源声明（生效智能体 / 本轮技能动作），并遵守以下流契约：

- **stage 取值专用**：来源声明 SHALL 使用专用于来源语义的 `stage` 取值（`turn_agent` / `turn_skill`），SHALL NOT 复用会改变正文/旁白判定的工具态取值（如检索、联网）。
- **逐轮各至多一次**：同一轮生成内，每类来源声明 SHALL 至多产出一条；不随 agent 多轮迭代或验证重生成重复。
- **顺序与时机**：产出顺序 SHALL 为「智能体声明 → 技能声明 → 既有节点状态行」，且两条声明 SHALL 先于同一轮的澄清（ask_user）与委派（delegate）事件进入缓冲。
- **条件不满足不产出**：未绑定智能体、本轮无技能动作、或命令失败时，对应声明 SHALL NOT 产出（不得输出空行或占位）。
- **结构化真源不变**：`agent_used` SHALL 继续作为生效智能体的结构化来源；消费端 SHALL NOT 从来源声明的文案文本反解结构化值。
- **向后兼容**：消费端 SHALL 容忍未知 `stage` 取值（忽略或以通用状态行渲染），不得因新增 stage 报错。

#### Scenario: 复用 status 承载来源声明
- **WHEN** 一轮生成声明了生效智能体与本轮技能
- **THEN** 该轮缓冲中出现两条 `status` 事件，`stage` 为来源专用取值，`message` 为用户可见文案

#### Scenario: 每轮至多一次
- **WHEN** 一轮生成内 agent 迭代多次并触发工具调用
- **THEN** 每类来源声明仍只出现一次

#### Scenario: 先于澄清与委派事件
- **WHEN** 同一轮随后产生 ask_user 或 delegate 事件
- **THEN** 两条来源声明在缓冲中的顺序早于这些事件

#### Scenario: 条件不满足不产出
- **WHEN** 本轮未绑定智能体且无技能动作
- **THEN** 缓冲中不出现来源声明事件，也不出现空文案的 `status`

#### Scenario: 消费端容忍未知 stage
- **WHEN** 消费端收到来源专用 stage 的状态事件
- **THEN** 它按通用状态行正常渲染，不报错；且不因该文案改变正文与旁白的判定口径

#### Scenario: 文案不作为结构化真源
- **WHEN** 消费端需要本轮的生效智能体值
- **THEN** 它从 `agent_used`（结构化）取值，而非解析来源声明的文本
