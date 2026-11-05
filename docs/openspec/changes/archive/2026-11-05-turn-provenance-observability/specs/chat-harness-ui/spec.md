# chat-harness-ui Specification (Delta)

## ADDED Requirements

### Requirement: 消息流展示本轮来源声明

消息流 SHALL 在每轮回答的过程区**最前**展示本轮来源声明（沿用既有状态行样式）：一条声明生效智能体（展示名），一条声明本轮技能动作。声明缺失时 SHALL NOT 渲染占位或空行。两条声明 SHALL 与实时流式渲染、历史回放两条路径表现一致。

#### Scenario: 声明出现在过程区最前
- **WHEN** 一轮生成开始并声明了智能体与技能
- **THEN** 过程区最前出现两条状态行：智能体声明在前、技能声明在后，且先于该轮其余状态行

#### Scenario: 智能体用展示名
- **WHEN** 会话绑定的预设带展示名（如「财务专家」）
- **THEN** 智能体声明行显示展示名，而非其 slug

#### Scenario: 多技能一条列全
- **WHEN** 一轮加载了多个技能
- **THEN** 技能声明以单行顿号列表呈现，不逐条渲染多行

#### Scenario: fork 轮措辞区分
- **WHEN** 本轮由子代理执行某个 fork 技能
- **THEN** 技能声明行使用"使用技能：/{name}（子代理执行）"措辞，与"成功加载 skills"区分

#### Scenario: 非本轮动作不展示
- **WHEN** 某技能仅在更早轮次加载、本轮未产生技能动作
- **THEN** 本轮过程区不出现技能声明行

#### Scenario: 历史回放一致
- **WHEN** 刷新页面或切换到已完成该轮的会话
- **THEN** 该轮过程区重建出与实时一致的来源声明

#### Scenario: 老数据兼容
- **WHEN** 回放不含来源声明的存量消息（无过程事件）
- **THEN** 该轮过程区正常渲染其余内容，不报错、不补占位行

## MODIFIED Requirements

### Requirement: 消息流样式

消息流 SHALL 参照现有 chat.html：用户消息为**浅灰气泡**（`#F1F5F9`，深字 `#1E293B`）；AI 回复为 markdown（浅灰底 `#F8FAFC` + 描边 `#E2E8F0`），末尾标注模型名（「由 DeepSeek-V4-Flash 回答」）。该标注 SHALL 在**实时与历史回放两条路径**均重建：实时取本轮 `model_info` 帧的模型名，历史回放取 assistant 消息的 `model_name` 列（`api/sessions.py` 已返回），SHALL NOT 依赖过程事件；缺失或为空（存量消息）时 SHALL NOT 渲染标注或占位，其余内容正常渲染。`is_fallback` 未持久化 → 回放标注 SHALL NOT 含 `fallback` 标记（已知限制）。**不含时间戳**；保留引用卡 / 工具调用状态 / 反馈按钮。

#### Scenario: 用户气泡与 AI 回复渲染
- **WHEN** 渲染一段对话
- **THEN** 用户消息为浅灰气泡（无时间戳），AI 回复以 markdown 排版并附模型标注（无时间戳），引用与工具状态保留

#### Scenario: 历史回放重建模型标注
- **WHEN** 加载或刷新含 `model_name` 的历史 assistant 消息
- **THEN** 该气泡行之后重建出「由 {model_name} 回答」，位置与实时路径一致（气泡行之后、与引用栏同级）

#### Scenario: 模型名缺失不渲染占位
- **WHEN** 回放的 assistant 消息 `model_name` 为空或缺失
- **THEN** 不渲染模型标注、不补占位行，该轮其余内容正常渲染

#### Scenario: 回放标注不含 fallback
- **WHEN** 回放一条当时发生过模型回退（fallback）的消息
- **THEN** 标注仍只显示模型名、不含 `fallback` 标记（该标记未持久化）
