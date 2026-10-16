import httpx
import pytest

from src.infra.llm.provider.http_observability import HookEvidence, make_logging_client

TIMEOUT = httpx.Timeout(connect=1, read=1, write=1, pool=1)


def _client_with(evidence: HookEvidence, handler) -> httpx.AsyncClient:
    """把被测 hooks 装到一个带 MockTransport 的 client 上（避免真出网）。"""
    return make_logging_client(
        timeout=TIMEOUT, evidence=evidence, transport=httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_response_hook_reads_body_and_records_status():
    ev = HookEvidence()
    ev.mark("eval_deadbeef")
    client = _client_with(
        ev,
        lambda req: httpx.Response(200, json={"choices": [{"finish_reason": "stop"}]}),
    )
    async with client:
        await client.post("https://example.invalid/v1/chat/completions")

    assert ev.status_codes == [200]
    assert ev.non_2xx is False
    assert ev.abnormal is False
    assert ev.events[-1]["json_ok"] is True
    assert ev.events[-1]["finish_reason"] == ["stop"]
    assert ev.events[-1]["trace_id"] == "eval_deadbeef"


@pytest.mark.asyncio
async def test_non_2xx_and_malformed_body_are_abnormal():
    ev = HookEvidence()
    for resp in (
        httpx.Response(429, json={"error": {"code": "Throttling"}}),
        httpx.Response(200, content=b"not-json"),
    ):
        client = _client_with(ev, lambda req, r=resp: r)
        async with client:
            await client.post("https://example.invalid/v1/chat/completions")

    assert ev.status_codes == [429, 200]
    assert ev.non_2xx is True
    assert ev.abnormal is True
    assert ev.events[-1]["json_ok"] is False
