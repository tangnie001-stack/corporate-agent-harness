"""主链路 LLM 的本层有界重试：**只在首个 chunk 之前**重试。

归属与理由（change `timeout-and-degradation-hardening` 的 design D16）：
- 关掉 SDK 重试后若不补本层重试，主链路会从"重试 2 次"变成**零重试**（相对现状倒退）；
- 但**流已开始输出就不能重试**（会重复输出给用户）⇒ 重试窗口仅限首个 chunk 之前；
- 必须实现为**模型对象子类**：`Runnable.with_retry` 明确不重试 stream
  （`langchain_core/runnables/retry.py:378`），循环 middleware 又拿不到 chunk。
- 本类定义在 provider 兼容层**之外**（ADR-0021：该层不做重试策略）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from loguru import logger

from src.core.log_events import Event
from src.core.logging import log_event
from src.infra import retry_policy
from src.infra.llm.provider.chat import ReasoningPreservingChat


class RetryingReasoningChat(ReasoningPreservingChat):
    """在"首个 chunk 之前"失败时按 `retry_policy` 有界重试。"""

    # pydantic 声明式字段：阈值由构造方（factory）从 settings 注入，本类不读环境
    model_retry_attempts: int = 3
    model_retry_initial_interval: float = 1.0
    model_retry_backoff: float = 2.0
    model_retry_jitter: float = 0.3
    model_retry_role: str = ""

    def _should_retry(self, exc: BaseException, attempt: int) -> bool:
        """是否重试：未达上限且异常属可重试类（取消/未知异常默认不重试）。"""
        return attempt < self.model_retry_attempts and retry_policy.is_retryable(exc)

    async def _sleep_before_retry(self, attempt: int) -> None:
        """按 `retry_policy` 算等待秒数并 `await asyncio.sleep`（不阻塞事件循环）。"""
        await asyncio.sleep(
            retry_policy.sleep_seconds(
                attempt,
                self.model_retry_initial_interval,
                self.model_retry_backoff,
                self.model_retry_jitter,
            )
        )

    def _log_retry(self, attempt: int, exc: BaseException) -> None:
        """留痕一次重试（role/attempt/err）。"""
        log_event(
            Event.MODEL_RETRY,
            role=self.model_retry_role or "unknown",
            attempt=attempt,
            err=type(exc).__name__,
        )

    async def _astream(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        """流式：仅在**尚未产出任何 chunk** 时重试。"""
        attempt = 0
        while True:
            produced = False
            try:
                async for chunk in super()._astream(*args, **kwargs):
                    produced = True
                    yield chunk
                return
            except BaseException as exc:  # 由 _should_retry 判定是否重试
                attempt += 1
                if produced or not self._should_retry(exc, attempt):
                    logger.warning(
                        "[llm] model retry skipped role={} produced={} err={}",
                        self.model_retry_role,
                        produced,
                        type(exc).__name__,
                    )
                    raise
                self._log_retry(attempt, exc)
                await self._sleep_before_retry(attempt)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """非流式：同一策略（无"已产出"概念，整次失败才重试）。"""
        attempt = 0
        while True:
            try:
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            except BaseException as exc:  # 由 _should_retry 判定是否重试
                attempt += 1
                if not self._should_retry(exc, attempt):
                    # 与 _astream 对齐：非流式路径也要留下"为何没重试"的证据
                    logger.warning(
                        "[llm] model retry skipped role={} path=agenerate err={} attempt={}",
                        self.model_retry_role,
                        type(exc).__name__,
                        attempt,
                    )
                    raise
                self._log_retry(attempt, exc)
                await self._sleep_before_retry(attempt)
