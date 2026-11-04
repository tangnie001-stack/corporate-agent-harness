"""McpLogSource 的单测：用 MockTransport 模拟 JSON-RPC 握手与 tools/call。"""

import json

import httpx
import pytest

from src.rca.sources.base import SourceEmptyError, SourceFailedError
from src.rca.sources.mcp import McpLogSource

_STATS = {
    "uri": "/x",
    "total": 2,
    "latency_ms": {"p95": 1057, "max": 1057},
    "slow": {"threshold": 1000, "count": 1},
    "errors": {"5xx": 0},
    "by_node": [{"addr": "172.18.0.1:8083", "cnt": 2, "p95": 1057}],
}


def _sse(obj: dict) -> str:
    """把对象包成 SSE 帧。"""
    return "event: message" + chr(10) + "data:" + json.dumps(obj, ensure_ascii=False)


def _handler(calls: list, tool_payload: dict, is_error: bool = False):
    """构造 MockTransport 处理器：initialize 回 JSON，tools/call 回 SSE。"""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append(body)
        if body.get("method") == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-06-18"},
                },
                headers={"mcp-session-id": "sess-1"},
            )
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202, text="")
        return httpx.Response(
            200,
            text=_sse(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "isError": is_error,
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(tool_payload, ensure_ascii=False),
                            }
                        ],
                    },
                }
            ),
            headers={"content-type": "text/event-stream"},
        )

    return handle


@pytest.mark.asyncio
async def test_handshake_and_latency_stats():
    calls = []
    transport = httpx.MockTransport(_handler(calls, _STATS))
    source = McpLogSource("https://m/mcp", "tok", timeout=5.0, transport=transport)

    data = await source.latency_stats(uri="/x", start_ts=1, end_ts=2, slow_ms=1000)

    assert data["total"] == 2
    assert calls[0]["method"] == "initialize"
    assert calls[1]["method"] == "notifications/initialized"
    assert calls[2]["params"]["name"] == "sls_latency_stats"
    assert calls[2]["params"]["arguments"]["slow_ms"] == 1000


@pytest.mark.asyncio
async def test_empty_total_raises_empty():
    calls = []
    transport = httpx.MockTransport(_handler(calls, {"uri": "/x", "total": 0}))
    source = McpLogSource("https://m/mcp", "tok", timeout=5.0, transport=transport)

    with pytest.raises(SourceEmptyError):
        await source.latency_stats(uri="/x", start_ts=1, end_ts=2)


@pytest.mark.asyncio
async def test_tool_error_raises_failed():
    calls = []
    transport = httpx.MockTransport(_handler(calls, {"detail": "boom"}, is_error=True))
    source = McpLogSource("https://m/mcp", "tok", timeout=5.0, transport=transport)

    with pytest.raises(SourceFailedError):
        await source.request_detail(uri="/x", start_ts=1, end_ts=2)


@pytest.mark.asyncio
async def test_401_raises_failed():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    source = McpLogSource(
        "https://m/mcp", "bad", timeout=5.0, transport=httpx.MockTransport(handle)
    )
    with pytest.raises(SourceFailedError):
        await source.latency_stats(uri="/x", start_ts=1, end_ts=2)


@pytest.mark.asyncio
async def test_business_impact_passes_uris():
    calls = []
    payload = {"endpoints": [{"uri": "/x", "pv": 3, "base_pv": 4}]}
    transport = httpx.MockTransport(_handler(calls, payload))
    source = McpLogSource(
        "https://m/mcp", "tok", timeout=5.0, uris=("/x",), transport=transport
    )

    data = await source.business_impact(start_ts=1, end_ts=2)

    assert data["endpoints"][0]["pv"] == 3
    assert calls[2]["params"]["arguments"]["uris"] == ["/x"]


def _capture_logs(records: list):
    """挂一个 loguru sink，返回移除函数。"""
    from loguru import logger

    sink_id = logger.add(lambda message: records.append(str(message)), level="INFO")
    return lambda: logger.remove(sink_id)


@pytest.mark.asyncio
async def test_success_writes_call_log():
    """成功调用写一条 [rca] mcp call 日志，含工具名、耗时与样本量。"""
    calls = []
    transport = httpx.MockTransport(_handler(calls, _STATS))
    source = McpLogSource("https://m/mcp", "tok", timeout=5.0, transport=transport)
    records: list[str] = []
    stop = _capture_logs(records)
    try:
        await source.latency_stats(uri="/x", start_ts=1, end_ts=2)
    finally:
        stop()
    text = "".join(records)
    assert "mcp call tool=sls_latency_stats status=ok" in text
    assert "elapsed_ms=" in text
    assert "total=2" in text


@pytest.mark.asyncio
async def test_failure_writes_reason_log():
    """工具失败时日志带 reason，且不再抛日志异常。"""
    calls = []
    transport = httpx.MockTransport(_handler(calls, {"detail": "boom"}, is_error=True))
    source = McpLogSource("https://m/mcp", "tok", timeout=5.0, transport=transport)
    records: list[str] = []
    stop = _capture_logs(records)
    try:
        with pytest.raises(SourceFailedError):
            await source.request_detail(uri="/x", start_ts=1, end_ts=2)
    finally:
        stop()
    text = "".join(records)
    assert "status=failed" in text
    assert "reason=tool_error" in text


@pytest.mark.asyncio
async def test_span_opened_and_closed(monkeypatch):
    """有 trace 上下文时建 mcp:<tool> span，并在结束时写入状态。"""
    import src.infra.llm.trace_context as trace_ctx
    import src.rca.sources.mcp as mcp_mod

    class _SpySpan:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.end_kwargs = None

        def end(self, **kwargs):
            self.end_kwargs = kwargs

    class _SpyClient:
        def __init__(self):
            self.spans = []

        def span(self, **kwargs):
            span = _SpySpan(**kwargs)
            self.spans.append(span)
            return span

    spy_client = _SpyClient()

    class _SpyContext:
        client_instance = spy_client

    monkeypatch.setattr(mcp_mod, "langfuse_context", _SpyContext())
    monkeypatch.setattr(mcp_mod.settings, "LANGFUSE_ENABLE", True)

    token = trace_ctx.current_trace_id.set("trace_rca_test")
    try:
        transport = httpx.MockTransport(_handler([], _STATS))
        source = McpLogSource("https://m/mcp", "tok", timeout=5.0, transport=transport)
        await source.latency_stats(uri="/x", start_ts=1, end_ts=2)
    finally:
        trace_ctx.current_trace_id.reset(token)

    assert len(spy_client.spans) == 1
    span = spy_client.spans[0]
    assert span.kwargs["name"] == "mcp:sls_latency_stats"
    assert span.kwargs["trace_id"] == "trace_rca_test"
    assert span.kwargs["input"]["slow_ms"] == 2000
    assert span.end_kwargs["output"]["status"] == "ok"
