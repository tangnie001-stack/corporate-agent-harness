"""告警诊断编排：白名单、三态与上限。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.config.alert import AlertConfig, AlertTarget
from src.rca.locator import ErrorGroup, LocateResult
from src.services import alert_diagnosis


def _cfg(names: frozenset[str]) -> AlertConfig:
    return AlertConfig(
        token="t",
        target=AlertTarget(bot_key="dev", chatid="c1"),
        dedup_window_seconds=300,
        dedup_capacity=10,
        turn_timeout_seconds=10,
        fallback_max_chars=100,
        diagnose_alert_names=names,
        diagnose_window_minutes=15,
    )


def _group(trace_id: str = "trace_a", code: str = "502") -> ErrorGroup:
    return ErrorGroup(
        key=("http error", "/api/chat", code),
        trace_id=trace_id,
        count=2,
        first_ts=datetime.now(UTC),
        summary=f"http error {code}",
    )


def test_gate_only_matches_whitelist():
    cfg = _cfg(frozenset({"api-5xx"}))
    assert alert_diagnosis.should_diagnose_alert("api-5xx", cfg)
    assert not alert_diagnosis.should_diagnose_alert("latency", cfg)


@pytest.mark.asyncio
async def test_failed_source_returns_undetermined(monkeypatch):
    monkeypatch.setattr(
        alert_diagnosis,
        "locate",
        lambda **kwargs: LocateResult("failed", (), (), 0, "日志源不可用"),
    )
    text = await alert_diagnosis.build_alert_card(window_minutes=15, limit=3)
    assert "未判定" in text


@pytest.mark.asyncio
async def test_empty_window_returns_d9_text(monkeypatch):
    monkeypatch.setattr(
        alert_diagnosis,
        "locate",
        lambda **kwargs: LocateResult("empty", (), (), 0, "未落到应用层"),
    )
    text = await alert_diagnosis.build_alert_card(window_minutes=15, limit=3)
    assert "未落到应用层" in text


@pytest.mark.asyncio
async def test_ok_uses_run_diagnosis_per_group(monkeypatch):
    group = _group()
    extra = _group("trace_b", "SESSION_NOT_FOUND")
    monkeypatch.setattr(
        alert_diagnosis,
        "locate",
        lambda **kwargs: LocateResult("ok", (group,), (extra,), 5, ""),
    )

    from src.agents.tools import diagnose_tools

    calls: list[tuple[str, str]] = []

    async def _fake(trace_id: str, kind: str = "answer", question: str = "") -> str:
        calls.append((trace_id, kind))
        return f"CARD {trace_id}"

    monkeypatch.setattr(diagnose_tools, "run_diagnosis", _fake)
    text = await alert_diagnosis.build_alert_card(window_minutes=15, limit=1)
    assert calls == [("trace_a", "5xx")]
    assert "CARD trace_a" in text
    assert "另 1 类" in text
