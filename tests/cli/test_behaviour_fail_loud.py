from __future__ import annotations

import pytest

from src.cli.eval_scoring import assert_behaviour_scores_complete


def _rows(n: int):
    return [
        {"q_index": i, "input": f"Q{i}", "metrics": {"Task Completion": 1.0}}
        for i in range(n)
    ]


def _traj(n: int):
    return [{"tool_calls": ["lookup"], "iterations": 2} for _ in range(n)]


def test_all_good_passes():
    assert_behaviour_scores_complete(["Q0"], _rows(1), _traj(1))


def test_missing_score_row_fails_loud():
    with pytest.raises(RuntimeError, match="行为分"):
        assert_behaviour_scores_complete(["Q0", "Q1"], _rows(1), _traj(2))


def test_empty_trajectory_fails_loud():
    with pytest.raises(RuntimeError, match="轨迹"):
        assert_behaviour_scores_complete(
            ["Q0"], _rows(1), [{"tool_calls": [], "iterations": 0}]
        )


def test_no_metric_score_at_all_fails_loud():
    rows = [{"q_index": 0, "input": "Q0", "metrics": {}}]
    with pytest.raises(RuntimeError, match="行为分"):
        assert_behaviour_scores_complete(["Q0"], rows, _traj(1))
