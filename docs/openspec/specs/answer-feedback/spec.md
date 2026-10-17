# answer-feedback Specification

## Purpose
TBD - created by syncing change agentic-clarification. Update Purpose after sync.
## Requirements

### Requirement: 答案反馈接口

系统 SHALL 提供 `POST /api/feedback` 接口，接收 `{session_id, message_index, rating, comment?}`（rating 为 positive/negative）；前端在每条助手回答下展示 👍/👎 按钮（可选用后附原因）；反馈落库，供评测与数据分析消费。反馈 SHALL 不进入模型上下文，不影响后续回答。

#### Scenario: 用户反馈正面
- **WHEN** 用户在回答下点击 👍 并可选填原因
- **THEN** 反馈记录落库，含 session_id/消息定位/rating/comment

#### Scenario: 反馈不进入上下文
- **WHEN** 反馈已提交
- **THEN** 反馈不注入后续轮次的模型消息

### Requirement: 反馈与历史关联

系统 SHALL 将反馈关联到具体消息（message_index 对应会话历史中的 assistant 消息），使 eval 能定位到被反馈的答案与当时上下文（trace_id）。

#### Scenario: 反馈可回溯
- **WHEN** eval 消费反馈数据
- **THEN** 可定位 session_id + message_index + trace_id，还原被反馈答案的生成链路
