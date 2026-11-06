"""启动期企微 profile 存在性校验（src.main._validate_wecom_profiles）单测。

校验点在 lifespan 里、`wecom_service.start()` 之前：配置错时启动即失败，
不留到第一条消息进来才在路由处报错。用临时 agents 目录替代真实内容库，
不触碰网络与数据库。
"""

import json
from pathlib import Path

import pytest

import src.main as main_mod


def _write_preset(root: Path, name: str) -> None:
    """在临时 agents 目录写入一份最小预设。

    bot- 前缀预设必须同时声明 hidden: true 与 channel: wecom，否则加载期被跳过。
    """
    root.mkdir(parents=True, exist_ok=True)
    if name.startswith("bot-"):
        frontmatter = f"name: {name}\nhidden: true\nchannel: wecom"
    else:
        frontmatter = f"name: {name}"
    (root / f"{name}.md").write_text(
        f"---\n{frontmatter}\n---\n\n你是 {name}。\n", encoding="utf-8"
    )


def _set_bots(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """把单台机器人写进 WECOM_BOTS 环境变量（单行 JSON）。"""
    items = [{"key": "ops", "bot_id": "b1", "secret": "s1", "profile": profile}]
    monkeypatch.setenv("WECOM_BOTS", json.dumps(items, separators=(",", ":")))


def test_skip_when_agents_root_missing(monkeypatch, tmp_path):
    """agents/ 不存在时跳过校验（既有降级路径），不阻断启动。"""
    monkeypatch.setattr(main_mod, "default_agents_root", lambda: tmp_path / "absent")
    _set_bots(monkeypatch, "bot-ops")
    main_mod._validate_wecom_profiles()


def test_raise_when_profile_not_registered(monkeypatch, tmp_path):
    """profile 指向未注册预设时启动失败，错误只含 key。"""
    _write_preset(tmp_path, "chat")
    monkeypatch.setattr(main_mod, "default_agents_root", lambda: tmp_path)
    _set_bots(monkeypatch, "bot-ops")
    with pytest.raises(ValueError) as excinfo:
        main_mod._validate_wecom_profiles()
    assert "key=ops" in str(excinfo.value)
    assert "s1" not in str(excinfo.value)


def test_pass_when_profile_registered(monkeypatch, tmp_path):
    """profile 指向已注册预设时通过。"""
    _write_preset(tmp_path, "bot-ops")
    monkeypatch.setattr(main_mod, "default_agents_root", lambda: tmp_path)
    _set_bots(monkeypatch, "bot-ops")
    main_mod._validate_wecom_profiles()
