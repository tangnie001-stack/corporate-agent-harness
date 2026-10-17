"""检索与查询改写 — 向量检索、Reranker 精排、查询分类与改写。"""

import asyncio
import hashlib
import time

from src.config import (
    HYBRID_SEARCH_ENABLED,
    RERANK_MAX_ATTEMPTS,
    RETRY_BACKOFF_FACTOR,
    RETRY_INITIAL_INTERVAL,
    RRF_K,
    RRF_TOP_N,
    TOP_K_RERANK,
    TOP_K_RETRIEVAL,
)
from src.config.const import (
    ENTITY_OPTIONAL_TYPES,
    ENTITY_TYPES,
    SSEInteractionTexts,
    resolve_source_tier,
)
from src.core.log_events import Event
from src.core.logging import log_event
from src.infra import retry_policy
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.chat_message import ChatMessage
from src.rag.context import RAGContext
from src.rag.errors import RetrievalUnavailableError
from src.rag.fusion import rrf_fusion

# 全部实体键（核心 + 可选），rerank 透传时从 chunk.metadata 读取
_ALL_ENTITY_KEYS: tuple[str, ...] = tuple(ENTITY_TYPES) + tuple(ENTITY_OPTIONAL_TYPES)


def _dedup_by_parent(contexts: list[RAGContext]) -> list[RAGContext]:
    """内容级去重：同一文档内同一父块只保留 `.score` 最高的一条。

    去重键为 `(doc_id, md5(parent_content))` —— 必须含 `doc_id`：跨文档的样板文本
    （年报"重要提示"等）可能逐字相同，只按内容哈希会把两个真实候选折叠成一个。
    `parent_content` 为空的 chunk 按自身保留（不参与折叠）。
    返回按 `.score` 降序排列；分数语义见调用方（精排分或降级回退分）。

    Args:
        contexts: 精排后（或降级回退后）的上下文列表，每项 `.score` 已填好

    Returns:
        去重后的列表，按 `.score` 降序
    """
    best: dict[tuple[str, str], RAGContext] = {}
    passthrough: list[RAGContext] = []
    for c in contexts:
        if not c.parent_content:
            passthrough.append(c)
            continue
        key = (c.doc_id, hashlib.md5(c.parent_content.encode("utf-8")).hexdigest())
        current = best.get(key)
        if current is None or c.score > current.score:
            best[key] = c
    merged = [*best.values(), *passthrough]
    merged.sort(key=lambda c: c.score, reverse=True)
    return merged


def _path_status(failed: bool, results: list[ChunkResult]) -> str:
    """支路本次调用状态（规格三态：ok / empty / failed）。

    failed 与 empty **必须可分**：`count = 0` 分不出"该路失败"与"该路成功但没命中"，
    而两者的处置完全不同（前者是降级、后者是缺席）。

    Args:
        failed: 该路调用是否未成功（抛错 / 超时）
        results: 该路成功时返回的结果列表（failed=True 时忽略）

    Returns:
        "failed" / "empty" / "ok"
    """
    if failed:
        return "failed"
    if results:
        return "ok"
    return "empty"


async def search(
    query: str,
    kb_id: str,
    vector_store: VectorStore,
) -> list[ChunkResult]:
    """执行检索：dense + 词法两路同源并发取数，融合在应用层。

    两路都经同一个 VectorStore（背后是同一个 PostgreSQL 实例的 chunks 表）——
    「某一支路半死而整体正常」的结构性原因由此消失。

    Args:
        query: 用户查询文本
        kb_id: 知识库 ID（调用方保证非空：`rag_tools.py` 在 kb_id 为空时直接返回空结果）
        vector_store: 向量存储实例（dense 与词法两路的共同入口）

    Returns:
        检索结果列表，按相关性降序排列；混合模式为 RRF 融合结果
    """
    if HYBRID_SEARCH_ENABLED:
        dense_coro = vector_store.dense_search(kb_id, query, TOP_K_RETRIEVAL)
        lexical_coro = vector_store.lexical_search(kb_id, query, TOP_K_RETRIEVAL)
        # return_exceptions=True：任一路失败不得连带丢弃另一路（design D7）。
        # 修前未传该参数 ⇒ 稠密路一抛整个 search 就抛，"退化为词法单路"根本不成立。
        dense_out, sparse_out = await asyncio.gather(
            dense_coro, lexical_coro, return_exceptions=True
        )
        dense_failed = isinstance(dense_out, BaseException)
        sparse_failed = isinstance(sparse_out, BaseException)
        if dense_failed and sparse_failed:
            # 两路皆失败 ⇒ 向量库不可用：按失败处置（抛给上层归类/告知），
            # 不得静默返回空结果当作"没有数据"。本层只抛不留痕（见计划 Task 1 落地规则）。
            assert isinstance(dense_out, BaseException)  # 供类型收窄，运行期恒真
            raise RetrievalUnavailableError(str(dense_out)) from dense_out
        if dense_failed:
            # 稠密路失败 ⇒ 跳过稠密路只走词法（降级动作在本调用点完成）。
            # 原因统一记 embedding_failed：dense 路的失败来源（查询向量化 / pgvector）
            # 与本降级动作同义，不引入区分二者所需的新判据。
            log_event(Event.DEGRADED, reason="embedding_failed", err=str(dense_out))
            dense_results: list[ChunkResult] = []
        else:
            dense_results = dense_out or []
        if sparse_failed:
            sparse_results: list[ChunkResult] = []
        else:
            sparse_results = sparse_out or []
        results = rrf_fusion(dense_results, sparse_results, k=RRF_K, top_n=RRF_TOP_N)
        # 两路各自的贡献必须可见：任一路为 0 时该字段就是 0。
        # 只记融合后的总数会让"某一路长期失效"不可发现（trace_c54ce259 的教训）。
        # dense_status / sparse_status 另把"失败"与"成功但空"分开（规格要求）。
        log_event(
            Event.HYBRID_DONE,
            kb_id=kb_id,
            query_len=len(query),
            dense_count=len(dense_results),
            sparse_count=len(sparse_results),
            dense_status=_path_status(dense_failed, dense_results),
            sparse_status=_path_status(sparse_failed, sparse_results),
            result_count=len(results),
        )
        return results

    results = await vector_store.dense_search(kb_id, query, k=TOP_K_RETRIEVAL)
    if results:
        result_count = len(results)
    else:
        result_count = 0
    log_event(
        Event.SEARCH_DONE,
        kb_id=kb_id,
        query_len=len(query),
        result_count=result_count,
    )
    return results or []


def _rerank_stats(reranked: list[dict], used_fallback: bool) -> dict:
    """汇总精排分数的分位信息与来源标记（供 `rerank done` 事件使用）。

    只取分位数、不取全量分数：该事件每次 `retrieve_kb` 都落，全量会放大日志体积。
    不另记 `score_top1` —— `reranked` 按分数降序，top1 恒等于 `score_max`。

    Args:
        reranked: 精排结果（或降级回退结果），每项含 `relevance_score`
        used_fallback: 是否走了 `except` 降级回退（分数来自 `1 - distance`）

    Returns:
        {"score_max","score_min","score_p50","scored"}；`scored` 取 "rerank" 或 "fallback"
    """
    scores = sorted(float(item.get("relevance_score", 0)) for item in reranked)
    if used_fallback:
        scored = "fallback"
    else:
        scored = "rerank"
    if not scores:
        return {"score_max": 0.0, "score_min": 0.0, "score_p50": 0.0, "scored": scored}
    mid = len(scores) // 2
    if len(scores) % 2 == 0:
        p50 = (scores[mid - 1] + scores[mid]) / 2
    else:
        p50 = scores[mid]
    return {
        "score_max": scores[-1],
        "score_min": scores[0],
        "score_p50": p50,
        "scored": scored,
    }


def rerank_results(
    query: str,
    results: list[ChunkResult],
    reranker,
) -> list[RAGContext]:
    """Reranker 精排，返回 top-N 的 RAGContext 列表。

    Args:
        query: 用户原始查询（用于 reranker 的相关性计算）
        results: 检索结果列表（dense 与词法两路已融合）
        reranker: Reranker 模型实例

    Returns:
        精排后的 RAGContext 列表，按相关性降序排列，长度不超过 TOP_K_RERANK；
        取前 TOP_K_RERANK 条相对结果，不应用绝对分数阈值过滤
    """
    if not results:
        log_event(Event.RERANK_SKIP, reason="empty_input")
        return []

    docs = [r.content for r in results]
    used_fallback = False
    start = time.monotonic()
    try:
        reranked = retry_policy.call_with_retry(
            reranker.rerank,
            docs,
            query,
            attempts=RERANK_MAX_ATTEMPTS,
            initial_interval=RETRY_INITIAL_INTERVAL,
            backoff=RETRY_BACKOFF_FACTOR,
        )
    except Exception as e:  # noqa: BLE001
        log_event(
            Event.RERANK_FAILED,
            attempts=RERANK_MAX_ATTEMPTS,
            query=query,
            err=str(e),
        )
        used_fallback = True
        reranked = []
        for i, r in enumerate(results):
            if r.distance is not None:
                fallback_score = 1 - r.distance
            else:
                fallback_score = 0
            reranked.append({"index": i, "relevance_score": fallback_score})
    rerank_ms = int((time.monotonic() - start) * 1000)

    contexts: list[RAGContext] = []
    for item in reranked:
        idx = item["index"]
        r = results[idx]
        score = item.get("relevance_score", 0)
        pc = r.metadata.get("parent_content")
        if pc:
            content = pc
        else:
            content = r.content
        contexts.append(
            RAGContext(
                content=content,
                source=r.metadata.get("source", ""),
                page=r.metadata.get("page", 0),
                doc_id=r.metadata.get("doc_id", ""),
                chunk_id=r.id,
                parent_content=pc,
                score=score,
                tier=resolve_source_tier(
                    r.metadata.get("source", ""),
                    SSEInteractionTexts.CITATION_KIND_KB,
                ),
                entities={
                    k: r.metadata.get(k) for k in _ALL_ENTITY_KEYS if r.metadata.get(k)
                },
            )
        )
    if contexts:
        log_event(
            Event.RERANK_DONE,
            doc_count=len(results),
            query_len=len(query),
            latency_ms=rerank_ms,
            **_rerank_stats(reranked, used_fallback),
        )
    before = len(contexts)
    contexts = _dedup_by_parent(contexts)
    log_event(Event.DEDUP_DONE, dropped=before - len(contexts), kept=len(contexts))
    return contexts[:TOP_K_RERANK]


def expand_query(query: str, history: list[ChatMessage]) -> str:
    """对模糊短查询进行扩展。"""
    if not history:
        return query
    for msg in reversed(history):
        if msg.role == "user" and msg.content != query:
            return f"{msg.content} {query}"
    return query


def condense_query(query: str) -> str:
    """将口语化查询精简。"""
    condense_patterns = ["分析", "解释", "说明", "为什么"]
    cleaned = query
    for pat in condense_patterns:
        cleaned = cleaned.replace(pat, "").strip()
    return cleaned if cleaned else query


def decompose_query(query: str) -> list[str]:
    """将对比类查询分解为子查询。"""
    separators = ["对比", "比较", "差异", "versus", "vs", "和", "与"]
    parts = [query]
    for sep in separators:
        new_parts = []
        for p in parts:
            new_parts.extend(p.split(sep))
        parts = [p.strip() for p in new_parts if p.strip()]
    return [p for p in parts if p]


def rewrite_query(
    query: str,
    history: list[ChatMessage],
    intent_route: str = "medium",
) -> str | list[str]:
    """根据三级分类执行相应的改写策略。

    Args:
        query: 用户原始查询
        history: 对话历史
        intent_route: 意图路由，由上游 classify_node 提供

    Returns:
        str:      simple / medium 路径返回改写后的单条查询
        list[str]: complex 路径返回分解后的多条子查询
    """
    if intent_route == "simple":
        return query
    if intent_route == "complex":
        return decompose_query(query)
    # medium
    if len(query.strip()) < 10:
        return expand_query(query, history)
    if any(w in query for w in ["分析", "解释", "说明", "为什么"]):
        return condense_query(query)
    return query
