"""kb_eval 响应须含 answer_correctness（detail_json 均值）与 pass_threshold。"""

import json

import pytest

from src.api import kb_eval
from src.config.const import EVAL_PASS_THRESHOLD


def _report(detail_json):
    """构造评估报告实体字段（detail_json 为落库的逐题明细 JSON 字符串）。"""
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
        "detail_json": detail_json,
    }


def test_eval_payload_derives_correctness_from_detail_and_exposes_threshold():
    """answer_correctness 由逐题明细求均值（跳过 NaN / 缺键），并下发阈值。"""
    detail = json.dumps(
        [
            {"q_index": 0, "answer_correctness": 0.9},
            {"q_index": 1, "answer_correctness": 0.5},
            {"q_index": 2, "answer_correctness": float("nan")},  # 掩码/失败 → 跳过
            {"q_index": 3},  # 缺键 → 跳过
        ]
    )

    payload = kb_eval._build_eval_payload(_report(detail))

    assert payload["answer_correctness"] == pytest.approx(0.7)
    assert payload["pass_threshold"] == EVAL_PASS_THRESHOLD


def test_eval_payload_correctness_none_when_nothing_to_average():
    """不可解析 / 空 / 无该指标值时，answer_correctness 为 None。"""
    assert (
        kb_eval._build_eval_payload(_report("{ not json"))["answer_correctness"] is None
    )
    assert kb_eval._build_eval_payload(_report("[]"))["answer_correctness"] is None
    only_nan = json.dumps([{"q_index": 0, "answer_correctness": float("nan")}])
    assert kb_eval._build_eval_payload(_report(only_nan))["answer_correctness"] is None
    assert kb_eval._build_eval_payload(_report(None))["answer_correctness"] is None
