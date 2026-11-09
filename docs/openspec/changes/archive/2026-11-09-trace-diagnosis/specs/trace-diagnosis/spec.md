# trace-diagnosis Specification (Delta)

## ADDED Requirements

### Requirement: 单 trace 的检索事实必须可复现

系统 SHALL 仅凭 `trace_id` 还原该轮的全部检索事实，不依赖应用日志即可判定检索侧根因：

- 每次 `retrieve_kb` 调用的 `chunk_id` / `doc_id` / `page` / `score` / `order_in_prompt`；
- 融合后候选条数（`result_count`）与精排后进入 prompt 的条数（`context_count`）；
- 分数的量纲（`score_kind`：`rerank` / `fallback` / `timeout` / `none`）。

#### Scenario: 检索事实可从 trace 读出
- **WHEN** 给定一个 30 天内的 `trace_id`
- **THEN** 诊断可在不读取应用日志的前提下取出上述字段，并能重建该轮每一轮迭代的检索轨迹

#### Scenario: 分数量纲必须随分数一并读取
- **WHEN** 某次调用的 `score` 低于阈值
- **THEN** 诊断必须先读取 `score_kind`：`rerank` 时判为"知识库无相关内容"，`fallback` / `timeout` 时判为"精排降级"，两种结论不得互换

### Requirement: 根因判定只用有硬证据的类别

系统 SHALL 仅对能被证据证伪的根因给出结论，其余一律呈现证据并标注未判定。

#### Scenario: 未检索与已检索可区分
- **WHEN** 该轮 trace 中没有 `retrieve_kb` 观测
- **THEN** 判定为"未检索"，不得输出"检索未命中"

#### Scenario: 证据不足不得猜测
- **WHEN** 所需证据源返回 `empty` 或 `failed`，或只能支撑推测性结论
- **THEN** 报告输出"未判定"并列出缺失的证据项，不得给出根因

### Requirement: 报告断言必须可回溯到证据

系统 SHALL 为每条证据分配 `evidence_id`，报告中每条断言 MUST 引用至少一个存在的 `evidence_id`。

#### Scenario: 引用校验失败即降级
- **WHEN** 报告生成后存在未引用证据的断言，或引用了不存在的 `evidence_id`（含重试后仍失败）
- **THEN** 报告降级为"证据清单 + 未判定"，不得输出带结论的正文

### Requirement: 诊断只读

系统 SHALL NOT 对生产数据或配置执行任何写操作；修复动作仅以命令文本形式输出，由人执行。

#### Scenario: 不执行修复
- **WHEN** 诊断给出修复建议
- **THEN** 报告只包含可复制的命令文本，不触发重放、重入库、配置变更或任何写请求

### Requirement: 问题分型决定判定分支

系统 SHALL 由入口给出 `kind ∈ answer | 5xx | 4xx`，并据此分派判定；错误类 MUST NOT 进入检索侧根因判定（检索事实与错误条目无因果关系）。

4xx 的分类 SHALL 只依据可获得的硬证据（nginx access 日志的 `status`）；读不到 access 日志时 MUST 输出"分类待日志"，不得按错误文案猜测业务原因。

#### Scenario: 4xx 有状态码即给分类与归属
- **WHEN** 入口分型为 `4xx` 且 access 日志中存在该 trace 的记录
- **THEN** 报告给出状态码对应的分类（参数 / 鉴权 / 限流 / 资源不存在 / 请求体超限）与归属 `ops`，根因仍为"未判定"

#### Scenario: 4xx 无 access 日志不猜
- **WHEN** 入口分型为 `4xx` 但 access 日志不可用或没有该 trace 的记录
- **THEN** 报告输出"分类待 access 日志"，不得给出具体分类

### Requirement: 告警定位器产出代表 trace

系统 SHALL 在告警时间窗内从应用 ERROR 日志定位带 `trace_id` 的错误行，按（错误类型, 接口, 错误码）聚类，每类只取**最早出现**的一个代表 trace；单次告警最多诊断 3 个代表，其余只输出类别计数。

三种数据形态 MUST 显式区分，不得合并：查询成功但 0 行 → "未落到应用层"；有行但无 `trace_id` → "无请求上下文"；日志源不可达 / 超时 / 参数错误 → "日志源不可用，未判定"。

#### Scenario: 空窗口是结论而不是缺证据
- **WHEN** 窗口内查询成功且没有 ERROR 行
- **THEN** 结论为"未落到应用层"，档位按 `建议观察`，不得输出"证据不足"

#### Scenario: 日志源失败不得落进空窗口结论
- **WHEN** 日志源抛出 `SourceFailedError`
- **THEN** 结论为"日志源不可用，未判定"，不得输出"未落到应用层"

#### Scenario: 代表数与上限
- **WHEN** 窗口内有 5 类错误
- **THEN** 只诊断最早的 3 类，卡片给出其余 2 类的类别与计数
