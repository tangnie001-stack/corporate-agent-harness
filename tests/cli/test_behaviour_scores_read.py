from __future__ import annotations

import json

import pytest

from src.cli.eval_scoring import read_behaviour_scores

_FIXTURE = {
    "testCases": [
        {
            "order": 0,
            "input": "Q0",
            "metricsData": [
                {"name": "Task Completion", "score": 1.0, "success": True},
                {"name": "Step Efficiency", "score": 0.5, "success": True},
            ],
        },
        {
            "order": 1,
            "input": "Q1",
            "metricsData": [
                {"name": "Task Completion", "score": 0.0, "success": False},
            ],
        },
    ]
}


def test_reads_per_question_scores_in_order(tmp_path):
    (tmp_path / "test_run_20261017_000000.json").write_text(
        json.dumps(_FIXTURE), encoding="utf-8"
    )

    rows = read_behaviour_scores(str(tmp_path))

    assert [r["q_index"] for r in rows] == [0, 1]
    assert [r["input"] for r in rows] == ["Q0", "Q1"]
    assert rows[0]["metrics"]["Task Completion"] == 1.0
    assert rows[1]["metrics"]["Task Completion"] == 0.0


def test_picks_the_newest_test_run_file(tmp_path):
    (tmp_path / "test_run_20260101_000000.json").write_text(
        json.dumps({"testCases": [{"order": 0, "input": "OLD", "metricsData": []}]}),
        encoding="utf-8",
    )
    (tmp_path / "test_run_20261017_000000.json").write_text(
        json.dumps(_FIXTURE), encoding="utf-8"
    )

    rows = read_behaviour_scores(str(tmp_path))

    assert rows[0]["input"] == "Q0"


def test_missing_json_fails_loud(tmp_path):
    with pytest.raises(RuntimeError, match="test_run"):
        read_behaviour_scores(str(tmp_path))


def test_read_behaviour_scores_carries_reason(tmp_path):
    """逐题失败证据由 test_run JSON 的 metricsData[].reason 承载（design R9）。"""
    payload = {
        "testCases": [
            {
                "order": 0,
                "input": "Q1",
                "metricsData": [
                    {
                        "name": "Task Completion",
                        "score": 1.0,
                        "success": True,
                        "reason": "ok",
                    },
                    {
                        "name": "Step Efficiency",
                        "score": None,
                        "success": False,
                        "reason": "no trace",
                    },
                ],
            }
        ]
    }
    (tmp_path / "test_run_20260101.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    rows = read_behaviour_scores(str(tmp_path))
    assert rows[0]["metrics"] == {"Task Completion": 1.0, "Step Efficiency": None}
    assert rows[0]["reasons"]["Step Efficiency"] == "no trace"


def test_shuffled_cases_are_sorted_and_q_index_is_order(tmp_path):
    payload = {
        "testCases": [
            {"order": 1, "input": "Q1", "metricsData": []},
            {"order": 0, "input": "Q0", "metricsData": []},
        ]
    }
    (tmp_path / "test_run_20261019.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    rows = read_behaviour_scores(str(tmp_path))
    assert [r["q_index"] for r in rows] == [0, 1]
    assert [r["order"] for r in rows] == [0, 1]
    assert [r["input"] for r in rows] == ["Q0", "Q1"]


@pytest.mark.parametrize(
    "payload",
    [
        {"testCases": [{"input": "Q0", "metricsData": []}]},
        {"testCases": [{"order": 2, "input": "Q0", "metricsData": []}]},
        {
            "testCases": [
                {"order": 0, "input": "Q0", "metricsData": []},
                {"order": 0, "input": "Q1", "metricsData": []},
            ]
        },
    ],
)
def test_invalid_order_fails_loud(tmp_path, payload):
    (tmp_path / "test_run_20261019.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="order"):
        read_behaviour_scores(str(tmp_path))
