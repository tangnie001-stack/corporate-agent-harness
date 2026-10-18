"""检索结果类型 — dense 语义检索与词法检索的统一输出类型。"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class ChunkResult:
    """检索结果统一类型。

    替代 similarity_search / 词法检索 / RRF fusion / rerank 之间的 list[dict]。
    统一 dense 与词法两路的输出格式；两路同源于一个 PostgreSQL 实例。
    """

    id: str
    """分块 ID，格式为 {doc_id}:{chunk_index}。"""
    content: str
    """分块的文本内容，由文档解析器生成，可能包含 Markdown 格式。"""
    metadata: dict = field(default_factory=dict)
    """元数据字典，含 doc_id / chunk_index / chunk_total / source / page 五个契约键，
    以及 chunker 产出的全部自定义键（如 parent_content / block_type / heading_path）。
    由列值与 jsonb 平铺合并回填，冲突以列为准。"""
    distance: float | None = None
    """余弦距离，仅 dense 检索时有值（越小越相似）；词法检索与分页查询时为 None。"""
    lexical_score: float | None = None
    """词法检索得分，仅词法检索时有值；dense 检索与分页查询时为 None。
    与引擎无关的命名：P3 后它来自 PostgreSQL 全文检索，不再是 BM25。"""
    dense_rank: int | None = None
    """该结果在 dense 路的排名（0 起）；未出现在 dense 路时为 None。"""
    sparse_rank: int | None = None
    """该结果在词法路的排名（0 起）；未出现在词法路时为 None。"""
    rrf_score: float | None = None
    """RRF 融合得分；仅 RRF 输出有值，dense/lexical 单路为 None。"""


@dataclass(slots=True)
class ChunkQueryResult:
    """分块分页查询结果（get_chunks_paginated 的返回类型）。"""

    items: list[ChunkResult]
    """当前页的分块列表。"""
    total: int
    """该文档的总分块数量。"""
    page: int
    """当前页码，从 1 开始。"""
    page_size: int
    """每页条数。"""
