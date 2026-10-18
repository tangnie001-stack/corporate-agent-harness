import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from src.core.eval_evidence import EvidenceRun, current_evidence
from src.infra.db.vector_store import VectorStore
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


@pytest.mark.asyncio
async def test_retrieve_kb_records_call_and_global_order(monkeypatch):
    from src.agents.tools import rag_tools

    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    collector = run.new_question(0)
    token = current_evidence.set(collector)
    request_ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx_token = current_request_ctx.set(request_ctx)

    async def fake_search(query, kb_id, vector_store):
        return [SimpleNamespace(id="raw:0"), SimpleNamespace(id="raw:1")]

    def fake_rerank(query, results, reranker):
        return [
            RAGContext(
                content="c1",
                source="a.pdf",
                page=1,
                doc_id="d1",
                chunk_id="d1:0",
                score=0.9,
            )
        ]

    monkeypatch.setattr(rag_tools.retrieval, "search", fake_search)
    monkeypatch.setattr(rag_tools.retrieval, "rerank_results", fake_rerank)
    tools = rag_tools.make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
        include_ask_user=False,
    )
    retrieve = next(t for t in tools if t.name == "retrieve_kb")
    try:
        await retrieve.ainvoke({"query": "q", "top_k": 1})
        assert collector.query["tool_queries"] == [{"call_index": 1, "query": "q"}]
        assert collector.tool_calls[0]["result_count"] == 2
        assert collector.tool_calls[0]["context_count"] == 1
        assert collector.retrieval["final_contexts"][0]["chunk_id"] == "d1:0"
        assert collector.retrieval["final_contexts"][0]["order_in_prompt"] == 1
    finally:
        current_request_ctx.reset(ctx_token)
        current_evidence.reset(token)


@pytest.mark.asyncio
async def test_retrieve_kb_order_and_call_index_continue_across_calls(monkeypatch):
    """同一题内多次调用：call_index 与 order_in_prompt 接续，顶层视图按调用顺序合并。"""
    from src.agents.tools import rag_tools

    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    collector = run.new_question(0)
    token = current_evidence.set(collector)
    request_ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx_token = current_request_ctx.set(request_ctx)

    async def fake_search(query, kb_id, vector_store):
        return [SimpleNamespace(id="raw:0")]

    def fake_rerank(query, results, reranker):
        return [
            RAGContext(
                content=f"c-{query}",
                source="a.pdf",
                page=1,
                doc_id="d1",
                chunk_id=f"d1:{query}",
                score=0.9,
            )
        ]

    monkeypatch.setattr(rag_tools.retrieval, "search", fake_search)
    monkeypatch.setattr(rag_tools.retrieval, "rerank_results", fake_rerank)
    tools = rag_tools.make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
        include_ask_user=False,
    )
    retrieve = next(t for t in tools if t.name == "retrieve_kb")
    try:
        await retrieve.ainvoke({"query": "q1", "top_k": 1})
        await retrieve.ainvoke({"query": "q2", "top_k": 1})
        assert [c["call_index"] for c in collector.tool_calls] == [1, 2]
        assert collector.query["tool_queries"] == [
            {"call_index": 1, "query": "q1"},
            {"call_index": 2, "query": "q2"},
        ]
        orders = [c["order_in_prompt"] for c in collector.retrieval["final_contexts"]]
        assert orders == [1, 2]
        chunk_ids = [c["chunk_id"] for c in collector.retrieval["final_contexts"]]
        assert chunk_ids == ["d1:q1", "d1:q2"]
    finally:
        current_request_ctx.reset(ctx_token)
        current_evidence.reset(token)


@pytest.mark.asyncio
async def test_retrieve_kb_parallel_calls_keep_distinct_call_index(monkeypatch):
    """并行工具调用：call_index 在追加前原子取号，两次调用不会记录同一个序号。"""
    from src.agents.tools import rag_tools

    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    collector = run.new_question(0)
    token = current_evidence.set(collector)
    request_ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx_token = current_request_ctx.set(request_ctx)

    async def fake_search(query, kb_id, vector_store):
        await asyncio.sleep(0)
        return [SimpleNamespace(id=f"raw:{query}")]

    def fake_rerank(query, results, reranker):
        return [
            RAGContext(
                content=f"c-{query}",
                source="a.pdf",
                page=1,
                doc_id="d1",
                chunk_id=f"d1:{query}",
                score=0.9,
            )
        ]

    monkeypatch.setattr(rag_tools.retrieval, "search", fake_search)
    monkeypatch.setattr(rag_tools.retrieval, "rerank_results", fake_rerank)
    tools = rag_tools.make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
        include_ask_user=False,
    )
    retrieve = next(t for t in tools if t.name == "retrieve_kb")
    try:
        await asyncio.gather(
            retrieve.ainvoke({"query": "q1", "top_k": 1}),
            retrieve.ainvoke({"query": "q2", "top_k": 1}),
        )
        assert sorted(c["call_index"] for c in collector.tool_calls) == [1, 2]
        assert sorted(q["call_index"] for q in collector.query["tool_queries"]) == [
            1,
            2,
        ]
        assert {q["query"] for q in collector.query["tool_queries"]} == {"q1", "q2"}
    finally:
        current_request_ctx.reset(ctx_token)
        current_evidence.reset(token)
