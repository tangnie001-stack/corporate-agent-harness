"""wecom_service.send_to_session：主动推送的服务层出口单测。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.channels.wecom.long_connection import LongConnectionDriver
from src.services import wecom_service


def test_send_to_session_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中驱动且发送成功 ⇒ True。"""
    driver = AsyncMock(spec=LongConnectionDriver)
    monkeypatch.setitem(wecom_service._drivers, "dev", driver)
    assert asyncio.run(wecom_service.send_to_session("dev", "C1", "hi")) is True
    driver.send_message.assert_awaited_once_with("C1", "hi")


def test_send_to_session_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """机器人不存在/未启动 ⇒ False，不抛。"""
    monkeypatch.setattr(wecom_service, "_drivers", {})
    assert asyncio.run(wecom_service.send_to_session("ghost", "C1", "hi")) is False


def test_send_to_session_send_failure_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 发送失败（抛 RuntimeError）⇒ 记 warning 并返回 False。"""
    driver = AsyncMock(spec=LongConnectionDriver)
    driver.send_message.side_effect = RuntimeError("errcode=93006 invalid chatid")
    monkeypatch.setitem(wecom_service._drivers, "dev", driver)
    assert asyncio.run(wecom_service.send_to_session("dev", "C1", "hi")) is False
