## ADDED Requirements

### Requirement: 评测结果回写为 trace 分数

RAGAS 评测 SHALL 把每题的各指标分数回写到其对应的 trace 上，使评测结果可在 Langfuse 中按 trace 查看与筛选。

回写 SHALL 沿用既有接线口径：客户端实例统一取 `langfuse_context.client_instance`，**SHALL NOT** 新建客户端实例；当 `LANGFUSE_ENABLE` 关闭时，回写 SHALL 被短路且不产生任何出网写入。

#### Scenario: Scores readable on the trace

- **WHEN** 一次评测运行完成
- **THEN** 每题对应的 trace 上出现以指标名命名的分数

#### Scenario: No writes when tracing disabled

- **WHEN** `LANGFUSE_ENABLE` 为关闭状态
- **THEN** 评测不向 Langfuse 写入任何分数，也不产生出网请求
