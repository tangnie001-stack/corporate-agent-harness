"""retrieve_kb 精排超时降级路径的内容级去重测试。

降级路径（`except TimeoutError` 分支）绕过 rerank_results、按检索原始顺序手工构造
RAGContext，改后同样调用 `_dedup_by_parent` 并落 `dedup done`。本测试通过把
RERANK_OUTER_TIMEOUT 置 0 强制 `asyncio.wait_for` 立即抛 TimeoutError 进入该分支，锁定
"同父块只留最高分 + dedup done"。
"""

from typing import cast

import pytest

from src.agents.graph.state import AgentState
from src.agents.tools import rag_tools as rag_tools_mod
from src.core.log_events import Event
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.rag import retrieval


@pytest.mark.asyncio
async def test_timeout_fallback_contexts_are_deduped(monkeypatch):
    """降级路径同样按父块去重：同父块 3 条只留 score 最高 1 条，并落 dedup done。"""
    parent = "同一段父块正文"
    results = [
        ChunkResult(
            id="doc1:0",
            content="子块0",
            distance=0.1,
            metadata={
                "source": "a.pdf",
                "page": 1,
                "doc_id": "d1",
                "parent_content": parent,
            },
        ),
        ChunkResult(
            id="doc1:1",
            content="子块1",
            distance=0.2,
            metadata={
                "source": "a.pdf",
                "page": 1,
                "doc_id": "d1",
                "parent_content": parent,
            },
        ),
        ChunkResult(
            id="doc1:2",
            content="子块2",
            distance=0.3,
            metadata={
                "source": "a.pdf",
                "page": 1,
                "doc_id": "d1",
                "parent_content": parent,
            },
        ),
    ]

    async def fake_search(query, kb_id, vector_store):
        """mock search：返回同父块 3 条 ChunkResult（distance 越小越相似）。"""
        return results

    def fake_rerank(query, results, reranker):
        """mock rerank_results：立即返回空（RERANK_OUTER_TIMEOUT=0 使其实际不被消费）。"""
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", fake_rerank)
    monkeypatch.setattr(rag_tools_mod, "RERANK_OUTER_TIMEOUT", 0)

    logged: list[dict] = []

    def fake_log_event(event, **fields):
        """mock log_event：捕获事件名与字段。"""
        logged.append({"event": event, **fields})

    monkeypatch.setattr(rag_tools_mod.core_logging, "log_event", fake_log_event)

    tool = rag_tools_mod.make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]

    state = AgentState.make_initial_state("s1", "kb1", "毛利率", [])
    out = await tool.ainvoke({"query": "毛利率", "state": state})

    # 同父块 3 条折叠为 1 条（score 最高 = distance 最小 = doc1:0）
    assert "[1]" in out
    assert "[2]" not in out
    assert parent in out

    dedup = [e for e in logged if e["event"] == Event.DEDUP_DONE]
    assert len(dedup) == 1
    assert dedup[0]["dropped"] == 2
    assert dedup[0]["kept"] == 1
