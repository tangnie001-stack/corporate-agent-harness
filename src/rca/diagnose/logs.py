"""本地应用日志证据源：按时间窗取错误行与 trace_id。

日志行格式（src/core/logging.py 的 _LOG_FORMAT）：
    时间 | 级别 | trace_id | session_id | channel | 位置 - 消息
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_TRACE_RE = re.compile(r"^trace_[0-9a-f-]{36}$")
_EVENT_RE = re.compile(r"^\[[a-z]+\]\s+([a-z][a-z ]*?)(?:\s+[a-z_]+=|$)")
_TIME_FMT = "%Y-%m-%d %H:%M:%S.%f"
_ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})


@dataclass(frozen=True)
class LogRow:
    """一条日志事实（不保留原文以外的解释）。"""

    ts: datetime  # 日志时间（UTC，按写入时的本地时间解析后视作 UTC）
    level: str  # ERROR / CRITICAL
    trace_id: str  # 第 3 段；非法或缺失时为空串（用于"未落到应用层"判定）
    event: str  # 从消息前缀提取的英文事件名（如 biz error）
    raw: str  # 原始行（供证据定位，展示前需脱敏）


class LocalLogSource:
    """读容器卷里的 app_*.log。"""

    def __init__(self, log_dir: str) -> None:
        """Args:
        log_dir: 日志目录（生产为 /data/logs，测试注入临时目录）。
        """
        self._dir = Path(log_dir)

    def errors_between(self, start: datetime, end: datetime) -> list[LogRow]:
        """返回窗口内的 ERROR/CRITICAL 行，按时间升序。"""
        if not self._dir.is_dir():
            return []
        rows: list[LogRow] = []
        for day in _days_between(start, end):
            path = self._dir / f"app_{day.isoformat()}.log"
            if not path.is_file():
                continue
            rows.extend(self._scan(path, start, end))
        rows.sort(key=lambda r: r.ts)
        return rows

    def _scan(self, path: Path, start: datetime, end: datetime) -> list[LogRow]:
        """逐行解析单个文件，跳过窗口外与非错误级别。"""
        out: list[LogRow] = []
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                parts = line.split(" | ", 5)
                if len(parts) < 6:
                    continue
                level = parts[1].strip()
                if level not in _ERROR_LEVELS:
                    continue
                ts = _parse_ts(parts[0].strip())
                if ts is None or ts < start or ts > end:
                    continue
                raw_tid = parts[2].strip()
                message = parts[5].split(" - ", 1)[-1].strip()
                out.append(
                    LogRow(
                        ts=ts,
                        level=level,
                        trace_id=raw_tid if _TRACE_RE.match(raw_tid) else "",
                        event=_extract_event(message),
                        raw=line.rstrip("\n"),
                    )
                )
        return out


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
