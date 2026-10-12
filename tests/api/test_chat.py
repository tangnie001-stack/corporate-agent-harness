"""Tests for SSE streaming chat endpoint."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_app_service
from src.main import app

client = TestClient(app)


def test_chat_stream_returns_sse():
    """POST /api/chat/stream returns SSE event stream."""
    from src.chat.streaming import streaming_manager
    from src.infra.llm.request_context import RequestContext
    from src.utils.sse import SSEDoneEvent, SSETokenEvent

    async def _sub():
        yield SSETokenEvent("净利润")
        yield SSEDoneEvent(trace_id="")

    async def fake_stream_chat(
        kb_id, session_id, query, deep_thinking=False, agent="", *, clarify=True
    ):
        # start_turn 从进程内事件缓冲订阅（真实 stream_chat 返回的订阅即缓冲
        # 消费者），故假 stream_chat 必须把事件写入缓冲，端点才能订阅到。
        streaming_manager.clear_buffer(session_id)
        streaming_manager.add_event(session_id, "token", {"token": "净利润"})
        streaming_manager.add_event(session_id, "done", {"trace_id": ""})
        return (
            _sub(),
            {
                "history": [],
                "ctx": RequestContext(session_id=session_id),
                "graph": None,
                "session_id": session_id,
                "kb_id": kb_id,
                "query": query,
                "deep_thinking": deep_thinking,
            },
        )

    mock_svc = AsyncMock()
    mock_svc.agent_service.stream_chat = fake_stream_chat
    app.dependency_overrides[get_app_service] = lambda: mock_svc

    try:
        with patch("src.services.turn_runner._run_with_finalize", new=AsyncMock()):
            response = client.post(
                "/api/chat/stream",
                json={"session_id": "s1", "kb_id": "kb-1", "query": "净利润多少"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "净利润" in response.text
    finally:
        streaming_manager.clear_buffer("s1")
        app.dependency_overrides.pop(get_app_service, None)


def test_chat_stream_passes_user_id():
    """chat_stream 应从请求上下文提取 user_id 传给编排入口。

    回归场景：会话持久化时未传 user_id，导致会话列表按用户过滤后为空。
    """
    mock_svc = AsyncMock()
    app.dependency_overrides[get_app_service] = lambda: mock_svc
    try:
        with patch(
            "src.services.turn_runner.start_turn", new_callable=AsyncMock
        ) as mock_start:

            async def _empty_events():
                if False:  # pragma: no cover - 仅提供异步迭代器协议
                    yield None

            handle = MagicMock()
            handle.events = _empty_events()
            mock_start.return_value = handle
            client.post(
                "/api/chat/stream",
                json={"session_id": "s1", "kb_id": "kb-1", "query": "hi"},
                cookies={"user_id": "user-123"},
            )
            # user_id 作为关键字参数传给 start_turn（auth middleware 从 user_id cookie 提取）
            assert mock_start.call_args.kwargs["user_id"] == "user-123"
    finally:
        app.dependency_overrides.pop(get_app_service, None)


def test_chat_stream_sets_channel_web():
    """chat_stream 在编排前把渠道标为 web（供日志第 5 段与 Langfuse metadata 读取）。"""
    from src.config.const import Channel
    from src.infra.llm.trace_context import current_channel

    mock_svc = AsyncMock()
    app.dependency_overrides[get_app_service] = lambda: mock_svc
    try:
        seen: list[str] = []
        with patch(
            "src.services.turn_runner.start_turn", new_callable=AsyncMock
        ) as mock_start:

            async def _empty_events():
                if False:  # pragma: no cover - 仅提供异步迭代器协议
                    yield None

            async def _capture(*args, **kwargs):
                seen.append(current_channel.get())
                handle = MagicMock()
                handle.events = _empty_events()
                return handle

            mock_start.side_effect = _capture
            client.post(
                "/api/chat/stream",
                json={"session_id": "s1", "kb_id": "kb-1", "query": "hi"},
            )
        assert seen == [Channel.WEB]
    finally:
        app.dependency_overrides.pop(get_app_service, None)


def test_chat_stream_emits_error_and_done_when_subscription_raises():
    """订阅迭代抛异常时，端点仍产出 error + done 终止帧（连接不中断）。

    回归场景：_frames 订阅 handle.events 时若 from_payload 遇畸形载荷抛异常，
    旧实现以 except 兜底为 error + done 终止态；若丢失该兜底，客户端只表现为
    连接中断且无终态（违反站点外部行为不变）。
    """
    from src.utils.sse import SSETokenEvent

    mock_svc = AsyncMock()
    app.dependency_overrides[get_app_service] = lambda: mock_svc

    async def _exploding_events():
        yield SSETokenEvent("部分")
        raise RuntimeError("boom")

    try:
        with patch(
            "src.services.turn_runner.start_turn", new_callable=AsyncMock
        ) as mock_start:
            handle = MagicMock()
            handle.events = _exploding_events()
            mock_start.return_value = handle
            response = client.post(
                "/api/chat/stream",
                json={"session_id": "s1", "kb_id": "kb-1", "query": "hi"},
            )
        assert response.status_code == 200
        assert "event: error" in response.text
        assert "event: done" in response.text
        assert "boom" in response.text
        # 终止帧顺序：error 在前、done 在后，且 done 为末帧
        assert response.text.index("event: error") < response.text.index("event: done")
        assert response.text.rstrip().endswith("}")
    finally:
        app.dependency_overrides.pop(get_app_service, None)


def test_chat_stream_passes_deep_thinking():
    """deep_thinking 请求体字段应透传至 agent_service.stream_chat。

    回归场景：前端「深度思考」开关打开时，请求应携带 deep_thinking=true，
    最终传递给 agent LLM 的 enable_thinking 参数；若断链则开关无效。
    """
    from src.chat.streaming import streaming_manager
    from src.infra.llm.request_context import RequestContext
    from src.utils.sse import SSEDoneEvent, SSETokenEvent

    captured = {}

    async def _sub():
        yield SSETokenEvent("ok")
        yield SSEDoneEvent(trace_id="")

    async def fake_stream_chat(
        kb_id, session_id, query, deep_thinking=False, agent="", *, clarify=True
    ):
        captured["deep_thinking"] = deep_thinking
        # 缓冲写入终止态：start_turn 从进程内缓冲订阅，否则端点无终态可收
        streaming_manager.clear_buffer(session_id)
        streaming_manager.add_event(session_id, "done", {"trace_id": ""})
        return (
            _sub(),
            {
                "history": [],
                "ctx": RequestContext(session_id=session_id),
                "graph": None,
                "session_id": session_id,
                "kb_id": kb_id,
                "query": query,
                "deep_thinking": deep_thinking,
            },
        )

    mock_svc = AsyncMock()
    mock_svc.agent_service.stream_chat = fake_stream_chat
    app.dependency_overrides[get_app_service] = lambda: mock_svc

    try:
        # 后台任务（_run_with_finalize）mock 掉，本用例只验证 deep_thinking 透传
        with patch("src.services.turn_runner._run_with_finalize", new=AsyncMock()):
            response = client.post(
                "/api/chat/stream",
                json={
                    "session_id": "s1",
                    "kb_id": "kb-1",
                    "query": "hi",
                    "deep_thinking": True,
                },
            )
        assert response.status_code == 200
        assert captured["deep_thinking"] is True
    finally:
        streaming_manager.clear_buffer("s1")
        app.dependency_overrides.pop(get_app_service, None)


def test_chat_stream_passes_agent():
    """agent 请求体字段应透传至 agent_service.stream_chat。

    回归场景：前端选择会话智能体时，请求应携带 agent=预设名，
    最终经 bind-once 绑定到会话并回传 agent_used；若断链则绑定失效。
    """
    from src.chat.streaming import streaming_manager
    from src.infra.llm.request_context import RequestContext
    from src.utils.sse import SSEDoneEvent, SSETokenEvent

    captured = {}

    async def _sub():
        yield SSETokenEvent("ok")
        yield SSEDoneEvent(trace_id="")

    async def fake_stream_chat(
        kb_id, session_id, query, deep_thinking=False, agent="", *, clarify=True
    ):
        captured["agent"] = agent
        # 缓冲写入终止态：start_turn 从进程内缓冲订阅，否则端点无终态可收
        streaming_manager.clear_buffer(session_id)
        streaming_manager.add_event(session_id, "done", {"trace_id": ""})
        return (
            _sub(),
            {
                "history": [],
                "ctx": RequestContext(session_id=session_id),
                "graph": None,
                "session_id": session_id,
                "kb_id": kb_id,
                "query": query,
                "deep_thinking": deep_thinking,
            },
        )

    mock_svc = AsyncMock()
    mock_svc.agent_service.stream_chat = fake_stream_chat
    app.dependency_overrides[get_app_service] = lambda: mock_svc

    try:
        with patch("src.services.turn_runner._run_with_finalize", new=AsyncMock()):
            response = client.post(
                "/api/chat/stream",
                json={
                    "session_id": "s1",
                    "kb_id": "kb-1",
                    "query": "hi",
                    "agent": "finance-expert",
                },
            )
        assert response.status_code == 200
        assert captured["agent"] == "finance-expert"
    finally:
        streaming_manager.clear_buffer("s1")
        app.dependency_overrides.pop(get_app_service, None)


@pytest.mark.asyncio
async def test_normal_answer_persisted():
    """正常回答（有 token 无澄清）时后台任务将完整回答落库为 complete。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.turn_runner import _run_with_finalize

    statuses = []
    svc = MagicMock()
    svc.save_assistant_async = AsyncMock(
        side_effect=lambda *a, **k: statuses.append(a[4])
    )
    svc.chat_manager = MagicMock()
    svc.chat_manager.add_message_async = AsyncMock()
    partial_holder = {"text": ""}

    async def answer_builder():
        partial_holder["text"] = "净利润100亿"
        return "净利润100亿"

    await _run_with_finalize(
        svc,
        "s1",
        "kb1",
        partial_holder,
        answer_builder,
        StreamingRunManager(),
        asyncio.Event(),
        lambda: None,
        RequestContext(session_id="s1"),
    )
    assert statuses == ["complete"]
    svc.save_assistant_async.assert_awaited_once()
