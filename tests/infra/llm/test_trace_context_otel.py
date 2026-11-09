"""observe_with_trace：显式绑定 trace id，并在 asyncio.to_thread 中保持归属。

不发网络：客户端用替身记录传入的 TraceContext。
"""

import asyncio
import contextlib

import pytest

from src.infra.llm import tracing
from src.infra.llm.trace_context import current_trace_id

TID = "0123456789abcdef0123456789abcdef"


class _FakeClient:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def start_as_current_observation(self, **kwargs):
        self.contexts.append(kwargs["trace_context"])

        @contextlib.contextmanager
        def _cm():
            yield object()

        return _cm()


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(tracing, "tracing_enabled", lambda: True)
    monkeypatch.setattr(tracing, "get_client", lambda: client)
    return client


def test_sync_binds_current_trace(fake_client):
    @tracing.observe_with_trace("unit")
    def work() -> str:
        return current_trace_id.get() or ""

    token = current_trace_id.set(TID)
    try:
        assert work() == TID
    finally:
        current_trace_id.reset(token)

    assert fake_client.contexts[0]["trace_id"] == TID


@pytest.mark.asyncio
async def test_thread_pool_keeps_trace_attribution(fake_client):
    @tracing.observe_with_trace("unit")
    def work() -> str:
        return current_trace_id.get() or ""

    token = current_trace_id.set(TID)
    try:
        seen = await asyncio.to_thread(work)
    finally:
        current_trace_id.reset(token)

    assert seen == TID
    assert fake_client.contexts[0]["trace_id"] == TID


def test_disabled_skips_client(monkeypatch):
    monkeypatch.setattr(tracing, "tracing_enabled", lambda: False)
    monkeypatch.setattr(
        tracing,
        "get_client",
        lambda: pytest.fail("disabled tracing must not touch client"),
    )

    @tracing.observe_with_trace("unit")
    def work() -> str:
        return "ok"

    assert work() == "ok"
