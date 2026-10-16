"""kb_eval 响应须含四项指标、pass_threshold，且不含 answer_correctness。"""

from src.api import kb_eval
from src.config.const import EVAL_PASS_THRESHOLD


def _report():
    """构造评估报告实体字段（answer_correctness 已退出评测，故不含该字段）。"""
    return {
        "eval_date": "2026-10-14",
        "faithfulness": 0.9,
        "answer_relevancy": 0.9,
        "context_precision": 0.9,
        "context_recall": 0.9,
        "overall_score": 0.86,
        "passed": True,
        "qa_count": 20,
        "run_type": "manual",
        "detail_json": None,
    }


def test_eval_payload_omits_answer_correctness():
    """answer_correctness 已退出评测，接口响应不得下发该键。"""
    payload = kb_eval._build_eval_payload(_report())

    assert "answer_correctness" not in payload
    for key in (
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ):
        assert key in payload


def test_eval_payload_exposes_threshold():
    """pass_threshold 仍由常量 EVAL_PASS_THRESHOLD 下发。"""
    payload = kb_eval._build_eval_payload(_report())

    assert payload["pass_threshold"] == EVAL_PASS_THRESHOLD
