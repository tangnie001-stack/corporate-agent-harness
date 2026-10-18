"""RRF（Reciprocal Rank Fusion）融合 —— 纯函数，无数据库、无 IO。

融合留在应用层：pgvector 本体不提供融合能力，ParadeDB 到 0.25.9 仍把
Native Hybrid Search 标为未发布；把 RRF 写进 SQL 等于把手写公式搬进字符串
并自担 tiebreaker 确定性，收益为零、代价明确（见 design.md D2）。

**两路等权**：同一个 `1/(k+rank+1)` 公式作用于两路，不引入权重参数 ——
加权重会改变融合输出，从而污染"存储替换不改变行为"的验证框架。
"""

from src.infra.db.vector_store.types import ChunkResult


def _merge_path_ranks(
    existing: ChunkResult | None,
    incoming: ChunkResult,
    *,
    dense_rank: int | None = None,
    sparse_rank: int | None = None,
) -> ChunkResult:
    """把一路的名次合并进已有结果。

    位置名次兜底（生产者未填时用融合时的位置），生产者已填的值优先；
    同一 id 出现在另一路时，把那一侧的排名携带过来 —— 融合只按 RRF 重排，
    不得抹掉任一路的排名（来源可辨）。
    """
    if existing is None:
        if dense_rank is not None and incoming.dense_rank is None:
            incoming.dense_rank = dense_rank
        if sparse_rank is not None and incoming.sparse_rank is None:
            incoming.sparse_rank = sparse_rank
        return incoming
    if dense_rank is not None and existing.dense_rank is None:
        existing.dense_rank = dense_rank
    if sparse_rank is not None and existing.sparse_rank is None:
        existing.sparse_rank = sparse_rank
    if existing.dense_rank is None:
        existing.dense_rank = incoming.dense_rank
    if existing.sparse_rank is None:
        existing.sparse_rank = incoming.sparse_rank
    return existing


def rrf_fusion(
    dense: list[ChunkResult],
    sparse: list[ChunkResult],
    k: int,
    top_n: int,
) -> list[ChunkResult]:
    """RRF 融合 dense 语义检索与词法检索结果。

    融合只按 RRF 得分重排；每个结果的 dense_rank / sparse_rank 按路保留，
    某条结果未出现在某一路时该路排名为 None。

    Args:
        dense: dense（向量）路结果，已按余弦距离升序
        sparse: 词法路结果，已按词法得分降序
        k: RRF 平滑常数（settings.RRF_K），控制排名权重衰减速度
        top_n: 融合后保留条数（settings.RRF_TOP_N）

    Returns:
        融合结果列表，按 RRF 得分降序，长度不超过 top_n
    """
    scores: dict[str, float] = {}
    merged: dict[str, ChunkResult] = {}

    for rank, doc in enumerate(dense):
        scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
        merged[doc.id] = _merge_path_ranks(merged.get(doc.id), doc, dense_rank=rank)

    for rank, doc in enumerate(sparse):
        scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
        merged[doc.id] = _merge_path_ranks(merged.get(doc.id), doc, sparse_rank=rank)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    result: list[ChunkResult] = []
    for doc_id, score in ranked[:top_n]:
        item = merged[doc_id]
        item.rrf_score = score
        result.append(item)
    return result


def rrf_fusion_multi(
    results_groups: list[list[ChunkResult]],
    k: int,
    top_n: int,
) -> list[ChunkResult]:
    """任意路 RRF 融合多组检索结果。

    每路按排名贡献 1/(k+rank+1)，跨路累加后按得分降序取 top_n。
    此函数不区分 dense / 词法，排名按融合时的位置兜底写入 dense_rank，
    sparse_rank 保持生产者已填的值（缺省为 None）。

    Args:
        results_groups: 多组检索结果（每组一个查询的 dense 或词法结果）
        k: RRF 平滑常数（settings.RRF_K）
        top_n: 融合后保留条数（settings.RRF_TOP_N）

    Returns:
        融合结果列表，按 RRF 得分降序，长度不超过 top_n
    """
    scores: dict[str, float] = {}
    merged: dict[str, ChunkResult] = {}
    for group in results_groups:
        for rank, doc in enumerate(group):
            scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
            merged[doc.id] = _merge_path_ranks(merged.get(doc.id), doc, dense_rank=rank)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    result: list[ChunkResult] = []
    for doc_id, score in ranked[:top_n]:
        item = merged[doc_id]
        item.rrf_score = score
        result.append(item)
    return result
