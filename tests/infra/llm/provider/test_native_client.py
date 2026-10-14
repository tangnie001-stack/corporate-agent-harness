"""native client 出口：端点/密钥取自 resolve_role，与 build_chat 同源。"""

import pytest

from src.infra.llm.provider import factory


def test_build_native_client_uses_resolved_endpoint(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    client = factory.build_native_client("judge")

    assert str(client.base_url).rstrip("/").endswith("/compatible-mode/v1")
    assert client.api_key == "k"


def test_build_native_client_rejects_unknown_role(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    with pytest.raises(KeyError):
        factory.build_native_client("nope")


def test_build_native_client_passes_timeout_when_given(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    client = factory.build_native_client("judge", timeout=12.5)

    assert client.timeout == 12.5
