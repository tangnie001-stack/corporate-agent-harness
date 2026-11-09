"""诊断证据源的窄协议：只描述流水线用得到的最小方法面。

与 alert-triage 的 `LogSource` 并列，不扩展它（两者查询维度不同）。
三态语义沿用 `SourceEmptyError` / `SourceFailedError`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.rca.diagnose.access import AccessRow
from src.rca.diagnose.history import HistoryRound
from src.rca.diagnose.logs import LogRow
from src.rca.diagnose.models import TraceFacts


class TraceFetcher(Protocol):
    """按 trace 取检索事实（Langfuse 实现；测试用替身）。"""

    def fetch(self, trace_id: str) -> TraceFacts:
        """返回检索调用、证据与引用判定；取不到抛三态异常。"""
        ...


class ErrorLogReader(Protocol):
    """按窗口读应用 ERROR 行。"""

    def errors_between(self, start: datetime, end: datetime) -> list[LogRow]:
        """返回窗口内的 ERROR / CRITICAL 行，按时间升序。"""
        ...


class HistoryReader(Protocol):
    """按 trace 取前序轮次事实（有界）。"""

    def rounds_before(self, trace_id: str, *, limit: int) -> tuple[HistoryRound, ...]:
        """返回最近 limit 轮，按时间升序；无历史返回空元组。"""
        ...


class AccessReader(Protocol):
    """按 trace 读 nginx 访问日志。"""

    def row_for_trace(self, trace_id: str, at: datetime) -> AccessRow | None:
        """返回该 trace 最近一条访问记录；取不到返回 None。"""
        ...
