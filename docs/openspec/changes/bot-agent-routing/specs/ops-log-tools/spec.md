## ADDED Requirements

### Requirement: 两个只读日志查询工具及其参数

系统 SHALL 为具备日志能力的 profile 提供两个工具：`sls_query_latency`（聚合指标）与 `sls_query_requests`（请求明细）。两者 SHALL 为只读，SHALL NOT 写任何外部状态。

参数约定：`uri`（接口路径，必填）；`minutes`（回看分钟数，1–360，默认 15）；`slow_ms`（慢请求阈值，默认 1000，仅聚合工具）；`limit`（返回行数上限，默认 20，仅明细工具）。时间窗由工具内部按服务端时钟换算为 epoch 半开区间，模型 SHALL NOT 自行计算时间戳。

#### Scenario: 使用默认参数

- **WHEN** 模型以 `uri` 调用聚合工具、未提供 `minutes`
- **THEN** 工具按最近 15 分钟查询，`slow_ms` 取 1000

#### Scenario: 越界参数被拒

- **WHEN** `minutes` 超出 1–360 或 `limit` 超出 1–20
- **THEN** 返回参数错误文案，不发起查询

### Requirement: 接口白名单

工具 SHALL 只允许查询配置在白名单（`RCA_TOOL_URI_WHITELIST`）中的接口，匹配方式为**精确相等**。非白名单取值 SHALL 返回结构化错误并列出可选接口，SHALL NOT 发起任何外部查询。

#### Scenario: 白名单内

- **WHEN** `uri` 精确等于白名单中的某一项
- **THEN** 正常查询并返回结果

#### Scenario: 白名单外

- **WHEN** `uri` 不在白名单（含拼写错误、带查询串、通配路径）
- **THEN** 返回错误并列出可选接口，不发起查询

### Requirement: 输出裁剪与脱敏

聚合结果 SHALL 压缩为不超过 5 行的文本摘要（样本量、分位耗时、慢请求数、节点分布、5xx 数）。

明细结果 SHALL 最多返回 20 行，每行只含时间、节点别名、总耗时、状态码、上游状态码、方法；SHALL NOT 返回客户端 IP、User-Agent、Referer、上游内网地址。节点地址 SHALL 以 `node-<序号>` 别名呈现。

#### Scenario: 明细不含敏感字段

- **WHEN** 查询明细并返回结果
- **THEN** 结果文本中不出现 IPv4 地址、User-Agent 或 Referer

#### Scenario: 行数上限

- **WHEN** 命中行数超过上限
- **THEN** 只返回按耗时倒序的前 N 行，并标注总命中数

### Requirement: 空结果与查询失败必须可区分

查询成功但窗口内无数据时，工具 SHALL 返回「无请求记录」类结果；调用失败（超时、传输错误、鉴权失败、服务端错误）时 SHALL 返回「查询失败 + 原因」。

系统 SHALL 在工具描述与提示词中明确：查询失败 SHALL NOT 被表述为「未发现异常」，也不得据此推断服务正常。

#### Scenario: 窗口无数据

- **WHEN** 查询成功且命中 0 条
- **THEN** 返回「近 N 分钟无请求记录」，语义为「查到了，确实没有请求」

#### Scenario: 查询超时

- **WHEN** 上游在超时时间内未返回
- **THEN** 返回「查询失败：超时」，不得返回空结果冒充成功

### Requirement: 单次调用超时且不自动重试

单次工具调用 SHALL 受 8 秒超时约束；超时后 SHALL 直接返回失败结果，SHALL NOT 在工具内部自动重试。

#### Scenario: 超时后立即返回

- **WHEN** 上游 8 秒内无响应
- **THEN** 工具在 8 秒后返回失败结果，本轮继续，由模型决定是否换查询
