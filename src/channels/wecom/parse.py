"""入站解析：解密后的 JSON 明文 → InboundMessage（回调 / 长连接共用）。"""

from src.channels.base import InboundMessage

#: 企微对被引用消息不支持回显时给的占位，按"未引用"处理
_QUOTE_PLACEHOLDER = "[该消息类型暂不能展示]"


def _extract_quote_text(plain: dict) -> str | None:
    """取被引用消息的文本；无引用或只有占位时返回 None。"""
    quote = plain.get("quote")
    if not isinstance(quote, dict):
        return None
    text_obj = quote.get("text")
    if not isinstance(text_obj, dict):
        return None
    content = text_obj.get("content")
    if not isinstance(content, str):
        return None
    cleaned = content.strip()
    if cleaned == "" or cleaned == _QUOTE_PLACEHOLDER:
        return None
    return cleaned


class InboundParseError(ValueError):
    """入站报文缺失必填字段或结构非法。"""


def parse_inbound(plain: dict) -> InboundMessage:
    """把企微回调明文解析为统一入站事件。

    Args:
        plain: 解密后的 JSON 明文 dict

    Returns:
        InboundMessage

    Raises:
        InboundParseError: 缺少 msgid 等必填字段
    """
    msgid = plain.get("msgid")
    if not msgid:
        raise InboundParseError("missing msgid")

    msgtype = plain.get("msgtype", "")
    text: str | None = None
    event_type: str | None = None
    if msgtype == "event":
        event = plain.get("event") or {}
        event_type = event.get("eventtype")
    elif msgtype == "text":
        text_obj = plain.get("text") or {}
        text = text_obj.get("content")

    from_obj = plain.get("from") or {}
    return InboundMessage(
        msgid=msgid,
        aibotid=plain.get("aibotid", ""),
        chatid=plain.get("chatid"),
        chattype=plain.get("chattype", ""),
        from_userid=from_obj.get("userid", ""),
        msgtype=msgtype,
        text=text,
        event_type=event_type,
        raw=plain,
        quote_text=_extract_quote_text(plain),
    )
