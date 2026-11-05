# agent-loop-observability Specification

## Purpose
TBD - created by syncing change agentic-clarification. Update Purpose after sync.
## Requirements
### Requirement: 全流程节点日志可查

系统 SHALL 为流水线每个图节点（kb_router、classify（若保留）、agent 循环、format 等）记录进入与结束日志：节点名、trace_id、关键入参摘要、输出摘要、耗时。日志经 trace_id SHALL 可还原单次请求的完整节点执行链（节点顺序 + 各节点输入输出）。

#### Scenario: 按 trace_id 还原执行链
- **WHEN** 排查一次回答的生成过程
- **THEN** 按 trace_id 聚合日志可按执行顺序还原每个节点的进出与耗时

#### Scenario: 节点异常可定位
- **WHEN** 某节点执行异常
- **THEN** 日志含节点名与 trace_id，可定位到具体节点与请求

### Requirement: 工具调用日志

系统 SHALL 为 agent 循环中的每次工具调用（retrieve_kb、ask_user、escalate_to_human）输出一行结构化日志：iteration 序号、工具名、参数摘要、结果摘要、耗时、token 增量（如有）。日志 SHALL 足以在无 Langfuse 的情况下从文本还原"模型调了什么工具 → 得到什么 → 下一步决策"。

#### Scenario: 工具调用可追踪
- **WHEN** agent 循环中调用 retrieve_kb
- **THEN** 日志输出一行含 trace_id/iteration/tool=retrieve_kb/args 摘要/结果条数/耗时

#### Scenario: 循环序列可还原
- **WHEN** 排查一次多轮检索的 agent 回答
- **THEN** 按 trace_id 聚合日志可按 iteration 顺序还原完整工具调用序列

### Requirement: 护栏命中告警

系统 SHALL 在护栏触发时输出 warn 级日志并记录现场：`MAX_AGENT_ITERATIONS` 命中（含 query 与 iteration 轨迹）、`ASK_USER_TIMEOUT` 超时、`MAX_ASK_PER_TURN` 超限、abort 中止（含已耗 token 与当前 stage）。此类日志作为任务定义/工具设计异常的运营信号。

#### Scenario: 迭代上限命中告警
- **WHEN** agent 循环达 MAX_AGENT_ITERATIONS 强制收尾
- **THEN** 记录 warn 日志，含 query、iteration 轨迹、触发原因

### Requirement: 循环内 token 用量聚合

系统 SHALL 在 agent 循环内对每次 LLM 调用的 token 用量（复用 TokenUsage）进行聚合，turn 结束时记录循环总用量（prompt/completion/total）与调用次数，随现有日志输出。

#### Scenario: 多轮检索用量汇总
- **WHEN** agent 循环含 3 次 LLM 调用
- **THEN** turn 结束日志含 3 次调用的总 token 用量与调用次数

### Requirement: 循环回合上限命中时的消息状态

回合上限命中时 SHALL 产出 warn 级日志——其事件名与字段集沿用既有「护栏命中告警」要求（`query` 与 `iteration`），本要求**不重复规定日志内容**，只补充两点：日志的产出条件 SHALL 与停止判定**解耦**（见下），且上限口径 SHALL 取**有效上限**（基础上限 + 委派放宽增量；既有要求里 `MAX_AGENT_ITERATIONS` 的表述以本条口径为准）。

该轮循环 SHALL 以如下**确切状态**结束：

- 计数达到有效上限时**即**产出该 warn 日志——产出条件 SHALL NOT 依赖"该轮是否仍声明工具调用"（模型在上限轮恰好正常收尾时，日志同样产出）
- 模型调用次数 SHALL 恰好等于该轮有效上限（含委派放宽后的值）
- 工具执行次数 SHALL 等于**有效上限减一**——即最后一轮模型声明的工具调用**不执行**；该式对上限为 1 的情形同样成立（此时工具执行次数为零）
- 消息序列末条 SHALL 是**含 `tool_calls` 的 `AIMessage`**；若上限轮为正常收尾（无工具调用），末条 SHALL 是该轮正常的 `AIMessage`

由此，该轮的答案文本 SHALL 允许为空串，且 SHALL NOT 被替换为提示性文案或工具返回内容。

**理由**：这是既有行为的确切语义，也是"触顶后用户看到空回答"这一已知缺陷的载体（见 `requirements_pool` F-37）。上游修复（轮次/时长护栏取值与兜底）由独立变更承担；在此之前本语义 SHALL 保持不变，任何改动都须显式可见。

#### Scenario: 上限轮正常收尾也产出告警

- **WHEN** 模型在第 N 次调用（N 等于有效上限）中未声明工具调用、正常收尾
- **THEN** 系统 SHALL 仍产出回合上限命中的 warn 日志（含 query 与 iteration）

#### Scenario: 触顶时的模型调用与工具执行次数

- **WHEN** 主循环达到有效回合上限且最后一轮模型声明了工具调用
- **THEN** 模型 SHALL 被调用恰好上限次，工具 SHALL 被执行上限减一次
- **AND** 最后一轮声明的工具调用 SHALL NOT 被执行

#### Scenario: 触顶时末条消息为含工具调用的 AIMessage

- **WHEN** 主循环达到有效回合上限且最后一轮声明了工具调用
- **THEN** 该轮消息序列的末条 SHALL 是含 `tool_calls` 的 `AIMessage`
- **AND** 该消息的正文 SHALL 允许为空串，SHALL NOT 被自动填充为工具结果或限流提示文案

#### Scenario: 不注入限流提示消息

- **WHEN** 回合上限命中
- **THEN** 系统 SHALL NOT 向消息序列注入任何提示性（如"已达调用上限"）消息

### Requirement: 循环域日志的字段值逐字保持

主循环的三条日志（`iteration done` / `iteration limit` / `model turn`）在循环改由装配入口承载后，SHALL 保持**事件名、字段集与字段值**逐字不变：

- `iteration done` 的 `msgs` SHALL 仍计**本轮送入模型的完整消息条数（含 system 段）**——system 段改由 middleware 施加后，SHALL NOT 因消息拆分而少计
- 三条日志的 `iteration` SHALL 都等于**本次模型调用的序号**（第 k 次调用取 k），SHALL NOT 因计数在钩子中的自增时机而整体偏移
- `model turn` 的 `temperature` / `temp_source` SHALL 与**实际施加到本次调用**的档位一致（观测 SHALL NOT 与生效参数脱节）
- 施加到模型的调用参数（温度档位与 `extra_body` 的思考开关）SHALL 与变更前一致：**思考开关的值 SHALL 来自随调用链传入的会话开关**，SHALL NOT 因该值未随图状态传入而静默取默认（那会让深度思考对所有请求关闭）

#### Scenario: msgs 计入 system 段

- **WHEN** 未绑 KB 的一次生成产出 `iteration done` 日志
- **THEN** 其 `msgs` SHALL 等于含两条 system 消息在内的完整消息条数，SHALL NOT 等于拆分后的非 system 条数

#### Scenario: iteration 为本次调用序号

- **WHEN** 主循环发生第 k 次模型调用
- **THEN** `iteration done` 与 `model turn` 的 `iteration` SHALL 均为 k（首次调用取 1）

#### Scenario: 温度上报与生效档位一致

- **WHEN** 某次生成绑定了 KB（沿用不传 `temperature` 的档位）
- **THEN** `model turn` 的 `temperature` / `temp_source` SHALL 反映该档位，且实际施加给模型的参数 SHALL 与之一致

#### Scenario: 思考开关随调用链传入

- **WHEN** 一次生成的会话开关为「开启深度思考」
- **THEN** 施加给模型的调用参数 SHALL 携带开启的思考开关，SHALL NOT 因该值未随图状态传入而落回关闭态

### Requirement: 每轮来源事实可查

系统 SHALL 为每次生成记录足以判定"这一轮由谁、以何种采样参数产生"的日志，且无需依赖数据库反查：

- 每次 LLM 调用的采样温度（含**实际取值来源**：显式传参 / 沿用构造默认）与是否绑定 KB 的档位；
- 生效智能体及其来源（沿用绑定 / 首次绑定 / 请求不一致被忽略 / 未注册降级 / 未绑定）；
- 人设是否实际生效（是否取自智能体预设）。

#### Scenario: 温度可从日志直接读出
- **WHEN** 排查某轮生成使用的采样温度
- **THEN** 该轮每次 LLM 调用的日志行含采样温度字段及其取值来源（显式传参 / 沿用默认）与 KB 档位，使"实际传值是多少"与"是否只是沿用默认"可区分

#### Scenario: 生效智能体与来源可读
- **WHEN** 排查某轮生效的智能体
- **THEN** 日志含请求值、会话绑定值、生效值与其来源（绑定/新增绑定/不一致忽略/未注册降级/未绑定）

#### Scenario: 人设生效与否可读
- **WHEN** 某轮绑定了智能体预设
- **THEN** 日志记录人设是否生效，从而可区分"人设已替换基础段"与"未选智能体沿用默认人设"

### Requirement: 技能加载与分派可查

系统 SHALL 记录本轮技能相关事实：**命令形态**的分派结果（命中已注册 / 形如命令但未注册）、技能执行形态（inline / fork）、以及技能正文成功注入（区分"用户命令触发"与"预设预加载"）及注入体量。**非命令（普通文本）轮 SHALL NOT 记录分派日志**——事件级别由 `EventSpec` 固定、调用点不能逐次降级，故以"不记"控制噪声。失败跳过仍按既有告警记录。

#### Scenario: 命令形态分派可读
- **WHEN** 排查某轮输入是否被当作命令解析
- **THEN** 该轮输入形如命令（命中已注册 / 形如命令但未注册）时，日志含解析结果与技能名、执行形态、以及直接执行目标；普通文本轮不产出该分派日志

#### Scenario: 注入来源可区分
- **WHEN** 某轮向会话注入了技能正文
- **THEN** 日志区分该注入来自用户命令还是预设首轮预加载，并记录体量（字符数）

#### Scenario: 预加载失败仍告警
- **WHEN** 预设声明的技能名在注册表中不存在
- **THEN** 仍输出既有的跳过告警（含技能名与原因），不受新增日志影响

### Requirement: prompt 组成与消息构成可查

系统 SHALL 记录每轮 system prompt 的组成来源与各段消息数量，使"人设来自预设还是基础段、是否注入引用要求/委派引导/检索纪律、注入了多少条隐藏消息"可从日志判定。

#### Scenario: 人设来源可读
- **WHEN** 排查某轮 system prompt 的人设来源
- **THEN** 日志标注人设取自预设正文或默认基础段

#### Scenario: 条件注入可读
- **WHEN** 某轮绑定了 KB 或存在可用技能
- **THEN** 日志标注引用要求/委派引导/检索纪律等条件段是否注入

#### Scenario: 消息构成可读
- **WHEN** 排查某轮首轮组装的消息构成
- **THEN** 日志给出 system / 隐藏注入 / 历史 三类消息的条数，而非仅总数
