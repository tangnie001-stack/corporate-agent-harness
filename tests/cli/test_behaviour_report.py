"""报告含行为段，且行为分不参与主分。"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from src.cli.eval_deepeval import save_markdown_report


class _FakeResult:
    """`save_markdown_report` 只要求 `result.to_pandas()`（`eval_deepeval.py:620`）。"""

    def to_pandas(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"q_index": 0, "faithfulness": 1.0, "sample_class": "pure_kb"}]
        )


def test_report_contains_behaviour_section(tmp_path):
    out = tmp_path / "r.csv"

    save_markdown_report(
        cast(Any, _FakeResult()),
        questions=["Q0"],
        output_path=str(out),
        sample_classes=["pure_kb"],
        behaviour_rows=[
            {"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}}
        ],
    )

    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "Agent 行为轴" in text
    assert "Task Completion" in text


def test_behaviour_section_shows_reason_for_missing_metric():
    from src.cli.eval_deepeval import _build_behaviour_section

    rows = [
        {
            "q_index": 0,
            "input": "Q1",
            "metrics": {"Task Completion": 1.0, "Step Efficiency": None},
            "reasons": {"Step Efficiency": "no trace"},
        }
    ]
    lines = _build_behaviour_section(rows)
    body = "\n".join(lines)
    assert "1.0000" in body
    assert "N/A（no trace）" in body


def test_behaviour_section_collapses_newlines_in_reason():
    from src.cli.eval_deepeval import _build_behaviour_section

    rows = [
        {
            "q_index": 0,
            "input": "Q1",
            "metrics": {"Task Completion": 1.0, "Step Efficiency": None},
            "reasons": {"Step Efficiency": "a\nb"},
        }
    ]
    lines = _build_behaviour_section(rows)
    reason_lines = [line for line in lines if "N/A（a b）" in line]
    assert len(reason_lines) == 1
    assert "N/A（a b）" in reason_lines[0]
    assert "\n" not in reason_lines[0]
