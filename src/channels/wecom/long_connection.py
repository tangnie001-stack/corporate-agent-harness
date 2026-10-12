"""企微智能机器人长连接驱动：包住官方 aibot SDK，实现 ChannelDriver。

由本服务主动连企微（wss://openws.work.weixin.qq.com），无需公网回调地址、无加解密。
仅使用 SDK 的公开接口，不调用 run()（它自建事件循环，与 FastAPI 冲突）。
"""

import asyncio
from typing import Any

from aibot import WSClient, WSClientOptions, generate_req_id
from loguru import logger

from src.channels.base import MessageHandler
from src.channels.wecom.parse import parse_inbound
from src.core.logging import encode_value

# SDK 事件名：消息与事件共用同一处理器（入站 body 结构与回调一致）
_MESSAGE_EVENTS: tuple[str, ...] = (
    "message.text",
    "message.image",
    "message.mixed",
    "message.voice",
    "message.file",
)
_EVENT_EVENTS: tuple[str, ...] = (
    "event.enter_chat",
    "event.template_card_event",
    "event.feedback_event",
)

# 驱动级事件（不进业务 handler）：被更新的连接顶替时由服务端推送。
# 实测（见 docs/agents/wecom-sdk-facts.md 的 E4）：服务端先推该事件（此刻本连接 WS 无异常），
# 随后才关闭本连接；该关闭会触发 SDK 自动重连，而重连成功又顶掉对方 ⇒ 形成互踢循环。
# 故收到即主动断开（SDK 手动断开不触发重连）并交出 client 引用，不抢回。
_DISPLACED_EVENT: str = "event.disconnected_event"

# 认证等待上界（秒）：SDK 的 connect() 不等认证（实测差 ~0.17s），故由驱动等
# `authenticated` 事件；超时只降级、不断开（交 SDK 自愈），见 design D11。
_AUTH_TIMEOUT_SECONDS: float = 5.0

# 凭证类失败的判据：SDK 对 SUBSCRIBE 响应 errcode≠0 抛出的错误以本串开头
# （实测错误凭证为 errcode=853000 / invalid bot_id or secret）。**普通 on_error 不构成凭证类**。
_AUTH_FAILED_PREFIX: str = "Authentication failed"


class _WsSink:
    """ReplySink 实现：把回复转成 SDK 的流式回复调用。"""

    def __init__(self, client: WSClient, frame: dict[str, Any], stream_id: str):
        """初始化。

        Args:
            client: 官方 SDK 客户端
            frame: 触发本次回复的入站帧（SDK 回复需透传）
            stream_id: 本条消息的流式 id（生成一次，多次回复复用）
        """
        self._client = client
        self._frame = frame
        self._stream_id = stream_id

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        """发送流式回复。

        Args:
            content: 本帧内容（累积全文）
            finish: 是否结束流式消息
            feedback: 反馈信息（仅首帧设置；None 表示不带）
        """
        await self._client.reply_stream(
            self._frame, self._stream_id, content, finish, feedback=feedback
        )


class LongConnectionDriver:
    """长连接驱动。"""

    name = "wecom_long_connection"

    def __init__(self, bot_id: str, secret: str, handler: MessageHandler):
        """初始化。

        Args:
            bot_id: 智能机器人 BotID
            secret: 长连接专用 Secret
            handler: 业务处理（吃入站事件，用 sink 回复）
        """
        self._bot_id = bot_id
        self._secret = secret
        self._handler = handler
        self._client: WSClient | None = None
        self._displaced: bool = False
        self._ready: bool = False
        self._fatal: bool = False

    @property
    def is_displaced(self) -> bool:
        """该台是否已被更新的连接顶替。

        被顶后驱动已主动断开且不再抢回（防互踢）；就绪口径与启动锚点对它的反映
        见 change `wecom-agent-bridge` 的 D11 与 tasks 5.1。
        """
        return self._displaced

    @property
    def is_ready(self) -> bool:
        """该台是否已认证就绪。

        未认证（等待超时）／凭证类失败／被更新的连接顶替后均为 False。
        """
        if self._displaced or self._fatal:
            return False
        return self._ready

    async def start(self) -> None:
        """建立长连接、等认证（上界 `_AUTH_TIMEOUT_SECONDS`）并注册事件处理器。"""
        # 已启动则直接返回：重复构造会覆盖 self._client，旧连接的 WebSocket
        # 与收帧循环不会 disconnect，仍会向同一 handler 派帧（重复处理 + 资源泄漏）。
        if self._client is not None:
            return
        # 早退守卫放行 = 本次 start() 代表一次重启/首启：清掉旧会话状态。
        # 若不清 _displaced，被顶后重启虽重新认证（_ready=True），is_ready 仍被
        # _displaced 永久卡死为 False；_ready 一并复位，避免残留旧就绪。
        self._displaced = False
        self._ready = False
        client = WSClient(WSClientOptions(bot_id=self._bot_id, secret=self._secret))
        auth_result: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        client.on("authenticated", lambda: self._on_authenticated(auth_result))
        client.on("error", lambda error: self._on_error(error, auth_result, client))
        handler = self._make_handler(client)
        for event in _MESSAGE_EVENTS + _EVENT_EVENTS:
            client.on(event, handler)
        client.on(_DISPLACED_EVENT, self._make_displaced_handler(client))
        await client.connect()
        self._client = client
        await self._await_auth(auth_result)

    async def stop(self) -> None:
        """断开长连接。"""
        if self._client is None:
            return
        self._client.disconnect()
        self._client = None
        self._ready = False

    async def send_message(self, chatid: str, content: str) -> None:
        """主动向指定会话推送一条消息（不依赖入站帧）。

        与 `reply_stream` 的区别：无入站 `req_id`，因而**没有流式更新语义**、
        也不能更新已发出的消息（多帧快照与消息更新均要求入站帧）。需要
        "先回执再结论"的调用方分两次调用。

        实测（docs/agents/wecom-sdk-facts.md E12/E12b）：可用，约 300ms ack；
        消息体为 `{"msgtype":"markdown","markdown":{"content":…}}`；不支持
        `msgtype=stream`（被拒 40008）；无效 chatid 报 93006。

        Args:
            chatid: 目标会话 id（群聊填群 chatid；单聊填用户 userid）
            content: 消息正文（markdown）

        Raises:
            RuntimeError: 连接未就绪，或 SDK 发送失败
        """
        client = self._client
        if client is None:
            raise RuntimeError("wecom driver not connected")
        await client.send_message(
            chatid, {"msgtype": "markdown", "markdown": {"content": content}}
        )

    def _on_authenticated(self, auth_result: asyncio.Future[bool]) -> None:
        """SDK 认证成功：置就绪并唤醒等待。

        迟到的认证同样置就绪（状态要如实）；但对已致命的驱动直接丢弃——凭证类失败
        后不得被翻回就绪。
        """
        if self._fatal:
            return
        self._ready = True
        logger.info("[wecom] bot authenticated bot_id={}", encode_value(self._bot_id))
        if not auth_result.done():
            auth_result.set_result(True)

    def _on_error(
        self, error: BaseException, auth_result: asyncio.Future[bool], client: WSClient
    ) -> None:
        """SDK 连接期错误：凭证类失败 → 主动断开并置致命；其余只记 warning。

        Args:
            error: SDK 抛出的异常（凭证类形如 `Authentication failed: … (code: 853000)`）
            auth_result: 认证等待 future（凭证类失败时以 False 唤醒）
            client: 本驱动当前使用的客户端（用于主动断开）
        """
        message = str(error)
        if not message.startswith(_AUTH_FAILED_PREFIX):
            logger.warning(
                "[wecom] connect error bot_id={} err={}",
                encode_value(self._bot_id),
                message,
            )
            return
        self._fatal = True
        logger.warning(
            "[wecom] auth failed fatal bot_id={} err={}",
            encode_value(self._bot_id),
            message,
        )
        client.disconnect()
        if self._client is client:
            self._client = None
        if not auth_result.done():
            auth_result.set_result(False)

    async def _await_auth(self, auth_result: asyncio.Future[bool]) -> None:
        """等认证结果，上界 `_AUTH_TIMEOUT_SECONDS`；超时只降级（不计就绪、不断开）。"""
        try:
            await asyncio.wait_for(asyncio.shield(auth_result), _AUTH_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                "[wecom] auth wait timeout bot_id={} timeout={}s",
                encode_value(self._bot_id),
                _AUTH_TIMEOUT_SECONDS,
            )

    def _make_displaced_handler(self, client: WSClient):
        """构造"被顶替"处理器：主动断开且不抢回，避免与顶替者互踢。

        幂等：重复到达直接返回。断开后交出 `_client` 引用，使后续 `stop()` 成为
        no-op、并允许运维侧显式重启该台（重新 `start()` 才会再次抢回归属）。
        """

        async def _handle_displaced(frame: dict[str, Any]) -> None:
            if self._displaced:
                return
            self._displaced = True
            self._ready = False
            body = frame.get("body", {})
            logger.warning(
                "[wecom] bot displaced by newer connection bot_id={} msgid={}"
                "; disconnecting without reclaim",
                encode_value(self._bot_id),
                body.get("msgid", ""),
            )
            client.disconnect()
            if self._client is client:
                self._client = None

        return _handle_displaced

    def _make_handler(self, client: WSClient):
        """构造 SDK 事件处理器：帧 → InboundMessage → 业务 handler。"""

        async def _handle(frame: dict[str, Any]) -> None:
            try:
                msg = parse_inbound(frame["body"])
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[wecom] parse inbound failed bot_id={} err={}",
                    encode_value(self._bot_id),
                    e,
                )
                return
            sink = _WsSink(client, frame, generate_req_id("stream"))
            try:
                await self._handler(msg, sink)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[wecom] handler failed bot_id={} msgid={} err={}",
                    encode_value(self._bot_id),
                    msg.msgid,
                    e,
                )

        return _handle
