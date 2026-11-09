"""告警定位器：窗口内 ERROR 行 → 去重 trace → 聚类 → 每类取最早代表。

只读应用日志；access 日志仅用于补"接口"维度（取不到退回模块名）。
三态语义见 design D9：empty 的两种形态本身就是结论，failed 不得并入 empty。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.rca.sources.base import SourceFailedError

_CODE_RE = re.compile(r"(?:code|status)=([A-Za-z0-9_]+)")
_TIME_FMT = "%Y-%m-%d %H:%M:%S.%f"


@dataclass(frozen=True)
class ErrorGroup:
    """一类错误（按 类型/接口/错误码 聚合）。"""

    key: tuple[str, str, str]  # (错误类型, 接口, 错误码)
    trace_id: str  # 代表 trace（该类最早）
    count: int  # 该类涉及的去重 trace 数
    first_ts: datetime  # 该类最早出现的时刻
    summary: str  # 事件名 + 错误码（不含原文）


@dataclass(frozen=True)
class LocateResult:
    """一次定位的产物；status 决定调用方走哪条分支。"""

    status: str  # ok / empty / failed
    groups: tuple[ErrorGroup, ...]  # 前 limit 类
    extra: tuple[ErrorGroup, ...]  # 超限类别（只计数）
    traces_total: int  # 窗口内去重 trace 数
    reason: str  # empty/failed 的结论文案；ok 为空串


def locate(
    *,
    log_source: Any,
    start: datetime,
    end: datetime,
    access_source: Any = None,
    limit: int = 3,
) -> LocateResult:
    """在窗口内定位错误 trace 并聚类。

    Args:
        log_source: 具备 errors_between(start, end) 的日志源
        start: 窗口起点
        end: 窗口终点
        access_source: 具备 row_for_trace(trace_id, at) 的访问日志源；None 表示不补接口
        limit: 最多返回的类别数（其余进 extra）

    Returns:
        LocateResult；取数失败不抛异常，转成 failed 态
    """
    try:
        rows = log_source.errors_between(start, end)
    except SourceFailedError as exc:
        return LocateResult("failed", (), (), 0, f"日志源不可用，未判定：{exc}")
    except Exception as exc:  # noqa: BLE001  任何取数异常都按不可用处理
        return LocateResult("failed", (), (), 0, f"日志源不可用，未判定：{exc}")

    if not rows:
        return LocateResult(
            "empty",
            (),
            (),
            0,
            "窗口内无 ERROR 记录：错误未落到应用层（可能来自网关 / 容器被 OOM / 与本应用无关）",
        )

    first_by_trace: dict[str, Any] = {}
    for row in rows:  # rows 已按时间升序
        trace_id = getattr(row, "trace_id", "")
        if trace_id and trace_id not in first_by_trace:
            first_by_trace[trace_id] = row
    if not first_by_trace:
        return LocateResult(
            "empty",
            (),
            (),
            0,
            f"窗口内有 {len(rows)} 条 ERROR 但没有 trace_id：无请求上下文，未判定",
        )

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trace_id, row in first_by_trace.items():
        raw = getattr(row, "raw", "")
        message = _split_raw(raw)[1]
        event = getattr(row, "event", "") or "unclassified"
        interface = _interface(trace_id, raw, access_source)
        code = _code(message)
        key = (event, interface, code)
        bucket = grouped.setdefault(
            key,
            {
                "trace_id": trace_id,
                "first_ts": row.ts,
                "count": 0,
                "summary": f"{event} {code}",
            },
        )
        bucket["count"] += 1
        if row.ts < bucket["first_ts"]:
            bucket["first_ts"] = row.ts
            bucket["trace_id"] = trace_id

    ordered = sorted(grouped.items(), key=lambda item: item[1]["first_ts"])
    groups = tuple(
        ErrorGroup(
            key=key,
            trace_id=data["trace_id"],
            count=data["count"],
            first_ts=data["first_ts"],
            summary=data["summary"].strip(),
        )
        for key, data in ordered[:limit]
    )
    extra = tuple(
        ErrorGroup(
            key=key,
            trace_id=data["trace_id"],
            count=data["count"],
            first_ts=data["first_ts"],
            summary=data["summary"].strip(),
        )
        for key, data in ordered[limit:]
    )
    return LocateResult("ok", groups, extra, len(first_by_trace), "")


def _split_raw(raw: str) -> tuple[str, str]:
    """把日志行拆成 (模块位置, 消息)。"""
    parts = raw.split(" | ", 5)
    if len(parts) < 6:
        return "", ""
    tail = parts[5]
    if " - " not in tail:
        return tail.strip(), ""
    head, message = tail.split(" - ", 1)
    return head.strip(), message.strip()


def _interface(trace_id: str, raw: str, access_source: Any) -> str:
    """接口维度：优先 access 日志的 uri，取不到退回模块名。"""
    if access_source is not None:
        try:
            row = access_source.row_for_trace(trace_id, _parse_ts(raw))
        except Exception:  # noqa: BLE001  接口维度是补强，失败退回模块名
            row = None
        if row is not None and getattr(row, "uri", ""):
            return str(row.uri)
    location = _split_raw(raw)[0]
    if not location:
        return ""
    return location.split(":")[0]


def _parse_ts(raw: str) -> datetime:
    """从日志行解析时间；失败返回当前时刻（只影响 access 的匹配窗口）。"""
    head = raw.split(" | ", 1)[0].strip()
    try:
        return datetime.strptime(head, _TIME_FMT).replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


def _code(message: str) -> str:
    """从消息里取 code=/status= 的值；没有返回 '-'。"""
    match = _CODE_RE.search(message)
    return match.group(1) if match else "-"
