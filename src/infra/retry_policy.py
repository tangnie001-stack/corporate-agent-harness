"""重试策略原语 —— 异常分类 + 有界退避 + 抖动（无状态、不持配置）。

归属：change `timeout-and-degradation-hardening` 的 design D1 ② / D5。
本模块只回答"**该不该重试**、**等多久**"：
- **不持配置**（阈值由调用方从 `settings` 传入，故可被 env 覆盖）；
- **不做降级动作**（那是各调用点的事，见 design D1 ③）；
- **不落 provider 层**（ADR-0021：该层不做 fallback / 重试策略）。

分类判据（D5，按"重试是否会改变结果"）：
- **不可重试**：输入校验类（`ValueError` / `TypeError`，特征为失败耗时 ≈ 0）、
  鉴权 / 权限类、请求格式类（400/422）；
- **可重试**：超时、连接错误、429、5xx；
- **未列出的异常默认不重试** —— 保守选择：不拿未知错误去放大调用次数。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import TypeVar

import httpx
from loguru import logger

T = TypeVar("T")

# ── 不可重试：显式列出（便于阅读与测试；未知异常默认也不重试） ──
_NON_RETRYABLE: tuple[type[BaseException], ...] = (
    ValueError,  # 含输入校验类（如"检索上下文为空"）
    TypeError,
    PermissionError,
    NotImplementedError,
)

# ── 可重试：与 provider 无关的传输类 ──
_RETRYABLE_BASE: tuple[type[BaseException], ...] = (
    TimeoutError,  # 含 asyncio.TimeoutError
    ConnectionError,
    httpx.TimeoutException,  # 含 ConnectTimeout / ReadTimeout
    httpx.TransportError,  # 含 ConnectError / ReadError
)

# openai SDK 的异常类名（该包可能未安装或版本不同 ⇒ 按名取、缺则跳过）
_OPENAI_RETRYABLE = (
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
)
_OPENAI_NON_RETRYABLE = (
    "AuthenticationError",
    "PermissionDeniedError",
    "BadRequestError",
    "UnprocessableEntityError",
)


@lru_cache(maxsize=1)
def _tables() -> tuple[
    tuple[type[BaseException], ...], tuple[type[BaseException], ...]
]:
    """合并基础表与 openai 异常类（进程内缓存一次）。"""
    retryable: list[type[BaseException]] = list(_RETRYABLE_BASE)
    non_retryable: list[type[BaseException]] = list(_NON_RETRYABLE)
    try:
        import openai
    except Exception:  # noqa: BLE001 - 缺依赖时只用基础表
        return tuple(retryable), tuple(non_retryable)
    for name in _OPENAI_RETRYABLE:
        cls = getattr(openai, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            retryable.append(cls)
    for name in _OPENAI_NON_RETRYABLE:
        cls = getattr(openai, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            non_retryable.append(cls)
    return tuple(retryable), tuple(non_retryable)


def is_retryable(exc: BaseException) -> bool:
    """该异常重试是否可能改变结果（判据见模块 docstring）。"""
    retryable, non_retryable = _tables()
    if isinstance(exc, tuple(non_retryable)):
        return False
    return isinstance(exc, tuple(retryable))


def sleep_seconds(
    attempt: int, initial_interval: float, backoff: float, jitter: float
) -> float:
    """第 `attempt` 次重试前的等待秒数（attempt 从 1 计）。

    基础值 = `initial_interval * backoff ** (attempt - 1)`，再叠加 ±`jitter` 比例的
    随机抖动（防多请求同时重试的惊群）。`jitter=0` 时为确定性值（便于测试）。
    """
    base = initial_interval * (backoff ** (attempt - 1))
    if jitter <= 0:
        return max(0.0, base)
    return max(0.0, base * (1 + random.uniform(-jitter, jitter)))


def _log_retry(
    name: str, attempt: int, attempts: int, exc: BaseException, wait: float
) -> None:
    logger.warning(
        "[retry] {} failed attempt={}/{} err={} wait={:.2f}s",
        name,
        attempt,
        attempts,
        type(exc).__name__,
        wait,
    )


def call_with_retry(  # noqa: UP047 - 泛型原语需在 3.11 兼容签名下暴露 T
    fn: Callable[..., T],
    *args: object,
    attempts: int,
    initial_interval: float,
    backoff: float,
    jitter: float = 0.3,
    on_retry: Callable[[int, BaseException], None] | None = None,
    **kwargs: object,
) -> T:
    """同步调用 `fn`，失败按 `is_retryable` 有界重试。"""
    name = getattr(fn, "__name__", repr(fn))
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:  # 由 is_retryable 决定是否重试
            if attempt >= attempts or not is_retryable(exc):
                raise
            wait = sleep_seconds(attempt, initial_interval, backoff, jitter)
            if on_retry is not None:
                on_retry(attempt, exc)
            _log_retry(name, attempt, attempts, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"{name} failed after {attempts} attempts")  # pragma: no cover


async def acall_with_retry(  # noqa: UP047 - 泛型原语需在 3.11 兼容签名下暴露 T
    fn: Callable[..., Awaitable[T]],
    *args: object,
    attempts: int,
    initial_interval: float,
    backoff: float,
    jitter: float = 0.3,
    on_retry: Callable[[int, BaseException], None] | None = None,
    **kwargs: object,
) -> T:
    """异步版：等待用 `await asyncio.sleep`，**不阻塞事件循环**。"""
    name = getattr(fn, "__name__", repr(fn))
    for attempt in range(1, attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except BaseException as exc:  # 由 is_retryable 决定是否重试
            if attempt >= attempts or not is_retryable(exc):
                raise
            wait = sleep_seconds(attempt, initial_interval, backoff, jitter)
            if on_retry is not None:
                on_retry(attempt, exc)
            _log_retry(name, attempt, attempts, exc, wait)
            await asyncio.sleep(wait)
    raise RuntimeError(f"{name} failed after {attempts} attempts")  # pragma: no cover
