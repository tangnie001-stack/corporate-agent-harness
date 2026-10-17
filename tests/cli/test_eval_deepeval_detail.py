import pandas as pd

from src.cli.eval_ragas import _build_eval_detail


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
