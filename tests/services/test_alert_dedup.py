"""入口去重单测（逐条指纹 + 时间窗）。"""

from src.services.alert_service import AlertDedup


def test_first_time_not_duplicate() -> None:
    """首次见到的指纹不是重复。"""
    d = AlertDedup(capacity=10, ttl_seconds=300, monotonic=lambda: 0.0)
    assert d.is_duplicate(["a", "b"]) is False


def test_all_seen_is_duplicate() -> None:
    """全部指纹都在窗口内 ⇒ 重复。"""
    d = AlertDedup(capacity=10, ttl_seconds=300, monotonic=lambda: 0.0)
    d.is_duplicate(["a", "b"])
    assert d.is_duplicate(["a", "b"]) is True


def test_partial_new_is_not_duplicate() -> None:
    """存在未命中的指纹 ⇒ 不判重（宁可多跑，不漏报）。"""
    d = AlertDedup(capacity=10, ttl_seconds=300, monotonic=lambda: 0.0)
    d.is_duplicate(["a"])
    assert d.is_duplicate(["a", "b"]) is False


def test_window_expiry_allows_again() -> None:
    """窗口过期后可再次处理。"""
    now = {"t": 0.0}
    d = AlertDedup(capacity=10, ttl_seconds=300, monotonic=lambda: now["t"])
    d.is_duplicate(["a"])
    now["t"] = 301.0
    assert d.is_duplicate(["a"]) is False


def test_capacity_bounds_memory() -> None:
    """容量上限：超限淘汰最旧（有界，不无界增长）。"""
    d = AlertDedup(capacity=2, ttl_seconds=300, monotonic=lambda: 0.0)
    d.is_duplicate(["a"])
    d.is_duplicate(["b"])
    d.is_duplicate(["c"])
    assert len(d) <= 2
