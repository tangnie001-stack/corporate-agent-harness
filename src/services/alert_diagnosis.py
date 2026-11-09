"""告警轮的"定位 + 诊断"编排（error 类告警）。

只做编排：定位、逐条诊断、拼卡片；推送由 alert_service 负责。
判定与文案仍全部在代码里，模型不参与。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.config import settings
from src.config.alert import AlertConfig
from src.config.diag import ALERT_EMPTY, ALERT_FAILED
from src.core import logging as core_logging
from src.core.log_events import Event
from src.rca.diagnose.access import AccessLogSource
from src.rca.diagnose.logs import LocalLogSource
from src.rca.diagnose.models import DiagnosisKind
from src.rca.diagnose.render import render_alert_card
from src.rca.locator import ErrorGroup, LocateResult, locate


def should_diagnose_alert(alertname: str, cfg: AlertConfig) -> bool:
    """灰度判断：告警名是否在"定位+诊断"白名单里。"""
    return alertname in cfg.diagnose_alert_names


async def build_alert_card(*, window_minutes: int, limit: int) -> str:
    """定位窗口内的代表 trace 并逐条诊断，返回告警级卡片或结论文案。

    Args:
        window_minutes: 回看分钟数
        limit: 最多诊断的类别数

    Returns:
        可直接推送的文本；任何失败都返回"未判定"类文案，不抛异常
    """
    now = datetime.now(UTC)
    result = locate(
        log_source=LocalLogSource(settings.DIAG_LOG_DIR),
        start=now - timedelta(minutes=window_minutes),
        end=now,
        access_source=_access_source(),
        limit=limit,
    )
    _log_located(result)
    if result.status == "empty":
        return ALERT_EMPTY
    if result.status == "failed":
        return ALERT_FAILED.format(reason=result.reason)

    from src.agents.tools.diagnose_tools import run_diagnosis

    cards: list[str] = []
    for group in result.groups:
        cards.append(
            await run_diagnosis(
                group.trace_id, kind=_kind_for(group).value, question=""
            )
        )
    extra = [(group.summary, group.count) for group in result.extra]
    return render_alert_card(traces_total=result.traces_total, cards=cards, extra=extra)


def _kind_for(group: ErrorGroup) -> DiagnosisKind:
    """按错误码推断分型：数字 5xx → 5xx；数字 4xx → 4xx；其余按错误告警默认 5xx。"""
    code = group.key[2]
    if code.isdigit():
        status = int(code)
        if 400 <= status < 500:
            return DiagnosisKind.ERROR_4XX
    return DiagnosisKind.ERROR_5XX


def _access_source() -> AccessLogSource | None:
    """访问日志未配置时返回 None（接口维度退回模块名）。"""
    directory = settings.DIAG_ACCESS_LOG_DIR.strip()
    if not directory:
        return None
    return AccessLogSource(directory)


def _log_located(result: LocateResult) -> None:
    """记录定位结果（不抛异常，不阻断告警）。"""
    core_logging.log_event(
        Event.TRACE_LOCATE_DONE,
        traces=result.traces_total,
        groups=len(result.groups) + len(result.extra),
        status=result.status,
    )
