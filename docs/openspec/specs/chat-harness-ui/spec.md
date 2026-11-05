# chat-harness-ui Specification

## Purpose
TBD - created by archiving change agent-harness-foundation. Update Purpose after archive.
## Requirements
### Requirement: 侧栏结构

聊天页侧栏 SHALL 自上而下包含：头部（Logo + 品牌名 + 版本号）、功能块（新建会话、知识库管理两个按钮）、会话历史列表（可滚动，项带相对时间，hover/active 态）、底部登录头像（登录/退出入口）。

#### Scenario: 侧栏渲染

- **WHEN** 打开聊天页
- **THEN** 侧栏按头部/功能块/会话历史/登录头像顺序渲染，样式为浅色系

### Requirement: 双页面形态

聊天页 SHALL 区分两个独立页面：**新对话页**（无消息，居中品牌 + 标题 + 居中输入框，无示例问题）与**历史对话页**（有消息，顶栏 + 消息流 + 底部固定输入框）；点击侧栏"新建会话"进入新对话页，点击会话历史项进入历史对话页。

#### Scenario: 新建会话进入新对话页

- **WHEN** 用户点击侧栏"新建会话"
- **THEN** 进入新对话页：品牌 + 主标题 + 副标题 + 居中输入框，无示例问题

#### Scenario: 打开历史会话进入历史对话页

- **WHEN** 用户点击会话历史中某一项
- **THEN** 进入历史对话页：顶栏显示会话标题与绑定 KB、消息流、底部固定输入框

### Requirement: 知识库选择器（新对话页输入框上方）

新对话页 SHALL 在输入框上方独立一行展示知识库选择器（胶囊按钮）；点击 SHALL 弹出单选弹窗（列出用户 KB + "知识库管理…"入口）；**默认不勾选**显示"选择知识库"，勾选后显示 KB 名，取消勾选显示"请选择知识库"；**单选**（选 A 取消 B）。

#### Scenario: 默认未选择知识库

- **WHEN** 新建会话进入新对话页
- **THEN** 知识库选择器默认不勾选任何 KB，胶囊显示"选择知识库"

#### Scenario: 单选勾选知识库

- **WHEN** 用户勾选"腾讯"
- **THEN** 腾讯被选中（其他 KB 取消），胶囊显示"腾讯"

#### Scenario: 取消勾选

- **WHEN** 用户再次点击已勾选的"腾讯"
- **THEN** 取消选中，胶囊显示"请选择知识库"

### Requirement: 知识库会话级绑定

知识库 SHALL 在新建会话时选定，会话内不可修改；要更换知识库 SHALL 新建会话重新选择。历史对话页顶栏 SHALL 显示 `会话名 + 空格 + 知识库:XXX`；会话未绑定 KB 时不显示知识库部分。

**KB = RAG 开关**：会话**绑定知识库才启用 RAG 检索**；未绑定 KB 的会话不调用 `retrieve_kb`（该工具不出现在 LLM 工具列表、system prompt 不引导检索），agent 走纯对话或联网。**跨库检索本轮废弃**（`kb_id` 空值语义为"不检索"，非"搜索所有知识库"）。

#### Scenario: 历史对话页顶栏显示绑定 KB

- **WHEN** 打开一个绑定了"腾讯"KB 的历史会话
- **THEN** 顶栏显示"腾讯这几年业绩怎么样 知识库:腾讯"

#### Scenario: 未绑定 KB 的会话

- **WHEN** 打开一个未选择知识库的历史会话
- **THEN** 顶栏只显示会话名，不显示知识库部分

#### Scenario: 未绑定 KB 不调用 RAG

- **WHEN** 用户新建会话未选择知识库直接提问
- **THEN** 系统不调用 `retrieve_kb`，agent 走纯对话/联网（不检索知识库）

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

### Requirement: 过程区历史回放

历史会话加载（loadSessionMessages）对含非空 `process` 字段的 assistant 消息 SHALL 按事件数组顺序重建过程容器（状态行/旁白块/委派区），渲染产物 SHALL 与实时观看一致；`process` 为 null 的存量消息 SHALL 仅渲染终稿，不报错。本要求取代 D7「历史重载不重建（仅展示终稿）」决策。

#### Scenario: 刷新后过程原样重现

- **WHEN** 用户刷新页面，加载含 process 数据的会话
- **THEN** 该回答上方 SHALL 按事件顺序重建过程容器，正文气泡仅含正式回答（无旁白粘连）

#### Scenario: 存量消息降级

- **WHEN** 加载 process 为 null 的历史消息
- **THEN** 仅渲染终稿与引用，无过程容器，无 JS 错误

### Requirement: 旁白块渲染

实时与历史路径中，判定为旁白的 content 流（最后一次工具调用之前的轮次 content）SHALL 渲染为过程容器内的旁白块（区别于正文的弱化样式：虚线左边框、斜体）；纯问答（全程无工具调用）SHALL 不产生旁白块。

#### Scenario: 旁白块样式区分

- **WHEN** 过程容器内渲染旁白块
- **THEN** 其样式 SHALL 与正文气泡可明确区分（虚线左边框 + 斜体 + 弱化配色）

### Requirement: 相邻同型合并渲染

过程容器渲染 SHALL 将「相邻且同类型」的元素合并为一个组 div，类型不同即分拆，只看相邻不看全局，整体严格按事件到达顺序；实时路径与历史回放 SHALL 共用同一合并实现。

#### Scenario: 连续状态合并

- **WHEN** 过程元素序列为 status, status, status, think, status
- **THEN** 渲染为 3 个组 div（status×3 合一、think 独立、status 独立）

#### Scenario: 类型交错不合并

- **WHEN** 过程元素序列为 status, preamble, think, status, retrieve
- **THEN** 渲染为 5 个组 div

### Requirement: 引用来源权威等级徽标

仅引用抽屉条目 SHALL 展示来源权威等级徽标（官方一手/权威媒体/一般/UGC/内部文档，对应 citation 的 tier 字段，位于来源名称之前）；引用横条保持既有形态不渲染徽标（设计已确认）。实时路径与历史回放路径行为一致；tier 缺失（存量消息）SHALL 不显示徽标，其余引用功能不受影响。

#### Scenario: 实时路径展示等级

- **WHEN** 实时流 citation 帧携带 tier 字段
- **THEN** 打开引用抽屉后，条目 SHALL 在来源名称之前展示对应等级标识

#### Scenario: 历史回放展示等级

- **WHEN** loadSessionMessages 重建引用（sources 含 tier）
- **THEN** 重建的抽屉条目 SHALL 与实时路径展示相同的等级徽标

#### Scenario: 存量数据无 tier

- **WHEN** citation 数据缺少 tier 字段（存量消息）
- **THEN** 不显示徽标，横条/抽屉/snippet 功能正常，无 JS 错误

### Requirement: 智能体选择器（新对话页）

新对话页 SHALL 在**知识库选择器右侧**提供智能体选择器（胶囊按钮 + 单选下拉菜单，与知识库选择器同构）。菜单 SHALL 由**前端合成的首项「默认」**（`value=""`，不在接口返回中）与 `GET /api/agents` 的 `data.agents` 组成。默认项文案 SHALL 为「**默认**」（对应未选择 ＝ 系统默认人设），选中其余项显示其 `display_name`。选择器 SHALL 提供当前智能体名与描述，并在菜单内提示"选定后本会话内不可更改"。

#### Scenario: 默认未选择

- **WHEN** 新建会话进入新对话页
- **THEN** 智能体选择器显示「默认」，为未选中态（次级字色）

#### Scenario: 位于知识库选择器右侧

- **WHEN** 新对话页渲染知识库选择器与智能体选择器
- **THEN** 两者位于同一行，智能体选择器在知识库选择器右侧

#### Scenario: 单选选中

- **WHEN** 用户从菜单选择"财务专家"
- **THEN** 胶囊显示"财务专家"（主蓝），该会话以财务专家身份服务

#### Scenario: 从历史页发起新会话

- **WHEN** 用户在历史对话页点击"新建会话"进入新对话页
- **THEN** 智能体选择器可用（与知识库选择器一致）；选择器**只在会话未开始时**可用

#### Scenario: 无可用预设

- **WHEN** `agents/` 目录缺失或为空
- **THEN** 选择器仅提供「默认」项，不报错

### Requirement: 技能选择器（输入区）

输入区 SHALL 在**深度思考开关右侧**提供技能选择器（同构 chip + 向上弹出的下拉菜单），列出可用户调用的技能（服务端已在 `GET /api/skills` 的 `data.skills` 中过滤掉 `user-invocable: false`，前端只负责渲染）。选择 SHALL 将该技能以 `/name ` 字面量插入输入框行首（与手输 `/` 等价），并同步 chip 为选中态；未选择时 chip 显示中性文案「技能」。

该选择器 SHALL 与输入框行首 `/` 补全**共用同一候选数据与同一后端解析**，不新增第二条调用通道。新对话页与历史对话页 SHALL 均提供。

#### Scenario: 选择技能插入字面量

- **WHEN** 用户从技能菜单选择 `financial-statement-analyzer`
- **THEN** 输入框行首插入 `/financial-statement-analyzer `，chip 变为选中态并显示该技能名

#### Scenario: 位置与深度思考并列

- **WHEN** 输入区渲染底部工具条
- **THEN** 技能选择器与深度思考开关位于同一行，技能选择器在其右侧

#### Scenario: 隐藏不可用户调用的技能

- **WHEN** 某 skill 声明 `user-invocable: false`
- **THEN** 该 skill 不出现在技能选择器菜单

#### Scenario: 清除选择

- **WHEN** 用户在菜单选择「不使用技能」
- **THEN** 输入框行首的 `/name` 前缀被移除，chip 恢复中性文案「技能」

#### Scenario: 无可用技能

- **WHEN** 没有任何可用户调用的技能
- **THEN** chip 仍可点击，菜单显示空态提示，不隐藏 chip

### Requirement: 智能体会话级绑定（前端约束）

智能体 SHALL 在新建会话时选定，**首次绑定即固化**；会话内选择器 SHALL 不可用（提示需新建对话）。历史对话页顶栏 SHALL 回显该会话的智能体名；该会话的 `agent` SHALL 随每轮请求携带（取自 `sessions/list` 项），以便后端按绑定值继续。回显数据缺失（预设已删除）时 SHALL 降级显示原始名。选择器仅在会话未开始时可用。

#### Scenario: 会话内不可更换

- **WHEN** 用户在已开始的会话中查看输入区
- **THEN** 智能体选择器不可交互（历史对话页不渲染选择器，顶栏以只读徽标回显）

#### Scenario: 历史会话回显

- **WHEN** 打开一个选定"财务专家"的历史会话
- **THEN** 顶栏回显"财务专家"（取自 `sessions/list` 项的 `agent`）

#### Scenario: 每轮携带绑定值

- **WHEN** 用户在已绑定智能体的会话中继续提问
- **THEN** 请求体携带该会话绑定的 `agent`（而非当轮 UI 状态）

#### Scenario: 生效值回传纠正显示

- **WHEN** 流事件带回 `agent_used` 与前端当前显示不一致
- **THEN** 前端据此纠正顶栏/标识显示（不阻断本轮）

### Requirement: 输入框 `/` 命令补全

输入框 SHALL 在行首输入 `/` 时展示可调用 skill 的候选列表（按名过滤），选中后插入 `/name ` 字面量；不展示 `user-invocable: false` 的 skill。

#### Scenario: 行首 `/` 触发候选

- **WHEN** 用户在输入框行首输入 `/`
- **THEN** 展示可调用 skill 候选列表

#### Scenario: 过滤与插入

- **WHEN** 用户继续输入 `/fin`
- **THEN** 候选按名过滤；选中"financial-statement-analyzer"后输入框插入 `/financial-statement-analyzer `

#### Scenario: 隐藏仅模型可调用的 skill

- **WHEN** 某 skill 声明 user-invocable: false
- **THEN** 该 skill 不出现在候选列表

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
