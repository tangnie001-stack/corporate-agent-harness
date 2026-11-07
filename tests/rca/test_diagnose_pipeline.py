"""pipeline.diagnose：证据 → 判定 → 报告的组装与失败路径。"""

from src.rca.diagnose.models import RetrievalCall, RootCause
from src.rca.diagnose.pipeline import diagnose
from src.rca.sources.base import SourceEmptyError, SourceFailedError


class _TraceOk:
    def __init__(self, calls):
        self._calls = calls

    def fetch(self, trace_id):
        from src.rca.diagnose.models import Evidence

        ev = tuple(
            Evidence(f"E{i}", "trace", f"retrieve_kb#{c.call_index}", "ok")
            for i, c in enumerate(self._calls, 1)
        )
        return list(self._calls), list(ev)


class _TraceEmpty:
    def fetch(self, trace_id):
        raise SourceEmptyError("no_retrieval_snapshot")


class _TraceDown:
    def fetch(self, trace_id):
        raise SourceFailedError("langfuse_down")


class _LogDown:
    def errors_between(self, start, end):
        raise SourceFailedError("unreachable")


def _call(**kw):
    base = {
        "call_index": 1,
        "query": "q",
        "top_k": 5,
        "iteration": 1,
        "result_count": 10,
        "context_count": 5,
        "score_kind": "rerank",
        "scores": (0.7, 0.6),
        "chunk_ids": ("c1",),
    }
    base.update(kw)
    return RetrievalCall(**base)


def test_empty_snapshot_is_undetermined_not_not_retrieved():
    rep = diagnose("t", trace_source=_TraceEmpty())
    assert rep.root_cause is RootCause.UNDETERMINED
    assert "检索快照" in rep.summary


def test_trace_failure_is_undetermined():
    rep = diagnose("t", trace_source=_TraceDown())
    assert rep.root_cause is RootCause.UNDETERMINED
    assert "trace 不可用" in rep.summary


def test_log_failure_blocks_conclusion():
    rep = diagnose("t", trace_source=_TraceOk([_call()]), log_source=_LogDown())
    assert rep.root_cause is RootCause.UNDETERMINED
    assert "日志源不可用" in rep.summary


def test_truncated_case_produces_report_with_evidence():
    rep = diagnose(
        "t",
        trace_source=_TraceOk([_call(result_count=46, context_count=2)]),
        cited=True,
        has_context_budget_high=True,
    )
    assert rep.root_cause is RootCause.CONTEXT_TRUNCATED
    assert rep.evidence and rep.assertions
    assert rep.assertions[0][1] == ("E1",)


def test_report_passes_validator():
    rep = diagnose(
        "t",
        trace_source=_TraceOk([_call(scores=(0.7, 0.6))]),
        cited=False,
    )
    assert rep.root_cause is RootCause.NOT_CITED
    assert rep.assertions[0][1] == ("E1",)


class _LogRows:
    def __init__(self, rows):
        self._rows = rows

    def errors_between(self, start, end):
        return list(self._rows)


def _row(event="biz error", ts=None):
    from datetime import UTC, datetime

    from src.rca.diagnose.logs import LogRow

    return LogRow(
        ts=ts or datetime(2026, 11, 6, 3, 12, tzinfo=UTC),
        level="ERROR",
        trace_id="trace_abc",
        event=event,
        raw="2026-11-06 03:12:41.500 | ERROR | trace_abc | s1 | web | src.api.chat:h - [app] biz error",
    )


def test_5xx_gets_dev_action_and_log_evidence():
    from src.rca.diagnose.models import DiagnosisKind, Owner
    from src.rca.models import Tier

    rep = diagnose(
        "t",
        kind=DiagnosisKind.ERROR_5XX,
        trace_source=_TraceEmpty(),
        log_source=_LogRows([_row(), _row("upstream timeout")]),
    )
    assert rep.tier is Tier.NEED_ACTION
    assert rep.owner is Owner.DEV
    assert [e.id for e in rep.evidence] == ["L1", "L2"]
    assert rep.assertions and rep.assertions[0][1] == ("L1", "L2")


def test_5xx_without_error_rows_says_untraced():
    from src.rca.diagnose.models import DiagnosisKind, Owner
    from src.rca.models import Tier

    rep = diagnose(
        "t",
        kind=DiagnosisKind.ERROR_5XX,
        trace_source=_TraceEmpty(),
        log_source=_LogRows([]),
    )
    assert rep.root_cause is RootCause.UNDETERMINED
    assert "未落到应用层" in rep.summary
    assert rep.tier is Tier.NEED_ACTION
    assert rep.owner is Owner.DEV


def test_4xx_is_ops_watch_case():
    from src.rca.diagnose.models import DiagnosisKind, Owner
    from src.rca.models import Tier

    rep = diagnose("t", kind=DiagnosisKind.ERROR_4XX, log_source=_LogRows([_row()]))
    assert rep.tier is Tier.WATCH
    assert rep.owner is Owner.OPS
