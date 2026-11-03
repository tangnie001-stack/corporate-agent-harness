## ADDED Requirements

### Requirement: 报警轮按 skill 声明采集证据，不交给模型选择查什么

系统 SHALL 按告警名匹配预先定义的 skill（`skills/rca-*/SKILL.md`），并由**代码**解析其 frontmatter 的
`evidence` 清单决定取哪些证据；SHALL NOT 由模型临场决定"查什么"。

`evidence` 条目 SHALL 包含：`id`、`tool`、`params`（支持 `${alert.*}` 占位符）、`required`、`timeout`，
以及可选的 `when`。占位符解析 SHALL 采用**严格模式**——缺字段时该条目标记 `failed`，SHALL NOT 用默认值兜底。

未匹配到任何 skill 时 SHALL 使用通用兜底清单，并在结论中标注"该告警类型未覆盖"。

#### Scenario: 匹配到专用 skill

- **WHEN** 告警名命中某 skill 的 `match` 规则
- **THEN** 使用该 skill 的 `evidence` 清单取数，并按该 skill 的判定正文生成结论

#### Scenario: 占位符缺字段

- **WHEN** 某证据条目的 `params` 引用了告警上下文里不存在的字段
- **THEN** 该条目记为 `failed`（原因 `missing_field`），不以默认值继续执行

#### Scenario: 未匹配到 skill

- **WHEN** 告警名不匹配任何 skill
- **THEN** 使用通用兜底清单，结论标注该告警类型未覆盖

### Requirement: 告警上下文从规则定义反查补齐

系统 SHALL 在证据采集前补齐告警上下文：接口 URI、告警阈值、`for` 时长、聚合方式、数据源标识。
来源 SHALL 为 Grafana 规则定义接口（`/api/prometheus/grafana/api/v1/rules`），按
**`alertname` + `grafana_folder`** 匹配（该接口不返回规则 UID）。

URI 与数据源 SHALL 从规则查询语句中提取（如 `request_uri = '...'`、`logstore`）；阈值 SHALL 取
条件表达式的阈值参数；`for` 时长 SHALL 取规则的 `duration`。

反查结果 SHALL 缓存，缓存键为 `alertname|grafana_folder`，有效期不超过 10 分钟。

反查失败或解析不出 URI 时，系统 SHALL 标记该告警为"语义未知"，SHALL 判为 `建议观察`，
SHALL NOT 引用其数值生成结论，SHALL NOT 猜测 URI。

#### Scenario: 正常反查

- **WHEN** 告警名与 folder 能在规则列表中找到，且查询语句含 `request_uri`
- **THEN** 上下文包含 uri、阈值、for 时长与数据源，后续取数使用这些值

#### Scenario: 规则写法不匹配

- **WHEN** 规则查询不含可提取的 `request_uri`（例如网关类 PromQL 规则）
- **THEN** 标记"语义未知"，结论为 `建议观察`，卡片列出缺失项

#### Scenario: 规则接口不可用

- **WHEN** 规则反查超时或返回错误
- **THEN** 同上处理，且不阻断后续兜底转发

### Requirement: 告警历史从注释接口读取

系统 SHALL 从 Grafana 注释接口（`/api/annotations?type=alert`）读取历史告警，用于判断"近 7 天同告警
是否反复出现且已自愈"。

该接口返回的是**每分钟快照**而非事件，系统 SHALL 按 `alertname` 与时间窗聚合成"出现的分钟数"，SHALL NOT
以返回条数直接充当告警次数。系统 SHALL 处理分页，SHALL 限制查询时间跨度。

#### Scenario: 近 7 天反复出现

- **WHEN** 同一 `alertname` 在 7 天内出现多次且当前规则状态为未触发
- **THEN** 作为"历史同模式且已自愈"的正向证据之一

#### Scenario: 无历史

- **WHEN** 7 天内无同告警记录
- **THEN** 该条证据记为 `empty`，不作为正向证据，也不阻断判定

### Requirement: 证据采集分波并行且有独立超时

系统 SHALL 先并行执行**无 `when` 条件**的条目，再对 `when` 成立（依赖前一波结果）的条目执行第二批；
两批 SHALL 分别并行，SHALL NOT 串行逐条执行。

每个条目 SHALL 有独立超时；整轮采集 SHALL 有总预算上限。超预算时已完成结果保留、未完成标
`failed/total_timeout`，并 SHALL 对所有在途查询执行取消。

采集 SHALL 在生成之前**完全收口**，SHALL NOT 与生成并行；采集与生成的总时长 SHALL NOT 超过既有整轮超时。

#### Scenario: 第一波全部并行

- **WHEN** skill 声明了 3 个无 `when` 的条目
- **THEN** 三条同时发起，总耗时接近最慢的一条

#### Scenario: 单个源超时

- **WHEN** 某条证据超过自身 `timeout`
- **THEN** 该条标记 `failed`，其余条目照常完成并进入判定

#### Scenario: 总预算耗尽

- **WHEN** 采集总耗时达到预算上限
- **THEN** 返回已完成的部分，未完成项标 `total_timeout`，并取消在途查询

### Requirement: 证据三态在结论中必须可区分

每个证据条目的结果 SHALL 带三态之一：`ok`、`empty`（查到但无数据）、`failed`（未查到，含超时 /
鉴权失败 / 字段缺失 / 工具不存在）。

结论 SHALL 区分"查到了没有异常"与"没有查到"；SHALL NOT 把 `failed` 表述为"未发现异常"。

`required` 条目 `failed` 时，系统 SHALL NOT 输出根因假设，只输出"证据不足"与缺失项清单，且判定档位
SHALL 为 `建议观察`。

#### Scenario: 必需源失败

- **WHEN** 标为 `required` 的条目返回 `failed`
- **THEN** 结论为"证据不足"，档位为 `建议观察`，列出失败条目，不给出根因假设

#### Scenario: 空结果不等于失败

- **WHEN** 某条目成功执行但返回零条数据
- **THEN** 记为 `empty`，结论中表述为"该源无数据"，而非"未取到"

### Requirement: 判定档位由代码确定，模型只产出解释

判定档位（`无需处理` / `建议观察` / `需要处理`）SHALL 由代码依据证据确定性计算。

模型 SHALL 只输出解释类字段：一句话结论、假设、反证、下一步动作、置信度；SHALL NOT 输出档位。

模型输出与代码计算结果冲突时（例如模型在文本中声称"无需处理"而代码判定为 `建议观察`），
SHALL 以代码档位为准，并记录一条降级日志。

#### Scenario: 模型与代码不一致

- **WHEN** 模型文本暗示的严重程度与代码计算的档位不同
- **THEN** 卡片显示代码档位，日志记录该差异

#### Scenario: 同一证据可复现

- **WHEN** 同一组证据被重复判定
- **THEN** 档位结果一致

### Requirement: 保守判定——「无需处理」必须同时满足四个条件

判为 `无需处理` SHALL 同时满足：

1. 观测窗口样本量 `n ≥ 1`；`n = 0` SHALL 判为 `建议观察`，并标注"日志断流或服务不可用"；
2. 窗口内 `5xx` 计数为 0；
3. 慢请求数不超过 1 条；
4. 窗口内最大耗时低于**严重阈值**（按接口配置，独立于告警阈值）。

另 SHALL 至少命中一条正向证据：近 7 天同告警形态一致且已自愈 / 当前规则状态已恢复 /
样本量低于告警口径所需的最小样本量。

系统 SHALL NOT 以"未找到原因""证据不足"推断 `无需处理`。

#### Scenario: 低流量接口的单条慢请求

- **WHEN** 窗口内 2 条请求，1 条略高于告警阈值但低于严重阈值，无 5xx
- **THEN** 判为 `无需处理`，附两条明细

#### Scenario: 两条请求都异常

- **WHEN** 窗口内 2 条请求，其中 2 条均超过慢请求阈值
- **THEN** 不判 `无需处理`，进入后续判定

#### Scenario: 零样本

- **WHEN** 窗口内没有任何请求
- **THEN** 判为 `建议观察`，标注"日志断流或服务不可用"，SHALL NOT 判 `无需处理`

#### Scenario: 超过严重阈值

- **WHEN** 窗口内最大耗时超过严重阈值
- **THEN** 判为 `建议观察` 或 `需要处理`，SHALL NOT 判 `无需处理`

### Requirement: 归因前先做样本量与统计显著性校验

系统 SHALL 在给出任何原因解释之前校验样本量：

- `n = 0`：判"日志断流或服务不可用"，不进入归因；
- `1 ≤ n < 20`：逐条比对明细，慢请求不超过 1 条则判孤立慢请求，不进入归因；
- `n ≥ 20`：进入正常归因。

"慢请求"阈值 SHALL 按接口配置，SHALL NOT 使用统一全局默认值。

#### Scenario: 低流量接口多条同特征慢请求

- **WHEN** 窗口内 10 条请求，4 条超过阈值且集中于同一节点
- **THEN** 进入归因，输出"单节点异常"方向

### Requirement: 定量字段由代码注入，模型不转写数字

卡片中的时间、影响量、分布、业务影响等数值 SHALL 由渲染层从证据包直接填充，SHALL NOT 由模型输出。

模型输出 SHALL 仅包含定性内容；其文本中出现的数值 SHALL NOT 被渲染进卡片。

#### Scenario: 模型复述了错误数字

- **WHEN** 模型在假设文本里写了与证据不一致的数字
- **THEN** 卡片中的定量字段仍取自证据，模型文本仅出现在"假设"段落且不参与关键结论

### Requirement: 结论以结构化字段产出并由模板渲染

系统 SHALL 校验模型输出的结构（字段缺失或类型不符即失败），失败时 SHALL 走既有兜底转发，
SHALL NOT 推送残缺卡片。

展示文本 SHALL 由模板渲染，模板位于 `src/config/`，渲染后通过既有报警推送通道发送。

#### Scenario: 结构校验失败

- **WHEN** 模型输出缺少必需字段
- **THEN** 走兜底转发，不推送卡片

### Requirement: 业务影响以昨日同时段为基线

系统 SHALL 给出核心接口的业务影响（请求量、错误率、耗时的同比变化），基线 SHALL 取**昨日同一时段**，
SHALL NOT 使用"前一小时"。

日志无法反映业务成败时，结论 SHALL 标注"业务成功率未知"，SHALL NOT 以接口层 2xx 比例代替。

#### Scenario: 业务结果不可见

- **WHEN** 该接口日志不含业务结果字段
- **THEN** 卡片标注"业务成功率未知"

### Requirement: 日志取数经 Grafana 代理，不依赖 SLS 凭据

系统 SHALL 通过 Grafana 的 `POST /api/ds/query` 代理查询日志，SHALL NOT 直接持有 SLS 的 AccessKey。

时间参数 SHALL 为 epoch 毫秒、UTC、半开区间 `[start, end)`；延迟统一换算为毫秒整数。

**成功判定 SHALL 依据 `frames[].data.values` 是否非空**，SHALL NOT 依据响应中的 `status` 字段
（实测成功查询也可能返回 `status: 500`）或仅依据 HTTP 状态码。

返回 SHALL 为列式结构（多字段对应多个数组），系统 SHALL 按字段名索引而非位置。

#### Scenario: 成功但 status 为 500

- **WHEN** 响应 HTTP 200、`status` 为 500、`frames[].data.values` 非空
- **THEN** 按成功处理，使用 frames 中的数据

#### Scenario: 真正的失败

- **WHEN** `frames` 为空或 `data.values` 全为空数组
- **THEN** 标记 `failed`，不得返回全零对象冒充成功

### Requirement: 进入模型的日志字段有白名单且脱敏

进入提示词的日志内容 SHALL 仅包含白名单字段：时间、节点、总耗时、上游耗时、状态码、上游状态码、方法、URI 路径。

系统 SHALL NOT 将用户代理、来源页、URI 查询串等外部可控文本送入提示词；字段值 SHALL 做控制字符清洗与
长度截断，并与指令段落显式分隔。

卡片与日志中的客户端 IP、节点内网地址 SHALL 脱敏；卡片 SHALL 使用节点别名而非内网地址。

#### Scenario: 查询串含可疑内容

- **WHEN** 日志 URI 携带查询串（可能被外部构造）
- **THEN** 该部分不进入提示词

### Requirement: 能力开关与灰度

系统 SHALL 提供全局开关，默认关闭；关闭时报警轮行为 SHALL 与变更前一致。

灰度 SHALL 以告警名白名单为单位；未在白名单的告警 SHALL 沿用变更前行为。

关闭态下，新格式轮次产生的证据包与结构化输出 SHALL NOT 写入报警会话历史，避免关闭开关后历史被污染。

#### Scenario: 开关关闭

- **WHEN** 全局开关为 false
- **THEN** 报警轮走自由分析，行为与变更前一致

#### Scenario: 不在白名单

- **WHEN** 开关开启但该告警名不在白名单
- **THEN** 该告警走变更前行为

### Requirement: 结论落库与推送同源

正常完成时，系统 SHALL 落库**渲染后的卡片文本**（与推送内容一致），并在结构化字段中附带判定档位与证据摘要。

结构校验失败时，落库状态 SHALL 为中断态，SHALL NOT 落为完成态；下一轮历史 SHALL 注入渲染文本，
SHALL NOT 注入模型原始 JSON。

#### Scenario: 正常完成

- **WHEN** 判定与渲染成功
- **THEN** 落库内容与推送到群的卡片文本一致

#### Scenario: 解析失败

- **WHEN** 模型输出无法解析
- **THEN** 落库为中断态，群侧收到兜底消息

### Requirement: 采集与解析异常必须有终态兜底

证据采集、skill 加载、占位符求值、规则反查中任一环节抛错，系统 SHALL 归一为 `failed`（原因
`internal_error`）并继续判定，SHALL NOT 让后台协程静默退出。

占位消息发出后，整段编排 SHALL 有终态兜底：无论成功、失败、超时，群侧 SHALL 至少收到一条结论或兜底消息，
SHALL NOT 出现"只有正在分析、没有下文"。

#### Scenario: fixture 或规则解析抛错

- **WHEN** 取数实现抛出未预期异常
- **THEN** 该源标记 `failed/internal_error`，判定继续，最终仍推送卡片或兜底消息
