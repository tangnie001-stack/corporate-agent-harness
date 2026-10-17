"""测试 retrieve_kb 工具 — 全局递增引用编号 + collector 累积。

检索与精排均通过 monkeypatch mock，不发真实网络调用。
async 工具不支持 sync invoke（NotImplementedError），测试统一用 ainvoke。
"""

from typing import cast

import pytest

from src.agents.graph.state import AgentState
from src.agents.tools.rag_tools import make_rag_tools
from src.infra.db.vector_store import VectorStore
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag import retrieval
from src.rag.context import RAGContext


def _fixed_contexts() -> list[RAGContext]:
    """构造固定两条 RAGContext，作为 mock 检索/精排的返回。"""
    return [
        RAGContext(
            content="毛利率 40%",
            source="财报.pdf",
            page=1,
            doc_id="doc1",
            chunk_id="doc1:0",
            score=0.9,
        ),
        RAGContext(
            content="营收 100 亿",
            source="财报.pdf",
            page=2,
            doc_id="doc1",
            chunk_id="doc1:1",
            score=0.8,
        ),
    ]


@pytest.fixture
def retrieve_kb(monkeypatch):
    """工厂构建的 retrieve_kb 工具，search/rerank_results 已 mock 为固定返回两条上下文。"""

    async def fake_search(query, kb_id, vector_store):
        """mock search：返回空列表，结果由 fake_rerank 决定。"""
        return []

    def fake_rerank(query, results, reranker):
        """mock rerank_results：返回固定两条 RAGContext。"""
        return _fixed_contexts()

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", fake_rerank)

    # 依赖传 None：search/rerank_results 已被 mock，闭包内依赖不会被真实调用
    return make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]


def _new_state() -> AgentState:
    """构造绑定 KB1 的初始 AgentState（kb_id 由 make_initial_state 携带）。"""
    return AgentState.make_initial_state("s1", "kb1", "毛利率", [])


@pytest.mark.asyncio
async def test_retrieve_kb_global_numbering(retrieve_kb):
    """连续两次调用，第二轮引用编号从 [3] 开始，与第一轮不冲突。"""
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        out1 = await retrieve_kb.ainvoke({"query": "毛利率", "state": _new_state()})
        out2 = await retrieve_kb.ainvoke({"query": "营收", "state": _new_state()})
    finally:
        current_request_ctx.reset(token)

    assert "[1]" in out1 and "[2]" in out1
    assert "[3]" in out2 and "[4]" in out2


@pytest.mark.asyncio
async def test_retrieve_kb_appends_to_collector(retrieve_kb):
    """调用后 current_request_ctx.get().tool_contexts 长度增加。"""
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await retrieve_kb.ainvoke({"query": "毛利率", "state": _new_state()})
        ctx = current_request_ctx.get()
        assert ctx is not None
        assert len(ctx.tool_contexts) == 2
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_retrieve_kb_unbound_returns_empty(monkeypatch):
    """未绑定 KB（kb_id=""）→ 不检索，返回空字符串（KB=RAG 开关硬保证）。"""
    search_called = []

    async def fake_search(query, kb_id, vector_store):
        search_called.append(kb_id)
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", lambda q, r, rk: [])

    tool = make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]

    state = AgentState.make_initial_state("s1", "", "财务年报毛利率多少", [])
    out = await tool.ainvoke({"query": "财务年报毛利率多少", "state": state})

    assert out == ""
    assert search_called == []


@pytest.mark.asyncio
async def test_retrieve_kb_fork_dict_state_falls_back_to_ctx(monkeypatch):
    """fork 子代理场景：state 为普通 dict（create_agent 图无 InjectedState 的 AgentState），
    不抛 AttributeError，kb_id 回退取 current_request_ctx 的 kb_id（子 ctx 已复制）。"""
    search_called = []

    async def fake_search(query, kb_id, vector_store):
        """mock search：记录底层检索被以哪个 kb_id 调用。"""
        search_called.append(kb_id)
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", lambda q, r, rk: [])

    tool = make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]

    ctx = RequestContext(session_id="s1", kb_id="kb_fork")
    token = current_request_ctx.set(ctx)
    try:
        # 子代理图注入的是普通 dict（仅 messages），无 kb_id 键
        out = await tool.ainvoke({"query": "腾讯营收", "state": {"messages": []}})
    finally:
        current_request_ctx.reset(token)

    assert out == ""
    assert search_called == ["kb_fork"]


@pytest.mark.asyncio
async def test_retrieve_kb_rerank_timeout_falls_back_raw_order(monkeypatch):
    """rerank 超时后降级为检索原始顺序上下文，不返回空结果触发 abstain。"""
    from src.agents.tools import rag_tools as rag_tools_mod
    from src.infra.db.vector_store.types import ChunkResult

    async def fake_search(query, kb_id, vector_store):
        """mock search：返回两条带 distance 的 ChunkResult（distance 越小越相似）。"""
        return [
            ChunkResult(
                id="doc1:0",
                content="毛利率 40%",
                distance=0.1,
                metadata={"source": "财报.pdf", "page": 1, "doc_id": "doc1"},
            ),
            ChunkResult(
                id="doc1:1",
                content="营收 100 亿",
                distance=0.2,
                metadata={"source": "财报.pdf", "page": 2, "doc_id": "doc1"},
            ),
        ]

    async def fake_to_thread(fn, *args, **kwargs):
        """mock asyncio.to_thread：模拟 rerank 线程内抛 TimeoutError。"""
        raise TimeoutError

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(rag_tools_mod.asyncio, "to_thread", fake_to_thread)

    tool = make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        out = await tool.ainvoke({"query": "毛利率", "state": _new_state()})
    finally:
        current_request_ctx.reset(token)

    # 降级为 raw order：两条上下文都保留，按检索原始顺序 + 引用编号（KB tier=0 → 内部文档标注）
    assert "[1]" in out and "[2]" in out
    assert "毛利率 40%" in out
    assert "来源: 财报.pdf (第1页, 内部文档)" in out


@pytest.mark.asyncio
async def test_retrieve_kb_timeout_fallback_assigns_tier_zero(monkeypatch):
    """rerank 超时降级分支构造的 RAGContext 显式赋 tier=0（KB 内部文档档）。"""
    from src.agents.tools import rag_tools as rag_tools_mod
    from src.infra.db.vector_store.types import ChunkResult

    async def fake_search(query, kb_id, vector_store):
        """mock search：返回一条带 metadata 的 ChunkResult。"""
        return [
            ChunkResult(
                id="doc1:0",
                content="毛利率 40%",
                distance=0.1,
                metadata={"source": "财报.pdf", "page": 1, "doc_id": "doc1"},
            )
        ]

    async def fake_to_thread(fn, *args, **kwargs):
        """mock asyncio.to_thread：模拟 rerank 线程内抛 TimeoutError。"""
        raise TimeoutError

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(rag_tools_mod.asyncio, "to_thread", fake_to_thread)

    tool = make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await tool.ainvoke({"query": "毛利率", "state": _new_state()})
    finally:
        current_request_ctx.reset(token)

    assert len(ctx.tool_contexts) == 1
    assert ctx.tool_contexts[0].tier == 0


@pytest.mark.asyncio
async def test_retrieve_kb_writes_temporal_years(retrieve_kb, monkeypatch):
    """含相对时间词查询：mock 时间解析后，temporal_years 填充、missing_years 正确计算。"""
    from src.agents.tools import rag_tools

    async def fake_derive_candidate_years(kb_ids: list[str]) -> list[int]:
        """mock 候选年份：知识库只覆盖 2024-2025。"""
        return [2024, 2025]

    async def fake_parse_temporal(query: str, candidates: list[int], llm) -> dict:
        """mock LLM 时间解析：要求覆盖 2023-2025。"""
        return {"years": [2023, 2024, 2025], "has_temporal": True}

    monkeypatch.setattr(
        rag_tools, "derive_candidate_years", fake_derive_candidate_years
    )
    monkeypatch.setattr(rag_tools, "parse_temporal", fake_parse_temporal)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await retrieve_kb.ainvoke(
            {"query": "腾讯这几年业绩怎么样", "state": _new_state()}
        )
        ctx = current_request_ctx.get()
        assert ctx is not None
        assert ctx.temporal_years == [2023, 2024, 2025]
        # 2023 不在候选（知识库未覆盖）→ 判定为缺失
        assert ctx.missing_years == [2023]
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_retrieve_kb_no_temporal_words_skips_parse(retrieve_kb, monkeypatch):
    """不含相对时间词查询：跳过时间解析，temporal_years / missing_years 保持空。"""
    from src.agents.tools import rag_tools

    parse_called = []

    async def fake_derive_candidate_years(kb_ids: list[str]) -> list[int]:
        """mock 候选年份：正常返回候选集。"""
        return [2023, 2024, 2025]

    async def fake_parse_temporal(query: str, candidates: list[int], llm) -> dict:
        """mock LLM 时间解析：记录调用（本测试应不被调用）。"""
        parse_called.append(query)
        return {"years": [2023, 2024, 2025], "has_temporal": True}

    monkeypatch.setattr(
        rag_tools, "derive_candidate_years", fake_derive_candidate_years
    )
    monkeypatch.setattr(rag_tools, "parse_temporal", fake_parse_temporal)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await retrieve_kb.ainvoke({"query": "腾讯 2024 年营收", "state": _new_state()})
        ctx = current_request_ctx.get()
        assert ctx is not None
        assert ctx.temporal_years == []
        assert ctx.missing_years == []
        assert parse_called == []
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_retrieve_kb_temporal_parse_once_per_turn(retrieve_kb, monkeypatch):
    """同一 turn 内多次调用 retrieve_kb：时间解析只执行一次，不重复 DB+LLM，约束保持首次结果。"""
    from src.agents.tools import rag_tools

    derive_calls = []
    parse_calls = []

    async def fake_derive_candidate_years(kb_ids: list[str]) -> list[int]:
        """mock 候选年份：记录调用次数。"""
        derive_calls.append(kb_ids)
        return [2023, 2024, 2025]

    async def fake_parse_temporal(query: str, candidates: list[int], llm) -> dict:
        """mock LLM 时间解析：记录调用次数，返回固定解析结果。"""
        parse_calls.append(query)
        return {"years": [2023, 2024, 2025], "has_temporal": True}

    monkeypatch.setattr(
        rag_tools, "derive_candidate_years", fake_derive_candidate_years
    )
    monkeypatch.setattr(rag_tools, "parse_temporal", fake_parse_temporal)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await retrieve_kb.ainvoke(
            {"query": "腾讯这几年业绩怎么样", "state": _new_state()}
        )
        await retrieve_kb.ainvoke(
            {"query": "腾讯这几年的营收趋势", "state": _new_state()}
        )
        ctx = current_request_ctx.get()
        assert ctx is not None
        # 首次调用解析一次，第二次调用复用结果，不再重复 DB+LLM
        assert len(derive_calls) == 1
        assert len(parse_calls) == 1
        assert ctx.temporal_years == [2023, 2024, 2025]
        assert ctx.missing_years == []
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_retrieve_kb_emits_empty_result_signal(monkeypatch):
    """态 B 检索空 → 产 empty_result 信号（检索行为缺陷信号）。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        """mock retrieval_signal：捕获调用参数。"""
        captured["signal"] = signal
        captured["query"] = query
        captured["iteration"] = iteration
        captured["fields"] = fields

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)

    async def fake_search(query, kb_id, vector_store):
        """mock search：返回空列表（检索空）。"""
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", lambda q, r, rk: [])

    tool = make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        out = await tool.ainvoke({"query": "毛利率", "state": _new_state()})
        assert out == ""  # 空检索返回空串
        assert captured.get("signal") == "empty_result"
        assert captured.get("iteration") == 0
        assert captured["fields"]["kb_id"] == "kb1"
        assert captured["fields"]["result_count"] == 0
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_retrieve_kb_no_signal_unbound(monkeypatch):
    """态 A（未绑定 KB）检索 → 不产 empty_result/reretrieve（设计行为非缺陷）。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        """mock retrieval_signal：捕获调用参数。"""
        captured["signal"] = signal

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)

    async def fake_search(query, kb_id, vector_store):
        """mock search：记录调用（态 A 不应触发搜索）。"""
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", lambda q, r, rk: [])

    tool = make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        state = AgentState.make_initial_state("s1", "", "财务年报毛利率多少", [])
        await tool.ainvoke({"query": "毛利率", "state": state})  # kb_id="" → 态 A
        assert "signal" not in captured  # 未产缺陷信号
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_retrieve_kb_reads_turn_count_when_state_is_mapping(monkeypatch):
    """create_agent 承载下（映射状态）迭代序号取 _turn_count，不得恒为 0。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        """mock retrieval_signal：捕获 iteration 与附带字段。"""
        captured["iteration"] = iteration
        captured["fields"] = fields

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)

    async def fake_search(query, kb_id, vector_store):
        """mock search：返回空列表（触发 empty_result 信号，携带 iteration）。"""
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", lambda q, r, rk: [])

    tool = make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]

    await tool.ainvoke(
        {"query": "q", "state": {"messages": [], "_turn_count": 3, "kb_id": "kb-1"}}
    )

    assert captured["iteration"] == 3, "映射承载下迭代序号不得恒为 0"
    assert captured["fields"]["kb_id"] == "kb-1"


@pytest.mark.asyncio
async def test_retrieve_kb_cli_style_entry_without_request_context(monkeypatch):
    """CLI 入口只把参数放进图输入、不建 ctx ⇒ 装配 seed 的 kb_id 仍被取到。

    等价于 src/cli/check_abstain.py:127 / eval_deepeval.py:129：kb_id 只在图输入里，
    工具不得依赖 current_request_ctx 取 kb_id（否则退化为空、不检索）。
    """
    assert current_request_ctx.get() is None, "本用例刻意不建 ctx"
    seen: dict = {}

    async def fake_search(query, kb_id, vector_store):
        """mock search：记录底层检索被以哪个 kb_id 调用。"""
        seen["kb_id"] = kb_id
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", lambda q, r, rk: [])

    tool = make_rag_tools(
        vector_store=cast(VectorStore, None),
        reranker=None,
        prompt_manager=None,
    )[0]

    await tool.ainvoke(
        {
            "query": "q",
            "state": {"messages": [], "_turn_count": 0, "kb_id": "kb-from-input"},
        }
    )

    assert seen["kb_id"] == "kb-from-input", "无 ctx 入口不得退化为空 kb_id"


@pytest.mark.asyncio
async def test_retrieve_kb_temporal_parse_disabled(retrieve_kb, monkeypatch):
    """TEMPORAL_PARSE_ENABLED=False：含时间词也跳过解析，temporal_years / missing_years 保持空。"""
    from src.agents.tools import rag_tools
    from src.config import settings

    monkeypatch.setattr(settings, "TEMPORAL_PARSE_ENABLED", False)

    parse_called = []

    async def fake_derive_candidate_years(kb_ids: list[str]) -> list[int]:
        """mock 候选年份：正常返回候选集。"""
        return [2023, 2024, 2025]

    async def fake_parse_temporal(query: str, candidates: list[int], llm) -> dict:
        """mock LLM 时间解析：记录调用（本测试应不被调用）。"""
        parse_called.append(query)
        return {"years": [2023, 2024, 2025], "has_temporal": True}

    monkeypatch.setattr(
        rag_tools, "derive_candidate_years", fake_derive_candidate_years
    )
    monkeypatch.setattr(rag_tools, "parse_temporal", fake_parse_temporal)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await retrieve_kb.ainvoke(
            {"query": "腾讯这几年业绩怎么样", "state": _new_state()}
        )
        ctx = current_request_ctx.get()
        assert ctx is not None
        assert ctx.temporal_years == []
        assert ctx.missing_years == []
        assert parse_called == []
    finally:
        current_request_ctx.reset(token)
