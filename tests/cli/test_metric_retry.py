"""单指标有界重试：可重试失败重试 1 次；超时类不重试（design D2 / D6）。"""

import math

import pytest

from src.cli import eval_ragas


class _Res:
    def __init__(self, value: float) -> None:
        self.value = value


async def _run(monkeypatch, fail_times: int, exc: Exception):
    """构造「前 fail_times 次抛 exc、之后成功」的假打分函数并调用被测函数。"""
    calls = {"n": 0}

    async def fake_score(name, metric, row):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise exc
        return _Res(0.75)

    monkeypatch.setattr(eval_ragas, "_score_metrics", fake_score)
    value, err = await eval_ragas._score_metric_with_retry(
        "faithfulness", object(), {"q_index": 1}
    )
    return value, err, calls["n"]


@pytest.mark.asyncio
async def test_retries_once_then_succeeds(monkeypatch):
    """可重试失败 → 重试 1 次成功后返回真值，且 error 为空。"""
    value, err, n = await _run(monkeypatch, fail_times=1, exc=ValueError("boom"))

    assert value == 0.75
    assert err == ""
    assert n == 2


@pytest.mark.asyncio
async def test_timeout_is_not_retried(monkeypatch):
    """超时类不重试：只尝试 1 次，误差如实记录。"""
    value, err, n = await _run(
        monkeypatch, fail_times=99, exc=RuntimeError("Request timed out.")
    )

    assert n == 1
    assert math.isnan(value)
    assert err.startswith("faithfulness:RuntimeError:")
    assert "timed out" in err


@pytest.mark.asyncio
async def test_retry_exhausted_reports_failure(monkeypatch):
    """非超时失败重试到上限仍失败 → 记 error（attempts=2 ⇒ 共 2 次）。"""
    value, err, n = await _run(monkeypatch, fail_times=99, exc=ValueError("bad shape"))

    assert n == eval_ragas.settings.RAGAS_METRIC_MAX_ATTEMPTS
    assert math.isnan(value)
    assert err.startswith("faithfulness:ValueError:")


@pytest.mark.asyncio
async def test_no_retry_when_attempts_is_one(monkeypatch):
    """attempts=1 时退化为「不重试」，只尝试 1 次。"""
    monkeypatch.setattr(eval_ragas.settings, "RAGAS_METRIC_MAX_ATTEMPTS", 1)
    value, err, n = await _run(monkeypatch, fail_times=99, exc=ValueError("boom"))

    assert n == 1
    assert math.isnan(value)
    assert err.startswith("faithfulness:ValueError:")


def test_is_timeout_error_detects_wrapped_timeout():
    """被包装（__cause__）的超时异常也要识别出来。"""
    try:
        try:
            raise TimeoutError("Request timed out.")
        except TimeoutError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as exc:
        assert eval_ragas._is_timeout_error(exc) is True

    assert eval_ragas._is_timeout_error(ValueError("bad shape")) is False
