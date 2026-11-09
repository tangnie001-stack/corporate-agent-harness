"""访问日志解析与 4xx 分类。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.rca.diagnose.access import AccessLogSource, classify_4xx


def _line(
    *,
    status: int = 200,
    upstream_status: str = "200",
    trace: str = "",
    uri: str = "/api/kbs",
) -> str:
    """按 masked 格式造一行访问日志（时间取当前 UTC，保证落在回看窗口内）。"""
    stamp = datetime.now(UTC).strftime("%d/%b/%Y:%H:%M:%S +0000")
    return (
        f'10.0.0.1 - - [{stamp}] "GET {uri} HTTP/1.1" {status} 87 '
        f'"-" "curl/8.0" upstream=172.18.0.2:8000 '
        f"upstream_status={upstream_status} trace={trace} rt=0.012\n"
    )


def _write(tmp_path: Path, *lines: str) -> AccessLogSource:
    path = tmp_path / "access.log"
    path.write_text("".join(lines), encoding="utf-8")
    return AccessLogSource(str(tmp_path))


def test_row_for_trace_parses_fields(tmp_path):
    trace = "trace_11111111-1111-1111-1111-111111111111"
    source = _write(tmp_path, _line(status=401, upstream_status="401", trace=trace))
    row = source.row_for_trace(trace, datetime.now(UTC))
    assert row is not None
    assert row.status == 401
    assert row.upstream_status == "401"
    assert row.uri == "/api/kbs"
    assert row.request_time == 0.012


def test_502_rows_have_no_trace_and_are_not_matched(tmp_path):
    # 502/504 时上游无响应头 ⇒ nginx 把 trace 写成 "-"，按 trace 永远取不到这类行
    source = _write(
        tmp_path, _line(status=502, upstream_status="-", trace="-", uri="/api/chat")
    )
    assert source.row_for_trace("-", datetime.now(UTC)) is None
    assert source.row_for_trace("trace_dead", datetime.now(UTC)) is None


def test_row_for_trace_missing_file_returns_none(tmp_path):
    assert (
        AccessLogSource(str(tmp_path / "nope")).row_for_trace(
            "trace_x", datetime.now(UTC)
        )
        is None
    )


def test_classify_4xx():
    assert classify_4xx(400) == "参数错误"
    assert classify_4xx(422) == "参数错误"
    assert classify_4xx(401) == "鉴权失败"
    assert classify_4xx(403) == "鉴权失败"
    assert classify_4xx(404) == "资源不存在"
    assert classify_4xx(413) == "请求体超限"
    assert classify_4xx(429) == "限流"
    assert classify_4xx(418) == "其他 4xx"
