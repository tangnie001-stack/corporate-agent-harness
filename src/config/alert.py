"""告警接入配置：从环境变量解析并校验（change: alibaba-alert-notify）。

独立于 settings.py（后者已接近 400 行红线），解析与校验合一，由 alert_service
在首次使用时加载（模块级缓存）。

错误信息只定位到键名，**绝不回显 ALERT_WEBHOOK_TOKEN（密钥）或 ALERT_TARGET
原值**——与 src/config/wecom_bots.py 同一纪律。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

_DEFAULT_DEDUP_WINDOW_SECONDS: float = 300.0
_DEFAULT_DEDUP_CAPACITY: int = 4096
_DEFAULT_TURN_TIMEOUT_SECONDS: float = 240.0
_DEFAULT_FALLBACK_MAX_CHARS: int = 1500


@dataclass(frozen=True)
class AlertTarget:
    """告警结论的推送目标（单目标；多群把 token 解释为路由键即可扩展）。"""

    bot_key: str  # 机器人别名（复用现有 WECOM_BOTS 里的 key）
    chatid: str  # 群会话 id（群维度，与机器人无关）


@dataclass(frozen=True)
class AlertConfig:
    """告警链路的全部可配置项。"""

    token: str  # 请求头鉴权 token；空串 = 未配置（端点恒 404，不阻断启动）
    target: AlertTarget | None  # 目标群；None = 未配置（不发送、记 warning）
    dedup_window_seconds: float  # 入口去重时间窗
    dedup_capacity: int  # 去重结构容量上限
    turn_timeout_seconds: float  # 整轮分析超时
    fallback_max_chars: int  # 兜底消息长度上限


def _positive_float(name: str, default: float) -> float:
    """读正数浮点环境变量；非法即 fail-fast。"""
    raw = os.getenv(name, "")
    if raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} 不是合法数字") from None
    if value <= 0:
        raise ValueError(f"{name} 必须为正数")
    return value


def _positive_int(name: str, default: int) -> int:
    """读正整数环境变量；非法即 fail-fast。"""
    raw = os.getenv(name, "")
    if raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} 不是合法整数") from None
    if value <= 0:
        raise ValueError(f"{name} 必须为正整数")
    return value


def _load_target() -> AlertTarget | None:
    """解析 ALERT_TARGET（单个 JSON 对象）；未配置返回 None。"""
    raw = os.getenv("ALERT_TARGET", "")
    if raw.strip() == "":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("ALERT_TARGET 不是合法 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("ALERT_TARGET 必须是 JSON 对象")  # noqa: TRY004
    bot_key = data.get("bot_key")
    chatid = data.get("chatid")
    if not isinstance(bot_key, str) or bot_key == "":
        raise ValueError("ALERT_TARGET.bot_key 为空")
    if not isinstance(chatid, str) or chatid == "":
        raise ValueError("ALERT_TARGET.chatid 为空")
    return AlertTarget(bot_key=bot_key, chatid=chatid)


def load_alert_config() -> AlertConfig:
    """解析并校验告警配置；任何非法立即抛 ValueError（fail-fast）。

    未配置 token 是**合法**状态（本地/开发环境）：token 为空、目标可为 None。
    """
    token = os.getenv("ALERT_WEBHOOK_TOKEN", "")
    return AlertConfig(
        token=token,
        target=_load_target(),
        dedup_window_seconds=_positive_float(
            "ALERT_DEDUP_WINDOW_SECONDS", _DEFAULT_DEDUP_WINDOW_SECONDS
        ),
        dedup_capacity=_positive_int("ALERT_DEDUP_CAPACITY", _DEFAULT_DEDUP_CAPACITY),
        turn_timeout_seconds=_positive_float(
            "ALERT_TURN_TIMEOUT_SECONDS", _DEFAULT_TURN_TIMEOUT_SECONDS
        ),
        fallback_max_chars=_positive_int(
            "ALERT_FALLBACK_MAX_CHARS", _DEFAULT_FALLBACK_MAX_CHARS
        ),
    )
