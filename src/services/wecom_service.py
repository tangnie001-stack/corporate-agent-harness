"""企微智能机器人通道的编排：按 WECOM_BOT_MODE 装配驱动 + 注入默认 handler。

多机器人（长连接）：每台一个驱动，集中存放于驱动注册表；逐台装配/降级/关停。
回调保留为 legacy 单机器人（存于保留键 `callback`）。
长连接复用同一桥接 handler（跑站点同款 Agent 管线）；回调仍用占位 handler（只打日志并回写死回复）。
"""

import asyncio
from typing import Protocol, runtime_checkable

from loguru import logger

from src.channels.base import ChannelDriver, InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver
from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom.handler import RagChannelHandler
from src.channels.wecom.long_connection import LongConnectionDriver
from src.config import settings
from src.config.const import WECOM_REPLY_PLACEHOLDER
from src.config.wecom_bots import CALLBACK_BOT_KEY, load_wecom_bots
from src.core.logging import encode_value
from src.services import turn_runner
from src.services.app_service import get_app_service

# 接入模式取值
_MODE_CALLBACK = "callback"
_MODE_LONG_CONNECTION = "long_connection"


@runtime_checkable
class _ReadyProbe(Protocol):
    """可查询认证就绪态的驱动（长连接）；callback 驱动不含该属性。"""

    @property
    def is_ready(self) -> bool:
        """该台是否已认证就绪。"""
        ...


# 驱动注册表：键 = bot_key（长连接）或保留键 callback（回调 legacy）
_drivers: dict[str, ChannelDriver] = {}
# 反查表：aibotid(BotID) → bot_key；由 start() 一次性构建（handler 只读）
# 语义为「已配置」级：由全部配置的机器人构建，非「已连接」级（_drivers 只含连接成功者）。
# 不要用它判断驱动是否在线；未连上者不会推消息，故当前无害，但两表语义不同，勿混用。
_bot_key_by_aibotid: dict[str, str] = {}


def _resolve_bot_key(aibotid: str) -> str | None:
    """把入站 aibotid 映射到 bot_key；未知返回 None。

    回调 legacy 模式下唯一驱动即回调驱动，固定返回保留键。
    """
    if settings.WECOM_BOT_MODE == _MODE_CALLBACK:
        return CALLBACK_BOT_KEY
    return _bot_key_by_aibotid.get(aibotid)


async def _default_handler(msg: InboundMessage, sink: ReplySink) -> None:
    """默认 handler：按 bot_key 分发；消息回写死流式回复，事件不回包。"""
    bot_key = _resolve_bot_key(msg.aibotid)
    if bot_key is None:
        logger.warning("[wecom] unknown bot aibotid={}", encode_value(msg.aibotid))
        return

    text_len = 0
    if msg.text:
        text_len = len(msg.text)
    logger.info(
        "[wecom] inbound bot_key={} msgid={} chattype={} msgtype={} event_type={} text_len={}",
        encode_value(bot_key),
        msg.msgid,
        msg.chattype,
        msg.msgtype,
        msg.event_type,
        text_len,
    )
    if settings.WECOM_BOT_LOG_CONTENT and msg.text:
        logger.debug("[wecom] content={}", msg.text)

    if msg.msgtype == "event":
        return
    await sink.reply_stream(WECOM_REPLY_PLACEHOLDER, finish=True)


def _build_bridge_handler() -> RagChannelHandler:
    """构造长连接用的桥接 handler（每次 start 重建：去重/触发者表随进程生命周期）。

    依赖注入而非在通道层 import 本模块：`channels/wecom/handler.py` 只依赖
    `services` 的叶子模块，避免与 `wecom_service → handler` 形成环。

    Returns:
        配置齐备的 RagChannelHandler
    """
    return RagChannelHandler.build_default(
        start_turn=turn_runner.start_turn,
        get_service=get_app_service,
        resolve_bot_key=_resolve_bot_key,
    )


def _validate_credentials() -> None:
    """callback 模式启动期校验：凭证非法即抛，不留请求期降级。"""
    if not settings.WECOM_BOT_TOKEN:
        raise RuntimeError("WECOM_BOT_TOKEN 未配置")
    if len(settings.WECOM_BOT_ENCODING_AES_KEY) != 43:
        raise RuntimeError("WECOM_BOT_ENCODING_AES_KEY 必须为 43 位")


async def start() -> None:
    """启动通道：enabled 时按 mode 装配驱动，否则 no-op。"""
    global _drivers, _bot_key_by_aibotid
    if not settings.WECOM_BOT_ENABLED:
        return
    mode = settings.WECOM_BOT_MODE

    if mode == _MODE_CALLBACK:
        _validate_credentials()
        crypto = WeComCrypto(
            settings.WECOM_BOT_TOKEN,
            settings.WECOM_BOT_ENCODING_AES_KEY,
            settings.WECOM_BOT_RECEIVE_ID,
        )
        _drivers = {CALLBACK_BOT_KEY: CallbackDriver(crypto, _default_handler)}
        _bot_key_by_aibotid = {}
        return

    if mode != _MODE_LONG_CONNECTION:
        raise RuntimeError(f"未知 WECOM_BOT_MODE: {mode}")

    # 解析/校验置于逐台 try/except 之外：配置错误必须 fail-fast，不得被降级吞掉
    bots = load_wecom_bots()
    if not bots:
        raise RuntimeError("WECOM_BOT_MODE=long_connection 但 WECOM_BOTS 为空")

    _bot_key_by_aibotid = {bot.bot_id: bot.key for bot in bots}
    _drivers.clear()
    bridge_handler = _build_bridge_handler()
    built: list[tuple[str, LongConnectionDriver]] = []
    for bot in bots:
        driver = LongConnectionDriver(bot.bot_id, bot.secret, bridge_handler)
        built.append((bot.key, driver))
    results = await asyncio.gather(
        *(driver.start() for _key, driver in built), return_exceptions=True
    )
    ready_count = 0
    for (bot_key, driver), result in zip(built, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "[wecom] bot connect failed bot_key={} err={}",
                encode_value(bot_key),
                result,
            )
            continue
        # 已建立连接的驱动一律登记（含未认证就绪者），否则 stop() 无法关闭它
        _drivers[bot_key] = driver
        # 连接失败的台既不注册也不计数；就绪台数只统计已登记者
        if driver.is_ready:
            ready_count += 1
    logger.info("[wecom] bots connected n={} total={}", ready_count, len(bots))


async def stop() -> None:
    """停止通道：逐台断开，单台失败不阻塞其余，幂等。"""
    global _drivers, _bot_key_by_aibotid
    for bot_key, driver in list(_drivers.items()):
        try:
            await driver.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[wecom] bot stop failed bot_key={} err={}",
                encode_value(bot_key),
                e,
            )
    _drivers = {}
    _bot_key_by_aibotid = {}


def get_driver(bot_key: str) -> ChannelDriver:
    """取指定机器人的驱动；未命中抛 RuntimeError。"""
    driver = _drivers.get(bot_key)
    if driver is None:
        raise RuntimeError(f"wecom driver 未启动或不存在: {bot_key}")
    return driver


def is_ready(bot_key: str) -> bool:
    """该台是否可用。

    长连接按驱动就绪判定（未认证超时／凭证类失败／被顶后为 False）；
    callback 模式无握手，装配成功即视为可用。

    以 `_ReadyProbe` 结构化探测而非绑定具体驱动类：生产里即 `LongConnectionDriver`，
    测试中驱动类可被替身替换，绑定具体类会随替换失效。

    Args:
        bot_key: 机器人别名

    Returns:
        True 表示该台可用；未注册或未就绪为 False
    """
    driver = _drivers.get(bot_key)
    if driver is None:
        return False
    if isinstance(driver, _ReadyProbe):
        return driver.is_ready
    return True


def get_callback_driver() -> CallbackDriver:
    """取回调驱动；非 callback 模式或未启动时抛 RuntimeError。"""
    if settings.WECOM_BOT_MODE != _MODE_CALLBACK:
        raise RuntimeError("当前非 callback 模式")
    driver = _drivers.get(CALLBACK_BOT_KEY)
    if driver is None:
        raise RuntimeError("wecom driver 未启动")
    if not isinstance(driver, CallbackDriver):
        # 契约要求统一抛 RuntimeError（路由按此捕获），故不换成 TypeError
        raise RuntimeError("当前驱动不是 CallbackDriver")  # noqa: TRY004
    return driver


async def send_to_session(bot_key: str, chatid: str, content: str) -> bool:
    """向指定会话主动推送一条消息；失败只记 warning 并返回 False。

    通道层对外只暴露"发到某个会话"这个语义，调用方不感知 SDK 细节。
    **不回落到默认会话**：目标由调用方显式给出，找不到就是失败。

    Args:
        bot_key: 机器人别名
        chatid: 目标会话 id
        content: 消息正文

    Returns:
        True 表示已发出（SDK ack 成功）；False 表示目标不存在或发送失败
    """
    driver = _drivers.get(bot_key)
    if driver is None or not isinstance(driver, LongConnectionDriver):
        logger.warning(
            "[wecom] send skipped reason=no_driver bot_key={}", encode_value(bot_key)
        )
        return False
    try:
        await driver.send_message(chatid, content)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[wecom] send failed bot_key={} chatid={} err={}",
            encode_value(bot_key),
            encode_value(chatid),
            e,
        )
        return False
    return True
