"""`/llm/test` 端点回归：空 `model` 经门面解析到 registry 默认。

Important 修复回归 —— 修复前 `model_name = body.model or settings.LLM_MODEL`
在 `settings.LLM_MODEL` 默认空串（design D3 "可选覆盖"）时会以空模型名调用。
"""

from types import SimpleNamespace

import pytest

from src.api import llm_test as llm_test_mod


class _FakeChat:
    """假 ChatOpenAI：只需 model_name + invoke。"""

    def __init__(self, model_name: str = "kimi-k3") -> None:
        self.model_name = model_name

    def invoke(self, prompt: str) -> SimpleNamespace:
        return SimpleNamespace(content="OK")


@pytest.mark.asyncio
async def test_empty_model_resolves_via_facade(monkeypatch):
    """空 model → 以 None 交门面（registry 兜底），返回值取解析后的 model_name。"""
    captured: dict[str, object] = {}

    def fake_get_llm(model=None, temperature=None, **kwargs):
        captured["model"] = model
        return _FakeChat("kimi-k3")

    monkeypatch.setattr(llm_test_mod, "get_llm", fake_get_llm)

    resp = await llm_test_mod.llm_test(llm_test_mod.LlmTestRequest(prompt="ping"))

    assert captured["model"] is None
    assert resp.code == "SUCCESS"
    assert resp.data["model"] == "kimi-k3"
    assert resp.data["response"] == "OK"


@pytest.mark.asyncio
async def test_explicit_model_is_passed_through(monkeypatch):
    """显式 model 原样透传给门面。"""
    captured: dict[str, object] = {}

    def fake_get_llm(model=None, temperature=None, **kwargs):
        captured["model"] = model
        return _FakeChat("kimi-k3")

    monkeypatch.setattr(llm_test_mod, "get_llm", fake_get_llm)

    body = llm_test_mod.LlmTestRequest(model="kimi-k3", prompt="ping")
    await llm_test_mod.llm_test(body)

    assert captured["model"] == "kimi-k3"
