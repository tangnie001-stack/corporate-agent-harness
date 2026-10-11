## ADDED Requirements

### Requirement: 工具调用记为 span

图循环内每一次工具调用 SHALL 产出一条 span 类型的 observation，至少记录工具名、入参、返回值与起止时间；工具执行抛错时 SHALL 记录错误态与错误信息。同一轮（同一次 `tools` 节点执行）内的多次调用 SHALL 归入该轮的一条父 span 之下。

工具事实 SHALL 从图的事件流（`on_tool_start` / `on_tool_end` / `on_tool_error`）获取；观测代码 **SHALL NOT** 依据具体工具名分支处理，**SHALL NOT** 要求为每个工具单独埋点 —— 使任何经统一入口注册进图的新工具（含 MCP 适配工具）自动获得观测。

#### Scenario: 单次工具调用的 span 字段

- **WHEN** 一轮对话中主 agent 调用一次 `retrieve_kb`
- **THEN** 该 trace 下存在一条携带工具名、入参、返回值与起止时间的 span
- **AND** 该 span 的耗时等于其起止时间之差

#### Scenario: 同一轮多次调用归入父 span

- **WHEN** 一轮 `tools` 节点执行中发生两次工具调用
- **THEN** 存在一条代表该轮的父 span，两条工具 span 均以其为父节点

#### Scenario: 工具执行失败

- **WHEN** 某次工具调用抛出异常
- **THEN** 对应 span 仍被产出并标记为错误级别，且携带错误信息

#### Scenario: 生成被取消时工具 span 不悬空

- **WHEN** 工具执行期间用户取消生成
- **THEN** 已开启的工具 span SHALL 在收尾时被关闭（携带结束时间），不留下只有开始时间的悬空节点

#### Scenario: 新工具零改动获得观测

- **WHEN** 通过既有统一入口注册一个新工具（本地工具或 MCP 适配工具）并在一轮对话中调用
- **THEN** 该调用自动产出一条字段相同的 span，无需为它单独埋点或修改观测代码

### Requirement: trace 的用户、标签与业务元数据

trace SHALL 记录触发该轮对话的用户标识；SHALL 记录一组**低基数且稳定**的标签用于筛选；SHALL 以元数据形式记录本轮的运行事实（生效智能体、本轮加载的技能、知识库标识与领域、深思考开关等）。

标签 SHALL 仅限低基数取值；**高基数或易变的取值（如具体知识库标识、智能体名、技能名）SHALL 写入元数据而非标签** —— 标签一经写入无法通过 API 删除，写错或改口径会永久残留。

#### Scenario: 用户标识落库

- **WHEN** 一轮对话完成
- **THEN** 该 trace 携带触发用户的标识，可据此筛选与聚合

#### Scenario: 低基数标签

- **WHEN** 一轮对话完成
- **THEN** 该 trace 的标签仅包含低基数的稳定取值（如是否绑定知识库）
- **AND** 具体知识库标识、生效智能体名与技能名出现在元数据中，而非标签中

#### Scenario: 业务元数据可读

- **WHEN** 在 Langfuse 中打开一条 trace
- **THEN** 可从其元数据读到本轮的生效智能体、加载的技能与知识库上下文，无需回查日志

### Requirement: 模型成本可见

系统 SHALL 支持为实际使用的模型配置输入 / 输出单价，使 Langfuse 能据用量计算成本。单价 SHALL 从配置读取（环境变量可覆盖），**默认值 SHALL 表示「未配置」**；未配置时系统 SHALL NOT 向 Langfuse 写入单价为 0 的模型定义 —— 避免成本页出现会被误读为「免费」的零值。

定价的写入 SHALL 通过可重复执行的幂等命令完成，使开发与生产环境各自落地同一份定价。

#### Scenario: 未配置单价时不产生零成本假象

- **WHEN** 单价配置保持默认（未配置）并完成一轮对话
- **THEN** Langfuse 中不出现单价为 0 的模型定义
- **AND** 该 trace 的成本字段保持为空，而非显示为 0

#### Scenario: 配置单价后可算出成本

- **WHEN** 配置了单价并执行一次定价写入命令
- **THEN** 后续对话的 generation 携带非零成本
- **AND** 定价写入命令重复执行不产生重复的模型定义

#### Scenario: 估算用量的成本口径可辨识

- **WHEN** 某次调用的 token 用量为估算值
- **THEN** 该轮成本由估算用量与单价计算得出，且该轮仍带有「用量为估算」的标识

## MODIFIED Requirements

### Requirement: 主 agent 每轮推理记为 generation

主 agent 的每一次 LLM 推理（图循环内每次模型调用）SHALL 产出一条 generation 类型的 observation，SHALL 至少记录模型名、输入消息、输出文本、token 用量与首 token 时间。

输入消息 SHALL 采用 OpenAI 形态的角色标识（system / user / assistant / tool），并 SHALL 保留 assistant 消息发起的工具调用（工具名与入参）与 tool 消息的工具名 —— 使「模型要调什么、工具结果回给了谁」可直接读出。当某轮模型只发起工具调用、未产出文本时，输出 SHALL 记录该轮的 `tool_calls` 而非留空。

#### Scenario: 单轮迭代的 generation 字段

- **WHEN** 主 agent 完成一次模型调用
- **THEN** 对应 generation 携带模型名、输入消息、输出文本与首 token 到达时间
- **AND** token 用量取流聚合后的真实计数

#### Scenario: 用量缺失时回落估算

- **WHEN** 模型响应未返回 token 用量元数据
- **THEN** generation 的用量 SHALL 使用文本长度估算值，并以可识别的方式标注该值为估算（例如写入 metadata）

#### Scenario: 多轮迭代各成一条

- **WHEN** 一轮对话内主 agent 迭代两次
- **THEN** 该 trace 下存在两条 generation，各自对应一次模型调用

#### Scenario: 工具轮输出不为空

- **WHEN** 某轮模型只发起工具调用、未产出任何文本
- **THEN** 该 generation 的输出记录本轮的工具调用（工具名与入参），而非空值

#### Scenario: 输入消息的角色与工具调用可读

- **WHEN** 检查某轮 generation 的输入消息列表
- **THEN** 各消息的角色为 OpenAI 形态（system / user / assistant / tool）
- **AND** assistant 消息携带其发起的工具调用（工具名与入参），tool 消息携带其工具名

### Requirement: trace 内容记录范围

trace 的 generation SHALL 记录该次 LLM 调用的输入与输出原文（含 system prompt、检索上下文与回答），使链路可完整回放。工具 span SHALL 记录该次工具调用的入参与返回值原文。记录输入 SHALL 采用**显式写入**方式；被装饰函数的**内部运行时对象 SHALL NOT 进入 trace**（如请求上下文对象、流式管理器、编译后的图、事件与队列等）。

该行为 SHALL 在文档中作为已知事实登记，并说明 trace 的保留期可能与对话记录的保留期不一致。

#### Scenario: 原文可回放

- **WHEN** 在 Langfuse 中打开某条 trace 的主 agent generation
- **THEN** 可看到该次调用的完整输入消息与输出文本

#### Scenario: 工具入参与返回可回放

- **WHEN** 在 Langfuse 中打开某条工具 span
- **THEN** 可看到该次调用的入参与该工具的返回值原文

#### Scenario: 内部运行时对象不进 trace

- **WHEN** 检查某条 trace 的根 observation、generation 或工具 span 的输入字段
- **THEN** 其中只包含该记的业务输入（查询文本 / 消息列表 / 工具实参等）
- **AND** 不出现请求上下文对象、流式管理器、编译后的图、事件或队列等内部对象的序列化结果

#### Scenario: 记录范围有明确文档说明

- **WHEN** 查阅 trace 相关文档
- **THEN** 可读到「trace 记录 prompt、回答与工具调用原文」及其保留期，无需从代码反推
