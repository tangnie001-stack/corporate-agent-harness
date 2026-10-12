"""阿里云告警接入：解析、去重、编排、推送与兜底（change: alibaba-alert-notify）。

分层：`api/alerts.py` 只读请求头 token 并把 body 交给本模块；解析/去重/编排
一律落在本层（`api/` 不得直接 import `config/`、`infra/`）。

载荷契约（design D21）：ARMS 侧必须按本项目规定的自定义 body 模板推送
（默认模板不含 `alarmId`/`fingerprint`），字段名即本模块解析的字段名。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.channels.wecom.bounded_map import BoundedTtlMap
from src.config.alert import AlertConfig, AlertTarget, load_alert_config
from src.config.const import WECOM_NS, AlertTexts
from src.core.logging import encode_value


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


class AlertDedup:
    """入口去重：逐条指纹 + 时间窗的有界结构。

    仅当**全部**指纹都命中窗口时判为重复（宁可多跑一条 Agent，也不漏报）；
    判为不重复时把全部指纹登记进窗口。
    """

    def __init__(
        self,
        *,
        capacity: int,
        ttl_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化。

        Args:
            capacity: 指纹条目容量上限（超出淘汰最旧）
            ttl_seconds: 指纹存活秒数（自登记起算）
            monotonic: 单调时钟（测试注入以稳定断言过期）
        """
        self._map = BoundedTtlMap(
            capacity=capacity, ttl_seconds=ttl_seconds, monotonic=monotonic
        )

    def is_duplicate(self, fingerprints: Sequence[str]) -> bool:
        """判断本批指纹是否全部在窗口内出现过。

        Args:
            fingerprints: 本条请求涉及的全部指纹（调用方保证非空）

        Returns:
            True 表示全部命中（应丢弃）；False 表示应正常处理（已登记本批）
        """
        if all(self._map.get(fp) is not None for fp in fingerprints):
            return True
        for fp in fingerprints:
            self._map.put(fp, "1")
        return False

    def __len__(self) -> int:
        """当前窗口内登记条目数（测试用）。"""
        return len(self._map)


# 进程级单例：载荷去重窗口与配置随进程生命周期（与企微桥接 handler 同款纪律）
_dedup: AlertDedup | None = None


def _get_dedup() -> AlertDedup:
    """惰性构造去重器（配置在首次调用时读取）。"""
    global _dedup
    if _dedup is None:
        cfg = load_alert_config()
        _dedup = AlertDedup(
            capacity=cfg.dedup_capacity, ttl_seconds=cfg.dedup_window_seconds
        )
    return _dedup


def _reset_for_test() -> None:
    """测试钩子：丢弃进程级去重器与配置缓存。"""
    global _dedup
    _dedup = None


def handle_webhook(raw_body: bytes, token_header: str | None) -> int:
    """处理一次告警请求，返回 HTTP 状态码。

    校验与去重通过后**立即**返回 202，把"跑 Agent → 推送"放进后台任务 ——
    上游 Webhook 有 5 秒硬超时，等待分析会把响应拖过上限并触发重试。

    Args:
        raw_body: 原始请求体
        token_header: 请求头里的 token（缺失为 None）

    Returns:
        202 已受理（含去重丢弃）；404 token 不匹配或未配置；400 载荷非法
    """
    cfg = load_alert_config()
    if cfg.token == "" or not hmac.compare_digest(token_header or "", cfg.token):
        return 404
    try:
        event = parse_alert_payload(raw_body)
    except ValueError as e:
        logger.warning("[alert] payload invalid err={}", e)
        return 400
    if _get_dedup().is_duplicate(event.fingerprints):
        logger.info(
            "[alert] duplicate dropped alarm_id={} n_fp={}",
            encode_value(event.alarm_id),
            len(event.fingerprints),
        )
        return 202
    asyncio.create_task(
        _run_analysis(
            cfg,
            event,
            target=cfg.target,
            bot_key=cfg.target.bot_key if cfg.target else "",
        )
    )
    return 202


async def _run_analysis(
    cfg: AlertConfig, event: AlertEvent, *, target: AlertTarget | None, bot_key: str
) -> None:
    """占位：Task 8 实现完整编排（本存根以异常兜底，禁止静默存活）。

    Raises:
        NotImplementedError: 恒抛 —— 编排尚未实现，后台任务须以异常暴露而非静默丢弃。
    """
    raise NotImplementedError("_run_analysis 由 Task 8 实现，此存根不得留在最终提交里")
