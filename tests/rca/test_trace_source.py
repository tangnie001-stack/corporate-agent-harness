"""TraceSource：从 trace metadata 还原检索事实。"""

from types import SimpleNamespace

import pytest

from src.rca.diagnose.sources import TraceSource
from src.rca.sources.base import SourceEmptyError, SourceFailedError


def _trace(contexts, output=None):
    return SimpleNamespace(
        id="trace_x", metadata={"retrieval_contexts": contexts}, output=output
    )


def test_parses_counts_scores_and_kind():
    src = TraceSource(
        fetcher=lambda tid: _trace(
            [
                {
                    "call_index": 1,
                    "query": "腾讯2024年营业收入",
                    "top_k": 5,
                    "iteration": 1,
                    "result_count": 46,
                    "context_count": 2,
                    "score_kind": "rerank",
                    "contexts": [
                        {"chunk_id": "c1", "score": 0.76},
                        {"chunk_id": "c2", "score": 0.20},
                    ],
                }
            ]
        )
    )
    facts = src.fetch("trace_x")
    calls, evidence = facts.calls, facts.evidence
    assert len(calls) == 1
    call = calls[0]
    assert (call.result_count, call.context_count, call.score_kind) == (46, 2, "rerank")
    assert call.scores == (0.76, 0.2)
    assert call.chunk_ids == ("c1", "c2")
    assert evidence[0].id == "E1"
    assert evidence[0].locator == "retrieve_kb#1"


def test_empty_snapshot_raises_empty():
    src = TraceSource(fetcher=lambda tid: _trace([]))
    with pytest.raises(SourceEmptyError):
        src.fetch("trace_x")


def test_fetcher_error_maps_to_failed():
    def boom(tid):
        raise RuntimeError("langfuse down")

    src = TraceSource(fetcher=boom)
    with pytest.raises(SourceFailedError):
        src.fetch("trace_x")


def test_missing_optional_fields_default_safely():
    src = TraceSource(
        fetcher=lambda tid: _trace(
            [{"call_index": 2, "contexts": [{"chunk_id": "c9"}]}]
        )
    )
    calls = src.fetch("trace_x").calls
    assert calls[0].result_count == 0
    assert calls[0].score_kind == "none"
    assert calls[0].scores == (0.0,)


def test_detects_citation_marker():
    src = TraceSource(
        fetcher=lambda tid: _trace(
            [{"call_index": 1, "result_count": 3, "context_count": 1, "contexts": []}],
            output="总收入 6,603 亿元 [3]。",
        )
    )
    assert src.fetch("trace_x").cited is True


def test_no_marker_is_not_cited():
    src = TraceSource(
        fetcher=lambda tid: _trace(
            [{"call_index": 1, "result_count": 3, "context_count": 1, "contexts": []}],
            output="总收入 6,603 亿元。",
        )
    )
    assert src.fetch("trace_x").cited is False


def test_missing_output_is_unknown():
    src = TraceSource(
        fetcher=lambda tid: _trace(
            [{"call_index": 1, "result_count": 3, "context_count": 1, "contexts": []}]
        )
    )
    assert src.fetch("trace_x").cited is None
