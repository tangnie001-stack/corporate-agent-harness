"""外图多档装配测试：build_graph 按能力档建 bundle，agent 节点按 profile 选档。

用假装配产物（_RecordingAgent）与占位工具，只验「选到哪一档」与工具面快照，
不触真实 LLM / 网络。
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import src.agents.graph.agent_node as agent_node_mod
from src.agents.graph.agent_node import AgentLoopBundle, make_agent_loop_node
from src.agents.graph.state import AgentState
from src.agents.graph.workflow import build_graph
from src.agents.presets.capability import (
    DEFAULT_CAPABILITY,
    LOG_CAPABILITY,
    LOG_PROFILE_MAX_TURNS,
)
from src.config.const import MAX_AGENT_ITERATIONS


class _RecordingAgent:
    """记录子图输入并返回「输入 + 固定新增段」的假装配产物。"""

    def __init__(self, answer: str) -> None:
        """记录固定新增段（一条 AIMessage）。"""
        self.inputs: list[dict] = []
        self._answer = answer

    async def ainvoke(self, payload: dict) -> dict:
        """记录输入，并在输入消息之后追加一条 AIMessage 返回。"""
        self.inputs.append(payload)
        return {"messages": [*payload["messages"], AIMessage(content=self._answer)]}


def _bundle(inner: _RecordingAgent, tool_names: frozenset[str]) -> AgentLoopBundle:
    """按给定工具面快照组装产物束（prompt_manager 用 MagicMock，被 spy 换掉）。"""
    return AgentLoopBundle(
        agent=inner,
        prompt_manager=MagicMock(),
        tool_names=tool_names,
    )


def _make_tool(name: str):
    """按名造一个无参占位工具（只用于工具面快照断言）。"""

    def _noop() -> str:
        """占位工具。"""
        return ""

    return tool(name)(_noop)


@pytest.mark.asyncio
async def test_agent_node_selects_bundle_by_profile(monkeypatch):
    """state.profile 落在日志档时，节点用日志档 bundle（工具段取该档工具名）。"""
    captured: list[frozenset[str]] = []

    def _spy(state_obj, prompt_manager, tool_names):
        captured.append(tool_names)
        return [], []

    monkeypatch.setattr(agent_node_mod, "_split_initial_messages", _spy)

    default_inner = _RecordingAgent("默认档回答")
    log_inner = _RecordingAgent("日志档回答")
    node = make_agent_loop_node(
        {
            DEFAULT_CAPABILITY: _bundle(default_inner, frozenset({"retrieve_kb"})),
            LOG_CAPABILITY: _bundle(
                log_inner, frozenset({"retrieve_kb", "sls_query_latency"})
            ),
        }
    )
    state = AgentState.make_initial_state("s", "", "q", [], profile="bot-ops")

    out = await node(state)

    assert captured == [frozenset({"retrieve_kb", "sls_query_latency"})]
    assert len(log_inner.inputs) == 1, "日志档 profile 必须走日志档装配产物"
    assert default_inner.inputs == [], "日志档 profile 不得走基础档装配产物"
    assert out["answer"] == "日志档回答"


@pytest.mark.asyncio
async def test_agent_node_unknown_profile_falls_back_to_default(monkeypatch):
    """未知 profile（含空串）回落到 default_key 对应的基础档。"""
    captured: list[frozenset[str]] = []

    def _spy(state_obj, prompt_manager, tool_names):
        captured.append(tool_names)
        return [], []

    monkeypatch.setattr(agent_node_mod, "_split_initial_messages", _spy)

    default_inner = _RecordingAgent("默认档回答")
    log_inner = _RecordingAgent("日志档回答")
    node = make_agent_loop_node(
        {
            DEFAULT_CAPABILITY: _bundle(default_inner, frozenset({"retrieve_kb"})),
            LOG_CAPABILITY: _bundle(log_inner, frozenset({"retrieve_kb"})),
        }
    )
    state = AgentState.make_initial_state("s", "", "q", [], profile="no-such-profile")

    out = await node(state)

    assert captured == [frozenset({"retrieve_kb"})]
    assert len(default_inner.inputs) == 1
    assert log_inner.inputs == []
    assert out["answer"] == "默认档回答"


def test_build_graph_builds_bundle_per_capability(monkeypatch):
    """build_graph 为基础档与增量档各建一份 bundle，轮次预算按档取。"""
    built: list[dict] = []

    def fake_build_agent(
        llm, tools, *, system=None, max_turns=None, middleware_extra=None
    ):
        built.append(
            {
                "tool_names": [str(t.name) for t in tools],
                "max_turns": max_turns,
            }
        )
        return MagicMock()

    captured_bundles: list[dict] = []

    async def _noop_node(state):
        return {}

    def fake_make_node(bundles, default_key=DEFAULT_CAPABILITY):
        captured_bundles.append(dict(bundles))
        return _noop_node

    monkeypatch.setattr("src.agents.graph.workflow.build_agent", fake_build_agent)
    monkeypatch.setattr(
        "src.agents.graph.workflow.make_agent_loop_node", fake_make_node
    )

    base_tool = _make_tool("retrieve_kb")
    log_tool = _make_tool("sls_query_latency")
    sink: list = []

    build_graph(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        tools=[base_tool],
        tool_sink=sink,
        profile_tools={LOG_CAPABILITY: [log_tool]},
        max_turns_by_profile={LOG_CAPABILITY: LOG_PROFILE_MAX_TURNS},
    )

    bundles = captured_bundles[0]
    assert set(bundles) == {DEFAULT_CAPABILITY, LOG_CAPABILITY}
    assert bundles[DEFAULT_CAPABILITY].tool_names == frozenset({"retrieve_kb"})
    assert bundles[LOG_CAPABILITY].tool_names == frozenset(
        {"retrieve_kb", "sls_query_latency"}
    )
    assert [b["max_turns"] for b in built] == [
        MAX_AGENT_ITERATIONS,
        LOG_PROFILE_MAX_TURNS,
    ]
    assert sink == [base_tool], "tool_sink 只收基础工具集：日志工具不进 fork 池"


def test_build_graph_without_profile_tools_builds_only_default(monkeypatch):
    """缺省 profile_tools 时行为不变：只建基础档，预算取 MAX_AGENT_ITERATIONS。"""
    captured_bundles: list[dict] = []

    def fake_build_agent(
        llm, tools, *, system=None, max_turns=None, middleware_extra=None
    ):
        return MagicMock()

    async def _noop_node(state):
        return {}

    def fake_make_node(bundles, default_key=DEFAULT_CAPABILITY):
        captured_bundles.append(dict(bundles))
        return _noop_node

    monkeypatch.setattr("src.agents.graph.workflow.build_agent", fake_build_agent)
    monkeypatch.setattr(
        "src.agents.graph.workflow.make_agent_loop_node", fake_make_node
    )

    build_graph(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        tools=[_make_tool("retrieve_kb")],
    )

    assert set(captured_bundles[0]) == {DEFAULT_CAPABILITY}
