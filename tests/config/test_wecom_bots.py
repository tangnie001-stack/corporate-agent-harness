"""WECOM_BOTS 注册表解析与校验单测。"""

import json

import pytest

from src.config.wecom_bots import CALLBACK_BOT_KEY, WeComBotConfig, load_wecom_bots


def _dump(items: list[dict]) -> str:
    """紧凑单行 JSON（与 .env 里的实际写法一致）。"""
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _bot(key: str, bot_id: str, secret: str) -> dict:
    return {"key": key, "bot_id": bot_id, "secret": secret}


def test_empty_returns_empty_list(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", "")
    assert load_wecom_bots() == []


def test_valid_three_bots(monkeypatch):
    monkeypatch.setenv(
        "WECOM_BOTS",
        _dump(
            [
                _bot("dev", "aibA", "s1"),
                _bot("support", "aibB", "s2"),
                _bot("finance", "aibC", "s3"),
            ]
        ),
    )
    bots = load_wecom_bots()
    assert [b.key for b in bots] == ["dev", "support", "finance"]
    assert bots[0] == WeComBotConfig(key="dev", bot_id="aibA", secret="s1")


def test_invalid_json_does_not_leak_secret(monkeypatch):
    """非法 JSON 的错误信息不得回显原值（否则三台 secret 一起泄露）。"""
    monkeypatch.setenv("WECOM_BOTS", '[{"key":"dev","secret":"TOPSECRET"')
    with pytest.raises(ValueError) as excinfo:
        load_wecom_bots()
    assert "TOPSECRET" not in str(excinfo.value)


def test_not_a_list(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", '{"key":"dev"}')
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_missing_field(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", _dump([_bot("dev", "aibA", "")]))
    with pytest.raises(ValueError) as excinfo:
        load_wecom_bots()
    assert "secret" in str(excinfo.value)


def test_key_charset(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", _dump([_bot("Dev", "aibA", "s1")]))
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_reserved_callback_key(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", _dump([_bot(CALLBACK_BOT_KEY, "aibA", "s1")]))
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_duplicate_key(monkeypatch):
    monkeypatch.setenv(
        "WECOM_BOTS", _dump([_bot("dev", "aibA", "s1"), _bot("dev", "aibB", "s2")])
    )
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_duplicate_bot_id(monkeypatch):
    monkeypatch.setenv(
        "WECOM_BOTS",
        _dump([_bot("dev", "aibA", "s1"), _bot("support", "aibA", "s2")]),
    )
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_name_parsed(monkeypatch):
    """name 是可选的人类可读名称，解析后随配置带出。"""
    monkeypatch.setenv(
        "WECOM_BOTS",
        _dump(
            [
                {
                    "key": "ops",
                    "name": "阿里云运维机器人",
                    "bot_id": "aibX",
                    "secret": "s",
                }
            ]
        ),
    )
    bots = load_wecom_bots()
    assert bots[0].name == "阿里云运维机器人"


def test_name_optional_defaults_empty(monkeypatch):
    """未写 name 时为空串，不影响其余字段。"""
    monkeypatch.setenv("WECOM_BOTS", _dump([_bot("dev", "aibA", "s1")]))
    bots = load_wecom_bots()
    assert bots[0].name == ""


def test_name_must_be_string(monkeypatch):
    """name 类型非法即 fail-fast，不回显原值。"""
    monkeypatch.setenv(
        "WECOM_BOTS",
        _dump([{"key": "dev", "name": 1, "bot_id": "aibA", "secret": "s1"}]),
    )
    with pytest.raises(ValueError) as excinfo:
        load_wecom_bots()
    assert "name" in str(excinfo.value)
    assert "s1" not in str(excinfo.value)
