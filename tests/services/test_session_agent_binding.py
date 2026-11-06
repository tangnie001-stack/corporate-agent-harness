"""会话智能体 bind-once：首轮绑定 / 沿用 / 忽略不一致 + warn / 未注册降级。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import settings
from src.config.const import Channel
from src.infra.llm.trace_context import current_channel
from src.services.agent_service import AgentService

pytestmark = pytest.mark.asyncio


def _service(
    bound: str = "", known: list[str] | None = None, channel: str = ""
) -> tuple[AgentService, AsyncMock]:
    """构造只带绑定所需依赖的 AgentService（跳过 __init__）。"""
    svc = AgentService.__new__(AgentService)
    svc._chat_manager = AsyncMock()
    svc._chat_manager.bind_session_agent_async = AsyncMock(return_value=True)
    registry = MagicMock()
    registry.get = MagicMock(
        side_effect=lambda name: (
            MagicMock(name=name, channel=channel) if name in (known or []) else None
        )
    )
    svc._preset_registry = registry
    return svc, svc._chat_manager


async def test_binds_on_first_request():
    """未绑定 + 合法 → 固化并返回该值。"""
    svc, chat_manager = _service(known=["finance-expert"])
    result = await svc._resolve_session_agent("sess_1", "finance-expert")
    assert result.effective == "finance-expert"
    chat_manager.bind_session_agent_async.assert_awaited_once_with(
        "sess_1", "finance-expert"
    )


async def test_empty_request_keeps_unbound():
    """未绑定 + 传入空 → 保持未绑定（不写库）。"""
    svc, chat_manager = _service(known=["finance-expert"])
    result = await svc._resolve_session_agent("sess_1", "")
    assert result.effective == ""
    chat_manager.bind_session_agent_async.assert_not_awaited()


async def test_inconsistent_request_is_ignored_with_warning(monkeypatch):
    """已绑定 + 传入不同值 → 忽略传入值、按绑定值继续，记 warning（不阻断）。"""
    svc, chat_manager = _service(known=["finance-expert", "legal-expert"])
    warned: list[dict] = []
    monkeypatch.setattr(
        "src.services.agent_service.core_logging.log_event",
        lambda event, **fields: warned.append({"event": event, **fields}),
    )

    result = await svc._resolve_session_agent(
        "sess_1", "legal-expert", bound="finance-expert"
    )

    assert result.effective == "finance-expert"
    chat_manager.bind_session_agent_async.assert_not_awaited()
    assert any("agent" in str(item) for item in warned)


async def test_unknown_request_is_ignored():
    """未绑定 + 传入未注册名 → 忽略 + 返回空（降级系统默认）。"""
    svc, _ = _service(known=[])
    result = await svc._resolve_session_agent("sess_1", "ghost")
    assert result.effective == ""


async def test_disabled_switch_ignores_bound_on_wecom(monkeypatch):
    """企微渠道 + 开关关闭：忽略已绑定值，回通用人设。"""
    svc = _service(known=["bot-ops"])[0]
    monkeypatch.setattr(settings, "WECOM_AGENT_ROUTING_ENABLED", False)
    token = current_channel.set(Channel.WECOM)
    try:
        result = await svc._resolve_session_agent("s1", "bot-ops", bound="bot-ops")
    finally:
        current_channel.reset(token)

    assert result.effective == ""
    assert result.source == "routing_disabled"


async def test_disabled_switch_keeps_web_binding(monkeypatch):
    """站点渠道不受开关影响：仍沿用绑定。"""
    svc = _service(known=["finance-expert"])[0]
    monkeypatch.setattr(settings, "WECOM_AGENT_ROUTING_ENABLED", False)
    token = current_channel.set(Channel.WEB)
    try:
        result = await svc._resolve_session_agent("s2", "", bound="finance-expert")
    finally:
        current_channel.reset(token)

    assert result.effective == "finance-expert"
    assert result.source == "bound"


async def test_disabled_switch_does_not_write_binding(monkeypatch):
    """override 不得改写 sessions.agent（回滚可逆）。

    输入取「会走到绑定写入」的组合（未绑定 + 请求合法预设）：开关开启时该路径
    必然 bind（见对照用例），因此本条断言 calls == [] 才有判别力。
    """
    svc, chat_manager = _service(known=["bot-ops"])
    monkeypatch.setattr(settings, "WECOM_AGENT_ROUTING_ENABLED", False)
    calls: list[str] = []

    async def _fake_bind(session_id: str, agent: str) -> None:
        calls.append(agent)

    monkeypatch.setattr(chat_manager, "bind_session_agent_async", _fake_bind)
    token = current_channel.set(Channel.WECOM)
    try:
        result = await svc._resolve_session_agent("s3", "bot-ops", bound="")
    finally:
        current_channel.reset(token)

    assert result.effective == ""
    assert result.source == "routing_disabled"
    assert calls == []


async def test_enabled_switch_writes_binding_on_wecom(monkeypatch):
    """对照：同一输入在开关开启时必须 bind 一次，证明上条用例有判别力。"""
    svc, chat_manager = _service(known=["bot-ops"])
    monkeypatch.setattr(settings, "WECOM_AGENT_ROUTING_ENABLED", True)
    token = current_channel.set(Channel.WECOM)
    try:
        result = await svc._resolve_session_agent("s3", "bot-ops", bound="")
    finally:
        current_channel.reset(token)

    assert result.effective == "bot-ops"
    assert result.source == "new_bound"
    chat_manager.bind_session_agent_async.assert_awaited_once_with("s3", "bot-ops")


async def test_disabled_switch_precedes_channel_check(monkeypatch):
    """开关分支先于渠道校验：顺序颠倒会得到 ignored 而非 routing_disabled。"""
    svc = _service(known=["site-only"], channel=Channel.WEB)[0]
    monkeypatch.setattr(settings, "WECOM_AGENT_ROUTING_ENABLED", False)
    token = current_channel.set(Channel.WECOM)
    try:
        result = await svc._resolve_session_agent("s4", "site-only")
    finally:
        current_channel.reset(token)

    assert result.effective == ""
    assert result.source == "routing_disabled"


async def test_stream_chat_wires_effective_agent():
    """stream_chat 解析生效智能体后写入 ctx.agent 与 launch_context（首轮绑定）。"""
    svc = AgentService.__new__(AgentService)
    chat_manager = AsyncMock()
    chat_manager.get_history_async.return_value = []
    chat_manager.get_session_agent_async.return_value = ""
    chat_manager.get_summary_async.return_value = ("", 0)
    chat_manager.bind_session_agent_async = AsyncMock(return_value=True)
    svc._chat_manager = chat_manager
    registry = MagicMock()
    registry.get = MagicMock(return_value=MagicMock(channel=""))
    svc._preset_registry = registry
    svc._skill_registry = None
    svc._graph = MagicMock()

    _, launch_ctx = await svc.stream_chat("kb1", "s1", "q", agent="finance-expert")

    assert launch_ctx["ctx"].agent == "finance-expert"
    assert launch_ctx["agent"] == "finance-expert"
    chat_manager.bind_session_agent_async.assert_awaited_once_with(
        "s1", "finance-expert"
    )
