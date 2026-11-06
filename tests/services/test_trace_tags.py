"""trace 标签收敛：三个低基数维度（知识库绑定 / 渠道 / profile）。"""

from src.config.const import Channel
from src.services.agent_service import _trace_tags


def test_tags_are_three_low_cardinality_dimensions():
    """三类标签：kb、渠道、profile 短名，站点轮次也带渠道标签。"""
    assert _trace_tags("kb", Channel.WEB, "") == ["kb", "web", "pf:chat"]
    assert _trace_tags("no_kb", Channel.WECOM, "bot-ops") == [
        "no_kb",
        "wecom",
        "pf:ops",
    ]
    assert _trace_tags("no_kb", Channel.ALERT, "") == ["no_kb", "alert", "pf:alert"]


def test_profile_without_bot_prefix_keeps_name():
    """非 bot- 前缀的 profile 原样进标签（如 finance）。"""
    assert _trace_tags("kb", Channel.WECOM, "finance") == ["kb", "wecom", "pf:finance"]


def test_alert_channel_wins_over_profile():
    """告警轮次一律 pf:alert，不携带会话绑定的 profile。"""
    assert _trace_tags("no_kb", Channel.ALERT, "bot-ops") == [
        "no_kb",
        "alert",
        "pf:alert",
    ]
