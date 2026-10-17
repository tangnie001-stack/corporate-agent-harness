"""`_exc_chain_summary` 与重试边界（attempts=1 退化为不重试）的收口测试。

重试主语义（NaN 触发有界重试、成功不重试、耗尽记 error、429 跳过外层重试）
已由 `tests/cli/test_eval_bridge_retry.py` 覆盖，此处不重复。
"""

import math

from src.cli import eval_deepeval


class _Nan:
    """恒返回 NaN 的假指标，并计数 `measure` 调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    def measure(self, test_case):
        self.calls += 1
        return math.nan


def test_exc_chain_summary_keeps_root_cause():
    """只记类名会丢根因（实测 150s 超时被包成无消息 RuntimeError）——须带根因类与消息。"""
    try:
        try:
            raise TimeoutError("Request timed out.")
        except TimeoutError as inner:
            raise RuntimeError("") from inner
    except RuntimeError as exc:
        summary = eval_deepeval._exc_chain_summary(exc)
        assert summary.startswith("RuntimeError<-TimeoutError:")
        assert "timed out" in summary

    assert (
        eval_deepeval._exc_chain_summary(ValueError("bad shape"))
        == "ValueError: bad shape"
    )


def test_no_retry_when_attempts_is_one(monkeypatch):
    """attempts=1 时退化为「不重试」，只 measure 一次并记 error。"""
    monkeypatch.setattr(eval_deepeval.settings, "EVAL_METRIC_MAX_ATTEMPTS", 1)
    metric = _Nan()

    value, err = eval_deepeval._score_metric_with_retry(
        "faithfulness", metric, object(), {"q_index": 0, "trace_id": "t0"}
    )

    assert metric.calls == 1
    assert math.isnan(value)
    assert err.startswith("faithfulness:")
