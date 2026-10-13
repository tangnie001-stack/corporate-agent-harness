"""告警载荷解析、会话派生与文案渲染单测。"""

import dataclasses
import json
import uuid

import pytest

from src.config.const import WECOM_NS, AlertTexts
from src.services.alert_service import (
    AlertEvent,
    build_query,
    dedup_keys,
    derive_alert_session_id,
    parse_alert_payload,
    render_fallback,
    render_placeholder,
    render_recovery,
)

# 真实抓到的 Grafana → webhook 载荷（字段按实测裁剪，结构不动）
_GRAFANA = {
    "receiver": "阿里云ARMS-grafana",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "_aliyun_arms_alert_level": "critical",
                "alertname": "接口请求pv测试报警",
                "severity": "critical",
            },
            "annotations": {
                "description": "接口状态503的量上升",
                "summary": "接口状态503的量上升",
            },
            "fingerprint": "af1f08f7368a765e",
        }
    ],
    "commonLabels": {
        "_aliyun_arms_alert_level": "critical",
        "alertname": "接口请求pv测试报警",
        "severity": "critical",
    },
    "commonAnnotations": {"summary": "接口状态503的量上升"},
    "title": "[FIRING:1] (接口请求pv测试报警 critical)",
    "state": "alerting",
    "message": "**Firing**\n\nValue: F=1835, G=1\n",
}


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


def test_parse_real_grafana_payload() -> None:
    """真实 Grafana 载荷：指纹/名称/等级/正文/状态都取到。"""
    ev = parse_alert_payload(_raw(_GRAFANA))
    assert ev.fingerprints == ("af1f08f7368a765e",)
    assert ev.alertname == "接口请求pv测试报警"
    assert ev.level == "critical"
    assert ev.details == "接口状态503的量上升"
    assert ev.status == "firing"
    assert ev.is_recovery is False


def test_alarm_id_falls_back_to_first_fingerprint() -> None:
    """载荷无 alarmId ⇒ 报警身份回退到首条指纹（同一告警稳定）。"""
    ev = parse_alert_payload(_raw(_GRAFANA))
    assert ev.alarm_id == "af1f08f7368a765e"


def test_alarm_id_prefers_explicit_field() -> None:
    """若上游提供了 alarmId，则优先用它。"""
    payload = dict(_GRAFANA, alarmId="ALM-001")
    assert parse_alert_payload(_raw(payload)).alarm_id == "ALM-001"


def test_level_falls_back_to_severity_label() -> None:
    """顶层无 level ⇒ 取 commonLabels.severity。"""
    payload = dict(_GRAFANA, commonLabels={"alertname": "A", "severity": "warning"})
    assert parse_alert_payload(_raw(payload)).level == "warning"


def test_level_defaults_to_dash_when_absent() -> None:
    """任何等级来源都没有 ⇒ `-`。"""
    payload = dict(_GRAFANA, commonLabels={"alertname": "A"})
    assert parse_alert_payload(_raw(payload)).level == "-"


def test_details_from_summary_when_no_message() -> None:
    """Grafana 无 annotations.message：取 summary（实测用例）。"""
    ev = parse_alert_payload(_raw(_GRAFANA))
    assert "接口状态503的量上升" in ev.details


def test_details_falls_back_to_top_level_message() -> None:
    """连注解都没有 ⇒ 退回顶层 message（Grafana 渲染好的文本）。"""
    payload = dict(
        _GRAFANA,
        alerts=[{"fingerprint": "fp-x", "annotations": {}}],
    )
    assert "Firing" in parse_alert_payload(_raw(payload)).details


def test_status_defaults_to_firing_when_absent() -> None:
    """载荷没有 status ⇒ 按 firing 处理（保守：不把触发当恢复）。"""
    payload = {k: v for k, v in _GRAFANA.items() if k != "status"}
    assert parse_alert_payload(_raw(payload)).status == "firing"


def test_resolved_status_marks_recovery() -> None:
    """status=resolved ⇒ is_recovery 为真。"""
    payload = dict(_GRAFANA, status="resolved")
    ev = parse_alert_payload(_raw(payload))
    assert ev.is_recovery is True
    assert ev.status == "resolved"


def test_resolved_defaults_to_firing_when_absent() -> None:
    """缺 status 时保守按 firing（不把触发误判成恢复）。"""
    payload = {k: v for k, v in _GRAFANA.items() if k != "status"}
    assert parse_alert_payload(_raw(payload)).is_recovery is False


def test_parse_bad_json() -> None:
    """非法 JSON ⇒ ValueError。"""
    with pytest.raises(ValueError, match="JSON"):
        parse_alert_payload(b"{not json")


def test_parse_non_object() -> None:
    """顶层不是对象 ⇒ ValueError。"""
    with pytest.raises(ValueError, match="JSON 对象"):
        parse_alert_payload(b"[1,2,3]")


def test_parse_missing_alertname() -> None:
    """缺 commonLabels.alertname ⇒ ValueError。"""
    payload = dict(_GRAFANA, commonLabels={})
    with pytest.raises(ValueError, match="alertname"):
        parse_alert_payload(_raw(payload))


def test_parse_no_fingerprint() -> None:
    """无任何 fingerprint ⇒ ValueError（去重与会话身份都依赖它）。"""
    payload = dict(_GRAFANA, alerts=[{"annotations": {"summary": "x"}}])
    with pytest.raises(ValueError, match="fingerprint"):
        parse_alert_payload(_raw(payload))


def test_derive_alert_session_id_is_stable_and_isolated() -> None:
    """同 bot_key+身份稳定；与群聊 scope 隔离；bot_key 参与哈希。"""
    a = derive_alert_session_id("dev", "af1f08f7368a765e")
    assert a == derive_alert_session_id("dev", "af1f08f7368a765e")
    assert a == str(uuid.uuid5(WECOM_NS, "wecom|dev|alert|af1f08f7368a765e"))
    assert derive_alert_session_id("dev", "other") != a
    assert derive_alert_session_id("finance", "af1f08f7368a765e") != a
    assert len(a) == 36


def test_dedup_keys_include_status() -> None:
    """去重键带状态 —— 否则恢复会被"同指纹"的窗口吃掉。"""
    firing = parse_alert_payload(_raw(_GRAFANA))
    resolved = parse_alert_payload(_raw(dict(_GRAFANA, status="resolved")))
    assert dedup_keys(firing) == ("af1f08f7368a765e|firing",)
    assert dedup_keys(resolved) == ("af1f08f7368a765e|resolved",)
    assert set(dedup_keys(firing)).isdisjoint(dedup_keys(resolved))


def test_build_query_uses_template() -> None:
    """query 按模板渲染，含名称/等级/正文。"""
    q = build_query(parse_alert_payload(_raw(_GRAFANA)))
    assert "接口请求pv测试报警" in q
    assert "critical" in q
    assert "接口状态503的量上升" in q


def test_render_fallback_prefix_and_limit() -> None:
    """兜底带前缀、限长、不整包 dump。"""
    out = render_fallback(parse_alert_payload(_raw(_GRAFANA)), max_chars=5)
    assert out.startswith(AlertTexts.FALLBACK_PREFIX)
    assert "receiver" not in out  # 不是原始 JSON dump


def test_render_placeholder() -> None:
    """占位文案含告警名称。"""
    assert "接口请求pv测试报警" in render_placeholder(
        parse_alert_payload(_raw(_GRAFANA))
    )


def test_render_recovery() -> None:
    """恢复文案带 `[已恢复]` 前缀 + 名称/等级 + 描述。"""
    out = render_recovery(parse_alert_payload(_raw(dict(_GRAFANA, status="resolved"))))
    assert out.startswith(AlertTexts.RECOVERY_PREFIX)
    assert "接口请求pv测试报警" in out
    assert "critical" in out
    assert "接口状态503的量上升" in out


def test_render_recovery_without_details() -> None:
    """无描述时只出标题行（不留空行）。"""
    payload = dict(_GRAFANA, alerts=[{"fingerprint": "fp-x", "annotations": {}}])
    payload.pop("message", None)
    out = render_recovery(parse_alert_payload(_raw(payload)))
    assert "\n" not in out


def test_alert_event_is_frozen() -> None:
    """AlertEvent 不可变（值对象）。"""
    ev = AlertEvent(
        alarm_id="a",
        alertname="n",
        level="P1",
        details="d",
        fingerprints=("f",),
        status="firing",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.alarm_id = "b"  # type: ignore[misc]
