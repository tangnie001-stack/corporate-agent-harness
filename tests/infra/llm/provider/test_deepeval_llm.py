"""适配器：把 provider 层裁判接进 DeepEval 原生指标。

为何需要：`initialize_model` 只接受 `str | DeepEvalBaseLLM`
（deepeval `metrics/utils/models.py:170`），langchain 模型不被接受；
且非 native 模型会退回 `generate(prompt)`、由 DeepEval 自己解析 JSON
（`models/base_model.py:151`）⇒ 本类只需返回内容文本。
"""

from __future__ import annotations

import pytest
from deepeval.models import DeepEvalBaseLLM

from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM


class _FakeMsg:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChat:
    """最小化身：记录调用并回固定 JSON 文本。"""

    model_name = "fake-judge"

    def __init__(self) -> None:
        self.invoked: list[str] = []
        self.ainvoked: list[str] = []

    def invoke(self, prompt: str) -> _FakeMsg:
        self.invoked.append(prompt)
        return _FakeMsg('{"verdict": "yes", "reason": "ok"}')

    async def ainvoke(self, prompt: str) -> _FakeMsg:
        self.ainvoked.append(prompt)
        return _FakeMsg('{"verdict": "yes", "reason": "ok"}')


def test_adapter_is_a_deepeval_llm_and_reports_name():
    llm = BridgeDeepEvalLLM(_FakeChat())

    assert isinstance(llm, DeepEvalBaseLLM)
    assert llm.get_model_name() == "fake-judge"
    assert llm.load_model() is not None


def test_generate_returns_content_text():
    chat = _FakeChat()
    llm = BridgeDeepEvalLLM(chat)

    out = llm.generate("prompt-A")

    assert out == '{"verdict": "yes", "reason": "ok"}'
    assert chat.invoked == ["prompt-A"]


@pytest.mark.asyncio
async def test_a_generate_returns_content_text():
    chat = _FakeChat()
    llm = BridgeDeepEvalLLM(chat)

    out = await llm.a_generate("prompt-B")

    assert out == '{"verdict": "yes", "reason": "ok"}'
    assert chat.ainvoked == ["prompt-B"]
