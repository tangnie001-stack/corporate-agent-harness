# evaluation-pipeline Specification

## Purpose
TBD - created by archiving change mvp-core-features. Update Purpose after archive.
## Requirements
### Requirement: QA test pair coverage
The system SHALL maintain a minimum of 20 QA test pairs covering at least two distinct financial documents (e.g., 贵州茅台 2024 年报 and 厦门灿坤 2019 年报).

Test questions SHALL cover the following dimensions:
- Revenue and growth metrics
- Per-share earnings and shareholder structure
- Core business analysis
- Regional/segment performance
- Company basic information

#### Scenario: QA pair count meets minimum
- **WHEN** running `python -m src.eval_ragas --check`
- **THEN** the system SHALL report the total QA pair count and warn if below 20

### Requirement: Chunk size comparison
The system SHALL support automated comparison of different chunk sizes (512, 768, 1024 tokens) across all QA test pairs.

For each chunk size configuration, the system SHALL compute:
- faithfulness
- answer_relevancy
- context_precision
- context_recall

#### Scenario: Run chunk size comparison
- **WHEN** user runs `python -m scripts.compare_chunk`
- **THEN** the system SHALL generate a Markdown report at `data/reports/chunk_comparison.md` with all four metrics per chunk size

### Requirement: Evaluation report archival
All RAGAS evaluation results SHALL be saved to `data/reports/` with timestamped filenames in format `ragas_eval_<date>_<chunk_size>.csv`.

A summary Markdown report SHALL be generated after each evaluation run listing:
- Date and configuration
- QA pair count
- Per-question metric scores
- Aggregate metric averages

#### Scenario: Evaluation saves CSV and Markdown
- **WHEN** running `python -m src.eval_ragas`
- **THEN** a CSV file and a Markdown summary SHALL be created in `data/reports/`

### Requirement: Evaluation passes without crash on empty KB
The evaluation system SHALL handle the case where the knowledge base has no documents gracefully, logging a clear error message and exiting with a non-zero exit code.

#### Scenario: Empty KB evaluation
- **WHEN** running evaluation against a KB with no documents
- **THEN** the system SHALL log "Knowledge base is empty" and exit with code 1

### Requirement: NLI context uses shared entity rendering

The RAGAS evaluation SHALL use `RAGContext.to_prompt_text()` (the same rendering as production) for the NLI context, so entity metadata (company / report_period / sec_code / person / currency / report_type) present in chunk metadata SHALL be visible to the faithfulness NLI check.

#### Scenario: Faithfulness context includes time anchor
- **WHEN** a table chunk has `entities={company: 东软集团, report_period: 2025年第一季度}` and the statement references "2025年第一季度"
- **THEN** the NLI context SHALL include the `report_period` entity, giving the statement an anchor to verify against

#### Scenario: Evaluation output unchanged for empty entities
- **WHEN** evaluation runs against a KB whose chunks have no entity metadata
- **THEN** the NLI context SHALL match the pre-change format, and the evaluation SHALL still produce a report without crashing

### Requirement: Current date in evaluation context

system prompt SHALL 在**生产路径与评估路径**下都包含当前日期（例："今天是 2026年8月9日"）。

日期注入 SHALL 由 system prompt 组装器的**唯一注入点**承担：`_with_current_date`，在段拼装完成、产出 system 消息之前应用；按 `Asia/Shanghai` 计算，且幂等（日期行已存在时不重复追加）。SHALL NOT 由 `PromptManager` 另行追加 —— 经典 RAG 与 agent 两条路径经同一组装入口（见 `<prompt-composition>`「经典 RAG 与 agent 两条路径共用同一套段」），日期因此只出现一次。

#### Scenario: Current date present in system prompt
- **WHEN** a generation or evaluation run assembles the system prompt
- **THEN** the resulting system message SHALL contain today's date, enabling relative-time queries ("本报告期", "今年") to be anchored
