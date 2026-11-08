"""diagnose_trace 工具：入口校验、失败文案、卡片返回、落库。"""

from dataclasses import replace

import pytest

from src.agents.tools import diagnose_tools
from src.config import settings
from src.rca.diagnose.history import HistoryRound
from src.rca.diagnose.models import Evidence, RetrievalCall, TraceFacts
from src.rca.sources.base import SourceEmptyError, SourceFailedError

pytestmark = pytest.mark.asyncio

_VALID = "trace_abcdefab-1234-5678-9abc-def012345678"


class _TraceOk:
    def __init__(self, calls):
        self._calls = calls

    def fetch(self, trace_id):
        ev = tuple(
            Evidence(f"E{i}", "trace", f"retrieve_kb#{c.call_index}", "result=30")
            for i, c in enumerate(self._calls, 1)
        )
        return TraceFacts(calls=tuple(self._calls), evidence=ev, cited=None)


class _TraceEmpty:
    def fetch(self, trace_id):
        raise SourceEmptyError("no_retrieval_snapshot")


class _TraceNotFound:
    def fetch(self, trace_id):
        raise SourceFailedError("trace_fetch_failed:NotFoundError")


class _Repo:
    def __init__(self):
        self.saved = []

    async def save(self, report, *, env, trigger="manual", cost_ms=0):
        self.saved.append((report, env, cost_ms))
        return "diag_test_id"


_BASE_CALL = RetrievalCall(
    call_index=1,
    query="q",
    top_k=5,
    iteration=1,
    result_count=30,
    context_count=5,
    score_kind="rerank",
    scores=(0.20, 0.19),
    chunk_ids=("c1",),
)


def _call(**kw):
    return replace(_BASE_CALL, **kw)


def _setup(trace_source, repo=None):
    diagnose_tools.configure(trace_source=trace_source, log_source=None, repo=repo)


async def test_rejects_bad_trace_id_format():
    _setup(_TraceOk([_call()]))
    text = await diagnose_tools.diagnose_trace.ainvoke({"trace_id": "abc"})
    assert "格式" in text


async def test_rejects_unknown_kind():
    _setup(_TraceOk([_call()]))
    text = await diagnose_tools.diagnose_trace.ainvoke(
        {"trace_id": _VALID, "kind": "timeout"}
    )
    assert "answer / 5xx / 4xx" in text


async def test_not_found_trace_has_own_text():
    _setup(_TraceNotFound())
    text = await diagnose_tools.diagnose_trace.ainvoke({"trace_id": _VALID})
    assert "未找到该 trace" in text


async def test_success_returns_card_and_persists(monkeypatch):
    repo = _Repo()
    _setup(_TraceOk([_call()]), repo=repo)
    monkeypatch.setattr(settings, "DIAG_DETAIL_BASE_URL", "http://diag.local")
    text = await diagnose_tools.diagnose_trace.ainvoke(
        {"trace_id": "trace_abcdefab-1234-5678-9abc-def012345678"}
    )
    assert "诊断 · 答错/答不准" in text
    assert "档位：需要处理" in text
    assert "http://diag.local/diagnose.html?diag_id=diag_test_id" in text
    assert len(repo.saved) == 1
    assert repo.saved[0][0].tier.value == "需要处理"


class _HistoryOne:
    def __init__(self):
        self.called = 0

    def rounds_before(self, trace_id, *, limit):
        self.called += 1
        return (
            HistoryRound(
                offset=-1,
                trace_id="trace_prev",
                model="qwen",
                result_count=46,
                context_count=4,
                score_kind="rerank",
                context_budget_high=True,
                history_budget_exceeded=False,
            ),
        )


async def test_followup_question_triggers_lookback():
    """追问（含指代）且本轮无检索 → 追溯上一轮，卡片标注轮次。"""
    history = _HistoryOne()
    diagnose_tools.configure(
        trace_source=_TraceEmpty(),
        log_source=None,
        repo=_Repo(),
        history_source=history,
    )
    text = await diagnose_tools.diagnose_trace.ainvoke(
        {"trace_id": _VALID, "kind": "answer", "question": "那它去年呢"}
    )
    assert history.called == 1
    assert "前 1 轮" in text
    assert "上一轮召回 46 条仅 4 条进入上下文" in text
