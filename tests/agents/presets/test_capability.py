"""profile → 能力档映射测试。"""

from src.agents.presets.capability import (
    DEFAULT_CAPABILITY,
    LOG_CAPABILITY,
    LOG_PROFILE_MAX_TURNS,
    capability_of,
)


def test_capability_mapping():
    """bot-ops / bot-dev 落日志档，其余预设与空串落基础档。"""
    assert capability_of("bot-ops") == "log"
    assert capability_of("bot-dev") == "log"
    assert capability_of("bot-support") == "default"
    assert capability_of("") == "default"


def test_capability_unknown_profile_falls_back_to_default():
    """未知 profile 不抛错，回落基础档。"""
    assert capability_of("no-such-profile") == DEFAULT_CAPABILITY


def test_capability_constants():
    """档名常量与日志档轮次预算固定。"""
    assert DEFAULT_CAPABILITY == "default"
    assert LOG_CAPABILITY == "log"
    assert LOG_PROFILE_MAX_TURNS == 6
