"""判定结论的推送与落库同源。"""

import asyncio

from src.config.alert import AlertConfig, AlertTarget
from src.rca.models import Tier, TriageVerdict
from src.rca.pipeline import TriageOutcome
from src.services import alert_service
from src.services.alert_service import AlertEvent, choose_final_text
from src.utils.sse import SSEDoneEvent, SSETokenEvent

_TARGET = AlertTarget(bot_key="dev", chatid="C1")
_EVENT = AlertEvent(
    alarm_id="ALM-001",
    alertname="接口响应时间上升",
    level="P2",
    details="p95 超阈值",
    fingerprints=("fp-a",),
    status="firing",
)
_VERDICT = TriageVerdict(Tier.NO_ACTION, "孤立慢请求", 2, False)


def _cfg() -> AlertConfig:
    return AlertConfig(
        token="tok",
        target=_TARGET,
        dedup_window_seconds=300.0,
        dedup_capacity=16,
        turn_timeout_seconds=5.0,
        fallback_max_chars=200,
    )


def test_card_wins_when_verdict_present():
    assert choose_final_text("模型的自由文本", "卡片文本", _VERDICT) == "卡片文本"


def test_falls_back_without_verdict():
    assert choose_final_text("模型的自由文本", "卡片文本", None) == "模型的自由文本"


def _install(
    monkeypatch, *, outcome: TriageOutcome, sent: list[str], seen: dict
) -> None:
    """替换判定、起轮与通道出口，记录 start_turn 实参与推送。"""

    async def _fake_triage(event, base_query, cfg):
        return outcome

    async def _fake_start_turn(svc, **kwargs):
        seen.update(kwargs)

        async def _events():
            yield SSETokenEvent(token="模型的自由文本")
            yield SSEDoneEvent()

        return type("_Handle", (), {"events": _events()})()

    async def _fake_send(bot_key, chatid, content):
        sent.append(content)
        return True

    async def _fake_svc():
        return object()

    monkeypatch.setattr(alert_service, "run_triage", _fake_triage)
    monkeypatch.setattr(alert_service.turn_runner, "start_turn", _fake_start_turn)
    monkeypatch.setattr(alert_service, "_get_service", _fake_svc, raising=False)
    monkeypatch.setattr(alert_service.wecom_service, "send_to_session", _fake_send)


def test_push_and_persist_share_card_text(monkeypatch):
    """判定成立：推送与落库都取卡片，且喂给 Agent 的是增强 query。"""
    sent: list[str] = []
    seen: dict = {}
    outcome = TriageOutcome("增强后的 query", _VERDICT, "卡片文本")
    _install(monkeypatch, outcome=outcome, sent=sent, seen=seen)

    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET))

    assert seen["query"] == "增强后的 query"
    assert seen["persist_text"] == "卡片文本"
    assert sent[-1] == "卡片文本"


def test_push_and_persist_keep_model_text_without_verdict(monkeypatch):
    """未判定：query 与落库形态均保持原样（persist_text=None）。"""
    sent: list[str] = []
    seen: dict = {}
    outcome = TriageOutcome(alert_service.build_query(_EVENT), None)
    _install(monkeypatch, outcome=outcome, sent=sent, seen=seen)

    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET))

    assert seen["query"] == alert_service.build_query(_EVENT)
    assert seen["persist_text"] is None
    assert sent[-1].strip() == "模型的自由文本"
