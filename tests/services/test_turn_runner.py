"""start_turn 的闸门与失败语义（不触真实 DB/Redis）。"""

import asyncio

import pytest

from src.chat.streaming import streaming_manager
from src.services.turn_runner import TurnBusy, start_turn


class _FakeAgentService:
    def __init__(self, raise_on_stream_chat: bool = False) -> None:
        self.raise_on_stream_chat = raise_on_stream_chat
        self.calls = 0

    async def stream_chat(self, kb_id, session_id, query, deep_thinking, agent=""):
        self.calls += 1
        if self.raise_on_stream_chat:
            raise RuntimeError("boom")
        raise AssertionError("本用例不应走到订阅成功路径")


class _FakeChatManager:
    def __init__(self) -> None:
        self._redis = None  # 触发"Redis 不可用"分支：跳过锁，不阻塞
        self.saved: list[tuple] = []

    async def save_session_async(self, session_id, title, kb_id, user_id, agent=""):
        self.saved.append(("session", session_id, title, kb_id, user_id))

    async def save_user_async(self, session_id, kb_id, user_msg):
        self.saved.append(("user", session_id, kb_id, user_msg))


class _FakeSvc:
    def __init__(self, **kw) -> None:
        self.chat_manager = _FakeChatManager()
        self.agent_service = _FakeAgentService(**kw)
        self.chat_repo_set = 0

    async def set_chat_repo(self) -> None:
        self.chat_repo_set += 1

    async def save_session_async(self, session_id, title, kb_id, user_id, agent=""):
        await self.chat_manager.save_session_async(session_id, title, kb_id, user_id)

    async def save_user_async(self, session_id, kb_id, user_msg):
        await self.chat_manager.save_user_async(session_id, kb_id, user_msg)


@pytest.mark.asyncio
async def test_raises_turn_busy_when_session_running():
    """注册表已有该会话任务 → TurnBusy（不双开）。"""
    session_id = "sess-busy"
    task = asyncio.create_task(asyncio.sleep(5))
    streaming_manager.register(session_id, task, asyncio.Event())
    try:
        svc = _FakeSvc()
        with pytest.raises(TurnBusy):
            # 结构化假 svc：只需 start_turn 用到的接口，不构造真 AppService
            await start_turn(svc, session_id=session_id, kb_id="", query="hi")  # type: ignore[reportArgumentType]
    finally:
        task.cancel()
        streaming_manager.unregister_if_current(session_id, task)


@pytest.mark.asyncio
async def test_set_chat_repo_called_before_persist():
    """落库前置必须先注入 repo（否则 save_* 静默跳过）。"""
    svc = _FakeSvc(raise_on_stream_chat=True)
    handle = await start_turn(svc, session_id="sess-1", kb_id="", query="你好世界")  # type: ignore[reportArgumentType]
    assert svc.chat_repo_set == 1
    assert ("session", "sess-1", "你好世界", "", "") in svc.chat_manager.saved

    events = []
    async for ev in handle.events:
        events.append(type(ev).__name__)
    # stream_chat 失败 → 不抛，产 error + done 终止态
    assert "SSEErrorEvent" in events
    assert events[-1] == "SSEDoneEvent"


@pytest.mark.asyncio
async def test_concurrent_burst_only_one_starts_without_redis():
    """Redis 不可用时，同 session 两个 start_turn 并发只有一个通过闸门。

    进程内 try_reserve 是同步原子预留（判定与预留间无 await），第二个请求在
    第一个仍停留在落库前置的 await 点时应被 TurnBusy 拒绝，不双开。
    """
    session_id = "sess-burst-no-redis"
    entered_persist = asyncio.Event()
    release_persist = asyncio.Event()

    svc = _FakeSvc(raise_on_stream_chat=True)  # _redis is None：Redis 不可用

    async def blocking_save_session(session_id, title, kb_id, user_id, agent=""):
        entered_persist.set()
        await release_persist.wait()
        await svc.chat_manager.save_session_async(session_id, title, kb_id, user_id)

    svc.save_session_async = blocking_save_session  # type: ignore[method-assign]

    first = asyncio.create_task(
        start_turn(svc, session_id=session_id, kb_id="", query="第一条")  # type: ignore[reportArgumentType]
    )
    await entered_persist.wait()
    assert streaming_manager.is_running(session_id) is True  # 预留中即视为运行

    with pytest.raises(TurnBusy):
        await start_turn(svc, session_id=session_id, kb_id="", query="第二条")  # type: ignore[reportArgumentType]

    release_persist.set()
    handle = await first
    assert handle.session_id == session_id
    # 第一个请求 stream_chat 失败 → error + done 终止态（非抛）
    events = []
    async for ev in handle.events:
        events.append(type(ev).__name__)
    assert "SSEErrorEvent" in events
    assert events[-1] == "SSEDoneEvent"


@pytest.mark.asyncio
async def test_persist_failure_releases_reservation():
    """落库前置失败后释放预留，同一 session 可再次启动（不被永久占用）。"""
    session_id = "sess-persist-fail"
    svc = _FakeSvc(raise_on_stream_chat=True)

    async def failing_save_session(session_id, title, kb_id, user_id, agent=""):
        raise RuntimeError("db down")

    svc.save_session_async = failing_save_session  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await start_turn(svc, session_id=session_id, kb_id="", query="第一条")  # type: ignore[reportArgumentType]

    assert streaming_manager.is_running(session_id) is False  # 预留已释放

    # 同一 session 可再次启动（换新 svc 但共享模块级 streaming_manager）
    svc2 = _FakeSvc(raise_on_stream_chat=True)
    handle = await start_turn(svc2, session_id=session_id, kb_id="", query="第二条")  # type: ignore[reportArgumentType]
    assert handle.session_id == session_id
    events = []
    async for ev in handle.events:
        events.append(type(ev).__name__)
    assert "SSEErrorEvent" in events  # raise_on_stream_chat=True → 终止态
    assert events[-1] == "SSEDoneEvent"


def test_stale_done_callback_does_not_clear_later_reservation():
    """R14：上一轮的 done-callback 不得清掉下一回合的预留（竞态防线）。

    竞态：任务 A 完成后其 done-callback 经 call_soon 要下一轮事件循环才执行；
    这缝隙里同 session 新回合 B 已 try_reserve 成功。若 A 的回调释放预留，
    B 的预留被清、is_running 变 False，第三个并发回合 C 也能通过闸门 → 双开。
    故任务路径回调只释放 Redis 锁，不释放预留。本用例直接调用生产同款回调
    （_make_release_lock_cb 的返回值，正是 _run_with_finalize.finally 与 task
    done_callback 所用对象），稳定复现该时序。
    """
    from src.services.turn_runner import _make_release_lock_cb

    session_id = "sess-r14-stale-cb"
    try:
        cb = _make_release_lock_cb(None, session_id, False)
        # 新回合 B 已成功预留
        assert streaming_manager.try_reserve(session_id) is True
        # A 的陈旧 done-callback 在下一轮触发
        cb()
        # B 的预留必须仍在：否则第三个并发回合会通过闸门
        assert streaming_manager.is_running(session_id) is True
        assert streaming_manager.try_reserve(session_id) is False
    finally:
        streaming_manager.release_reservation(session_id)


@pytest.mark.asyncio
async def test_success_path_never_calls_release_reservation(monkeypatch):
    """R14：成功路径的预留由 register 替换、unregister_if_current 清理，
    finally / done-callback 都不得再调用 release_reservation（否则会误清
    下一回合的预留）。"""
    from unittest.mock import AsyncMock

    from src.infra.llm.request_context import RequestContext
    from src.services import turn_runner

    session_id = "sess-r14-success"
    ctx = RequestContext(session_id=session_id)
    launch_ctx = {
        "session_id": session_id,
        "kb_id": "",
        "query": "q",
        "history": [],
        "deep_thinking": False,
        "ctx": ctx,
        "graph": None,
        "direct_skill": "",
    }

    async def empty_subscription():
        return
        yield  # pragma: no cover  # 生成器：仅供 stream_chat 返回

    svc = _FakeSvc()  # _redis is None → 跳过 Redis 锁，聚焦进程内预留
    svc.agent_service.stream_chat = AsyncMock(  # type: ignore[method-assign]
        return_value=(empty_subscription(), launch_ctx)
    )
    svc.save_assistant_async = AsyncMock()  # type: ignore[attr-defined]
    svc.chat_manager.add_message_async = AsyncMock()  # type: ignore[attr-defined]

    async def fake_run_generation(*args, **kwargs):
        return "完整回答"

    monkeypatch.setattr(turn_runner, "_run_generation", fake_run_generation)

    release_calls: list[str] = []
    orig_release = streaming_manager.release_reservation

    def spy_release(sid: str) -> None:
        release_calls.append(sid)
        orig_release(sid)

    monkeypatch.setattr(streaming_manager, "release_reservation", spy_release)

    try:
        await turn_runner.start_turn(
            svc,  # type: ignore[reportArgumentType]
            session_id=session_id,
            kb_id="",
            query="q",
        )
        # 等后台任务收尾：is_running 转 False 即 finally/注销已完成，
        # 再让 done-callback 跑完（release_lock_cb 注册在注销回调之前）
        for _ in range(50):
            if not streaming_manager.is_running(session_id):
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0)
        assert release_calls == []
    finally:
        streaming_manager.clear_buffer(session_id)
        streaming_manager.unregister(session_id)


def test_safe_error_copy_two_branches():
    """safe_error_copy：未归类 → 空串（前端回落通用文案）；已归类 → 取值集合表文案。

    它是"异常 → 用户可见文案"策略的唯一归属：前端 T4 后消费 data.error，
    凡以其结果入 error 事件的发射点都不得透出原始异常文本。
    """
    from src.config.const import SSEInteractionTexts
    from src.services.turn_runner import safe_error_copy

    # 未归类（无 status_code 的普通异常）⇒ 空串
    assert safe_error_copy(RuntimeError("boom")) == ""

    class _ProviderDown(Exception):
        status_code = 403  # 401/403 ⇒ provider_unavailable

    assert (
        safe_error_copy(_ProviderDown("upstream 403"))
        == SSEInteractionTexts.DEGRADATION_TEXTS["provider_unavailable"]
    )


@pytest.mark.asyncio
async def test_stream_chat_failure_error_event_payload_uses_safe_copy():
    """起流失败兜底事件（_error_events）不得透出原始异常文本。

    端到端：stream_chat 抛未归类异常 ⇒ 句柄产 error + done，error 载荷为空串
    （非 str(e)）。这是跨层泄漏修复的核心证据：前端消费 data.error 后，
    空串会让其回落通用文案，与改造前逐字一致。
    """
    from src.utils.sse import SSEErrorEvent

    svc = _FakeSvc(raise_on_stream_chat=True)  # RuntimeError("boom")：未归类
    handle = await start_turn(svc, session_id="sess-safe", kb_id="", query="你好")  # type: ignore[reportArgumentType]

    errors = []
    async for ev in handle.events:
        if isinstance(ev, SSEErrorEvent):
            errors.append(ev.error)
    assert errors == [""]
