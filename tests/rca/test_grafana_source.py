from dataclasses import replace

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
                            {"name": "max_ms"},
                            {"name": "slow_cnt"},
                            {"name": "c5xx"},
                        ]
                    },
                    "data": {
                        "values": [["10.0.0.1"], [3], [100.0], [4200.0], [1], [2]]
                    },
                }
            ]
        }
    }
}

_DETAIL_PAYLOAD = {
    "results": {
        "A": {
            "frames": [
                {
                    "schema": {
                        "fields": [
                            {"name": "ts"},
                            {"name": "upstream_addr"},
                            {"name": "rt_ms"},
                            {"name": "urt_ms"},
                            {"name": "status"},
                            {"name": "upstream_status"},
                        ]
                    },
                    "data": {
                        "values": [
                            [1000],
                            ["10.0.0.1"],
                            [20.0],
                            [10.0],
                            [200],
                            [502],
                        ]
                    },
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


@pytest.mark.asyncio
async def test_stats_reads_max_latency_and_5xx_count():
    client = _StubClient(_STATS_PAYLOAD)
    source = GrafanaLogSource(client, _CTX, _cfg())
    data = await source.latency_stats("/x", 1, 2)
    assert data["latency_ms"]["max"] == 4200.0
    assert data["errors"]["5xx"] == 2
    query = client.bodies[0]["queries"][0]["query"]
    assert "max(upstream_response_time) * 1000 as max_ms" in query
    assert "count_if(status >= 500) as c5xx" in query


@pytest.mark.asyncio
async def test_context_threshold_overrides_default_slow_ms():
    """规则阈值 300ms 时，慢请求口径不得退回 2000ms。"""
    client = _StubClient(_STATS_PAYLOAD)
    source = GrafanaLogSource(client, replace(_CTX, threshold=300.0), _cfg())
    data = await source.latency_stats("/x", 1, 2)
    query = client.bodies[0]["queries"][0]["query"]
    assert "count_if(upstream_response_time * 1000 >= 300) as slow_cnt" in query
    assert data["slow"]["threshold"] == 300


@pytest.mark.asyncio
async def test_missing_threshold_keeps_default_slow_ms():
    client = _StubClient(_STATS_PAYLOAD)
    source = GrafanaLogSource(client, replace(_CTX, threshold=None), _cfg())
    await source.latency_stats("/x", 1, 2)
    query = client.bodies[0]["queries"][0]["query"]
    assert "count_if(upstream_response_time * 1000 >= 2000) as slow_cnt" in query


@pytest.mark.asyncio
async def test_request_detail_returns_upstream_status():
    client = _StubClient(_DETAIL_PAYLOAD)
    source = GrafanaLogSource(client, _CTX, _cfg())
    rows = await source.request_detail("/x", 1, 2)
    assert rows[0]["status"] == 200
    assert rows[0]["upstream_status"] == 502
