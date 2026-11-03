import asyncio
import time
from dataclasses import replace
from typing import cast

import pytest

from src.config.rca import RcaConfig
from src.rca.collector import collect_evidence
from src.rca.models import EvidenceSpec, EvidenceStatus
from src.rca.sources.base import LogSource, SourceFailedError


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


class WindowSource:
    """记录收到的观测窗口的假源（三个工具都需要窗口）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def latency_stats(self, uri, start_ts, end_ts, slow_ms=2000):
        self.calls.append((start_ts, end_ts))
        return {"total": 1}

    async def request_detail(self, uri, start_ts, end_ts, limit=100):
        self.calls.append((start_ts, end_ts))
        return [{"ts": 1}]

    async def business_impact(self, start_ts, end_ts):
        self.calls.append((start_ts, end_ts))
        return {"endpoints": []}


class NoWindowSource:
    """签名不含窗口的假源：注入窗口会 TypeError。"""

    async def latency_stats(self, uri):
        return {"total": 1}

    async def request_detail(self, uri):
        return [{"ts": 1}]

    async def business_impact(self):
        return {"endpoints": []}


@pytest.mark.asyncio
async def test_window_injected_only_when_signature_accepts():
    """工具签名含 start_ts/end_ts ⇒ 从 fields 注入编排层给的窗口。"""
    src = WindowSource()
    specs = [
        EvidenceSpec(id="stats", tool="latency_stats", params={"uri": "${alert.uri}"}),
        EvidenceSpec(
            id="detail",
            tool="request_detail",
            params={"uri": "${alert.uri}"},
            required=False,
        ),
        EvidenceSpec(id="impact", tool="business_impact", required=False),
    ]
    out = await collect_evidence(
        specs, src, {"uri": "/x", "start_ts": 1000, "end_ts": 1900}, _cfg()
    )
    assert out["stats"].status is EvidenceStatus.OK
    assert out["detail"].status is EvidenceStatus.OK
    assert out["impact"].status is EvidenceStatus.OK
    assert src.calls == [(1000, 1900)] * 3


@pytest.mark.asyncio
async def test_window_not_injected_when_signature_lacks_it():
    """工具签名不含窗口 ⇒ 不得注入多余关键字（否则 TypeError ⇒ 全部 failed）。"""
    specs = [
        EvidenceSpec(id="stats", tool="latency_stats", params={"uri": "${alert.uri}"})
    ]
    out = await collect_evidence(
        specs,
        cast(LogSource, NoWindowSource()),
        {"uri": "/x", "start_ts": 1, "end_ts": 2},
        _cfg(),
    )
    assert out["stats"].status is EvidenceStatus.OK


@pytest.mark.asyncio
async def test_empty_data_marks_empty():
    specs = [EvidenceSpec(id="impact", tool="business_impact", required=False)]
    out = await collect_evidence(specs, FastSource(), {}, _cfg())
    assert out["impact"].status is EvidenceStatus.EMPTY


class SplitSlowSource:
    """两波各慢 0.45s：用于验证总预算只扣一次，不会按波翻倍。"""

    async def latency_stats(self, uri, start_ts, end_ts, slow_ms=2000):
        await asyncio.sleep(0.45)
        return {"total": 1}

    async def request_detail(self, uri, start_ts, end_ts, limit=100):
        await asyncio.sleep(0.45)
        return [{"ts": 1}]

    async def business_impact(self, start_ts, end_ts):
        return {"endpoints": []}


@pytest.mark.asyncio
async def test_budget_spans_both_waves():
    """总预算跨两波共用：第二波只能花剩余时间，不能各拿一份预算。"""
    cfg = replace(_cfg(), collect_budget_seconds=0.6)
    specs = [
        EvidenceSpec(id="stats", tool="latency_stats", params={"uri": "${alert.uri}"}),
        EvidenceSpec(
            id="detail",
            tool="request_detail",
            params={"uri": "${alert.uri}"},
            required=False,
            when="stats.total >= 1",
        ),
    ]
    started = time.monotonic()
    out = await collect_evidence(specs, SplitSlowSource(), {"uri": "/x"}, cfg)
    elapsed = time.monotonic() - started
    assert out["stats"].status is EvidenceStatus.OK
    assert out["detail"].status is EvidenceStatus.FAILED
    assert out["detail"].reason == "budget_exceeded"
    assert elapsed < 0.9, "两波合计不得超过单轮总预算"
