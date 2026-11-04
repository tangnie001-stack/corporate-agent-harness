"""报警轮判定闸门：开关与白名单。"""

from src.config.rca import RcaConfig
from src.rca.pipeline import should_triage


def _cfg(enabled: bool, whitelist: tuple[str, ...]) -> RcaConfig:
    return RcaConfig(
        enabled=enabled,
        alert_whitelist=whitelist,
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


def test_disabled_returns_false():
    assert should_triage("任意告警", _cfg(False, ("任意告警",))) is False


def test_not_in_whitelist_returns_false():
    assert (
        should_triage("其他告警", _cfg(True, ("支付单createOrder响应时间上升",)))
        is False
    )


def test_whitelisted_returns_true():
    assert (
        should_triage(
            "支付单createOrder响应时间上升",
            _cfg(True, ("支付单createOrder响应时间上升",)),
        )
        is True
    )


def test_star_matches_all():
    """白名单里放 * 表示对全部告警开放。"""
    assert should_triage("任意告警名", _cfg(True, ("*",))) is True
