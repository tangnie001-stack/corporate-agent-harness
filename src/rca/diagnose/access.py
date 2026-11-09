"""nginx 访问日志证据源：按 trace 取状态码与耗时。

日志行由 deploy/nginx/nginx.conf 的 masked 格式产生，末尾四个字段顺序固定：
    upstream=<addr> upstream_status=<code|-> trace=<X-Trace-ID|-> rt=<秒>
nginx 把空变量写成 "-"，解析时归一为空串。上游无响应（502/504 / 连接失败）
时 upstream_status 与 trace 均为空 —— 这类行按 trace 取不到，属定位器
（应用日志无该 trace）的判定范围，不在本模块。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# 只回看 trace 前后一小时；访问日志是单文件，不能无限扫
_LOOKBACK = timedelta(hours=1)
_LINE_RE = re.compile(
    r'"[A-Z]+ (?P<uri>\S+) HTTP/[\d.]+" (?P<status>\d{3}) \d+ "[^"]*" "[^"]*" '
    r"upstream=(?P<upstream>\S*) upstream_status=(?P<upstream_status>\S*) "
    r"trace=(?P<trace>\S*) rt=(?P<rt>[\d.]+)"
)
_TIME_RE = re.compile(r"\[(?P<ts>[^\]]+)]")


@dataclass(frozen=True)
class AccessRow:
    """一条访问日志事实（只保留诊断用得到的字段）。"""

    ts: datetime  # 请求时间（按日志里的本地时区解析）
    method: str  # HTTP 方法
    uri: str  # 已脱敏的请求路径（query 可能被掩）
    status: int  # 客户端看到的状态码
    upstream_status: str  # 上游状态码；空串 = 上游无响应
    request_time: float  # 请求总耗时（秒）
    trace_id: str  # 上游返回的 X-Trace-ID；空串 = 无响应头


class AccessLogSource:
    """读 nginx 访问日志；文件缺失等同"没有 access 证据"。

    只按 trace 匹配：502/504 的行没有 trace，取不到属于预期行为。
    """

    def __init__(self, log_dir: str) -> None:
        """Args:
        log_dir: 访问日志目录（生产 /data/nginx-logs，测试注入临时目录）；空串表示未启用。
        """
        self._dir = Path(log_dir) if log_dir else None

    def row_for_trace(self, trace_id: str, at: datetime) -> AccessRow | None:
        """返回该 trace 在 at 附近最近的一条访问记录；取不到返回 None。

        Args:
            trace_id: 目标 trace（空串直接返回 None）
            at: trace 发生时刻（用于限定扫描窗口）

        Returns:
            最近的 AccessRow；文件缺失、无匹配或解析失败返回 None
        """
        if self._dir is None or not trace_id:
            return None
        path = self._dir / "access.log"
        if not path.is_file():
            return None
        best: AccessRow | None = None
        start = at - _LOOKBACK
        end = at + _LOOKBACK
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                match = _LINE_RE.search(line)
                if match is None or _norm(match.group("trace")) != trace_id:
                    continue
                ts = _parse_time(line)
                if ts is None or ts < start or ts > end:
                    continue
                row = AccessRow(
                    ts=ts,
                    method=line.split('"', 2)[1].split(" ", 1)[0],
                    uri=match.group("uri"),
                    status=int(match.group("status")),
                    upstream_status=_norm(match.group("upstream_status")),
                    request_time=float(match.group("rt")),
                    trace_id=_norm(match.group("trace")),
                )
                if best is None or abs(row.ts - at) < abs(best.ts - at):
                    best = row
        return best


def _norm(value: str) -> str:
    """把 nginx 的空值占位 '-' 归一为空串。"""
    if value == "-":
        return ""
    return value


def classify_4xx(status: int) -> str:
    """把 4xx 状态码映射成报告用的分类文案。"""
    if status in (400, 422):
        return "参数错误"
    if status in (401, 403):
        return "鉴权失败"
    if status == 404:
        return "资源不存在"
    if status == 413:
        return "请求体超限"
    if status == 429:
        return "限流"
    return "其他 4xx"


def _parse_time(line: str) -> datetime | None:
    """解析日志里的 [09/Nov/2026:09:40:13 +0800]；失败返回 None。"""
    match = _TIME_RE.search(line)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group("ts"), "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
