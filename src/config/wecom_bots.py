"""企业微信多机器人配置：从 WECOM_BOTS（JSON 数组）解析并校验每台机器人。

独立于 settings.py（后者已接近 400 行红线）。解析与校验合并在
load_wecom_bots()，由 wecom_service.start() 调用（受 WECOM_BOT_ENABLED 门控）；
profile 的存在性由 src.main 的启动期校验（_validate_wecom_profiles）负责。

错误信息只定位到下标或 key，绝不回显 WECOM_BOTS 原值或任何 secret——
三台凭证同处一个 env 变量，回显原值会批量泄露。
"""

import json
import os
import re
from dataclasses import dataclass, field

from src.config.const import CAPABILITY_NAME_PATTERN

# 保留 key：回调 legacy 驱动占用，长连接侧不得使用
CALLBACK_BOT_KEY: str = "callback"

# key 允许的字符集：保证日志可裸写、可作 dict key
_KEY_PATTERN = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class WeComBotConfig:
    """单台智能机器人的长连接配置。"""

    key: str  # 业务线标识；唯一、匹配 [a-z0-9_-]+、不得为保留字 callback
    bot_id: str  # 智能机器人 BotID（= 入站报文的 aibotid）
    secret: str = field(
        repr=False
    )  # 长连接专用 Secret；repr=False 防止插入日志/异常回溯时裸泄
    name: str = ""  # 人类可读名称（如「阿里云运维机器人」）；仅用于日志辨识，不参与路由
    profile: str = ""  # Agent 预设名；空=通用人设；存在性由启动期校验


def load_wecom_bots() -> list[WeComBotConfig]:
    """解析并校验 WECOM_BOTS；任何非法立即抛 ValueError（fail-fast）。

    空值返回空列表（"长连接模式下注册表不得为空"由调用方按 mode 判定）。
    """
    raw = os.getenv("WECOM_BOTS", "")
    if raw.strip() == "":
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 不回显 raw：它含三台 secret
        raise ValueError("WECOM_BOTS 不是合法 JSON") from None
    if not isinstance(data, list):
        raise ValueError("WECOM_BOTS 必须是 JSON 数组")  # noqa: TRY004  # 校验外部配置值，统一抛 ValueError

    bots: list[WeComBotConfig] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"WECOM_BOTS[{index}] 必须是对象")  # noqa: TRY004  # 校验外部配置值，统一抛 ValueError
        key = item.get("key")
        bot_id = item.get("bot_id")
        secret = item.get("secret")
        name = item.get("name", "")
        profile = item.get("profile", "")
        if profile is None:
            profile = ""
        if not isinstance(key, str) or key == "":
            raise ValueError(f"WECOM_BOTS[{index}].key 为空")
        if not isinstance(bot_id, str) or bot_id == "":
            raise ValueError(f"WECOM_BOTS[{index}].bot_id 为空")
        if not isinstance(secret, str) or secret == "":
            raise ValueError(f"WECOM_BOTS[{index}].secret 为空")
        if not isinstance(name, str):
            raise ValueError(f"WECOM_BOTS[{index}].name 必须是字符串")  # noqa: TRY004  # 校验外部配置值，统一抛 ValueError
        if not isinstance(profile, str):
            raise ValueError(f"WECOM_BOTS[{index}].profile 必须是字符串")  # noqa: TRY004  # 校验外部配置值，统一抛 ValueError
        if profile != "" and CAPABILITY_NAME_PATTERN.fullmatch(profile) is None:
            raise ValueError(f"WECOM_BOTS[{index}].profile 非法（仅允许 ASCII slug）")
        if _KEY_PATTERN.fullmatch(key) is None:
            raise ValueError(f"WECOM_BOTS[{index}].key 只允许 [a-z0-9_-]")
        if key == CALLBACK_BOT_KEY:
            raise ValueError(f"WECOM_BOTS[{index}].key 为保留字 {CALLBACK_BOT_KEY}")
        if key in seen_keys:
            raise ValueError(f"WECOM_BOTS[{index}].key 重复")
        if bot_id in seen_ids:
            raise ValueError(f"WECOM_BOTS[{index}].bot_id 重复")
        seen_keys.add(key)
        seen_ids.add(bot_id)
        bots.append(
            WeComBotConfig(
                key=key, bot_id=bot_id, secret=secret, name=name, profile=profile
            )
        )
    return bots


def validate_bot_profiles(bots: list[WeComBotConfig], known: set[str]) -> None:
    """校验每台机器人的 profile 指向已注册预设。

    Args:
        bots: 已解析的机器人配置
        known: 预设目录中可加载的预设名集合

    Raises:
        ValueError: 某台机器人的 profile 不在 known 中；错误只含 key，不含 secret
    """
    for bot in bots:
        if bot.profile == "":
            continue
        if bot.profile not in known:
            raise ValueError(
                f"WECOM_BOTS key={bot.key} 的 profile 未注册: {bot.profile}"
            )
