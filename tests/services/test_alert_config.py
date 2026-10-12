"""告警配置解析与校验单测。"""

import json

import pytest

from src.config.alert import AlertConfig, AlertTarget, load_alert_config


def test_unset_token_yields_empty_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置 token ⇒ token 为空、目标为 None，不抛异常（不阻断启动）。"""
    monkeypatch.delenv("ALERT_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    cfg = load_alert_config()
    assert cfg.token == ""
    assert cfg.target is None


def test_valid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """齐备配置解析出目标与阈值。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.setenv("ALERT_TARGET", '{"bot_key": "dev", "chatid": "C1"}')
    monkeypatch.setenv("ALERT_DEDUP_WINDOW_SECONDS", "180")
    monkeypatch.setenv("ALERT_TURN_TIMEOUT_SECONDS", "240")
    cfg = load_alert_config()
    assert isinstance(cfg, AlertConfig)
    assert cfg.token == "s3cret"
    assert cfg.target == AlertTarget(bot_key="dev", chatid="C1")
    assert cfg.dedup_window_seconds == 180.0
    assert cfg.turn_timeout_seconds == 240.0


def test_bad_target_json_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标群 JSON 非法 ⇒ fail-fast，且错误信息不回显原值。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.setenv("ALERT_TARGET", "{not json")
    with pytest.raises(ValueError) as ei:
        load_alert_config()
    assert "ALERT_TARGET" in str(ei.value)
    assert "{not json" not in str(ei.value)


def test_target_missing_bot_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标群缺 bot_key ⇒ fail-fast。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.setenv("ALERT_TARGET", json.dumps({"chatid": "C1"}))
    with pytest.raises(ValueError, match="bot_key"):
        load_alert_config()


def test_token_set_but_target_missing_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有 token 无目标：合法（端点收包后按"未配置目标"记 warning）。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    cfg = load_alert_config()
    assert cfg.token == "s3cret"
    assert cfg.target is None
