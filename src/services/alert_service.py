"""阿里云告警接入：解析、去重、编排、推送与兜底（change: alibaba-alert-notify）。

分层：`api/alerts.py` 只读请求头 token 并把 body 交给本模块；解析/去重/编排
一律落在本层（`api/` 不得直接 import `config/`、`infra/`）。

载荷契约（design D21）：ARMS 侧必须按本项目规定的自定义 body 模板推送
（默认模板不含 `alarmId`/`fingerprint`），字段名即本模块解析的字段名。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from src.config.const import WECOM_NS, AlertTexts


@dataclass(frozen=True)
class AlertEvent:
    """一次告警请求解析后的值对象。"""

    alarm_id: str  # 报警标识（派生会话 key）
    alertname: str  # 告警名称
    level: str  # 告警等级
    details: str  # 各条告警描述的合并文本
    fingerprints: tuple[str, ...]  # 逐条告警指纹（入口去重键）


def parse_alert_payload(raw: bytes) -> AlertEvent:
    """解析 ARMS 通知载荷（按约定模板的字段名）。

    Args:
        raw: 原始请求体字节

    Returns:
        AlertEvent

    Raises:
        ValueError: JSON 非法，或缺报警标识 / 缺告警名称 / 无任何指纹
    """
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("告警载荷不是合法 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("告警载荷必须是 JSON 对象")  # noqa: TRY004

    alarm_id = data.get("alarmId")
    if not isinstance(alarm_id, str) or alarm_id == "":
        raise ValueError("告警载荷缺 alarmId")

    labels = data.get("commonLabels") or {}
    alertname = labels.get("alertname") if isinstance(labels, dict) else None
    if not isinstance(alertname, str) or alertname == "":
        raise ValueError("告警载荷缺 commonLabels.alertname")

    level_raw = data.get("level")
    level = level_raw if isinstance(level_raw, str) and level_raw else "-"

    raw_alerts = data.get("alerts")
    alerts = raw_alerts if isinstance(raw_alerts, list) else []
    fingerprints: list[str] = []
    messages: list[str] = []
    for item in alerts:
        if not isinstance(item, dict):
            continue
        fp = item.get("fingerprint")
        if isinstance(fp, str) and fp:
            fingerprints.append(fp)
        annotations = item.get("annotations") or {}
        msg = annotations.get("message") if isinstance(annotations, dict) else None
        if isinstance(msg, str) and msg:
            messages.append(msg)
    if not fingerprints:
        raise ValueError("告警载荷无有效 fingerprint")

    return AlertEvent(
        alarm_id=alarm_id,
        alertname=alertname,
        level=level,
        details="\n".join(messages),
        fingerprints=tuple(fingerprints),
    )


def derive_alert_session_id(bot_key: str, alarm_id: str) -> str:
    """派生报警会话标识（`scope=alert`，与群聊/单聊问答会话隔离）。

    同一报警标识复用同一会话（历史累积）；标识每次都变时自动退化为"每次新会话"。

    Args:
        bot_key: 机器人别名
        alarm_id: 报警标识

    Returns:
        36 字符 UUIDv5 字符串
    """
    return str(uuid.uuid5(WECOM_NS, f"wecom|{bot_key}|alert|{alarm_id}"))


def build_query(event: AlertEvent) -> str:
    """按模板构造喂给 Agent 的输入文本。"""
    return AlertTexts.QUERY_TEMPLATE.format(
        alertname=event.alertname, level=event.level, details=event.details
    )


def render_placeholder(event: AlertEvent) -> str:
    """受理占位文案（第一段推送）。"""
    return AlertTexts.PLACEHOLDER_TEMPLATE.format(alertname=event.alertname)


def render_fallback(event: AlertEvent, max_chars: int) -> str:
    """兜底转发文案：带前缀、限长、不整包 dump 原始载荷。

    Args:
        event: 解析后的事件
        max_chars: 正文长度上限（前缀不计入）

    Returns:
        形如 `[原始告警] CPU 使用率过高（P2）\n<正文截断>`
    """
    body = f"{event.alertname}（{event.level}）\n{event.details}".strip()
    if len(body) > max_chars:
        body = body[:max_chars]
    return f"{AlertTexts.FALLBACK_PREFIX} {body}"
