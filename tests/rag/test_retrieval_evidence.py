"""检索链路证据（search / rerank / dedup / top-k / timeout）测试。

证据是旁路：这里同时锁定"在范围内写出哪些阶段"与"阶段键不缺失"。
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.graph.state import AgentState
from src.agents.tools import rag_tools as rag_tools_mod
from src.core.eval_evidence import (
    EvidenceRun,
    QuestionEvidence,
    current_evidence,
    parent_key,
)
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.rag import retrieval
from src.rag.retrieval import _evidence_candidate, _evidence_stage


def _chunk(
    cid: str,
    *,
    distance: float | None = None,
    lexical_score: float | None = None,
    parent: str | None = None,
) -> ChunkResult:
    """构造证据测试用的 ChunkResult（默认 metadata 含 doc/source/page）。"""
    metadata: dict = {"doc_id": "d1", "source": "a.pdf", "page": 2}
    if parent is not None:
        metadata["parent_content"] = parent
    return ChunkResult(
        id=cid,
        content=f"content {cid}",
        metadata=metadata,
        distance=distance,
        lexical_score=lexical_score,
    )


def _collector() -> tuple[EvidenceRun, QuestionEvidence]:
    """新建一轮证据与第 0 题 collector。"""
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    return run, run.new_question(0)


def test_candidate_has_identity_and_score_kind():
    chunk = ChunkResult(
        id="d1:0",
        content="x",
        metadata={"doc_id": "d1", "source": "a.pdf", "page": 2},
        distance=0.2,
        dense_rank=0,
    )
    item = _evidence_candidate(chunk, rank=0, score=0.8, score_kind="cosine_similarity")
    assert item["chunk_id"] == "d1:0"
    assert item["parent_key"] == ("d1", "d1:0")
    assert item["score_kind"] == "cosine_similarity"
    assert item["distance"] == 0.2


def test_stage_truncates_and_reports_total():
    chunks = [
        ChunkResult(id=f"d1:{i}", content="x", metadata={"doc_id": "d1"})
        for i in range(60)
    ]
    stage = _evidence_stage(chunks, "lexical", score_fn=lambda c: 1.0)
    assert stage["count"] == 50
    assert stage["total_count"] == 60
    assert stage["truncated"] is True


@pytest.mark.asyncio
async def test_search_records_hybrid_stages(monkeypatch):
    """混合路：dense / lexical / rrf 三段都写，RRF 带 rrf_score。"""
    _, collector = _collector()
    token = current_evidence.set(collector)
    try:
        vs = MagicMock()
        vs.dense_search = AsyncMock(
            return_value=[
                _chunk("d1:0", distance=0.2),
                _chunk("d1:1", distance=0.4),
            ]
        )
        vs.lexical_search = AsyncMock(return_value=[_chunk("d1:1", lexical_score=0.7)])
        monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
        await retrieval.search("q", "kb1", vector_store=vs)
    finally:
        current_evidence.reset(token)

    dense = collector.retrieval["dense"]
    assert [i["chunk_id"] for i in dense["items"]] == ["d1:0", "d1:1"]
    assert dense["items"][0]["score"] == pytest.approx(0.8)
    assert dense["items"][0]["score_kind"] == "cosine_similarity"
    assert dense["items"][0]["distance"] == 0.2

    lexical = collector.retrieval["lexical"]
    assert lexical["items"][0]["score"] == 0.7
    assert lexical["items"][0]["score_kind"] == "lexical"
    assert lexical["items"][0]["distance"] is None

    rrf = collector.retrieval["rrf"]
    assert len(rrf["items"]) == 2
    assert rrf["items"][0]["score_kind"] == "rrf"
    assert rrf["items"][0]["score"] is not None


@pytest.mark.asyncio
async def test_search_records_single_dense_and_empty_rrf(monkeypatch):
    """非混合路：只写 dense，rrf 记空阶段（不是缺键）。"""
    _, collector = _collector()
    token = current_evidence.set(collector)
    try:
        vs = MagicMock()
        vs.dense_search = AsyncMock(return_value=[_chunk("d1:0", distance=0.1)])
        monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", False)
        await retrieval.search("q", "kb1", vector_store=vs)
    finally:
        current_evidence.reset(token)

    assert collector.retrieval["dense"]["count"] == 1
    assert collector.retrieval["rrf"]["items"] == []
    assert collector.retrieval["rrf"]["count"] == 0
    assert "lexical" not in collector.retrieval


def test_rerank_records_before_after_and_dedup_topk():
    """精排链路：rerank_before / rerank_after / dedup / topk 都写且丢弃 ID 可查。"""
    _, collector = _collector()
    parent = "同一段父块正文"
    results = [
        _chunk(f"d1:{i}", distance=0.5, parent=parent if i < 2 else f"父块{i}")
        for i in range(7)
    ]

    class _Reranker:
        def rerank(self, docs, query):
            return [
                {"index": i, "relevance_score": 0.95 - i * 0.05}
                for i in range(len(docs))
            ]

    token = current_evidence.set(collector)
    try:
        contexts = retrieval.rerank_results("q", results, _Reranker())
    finally:
        current_evidence.reset(token)

    assert len(contexts) == retrieval.TOP_K_RERANK

    before = collector.retrieval["rerank_before"]
    assert before["count"] == 7
    assert before["items"][0]["score_kind"] == "rrf"

    after = collector.retrieval["rerank_after"]
    assert after["count"] == 7
    assert after["total_count"] == 7
    assert after["failed"] is False
    assert after["reason"] is None
    assert after["items"][0]["score"] == pytest.approx(0.95)
    assert after["items"][0]["score_kind"] == "rerank"

    dedup = collector.retrieval["dedup"]
    assert dedup["dropped_chunk_ids"] == ["d1:1"]
    assert "d1:1" not in dedup["kept_chunk_ids"]

    topk = collector.retrieval["topk"]
    assert topk["rank_before_topk"] == 6
    assert topk["rank_after_topk"] == retrieval.TOP_K_RERANK
    assert topk["dropped_chunk_ids"] == ["d1:6"]


def test_rerank_empty_input_records_all_stages():
    """检索为空：rerank 早退也要留下完整空阶段，不得缺段。"""
    _, collector = _collector()
    token = current_evidence.set(collector)
    try:
        contexts = retrieval.rerank_results("q", [], MagicMock())
    finally:
        current_evidence.reset(token)

    assert contexts == []
    for key in ("rerank_before", "rerank_after", "dedup", "topk"):
        assert key in collector.retrieval
    assert collector.retrieval["rerank_after"]["items"] == []
    assert collector.retrieval["dedup"]["dropped_chunk_ids"] == []


def test_rerank_fallback_marks_failed(monkeypatch):
    """精排失败回退：rerank_after 标 failed/reason，分数类型为 fallback。"""
    _, collector = _collector()
    results = [_chunk("d1:0", distance=0.2)]

    class _BrokenReranker:
        def rerank(self, docs, query):
            raise RuntimeError("rerank down")

    monkeypatch.setattr(
        retrieval.retry_policy,
        "call_with_retry",
        lambda fn, *args, **kwargs: fn(*args),
    )
    token = current_evidence.set(collector)
    try:
        retrieval.rerank_results("q", results, _BrokenReranker())
    finally:
        current_evidence.reset(token)

    after = collector.retrieval["rerank_after"]
    assert after["failed"] is True
    assert after["reason"] == "rerank_failed"
    assert after["items"][0]["score"] == pytest.approx(0.8)
    assert after["items"][0]["score_kind"] == "fallback"


@pytest.mark.asyncio
async def test_timeout_branch_records_rerank_and_dedup(monkeypatch):
    """外层超时降级：写 rerank 标记、rerank_after 快照与 dedup 丢弃 ID。"""
    _, collector = _collector()
    parent = "同一段父块正文"
    results = [
        _chunk("doc1:0", distance=0.1, parent=parent),
        _chunk("doc1:1", distance=0.2, parent=parent),
        _chunk("doc1:2", distance=0.3, parent=parent),
    ]

    async def fake_search(query, kb_id, vector_store):
        return results

    def fake_rerank(query, results, reranker):
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", fake_rerank)
    monkeypatch.setattr(rag_tools_mod, "RERANK_OUTER_TIMEOUT", 0)

    tool = rag_tools_mod.make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]
    state = AgentState.make_initial_state("s1", "kb1", "毛利率", [])

    token = current_evidence.set(collector)
    try:
        await tool.ainvoke({"query": "毛利率", "state": state})
    finally:
        current_evidence.reset(token)

    assert collector.retrieval["rerank"] == {
        "failed": True,
        "reason": "timeout",
        "score_kind": "fallback",
    }

    after = collector.retrieval["rerank_after"]
    assert after["count"] == 3
    assert after["total_count"] == 3
    assert after["failed"] is True
    assert after["reason"] == "timeout"
    assert after["items"][0]["chunk_id"] == "doc1:0"
    assert after["items"][0]["score"] == pytest.approx(0.9)
    assert after["items"][0]["score_kind"] == "fallback"
    assert after["items"][0]["distance"] is None
    assert after["items"][0]["parent_key"] == parent_key("d1", "doc1:0", parent)

    dedup = collector.retrieval["dedup"]
    assert dedup["dropped_chunk_ids"] == ["doc1:1", "doc1:2"]
    assert dedup["kept_chunk_ids"] == ["doc1:0"]
