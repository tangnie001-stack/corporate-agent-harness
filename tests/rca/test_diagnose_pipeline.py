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
