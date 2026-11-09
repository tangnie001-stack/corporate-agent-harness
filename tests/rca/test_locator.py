"""告警定位器：聚类、取代表、上限与三态。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.rca.diagnose.logs import LogRow
from src.rca.locator import locate
from src.rca.sources.base import SourceFailedError

_NOW = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
_LOCATION = "src.services.turn_runner:_launch_and_register:524"


def _row(
    trace_id: str, message: str = "Chat stream setup failed: boom", **kw
) -> LogRow:
    ts = kw.pop("ts", _NOW - timedelta(minutes=5))
    event = kw.pop("event", "unclassified")
    return LogRow(
        ts=ts,
        level="ERROR",
        trace_id=trace_id,
        event=event,
        raw=f"2026-11-09 01:55:00.000 | ERROR | {trace_id} |  | none | {_LOCATION} - {message}",
    )


class _Logs:
    def __init__(self, rows, exc=None):
        self._rows = rows
        self._exc = exc

    def errors_between(self, start, end):
        if self._exc is not None:
            raise self._exc
        return list(self._rows)


def _ctx(logs, access=None, limit=3):
    return locate(
        log_source=logs,
        start=_NOW - timedelta(minutes=15),
        end=_NOW,
        access_source=access,
        limit=limit,
    )


def test_empty_window_is_conclusion():
    result = _ctx(_Logs([]))
    assert result.status == "empty"
    assert "未落到应用层" in result.reason


def test_failed_source_is_not_empty():
    result = _ctx(_Logs([], exc=SourceFailedError("timeout")))
    assert result.status == "failed"
    assert "未判定" in result.reason


def test_rows_without_trace_id():
    result = _ctx(_Logs([_row("")]))
    assert result.status == "empty"
    assert "无请求上下文" in result.reason


def test_clusters_and_caps():
    logs = _Logs(
        [
            _row("trace_a", "code=SESSION_NOT_FOUND", event="biz error"),
            _row("trace_b", "code=SESSION_NOT_FOUND", event="biz error"),
            _row(
                "trace_c",
                "code=DOC_NOT_FOUND",
                event="biz error",
                ts=_NOW - timedelta(minutes=4),
            ),
            _row("trace_d", "err=ack timeout", ts=_NOW - timedelta(minutes=3)),
            _row("trace_e", "err=ack timeout", ts=_NOW - timedelta(minutes=2)),
        ]
    )
    result = _ctx(logs, limit=1)
    assert result.status == "ok"
    assert result.traces_total == 5
    assert len(result.groups) == 1
    assert result.groups[0].count == 2  # 同类两条 trace
    assert len(result.extra) == 2  # 其余两类只计数
    assert result.groups[0].trace_id == "trace_a"  # 同类取最早


def test_interface_prefers_access_uri():
    class _Access:
        def row_for_trace(self, trace_id, at):
            class _Row:
                uri = "/api/chat"

            return _Row()

    result = _ctx(_Logs([_row("trace_a")]), access=_Access())
    assert result.groups[0].key[1] == "/api/chat"
