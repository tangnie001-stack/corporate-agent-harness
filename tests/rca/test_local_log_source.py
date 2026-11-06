"""LocalLogSource：按时间窗从应用日志取错误行与 trace_id。"""

from datetime import UTC, datetime

from src.rca.diagnose.logs import LocalLogSource

TID = "trace_12345678-1234-1234-1234-123456789abc"
LINE = f"2026-11-06 03:12:41.500 | ERROR   | {TID:36} | s1 | web | src.api.chat:h - [app] biz error code=E1\n"
INFO = (
    "2026-11-06 03:12:40.000 | INFO    | "
    + f"{TID:36}"
    + " | s1 | web | src.api.chat:h - [agent] model turn model=q\n"
)


def test_extracts_only_error_lines_within_window(tmp_path):
    (tmp_path / "app_2026-11-06.log").write_text(LINE + INFO, encoding="utf-8")
    src = LocalLogSource(str(tmp_path))
    rows = src.errors_between(
        datetime(2026, 11, 6, 3, 0, tzinfo=UTC),
        datetime(2026, 11, 6, 4, 0, tzinfo=UTC),
    )
    assert len(rows) == 1
    assert rows[0].trace_id == TID
    assert rows[0].event == "biz error"
    assert rows[0].level == "ERROR"


def test_skips_lines_outside_window(tmp_path):
    (tmp_path / "app_2026-11-06.log").write_text(LINE, encoding="utf-8")
    src = LocalLogSource(str(tmp_path))
    rows = src.errors_between(
        datetime(2026, 11, 6, 5, 0, tzinfo=UTC),
        datetime(2026, 11, 6, 6, 0, tzinfo=UTC),
    )
    assert rows == []


def test_missing_dir_returns_empty(tmp_path):
    src = LocalLogSource(str(tmp_path / "nope"))
    rows = src.errors_between(
        datetime(2026, 11, 6, 3, 0, tzinfo=UTC),
        datetime(2026, 11, 6, 4, 0, tzinfo=UTC),
    )
    assert rows == []


def test_invalid_trace_id_kept_as_empty(tmp_path):
    bad = "2026-11-06 03:12:41.500 | ERROR   | " + " " * 36
    bad += " | s1 | web | src.api.chat:h - [app] biz error code=E2\n"
    (tmp_path / "app_2026-11-06.log").write_text(bad, encoding="utf-8")
    rows = LocalLogSource(str(tmp_path)).errors_between(
        datetime(2026, 11, 6, 3, 0, tzinfo=UTC),
        datetime(2026, 11, 6, 4, 0, tzinfo=UTC),
    )
    assert rows and rows[0].trace_id == ""
