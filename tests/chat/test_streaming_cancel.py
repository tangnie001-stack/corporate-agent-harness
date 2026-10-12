"""StreamingRunManager.cancel：硬取消单测。"""

import asyncio

import pytest

from src.chat.streaming import StreamingRunManager


def test_cancel_running_task_raises_cancelled() -> None:
    """cancel 使注册中的任务收到 CancelledError。"""

    async def _scenario() -> None:
        mgr = StreamingRunManager()

        async def _hang() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(_hang())
        mgr.register("s1", task, asyncio.Event())
        await asyncio.sleep(0)  # 让任务真正开始
        assert mgr.cancel("s1") is True
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_scenario())


def test_cancel_unknown_session_is_false() -> None:
    """无该会话任务 ⇒ False（不抛）。"""
    mgr = StreamingRunManager()
    assert mgr.cancel("nope") is False


def test_cancel_finished_task_is_false() -> None:
    """任务已结束 ⇒ False。"""

    async def _scenario() -> None:
        mgr = StreamingRunManager()

        async def _quick() -> None:
            return

        task = asyncio.create_task(_quick())
        mgr.register("s1", task, asyncio.Event())
        await task
        assert mgr.cancel("s1") is False

    asyncio.run(_scenario())
