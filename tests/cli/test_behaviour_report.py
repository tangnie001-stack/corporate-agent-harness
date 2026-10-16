"""报告含行为段，且行为分不参与主分。"""

from __future__ import annotations

import pandas as pd

from src.cli.eval_ragas import save_markdown_report


class _FakeResult:
    """`save_markdown_report` 只要求 `result.to_pandas()`（`eval_ragas.py:620`）。"""

    def to_pandas(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"q_index": 0, "faithfulness": 1.0, "sample_class": "pure_kb"}]
        )


def test_report_contains_behaviour_section(tmp_path):
    out = tmp_path / "r.csv"

    save_markdown_report(
        _FakeResult(),
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
