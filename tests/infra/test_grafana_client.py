import httpx
import pytest

from src.infra.grafana_client import GrafanaClient, GrafanaError


@pytest.mark.asyncio
async def test_bearer_header_sent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    client = GrafanaClient(
        "https://g:80", "glsa_x", timeout=5.0, transport=httpx.MockTransport(handler)
    )
    assert await client.get_json("/api/health") == {"ok": True}
    assert captured["auth"] == "Bearer glsa_x"


@pytest.mark.asyncio
async def test_401_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    client = GrafanaClient(
        "https://g:80", "bad", timeout=5.0, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GrafanaError) as exc:
        await client.get_json("/api/health")
    assert exc.value.status == 401
