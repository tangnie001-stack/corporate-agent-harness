from src.infra.db.vector_store.types import ChunkResult
from src.rag.fusion import rrf_fusion


def _chunk(cid: str) -> ChunkResult:
    return ChunkResult(id=cid, content=cid, metadata={"doc_id": "d1"})


def test_rrf_fusion_sets_score_and_keeps_ranks():
    dense = [_chunk("a"), _chunk("b")]
    sparse = [_chunk("b"), _chunk("c")]
    out = rrf_fusion(dense, sparse, k=60, top_n=10)
    by_id = {c.id: c for c in out}
    score_b = by_id["b"].rrf_score
    score_a = by_id["a"].rrf_score
    assert score_b is not None
    assert score_a is not None
    assert score_b > score_a
    assert by_id["a"].dense_rank == 0
    assert by_id["b"].dense_rank == 1
    assert by_id["b"].sparse_rank == 0
    assert by_id["c"].sparse_rank == 1
