# eval-report-model Specification

## Purpose
TBD - created by archiving change agent-state-dataclass. Update Purpose after archive.

## Requirements

### Requirement: insert_eval_report SHALL accept EvalReportEntity directly

**MODIFIED**: The method signature SHALL change from `report: dict` to `report: EvalReportEntity`.

#### Scenario: Caller constructs EvalReportEntity directly

- **WHEN** `eval_deepeval.py` calls `svc.insert_eval_report()`
- **THEN** it SHALL construct an `EvalReportEntity` instance instead of a dict
- **AND** pass it directly to the method
- **AND** the 11 `.get()` calls inside the method SHALL be removed
