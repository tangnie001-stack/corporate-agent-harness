"""retry_policy 原语单测：异常分类 + 有界 + 抖动 + 异步版。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.infra import retry_policy


class _Fake429(Exception):
    """模拟 openai.RateLimitError 的形态（本测试不依赖 openai 包）。"""


def test_value_error_not_retryable():
    assert retry_policy.is_retryable(ValueError("检索上下文为空")) is False


def test_timeout_retryable():
    assert retry_policy.is_retryable(TimeoutError()) is True
    assert retry_policy.is_retryable(httpx.ReadTimeout("read timeout")) is True


def test_connection_retryable():
    assert retry_policy.is_retryable(ConnectionError("reset")) is True


def test_unknown_exception_not_retryable():
    """未列出的异常默认不重试（保守：不拿未知错误去放大调用）。"""

    class Weird(Exception):
        pass

    assert retry_policy.is_retryable(Weird("?")) is False


def test_call_with_retry_succeeds_after_transient_failure():
    calls: list[int] = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("boom")
        return "ok"

    result = retry_policy.call_with_retry(
        flaky, attempts=3, initial_interval=0.0, backoff=2.0, jitter=0.0
    )
    assert result == "ok"
    assert len(calls) == 3


def test_call_with_retry_stops_on_non_retryable():
    calls: list[int] = []

    def bad_input():
        calls.append(1)
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        retry_policy.call_with_retry(
            bad_input, attempts=3, initial_interval=0.0, backoff=2.0, jitter=0.0
        )
    assert len(calls) == 1, "输入校验类必须只尝试一次"


def test_call_with_retry_bounded_by_attempts():
    calls: list[int] = []

    def always_timeout():
        calls.append(1)
        raise TimeoutError("always")

    with pytest.raises(TimeoutError):
        retry_policy.call_with_retry(
            always_timeout, attempts=3, initial_interval=0.0, backoff=2.0, jitter=0.0
        )
    assert len(calls) == 3


def test_on_retry_callback_receives_attempt_and_exception():
    seen: list[tuple[int, str]] = []

    def flaky():
        if len(seen) < 2:
            raise TimeoutError("x")
        return "ok"

    retry_policy.call_with_retry(
        flaky,
        attempts=3,
        initial_interval=0.0,
        backoff=2.0,
        jitter=0.0,
        on_retry=lambda attempt, exc: seen.append((attempt, type(exc).__name__)),
    )
    assert seen == [(1, "TimeoutError"), (2, "TimeoutError")]


def test_sleep_seconds_has_jitter_within_range():
    base = retry_policy.sleep_seconds(2, 1.0, 2.0, jitter=0.0)
    assert base == 2.0
    lo = retry_policy.sleep_seconds(2, 1.0, 2.0, jitter=0.5)
    assert 1.0 <= lo <= 3.0, "±50% 抖动应落在 [base*(1-0.5), base*(1+0.5)]"


def test_acall_with_retry_uses_async_sleep():
    calls: list[int] = []

    async def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise TimeoutError("boom")
        return "ok"

    result = asyncio.run(
        retry_policy.acall_with_retry(
            flaky, attempts=3, initial_interval=0.0, backoff=2.0, jitter=0.0
        )
    )
    assert result == "ok"
    assert len(calls) == 2
