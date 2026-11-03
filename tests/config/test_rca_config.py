"""RCA 配置解析的守卫测试。"""

import pytest

from src.config.rca import load_rca_config, reset_for_test


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个用例前后各清一次配置缓存，避免 _CACHE 跨用例泄漏。"""
    reset_for_test()
    yield
    reset_for_test()


def test_defaults_disabled(monkeypatch):
    """未配置开关与预算时，走默认关闭 + 25 秒预算。"""
    monkeypatch.delenv("RCA_ENABLED", raising=False)
    monkeypatch.delenv("RCA_COLLECT_BUDGET_SECONDS", raising=False)
    cfg = load_rca_config()
    assert cfg.enabled is False
    assert cfg.collect_budget_seconds == 25.0


def test_whitelist_parsed(monkeypatch):
    """白名单按半角逗号切分，条目去空白。"""
    monkeypatch.setenv("RCA_ENABLED", "true")
    monkeypatch.setenv(
        "RCA_ALERT_WHITELIST",
        "支付单createOrder响应时间上升, 用户登录userlogin响应时间上升",
    )
    cfg = load_rca_config()
    assert cfg.enabled is True
    assert "支付单createOrder响应时间上升" in cfg.alert_whitelist


def test_whitelist_accepts_fullwidth_comma(monkeypatch):
    """白名单兼容中文输入法产出的全角逗号。"""
    monkeypatch.setenv("RCA_ALERT_WHITELIST", "告警甲，告警乙")
    cfg = load_rca_config()
    assert cfg.alert_whitelist == ("告警甲", "告警乙")


def test_float_rejects_nan(monkeypatch):
    """nan 不得通过正数校验。"""
    monkeypatch.setenv("RCA_CACHE_TTL_SECONDS", "nan")
    with pytest.raises(ValueError, match="RCA_CACHE_TTL_SECONDS must be positive"):
        load_rca_config()
