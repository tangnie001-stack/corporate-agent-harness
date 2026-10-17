import math

import pytest

from src.cli.eval_ragas import _score_metric_with_retry
from src.infra.llm.provider.http_observability import HookEvidence


class _Seq:
    """按序列返回分值；None 表示返回 NaN。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def measure(self, test_case):
        self.calls += 1
        v = self.seq.pop(0)
        self.score = math.nan if v is None else v
        return self.score


class _Raising:
    """每次 measure 都抛异常，异常链携带独立根因（而非返回 NaN）。"""

    def __init__(self):
        self.calls = 0

    def measure(self, test_case):
        self.calls += 1
        raise RuntimeError("outer") from TimeoutError("boom")


ROW = {"q_index": 0, "trace_id": "t0"}


def test_nan_triggers_bounded_retry_and_can_recover():
    m = _Seq([None, 0.8])
    value, err = _score_metric_with_retry(
        "faithfulness", m, object(), ROW, HookEvidence()
    )

    assert m.calls == 2, "首次非掩码 NaN 必须触发重试"
    assert value == pytest.approx(0.8) and err == ""


def test_success_does_not_retry():
    m = _Seq([0.9])
    value, err = _score_metric_with_retry(
        "faithfulness", m, object(), ROW, HookEvidence()
    )

    assert m.calls == 1 and value == pytest.approx(0.9) and err == ""


def test_exhausted_retries_report_error_and_return_nan():
    m = _Seq([None, None, None])
    value, err = _score_metric_with_retry(
        "faithfulness", m, object(), ROW, HookEvidence()
    )

    assert math.isnan(value)
    assert err.startswith("faithfulness:")
    assert m.calls == 3, "有界：RAGAS_METRIC_MAX_ATTEMPTS=3"


def test_measure_raises_chain_survives_into_error_and_retries():
    m = _Raising()
    value, err = _score_metric_with_retry(
        "faithfulness", m, object(), ROW, HookEvidence()
    )

    assert math.isnan(value)
    assert m.calls == 3, "有界：RAGAS_METRIC_MAX_ATTEMPTS=3"
    # 异常链须完整存活进错误串：外层类 + 根因类 + 根因消息，而非退化回 ":nan"
    assert "RuntimeError" in err and "TimeoutError" in err
    assert "boom" in err, "根因消息必须存活进错误串"
    assert err != "faithfulness:nan"


def test_rate_limit_retries_with_retry_after(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.cli.eval_ragas.time.sleep", lambda s: sleeps.append(s))

    ev = HookEvidence()
    ev.mark("t0")
    ev.append(
        {
            "phase": "response",
            "status": 429,
            "json_ok": True,
            "finish_reason": [],
            "retry_after": "2",
        }
    )
    m = _Seq([None, 0.8])

    value, err = _score_metric_with_retry("faithfulness", m, object(), ROW, ev)

    assert m.calls == 2, "429 不再短路，必须由外层重试"
    assert value == pytest.approx(0.8)
    assert err == ""
    assert sleeps == [2.0], "优先使用 Retry-After"


def test_diagnostic_max_attempts_one(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.cli.eval_ragas.time.sleep", lambda s: sleeps.append(s))
    m = _Seq([None, 0.8])

    value, _err = _score_metric_with_retry(
        "contextual_relevancy", m, object(), ROW, HookEvidence(), max_attempts=1
    )

    assert math.isnan(value)
    assert m.calls == 1
    assert sleeps == []
