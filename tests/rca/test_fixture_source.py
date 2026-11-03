import pytest

from src.rca.sources.base import SourceEmptyError
from src.rca.sources.fixture import FixtureLogSource


@pytest.mark.asyncio
async def test_returns_configured_stats():
    src = FixtureLogSource(
        {"latency_stats": {"total": 2, "latency_ms": {"p95": 1057.0}, "by_node": []}}
    )
    out = await src.latency_stats(uri="/x", start_ts=1, end_ts=2)
    assert out["total"] == 2


@pytest.mark.asyncio
async def test_missing_key_raises():
    src = FixtureLogSource({})
    with pytest.raises(SourceEmptyError):
        await src.latency_stats(uri="/x", start_ts=1, end_ts=2)
