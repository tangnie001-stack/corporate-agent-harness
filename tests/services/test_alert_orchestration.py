"""告警编排：两段推送 / 兜底 / 超时 / 繁忙单测。"""

import asyncio

import pytest

from src.config.alert import AlertConfig, AlertTarget
from src.services import alert_service, turn_runner
from src.services.alert_service import AlertEvent
from src.utils.sse import SSEDoneEvent, SSEStatusEvent, SSETokenEvent

_TARGET = AlertTarget(bot_key="dev", chatid="C1")
_EVENT = AlertEvent(
    alarm_id="ALM-001",
    alertname="CPU 高",
    level="P2",
    details="ecs-1 CPU 92%",
    fingerprints=("fp-a",),
    status="firing",
)
_RECOVERY = AlertEvent(
    alarm_id="ALM-001",
    alertname="CPU 高",
    level="P2",
    details="ecs-1 CPU 92%",
    fingerprints=("fp-a",),
    status="resolved",
)


def _cfg() -> AlertConfig:
    return AlertConfig(
        token="tok",
        target=_TARGET,
        dedup_window_seconds=300.0,
        dedup_capacity=16,
        turn_timeout_seconds=5.0,
        fallback_max_chars=200,
    )


class _Handle:
    """假 TurnHandle：events 为给定的异步生成器。"""

    def __init__(self, events) -> None:
        self.events = events


def _install(monkeypatch, *, events_factory, sent: list[str]) -> None:
    """替换 start_turn 与通道出口，并记录推送。"""

    async def _fake_start_turn(svc, **kwargs):
        assert kwargs["clarify"] is False, "报警轮必须禁用澄清"
        return _Handle(events_factory())

    async def _fake_send(bot_key, chatid, content):
        sent.append(content)
        return True

    async def _fake_svc():
        return object()

    monkeypatch.setattr(alert_service.turn_runner, "start_turn", _fake_start_turn)
    monkeypatch.setattr(alert_service, "_get_service", _fake_svc, raising=False)
    monkeypatch.setattr(alert_service.wecom_service, "send_to_session", _fake_send)


def test_two_stage_push_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功：先占位、后结论（净化正文），两条独立消息。"""
    sent: list[str] = []

    async def _events():
        yield SSETokenEvent(token="旁白：我先检索")
        # 非 token 事件把旁白固化为 preamble，其后 token 才是末轮正文流
        yield SSEStatusEvent(stage="generate", message="生成中")
        yield SSETokenEvent(token="结论正文")
        yield SSEDoneEvent()

    _install(monkeypatch, events_factory=_events, sent=sent)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET))
    assert len(sent) == 2
    assert "CPU 高" in sent[0]  # 占位
    assert sent[1].strip() == "结论正文"  # 净化正文（旁白剔除）


def test_fallback_on_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """结论为空 ⇒ 兜底（带前缀），不推空气泡。"""
    sent: list[str] = []

    async def _events():
        yield SSEDoneEvent()

    _install(monkeypatch, events_factory=_events, sent=sent)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET))
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)


def test_fallback_on_serialize_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """净化过程抛异常 ⇒ 视作空正文走兜底，既不 500 也不静默丢失。"""
    sent: list[str] = []

    async def _events():
        yield SSETokenEvent(token="正文")
        yield SSEDoneEvent()

    def _boom(events_log) -> tuple[str, str]:
        raise RuntimeError("boom")

    _install(monkeypatch, events_factory=_events, sent=sent)
    monkeypatch.setattr(alert_service, "serialize_process", _boom, raising=False)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET))
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)


def test_fallback_on_turn_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """TurnBusy ⇒ 兜底（不 5xx、不静默丢失）。"""
    sent: list[str] = []

    async def _busy(svc, **kwargs):
        raise turn_runner.TurnBusy("busy")

    async def _fake_send(bot_key, chatid, content):
        sent.append(content)
        return True

    async def _fake_svc():
        return object()

    monkeypatch.setattr(alert_service.turn_runner, "start_turn", _busy)
    monkeypatch.setattr(alert_service, "_get_service", _fake_svc, raising=False)
    monkeypatch.setattr(alert_service.wecom_service, "send_to_session", _fake_send)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET))
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)


def test_timeout_cancels_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时 ⇒ 硬取消该轮 + 兜底。"""
    sent: list[str] = []
    cancelled: list[str] = []

    async def _events():
        await asyncio.sleep(3600)
        yield SSEDoneEvent()  # pragma: no cover

    class _Mgr:
        def cancel(self, session_id: str) -> bool:
            cancelled.append(session_id)
            return True

    _install(monkeypatch, events_factory=_events, sent=sent)
    monkeypatch.setattr(alert_service, "streaming_manager", _Mgr(), raising=False)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET))
    assert cancelled, "超时必须调用 streaming_manager.cancel"
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)


def test_no_target_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置目标群 ⇒ 不发送并记 warning，流程不失败。"""
    sent: list[str] = []

    async def _events():
        yield SSETokenEvent(token="正文")
        yield SSEDoneEvent()

    _install(monkeypatch, events_factory=_events, sent=sent)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=None))
    assert sent == []


def test_recovery_pushes_template_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复通知：只推一条模板消息，**不跑 Agent**、不发占位。"""
    sent: list[str] = []

    async def _must_not_run(svc, **kwargs):  # pragma: no cover
        raise AssertionError("恢复通知不得触发 Agent 轮次")

    async def _fake_send(bot_key, chatid, content):
        sent.append(content)
        return True

    monkeypatch.setattr(alert_service.turn_runner, "start_turn", _must_not_run)
    monkeypatch.setattr(alert_service.wecom_service, "send_to_session", _fake_send)

    asyncio.run(alert_service._push_recovery(_RECOVERY, target=_TARGET))

    assert len(sent) == 1
    assert sent[0].startswith(alert_service.AlertTexts.RECOVERY_PREFIX)
    assert "CPU 高" in sent[0]


def test_recovery_no_target_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复但未配置目标群 ⇒ 不发送、不抛。"""
    sent: list[str] = []

    async def _fake_send(bot_key, chatid, content):
        sent.append(content)
        return True

    monkeypatch.setattr(alert_service.wecom_service, "send_to_session", _fake_send)
    asyncio.run(alert_service._push_recovery(_RECOVERY, target=None))
    assert sent == []
