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


def test_trajectory_count_mismatch_fails_loud():
    """轨迹数偏短 ⇒ 循环体一次都不跑，会静默通过 —— 必须显式拦（评审 Important）。"""
    with pytest.raises(RuntimeError, match="轨迹"):
        assert_behaviour_scores_complete(["Q0", "Q1"], _rows(2), _traj(1))


def test_failed_trajectory_is_exempt():
    rows = [{"q_index": 0, "input": "Q0", "metrics": {}}]
    assert_behaviour_scores_complete(
        ["Q0"],
        rows,
        [{"tool_calls": [], "iterations": 0, "failed": True}],
    )


def test_failed_trajectory_with_other_row_still_checks_the_rest():
    rows = [
        {"q_index": 0, "input": "Q0", "metrics": {}},
        {"q_index": 1, "input": "Q1", "metrics": {"Task Completion": 1.0}},
    ]
    trajectories = [
        {"tool_calls": [], "iterations": 0, "failed": True},
        {"tool_calls": ["lookup"], "iterations": 2},
    ]
    assert_behaviour_scores_complete(["Q0", "Q1"], rows, trajectories)


def test_fill_missing_failed_row():
    from src.cli.eval_scoring import fill_missing_behaviour_rows

    rows = fill_missing_behaviour_rows(
        [],
        ["Q0"],
        [{"tool_calls": [], "iterations": 0, "failed": True}],
    )
    assert rows == [{"q_index": 0, "input": "Q0", "metrics": {}, "reasons": {}}]


def test_non_failed_empty_trajectory_still_fails_loud():
    with pytest.raises(RuntimeError, match="轨迹"):
        assert_behaviour_scores_complete(
            ["Q0"],
            [{"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}}],
            [{"tool_calls": [], "iterations": 0}],
        )


def test_fill_missing_non_failed_row_fails_loud():
    """非 failed 题缺行时 fill 必须直接抛错，不得静默补空行。"""
    from src.cli.eval_scoring import fill_missing_behaviour_rows

    with pytest.raises(RuntimeError, match="第 0 题缺少行为分行"):
        fill_missing_behaviour_rows(
            [],
            ["Q0"],
            [{"tool_calls": ["x"], "iterations": 1}],
        )


def test_is_failed_run_detects_three_failure_forms():
    """三种失败形态统一：failed 标记 / [ERROR] 占位 / 空回答与纯空白。"""
    from src.cli.eval_scoring import is_failed_run

    assert is_failed_run("任意回答", {"failed": True})
    assert is_failed_run("[ERROR] 模拟错误", {"tool_calls": [], "iterations": 0})
    assert is_failed_run("", {"tool_calls": [], "iterations": 0})
    assert is_failed_run(" \n\t ", {"tool_calls": [], "iterations": 0})
    assert is_failed_run(None, None)


def test_is_failed_run_accepts_successful_run():
    from src.cli.eval_scoring import is_failed_run

    assert not is_failed_run("正常回答", {"tool_calls": ["lookup"], "iterations": 2})
    assert not is_failed_run("正常回答", None)
