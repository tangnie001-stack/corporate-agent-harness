"""占位之后的异常必须兜底转发（不得只有占位）。"""

import pytest

from src.config.alert import AlertConfig, AlertTarget
from src.rca.pipeline import TriageOutcome
from src.services import alert_service


def _cfg() -> AlertConfig:
    return AlertConfig(
        token="t",
        target=AlertTarget(bot_key="bot", chatid="chat"),
        dedup_window_seconds=300.0,
        dedup_capacity=10,
        turn_timeout_seconds=5.0,
        fallback_max_chars=100,
    )


def _event() -> alert_service.AlertEvent:
    return alert_service.AlertEvent(
        alarm_id="a1",
        alertname="测试告警",
        level="P1",
        details="详情",
        fingerprints=("fp1",),
        status="firing",
    )


def _no_triage(monkeypatch) -> None:
    """固定走"未判定"分支，避免用例结果依赖 RCA 环境变量。"""

    async def _identity(event, base_query, cfg):
        return TriageOutcome(base_query, None)

    monkeypatch.setattr(alert_service, "run_triage", _identity)


@pytest.mark.asyncio
async def test_turn_start_error_triggers_fallback(monkeypatch):
    """起轮失败（_get_service 抛错）⇒ 占位 + 兜底两条。"""
    sent: list[str] = []

    async def fake_send(target, content):
        sent.append(content)

    async def boom(*args, **kwargs):
        raise RuntimeError("triage exploded")

    _no_triage(monkeypatch)
    monkeypatch.setattr(alert_service, "_safe_send", fake_send)
    monkeypatch.setattr(alert_service, "_get_service", boom)

    cfg = _cfg()
    await alert_service._run_analysis(cfg, _event(), target=cfg.target)
    assert len(sent) == 2
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)


@pytest.mark.asyncio
async def test_triage_crash_triggers_fallback(monkeypatch):
    """判定环节直接抛错（未自吞）⇒ 同样兜底，不允许只剩占位。"""
    sent: list[str] = []

    async def fake_send(target, content):
        sent.append(content)

    async def triage_boom(event, base_query, cfg):
        raise RuntimeError("triage exploded")

    monkeypatch.setattr(alert_service, "_safe_send", fake_send)
    monkeypatch.setattr(alert_service, "run_triage", triage_boom)

    cfg = _cfg()
    await alert_service._run_analysis(cfg, _event(), target=cfg.target)
    assert len(sent) == 2
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)
