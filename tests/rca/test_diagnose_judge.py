"""诊断 judge：5 类根因的确定性判定（与 alert-triage 的延迟判定无关）。"""

from src.rca.diagnose.judge import judge
from src.rca.diagnose.models import RetrievalCall, RootCause


def call(**kw):
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


def test_no_call_means_not_retrieved():
    cause, reason = judge([], cited=None, has_context_budget_high=False)
    assert cause is RootCause.NOT_RETRIEVED
    assert "检索" in reason


def test_low_scores_flat_means_low_relevance():
    c = call(scores=(0.20, 0.195), result_count=30, context_count=5)
    assert (
        judge([c], cited=False, has_context_budget_high=False)[0]
        is RootCause.LOW_RELEVANCE
    )


def test_single_high_score_is_not_low_relevance():
    c = call(scores=(0.72, 0.20, 0.19))
    assert (
        judge([c], cited=False, has_context_budget_high=False)[0]
        is not RootCause.LOW_RELEVANCE
    )


def test_fallback_scores_never_low_relevance():
    c = call(scores=(0.10, 0.09), score_kind="fallback")
    cause, reason = judge([c], cited=False, has_context_budget_high=False)
    assert cause is not RootCause.LOW_RELEVANCE
    assert "量纲" in reason


def test_truncated_when_budget_high_and_fewer_contexts():
    c = call(result_count=46, context_count=2, scores=(0.7, 0.6))
    assert (
        judge([c], cited=True, has_context_budget_high=True)[0]
        is RootCause.CONTEXT_TRUNCATED
    )


def test_not_cited_when_high_score_and_no_citation():
    c = call(scores=(0.7, 0.6))
    assert (
        judge([c], cited=False, has_context_budget_high=False)[0] is RootCause.NOT_CITED
    )


def test_unknown_citation_is_undetermined():
    c = call(scores=(0.7, 0.6))
    cause, reason = judge([c], cited=None, has_context_budget_high=False)
    assert cause is RootCause.UNDETERMINED
    assert "引用" in reason
