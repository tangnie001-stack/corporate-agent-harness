"""/api/chat/stream 流程改造测试 — user 同步落库（POST）。

验证：流式请求开始即同步写 user（MySQL），端点方法为 POST（body 为
ChatStreamRequest）；并发防护先查进程内注册表再取 Redis 锁。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from src.api.chat import chat_stream
from src.api.model.request import ChatStreamRequest
from src.chat.streaming import streaming_manager


def test_chat_stream_persists_user_before_stream(auth_client, mock_app_service):
    """请求开始即写 user（MySQL 同步），再进入流式生成。"""
    mock_app_service.set_chat_repo = AsyncMock()
    mock_app_service.agent_service.stream_chat = AsyncMock()
    mock_app_service.agent_service.stream_chat.return_value = iter(())
    mock_app_service.save_user_async = AsyncMock()
    mock_app_service.save_session_async = AsyncMock()

    resp = auth_client.post(
        "/api/chat/stream",
        json={"session_id": "s1", "kb_id": "kb1", "query": "营收多少"},
    )
    assert resp.status_code == 200
    mock_app_service.save_session_async.assert_called_once()
    mock_app_service.save_user_async.assert_called_once_with("s1", "kb1", "营收多少")


@pytest.mark.asyncio
async def test_background_task_finalizes_assistant_on_cancel():
    """后台任务收到 abort（task.cancel）后，已产出 token 落 interrupted 并写 done 终态。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.turn_runner import _run_with_finalize

    calls = []
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock(
        side_effect=lambda *a, **k: calls.append(("assistant", a[4]))
    )
    partial_holder = {
        "text": "部分回答",
        "events_log": [{"type": "token", "payload": {"token": "部分回答"}}],
    }
    mgr = StreamingRunManager()
    entered = asyncio.Event()

    async def answer_builder():
        entered.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(
        _run_with_finalize(
            fake_svc,
            "s1",
            "kb1",
            partial_holder,
            answer_builder,
            mgr,
            asyncio.Event(),
            lambda: None,
            RequestContext(session_id="s1"),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ("assistant", "interrupted") in calls
    assert mgr.has_terminal("s1") is True


@pytest.mark.asyncio
async def test_background_task_finalizes_assistant_on_complete():
    """后台任务正常结束：完整回答落 complete 并写 done 终态。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.turn_runner import _run_with_finalize

    calls = []
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock(
        side_effect=lambda *a, **k: calls.append(("assistant", a[4]))
    )
    fake_svc.chat_manager = MagicMock()
    fake_svc.chat_manager.add_message_async = AsyncMock()
    partial_holder = {"text": ""}
    mgr = StreamingRunManager()

    async def answer_builder():
        partial_holder["text"] = "完整回答"
        return "完整回答"

    await _run_with_finalize(
        fake_svc,
        "s1",
        "kb1",
        partial_holder,
        answer_builder,
        mgr,
        asyncio.Event(),
        lambda: None,
        RequestContext(session_id="s1"),
    )
    assert ("assistant", "complete") in calls
    assert mgr.has_terminal("s1") is True


@pytest.mark.asyncio
async def test_complete_path_writes_assistant_to_redis():
    """正常结束：完整回答除落 MySQL 外，还写 Redis 对话历史（跨 turn 上下文）。

    C1 回归：get_history_async（Redis）供下一轮 prompt 上下文，此前完整回答
    只落 MySQL 未写 Redis，多轮对话退化。中断/异常的部分回答保持仅 MySQL。
    """
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.turn_runner import _run_with_finalize

    redis_calls = []
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock()
    fake_svc.chat_manager = MagicMock()
    fake_svc.chat_manager.add_message_async = AsyncMock(
        side_effect=lambda *a, **k: redis_calls.append(a)
    )
    partial_holder = {
        "text": "",
        "events_log": [{"type": "token", "payload": {"token": "完整回答"}}],
    }
    mgr = StreamingRunManager()

    async def answer_builder():
        partial_holder["text"] = "完整回答"
        return "完整回答"

    await _run_with_finalize(
        fake_svc,
        "s1",
        "kb1",
        partial_holder,
        answer_builder,
        mgr,
        asyncio.Event(),
        lambda: None,
        RequestContext(session_id="s1"),
    )
    assert redis_calls == [("s1", "assistant", "完整回答")]
    assert mgr.has_terminal("s1") is True


@pytest.mark.asyncio
async def test_cancelled_path_skips_redis_assistant():
    """取消/部分回答路径：不写 Redis 对话历史（仅落 MySQL interrupted）。

    C1 回归：中断的部分回答是残片，不进入下一轮 prompt 上下文。
    """
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.turn_runner import _run_with_finalize

    redis_calls = []
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock()
    fake_svc.chat_manager = MagicMock()
    fake_svc.chat_manager.add_message_async = AsyncMock(
        side_effect=lambda *a, **k: redis_calls.append(a)
    )
    partial_holder = {
        "text": "部分回答",
        "events_log": [{"type": "token", "payload": {"token": "部分回答"}}],
    }
    mgr = StreamingRunManager()

    async def answer_builder():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _run_with_finalize(
            fake_svc,
            "s1",
            "kb1",
            partial_holder,
            answer_builder,
            mgr,
            asyncio.Event(),
            lambda: None,
            RequestContext(session_id="s1"),
        )
    assert redis_calls == []
    fake_svc.save_assistant_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_task_done_event_carries_explicit_trace_id():
    """后台任务显式传递 trace_id：任务内 contextvar 被 set，done 终态事件携带该 trace_id。

    验证 M4：调用方在启动前捕获 current_trace_id.get() 显式传入 _run_with_finalize，
    任务入口 set 到 current_trace_id（跨任务不依赖自动传播），answer_builder 内读取
    到的即为请求 trace_id，done 事件 payload {"trace_id": ...} 与之相等。
    """
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.infra.llm.trace_context import current_trace_id
    from src.services.turn_runner import _run_with_finalize

    trace_id = "trace_task_4_3"
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock()
    fake_svc.chat_manager = MagicMock()
    fake_svc.chat_manager.add_message_async = AsyncMock()
    partial_holder = {"text": ""}
    mgr = StreamingRunManager()
    seen_in_task = []

    async def answer_builder():
        seen_in_task.append(current_trace_id.get())
        return "完整回答"

    await _run_with_finalize(
        fake_svc,
        "s1",
        "kb1",
        partial_holder,
        answer_builder,
        mgr,
        asyncio.Event(),
        lambda: None,
        RequestContext(session_id="s1"),
        trace_id=trace_id,
    )

    # 任务内（answer_builder 执行时）current_trace_id 已显式 set
    assert seen_in_task == [trace_id]
    done_events = [
        (seq, etype, payload)
        for seq, etype, payload in mgr.get_events_since("s1", 0)
        if etype == "done"
    ]
    assert len(done_events) == 1
    _, _, payload = done_events[0]
    assert payload == {"trace_id": trace_id}


@pytest.mark.asyncio
async def test_background_task_error_event_round_trips_from_payload():
    """answer_builder 抛异常时，error 终态事件 payload 可被 from_payload 正常回放。

    回归防线：error 事件必须以 dict {"error": str} 入缓冲（与实时 sse_error 的
    data: 同构），否则 resume/status 回放路径在 from_payload 处抛
    TypeError: string indices must be integers。

    载荷文案另有契约：未归类的异常不得透出内部文本，留空串由前端回落通用
    文案（timeout-and-degradation-hardening P2；见 turn_runner.safe_error_copy）。
    """
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.turn_runner import _run_with_finalize
    from src.utils.sse import SSEErrorEvent, from_payload

    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock()
    partial_holder = {
        "text": "部分回答",
        "events_log": [{"type": "token", "payload": {"token": "部分回答"}}],
    }
    mgr = StreamingRunManager()

    async def answer_builder():
        raise RuntimeError("LLM generation failed")

    await _run_with_finalize(
        fake_svc,
        "s1",
        "kb1",
        partial_holder,
        answer_builder,
        mgr,
        asyncio.Event(),
        lambda: None,
        RequestContext(session_id="s1"),
    )

    assert mgr.has_terminal("s1") is True
    error_events = [
        (seq, etype, payload)
        for seq, etype, payload in mgr.get_events_since("s1", 0)
        if etype == "error"
    ]
    assert len(error_events) == 1
    _, etype, payload = error_events[0]
    assert isinstance(payload, dict)
    restored = from_payload(etype, payload)
    assert isinstance(restored, SSEErrorEvent)
    # RuntimeError 未归类 ⇒ 安全文案为空串（内部异常文本不外泄）
    assert restored.error == ""


@pytest.mark.asyncio
async def test_background_task_done_cancelled_round_trips():
    """answer_builder 抛 CancelledError（abort 触达生产者）→ 落 interrupted + done(cancelled) 入缓冲。

    验证 F1+F2 链路：生产者 abort 抛 CancelledError → _run_with_finalize 捕获 →
    部分回答落 interrupted → 写 done 终态事件 payload {"cancelled": True} →
    from_payload 还原保留 cancelled 标记 → to_sse 序列化 "cancelled": true。
    """
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.turn_runner import _run_with_finalize
    from src.utils.sse import SSEDoneEvent, from_payload, to_sse

    calls = []
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock(
        side_effect=lambda *a, **k: calls.append(("assistant", a[4]))
    )
    partial_holder = {
        "text": "部分回答",
        "events_log": [{"type": "token", "payload": {"token": "部分回答"}}],
    }
    mgr = StreamingRunManager()

    async def answer_builder():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _run_with_finalize(
            fake_svc,
            "s1",
            "kb1",
            partial_holder,
            answer_builder,
            mgr,
            asyncio.Event(),
            lambda: None,
            RequestContext(session_id="s1"),
        )

    assert ("assistant", "interrupted") in calls
    assert mgr.has_terminal("s1") is True
    done_events = [
        (seq, etype, payload)
        for seq, etype, payload in mgr.get_events_since("s1", 0)
        if etype == "done"
    ]
    assert len(done_events) == 1
    _, etype, payload = done_events[0]
    assert payload == {"cancelled": True}
    restored = from_payload(etype, payload)
    assert isinstance(restored, SSEDoneEvent)
    assert restored.cancelled is True
    assert '"cancelled": true' in to_sse(restored)


@pytest.mark.asyncio
async def test_chat_stream_conflict_returns_409(mock_app_service):
    """注册表已有活跃任务 → chat_stream 先查注册表返回 409（不依赖 Redis 锁）。

    在模块级 streaming_manager 单例上登记一个未完成的任务，端点必须
    在取 Redis 锁之前被 is_running 拦下。用唯一 session_id 并在
    teardown 注销/取消，避免污染其它测试。
    """
    session_id = "s-conflict-409"
    task = asyncio.create_task(asyncio.sleep(10))
    streaming_manager.register(session_id, task, asyncio.Event())
    try:
        assert streaming_manager.is_running(session_id) is True
        with pytest.raises(HTTPException) as exc_info:
            await chat_stream(
                request=Request({"type": "http", "method": "POST"}),
                body=ChatStreamRequest(
                    session_id=session_id, kb_id="kb1", query="营收多少"
                ),
                svc=mock_app_service,
            )
        assert exc_info.value.status_code == 409
    finally:
        streaming_manager.unregister(session_id)
        task.cancel()


@pytest.mark.asyncio
async def test_start_turn_wires_ctx_abort_signal(monkeypatch):
    """start_turn 把调用方持有的 abort_signal 接到 ctx.abort_signal。

    C2 回归：ask_user 的 wait_with_abort_and_timeout 等待 ctx.abort_signal，
    若与注册表里的 abort 信号不是同一事件，取消唤不醒澄清等待，会干等
    ASK_USER_TIMEOUT。断言接线后 ctx.abort_signal 即调用方传入（cancel 端点
    经 set_abort 置位）的对象，且 set_abort 能直接置位它。
    """
    from src.chat.streaming import streaming_manager
    from src.infra.llm.request_context import RequestContext
    from src.services import turn_runner
    from src.utils.sse import SSEStatusEvent

    session_id = "s-abort-wiring"
    ctx = RequestContext(session_id=session_id)
    launch_ctx = {
        "session_id": session_id,
        "kb_id": "kb1",
        "query": "营收多少",
        "history": [],
        "deep_thinking": False,
        "ctx": ctx,
        "graph": None,
        "direct_skill": "",
    }

    async def fake_subscription():
        yield SSEStatusEvent(stage="agent", message="正在思考...")

    fake_svc = MagicMock()
    fake_svc.set_chat_repo = AsyncMock()
    fake_svc.save_session_async = AsyncMock()
    fake_svc.save_user_async = AsyncMock()
    fake_svc.save_assistant_async = AsyncMock()
    fake_svc.chat_manager = MagicMock()
    fake_svc.chat_manager._redis = None  # 跳过锁，聚焦 abort 接线
    fake_svc.chat_manager.add_message_async = AsyncMock()
    fake_svc.agent_service = MagicMock()
    fake_svc.agent_service.stream_chat = AsyncMock(
        return_value=(fake_subscription(), launch_ctx)
    )

    async def fake_run_generation(*args, **kwargs):
        return "完整回答"

    monkeypatch.setattr(turn_runner, "_run_generation", fake_run_generation)

    abort_signal = asyncio.Event()
    await turn_runner.start_turn(
        fake_svc,  # type: ignore[reportArgumentType]
        session_id=session_id,
        kb_id="kb1",
        query="营收多少",
        abort_signal=abort_signal,
    )
    try:
        assert streaming_manager.get_abort_signal(session_id) is abort_signal
        assert ctx.abort_signal is abort_signal
        # cancel 端点经 set_abort 置位的就是 ctx.abort_signal
        streaming_manager.set_abort(session_id)
        assert ctx.abort_signal.is_set()
    finally:
        streaming_manager.clear_buffer(session_id)
        # 等待后台任务收尾注销（_run_generation 已 mock，立即完成）
        for _ in range(50):
            if not streaming_manager.is_running(session_id):
                break
            await asyncio.sleep(0.05)
        streaming_manager.unregister(session_id)
