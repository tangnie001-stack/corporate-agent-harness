import pandas as pd

from src.cli.eval_deepeval import (
    _build_eval_detail,
    _build_markdown_table,
    save_markdown_report,
)
from src.config.const import (
    EVAL_RELEVANCY_MAX_CONTEXTS,
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
)


def test_detail_includes_tool_correctness_score_reason_and_null_na():
    df = pd.DataFrame(
        {
            "faithfulness": [0.8, 0.7],
            "answer_relevancy": [0.7, 0.6],
            "context_precision": [0.6, 0.5],
            "context_recall": [0.5, 0.4],
            "contextual_relevancy": [0.4, float("nan")],
            "tool_correctness": [1.0, float("nan")],
            "tool_correctness_reason": ["", "failed"],
            "error": ["", "sample_class=empty"],
        }
    )

    detail, _ = _build_eval_detail(df, 2, None)

    assert detail[0]["tool_correctness"] == 1.0
    assert detail[0]["tool_correctness_reason"] == ""
    assert detail[1]["tool_correctness"] is None
    assert detail[1]["tool_correctness_reason"] == "failed"


def test_report_tool_section_counts_and_fully_matched(tmp_path):
    from unittest.mock import MagicMock

    from src.cli.eval_deepeval import save_markdown_report

    df = pd.DataFrame(
        {
            "faithfulness": [0.9, 0.8, 0.7],
            "answer_relevancy": [0.9, 0.8, 0.7],
            "context_precision": [0.9, 0.8, 0.7],
            "context_recall": [0.9, 0.8, 0.7],
            "contextual_relevancy": [0.5, 0.5, 0.5],
            "tool_correctness": [1.0, 0.0, float("nan")],
            "tool_correctness_reason": ["", "missing search_web", ""],
        }
    )
    mock_result = MagicMock()
    mock_result.to_pandas.return_value = df
    csv_path = str(tmp_path / "report.csv")

    save_markdown_report(
        mock_result,
        ["Q1", "Q2", "Q3"],
        csv_path,
        sample_classes=["pure_kb", "pure_kb", "pure_kb"],
    )

    content = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "工具选择" in content
    assert "Fully matched:** 1/2" in content
    assert "no_label=1" in content
    assert "failed=0" in content
    assert "metric_error=0" in content
    assert "scored=2" in content
    assert "missing search_web" in content
    main_line = next(
        line for line in content.splitlines() if line.startswith("**Main scores")
    )
    assert "tool_correctness" not in main_line


def test_report_diagnostic_section_uses_const_and_counts_nan(tmp_path):
    """诊断段用常量渲染截断条数、单独统计 pure-KB NaN，且不进主分行。"""
    from unittest.mock import MagicMock

    df = pd.DataFrame(
        {
            "faithfulness": [0.9, 0.4, 0.3],
            "answer_relevancy": [0.8, 0.7, 0.6],
            "context_precision": [0.7, 0.5, 0.4],
            "context_recall": [0.6, 0.4, 0.2],
            "contextual_relevancy": [0.5, float("nan"), 0.2],
        }
    )
    mock_result = MagicMock()
    mock_result.to_pandas.return_value = df

    csv_path = str(tmp_path / "report.csv")
    save_markdown_report(
        mock_result,
        ["Q1", "Q2", "Q3"],
        csv_path,
        sample_classes=[
            SAMPLE_CLASS_PURE_KB,
            SAMPLE_CLASS_PURE_KB,
            SAMPLE_CLASS_EMPTY,
        ],
    )
    md_path = csv_path.rsplit(".", 1)[0] + ".md"
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 截断条数从常量渲染，不写死字面量
    assert (
        f"> ContextualRelevancy 仅判前 {EVAL_RELEVANCY_MAX_CONTEXTS} 条 context"
        in content
    )
    # 纯 KB 两行：contextual_relevancy 一例 NaN
    assert "**Diagnostic NaN (pure-KB):** contextual_relevancy=1/2" in content
    # 诊断不进主分行
    main_line = next(
        line
        for line in content.splitlines()
        if line.startswith("**Main scores (pure-KB):**")
    )
    assert "contextual_relevancy" not in main_line


def test_detail_entries_carry_testset_version():
    import pandas as pd

    from src.cli.eval_deepeval import _build_eval_detail

    df = pd.DataFrame(
        [
            {
                "faithfulness": 1.0,
                "answer_relevancy": 1.0,
                "context_precision": 1.0,
                "context_recall": 1.0,
                "contextual_relevancy": 1.0,
            }
        ]
    )
    detail, _ = _build_eval_detail(df, 1, None, testset_version=6)
    assert detail[0]["testset_version"] == 6


def test_markdown_table_escapes_pipe_in_error_cell():
    """error 文本含 "|" / 换行时归一化为 "/" 与单空格，避免破坏 Markdown 列。"""
    df = pd.DataFrame(
        {
            "faithfulness": [0.5],
            "error": ["faithfulness: bad | timeout\nretry"],
        }
    )

    lines = _build_markdown_table(df, ["faithfulness"], None, None)
    row = lines[2]

    assert "bad / timeout retry" in row
    assert "bad | timeout" not in row
    # 表格列数不被 error 内容里的 "|" 改变：头部 3 列 -> 4 个分隔符
    assert row.count("|") == 4
