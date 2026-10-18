## MODIFIED Requirements

### Requirement: Every sample carries expected_tools

每条样本 SHALL 带 `expected_tools`，值为字符串列表。

- v6 题集中的所有样本 SHALL 标注 `["retrieve_kb"]`，SHALL NOT 因 `hop_type=multi_hop` 标注重复工具。该取值是 v6 的题集事实，SHALL NOT 被评测侧当作通用默认值。
- 存储形态 SHALL 为 `list[str]`，SHALL NOT 为 DeepEval `ToolCall` 对象。
- 评测侧 SHALL 消费该字段；无该字段的旧题集 SHALL 记 `null`（N/A）。

#### Scenario: Multi-hop still expects one retrieval

- **WHEN** v6 题集中某样本 `hop_type=multi_hop`
- **THEN** 其 `expected_tools` SHALL 仍为 `["retrieve_kb"]`

#### Scenario: Evaluation consumes expected_tools

- **WHEN** 用带 `expected_tools` 的题集（如 v6）运行 `eval_deepeval`
- **THEN** `tool_correctness` SHALL 按 `expected_tools` 逐题评分；四个主指标 SHALL NOT 读取 `expected_tools`
