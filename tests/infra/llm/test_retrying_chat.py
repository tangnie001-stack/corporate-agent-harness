"""RetryingReasoningChat：只在首个 chunk 之前重试，且不阻塞事件循环。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from src.infra.llm.provider.chat import ReasoningPreservingChat
from src.infra.llm.retrying_chat import RetryingReasoningChat

# ⚠ 关键：必须 patch **父类** `ReasoningPreservingChat._astream`，而不是被测的
# `RetryingReasoningChat._astream` —— 后者一 patch 就把"重试包装"本身换掉了，
# 测试会变成假通过。被测层调 `super()._astream(...)` ⇒ 落在被 patch 的父类上。


def _chunk(text: str) -> ChatGenerationChunk:
    return ChatGenerationChunk(message=AIMessageChunk(content=text))


def _model(**kwargs) -> RetryingReasoningChat:
    return RetryingReasoningChat(
        model="fake",
        api_key="k",  # type: ignore[arg-type]
        base_url="http://localhost:1",
        max_retries=0,
        **kwargs,
    )


def test_retries_before_first_chunk(monkeypatch):
    model = _model(model_retry_attempts=3, model_retry_initial_interval=0.0)
    calls: list[int] = []

    async def fake_astream(self, *args, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("boom")
        yield _chunk("ok")

    monkeypatch.setattr(ReasoningPreservingChat, "_astream", fake_astream, raising=True)

    async def run() -> list[object]:
        return [c.message.content async for c in model._astream([])]

    assert asyncio.run(run()) == ["ok"]
    assert len(calls) == 3


def test_does_not_retry_after_first_chunk(monkeypatch):
    model = _model(model_retry_attempts=3, model_retry_initial_interval=0.0)
    calls: list[int] = []

    async def fake_astream(self, *args, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        calls.append(1)
        yield _chunk("partial")
        raise TimeoutError("mid-stream")

    monkeypatch.setattr(ReasoningPreservingChat, "_astream", fake_astream, raising=True)

    async def run() -> list[object]:
        # content 的静态类型为 str | list[...]（多模态载荷）⇒ 收成 list[object]，
        # 本用例只关心"已产出的那一段文本"
        out: list[object] = []
        with pytest.raises(TimeoutError):
            async for c in model._astream([]):
                out.append(c.message.content)
        return out

    assert asyncio.run(run()) == ["partial"], "已产出后不得重试（否则重复输出）"
    assert len(calls) == 1


def test_does_not_retry_non_retryable(monkeypatch):
    model = _model(model_retry_attempts=3, model_retry_initial_interval=0.0)
    calls: list[int] = []

    async def fake_astream(self, *args, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        calls.append(1)
        raise ValueError("bad input")
        if False:  # pragma: no cover - 仅为让本函数成为 async generator
            yield _chunk("")

    monkeypatch.setattr(ReasoningPreservingChat, "_astream", fake_astream, raising=True)

    async def run() -> None:
        async for _ in model._astream([]):
            pass

    with pytest.raises(ValueError):
        asyncio.run(run())
    assert len(calls) == 1, "输入校验类不得不重试"


def test_cancellation_is_not_retried(monkeypatch):
    """取消是用户主动动作（request-abort 契约）：必须原样上抛且不重试。

    `asyncio.CancelledError` 属 `BaseException`，会被本层的 `except BaseException`
    捕获；判据是 `is_retryable(CancelledError) is False`（该类不在可重试表内），
    故实现**不得**为它加特例分支。
    """
    model = _model(model_retry_attempts=3, model_retry_initial_interval=0.0)
    calls: list[int] = []

    async def fake_astream(self, *args, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        calls.append(1)
        raise asyncio.CancelledError()
        if False:  # pragma: no cover - 仅为让本函数成为 async generator
            yield _chunk("")

    monkeypatch.setattr(ReasoningPreservingChat, "_astream", fake_astream, raising=True)

    async def run() -> None:
        async for _ in model._astream([]):
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert len(calls) == 1, "取消不得被重试"


def test_cancellation_is_not_retryable():
    """直接钉死 `retry_policy.is_retryable(CancelledError) is False`（实现依赖点）。"""
    from src.infra import retry_policy

    assert retry_policy.is_retryable(asyncio.CancelledError()) is False


def test_factory_returns_retrying_chat_with_injected_settings(monkeypatch):
    """factory 必须构造本子类并注入 settings 阈值与 role（D）。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    from src.config import settings
    from src.infra.llm.provider import factory

    model = factory.build_chat("main")
    assert isinstance(model, RetryingReasoningChat)
    assert model.model_retry_attempts == settings.RETRY_MAX_ATTEMPTS
    assert model.model_retry_initial_interval == settings.RETRY_INITIAL_INTERVAL
    assert model.model_retry_backoff == settings.RETRY_BACKOFF_FACTOR
    assert model.model_retry_role == "main"
