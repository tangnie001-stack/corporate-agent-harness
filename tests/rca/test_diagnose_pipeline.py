"""pipeline.diagnose：证据 → 判定 → 报告的组装与失败路径。"""

from dataclasses import replace

from src.rca.diagnose.history import HistoryRound
from src.rca.diagnose.models import RetrievalCall, RootCause
from src.rca.diagnose.pipeline import diagnose
from src.rca.sources.base import SourceEmptyError, SourceFailedError


class _TraceOk:
    def __init__(self, calls, cited=None):
        self._calls = calls
        self._cited = cited

    def fetch(self, trace_id):
        from src.rca.diagnose.models import Evidence, TraceFacts

        ev = tuple(
            Evidence(f"E{i}", "trace", f"retrieve_kb#{c.call_index}", "ok")
            for i, c in enumerate(self._calls, 1)
        )
        return TraceFacts(calls=tuple(self._calls), evidence=ev, cited=self._cited)


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


class _History:
    def __init__(self, rounds):
        self._rounds = rounds
        self.calls = 0

    def rounds_before(self, trace_id, *, limit):
        self.calls += 1
        return self._rounds[:limit]


_PRIOR = HistoryRound(
    offset=-1,
    trace_id="trace_prev",
    model="qwen",
    result_count=46,
    context_count=4,
    score_kind="rerank",
    context_budget_high=True,
    history_budget_exceeded=False,
)


def _prior_round(**kw):
    return replace(_PRIOR, **kw)


def test_lookback_adds_history_evidence_and_basis_rounds():
    history = _History((_prior_round(),))
    rep = diagnose(
        "t",
        trace_source=_TraceEmpty(),
        history_source=history,
        has_context_budget_high=True,
    )
    assert history.calls == 1
    assert rep.basis_rounds == 1
    assert [e.id for e in rep.evidence] == ["H1"]
    assert rep.root_cause is RootCause.CONTEXT_TRUNCATED
    assert mid_assertion_refs(rep) == ("H1",)


def test_no_lookback_when_not_triggered():
    history = _History((_prior_round(),))
    rep = diagnose("t", trace_source=_TraceOk([_call()]), history_source=history)
    assert history.calls == 0
    assert rep.basis_rounds == 0


def test_lookback_failure_does_not_block_report():
    class _Boom:
        def rounds_before(self, trace_id, *, limit):
            raise RuntimeError("langfuse down")

    rep = diagnose(
        "t",
        trace_source=_TraceOk([_call()]),
        history_source=_Boom(),
        has_context_budget_high=True,
    )
    assert rep.root_cause is RootCause.CONTEXT_TRUNCATED
    assert rep.basis_rounds == 0


def mid_assertion_refs(rep):
    """取含"前 1 轮上下文"的那条断言的引用（历史断言）。"""
    for text, refs in rep.assertions:
        if "前 1 轮" in text:
            return refs
    return ()


def test_cited_comes_from_trace_facts_when_not_passed():
    """工具不传 cited：由 trace 输出判定（无 [n] 标记 → 未引用）。"""
    rep = diagnose("t", trace_source=_TraceOk([_call(scores=(0.7, 0.6))], cited=False))
    assert rep.root_cause is RootCause.NOT_CITED


def test_probe_adds_rephrase_assertion():
    from src.rca.diagnose.probe import ProbeFacts, ProbeVariant

    probe = ProbeFacts((ProbeVariant("腾讯营业收入", 12, 0.82),))
    rep = diagnose(
        "t",
        trace_source=_TraceOk([_call(scores=(0.20, 0.19))], cited=False),
        probe=probe,
    )
    assert rep.root_cause is RootCause.LOW_RELEVANCE
    assert any("表述问题" in text for text, _refs in rep.assertions)
    assert rep.evidence[-1].id == "P1"


def test_probe_missing_coverage_assertion():
    from src.rca.diagnose.probe import ProbeFacts, ProbeVariant

    probe = ProbeFacts((ProbeVariant("腾讯营业收入", 0, 0.0),))
    rep = diagnose(
        "t",
        trace_source=_TraceOk([_call(scores=(0.20, 0.19))], cited=False),
        probe=probe,
    )
    assert any("覆盖缺失" in text for text, _refs in rep.assertions)


def test_4xx_pipeline_adds_access_evidence():
    from datetime import UTC, datetime

    from src.rca.diagnose.access import AccessRow
    from src.rca.diagnose.models import DiagnosisKind

    class _Access:
        def row_for_trace(self, trace_id, at):
            return AccessRow(
                ts=datetime.now(UTC),
                method="GET",
                uri="/api/kbs",
                status=429,
                upstream_status="429",
                request_time=0.02,
                trace_id=trace_id,
            )

    report = diagnose(
        "trace_11111111-1111-1111-1111-111111111111",
        kind=DiagnosisKind.ERROR_4XX,
        access_source=_Access(),
        now=datetime.now(UTC),
    )
    assert report.root_cause is RootCause.UNDETERMINED
    assert any(e.id == "A1" for e in report.evidence)
    assert "限流" in report.summary
    assert report.assertions
