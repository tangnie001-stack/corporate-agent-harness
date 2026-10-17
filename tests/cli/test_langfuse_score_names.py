"""Langfuse 回写口径：新 score 名带后缀、行为轴一并回写、NaN 不写（design D9）。"""

import math

import pandas as pd

from src.cli import eval_deepeval
from src.cli.eval_scoring import write_scores
from src.config.const import LANGFUSE_SCORE_SUFFIX


def _df_with_one_row():
    return pd.DataFrame(
        {
            "faithfulness": [0.9],
            "answer_relevancy": [0.8],
            "context_precision": [0.7],
            "context_recall": [0.6],
            "sample_class": ["pure_kb"],
        }
    )


def test_scores_carry_caliber_suffix_and_include_behaviour(monkeypatch):
    captured: dict = {}

    def _fake_write(trace_ids, scores, enabled):
        captured["trace_ids"] = trace_ids
        captured["scores"] = scores
        captured["enabled"] = enabled

    monkeypatch.setattr(eval_deepeval, "write_scores", _fake_write)
    monkeypatch.setattr(eval_deepeval.settings, "LANGFUSE_ENABLE", True)

    behaviour_rows = [{"q_index": 0, "input": "q", "metrics": {"Task Completion": 1.0}}]
    eval_deepeval._write_langfuse_scores(_df_with_one_row(), ["t0"], behaviour_rows)

    names = set(captured["scores"])
    assert f"faithfulness{LANGFUSE_SCORE_SUFFIX}" in names
    assert f"Task Completion{LANGFUSE_SCORE_SUFFIX}" in names
    # 裸名不出现（否则会覆盖历史同名 score）
    assert "faithfulness" not in names
    assert "Task Completion" not in names
    assert captured["trace_ids"] == ["t0"]


def test_behaviour_row_count_must_match_trace_ids(monkeypatch):
    """行为轴行数与 trace_ids 不齐即 fail-loud（防分数写到别的 trace）。"""
    monkeypatch.setattr(eval_deepeval, "write_scores", lambda *a, **k: None)
    monkeypatch.setattr(eval_deepeval.settings, "LANGFUSE_ENABLE", True)

    import pytest

    with pytest.raises(RuntimeError):
        eval_deepeval._write_langfuse_scores(
            _df_with_one_row(), ["t0", "t1"], [{"q_index": 0, "metrics": {}}]
        )


def test_nan_is_not_written_even_under_suffixed_name():
    """NaN 维持「缺失」语义：不写 score、也不写 0（后缀名不改变该语义）。"""
    from unittest.mock import patch

    # 客户端是只读 property，故 patch 其所属类的同名属性（同 test_eval_scoring.py）
    with patch(
        "langfuse.decorators.langfuse_decorator.LangfuseDecorator.client_instance"
    ) as ci:
        write_scores(
            ["t0"],
            {f"faithfulness{LANGFUSE_SCORE_SUFFIX}": [math.nan]},
            enabled=True,
        )
    ci.score.assert_not_called()
