"""诊断工具：给定 trace_id，产出可点开的诊断卡片。

挂日志能力档（LOG_PROFILES），不是独立机器人（D12）。只读：除诊断报告表外不写任何数据。
判定与文案全部在代码里（D1）：模型只负责把卡片转述给用户，不改写其中的数字。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace
from typing import Any

from langchain_core.tools import BaseTool, tool
from loguru import logger

from src.config import settings
from src.config.diag import (
    INVALID_KIND,
    INVALID_TRACE_ID,
    TRACE_NOT_FOUND,
    TRACE_UNAVAILABLE,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.db.engine import session_factory
from src.infra.db.repos.diag_repo import DiagRepo
from src.rca.diagnose.logs import LocalLogSource
from src.rca.diagnose.models import DiagnosisKind, DiagnosisReport
from src.rca.diagnose.pipeline import diagnose
from src.rca.diagnose.render import render_card
from src.rca.diagnose.sources import TraceSource

_TRACE_RE = re.compile(r"^trace_[0-9a-fA-F-]{36}$")
_KINDS: dict[str, DiagnosisKind] = {
    "answer": DiagnosisKind.ANSWER,
    "5xx": DiagnosisKind.ERROR_5XX,
    "4xx": DiagnosisKind.ERROR_4XX,
}

_trace_source: Any = None
_log_source: Any = None
_repo: Any = None


def configure(
    trace_source: Any = None, log_source: Any = None, repo: Any = None
) -> None:
    """注入证据源与仓库（测试用）；传 None 表示恢复按配置默认构造。

    Args:
        trace_source: 具备 `fetch(trace_id)` 的证据源
        log_source: 具备 `errors_between(start, end)` 的证据源
        repo: 具备 `save(report, ...)` 的报告仓库
    """
    global _trace_source, _log_source, _repo
    _trace_source = trace_source
    _log_source = log_source
    _repo = repo


def _sources() -> tuple[Any, Any]:
    """返回当前证据源；未注入时按配置构造（trace 走 Langfuse，日志走本地目录）。"""
    trace_source = _trace_source if _trace_source is not None else TraceSource()
    log_source = (
        _log_source
        if _log_source is not None
        else LocalLogSource(settings.DIAG_LOG_DIR)
    )
    return trace_source, log_source


def _repo_or_default() -> Any:
    """返回报告仓库；未注入时用进程级 session factory 构造。"""
    if _repo is not None:
        return _repo
    return DiagRepo(session_factory)


def _detail_url(diag_id: str) -> str:
    """拼详情页地址；未配置基地址时返回空串（卡片不带链接）。"""
    base = settings.DIAG_DETAIL_BASE_URL.strip().rstrip("/")
    if not base or not diag_id:
        return ""
    return f"{base}/diagnose.html?diag_id={diag_id}"


def _failure_text(report: DiagnosisReport) -> str | None:
    """把 trace 取数失败映射成入口文案；非失败返回 None。

    Args:
        report: 流水线产出的报告

    Returns:
        三种入口文案之一，或 None（继续按卡片返回）
    """
    if not report.summary.startswith("trace 不可用"):
        return None
    if "NotFound" in report.summary:
        return TRACE_NOT_FOUND
    return TRACE_UNAVAILABLE


@tool("diagnose_trace")
async def diagnose_trace(trace_id: str, kind: str = "answer") -> str:
    """诊断一条 trace：给出档位、归属、根因与证据计数，并附详情链接。

    何时调用：用户给出 trace_id 并问"为什么报错/为什么答错/查一下这条"时。
    只读诊断，不做任何修复；结论里的"未判定"就是未判定，不要替它编原因。

    Args:
        trace_id: 形如 trace_<uuid> 的请求标识
        kind: 问题分型，answer（答错，默认）/ 5xx（服务端错误）/ 4xx（客户端错误）

    Returns:
        可转述给用户的 markdown 卡片；格式或取数失败时返回对应的提示文案
    """
    if not _TRACE_RE.match(trace_id.strip()):
        return INVALID_TRACE_ID
    parsed_kind = _KINDS.get(kind.strip().lower())
    if parsed_kind is None:
        return INVALID_KIND.format(kind=kind)

    trace_source, log_source = _sources()
    started = time.monotonic()
    report = await asyncio.to_thread(
        diagnose,
        trace_id.strip(),
        kind=parsed_kind,
        trace_source=trace_source,
        log_source=log_source,
    )
    failure = _failure_text(report)
    if failure is not None:
        logger.info(
            "[diagnose] trace rejected trace_id={} kind={} reason={}",
            trace_id.strip(),
            parsed_kind.value,
            report.summary,
        )
        return failure

    cost_ms = int((time.monotonic() - started) * 1000)
    diag_id = ""
    try:
        diag_id = await _repo_or_default().save(
            report, env=settings.DIAG_ENV, cost_ms=cost_ms
        )
    except Exception as exc:  # noqa: BLE001  落库失败不阻断结论，只是没有详情链接
        logger.warning(
            "[diagnose] persist failed trace_id={} err={}", trace_id.strip(), exc
        )
    if diag_id:
        report = replace(report, diag_id=diag_id)
    core_logging.log_event(
        Event.DIAGNOSE_DONE,
        trace_id=trace_id.strip(),
        kind=parsed_kind.value,
        tier=report.tier.value,
        root_cause=report.root_cause.value,
        owner=report.owner.value,
        cost_ms=cost_ms,
        diag_id=diag_id,
    )
    if report.root_cause.value == "undetermined":
        core_logging.log_event(
            Event.DIAGNOSE_UNDETERMINED,
            trace_id=trace_id.strip(),
            kind=parsed_kind.value,
            reason=report.summary,
        )
    return render_card(report, detail_url=_detail_url(diag_id))


def make_diagnose_tools() -> list[BaseTool]:
    """返回诊断工具（装配给日志能力档）。

    Returns:
        [diagnose_trace]；在注册表内以 readonly=True 登记
    """
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("diagnose_trace", diagnose_trace, readonly=True)
    return registry.enabled_tools()
