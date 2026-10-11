"""seed 判据纯函数的单测（dry-run 与真跑共用同一判据）。"""

from src.cli.seed_langfuse_models import decide


def test_zero_price_skips():
    assert decide("m", 0.0, 1.0, set()) == "skipped"


def test_existing_wins_over_create():
    assert decide("m", 1.0, 1.0, {"m"}) == "exists"


def test_new_creates():
    assert decide("m", 1.0, 1.0, set()) == "created"
