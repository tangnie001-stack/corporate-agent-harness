import asyncio
import time
from dataclasses import replace

import pytest

from src.config.rca import RcaConfig
from src.rca.collector import collect_evidence
from src.rca.models import EvidenceSpec, EvidenceStatus
from src.rca.sources.base import SourceFailedError


def _cfg() -> RcaConfig:
    return RcaConfig(
        enabled=True,
        alert_whitelist=(),
        grafana_base_url="",
        grafana_token="",
        collect_budget_seconds=2.0,
        source_timeout_seconds=1.0,
        rule_timeout_seconds=1.0,
        history_timeout_seconds=1.0,
        concurrency=4,
        severe_latency_ms=3000,
        cache_ttl_seconds=600.0,
        business_uris=(),
    )


class SlowSource:
    async def latency_stats(self, uri, start_ts, end_ts, slow_ms=2000):
        await asyncio.sleep(5)
        return {}

    async def request_detail(self, uri, start_ts, end_ts, limit=100):
        return []

    async def business_impact(self, start_ts, end_ts):
        return {}


class FastSource:
    async def latency_stats(self, uri, start_ts, end_ts, slow_ms=2000):
        return {"total": 2}

    async def request_detail(self, uri, start_ts, end_ts, limit=100):
        return [{"ts": 1}]

    async def business_impact(self, start_ts, end_ts):
        return {}


class FailingSource:
    """stats 立即失败、其余慢：用于验证必需源短路。"""

    async def latency_stats(self, uri, start_ts, end_ts, slow_ms=2000):
        raise SourceFailedError("grafana_error")

    async def request_detail(self, uri, start_ts, end_ts, limit=100):
        await asyncio.sleep(5)
        return []

    async def business_impact(self, start_ts, end_ts):
        await asyncio.sleep(5)
        return {}


@pytest.mark.asyncio
async def test_timeout_marks_failed():
    specs = [
        EvidenceSpec(
            id="stats",
            tool="latency_stats",
            params={"uri": "${alert.uri}"},
            timeout=0.2,
        )
    ]
    out = await collect_evidence(specs, SlowSource(), {"uri": "/x"}, _cfg())
    assert out["stats"].status is EvidenceStatus.FAILED
    assert out["stats"].reason == "timeout"


@pytest.mark.asyncio
async def test_when_skips_second_wave():
    specs = [
        EvidenceSpec(id="stats", tool="latency_stats", params={"uri": "${alert.uri}"}),
        EvidenceSpec(
            id="detail",
            tool="request_detail",
            params={"uri": "${alert.uri}"},
            required=False,
            when="stats.total >= 100",
        ),
    ]
    out = await collect_evidence(specs, FastSource(), {"uri": "/x"}, _cfg())
    assert out["stats"].status is EvidenceStatus.OK
    assert "detail" not in out


@pytest.mark.asyncio
async def test_required_failed_returns_immediately():
    specs = [
        EvidenceSpec(id="stats", tool="latency_stats", params={"uri": "${alert.uri}"}),
        EvidenceSpec(id="impact", tool="business_impact", required=False),
    ]
    started = time.monotonic()
    out = await collect_evidence(specs, FailingSource(), {"uri": "/x"}, _cfg())
    elapsed = time.monotonic() - started
    assert out["stats"].status is EvidenceStatus.FAILED
    assert out["stats"].reason == "grafana_error"
    assert "impact" not in out
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_budget_exhausted_marks_failed():
    cfg = replace(_cfg(), collect_budget_seconds=0.2)
    specs = [
        EvidenceSpec(id="stats", tool="latency_stats", params={"uri": "${alert.uri}"})
    ]
    out = await collect_evidence(specs, SlowSource(), {"uri": "/x"}, cfg)
    assert out["stats"].status is EvidenceStatus.FAILED
    assert out["stats"].reason == "budget_exceeded"


@pytest.mark.asyncio
async def test_empty_data_marks_empty():
    specs = [EvidenceSpec(id="impact", tool="business_impact", required=False)]
    out = await collect_evidence(specs, FastSource(), {}, _cfg())
    assert out["impact"].status is EvidenceStatus.EMPTY
