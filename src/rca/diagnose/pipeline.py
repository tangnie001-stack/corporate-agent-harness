"""诊断流水线：证据 → 判定 → 报告（P1 不含入口与投递）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.rca.diagnose.judge import judge
from src.rca.diagnose.models import DiagnosisReport, RootCause
from src.rca.diagnose.report import validate
from src.rca.sources.base import SourceEmptyError, SourceFailedError

# 日志窗口：与告警时间窗一致，取 trace 发生时前后各 15 分钟
_LOG_WINDOW = timedelta(minutes=15)


def diagnose(
    trace_id: str,
    *,
    cited: bool | None = None,
    has_context_budget_high: bool = False,
    trace_source: object | None = None,
    log_source: object | None = None,
    now: datetime | None = None,
) -> DiagnosisReport:
    """对单个 trace 产出诊断报告。

    Args:
        trace_id: 目标 trace。
        cited: 回答是否引用了上下文；None 表示未知（不判"未引用"）。
        has_context_budget_high: 本轮是否出现上下文预算告警。
        trace_source: 具备 `fetch(trace_id)` 的证据源；None 表示无 trace 证据。
        log_source: 具备 `errors_between(start, end)` 的证据源；None 表示不查日志。
        now: 当前时间（测试注入），默认取 UTC 当前时刻。

    Returns:
        DiagnosisReport（已过引用校验）。
    """
    if trace_source is None:
        return _undetermined(trace_id, "未提供 trace 证据源")

    try:
        calls, evidence = trace_source.fetch(trace_id)  # type: ignore[attr-defined]
    except SourceEmptyError:
        return _undetermined(
            trace_id, "无检索快照：本轮可能未检索，或该 trace 早于字段上线"
        )
    except SourceFailedError as exc:
        return _undetermined(trace_id, f"trace 不可用：{exc}")

    if log_source is not None:
        end = now or datetime.now(UTC)
        try:
            log_source.errors_between(end - _LOG_WINDOW, end)  # type: ignore[attr-defined]
        except SourceFailedError as exc:
            return _undetermined(trace_id, f"日志源不可用：{exc}")
        except SourceEmptyError:
            pass

    cause, reason = judge(
        calls, cited=cited, has_context_budget_high=has_context_budget_high
    )
    assertions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    if evidence:
        assertions = ((reason, tuple(e.id for e in evidence)),)
    report = DiagnosisReport(
        trace_id=trace_id,
        root_cause=cause,
        evidence=tuple(evidence),
        assertions=assertions,
        next_actions=(),
        basis_rounds=0,
        summary=reason,
    )
    return validate(report)


def _undetermined(trace_id: str, summary: str) -> DiagnosisReport:
    """构造"未判定"报告：无断言，原因写进 summary。"""
    return DiagnosisReport(
        trace_id=trace_id,
        root_cause=RootCause.UNDETERMINED,
        evidence=(),
        assertions=(),
        next_actions=(summary,),
        basis_rounds=0,
        summary=summary,
    )
