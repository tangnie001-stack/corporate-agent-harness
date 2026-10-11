## MODIFIED Requirements

### Requirement: Current date in evaluation context

system prompt SHALL 在**生产路径与评估路径**下都包含当前日期（例："今天是 2026年8月9日"）。

日期注入 SHALL 由 system prompt 组装器的**唯一注入点**承担：`_with_current_date`，在段拼装完成、产出 system 消息之前应用；按 `Asia/Shanghai` 计算，且幂等（日期行已存在时不重复追加）。SHALL NOT 由 `PromptManager` 另行追加 —— 经典 RAG 与 agent 两条路径经同一组装入口（见 `<prompt-composition>`「经典 RAG 与 agent 两条路径共用同一套段」），日期因此只出现一次。

#### Scenario: Current date present in system prompt
- **WHEN** a generation or evaluation run assembles the system prompt
- **THEN** the resulting system message SHALL contain today's date, enabling relative-time queries ("本报告期", "今年") to be anchored
