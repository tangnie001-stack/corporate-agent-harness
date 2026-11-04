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
