"""LongConnectionDriver.send_message：主动推送（无入站帧）单测。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.channels.wecom.long_connection import LongConnectionDriver


def _driver_with_client() -> tuple[LongConnectionDriver, MagicMock]:
    """构造已注入假 client 的驱动。"""
    driver = LongConnectionDriver("botA", "secret", AsyncMock())
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"errcode": 0})
    driver._client = client
    return driver, client


def test_send_message_uses_markdown_body() -> None:
    """body 形状与实测一致：{"msgtype":"markdown","markdown":{"content":…}}。"""
    import asyncio

    driver, client = _driver_with_client()
    asyncio.run(driver.send_message("C1", "hello"))
    client.send_message.assert_awaited_once_with(
        "C1", {"msgtype": "markdown", "markdown": {"content": "hello"}}
    )


def test_send_message_without_client_raises() -> None:
    """连接未就绪（无 client）⇒ 抛 RuntimeError，不回落到任何默认会话。"""
    import asyncio

    driver = LongConnectionDriver("botA", "secret", AsyncMock())
    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(driver.send_message("C1", "hello"))
