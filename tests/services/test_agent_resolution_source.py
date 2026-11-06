"""`_resolve_session_agent` 的来源枚举（`AgentResolution.source` 五分支）。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.agent_service import AgentService


def _preset(name: str):
    """构造带真实字段形状的预设替身（channel=\"\" 表示不限渠道）。"""
    preset = MagicMock(name=name)
    preset.channel = ""
    return preset


def _service(known: list[str] | None = None):
    """构造只带注册表与会话管理替身的 AgentService（跳过 __init__）。"""
    svc = AgentService.__new__(AgentService)
    registry = MagicMock()
    registry.get = MagicMock(
        side_effect=lambda name: _preset(name) if name in (known or []) else None
    )
    svc._preset_registry = registry
    svc._chat_manager = MagicMock()
    svc._chat_manager.bind_session_agent_async = AsyncMock()
    return svc, svc._chat_manager


@pytest.mark.asyncio
async def test_source_bound_when_already_bound():
    svc, chat_manager = _service(known=["finance-expert"])
    result = await svc._resolve_session_agent("sess_1", "", bound="finance-expert")
    assert result.effective == "finance-expert"
    assert result.source == "bound"
    chat_manager.bind_session_agent_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_new_bound_on_first_request():
    svc, _ = _service(known=["finance-expert"])
    result = await svc._resolve_session_agent("sess_1", "finance-expert")
    assert result.effective == "finance-expert"
    assert result.source == "new_bound"


@pytest.mark.asyncio
async def test_source_ignored_when_request_conflicts():
    svc, _ = _service(known=["finance-expert", "legal-expert"])
    result = await svc._resolve_session_agent(
        "sess_1", "legal-expert", bound="finance-expert"
    )
    assert result.effective == "finance-expert"
    assert result.source == "ignored"


@pytest.mark.asyncio
async def test_source_unregistered_when_unknown_name():
    svc, _ = _service(known=[])
    result = await svc._resolve_session_agent("sess_1", "ghost")
    assert result.effective == ""
    assert result.source == "unregistered"


@pytest.mark.asyncio
async def test_source_none_when_neither_bound_nor_requested():
    svc, _ = _service(known=["finance-expert"])
    result = await svc._resolve_session_agent("sess_1", "")
    assert result.effective == ""
    assert result.source == "none"
