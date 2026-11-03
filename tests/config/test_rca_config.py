from src.config.rca import load_rca_config, reset_for_test


def test_defaults_disabled(monkeypatch):
    reset_for_test()
    monkeypatch.delenv("RCA_ENABLED", raising=False)
    cfg = load_rca_config()
    assert cfg.enabled is False
    assert cfg.collect_budget_seconds == 25.0


def test_whitelist_parsed(monkeypatch):
    reset_for_test()
    monkeypatch.setenv("RCA_ENABLED", "true")
    monkeypatch.setenv(
        "RCA_ALERT_WHITELIST",
        "支付单createOrder响应时间上升, 用户登录userlogin响应时间上升",
    )
    cfg = load_rca_config()
    assert cfg.enabled is True
    assert "支付单createOrder响应时间上升" in cfg.alert_whitelist
