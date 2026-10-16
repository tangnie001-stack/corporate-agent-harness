"""`dense_search` 的 embedding 单阶段耗时（P4 / tasks 4.1）。

**故意 DB-free**：`PgVectorStore.__init__(chunk_repo, embed_fn)` 两个依赖都可注入
（已核实：`pg_store.py:67-86`），故用假 repo + 假 embed_fn 即可，使该用例在只跑
非 DB 单测的环境（worktree，批量跑里 `--ignore=tests/infra/db`）也能**显式**执行。
"""

import time

import pytest

from src.infra.db.vector_store import pg_store as pg_store_mod


class _FakeEmbedder:
    """假向量化器：睡一小会儿，便于断言耗时字段 ≥ 1ms。"""

    def embed_query(self, text: str) -> list[float]:
        time.sleep(0.01)
        return [0.0] * 4


class _FakeRepo:
    """假 repo：dense 检索返回空结果（本用例只关心计时字段）。"""

    async def search_dense(self, kb_id: str, vec: list[float], k: int):
        return []


@pytest.mark.asyncio
async def test_dense_search_reports_embedding_stage_latency(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        pg_store_mod.core_logging,
        "log_event",
        lambda event, **fields: captured.append(fields),
    )
    store = pg_store_mod.PgVectorStore(chunk_repo=_FakeRepo(), embed_fn=_FakeEmbedder())  # type: ignore[reportArgumentType]

    results = await store.dense_search("kb1", "资产负债率", 3)

    assert results == []
    assert captured, "dense_search 必须落一条 search result 事件"
    assert "embedding_ms" in captured[0], "search result 未带 embedding_ms"
    assert isinstance(captured[0]["embedding_ms"], int)
    assert captured[0]["embedding_ms"] >= 1, "假向量化器睡了 10ms，耗时字段应 ≥ 1ms"
