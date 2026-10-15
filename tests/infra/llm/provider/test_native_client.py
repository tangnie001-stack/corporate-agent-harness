"""native client 出口：端点/密钥取自 resolve_role，与 build_chat 同源。"""

import httpx
import pytest

from src.config import settings
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


def test_build_native_client_read_timeout_override(monkeypatch):
    """显式传 read_timeout 时只覆盖 read，其余细粒度项仍取默认。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    client = factory.build_native_client("judge", read_timeout=12.5)

    timeout = client.timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 12.5
    assert timeout.connect == settings.RAGAS_HTTP_TIMEOUT_CONNECT
