"""ToolTraceCollector：事件 → span 的配对、归组、错误与兜底（client 为替身）。"""

from typing import Any

from langchain_core.messages import ToolMessage

from src.infra.llm.tool_trace import (
    ToolTraceCollector,
    current_delegate_span_id,
    take_delegate_tool_span,
)


class _FakeSpan:
    """替身 span：记录构造参数与 end() 收到的字段。"""

    def __init__(
        self,
        span_id: str,
        name: str,
        parent_id: str | None,
        trace_id: str | None,
        input: Any,
        metadata: dict | None,
    ):
        self.id = span_id
        self.name = name
        self.parent_observation_id = parent_id
        self.trace_id = trace_id
        self.input = input
        self.metadata = metadata
        self.ended: dict | None = None

    def update(self, **kwargs):
        self.ended = {**(self.ended or {}), **kwargs}

    def end(self):
        pass


class _FakeClient:
    """替身客户端：只实现 start_observation 这一条被用到的路径。"""

    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def start_observation(
        self, *, trace_context=None, name="", as_type="span", input=None, metadata=None
    ):
        ctx = trace_context or {}
        span = _FakeSpan(
            f"s{len(self.spans)}",
            name,
            ctx.get("parent_span_id"),
            ctx.get("trace_id"),
            input,
            metadata,
        )
        self.spans.append(span)
        return span

    def get_current_observation_id(self) -> str | None:
        """替身：主链路没有环境 span（真实客户端返回当前 OTel span id）。"""
        return None


def _item(event: str, name: str = "", run_id: str = "r1", **data):
    """构造一条 astream_events item（只含被测代码读取的键）。"""
    return {
        "event": event,
        "name": name,
        "run_id": run_id,
        "metadata": {"langgraph_node": "tools"},
        "data": data,
    }


def test_pairs_tool_span_by_run_id_and_nests_under_round():
    """chain 事件开合父 span；工具 span 以它为父，并按 run_id 配对 end。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(
        _item("on_tool_start", name="retrieve_kb", run_id="rA", input={"query": "q"})
    )
    collector.consume(_item("on_chain_end", name="tools"))
    collector.consume(
        _item("on_tool_end", name="retrieve_kb", run_id="rA", output="[1] 来源…")
    )

    round_span, tool_span = client.spans
    assert round_span.name == "tools"
    assert tool_span.name == "retrieve_kb"
    assert tool_span.parent_observation_id == round_span.id
    assert tool_span.ended is not None
    assert tool_span.ended["output"] == "[1] 来源…"
    assert round_span.ended is not None


def test_parallel_tools_share_one_parent():
    """同一轮并行多个工具：各自成 span，父同为一个 round span。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="a", run_id="r1", input={}))
    collector.consume(_item("on_tool_start", name="b", run_id="r2", input={}))
    collector.consume(_item("on_tool_end", name="a", run_id="r1", output="A"))
    collector.consume(_item("on_tool_end", name="b", run_id="r2", output="B"))

    assert len(client.spans) == 3
    round_span = client.spans[0]
    assert client.spans[1].parent_observation_id == round_span.id
    assert client.spans[2].parent_observation_id == round_span.id


def test_tool_message_output_is_unpacked():
    """data.output 是 ToolMessage 对象时必须显式取字段，不能整对象塞进 span。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1", input={}))
    collector.consume(
        _item(
            "on_tool_end",
            name="retrieve_kb",
            run_id="r1",
            output=ToolMessage(
                content="[1] 来源…", tool_call_id="call_1", name="retrieve_kb"
            ),
        )
    )

    tool_span = client.spans[-1]
    assert tool_span.ended is not None
    assert tool_span.ended["output"] == {
        "tool_call_id": "call_1",
        "name": "retrieve_kb",
        "content": "[1] 来源…",
    }


def test_tool_error_marks_error_level():
    """工具抛错（无 on_tool_end）也要收尾，并标 ERROR。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1", input={}))
    collector.consume(
        _item(
            "on_tool_error",
            name="retrieve_kb",
            run_id="r1",
            error=RuntimeError("boom"),
            tool_call_id="call_1",
        )
    )

    tool_span = client.spans[-1]
    assert tool_span.ended is not None
    assert tool_span.ended["level"] == "ERROR"
    assert "boom" in tool_span.ended["status_message"]


def test_close_ends_leftovers():
    """取消/异常路径：close() 必须关掉未结束的 span，不留悬空节点。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1", input={}))
    collector.close()

    assert all(span.ended is not None for span in client.spans)


def test_disabled_collector_produces_nothing():
    """LANGFUSE_ENABLE=false 时整条路径短路，一次 span 都不建。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=False, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="a", run_id="r1", input={}))
    collector.close()

    assert client.spans == []


def test_events_outside_tools_node_are_ignored():
    """只认 metadata.langgraph_node == 'tools' 的事件（与 _convert_event 同口径）。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    item = _item("on_tool_start", name="retrieve_kb", run_id="r1", input={})
    # 非 tools 节点（模型节点）的事件不误收：判据只认 "tools"
    item["metadata"] = {"langgraph_node": "model"}
    collector.consume(item)

    assert client.spans == []


def test_blank_trace_id_disables_collector():
    """trace_id 为空（无根）时不建 span —— 命令式路径没有 trace 可挂。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="", client=client)

    collector.consume(_item("on_tool_start", name="a", run_id="r1", input={}))

    assert client.spans == []


def test_chain_event_with_other_name_does_not_open_or_close_round():
    """chain 事件按节点名判别：非 'tools' 名既不建父也不关父。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="nested_chain"))
    assert client.spans == []

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_chain_end", name="nested_chain"))
    assert len(client.spans) == 1
    round_span = client.spans[0]
    assert round_span.ended is None

    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="rA", input={}))
    assert client.spans[-1].parent_observation_id == round_span.id


def test_duplicate_tools_chain_start_opens_single_parent():
    """同一轮内重复的 name=='tools' on_chain_start 只建一个父 span。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_chain_start", name="tools"))

    assert len(client.spans) == 1


def test_spans_carry_trace_id_and_tool_input():
    """span 一律带 trace_id；工具 span 记录事件的 input（观测面契约）。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(
        _item("on_tool_start", name="retrieve_kb", run_id="rA", input={"query": "q"})
    )

    round_span, tool_span = client.spans
    assert round_span.trace_id == "t1"
    assert tool_span.trace_id == "t1"
    assert tool_span.input == {"query": "q"}


def test_default_scope_is_main_unchanged():
    """缺省参数下行为与既有完全一致（回归）。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)
    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1"))
    collector.consume(_item("on_tool_end", run_id="r1", output="ok"))
    collector.close()
    assert [s.name for s in client.spans] == ["tools", "retrieve_kb"]
    assert client.spans[0].parent_observation_id is None


def test_delegate_scope_prefixes_names_and_nests_under_parent():
    """委派域：名字带前缀，且挂在该次委派的父 span 之下。"""
    client = _FakeClient()
    collector = ToolTraceCollector(
        enabled=True,
        trace_id="t1",
        client=client,
        scope="delegate",
        name_prefix="delegate:",
    )
    parent = collector.open_delegate_span(delegate_id="d1", skill="analyst")
    assert parent is not None
    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1"))
    collector.consume(_item("on_tool_end", run_id="r1", output="ok"))
    collector.close()
    names = [s.name for s in client.spans]
    assert names == ["delegate", "delegate:tools", "delegate:retrieve_kb"]
    assert client.spans[0].metadata == {"delegate_id": "d1", "skill": "analyst"}
    assert client.spans[1].parent_observation_id == parent.id  # round 挂在父下
    assert (
        client.spans[2].parent_observation_id == client.spans[1].id
    )  # 工具挂在 round 下


def test_delegate_span_failure_does_not_break_conversation():
    """观测失败不影响对话：父 span 建不出来时返回 None，工具 span 仍可产出。"""

    class _BrokenClient(_FakeClient):
        def start_observation(self, **kwargs):
            if kwargs.get("name") == "delegate":
                raise RuntimeError("langfuse down")
            return super().start_observation(**kwargs)

    collector = ToolTraceCollector(
        enabled=True, trace_id="t1", client=_BrokenClient(), scope="delegate"
    )
    assert collector.open_delegate_span(delegate_id="d1", skill="s") is None
    collector.close()  # 不得抛


def test_collector_records_tool_names_even_when_disabled():
    """tools_used 是元数据而非 span：观测关闭时仍记录，且去重、稳定排序。"""
    collector = ToolTraceCollector(enabled=False, trace_id="t")
    collector.consume(
        {
            "event": "on_tool_start",
            "name": "sls_query_latency",
            "run_id": "r1",
            "data": {},
        }
    )
    collector.consume(
        {"event": "on_tool_start", "name": "retrieve_kb", "run_id": "r2", "data": {}}
    )
    collector.consume(
        {"event": "on_tool_start", "name": "retrieve_kb", "run_id": "r3", "data": {}}
    )
    assert collector.tools_used == ["retrieve_kb", "sls_query_latency"]


def test_tools_used_empty_when_no_tool_called():
    """未发生工具调用时返回空列表（trace metadata 恒有该字段）。"""
    collector = ToolTraceCollector(enabled=False, trace_id="t")
    assert collector.tools_used == []


def test_delegate_span_nests_under_delegate_task_tool():
    """委派 span 挂在触发它的 delegate_task 工具 span 下，并在运行期暴露为当前委派父。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(
        _item("on_tool_start", name="delegate_task", run_id="rD", input={"task": "x"})
    )
    tool_span = client.spans[-1]

    delegate = collector.open_delegate_span("d1", "bot-dev")

    assert delegate is not None
    assert delegate.parent_observation_id == tool_span.id
    assert current_delegate_span_id() == delegate.id

    collector.close()
    assert current_delegate_span_id() is None


def test_delegate_scope_round_nests_under_delegate_span():
    """委派域的轮次 span 挂在 delegate span 下（子代理工具不落到主链路）。"""
    client = _FakeClient()
    collector = ToolTraceCollector(
        enabled=True,
        trace_id="t1",
        client=client,
        scope="delegate",
        name_prefix="delegate:",
    )

    delegate = collector.open_delegate_span("d1", "bot-dev")
    assert delegate is not None
    collector.consume(_item("on_chain_start", name="tools"))

    assert client.spans[-1].parent_observation_id == delegate.id
    collector.close()


def test_delegate_tool_span_pairs_once_per_trace():
    """委派工具 span 按 trace 登记，取一次即出队（下一个委派不会复用）。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_tool_start", name="delegate_task", run_id="rD"))
    tool_span = client.spans[-1]

    assert take_delegate_tool_span("t1") == tool_span.id
    assert take_delegate_tool_span("t1") is None
    collector.close()
