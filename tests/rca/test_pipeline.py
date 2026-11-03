"""判定编排：历史证据的接入与降级。"""

import pytest

from src.config.rca import RcaConfig
from src.infra.grafana_client import GrafanaClient
from src.rca.models import EvidenceStatus
from src.rca.pipeline import collect_history


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
        business_uris=(),
    )


class _HistoryClient(GrafanaClient):
    """只实现历史接口的假客户端；payload 或异常二者其一。"""

    def __init__(
        self, payload: dict | list | None = None, error: Exception | None = None
    ) -> None:
        super().__init__(base_url="", token="", timeout=1.0)
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict | list:
        self.calls.append((path, dict(params or {})))
        if self.error is not None:
            raise self.error
        if self.payload is None:
            return []
        return self.payload


_ALERT = "支付单createOrder响应时间上升"


@pytest.mark.asyncio
async def test_history_ok_with_minutes():
    payload = [
        {"time": 1000, "text": f"{_ALERT} {{alertname={_ALERT}}}"},
        {"time": 61000, "text": f"{_ALERT} {{alertname={_ALERT}}}"},
    ]
    client = _HistoryClient(payload=payload)
    result = await collect_history(client, _ALERT, 1_700_000_000, _cfg())
    assert result.id == "history"
    assert result.status is EvidenceStatus.OK
    assert result.data == {"minutes": 2}
    path, params = client.calls[0]
    assert path == "/api/annotations"
    assert params["to"] == str(1_700_000_000_000)
    assert params["from"] == str((1_700_000_000 - 7 * 86400) * 1000)


@pytest.mark.asyncio
async def test_history_failure_is_empty_not_raised():
    client = _HistoryClient(error=RuntimeError("boom"))
    result = await collect_history(client, _ALERT, 1_700_000_000, _cfg())
    assert result.status is EvidenceStatus.EMPTY
    assert result.data is None
    assert result.reason.startswith("history_failed:")
