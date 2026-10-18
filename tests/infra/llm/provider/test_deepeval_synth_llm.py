"""出题适配器：schema 解析 + openai 异常重试。"""

from __future__ import annotations

import asyncio

import httpx
import openai
import pytest
from deepeval.synthesizer.schema import Response, SyntheticData
from langchain_core.messages import AIMessage

from src.infra.llm.provider.deepeval_synth_llm import SynthDeepEvalLLM


def _rate_limit(retry_after: str = "12") -> openai.RateLimitError:
    """构造带 Retry-After 的真实 openai 429 异常。"""
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(
        429, request=request, headers={"retry-after": retry_after}
    )
    return openai.RateLimitError("rate limited", response=response, body=None)


class _FlakyChat:
    """前 failures 次抛指定异常，之后返回固定 JSON。"""

    model_name = "fake-judge"

    def __init__(self, error: Exception, failures: int = 1) -> None:
        self._error = error
        self._failures = failures
        self.calls = 0

    def invoke(self, prompt: str, **kwargs):
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return AIMessage(content='{"response": "ok"}')

    async def ainvoke(self, prompt: str, **kwargs):
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return AIMessage(content='{"response": "ok"}')


class _JsonChat:
    """返回可被 schema 解析的 JSON。"""

    model_name = "fake-judge"

    def __init__(self, content: str) -> None:
        self._content = content

    def invoke(self, prompt: str, **kwargs):
        return AIMessage(content=self._content)

    async def ainvoke(self, prompt: str, **kwargs):
        return AIMessage(content=self._content)


def test_generate_without_schema_returns_text():
    llm = SynthDeepEvalLLM(_FlakyChat(RuntimeError(), failures=0))
    assert llm.generate("prompt") == '{"response": "ok"}'


def test_generate_response_schema_has_response_attribute():
    llm = SynthDeepEvalLLM(_FlakyChat(RuntimeError(), failures=0))
    result = llm.generate("prompt", schema=Response)
    assert isinstance(result, Response)
    assert result.response == '{"response": "ok"}'


def test_generate_other_schema_returns_model_instance():
    llm = SynthDeepEvalLLM(_JsonChat('{"input": "问题"}'))
    result = llm.generate("prompt", schema=SyntheticData)
    assert isinstance(result, SyntheticData)
    assert result.input == "问题"


def test_invalid_json_raises_value_error_not_type_error():
    llm = SynthDeepEvalLLM(_JsonChat("not json at all"))
    with pytest.raises(ValueError):
        llm.generate("prompt", schema=SyntheticData)


def test_rate_limit_is_retried_with_retry_after(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(
        "src.infra.llm.provider.deepeval_synth_llm.time.sleep",
        lambda seconds: slept.append(seconds),
    )
    llm = SynthDeepEvalLLM(_FlakyChat(_rate_limit("12")))
    assert llm.generate("prompt") == '{"response": "ok"}'
    assert slept == [12.0]


def test_async_rate_limit_is_retried(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(
        "src.infra.llm.provider.deepeval_synth_llm.asyncio.sleep", fake_sleep
    )
    llm = SynthDeepEvalLLM(_FlakyChat(_rate_limit("3")))
    result = asyncio.run(llm.a_generate("prompt"))
    assert result == '{"response": "ok"}'
    assert slept == [3.0]


def test_non_rate_limit_4xx_is_not_retried():
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(404, request=request)
    error = openai.NotFoundError("not found", response=response, body=None)
    chat = _FlakyChat(error)
    llm = SynthDeepEvalLLM(chat)
    with pytest.raises(openai.NotFoundError):
        llm.generate("prompt")
    assert chat.calls == 1


def test_adapter_uses_passed_chat_not_default_model():
    chat = _FlakyChat(RuntimeError(), failures=0)
    llm = SynthDeepEvalLLM(chat)
    assert llm.model is chat
    assert llm.get_model_name() == "fake-judge"
