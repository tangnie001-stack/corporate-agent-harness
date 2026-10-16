"""适配器：把 provider 层裁判接进 DeepEval 原生指标。

为何需要：`initialize_model` 只接受 `str | DeepEvalBaseLLM`
（deepeval `metrics/utils/models.py:170`），langchain 模型不被接受；
且非 native 模型会退回 `generate(prompt)`、由 DeepEval 自己解析 JSON
（`models/base_model.py:151`）⇒ 本类只需返回内容文本。
"""

from __future__ import annotations

import pytest
from deepeval.models import DeepEvalBaseLLM
from langchain_core.messages import AIMessage

from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM


class _FakeChat:
    """最小化身：记录调用并回固定 JSON 文本。

    返回**真实** `AIMessage`（langchain 实际返回的类型）：使适配器的 `_content`
    走 `isinstance(msg, BaseMessage)` 分支。用自定义假消息类会落到 `str(msg)`
    分支、拿不到内容。
    """

    model_name = "fake-judge"

    def __init__(self) -> None:
        self.invoked: list[str] = []
        self.ainvoked: list[str] = []

    def invoke(self, prompt: str) -> AIMessage:
        self.invoked.append(prompt)
        return AIMessage(content='{"verdict": "yes", "reason": "ok"}')

    async def ainvoke(self, prompt: str) -> AIMessage:
        self.ainvoked.append(prompt)
        return AIMessage(content='{"verdict": "yes", "reason": "ok"}')


def test_adapter_is_a_deepeval_llm_and_reports_name():
    chat = _FakeChat()
    llm = BridgeDeepEvalLLM(chat)

    assert isinstance(llm, DeepEvalBaseLLM)
    assert llm.get_model_name() == "fake-judge"
    # `super().__init__()` 生效的证据：基类据 load_model() 置 self.model
    # （deepeval `models/base_model.py:75`）
    assert llm.load_model() is chat
    assert llm.model is chat


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
