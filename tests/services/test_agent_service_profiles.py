"""AgentService 装配回归：外图按档注入日志工具、绑定未注册预设仍有日志。

装配级用真实 AgentService（外部依赖替身，不触 LLM / 网络 / DB），从编译图
agent 节点的闭包取能力档 → 装配产物映射，直接断言工具面。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.presets.capability import DEFAULT_CAPABILITY, LOG_CAPABILITY
from src.core import logging as core_logging
from src.core.log_events import Event
from src.services.agent_service import AgentService

_LOG_TOOL_NAMES = frozenset({"sls_query_latency", "sls_query_requests"})


def _bundles(graph) -> dict:
    """从编译图 agent 节点闭包取出「能力档名 → 装配产物」映射。"""
    node = graph.nodes["agent"]
    for cell in node.bound.afunc.__closure__ or ():
        content = cell.cell_contents
        if isinstance(content, dict):
            return content
    raise AssertionError("agent 节点闭包中未找到 bundles")


def _make_real_service() -> AgentService:
    """构造真实装配的 AgentService（外部依赖全部替身）。"""
    return AgentService(
        MagicMock(),
        MagicMock(),
        llm=MagicMock(),
        reranker=MagicMock(),
        prompt_manager=MagicMock(),
    )


def test_no_clarify_graph_log_bundle_has_log_tools_without_ask_user():
    """no-clarify 图：日志档含两个 sls 工具，基础档不含；两档均无 ask_user。"""
    service = _make_real_service()
    bundles = _bundles(service._graph_no_clarify)

    assert set(bundles) == {DEFAULT_CAPABILITY, LOG_CAPABILITY}

    log_names = bundles[LOG_CAPABILITY].tool_names
    assert _LOG_TOOL_NAMES <= log_names
    assert "ask_user" not in log_names

    default_names = bundles[DEFAULT_CAPABILITY].tool_names
    assert not (_LOG_TOOL_NAMES & default_names)
    assert "ask_user" not in default_names


@pytest.mark.asyncio
async def test_bound_but_unregistered_preset_logs_and_keeps_bound(monkeypatch):
    """已绑定但注册表查不到的预设：记 AGENT_BIND_IGNORED，仍返回绑定值。"""
    service = AgentService.__new__(AgentService)
    service._chat_manager = AsyncMock()
    service._preset_registry = MagicMock()
    service._preset_registry.get.return_value = None

    logged: list[tuple] = []

    def _capture(event, **fields):
        logged.append((event, fields))

    monkeypatch.setattr(core_logging, "log_event", _capture)

    result = await service._resolve_session_agent("s1", "", bound="ghost")

    assert result.effective == "ghost"
    assert result.source == "bound"
    assert (
        Event.AGENT_BIND_IGNORED,
        {"session_id": "s1", "bound": "ghost", "requested": ""},
    ) in logged
