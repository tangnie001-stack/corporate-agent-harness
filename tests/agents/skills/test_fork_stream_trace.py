"""fork 子代理事件流 → 委派域 Langfuse span（父 span 标注 + 工具 span 挂靠）。"""

import pytest

from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.fork_stream import consume_fork_events
from src.infra.llm.request_context import RequestContext


class _FakeSpan:
    def __init__(self, span_id, name, parent_id, metadata=None):
        self.id = span_id
        self.name = name
        self.parent_observation_id = parent_id
        self.metadata = metadata or {}
        self.ended = None

    def update(self, **kwargs):
        self.ended = {**(self.ended or {}), **kwargs}

    def end(self):
        pass


class _FakeClient:
    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def start_observation(self, **kwargs):
        ctx = kwargs.get("trace_context") or {}
        span = _FakeSpan(
            f"s{len(self.spans)}",
            kwargs.get("name", ""),
            ctx.get("parent_span_id"),
            kwargs.get("metadata"),
        )
        self.spans.append(span)
        return span

    def get_current_observation_id(self) -> str | None:
        """替身：当前没有主链路的环堢 span。"""
        return None


class _Chunk:
    def __init__(self, text):
        self.content = text
        self.additional_kwargs = {}


class _SubAgentWithTool:
    """替身子代理：先调用一次工具，再产出正文。"""

    async def astream_events(self, inputs, config=None, version="v2"):
        yield {
            "event": "on_chain_start",
            "name": "tools",
            "metadata": {"langgraph_node": "tools"},
        }
        yield {
            "event": "on_tool_start",
            "name": "retrieve_kb",
            "run_id": "r1",
            "metadata": {"langgraph_node": "tools"},
            "data": {"input": {"query": "营收"}},
        }
        yield {
            "event": "on_tool_end",
            "run_id": "r1",
            "metadata": {"langgraph_node": "tools"},
            "data": {"output": "检索结果"},
        }
        yield {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("子代理结论")}}


@pytest.mark.asyncio
async def test_fork_events_feed_delegate_collector(monkeypatch):
    """消费子代理事件时同步喂给委派域采集器：父 span 标注 + 工具 span 挂其下。"""
    from src.infra.llm.tool_trace import ToolTraceCollector

    client = _FakeClient()
    collector = ToolTraceCollector(
        enabled=True,
        trace_id="t1",
        client=client,
        scope="delegate",
        name_prefix="delegate:",
    )
    run = DelegateRun(
        delegate_id="d1", skill_name="analyst", ctx=RequestContext(session_id="s1")
    )
    # 生产里由 _run_fork 先开委派父 span，再消费事件；此处照做
    parent = collector.open_delegate_span(run.delegate_id, "analyst")
    assert parent is not None
    text = await consume_fork_events(
        _SubAgentWithTool(),
        run,
        "任务",
        "analyst",
        max_turns=5,
        trace_collector=collector,
    )
    collector.close()
    assert text == "子代理结论"
    names = [s.name for s in client.spans]
    assert names == ["delegate", "delegate:tools", "delegate:retrieve_kb"]
    assert client.spans[1].parent_observation_id == parent.id  # round 挂在委派父下
    assert (
        client.spans[2].parent_observation_id == client.spans[1].id
    )  # 工具挂在 round 下


@pytest.mark.asyncio
async def test_no_collector_is_noop():
    """不传采集器时行为与既有完全一致（既有路径不受影响）。"""
    run = DelegateRun(
        delegate_id="d1", skill_name="analyst", ctx=RequestContext(session_id="s1")
    )
    text = await consume_fork_events(
        _SubAgentWithTool(), run, "任务", "analyst", max_turns=5
    )
    assert text == "子代理结论"
