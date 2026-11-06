## MODIFIED Requirements

### Requirement: trace 根落在后台生成任务内

trace 根 SHALL 在承载该轮的 asyncio task 内部开启与结束。

站点与企微轮次：根 SHALL 覆盖「准备就绪后的整轮生成」（图事件循环的完整生命周期），开在生成任务内。

告警轮次：根 SHALL 开在生成任务内（即生成开始）；采集发生在根之前，采集 span 可早于根，属已知现象（判据见 `docs/agents/troubleshooting.md` §4），不视为违反本要求。

SSE 订阅与推送侧 SHALL NOT 单独产生 trace。

#### Scenario: 根的生命周期覆盖整轮生成

- **WHEN** 一轮生成包含多轮 agent 迭代与工具调用
- **THEN** 这些迭代与工具调用产出的 observation 均隶属于同一条 trace 之下

#### Scenario: 生成被取消时 trace 不被丢失

- **WHEN** 用户在生成过程中触发取消
- **THEN** 已产生的 observation 正常上报，该 trace 仍可在 Langfuse 中查到

### Requirement: trace 内容记录范围

trace 的 generation SHALL 记录该次 LLM 调用的输入与输出原文（含 system prompt、检索上下文与回答），使链路可完整回放。工具 span SHALL 记录该次工具调用的入参与返回值原文。记录输入 SHALL 采用**显式写入**方式；被装饰函数的**内部运行时对象 SHALL NOT 进入 trace**（如请求上下文对象、流式管理器、编译后的图、事件与队列等）。

trace 根的标签 SHALL 只包含低基数、稳定、可枚举的三个维度：知识库绑定（`kb` / `no_kb`）、渠道（`web` / `wecom` / `alert`，未知或未设置的渠道归一为 `none`）、profile（`pf:<preset 短名>`，如 `pf:chat` / `pf:alert` / `pf:ops`）。SHALL NOT 把工具名、Agent 显示名、知识库 ID 等高基数值写入标签（profile 维度只写预设短名）。

trace 根的 metadata SHALL 包含：`profile`、`agent`、`agent_display_name`、`channel`、`kb_id`、`kb_domain`、`tools_used`（本轮实际调用的工具名，未调用时为空列表）、`loaded_skills`、`skill_action`。

该行为 SHALL 在文档中作为已知事实登记，并说明 trace 的保留期可能与对话记录的保留期不一致。

#### Scenario: 原文可回放

- **WHEN** 在 Langfuse 中打开某条 trace 的主 agent generation
- **THEN** 可看到该次调用的完整输入消息与输出文本

#### Scenario: 工具入参与返回可回放

- **WHEN** 在 Langfuse 中打开某条工具 span
- **THEN** 可看到该次调用的入参与该工具的返回值原文

#### Scenario: 标签只用低基数维度

- **WHEN** 检查任一 trace 的标签
- **THEN** 标签只取自 kb/no_kb、web/wecom/alert/none、pf:* 三类的并集，不含工具名或 Agent 显示名

#### Scenario: 工具使用可查询

- **WHEN** 某一轮调用了日志工具
- **THEN** metadata 的 `tools_used` 含该工具名，且对应的工具 span 可按名称检索

#### Scenario: 内部运行时对象不进 trace

- **WHEN** 检查某条 trace 的根 observation、generation 或工具 span 的输入字段
- **THEN** 其中只包含该记的业务输入（查询文本 / 消息列表 / 工具实参等）
- **AND** 不出现请求上下文对象、流式管理器、编译后的图、事件或队列等内部对象的序列化结果

#### Scenario: 记录范围有明确文档说明

- **WHEN** 查阅 trace 相关文档
- **THEN** 可读到「trace 记录 prompt、回答与工具调用原文」及其保留期、标签值域与 metadata 字段，无需从代码反推
