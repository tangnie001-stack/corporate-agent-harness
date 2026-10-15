"""trace_logs：按 trace_id 拉日志（含 judge call 的原始模型响应）。"""

from src.cli import trace_logs


def test_iter_trace_lines_filters_by_substring(tmp_path):
    (tmp_path / "app_2026-10-15.log").write_text(
        "a eval_abc123 judge call metric=faithfulness\n"
        "b other line\n"
        "c eval_abc123 metric retry\n",
        encoding="utf-8",
    )
    (tmp_path / "error_2026-10-15.log").write_text(
        "d eval_abc123 boom\n", encoding="utf-8"
    )

    lines = trace_logs.iter_trace_lines("eval_abc123", str(tmp_path))

    assert len(lines) == 3
    assert any("judge call" in line for line in lines)
    assert all("eval_abc123" in line for line in lines)


def test_iter_trace_lines_missing_dir_returns_empty(tmp_path):
    assert trace_logs.iter_trace_lines("nope", str(tmp_path / "absent")) == []
