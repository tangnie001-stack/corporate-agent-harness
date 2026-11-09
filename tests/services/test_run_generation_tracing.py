"""trace 根：装饰契约与 trace 级字段（D2 / D3）。不发网络（测试期 tracing 已全局关停）。"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest

from src.chat.streaming import StreamingRunManager
from src.infra.llm.request_context import RequestContext
from src.services import agent_service
from src.services.agent_service import _run_generation


class _SpyTrace:
    """替代 update_current_trace 的调用记录器（v5 无 langfuse_context）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)

    @property
    def first(self) -> dict:
        return self.calls[0] if self.calls else {}

    @property
    def metadata_calls(self) -> list[dict]:
        return [c["metadata"] for c in self.calls if "metadata" in c]


def _patch_trace(monkeypatch) -> _SpyTrace:
    spy = _SpyTrace()
    monkeypatch.setattr(agent_service, "update_current_trace", spy)
    return spy


async def _fake_astream(*args, **kwargs):
    from tests.services.test_agent_service import (
        _chat_model_end_item,
        _chat_model_stream_item,
    )

    yield _chat_model_stream_item("你好")
    yield _chat_model_end_item("qwen-max")


@pytest.mark.asyncio
async def test_run_generation_records_trace_fields(monkeypatch):
    spy = _patch_trace(monkeypatch)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    first = spy.first
    assert first["session_id"] == "s1"
    assert first["input"]["query"] == "q"
    assert first["input"]["kb_id"] == "kb1"
    assert all(
        isinstance(v, (str, bool, int, float, type(None)))
        for v in first["input"].values()
    )


def test_run_generation_is_observed_with_trace():
    """根必须经 observe_with_trace 显式绑定 trace，且不再使用 v2 的 id kwarg。"""
    src = inspect.getsource(agent_service)
    assert '@observe_with_trace("chat_turn")' in src
    assert "langfuse_observation_id" not in src


@pytest.mark.asyncio
async def test_run_generation_writes_structured_retrieval_contexts(monkeypatch):
    spy = _patch_trace(monkeypatch)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.retrieval_trace = [
        {"call_index": 1, "query": "q", "contexts": [{"chunk_id": "d1:0"}]}
    ]

    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    assert spy.metadata_calls[-1]["retrieval_contexts"] == ctx.retrieval_trace


def test_answer_builder_does_not_pass_observation_id():
    """后台任务不再传 langfuse_observation_id；根绑定由装饰器负责。"""
    from src.services import turn_runner

    assert "langfuse_observation_id" not in inspect.getsource(
        turn_runner._make_answer_builder
    )


async def _fake_astream_with_tool(*args, **kwargs):
    yield {
        "event": "on_chain_start",
        "name": "tools",
        "run_id": "rc",
        "metadata": {"langgraph_node": "tools"},
        "data": {},
    }
    yield {
        "event": "on_tool_start",
        "name": "retrieve_kb",
        "run_id": "r1",
        "metadata": {"langgraph_node": "tools"},
        "data": {"input": {"query": "q"}},
    }
    yield {
        "event": "on_tool_end",
        "name": "retrieve_kb",
        "run_id": "r1",
        "metadata": {"langgraph_node": "tools"},
        "data": {"output": "ok"},
    }


@pytest.mark.asyncio
async def test_run_generation_feeds_collector_and_closes(monkeypatch):
    seen: list[dict] = []
    closed: list[bool] = []

    class _SpyCollector:
        def __init__(self, *, enabled, trace_id, client=None):
            self.enabled = enabled
            self.trace_id = trace_id
            self.tools_used = []

        def consume(self, item):
            seen.append(item)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(agent_service, "ToolTraceCollector", _SpyCollector)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream_with_tool
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    assert [i["event"] for i in seen] == [
        "on_chain_start",
        "on_tool_start",
        "on_tool_end",
    ]
    assert closed == [True]


@pytest.mark.asyncio
async def test_run_generation_writes_tools_used_metadata(monkeypatch):
    spy = _patch_trace(monkeypatch)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream_with_tool
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    assert all("tools_used" in m for m in spy.metadata_calls)
    assert spy.metadata_calls[-1]["tools_used"] == ["retrieve_kb"]


@pytest.mark.asyncio
async def test_run_generation_writes_user_tags_metadata(monkeypatch):
    spy = _patch_trace(monkeypatch)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1", kb_id="kb1", kb_domain="finance")
    ctx.agent = "financial-analyst"
    ctx.agent_display_name = "财务专家"
    ctx.loaded_skills = ["finance-qa"]
    ctx.skill_action = "inline"
    ctx.has_skills = True
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph, user_id="u-42"
    )

    first = spy.first
    assert first["user_id"] == "u-42"
    assert first["tags"] == ["kb", "none", "pf:financial-analyst"]
    md = first["metadata"]
    assert md["agent"] == "financial-analyst"
    assert md["kb_id"] == "kb1"
    assert md["loaded_skills"] == ["finance-qa"]
    assert "kb1" not in first["tags"]


@pytest.mark.asyncio
async def test_run_generation_writes_channel_metadata(monkeypatch):
    from src.config.const import Channel
    from src.infra.llm.trace_context import current_channel

    spy = _patch_trace(monkeypatch)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx.clarify_channel = asyncio.Queue()

    tok = current_channel.set(Channel.WECOM)
    try:
        await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)
    finally:
        current_channel.reset(tok)

    assert spy.first["metadata"]["channel"] == Channel.WECOM


@pytest.mark.asyncio
async def test_blank_user_id_is_not_written_as_empty_string(monkeypatch):
    spy = _patch_trace(monkeypatch)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1", "", "q", [], False, ctx, mgr, graph=fake_graph, user_id=""
    )

    assert spy.first["user_id"] is None
    assert spy.first["tags"] == ["no_kb", "none", "pf:chat"]


def test_trace_tags_for_alert_channel():
    from src.config.const import Channel

    assert agent_service._trace_tags("no_kb", Channel.ALERT, "bot-ops") == [
        "no_kb",
        "alert",
        "pf:alert",
    ]


def test_trace_tags_for_wecom_channel():
    from src.config.const import Channel

    assert agent_service._trace_tags("kb", Channel.WECOM, "bot-support") == [
        "kb",
        "wecom",
        "pf:support",
    ]


def test_trace_tags_for_web_channel():
    from src.config.const import Channel

    assert agent_service._trace_tags("kb", Channel.WEB, "") == ["kb", "web", "pf:chat"]


def test_trace_tags_normalize_unknown_channel():
    from src.config.const import Channel

    assert agent_service._trace_tags("kb", Channel.FEISHU, "bot-ops") == [
        "kb",
        "none",
        "pf:ops",
    ]


async def _astream_tool_then_raise(*args, **kwargs):
    from tests.services.test_agent_service import _chat_model_stream_item

    yield _chat_model_stream_item("部分回答")
    yield {
        "event": "on_tool_start",
        "name": "sls_query_latency",
        "run_id": "r1",
        "data": {},
        "metadata": {"langgraph_node": "tools"},
        "parent_ids": [],
    }
    raise RuntimeError("upstream down")


@pytest.mark.asyncio
async def test_tools_used_written_on_failure_path(monkeypatch):
    spy = _patch_trace(monkeypatch)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _astream_tool_then_raise
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx.clarify_channel = asyncio.Queue()

    with pytest.raises(RuntimeError):
        await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    assert spy.metadata_calls[-1]["tools_used"] == ["sls_query_latency"]


@pytest.mark.asyncio
async def test_metadata_write_failure_does_not_fail_generation(monkeypatch):
    captured: dict = {}

    def _flaky(**kwargs):
        if set(kwargs) == {"metadata"}:
            raise RuntimeError("langfuse down")
        captured.update(kwargs)

    monkeypatch.setattr(agent_service, "update_current_trace", _flaky)
    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx.clarify_channel = asyncio.Queue()

    answer = await _run_generation(
        "s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph
    )

    assert answer == "你好"
    assert captured["metadata"]["tools_used"] == []
