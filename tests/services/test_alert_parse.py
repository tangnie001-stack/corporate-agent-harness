"""告警载荷解析、会话派生与文案渲染单测。"""

import json
import uuid

import pytest

from src.config.const import WECOM_NS, AlertTexts
from src.services.alert_service import (
    AlertEvent,
    build_query,
    derive_alert_session_id,
    parse_alert_payload,
    render_fallback,
    render_placeholder,
)

_SAMPLE = {
    "alarmId": "ALM-001",
    "level": "P2",
    "commonLabels": {"alertname": "CPU 使用率过高"},
    "alerts": [
        {"fingerprint": "fp-a", "annotations": {"message": "ecs-1 CPU 92%"}},
        {"fingerprint": "fp-b", "annotations": {"message": "ecs-2 CPU 95%"}},
    ],
}


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


def test_parse_ok() -> None:
    """正常载荷解析出全部字段，指纹逐条收集。"""
    ev = parse_alert_payload(_raw(_SAMPLE))
    assert ev.alarm_id == "ALM-001"
    assert ev.alertname == "CPU 使用率过高"
    assert ev.level == "P2"
    assert ev.fingerprints == ("fp-a", "fp-b")
    assert "ecs-1 CPU 92%" in ev.details
    assert "ecs-2 CPU 95%" in ev.details


def test_parse_bad_json() -> None:
    """非法 JSON ⇒ ValueError。"""
    with pytest.raises(ValueError, match="JSON"):
        parse_alert_payload(b"{not json")


def test_parse_missing_alarm_id() -> None:
    """缺报警标识 ⇒ ValueError（会话 key 依赖它）。"""
    payload = {k: v for k, v in _SAMPLE.items() if k != "alarmId"}
    with pytest.raises(ValueError, match="alarmId"):
        parse_alert_payload(_raw(payload))


def test_parse_no_fingerprint() -> None:
    """无任何 fingerprint ⇒ ValueError（去重依赖它）。"""
    payload = dict(_SAMPLE, alerts=[{"annotations": {"message": "x"}}])
    with pytest.raises(ValueError, match="fingerprint"):
        parse_alert_payload(_raw(payload))


def test_derive_alert_session_id_is_stable_and_isolated() -> None:
    """同 bot_key+alarmId 稳定；与群聊 scope 隔离；bot_key 参与哈希。"""
    a = derive_alert_session_id("dev", "ALM-001")
    assert a == derive_alert_session_id("dev", "ALM-001")
    assert a == str(uuid.uuid5(WECOM_NS, "wecom|dev|alert|ALM-001"))
    assert derive_alert_session_id("dev", "ALM-002") != a
    assert derive_alert_session_id("finance", "ALM-001") != a
    assert len(a) == 36


def test_build_query_uses_template() -> None:
    """query 按模板渲染，含名称/等级/正文。"""
    ev = parse_alert_payload(_raw(_SAMPLE))
    q = build_query(ev)
    assert "CPU 使用率过高" in q
    assert "P2" in q
    assert "ecs-2 CPU 95%" in q


def test_render_fallback_prefix_and_limit() -> None:
    """兜底带前缀、限长、不整包 dump。"""
    ev = parse_alert_payload(_raw(_SAMPLE))
    out = render_fallback(ev, max_chars=40)
    assert out.startswith(AlertTexts.FALLBACK_PREFIX)
    assert len(out) <= 40 + len(AlertTexts.FALLBACK_PREFIX) + 1
    assert "alarmId" not in out  # 不是原始 JSON dump


def test_render_placeholder() -> None:
    """占位文案含告警名称。"""
    ev = parse_alert_payload(_raw(_SAMPLE))
    assert "CPU 使用率过高" in render_placeholder(ev)


def test_alert_event_is_frozen() -> None:
    """AlertEvent 不可变（值对象）。"""
    ev = AlertEvent(
        alarm_id="a", alertname="n", level="P1", details="d", fingerprints=("f",)
    )
    with pytest.raises(Exception):  # noqa: B017
        ev.alarm_id = "b"  # type: ignore[misc]
