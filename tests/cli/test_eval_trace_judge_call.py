"""JUDGE_CALL 汇总：多 HTTP、失败记录、reason 不复用。"""

from __future__ import annotations

import math

import pytest
from loguru import logger

from src.cli.eval_deepeval import _score_metric_with_retry
from src.infra.llm.provider.http_observability import HookEvidence


class _Metric:
    def __init__(self, value=0.9, reason="because"):
        self.value = value
        self.reason = reason
        self.calls = 0

    def measure(self, test_case):
        self.calls += 1
        return self.value


class _BadMetric:
    def __init__(self):
        self.calls = 0

    def measure(self, test_case):
        self.calls += 1
        raise RuntimeError("outer") from TimeoutError("boom")


ROW = {"q_index": 0, "trace_id": "eval_t1"}


def _capture():
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    return messages, sink_id


def test_judge_call_carries_measure_summary_and_reason_hash():
    ev = HookEvidence()
    ev.set_run("eval_run_x_abcd1234")
    messages, sink_id = _capture()
    try:
        value, err = _score_metric_with_retry(
            "faithfulness", _Metric(), object(), ROW, ev
        )
    finally:
        logger.remove(sink_id)

    assert value == pytest.approx(0.9) and err == ""
    line = next(m for m in messages if "judge call" in m)
    assert "judge_measure_id=eval_t1:faithfulness:1" in line
    assert "component=judge" in line
    assert "request_count=0" in line
    assert "statuses=[]" in line
    assert "reason_len=7" in line
    assert "reason_hash=" in line
    assert "because" not in line  # 不落完整 reason


def test_failed_measure_does_not_read_stale_reason():
    ev = HookEvidence()
    ev.set_run("eval_run_x_abcd1234")
    metric = _BadMetric()
    messages, sink_id = _capture()
    try:
        value, err = _score_metric_with_retry("faithfulness", metric, object(), ROW, ev)
    finally:
        logger.remove(sink_id)

    assert math.isnan(value)
    assert "TimeoutError" in err
    lines = [m for m in messages if "judge call" in m]
    assert lines
    assert all("reason_len=null" in m for m in lines)
    assert all("reason_hash=null" in m for m in lines)


def test_evidence_none_is_noop():
    value, err = _score_metric_with_retry(
        "faithfulness", _Metric(), object(), ROW, None
    )
    assert value == pytest.approx(0.9) and err == ""


def test_row_without_trace_id_does_not_keyerror():
    ev = HookEvidence()
    value, err = _score_metric_with_retry(
        "faithfulness", _Metric(), object(), {"q_index": 0}, ev
    )
    assert value == pytest.approx(0.9) and err == ""
