import pandas as pd

from src.cli.eval_ragas import _build_eval_detail, save_markdown_report
from src.config.const import (
    EVAL_RELEVANCY_MAX_CONTEXTS,
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
)


def test_detail_includes_diagnostics_error_and_null_tool_correctness():
    """detail_json 逐题条目含两项诊断分、恒 None 的 tool_correctness 与 error。"""
    df = pd.DataFrame(
        {
            "faithfulness": [0.8],
            "answer_relevancy": [0.7],
            "context_precision": [0.6],
            "context_recall": [0.5],
            "contextual_relevancy": [0.4],
            "non_advice": [1.0],
            "error": ["faithfulness:timeout"],
        }
    )

    detail, _ = _build_eval_detail(df, 1, None)

    assert detail[0]["contextual_relevancy"] == 0.4
    assert detail[0]["non_advice"] == 1.0
    assert detail[0]["tool_correctness"] is None
    assert detail[0]["error"] == "faithfulness:timeout"


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
            "non_advice": [1.0, 1.0, 0.0],
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
    # 纯 KB 两行：contextual_relevancy 一例 NaN、non_advice 无 NaN
    assert (
        "**Diagnostic NaN (pure-KB):** contextual_relevancy=1/2, non_advice=0/2"
        in content
    )
    # 诊断不进主分行
    main_line = next(
        line
        for line in content.splitlines()
        if line.startswith("**Main scores (pure-KB):**")
    )
    assert "contextual_relevancy" not in main_line
    assert "non_advice" not in main_line
