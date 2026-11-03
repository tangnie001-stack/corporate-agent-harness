import pytest

from src.config.rca import RcaConfig
from src.infra.grafana_client import GrafanaClient
from src.rca.models import AlertContext
from src.rca.sources.base import SourceFailedError
from src.rca.sources.grafana import GrafanaLogSource, parse_frame_column

_CTX = AlertContext(
    alertname="a",
    folder="f",
    uri="/x",
    logstore="ls",
    datasource_uid="uid",
    threshold=1.0,
    for_seconds=1,
)

_STATS_PAYLOAD = {
    "results": {
        "A": {
            "frames": [
                {
                    "schema": {
                        "fields": [
                            {"name": "upstream_addr"},
                            {"name": "cnt"},
                            {"name": "p95"},
                            {"name": "slow_cnt"},
                        ]
                    },
                    "data": {"values": [["10.0.0.1"], [3], [100.0], [1]]},
                }
            ]
        }
    }
}


class _StubClient(GrafanaClient):
    """记录请求体并返回预置 payload 的假客户端。"""

    def __init__(self, payload: dict) -> None:
        super().__init__(base_url="", token="", timeout=1.0)
        self.payload = payload
        self.bodies: list[dict] = []

    async def post_json(self, path: str, body: dict) -> dict:
        self.bodies.append(body)
        return self.payload


def _cfg() -> RcaConfig:
    return RcaConfig(
        enabled=True,
        alert_whitelist=(),
        grafana_base_url="",
        grafana_token="",
        collect_budget_seconds=25.0,
        source_timeout_seconds=8.0,
        rule_timeout_seconds=5.0,
        history_timeout_seconds=5.0,
        concurrency=4,
        severe_latency_ms=3000,
        cache_ttl_seconds=600.0,
        business_uris=("/p",),
    )


def test_parse_frame_column_by_name():
    payload = {
        "results": {
            "A": {
                "status": 500,
                "frames": [
                    {
                        "schema": {"fields": [{"name": "time"}, {"name": "cnt"}]},
                        "data": {"values": [[1000, 2000], [3, 4]]},
                    }
                ],
            }
        }
    }
    assert parse_frame_column(payload, "A", "cnt") == [3, 4]


def test_parse_frame_column_empty_frames():
    payload = {"results": {"A": {"status": 500, "frames": []}}}
    assert parse_frame_column(payload, "A", "cnt") == []


@pytest.mark.asyncio
async def test_result_error_payload_raises_failed():
    client = _StubClient({"results": {"A": {"error": "bad query", "frames": []}}})
    source = GrafanaLogSource(client, _CTX, _cfg())
    with pytest.raises(SourceFailedError) as exc_info:
        await source.latency_stats("/x", 1, 2)
    assert str(exc_info.value) == "grafana_error"


@pytest.mark.asyncio
async def test_top_level_error_payload_raises_failed():
    client = _StubClient({"error": "boom", "results": {}})
    source = GrafanaLogSource(client, _CTX, _cfg())
    with pytest.raises(SourceFailedError) as exc_info:
        await source.business_impact(1, 2)
    assert str(exc_info.value) == "grafana_error"


@pytest.mark.asyncio
async def test_uri_single_quote_escaped():
    client = _StubClient(_STATS_PAYLOAD)
    source = GrafanaLogSource(client, _CTX, _cfg())
    await source.latency_stats("/a'b", 1, 2)
    query = client.bodies[0]["queries"][0]["query"]
    assert "request_uri = '/a''b'" in query
