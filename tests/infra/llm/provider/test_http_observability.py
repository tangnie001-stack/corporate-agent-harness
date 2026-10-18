import asyncio
import json

import httpx
import pytest
from loguru import logger

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


@pytest.mark.asyncio
async def test_response_hook_warns_on_non_2xx_and_on_unreadable_body():
    ev = HookEvidence()
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m), level="WARNING")
    try:
        for resp in (
            httpx.Response(503),
            httpx.Response(200, content=b"not-json"),
        ):
            client = _client_with(ev, lambda req, r=resp: r)
            async with client:
                await client.post("https://example.invalid/v1/chat/completions")
    finally:
        logger.remove(sink_id)

    joined = "".join(messages)
    assert "bridge judge http non-2xx" in joined
    assert "bridge judge http body unreadable" in joined


@pytest.mark.asyncio
async def test_response_hook_records_retry_after():
    ev = HookEvidence()
    client = _client_with(
        ev,
        lambda req: httpx.Response(429, headers={"Retry-After": "2"}),
    )
    async with client:
        await client.post("https://example.invalid/v1/chat/completions")

    assert ev.events[-1]["retry_after"] == "2"


def _begin(ev, measure_id="eval_t1:faithfulness:1"):
    ev.set_run("eval_run_20261019_000000_abcd1234")
    ev.mark("eval_t1")
    ev.begin_measure(
        measure_id=measure_id,
        metric="faithfulness",
        attempt=1,
        trace_id="eval_t1",
        q_index=0,
    )


@pytest.mark.asyncio
async def test_success_records_usage_and_one_http_event():
    ev = HookEvidence()
    _begin(ev)
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        client = _client_with(
            ev,
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                },
            ),
        )
        async with client:
            await client.post("https://example.invalid/v1/chat/completions")
        measure = ev.end_measure(reason="ok", error="")
    finally:
        logger.remove(sink_id)

    assert measure["request_count"] == 1
    assert measure["response_count"] == 1
    assert measure["statuses"] == [200]
    assert measure["tokens_in"] == 10
    assert measure["tokens_out"] == 3
    assert measure["calls"][0]["judge_call_id"] == "eval_t1:faithfulness:1:1"
    assert measure["calls"][0]["json_ok"] is True
    assert measure["calls"][0]["finish_reasons"] == ["stop"]

    http_line = next(m for m in messages if "judge http" in m)
    assert "judge_call_id=eval_t1:faithfulness:1:1" in http_line
    assert "status=200" in http_line
    assert "tokens_in=10" in http_line


@pytest.mark.asyncio
async def test_out_of_order_responses_are_sorted_by_call_index():
    ev = HookEvidence()
    _begin(ev)

    async def handler(request):
        kind = json.loads(request.content)["kind"]
        if kind == "slow":
            await asyncio.sleep(0.02)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop"}]})
        return httpx.Response(429, json={"error": "rate"})

    client = _client_with(ev, handler)
    async with client:
        slow = asyncio.create_task(
            client.post(
                "https://example.invalid/v1/chat/completions", json={"kind": "slow"}
            )
        )
        await asyncio.sleep(0.005)
        fast = asyncio.create_task(
            client.post(
                "https://example.invalid/v1/chat/completions", json={"kind": "fast"}
            )
        )
        await asyncio.gather(slow, fast)

    measure = ev.end_measure(reason=None, error="")
    assert [c["call_index"] for c in measure["calls"]] == [1, 2]
    assert measure["statuses"] == [200, 429]


@pytest.mark.asyncio
async def test_non_2xx_keeps_real_status():
    ev = HookEvidence()
    _begin(ev)
    client = _client_with(ev, lambda req: httpx.Response(429, json={"error": "rate"}))
    async with client:
        await client.post("https://example.invalid/v1/chat/completions")

    measure = ev.end_measure(reason=None, error="")
    assert measure["statuses"] == [429]
    assert measure["calls"][0]["status"] == 429


@pytest.mark.asyncio
async def test_transport_failure_gets_a_failure_record():
    ev = HookEvidence()
    _begin(ev)

    def boom(req):
        raise httpx.ConnectError("no route", request=req)

    client = _client_with(ev, boom)
    async with client:
        with pytest.raises(httpx.ConnectError):
            await client.post("https://example.invalid/v1/chat/completions")

    measure = ev.end_measure(reason=None, error="ConnectError: no route")
    assert measure["request_count"] == 1
    assert measure["response_count"] == 0
    assert measure["statuses"] == [None]
    assert measure["calls"][0]["status"] is None
    assert measure["calls"][0]["error"] == "no_response"


@pytest.mark.asyncio
async def test_no_active_measure_does_not_emit_http_event():
    ev = HookEvidence()
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        client = _client_with(ev, lambda req: httpx.Response(200, json={"choices": []}))
        async with client:
            await client.post("https://example.invalid/v1/chat/completions")
    finally:
        logger.remove(sink_id)

    assert ev.measures == []
    assert any(e.get("phase") == "response" for e in ev.events)
    assert not any("judge http" in m for m in messages)


@pytest.mark.asyncio
async def test_observability_failure_does_not_fail_request(monkeypatch):
    ev = HookEvidence()
    _begin(ev)

    def boom(*args, **kwargs):
        raise RuntimeError("log sink down")

    monkeypatch.setattr(
        "src.infra.llm.provider.http_observability.core_logging.log_event", boom
    )
    client = _client_with(ev, lambda req: httpx.Response(200, json={"choices": []}))
    async with client:
        resp = await client.post("https://example.invalid/v1/chat/completions")

    assert resp.status_code == 200
