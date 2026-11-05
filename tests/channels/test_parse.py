"""入站解析单测：消息/事件分流与字段容错。"""

import pytest

from src.channels.wecom.parse import InboundParseError, parse_inbound


def test_text_message_extracts_content():
    plain = {
        "msgid": "M1",
        "aibotid": "BOT1",
        "chatid": "C1",
        "chattype": "group",
        "from": {"userid": "U1"},
        "msgtype": "text",
        "text": {"content": "@RobotA hello"},
    }
    msg = parse_inbound(plain)
    assert msg.msgid == "M1"
    assert msg.chattype == "group"
    assert msg.from_userid == "U1"
    assert msg.msgtype == "text"
    assert msg.text == "@RobotA hello"
    assert msg.event_type is None


def test_event_message_sets_event_type_and_no_text():
    plain = {
        "msgid": "M2",
        "aibotid": "BOT1",
        "chattype": "single",
        "from": {"userid": "U2"},
        "msgtype": "event",
        "event": {"eventtype": "enter_chat"},
    }
    msg = parse_inbound(plain)
    assert msg.msgtype == "event"
    assert msg.event_type == "enter_chat"
    assert msg.text is None
    assert msg.chatid is None


def test_non_text_message_has_no_text():
    plain = {
        "msgid": "M3",
        "chattype": "single",
        "from": {"userid": "U3"},
        "msgtype": "image",
        "image": {"url": "http://x/y"},
    }
    msg = parse_inbound(plain)
    assert msg.text is None
    assert msg.msgtype == "image"


def test_missing_msgid_raises():
    with pytest.raises(InboundParseError):
        parse_inbound({"msgtype": "text", "text": {"content": "hi"}})


def _plain_with_quote(quote):
    plain = {
        "msgid": "M2",
        "aibotid": "BOT1",
        "chatid": "C1",
        "chattype": "group",
        "from": {"userid": "U1"},
        "msgtype": "text",
        "text": {"content": "@Robot 看一下"},
    }
    if quote is not None:
        plain["quote"] = quote
    return plain


def test_quote_text_extracted():
    """引用普通文本消息时取出被引用内容。"""
    msg = parse_inbound(
        _plain_with_quote({"msgtype": "text", "text": {"content": "今天订单少了很多"}})
    )
    assert msg.quote_text == "今天订单少了很多"


def test_quote_placeholder_ignored():
    """企微给占位时按未引用处理。"""
    msg = parse_inbound(
        _plain_with_quote(
            {"msgtype": "text", "text": {"content": "[该消息类型暂不能展示]"}}
        )
    )
    assert msg.quote_text is None


def test_quote_absent_is_none():
    """没有 quote 字段时为 None。"""
    msg = parse_inbound(_plain_with_quote(None))
    assert msg.quote_text is None
