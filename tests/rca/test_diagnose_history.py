"""D3 追溯：触发条件与轮次取数。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.rca.diagnose.history import HistorySource, should_lookback
from src.rca.diagnose.logs import BudgetFlags
from src.rca.diagnose.models import RetrievalCall


@dataclass
class _Item:
    id: str
    timestamp: datetime
    metadata: dict


def _call(result_count=10):
    return RetrievalCall(
        call_index=1,
        query="q",
        top_k=5,
        iteration=1,
        result_count=result_count,
        context_count=4,
        score_kind="rerank",
        scores=(0.5,),
        chunk_ids=("c1",),
    )


def test_lookback_on_budget_flags():
    calls = [_call()]
    assert should_lookback(
        calls,
        has_context_budget_high=True,
        has_history_budget_exceeded=False,
        question="",
    )
    assert should_lookback(
        calls,
        has_context_budget_high=False,
        has_history_budget_exceeded=True,
        question="",
    )


def test_lookback_on_zero_hits_with_anaphora():
    calls = [_call(result_count=0)]
    assert should_lookback(
        calls,
        has_context_budget_high=False,
        has_history_budget_exceeded=False,
        question="那它去年呢",
    )


def test_no_lookback_for_independent_question():
    calls = [_call(result_count=0)]
    assert not should_lookback(
        calls,
        has_context_budget_high=False,
        has_history_budget_exceeded=False,
        question="腾讯2024年营业收入",
    )


def test_zero_hits_includes_no_retrieval_call():
    """追问轮常常完全没有 retrieve_kb 观测：同样属"零召回"，含指代时追溯。"""
    assert should_lookback(
        [],
        has_context_budget_high=False,
        has_history_budget_exceeded=False,
        question="那它呢",
    )
    assert not should_lookback(
        [],
        has_context_budget_high=False,
        has_history_budget_exceeded=False,
        question="",
    )


def _item(trace_id, ts, result_count, context_count):
    return _Item(
        id=trace_id,
        timestamp=ts,
        metadata={
            "model": "qwen-max",
            "retrieval_contexts": [
                {
                    "call_index": 1,
                    "result_count": result_count,
                    "context_count": context_count,
                    "score_kind": "rerank",
                    "contexts": [],
                }
            ],
        },
    )


def test_rounds_before_takes_last_two_ascending():
    now = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
    items = [
        _item("trace_1", now - timedelta(minutes=30), 40, 4),
        _item("trace_2", now - timedelta(minutes=20), 41, 4),
        _item("trace_3", now - timedelta(minutes=10), 46, 2),
    ]
    source = HistorySource(
        session_fetcher=lambda tid: (now, items),
        budget_reader=lambda tid, ts: BudgetFlags(
            context_budget_high=(tid == "trace_3")
        ),
    )
    rounds = source.rounds_before("trace_x", limit=2)
    assert [r.offset for r in rounds] == [-2, -1]
    assert rounds[-1].trace_id == "trace_3"
    assert rounds[-1].result_count == 46
    assert rounds[-1].context_count == 2
    assert rounds[-1].model == "qwen-max"
    assert rounds[-1].truncated is True
    assert rounds[0].truncated is False


def test_rounds_before_empty_when_no_history():
    source = HistorySource(
        session_fetcher=lambda tid: (None, []),
        budget_reader=lambda tid, ts: BudgetFlags(),
    )
    assert source.rounds_before("trace_x", limit=2) == ()


def test_round_without_snapshot_counts_as_zero():
    now = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
    items = [_Item(id="trace_1", timestamp=now, metadata={})]
    source = HistorySource(
        session_fetcher=lambda tid: (now, items),
        budget_reader=lambda tid, ts: BudgetFlags(),
    )
    rounds = source.rounds_before("trace_x", limit=2)
    assert rounds[0].result_count == 0
    assert rounds[0].score_kind == "none"
    assert rounds[0].model == ""
