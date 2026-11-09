"""本地应用日志证据源：按时间窗取错误行、按 trace 取预算标记。

日志行格式（src/core/logging.py 的 _LOG_FORMAT）：
    时间 | 级别 | trace_id | session_id | channel | 位置 - 消息
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_TRACE_RE = re.compile(r"^trace_[0-9a-f-]{36}$")
_EVENT_RE = re.compile(r"^\[[a-z]+\]\s+([a-z][a-z ]*?)(?:\s+[a-z_]+=|$)")
_TIME_FMT = "%Y-%m-%d %H:%M:%S.%f"
_ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})
# 预算标记的回看窗口：trace 从开始到落全所有事件的跨度通常在分钟级
_FLAG_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class LogRow:
    """一条日志事实（不保留原文以外的解释）。"""

    ts: datetime  # 日志时间（UTC，按写入时的本地时间解析后视作 UTC）
    level: str  # ERROR / CRITICAL
    trace_id: str  # 第 3 段；非法或缺失时为空串（用于"未落到应用层"判定）
    event: str  # 从消息前缀提取的英文事件名（如 biz error）
    raw: str  # 原始行（供证据定位，展示前需脱敏）


@dataclass(frozen=True)
class BudgetFlags:
    """单条 trace 是否触发过预算类告警（来源：日志事件行）。"""

    history_budget_exceeded: bool = False  # 历史预算被裁（history budget exceeded）
    context_budget_high: bool = False  # 上下文预算告警（context budget high）


class LocalLogSource:
    """读容器卷里的 app_*.log。"""

    def __init__(self, log_dir: str) -> None:
        """Args:
        log_dir: 日志目录（生产为 /data/logs，测试注入临时目录）。
        """
        self._dir = Path(log_dir)

    def errors_between(self, start: datetime, end: datetime) -> list[LogRow]:
        """返回窗口内的 ERROR/CRITICAL 行，按时间升序。"""
        rows: list[LogRow] = []
        for ts, level, raw_tid, message, raw in self._iter_window(start, end):
            if level not in _ERROR_LEVELS:
                continue
            rows.append(
                LogRow(
                    ts=ts,
                    level=level,
                    trace_id=raw_tid if _TRACE_RE.match(raw_tid) else "",
                    event=_extract_event(message),
                    raw=raw,
                )
            )
        rows.sort(key=lambda r: r.ts)
        return rows

    def flags_for_trace(self, trace_id: str, at: datetime) -> BudgetFlags:
        """扫描 trace 前后 1 小时的日志，取预算类告警标记。

        Args:
            trace_id: 目标 trace
            at: trace 发生时刻（UTC）

        Returns:
            预算标记；日志缺失或没有该 trace 的行时全 False（"没记录"不等于"没发生"，
            调用方只使用 True，因此不影响结论）
        """
        history_hit = False
        context_hit = False
        for _ts, _level, row_trace, message, _raw in self._iter_window(
            at - _FLAG_WINDOW, at + _FLAG_WINDOW
        ):
            if row_trace != trace_id:
                continue
            if "history budget exceeded" in message:
                history_hit = True
            if "context budget high" in message:
                context_hit = True
        return BudgetFlags(
            history_budget_exceeded=history_hit, context_budget_high=context_hit
        )

    def _iter_window(
        self, start: datetime, end: datetime
    ) -> Iterator[tuple[datetime, str, str, str, str]]:
        """遍历窗口内的日志行：产出 (时间, 级别, trace_id, 消息, 原始行)。"""
        if not self._dir.is_dir():
            return
        for day in _days_between(start, end):
            path = self._dir / f"app_{day.isoformat()}.log"
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    parts = line.split(" | ", 5)
                    if len(parts) < 6:
                        continue
                    ts = _parse_ts(parts[0].strip())
                    if ts is None or ts < start or ts > end:
                        continue
                    yield (
                        ts,
                        parts[1].strip(),
                        parts[2].strip(),
                        parts[5].split(" - ", 1)[-1].strip(),
                        line.rstrip("\n"),
                    )


def _days_between(start: datetime, end: datetime) -> list[date]:
    """窗口覆盖的日期列表（含首尾，最多 31 天，防止误传超大窗口）。"""
    first = start.date()
    last = end.date()
    span = (last - first).days
    if span < 0:
        return []
    return [first + timedelta(days=i) for i in range(min(span, 30) + 1)]


def _parse_ts(text: str) -> datetime | None:
    """解析日志时间；失败返回 None（该行跳过）。"""
    try:
        return datetime.strptime(text, _TIME_FMT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _extract_event(message: str) -> str:
    """从 `[前缀] 事件名 key=...` 提取事件名；不匹配时返回空串。"""
    match = _EVENT_RE.match(message)
    return match.group(1).strip() if match else ""
