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


def test_behaviour_section_renders_failed_row_as_na():
    """failed 行整行记 N/A（failed），同表正常行仍渲染分数/理由。"""
    from src.cli.eval_deepeval import _build_behaviour_section

    rows = [
        {"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}},
        {
            "q_index": 1,
            "input": "Q1",
            "metrics": {"Task Completion": None, "Step Efficiency": None},
            "reasons": {"Task Completion": "empty answer"},
        },
    ]
    trajectories = [
        {"tool_calls": ["lookup"], "iterations": 2},
        {"tool_calls": [], "iterations": 0, "failed": True},
    ]
    lines = _build_behaviour_section(rows, trajectories)

    failed_lines = [line for line in lines if "N/A（failed）" in line]
    assert len(failed_lines) == 1
    # 每个指标列都是 N/A（failed），且不是裁判理由（empty answer）
    assert failed_lines[0] == "| Q1 | N/A（failed） | N/A（failed） |"
    assert "empty answer" not in failed_lines[0]

    ok_lines = [line for line in lines if line.startswith("| Q0 |")]
    assert len(ok_lines) == 1
    assert "1.0000" in ok_lines[0]


class _TwoRowFakeResult:
    """两题结果，用于覆盖 `save_markdown_report(..., trajectories=...)` 调用链。"""

    def to_pandas(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"q_index": 0, "faithfulness": 1.0, "sample_class": "pure_kb"},
                {"q_index": 1, "faithfulness": 0.5, "sample_class": "pure_kb"},
            ]
        )


def test_report_renders_failed_row_as_na(tmp_path):
    out = tmp_path / "r.csv"

    save_markdown_report(
        cast(Any, _TwoRowFakeResult()),
        questions=["Q0", "Q1"],
        output_path=str(out),
        sample_classes=["pure_kb", "pure_kb"],
        trajectories=[
            {"tool_calls": ["lookup"], "iterations": 2},
            {"tool_calls": [], "iterations": 0, "failed": True},
        ],
        behaviour_rows=[
            {"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}},
            {
                "q_index": 1,
                "input": "Q1",
                "metrics": {"Task Completion": 0.0},
            },
        ],
    )

    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "| Q1 | N/A（failed） |" in text
    # 失败题的行为分不得写成 0
    assert "| Q1 | 0.0000 |" not in text
    assert "| Q0 | 1.0000 |" in text
