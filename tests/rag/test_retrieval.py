"""检索模块（retrieval）单元测试。

测试目标：
- search()：向量检索功能
- rerank_results()：重排序功能
- expand_query() / condense_query() / decompose_query()：查询改写
- rewrite_query()：集成改写入口

注意：外部依赖通过 unittest.mock 进行 mock。
"""

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import TOP_K_RERANK
from src.core.log_events import Event
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.chat_message import ChatMessage
from src.rag import retrieval
from src.rag.context import RAGContext
from src.rag.errors import RetrievalUnavailableError
from src.rag.retrieval import _rerank_stats, rerank_results


def _cr(content, cid="c1", doc_id=None, parent=None) -> ChunkResult:
    """构造一个 mock ChunkResult，字段满足 rerank_results 读取所需。

    doc_id / parent 为 None 时不写进 metadata（保持既有用例的 metadata={} 行为）。
    """
    metadata: dict = {}
    if doc_id is not None:
        metadata["doc_id"] = doc_id
        metadata["source"] = f"{doc_id}.pdf"
        metadata["page"] = 1
    if parent is not None:
        metadata["parent_content"] = parent
    return cast(
        ChunkResult,
        type(
            "CR",
            (),
            {
                "content": content,
                "id": cid,
                "distance": 0.3,
                "metadata": metadata,
                "lexical_score": None,
            },
        )(),
    )


def _mock_reranker(scores):
    """构造按给定分数列表返回结果的 mock reranker。"""

    class R:
        def rerank(self, docs, query):
            return [{"index": i, "relevance_score": s} for i, s in enumerate(scores)]

    return R()


# ==================== 检索测试 ====================
class TestSearch:
    """测试 search() 检索功能。"""

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        """search() 应调用 vector_store.dense_search 并返回结果。"""
        vs = MagicMock()
        vs.dense_search = AsyncMock(
            return_value=[
                ChunkResult(id="1", content="test", metadata={"doc_id": "d1"})
            ]
        )
        with patch("src.rag.retrieval.HYBRID_SEARCH_ENABLED", False):
            results = await retrieval.search("query", "kb_123", vector_store=vs)
        assert len(results) == 1
        vs.dense_search.assert_called_once()


# ==================== 重排序测试 ====================
class TestRerank:
    """测试 rerank_results() 重排序功能。"""

    def test_rerank_returns_contexts(self):
        """rerank_results() 应返回 RAGContext 列表。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.9},
        ]
        results = [
            ChunkResult(
                id="d1:0",
                content="test content",
                metadata={"source": "a.pdf", "page": 1, "doc_id": "d1"},
            )
        ]
        contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 1
        assert contexts[0].source == "a.pdf"
        assert contexts[0].doc_id == "d1"

    def test_rerank_passthrough_entities(self):
        """rerank 后 chunk metadata 中的实体键应透传到 RAGContext.entities。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.9},
        ]
        results = [
            ChunkResult(
                id="d1:0",
                content="营收增长",
                metadata={
                    "source": "neusoft_2025_q1.pdf",
                    "page": 3,
                    "doc_id": "d1",
                    "company": "东软集团",
                    "report_period": "2025年第一季度",
                    "sec_code": "600718",
                },
            )
        ]
        contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 1
        assert contexts[0].entities == {
            "company": "东软集团",
            "report_period": "2025年第一季度",
            "sec_code": "600718",
        }
        # to_prompt_text 渲染实体锚点
        text = contexts[0].to_prompt_text()
        assert "公司: 东软集团" in text
        assert "期间: 2025年第一季度" in text
        assert "代码: 600718" in text

    def test_rerank_empty_results(self):
        """空结果应返回空列表。"""
        contexts = retrieval.rerank_results("query", [], MagicMock())
        assert contexts == []

    def test_rerank_no_absolute_threshold(self):
        """低分但相对相关的结果不再被绝对阈值过滤，全部进入 top-N。"""
        results = [_cr("a"), _cr("b")]
        # 两个低分但相对相关，旧逻辑会被 0.3 全过滤；新逻辑取 top2
        ctx = rerank_results("q", results, _mock_reranker([0.2, 0.15]))
        assert len(ctx) == 2  # 不再因 <0.3 过滤
        assert [c.score for c in ctx] == [0.2, 0.15]

    def test_rerank_top_n_capped(self):
        """rerank 结果数量以 TOP_K_RERANK 为上限。"""
        # 构造超过 TOP_K_RERANK(5) 的 7 条候选，验证真正发生截断而非恒真断言
        results = [_cr(f"r{i}", cid=f"c{i}") for i in range(7)]
        scores = [0.95 - i * 0.05 for i in range(7)]
        ctx = rerank_results("q", results, _mock_reranker(scores))
        assert len(ctx) == TOP_K_RERANK

    def test_rerank_fallback_orders_by_score_desc(self):
        """rerank 失败 fallback（1-distance 分数）按分数降序排列，不应用绝对阈值。"""
        reranker = MagicMock()
        reranker.rerank.side_effect = RuntimeError("rerank down")
        results = [
            ChunkResult(
                id=f"d1:{i}",
                content=f"content {i}",
                distance=0.9,
                metadata={"source": f"a{i}.pdf", "page": 1, "doc_id": "d1"},
            )
            for i in range(2)
        ]
        # r0 距离 0.9 → 分 0.1，r1 距离 0.5 → 分 0.5；分数降序下先 r1 后 r0
        results[1].distance = 0.5
        with patch(
            "src.rag.retrieval.retry_policy.call_with_retry",
            side_effect=lambda f, *a, **kw: f(*a, **kw),
        ):
            contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 2
        assert [c.score for c in contexts] == pytest.approx([0.5, 0.1])

    def test_rerank_single_attempt_budget(self, monkeypatch):
        """rerank 只做 1 次尝试（D13）：内层 5s 才是真实上界，不会被退避叠加撞穿外层。

        既有 `test_rerank_outer_budget_exceeds_inner` 只比较两个 settings 数值、测不到
        attempts；本用例截获真实传给 `retry_policy.call_with_retry` 的关键字参数做行为级断言。
        若 attempts>1，每次尝试 5s 再叠加 1s/2s 退避，内层最坏 ≈18s 远超外层 6s ⇒
        外层先放弃、`to_thread` 线程仍跑到底（D13 点名要避免的形态）。
        """
        seen: dict = {}

        def _fake_call(fn, *args, **kwargs):
            seen.update(kwargs)
            return fn(*args)

        monkeypatch.setattr(retrieval.retry_policy, "call_with_retry", _fake_call)
        results = [_cr("a", cid="c1", doc_id="d1")]
        rerank_results("q", results, _mock_reranker([0.9]))
        assert seen["attempts"] == 1

    def test_kb_construction_assigns_tier_zero(self):
        """KB 构造点（rerank_results）显式产出 T0（内部文档）。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.9},
        ]
        results = [
            ChunkResult(
                id="d1:0",
                content="test content",
                metadata={"source": "a.pdf", "page": 1, "doc_id": "d1"},
            )
        ]
        contexts = retrieval.rerank_results("query", results, reranker)
        assert contexts[0].tier == 0


# ==================== to_prompt_text 实体渲染测试 ====================
def test_to_prompt_text_with_entities():
    """RAGContext 带实体时，to_prompt_text 应按 ENTITY_RENDER_ORDER 渲染存在的实体。"""
    from src.rag.context import RAGContext

    ctx = RAGContext(
        content="营收增长",
        source="neusoft_2025_q1.pdf",
        page=3,
        doc_id="d",
        chunk_id="c",
        entities={"company": "东软集团", "report_period": "2025年第一季度"},
    )
    text = ctx.to_prompt_text()
    assert "东软集团" in text
    assert "2025年第一季度" in text


def test_to_prompt_text_without_entities():
    """无实体时，to_prompt_text 应保持原格式（来源/页码/内容）。"""
    from src.rag.context import RAGContext

    ctx = RAGContext(
        content="营收增长",
        source="neusoft_2025_q1.pdf",
        page=3,
        doc_id="d",
        chunk_id="c",
    )
    text = ctx.to_prompt_text()
    assert text == "来源: neusoft_2025_q1.pdf (第3页)\n内容: 营收增长"


# ==================== 查询改写测试 ====================
class TestQueryRewrite:
    """测试 rewrite_query() 及辅助改写函数。"""

    def test_simple_passthrough(self):
        """简单查询应直接原样返回（passthrough）。"""
        result = retrieval.rewrite_query("2024年营业收入是多少？", [])
        assert result == "2024年营业收入是多少？"

    def test_short_query_expands(self):
        """模糊短查询应触发 expand_query。"""
        history = [ChatMessage(role="user", content="茅台2024年营收情况")]
        result = retrieval.rewrite_query("净利润呢", history)
        assert "茅台" in result
        assert "净利润" in result

    def test_medium_condenses(self):
        """分析类查询应触发 condense_query 去除口语引导词。"""
        result = retrieval.rewrite_query("分析一下茅台2024年的营收", [])
        assert "分析一下" not in result

    def test_complex_decomposes(self):
        """对比查询应触发 decompose_query 返回子查询列表。"""
        result = retrieval.rewrite_query(
            "对比茅台和五粮液营收", [], intent_route="complex"
        )
        assert isinstance(result, list)
        assert len(result) > 1

    def test_expand_with_history(self):
        """expand_query 应使用最近用户消息扩展短查询。"""
        history = [
            ChatMessage(role="user", content="茅台2024年营收情况"),
            ChatMessage(role="assistant", content="营收1741亿元"),
            ChatMessage(role="user", content="净利润呢"),
        ]
        result = retrieval.expand_query("净利润呢", history)
        assert "净利润呢" in result
        assert "茅台" in result

    def test_expand_no_history(self):
        """无历史时 expand_query 应返回原查询。"""
        result = retrieval.expand_query("营收", [])
        assert result == "营收"

    def test_condense_removes_patterns(self):
        """condense_query 应移除口语化引导词。"""
        result = retrieval.condense_query("分析一下茅台2024年营收")
        assert "分析一下" not in result
        assert "茅台2024年营收" in result

    def test_decompose_splits_comparison(self):
        """decompose_query 应将对比查询拆分为子查询列表。"""
        result = retrieval.decompose_query("对比茅台和五粮液营收")
        assert isinstance(result, list)
        assert len(result) >= 2


# ==================== tier 字段与档位标注测试（source-tier-labeling）====================


class TestTierAnnotation:
    """to_prompt_text 档位标注：tier 非 None 追加标签，None 保持存量格式。"""

    def _make(self, tier=None):
        return RAGContext(
            content="内容",
            source="a.pdf",
            page=1,
            doc_id="d1",
            chunk_id="c1",
            tier=tier,
        )

    def test_kb_tier_annotation(self):
        """KB（tier=0）标注「内部文档」，并入页码括注。"""
        assert "(第1页, 内部文档)" in self._make(tier=0).to_prompt_text()

    def test_web_tier_annotation(self):
        """web tier=2 标注「权威媒体」。"""
        assert "(第1页, 权威媒体)" in self._make(tier=2).to_prompt_text()

    def test_tier_none_keeps_legacy_format(self):
        """tier=None（存量/未定档）保持原格式，无标签。"""
        text = self._make(tier=None).to_prompt_text()
        assert "来源: a.pdf (第1页)" in text
        assert "内部文档" not in text

    def test_default_tier_is_none(self):
        """默认值为 None（design D6：不设 T0 兜底，防漏传错标最高信任档）。"""
        assert self._make().tier is None


def test_to_prompt_text_web_ctx_with_tier_label():
    """web 上下文（tier 非 None）走 to_prompt_text 渲染时，来源括注内追加档位标签。

    前序遗留覆盖：Task 3 仅测了块文本，web ctx 经 to_prompt_text 的渲染未覆盖；
    生产 prompt 与 RAGAS NLI 共用此格式，需保证档位标签在两处一致出现。
    """
    from src.config.const import SSEInteractionTexts
    from src.rag.context import RAGContext

    ctx = RAGContext(
        content="网页内容",
        source="https://www.tencent.com/a",
        page=0,
        doc_id="u",
        chunk_id="u",
        kind=SSEInteractionTexts.CITATION_KIND_WEB,
        tier=1,
    )
    text = ctx.to_prompt_text()
    assert text == ("来源: https://www.tencent.com/a (第0页, 官方一手)\n内容: 网页内容")


# ==================== 两路同源并发检索（P3 Task 8）====================


@pytest.mark.asyncio
async def test_hybrid_runs_both_paths_concurrently_on_same_store(monkeypatch):
    """两路并发、同源于一个 VectorStore，且各带各的排名与得分。

    可证伪性：两路各自进入方法后必须在 `asyncio.Barrier(2)` 上会合——只有
    两路同时「在飞」屏障才放行。串行实现下先进入的那一路永远等不到第二个
    party，0.5s 后 `Barrier.wait()` 超时 → 整体 `TimeoutError`，测试必败。
    """
    barrier = asyncio.Barrier(2)

    class _Store:
        async def dense_search(self, kb_id, query, k):
            await asyncio.wait_for(barrier.wait(), timeout=0.5)
            return [
                ChunkResult(
                    id="a", content="A", metadata={}, distance=0.1, dense_rank=0
                ),
                ChunkResult(
                    id="b", content="B", metadata={}, distance=0.2, dense_rank=1
                ),
            ]

        async def lexical_search(self, kb_id, query, k):
            await asyncio.wait_for(barrier.wait(), timeout=0.5)
            return [
                ChunkResult(
                    id="b", content="B", metadata={}, lexical_score=0.9, sparse_rank=0
                ),
            ]

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    results = await asyncio.wait_for(
        retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store())),
        timeout=1.0,
    )

    by_id = {r.id: r for r in results}
    assert by_id["b"].dense_rank == 1
    assert by_id["b"].sparse_rank == 0
    assert by_id["a"].sparse_rank is None


@pytest.mark.asyncio
async def test_hybrid_logs_both_path_contributions(monkeypatch):
    """两路贡献必须都进日志：任一路为 0 时可被直接看出。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            return [ChunkResult(id="a", content="A", metadata={}, distance=0.1)]

        async def lexical_search(self, kb_id, query, k):
            return []

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: dict = {}
    monkeypatch.setattr(
        retrieval,
        "log_event",
        lambda event, **fields: logged.update({"event": event, **fields}),
    )
    results = await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    assert logged["dense_count"] == 1
    assert logged["sparse_count"] == 0
    # 一路为空时融合结果仍非空、可用于回答（delta scenario 直接覆盖）
    assert [r.id for r in results] == ["a"]


@pytest.mark.asyncio
async def test_dense_only_when_hybrid_disabled(monkeypatch):
    """混合关闭时只走 dense，不调词法路。"""
    called: list[str] = []

    class _Store:
        async def dense_search(self, kb_id, query, k):
            return [ChunkResult(id="a", content="A", metadata={}, distance=0.1)]

        async def lexical_search(self, kb_id, query, k):
            called.append("sparse")
            return []

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", False)
    results = await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    assert called == []
    assert [r.id for r in results] == ["a"]


def test_rerank_stats_percentiles_and_source():
    """分位数取 max/min/p50；未降级时来源标记为 rerank。"""
    reranked = [
        {"index": 0, "relevance_score": 0.9},
        {"index": 1, "relevance_score": 0.5},
        {"index": 2, "relevance_score": 0.1},
    ]
    out = _rerank_stats(reranked, used_fallback=False)
    assert out["score_max"] == 0.9
    assert out["score_min"] == 0.1
    assert out["score_p50"] == 0.5
    assert out["scored"] == "rerank"


def test_rerank_stats_marks_fallback_source():
    """精排失败回退（1 - distance）时来源标记为 fallback。"""
    reranked = [
        {"index": 0, "relevance_score": 0.0},
        {"index": 1, "relevance_score": 1.0},
    ]
    out = _rerank_stats(reranked, used_fallback=True)
    assert out["scored"] == "fallback"
    assert out["score_max"] == 1.0
    assert out["score_min"] == 0.0
    assert out["score_p50"] == 0.5


def test_rerank_dedups_before_truncating(monkeypatch):
    """去重发生在 TOP_K_RERANK 截断之前：3 条同父块只占 1 个名额。"""
    parent = "同一段父块正文"
    results = [
        _cr("a1", cid="c1", doc_id="d1", parent=parent),
        _cr("a2", cid="c2", doc_id="d1", parent=parent),
        _cr("a3", cid="c3", doc_id="d1", parent=parent),
        _cr("b1", cid="c4", doc_id="d2", parent="另一段父块"),
    ]
    monkeypatch.setattr(retrieval, "TOP_K_RERANK", 2)
    out = rerank_results("q", results, _mock_reranker([0.9, 0.89, 0.88, 0.87]))
    assert len(out) == 2
    assert out[0].chunk_id == "c1"  # 同父块里 .score 最高者
    assert "c4" in [c.chunk_id for c in out]  # 另一父块未被重复项挤掉


def test_rerank_dedup_logs_dropped_kept(monkeypatch):
    """内容级去重折叠时 dedup done 事件记录 dropped/kept。"""
    parent = "同一段父块正文"
    results = [
        _cr("a1", cid="c1", doc_id="d1", parent=parent),
        _cr("a2", cid="c2", doc_id="d1", parent=parent),
        _cr("a3", cid="c3", doc_id="d1", parent=parent),
        _cr("b1", cid="c4", doc_id="d2", parent="另一段父块"),
    ]
    logged: list[dict] = []
    monkeypatch.setattr(
        retrieval,
        "log_event",
        lambda event, **fields: logged.append({"event": event, **fields}),
    )
    rerank_results("q", results, _mock_reranker([0.9, 0.89, 0.88, 0.87]))
    dedup = [e for e in logged if e["event"] == retrieval.Event.DEDUP_DONE]
    assert len(dedup) == 1
    assert dedup[0]["dropped"] == 2
    assert dedup[0]["kept"] == 2


def test_rerank_dedup_logs_zero_drop(monkeypatch):
    """无折叠时 dedup done 事件记录 dropped=0（spec 无丢弃时记零）。"""
    results = [
        _cr("a1", cid="c1", doc_id="d1", parent="父块A"),
        _cr("a2", cid="c2", doc_id="d2", parent="父块B"),
    ]
    logged: list[dict] = []
    monkeypatch.setattr(
        retrieval,
        "log_event",
        lambda event, **fields: logged.append({"event": event, **fields}),
    )
    rerank_results("q", results, _mock_reranker([0.9, 0.8]))
    dedup = [e for e in logged if e["event"] == retrieval.Event.DEDUP_DONE]
    assert len(dedup) == 1
    assert dedup[0]["dropped"] == 0
    assert dedup[0]["kept"] == 2


# ==================== 单路失败降级与三态状态字段（timeout-and-degradation-hardening P2）====================


@pytest.mark.asyncio
async def test_hybrid_dense_failure_degrades_to_lexical_only(monkeypatch):
    """稠密路失败 ⇒ 仍返回词法结果；状态字段区分 failed 与 ok，并留痕 degraded。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            raise RuntimeError("embedding endpoint down")

        async def lexical_search(self, kb_id, query, k):
            return [
                ChunkResult(
                    id="b", content="B", metadata={}, lexical_score=0.9, sparse_rank=0
                )
            ]

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: logged.append((event, fields))
    )

    results = await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    # 词法结果必须被返回（修前：gather 无 return_exceptions ⇒ 整体抛错、结果丢失）
    assert [r.id for r in results] == ["b"]
    hybrid = [f for e, f in logged if e == Event.HYBRID_DONE]
    assert len(hybrid) == 1
    assert hybrid[0]["dense_status"] == "failed"
    assert hybrid[0]["sparse_status"] == "ok"
    assert hybrid[0]["dense_count"] == 0
    degraded = [f for e, f in logged if e == Event.DEGRADED]
    assert len(degraded) == 1
    assert degraded[0]["reason"] == "embedding_failed"


@pytest.mark.asyncio
async def test_hybrid_sparse_failure_still_returns_dense(monkeypatch):
    """词法路失败 ⇒ 仍返回稠密结果（对称场景，防只改了一半）。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            return [ChunkResult(id="a", content="A", metadata={}, distance=0.1)]

        async def lexical_search(self, kb_id, query, k):
            raise RuntimeError("pg fts down")

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: logged.append((event, fields))
    )

    results = await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    assert [r.id for r in results] == ["a"]
    hybrid = next(f for e, f in logged if e == Event.HYBRID_DONE)
    assert hybrid["dense_status"] == "ok"
    assert hybrid["sparse_status"] == "failed"


@pytest.mark.asyncio
async def test_hybrid_success_but_empty_is_empty_not_failed(monkeypatch):
    """成功但空结果 ⇒ empty（缺席），不得与 failed 混淆。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            return []

        async def lexical_search(self, kb_id, query, k):
            return []

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: logged.append((event, fields))
    )

    results = await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    assert results == []
    hybrid = next(f for e, f in logged if e == Event.HYBRID_DONE)
    assert hybrid["dense_status"] == "empty"
    assert hybrid["sparse_status"] == "empty"
    assert not [f for e, f in logged if e == Event.DEGRADED]


@pytest.mark.asyncio
async def test_hybrid_both_paths_failed_raises_retrieval_unavailable(monkeypatch):
    """两路皆失败 ⇒ 抛 RetriavalUnavailableError（按失败处置，不静默返回空）。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            raise RuntimeError("embedding down")

        async def lexical_search(self, kb_id, query, k):
            raise RuntimeError("pg down")

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: logged.append((event, fields))
    )

    with pytest.raises(RetrievalUnavailableError):
        await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    # 本层只抛不留痕：终态那条由上层（agent_service 的降级发射）记，避免同失败两处各记一次
    assert not [f for e, f in logged if e == Event.DEGRADED]
    assert not [f for e, f in logged if e == Event.HYBRID_DONE]


# ==================== 单阶段耗时（timeout-and-degradation-hardening P4）====================


def test_rerank_done_carries_stage_latency(monkeypatch):
    """`rerank done` 必须带本阶段耗时 `latency_ms`（D11 待校准项要它来定值）。

    计时只在精排调用**周围**，与 `retrieve done` 的全链路合计相区分。
    **注意**：`rerank_results` 在 `rerank done` **之后**还会落一条 `dedup done`
    ⇒ 捕获必须收成**列表再按事件过滤**，不能用"最后一条覆盖"的 dict
    （否则拿到的永远是 `dedup done` 的字段，断言必红）。
    """
    captured: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: captured.append((event, fields))
    )
    results = [
        ChunkResult(id="a", content="A", metadata={}, distance=0.1),
        ChunkResult(id="b", content="B", metadata={}, distance=0.2),
    ]

    class _ReRanker:
        def rerank(self, docs, query):
            return [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.1},
            ]

    contexts = retrieval.rerank_results("资产负债率", results, _ReRanker())

    assert len(contexts) == 2
    rerank_done = [f for e, f in captured if e == Event.RERANK_DONE]
    assert rerank_done, "未落 rerank done 事件"
    assert "latency_ms" in rerank_done[0], "rerank done 未带 latency_ms"
    assert isinstance(rerank_done[0]["latency_ms"], int)
    assert rerank_done[0]["latency_ms"] >= 0
