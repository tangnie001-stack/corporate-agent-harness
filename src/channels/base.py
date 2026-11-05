"""通道抽象：屏蔽接入方式（URL 回调 / 长连接）的传输差异。

业务代码只依赖本模块的 InboundMessage / ReplySink / MessageHandler，
切换接入方式不改业务。
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InboundMessage:
    """统一入站事件——消息与事件共用同一结构。"""

    msgid: str  # 本次回调唯一标志，用于排重
    aibotid: str  # 智能机器人 id
    chatid: str | None  # 群聊会话 id；单聊为 None
    chattype: str  # 会话类型："single" | "group"
    from_userid: str  # 触发者 userid（非超管场景为密文）
    msgtype: str  # text/image/mixed/voice/file/video，或 "event"
    text: str | None  # 仅文本消息取 text.content；其它为 None
    event_type: str | None  # 事件类型（msgtype == "event" 时非空）
    raw: dict  # 原始明文兜底；只读约定，不深拷贝
    quote_text: str | None = None  # 被引用消息的文本；未引用或企微给占位时为 None


class ReplySink(Protocol):
    """一条入站消息对应的回复出口；由驱动决定落到 HTTP 响应还是 WS 帧。"""

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        """回复流式消息。

        Args:
            content: 本帧内容（企微为累积全文：每次刷新整段替换气泡）
            finish: True 表示结束本轮
            feedback: 反馈信息（仅首帧有效，值为 {"id": trace_id}）；
                不支持该能力的通道忽略之
        """
        ...


class MessageHandler(Protocol):
    """业务处理：吃入站事件，用 sink 回复。"""

    async def __call__(self, msg: InboundMessage, sink: ReplySink) -> None: ...


class ChannelDriver(Protocol):
    """接入通道驱动：回调为 no-op 生命周期，长连接在 start/stop 建连/断开。"""

    name: str  # 驱动标识

    async def start(self) -> None:
        """启动通道（回调 no-op）。"""
        ...

    async def stop(self) -> None:
        """停止通道（回调 no-op）。"""
        ...
