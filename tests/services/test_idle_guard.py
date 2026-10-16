"""业务层 idle 守卫单测（P3）。

判据是"事件条数是否增长"，故替身只需实现 StreamingRunManager 的三个方法。
阈值被压到毫秒级、轮询 10ms，让事件循环里能真实走完"阈值到点 → 发通知"。
"""

import asyncio

import pytest

from src.config import settings
from src.config.const import SSEInteractionTexts
from src.core.log_events import Event
from src.services import agent_service


class _FakeBuffer:
    """StreamingRunManager 的最小替身：get_events_since / buffer_exists / add_event。"""

    def __init__(self, exists: bool = True) -> None:
        self.events: list[tuple[int, str, dict]] = []
        self.notices: list[tuple[str, dict]] = []
        self._exists = exists

    def get_events_since(self, session_id: str, after_seq: int) -> list:
        return [e for e in self.events if e[0] > after_seq]

    def buffer_exists(self, session_id: str) -> bool:
        return self._exists

    def add_event(self, session_id: str, etype: str, payload: dict) -> int:
        seq = len(self.events) + 1
        self.events.append((seq, etype, payload))
        self.notices.append((etype, payload))
        return seq


def _fast(monkeypatch, **overrides) -> None:
    """把轮询与四档阈值压到毫秒级（定时用例依赖真实 await）。"""
    for key, value in {
        "MAIN_IDLE_POLL_INTERVAL_S": 0.01,
        "MAIN_IDLE_FIRST_S": 0.05,
        "MAIN_IDLE_FIRST_THINKING_S": 0.05,
        "MAIN_IDLE_GAP_S": 0.05,
        "MAIN_IDLE_GAP_THINKING_S": 0.05,
    }.items():
        monkeypatch.setattr(settings, key, value)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)


def _spy_log(monkeypatch) -> list:
    logged: list = []
    monkeypatch.setattr(
        agent_service.core_logging,
        "log_event",
        lambda event, **fields: logged.append((event, fields)),
    )
    return logged


async def _run_briefly(coro, seconds: float) -> None:
    """跑 coro 若干秒后取消（守卫是长驻协程，用例以取消结束）。"""
    task = asyncio.create_task(coro)
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_idle_guard_notifies_once_per_silence_episode(monkeypatch):
    """静默到点：记一条 degraded(reason=generation_idle) + 送一条 status(degraded)。"""
    _fast(monkeypatch)
    logged = _spy_log(monkeypatch)
    buf = _FakeBuffer()

    await _run_briefly(
        agent_service._idle_guard(buf, "s1", None, False),  # type: ignore[reportArgumentType]  # 运行时为最小替身 _FakeBuffer
        0.25,
    )

    degraded = [f for e, f in logged if e == Event.DEGRADED]
    assert len(degraded) == 1, "每个静默段只记一条：本层自己的通知不算事件恢复"
    assert degraded[0]["reason"] == "generation_idle"
    assert degraded[0]["tier"] == "first"
    notices = [p for t, p in buf.notices if t == "status"]
    assert len(notices) == 1
    assert notices[0]["stage"] == SSEInteractionTexts.STAGE_DEGRADED
    assert (
        notices[0]["message"]
        == SSEInteractionTexts.DEGRADATION_TEXTS["generation_idle"]
    )


@pytest.mark.asyncio
async def test_idle_guard_re_notifies_after_activity_resumes(monkeypatch):
    """外部事件恢复后再静默 ⇒ 第二条通知（档位从 first 切到 gap）。"""
    _fast(monkeypatch)
    logged = _spy_log(monkeypatch)
    buf = _FakeBuffer()

    task = asyncio.create_task(
        agent_service._idle_guard(buf, "s1", None, False)  # type: ignore[reportArgumentType]  # 运行时为最小替身 _FakeBuffer
    )
    await asyncio.sleep(0.15)  # 先触发 first 档
    buf.add_event("s1", "token", {"token": "x"})  # 外部事件恢复
    await asyncio.sleep(0.15)  # 再次静默 ⇒ 触发 gap 档
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    degraded = [f for e, f in logged if e == Event.DEGRADED]
    assert [f["tier"] for f in degraded] == ["first", "gap"]


@pytest.mark.asyncio
async def test_idle_guard_stays_silent_while_below_threshold(monkeypatch):
    """阈值未到 ⇒ 一条都不发。"""
    _fast(monkeypatch, MAIN_IDLE_FIRST_S=5.0, MAIN_IDLE_GAP_S=5.0)
    logged = _spy_log(monkeypatch)

    await _run_briefly(
        agent_service._idle_guard(
            _FakeBuffer(),  # type: ignore[reportArgumentType]  # 运行时为最小替身
            "s1",
            None,
            False,
        ),
        0.2,
    )

    assert not [f for e, f in logged if e == Event.DEGRADED]


@pytest.mark.asyncio
async def test_idle_guard_exits_when_buffer_is_gone(monkeypatch):
    """缓冲已清理 ⇒ 守卫正常返回（不无限刷通知）。"""
    _fast(monkeypatch)
    logged = _spy_log(monkeypatch)

    task = asyncio.create_task(
        agent_service._idle_guard(
            _FakeBuffer(exists=False),  # type: ignore[reportArgumentType]  # 运行时为最小替身
            "s1",
            None,
            False,
        )
    )
    await asyncio.wait_for(task, timeout=0.5)  # 不抛即"自行退出"

    assert not [f for e, f in logged if e == Event.DEGRADED]


def test_idle_thresholds_follow_thinking_tier():
    """档位选择：思考档取 *_THINKING_S，否则取非思考档。"""
    assert agent_service._idle_thresholds(False) == (
        settings.MAIN_IDLE_FIRST_S,
        settings.MAIN_IDLE_GAP_S,
    )
    assert agent_service._idle_thresholds(True) == (
        settings.MAIN_IDLE_FIRST_THINKING_S,
        settings.MAIN_IDLE_GAP_THINKING_S,
    )
