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
from src.chat.process_log import serialize_process
from src.chat.streaming import streaming_manager  # 共享单例：必须与 turn_runner 同一个
from src.config.alert import AlertConfig, AlertTarget, load_alert_config
from src.config.const import ALERT_USER_ID, WECOM_NS, AlertTexts, Channel
from src.core.logging import encode_value
from src.infra.llm.trace_context import current_channel, current_trace_id
from src.infra.llm.tracing import new_trace_id
from src.services import turn_runner, wecom_service
from src.utils.sse import SSEDoneEvent, SSEErrorEvent


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

# 火忘任务的强引用集合：asyncio 只对 Task 持弱引用，若不保留强引用，编排任务
# 可能在某个 await 挂起时被 GC 回收 ⇒ 该次报警既无结论也无兜底（本功能的初衷）。
_pending_tasks: set[asyncio.Task[None]] = set()


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
    try:
        cfg = load_alert_config()
    except ValueError as e:
        # 配置非法（正常已被启动期 fail-fast 拦下）：降级为 404，绝不 500
        logger.warning("[alert] config invalid err={}", encode_value(str(e)))
        return 404
    # token 为 ASCII 的前提不可假设：请求头可能含非 ASCII，故按字节比较
    # （hmac.compare_digest 对含非 ASCII 的 str 会抛 TypeError ⇒ 逃逸成 500）
    if cfg.token == "" or not hmac.compare_digest(
        (token_header or "").encode("utf-8"), cfg.token.encode("utf-8")
    ):
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
    task = asyncio.create_task(_run_analysis(cfg, event, target=cfg.target))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)
    return 202


async def _get_service():
    """取 AppService（惰性导入，避免 services 内部循环依赖）。"""
    from src.services.app_service import get_app_service

    return await get_app_service()


async def _consume_events(events) -> tuple[list[dict], bool]:
    """消费事件流，收集 events_log 与是否正常收尾。

    Args:
        events: TurnHandle.events

    Returns:
        (events_log, abnormal) —— abnormal 表示收到 error 事件、或流在无 done
        的情况下结束
    """
    events_log: list[dict] = []
    seen_done = False
    abnormal = False
    async for ev in events:
        if isinstance(ev, SSEDoneEvent):
            seen_done = True
        if isinstance(ev, SSEErrorEvent):
            abnormal = True
        events_log.append({"type": ev.type, "payload": ev.payload_for_buffer()})
    return events_log, abnormal or not seen_done


async def _run_analysis(
    cfg: AlertConfig, event: AlertEvent, *, target: AlertTarget | None
) -> None:
    """后台编排：占位 → 跑 Agent（禁澄清）→ 结论 / 兜底。

    Args:
        cfg: 告警配置
        event: 解析后的事件
        target: 目标群（None = 未配置，不发送）

    Returns:
        无返回值；推送为副作用。
    """
    if target is None:
        logger.warning(
            "[alert] no target configured alarm_id={}", encode_value(event.alarm_id)
        )
        return

    # 占位必须在后台流里发（不在响应路径上）：等 ack 的超时常量与上游 5 秒同量级
    await _safe_send(target, render_placeholder(event))

    session_id = derive_alert_session_id(target.bot_key, event.alarm_id)
    channel_token = current_channel.set(Channel.ALERT)
    trace_token = current_trace_id.set(new_trace_id())
    try:
        try:
            svc = await _get_service()
            handle = await turn_runner.start_turn(
                svc,
                session_id=session_id,
                kb_id="",
                query=build_query(event),
                user_id=ALERT_USER_ID,
                title=f"[企微·{target.bot_key}] 报警 {event.alertname}",
                clarify=False,
            )
        except turn_runner.TurnBusy:
            logger.warning(
                "[alert] turn busy alarm_id={}", encode_value(event.alarm_id)
            )
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[alert] turn start failed alarm_id={} err={}",
                encode_value(event.alarm_id),
                e,
            )
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return

        try:
            events_log, abnormal = await asyncio.wait_for(
                _consume_events(handle.events), timeout=cfg.turn_timeout_seconds
            )
        except TimeoutError:
            logger.warning(
                "[alert] turn timeout alarm_id={} timeout={}s",
                encode_value(event.alarm_id),
                cfg.turn_timeout_seconds,
            )
            streaming_manager.cancel(session_id)
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[alert] turn consume failed alarm_id={} err={}",
                encode_value(event.alarm_id),
                e,
            )
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return

        try:
            _, purified = serialize_process(events_log)
        except Exception as e:  # noqa: BLE001
            # 净化失败 ⇒ 视作空正文，交给下方空回答分支走兜底，绝不静默丢失
            logger.exception(
                "[alert] serialize failed alarm_id={} err={}",
                encode_value(event.alarm_id),
                e,
            )
            purified = ""
        if abnormal or purified.strip() == "":
            logger.warning(
                "[alert] empty or abnormal answer alarm_id={} abnormal={}",
                encode_value(event.alarm_id),
                abnormal,
            )
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return
        await _safe_send(target, purified)
    finally:
        current_trace_id.reset(trace_token)
        current_channel.reset(channel_token)


async def _safe_send(target: AlertTarget, content: str) -> None:
    """推送一段文本到目标群；失败只记 warning，不中断流程。

    Args:
        target: 目标
        content: 正文
    """
    ok = await wecom_service.send_to_session(target.bot_key, target.chatid, content)
    if not ok:
        logger.warning(
            "[alert] push failed bot_key={} chatid={}",
            encode_value(target.bot_key),
            encode_value(target.chatid),
        )
