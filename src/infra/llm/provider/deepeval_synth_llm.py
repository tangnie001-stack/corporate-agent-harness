"""出题用 DeepEval LLM 适配器：schema 解析 + 传输层重试。

为什么不能直接用 `BridgeDeepEvalLLM`：Synthesizer 对非原生模型执行
`res = model.generate(prompt, schema=Response); return res.response`，
而父类把 `schema` 吞进 `**kwargs` 并返回 `str` ⇒ `AttributeError`。
本类只覆盖 schema 分支与重试，指标路径仍走父类。
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

# 必须先 import settings：它在 deepeval 之前设置 DEEPEVAL_DISABLE_DOTENV /
# DEEPEVAL_TELEMETRY_OPT_OUT，避免框架抢先加载 .env 与遥测。
from src.config import settings  # noqa: F401
from src.config.const import (
    SYNTH_RETRY_AFTER_MAX,
    SYNTH_RETRY_BASE_DELAY,
    SYNTH_RETRY_MAX_ATTEMPTS,
    SYNTH_RETRY_MAX_DELAY,
)
from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM, _truncate_prompt

# isort: split
from deepeval.metrics.utils.generation import trimAndLoadJson
from deepeval.synthesizer.schema import Response
from httpx import TimeoutException, TransportError
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel


def _is_retryable(exc: BaseException) -> bool:
    """判断异常是否属于可重试的传输/限流失败。

    Args:
        exc: 单次调用抛出的异常

    Returns:
        True 表示 429/5xx/超时/连接失败
    """
    if isinstance(exc, APITimeoutError):
        return True
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        code = exc.status_code
        return code == 429 or 500 <= code < 600
    return isinstance(exc, (TimeoutException, TransportError))


def _retry_delay(exc: BaseException, attempt: int) -> float:
    """计算第 attempt 次失败后的等待秒数。

    Args:
        exc: 本次异常
        attempt: 1-based 尝试序号

    Returns:
        优先 Retry-After（上限 30s），否则指数退避（上限 8s）
    """
    raw: str | None = None
    if isinstance(exc, APIStatusError):
        raw = exc.response.headers.get("retry-after")
    if raw is not None:
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            delay = None
        if delay is not None and math.isfinite(delay) and delay >= 0:
            return min(delay, SYNTH_RETRY_AFTER_MAX)
    return min(SYNTH_RETRY_BASE_DELAY * (2 ** (attempt - 1)), SYNTH_RETRY_MAX_DELAY)


class SynthDeepEvalLLM(BridgeDeepEvalLLM):
    """给 Synthesizer 用的适配器：支持 schema，并对传输失败做有界重试。"""

    def _coerce(self, text: str, schema: type[BaseModel] | None) -> Any:
        """把文本按 schema 转成 DeepEval 期望的对象。

        Args:
            text: 裁判返回的原始文本
            schema: None / Response / 其它 Pydantic 模型

        Returns:
            str 或 Pydantic 实例

        Raises:
            ValueError: JSON 无法解析或字段不匹配
        """
        if schema is None:
            return text
        if schema is Response:
            return Response(response=text)
        return schema(**trimAndLoadJson(text))

    def _invoke(self, prompt: str) -> str:
        """同步调用并在可重试异常上退避。

        Args:
            prompt: 已截断的提示词

        Returns:
            模型文本

        Raises:
            最后一次失败的原异常
        """
        for attempt in range(1, SYNTH_RETRY_MAX_ATTEMPTS + 2):
            try:
                return self._content(self._chat.invoke(prompt))
            except Exception as exc:
                if attempt > SYNTH_RETRY_MAX_ATTEMPTS or not _is_retryable(exc):
                    raise
                time.sleep(_retry_delay(exc, attempt))
        raise RuntimeError("unreachable")

    async def _ainvoke(self, prompt: str) -> str:
        """异步调用并在可重试异常上退避（不阻塞事件循环）。

        Args:
            prompt: 已截断的提示词

        Returns:
            模型文本

        Raises:
            最后一次失败的原异常
        """
        for attempt in range(1, SYNTH_RETRY_MAX_ATTEMPTS + 2):
            try:
                return self._content(await self._chat.ainvoke(prompt))
            except Exception as exc:
                if attempt > SYNTH_RETRY_MAX_ATTEMPTS or not _is_retryable(exc):
                    raise
                await asyncio.sleep(_retry_delay(exc, attempt))
        raise RuntimeError("unreachable")

    def generate(
        self, prompt: str, schema: type[BaseModel] | None = None, **kwargs: Any
    ) -> Any:
        """同步生成；schema 非空时返回 Pydantic 实例。

        Args:
            prompt: Synthesizer 拼好的提示词
            schema: None 返回原始文本；Response 包装成响应对象；其余按 JSON 解析
            **kwargs: 父类签名的兼容位，当前无消费语义

        Returns:
            str 或 Pydantic 实例
        """
        return self._coerce(self._invoke(_truncate_prompt(prompt)), schema)

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None, **kwargs: Any
    ) -> Any:
        """异步生成；schema 非空时返回 Pydantic 实例。

        Args:
            prompt: Synthesizer 拼好的提示词
            schema: None 返回原始文本；Response 包装成响应对象；其余按 JSON 解析
            **kwargs: 父类签名的兼容位，当前无消费语义

        Returns:
            str 或 Pydantic 实例
        """
        return self._coerce(await self._ainvoke(_truncate_prompt(prompt)), schema)
