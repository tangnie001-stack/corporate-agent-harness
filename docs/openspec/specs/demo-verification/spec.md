# demo-verification Specification

## Purpose
TBD - created by archiving change mvp-core-features. Update Purpose after archive.
## Requirements
### Requirement: End-to-end demo flow
The system SHALL support a documented end-to-end demo flow covering:
1. Create a knowledge base
2. Upload a test financial PDF document
3. Wait for document processing to complete
4. Ask 5 representative financial questions
5. Verify answers contain correct financial figures with citations

A demo script SHALL be maintained at `docs/demo-script.md` with exact steps and expected outputs.

#### Scenario: Demo flow is documented
- **WHEN** checking the project documentation
- **THEN** a file `docs/demo-script.md` SHALL exist with step-by-step instructions and expected results

### Requirement: RAGAS quality gate
Before marking MVP as complete, the system SHALL meet minimum RAGAS scores on the 20+ QA test pairs:
- faithfulness >= 0.85
- context_precision >= 0.80
- context_recall >= 0.70
- answer_relevancy >= 0.85

If any metric falls below the threshold, the evaluation script SHALL exit with a non-zero code and list the failing questions.

#### Scenario: Quality gate passes
- **WHEN** running `python -m src.eval_deepeval --gate`
- **THEN** the system SHALL output per-metric scores and exit 0 if all pass, or exit 1 with failing questions listed

### Requirement: RAGAS report archival for sign-off
The final MVP RAGAS evaluation report SHALL be archived at `data/reports/mvp-signoff-ragas-report.md` containing:
- Final Qwen configuration (model, chunk_size, TOP_K parameters)
- Full per-question metric table
- Aggregate scores
- Date and evaluator notes

#### Scenario: Sign-off report exists
- **WHEN** MVP is ready for sign-off
- **THEN** `data/reports/mvp-signoff-ragas-report.md` SHALL exist with required content

### Requirement: Graceful error handling in chat UI
When the RAG pipeline encounters an error (e.g., LLM timeout, retrieval failure, empty KB), the chat frontend SHALL display a user-friendly error message instead of a cryptic server error or blank response.

Error categories:
- Retrieval returned no results → "未找到相关信息，请尝试换个问法"
- LLM API timeout → "模型响应超时，请稍后重试"
- Knowledge base not found → "知识库不存在，请刷新页面"
- General server error → "服务异常，请稍后重试"

#### Scenario: Empty retrieval shows friendly message
- **WHEN** user asks a question in a KB with no matching content
- **THEN** the chat UI SHALL display "未找到相关信息，请尝试换个问法" in a styled warning message
