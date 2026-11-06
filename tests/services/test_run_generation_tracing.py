"""trace 根：装饰契约与 trace 级字段（D2 / D3）。不发网络（测试期 tracing 已全局关停）。"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest

from src.chat.streaming import StreamingRunManager
from src.infra.llm.request_context import RequestContext
from src.services import agent_service
from src.services.agent_service import _run_generation


async def _fake_astream(*args, **kwargs):
    """最小图事件源：一个 token + 一次 model end。"""
    from tests.services.test_agent_service import (
        _chat_model_end_item,
        _chat_model_stream_item,
    )

    yield _chat_model_stream_item("你好")
    yield _chat_model_end_item("qwen-max")


@pytest.mark.asyncio
async def test_run_generation_records_trace_fields(monkeypatch):
    """根内写入 trace 级 input / session_id，且 input 只含标量。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            captured.setdefault("_obs", []).append(kwargs)

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        # 该 kwarg 由 @observe 包装器在调用前取走，静态签名看不到
        langfuse_observation_id="trace_unit_root",  # type: ignore[reportCallIssue]
    )

    assert captured["session_id"] == "s1"
    assert captured["input"]["query"] == "q"
    assert captured["input"]["kb_id"] == "kb1"
    assert all(
        isinstance(v, (str, bool, int, float, type(None)))
        for v in captured["input"].values()
    )


def test_run_generation_is_observed_without_input_capture():
    """根必须以 capture_input=False 装饰 —— 否则内部对象会被序列化进 trace。"""
    assert '@observe(name="chat_turn", capture_input=False)' in inspect.getsource(
        agent_service
    )


@pytest.mark.asyncio
async def test_run_generation_writes_structured_retrieval_contexts(monkeypatch):
    """收尾把结构化检索上下文并入 trace metadata，供 eval_trace 回放。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.retrieval_trace = [
        {
            "call_index": 1,
            "query": "q",
            "contexts": [{"chunk_id": "d1:0", "entities": {"company": "tencent"}}],
        }
    ]

    await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        langfuse_observation_id="trace_unit_root",  # type: ignore[reportCallIssue]
    )

    assert captured["metadata"]["retrieval_contexts"] == ctx.retrieval_trace


def test_answer_builder_passes_observation_id():
    """调用方必须把当前 trace_id 作为根 observation id 传入。"""
    from src.services import turn_runner

    assert "langfuse_observation_id=" in inspect.getsource(
        turn_runner._make_answer_builder
    )


async def _fake_astream_with_tool(*args, **kwargs):
    """最小事件源：一条 tools 父 span 事件 + 一次工具开始/结束。"""
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
    """循环里每一项都要喂给采集器；收尾必须 close（取消/异常也走这里）。"""
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

    await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        langfuse_observation_id="trace_unit_tools",  # type: ignore[reportCallIssue]
    )

    assert [i["event"] for i in seen] == [
        "on_chain_start",
        "on_tool_start",
        "on_tool_end",
    ]
    assert closed == [True]


@pytest.mark.asyncio
async def test_run_generation_writes_tools_used_metadata(monkeypatch):
    """收尾二次写入 metadata 时必须带上本轮实际调用过的工具名。"""
    calls: list[dict] = []

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            calls.append(kwargs)

        def update_current_observation(self, **kwargs):
            pass

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream_with_tool
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        langfuse_observation_id="trace_unit_tools_used",  # type: ignore[reportCallIssue]
    )

    metadata_calls = [c["metadata"] for c in calls if "metadata" in c]
    assert all("tools_used" in metadata for metadata in metadata_calls)
    assert metadata_calls[-1]["tools_used"] == ["retrieve_kb"]


@pytest.mark.asyncio
async def test_run_generation_writes_user_tags_metadata(monkeypatch):
    """trace 级写入 user_id / 低基数 tags / 业务 metadata；空 user_id 不得写成空串。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            pass

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

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
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        user_id="u-42",
        langfuse_observation_id="trace_unit_enrich",  # type: ignore[reportCallIssue]
    )

    assert captured["user_id"] == "u-42"
    assert captured["tags"] == ["kb", "none", "pf:financial-analyst"]
    metadata = captured["metadata"]
    assert metadata["agent"] == "financial-analyst"
    assert metadata["agent_display_name"] == "财务专家"
    assert metadata["kb_id"] == "kb1"
    assert metadata["kb_domain"] == "finance"
    assert metadata["skill_action"] == "inline"
    assert metadata["loaded_skills"] == ["finance-qa"]
    assert metadata["has_skills"] is True
    # 高基数取值不得进 tags
    assert "kb1" not in captured["tags"]
    assert "financial-analyst" not in captured["tags"]


@pytest.mark.asyncio
async def test_run_generation_writes_channel_metadata(monkeypatch):
    """渠道标识写入 trace metadata.channel（供浏览器按渠道筛 trace）。"""
    from src.config.const import Channel
    from src.infra.llm.trace_context import current_channel

    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            pass

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx.clarify_channel = asyncio.Queue()

    chan_tok = current_channel.set(Channel.WECOM)
    try:
        await _run_generation(
            "s1",
            "kb1",
            "q",
            [],
            False,
            ctx,
            mgr,
            graph=fake_graph,
            langfuse_observation_id="trace_unit_channel",  # type: ignore[reportCallIssue]
        )
    finally:
        current_channel.reset(chan_tok)

    assert captured["metadata"]["channel"] == Channel.WECOM


@pytest.mark.asyncio
async def test_blank_user_id_is_not_written_as_empty_string(monkeypatch):
    """未登录时 current_user_id 是空串；必须转 None，否则空串会被写进 trace。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            pass

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1",
        "",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        user_id="",
        langfuse_observation_id="trace_unit_nouser",  # type: ignore[reportCallIssue]
    )

    assert captured["user_id"] is None
    assert captured["tags"] == ["no_kb", "none", "pf:chat"]


def test_trace_tags_for_alert_channel():
    """告警分析轮次带 alert 标签，profile 档位恒为 pf:alert。"""
    from src.config.const import Channel

    assert agent_service._trace_tags("no_kb", Channel.ALERT, "bot-ops") == [
        "no_kb",
        "alert",
        "pf:alert",
    ]


def test_trace_tags_for_wecom_channel():
    """企微问答轮次带 wecom 标签，profile 取去掉 bot- 前缀的短名。"""
    from src.config.const import Channel

    assert agent_service._trace_tags("kb", Channel.WECOM, "bot-support") == [
        "kb",
        "wecom",
        "pf:support",
    ]


def test_trace_tags_for_web_channel():
    """站点轮次也带渠道标签（web），空 profile 记 pf:chat。"""
    from src.config.const import Channel

    assert agent_service._trace_tags("kb", Channel.WEB, "") == [
        "kb",
        "web",
        "pf:chat",
    ]


def test_trace_tags_normalize_unknown_channel():
    """未知 / 预留渠道归一为 none，保证标签值域可枚举。"""
    from src.config.const import Channel

    assert agent_service._trace_tags("kb", Channel.FEISHU, "bot-ops") == [
        "kb",
        "none",
        "pf:ops",
    ]


async def _astream_tool_then_raise(*args, **kwargs):
    """先产出一次工具调用，随后抛错（验证异常路径也带 tools_used）。"""
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
    """异常路径同样写入本轮已调用的工具名（排障最需要这类轮次）。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            pass

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _astream_tool_then_raise
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx.clarify_channel = asyncio.Queue()

    with pytest.raises(RuntimeError):
        await _run_generation(
            "s1",
            "kb1",
            "q",
            [],
            False,
            ctx,
            mgr,
            graph=fake_graph,
            langfuse_observation_id="trace_unit_fail_tools",  # type: ignore[reportCallIssue]
        )

    assert captured["metadata"]["tools_used"] == ["sls_query_latency"]


@pytest.mark.asyncio
async def test_metadata_write_failure_does_not_fail_generation(monkeypatch):
    """观测写入抛出只告警：不得把观测故障冒泡成生成失败。"""
    captured: dict = {}

    class _FlakyContext:
        def update_current_trace(self, **kwargs):
            if set(kwargs) == {"metadata"}:
                raise RuntimeError("langfuse down")
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            pass

    monkeypatch.setattr(agent_service, "langfuse_context", _FlakyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    ctx.clarify_channel = asyncio.Queue()

    answer = await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        langfuse_observation_id="trace_unit_meta_fail",  # type: ignore[reportCallIssue]
    )

    assert answer == "你好"
    assert captured["metadata"]["tools_used"] == []
