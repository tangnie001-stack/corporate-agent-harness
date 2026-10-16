"""dense 检索的 PostgreSQL + pgvector 后端（公开入口 VectorStore 的实现）。

契约要点：
- distance 是**余弦距离**（pgvector `<=>`），消费方用 score = 1 - distance；
- k 的上限是 MAX_QUERY_K=100：PG 本身无此限制，保留它是为了不把
  「检索条数的能力提升」混进迁移等价性验收。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.chunking.validator import ChunkData
from src.config import EMBEDDING_MODEL
from src.config.const import MAX_QUERY_K
from src.core import logging as core_logging
from src.core.log_events import Event
from src.core.logging import LOG_MAX_BODY
from src.infra.db.lexical_query import build_lexical_query
from src.infra.db.vector_store.mapping import build_rows, row_to_chunk_result
from src.infra.db.vector_store.types import ChunkQueryResult, ChunkResult
from src.models import get_embeddings

if TYPE_CHECKING:
    # 仅在类型检查期导入，避免在导入期与 chunk_repo 形成循环依赖
    # （chunk_repo → vector_store 父包 __init__ → pg_store → chunk_repo）。
    from src.infra.db.repos.chunk_repo import ChunkRepo


class QueryEmbedder:
    """查询/文档的向量化入口 —— 薄封装，便于测试注入假实现。"""

    def embed_query(self, text: str) -> list[float]:
        """把查询文本向量化（用查询档 read 超时，失败可退词法，D10）。

        Args:
            text: 查询文本

        Returns:
            1024 维向量
        """
        from src.config import settings

        return get_embeddings(
            read_timeout=settings.EMBEDDING_TIMEOUT_READ_QUERY
        ).embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档文本。

        Args:
            texts: 文本列表

        Returns:
            与输入一一对应的向量列表
        """
        return get_embeddings().embed_documents(texts)


class PgVectorStore:
    """分块向量存储的 PostgreSQL 后端（单表 + kb_id 列，无 collection 概念）。"""

    def __init__(
        self,
        chunk_repo: ChunkRepo | None = None,
        embed_fn: QueryEmbedder | None = None,
    ) -> None:
        """初始化。

        Args:
            chunk_repo: chunks 表访问层；缺省时用应用默认 session_factory 构造
            embed_fn: 向量化入口；缺省时用 QueryEmbedder（DashScope）
        """
        if chunk_repo is None:
            from src.infra.db.engine import session_factory
            from src.infra.db.repos.chunk_repo import ChunkRepo

            chunk_repo = ChunkRepo(session_factory)
        if embed_fn is None:
            embed_fn = QueryEmbedder()
        self._repo = chunk_repo
        self._embed_fn = embed_fn

    async def add_chunks(
        self,
        kb_id: str,
        chunks: list[ChunkData],
        doc_id: str,
        embeddings: list[list[float]] | None = None,
        session: AsyncSession | None = None,
    ) -> int:
        """批量写入分块（按 (kb_id, doc_id, chunk_index) 幂等覆盖）。

        Args:
            kb_id: 知识库 ID
            chunks: 分块数据列表
            doc_id: 文档 ID
            embeddings: 预计算向量；`session` 为 None 时留 None 可在此处补算，
                `session` 非 None 时必须由调用方在事务外算好
            session: 外部事务边界提供的会话；None 时下层自开会话并提交

        Returns:
            实际写入的分块数量

        Note:
            传入 `session` 时本方法**不提交**：提交/回滚由外部边界决定。调用方须
            保证 embedding 已在事务外算好 —— 向量化是慢的外部调用，不应占用事务。

        Raises:
            ValueError: 同时传入 `session` 与 `embeddings=None`；该组合会把 embedding
                外部调用带进调用方已开启的事务，违反 D7
        """
        # 先于空列表短路：传 session 却依赖内部补算属契约误用，需在任何路径上确定性报错
        if session is not None and embeddings is None:
            raise ValueError(
                "add_chunks: 传入 session 时必须由调用方在事务外预计算 embeddings"
            )
        if not chunks:
            return 0
        if embeddings is None:
            # 向量化是同步的 HTTP 调用 → 必须 offload，否则阻塞事件循环（单 worker 下会冻住所有请求与 SSE）
            embeddings = await asyncio.to_thread(
                self._embed_fn.embed_documents, [c.content for c in chunks]
            )
        # 分词是 CPU 工作且 jieba 首次调用要加载词典（约 0.5–1 s）→ offload，
        # 否则阻塞事件循环（单 worker 下会冻住所有请求与 SSE）
        rows = await asyncio.to_thread(build_rows, kb_id, doc_id, chunks, embeddings)
        await self._repo.upsert_chunks(rows, session=session)
        # 分块数变少时删掉尾部残留（upsert 只覆盖 [0, len(rows)) 区间）
        await self._repo.delete_tail(kb_id, doc_id, len(rows), session=session)
        core_logging.log_event(
            Event.CHUNKS_ADDED, kb_id=kb_id, doc_id=doc_id, count=len(rows)
        )
        data_str = str(rows)[:LOG_MAX_BODY]
        logger.debug(
            "[PG] method=add_chunks | kb_id={} | doc_id={} | rows={} | data={}",
            kb_id,
            doc_id,
            len(rows),
            data_str,
        )
        return len(rows)

    async def dense_search(
        self, kb_id: str, query: str, k: int = 5
    ) -> list[ChunkResult]:
        """dense 路取 top-k（余弦距离升序），并填充 dense_rank。

        Args:
            kb_id: 知识库 ID
            query: 查询文本
            k: 返回条数上限（内部再按 MAX_QUERY_K 截断）

        Returns:
            按余弦距离升序的 ChunkResult；每项 distance 有值、dense_rank 为 0 起的名次
        """
        effective_k = min(k, MAX_QUERY_K)
        query_vec = await asyncio.to_thread(self._embed_fn.embed_query, query)
        pairs = await self._repo.search_dense(kb_id, query_vec, effective_k)
        results = [
            row_to_chunk_result(row, distance=distance, dense_rank=rank)
            for rank, (row, distance) in enumerate(pairs)
        ]
        core_logging.log_event(
            Event.SEARCH_RESULT,
            kb_id=kb_id,
            query_len=len(query),
            result_count=len(results),
            model=EMBEDDING_MODEL,
        )
        logger.debug(
            "[PG] method=dense_search | kb_id={} | rows={} | data={}",
            kb_id,
            len(results),
            str(results)[:LOG_MAX_BODY],
        )
        return results

    async def lexical_search(
        self, kb_id: str, query: str, k: int = 5
    ) -> list[ChunkResult]:
        """词法路取 top-k（ts_rank 降序），并填充 sparse_rank。

        与 dense_search 对称：分词（含首次加载 jieba 词典，CPU 约 0.5–1 s）
        放在线程池，避免阻塞事件循环。

        Args:
            kb_id: 知识库 ID
            query: 用户查询文本
            k: 返回条数上限（内部再按 MAX_QUERY_K 截断）

        Returns:
            按词法得分降序的 ChunkResult；每项 lexical_score 有值、
            sparse_rank 为 0 起的名次、distance 为 None
        """
        effective_k = min(k, MAX_QUERY_K)
        plan = await asyncio.to_thread(build_lexical_query, query)
        pairs = await self._repo.search_lexical(kb_id, plan, effective_k)
        results = [
            row_to_chunk_result(row, lexical_score=score, sparse_rank=rank)
            for rank, (row, score) in enumerate(pairs)
        ]
        logger.debug(
            "[PG] method=lexical_search | kb_id={} | rows={} | substring={} | data={}",
            kb_id,
            len(results),
            plan.use_substring,
            str(results)[:LOG_MAX_BODY],
        )
        return results

    async def similarity_search(
        self, kb_id: str, query: str, k: int = 5
    ) -> list[ChunkResult]:
        """dense 检索入口（dense_search 的别名，保留既有方法名与语义）。

        语义与 Chroma 后端一致：余弦**距离**，越小越相似；消费方用 score = 1 - distance。
        """
        return await self.dense_search(kb_id, query, k=k)

    async def get_chunks_by_doc_id(self, doc_id: str, kb_id: str) -> list[ChunkResult]:
        """取某文档的全部分块（分路字段为 None）。"""
        rows = await self._repo.get_by_doc(doc_id, kb_id)
        return [row_to_chunk_result(row) for row in rows]

    async def get_chunks_paginated(
        self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50
    ) -> ChunkQueryResult:
        """分页取某文档的分块。

        Args:
            doc_id: 文档 ID
            kb_id: 知识库 ID
            page: 页码，**1-based**（调用方保证 >= 1；<= 0 会被 PG 拒绝）
            page_size: 每页条数
        """
        rows, total = await self._repo.get_paginated(doc_id, kb_id, page, page_size)
        return ChunkQueryResult(
            items=[row_to_chunk_result(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_all_chunks(self, kb_id: str) -> list[ChunkResult]:
        """取整个知识库的全部分块（空库检查 / 全量读取用）。"""
        rows = await self._repo.get_by_kb(kb_id)
        core_logging.log_event(Event.CHUNKS_READ, kb_id=kb_id, count=len(rows))
        return [row_to_chunk_result(row) for row in rows]

    async def list_collections(self) -> list[str]:
        """枚举含分块的知识库 ID（PG 无 collection，语义是「有哪些 kb 有分块」）。

        只读，不创建任何东西。
        """
        return await self._repo.list_kb_ids()

    async def delete_document(
        self, kb_id: str, doc_id: str, session: AsyncSession | None = None
    ) -> int:
        """删除某文档的全部分块，返回删除行数。

        Args:
            kb_id: 知识库 ID
            doc_id: 文档 ID
            session: 外部事务边界提供的会话；None 时下层自开会话并提交
        """
        return await self._repo.delete_by_doc(kb_id, doc_id, session=session)

    async def delete_collection(
        self, kb_id: str, session: AsyncSession | None = None
    ) -> bool:
        """删除某知识库的全部分块；返回是否删除了行。

        Args:
            kb_id: 知识库 ID
            session: 外部事务边界提供的会话；None 时下层自开会话并提交
        """
        deleted = await self._repo.delete_by_kb(kb_id, session=session)
        return deleted > 0

    async def get_or_create_collection(self, kb_id: str) -> str:
        """PG 无 collection 概念：该方法不产生副作用，直接返回 kb_id。

        保留是为了维持既有方法名契约；调用方不应依赖它「创建」任何东西。
        """
        return kb_id
