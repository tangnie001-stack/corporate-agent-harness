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
