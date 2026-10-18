"""JUDGE_CALL 汇总：多 HTTP、失败记录、reason 不复用。"""

from __future__ import annotations

import asyncio
import math

import httpx
import pytest
from loguru import logger

from src.cli.eval_deepeval import _score_metric_with_retry
from src.infra.llm.provider.http_observability import HookEvidence, make_logging_client


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
        # 模拟上一题成功后残留在指标实例上的 reason
        self.reason = "stale from previous question"

    def measure(self, test_case):
        self.calls += 1
        raise RuntimeError("outer") from TimeoutError("boom")


class _TwoCallMetric:
    """measure 内串行发 2 次真实 HTTP（走与 build_bridge_judge 同一 client 工厂）。"""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.reason = "ok"

    def measure(self, test_case):
        async def _two_posts() -> None:
            async with self.client:
                await self.client.post(JUDGE_URL, json={"call": 1})
                await self.client.post(JUDGE_URL, json={"call": 2})

        asyncio.run(_two_posts())
        return 0.9


ROW = {"q_index": 0, "trace_id": "eval_t1"}
JUDGE_URL = "https://example.invalid/v1/chat/completions"


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


def test_judge_call_counts_real_http_calls():
    """2 次真实 HTTP → 1 条 JUDGE_CALL 汇总 2 次请求（spec 招牌场景）。"""
    ev = HookEvidence()
    ev.set_run("eval_run_x_abcd1234", "judge-x")
    ev.mark(ROW["trace_id"])
    client = make_logging_client(
        timeout=httpx.Timeout(connect=1, read=1, write=1, pool=1),
        evidence=ev,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                },
            )
        ),
    )
    messages, sink_id = _capture()
    try:
        value, err = _score_metric_with_retry(
            "faithfulness", _TwoCallMetric(client), object(), ROW, ev
        )
    finally:
        logger.remove(sink_id)

    assert value == pytest.approx(0.9) and err == ""
    measure = ev.measures[0]
    assert measure["request_count"] == 2
    assert measure["response_count"] == 2
    assert measure["statuses"] == [200, 200]
    assert measure["tokens_in"] == 20
    assert measure["tokens_out"] == 6
    assert measure["reason"] == "ok"
    calls = measure["calls"]
    assert len(calls) == 2
    assert calls[0]["judge_call_id"] == "eval_t1:faithfulness:1:1"
    assert calls[1]["judge_call_id"] == "eval_t1:faithfulness:1:2"

    line = next(m for m in messages if "judge call" in m)
    assert "request_count=2" in line
    assert "statuses=[200,200]" in line


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
    assert ev.measures
    assert all(record["reason"] is None for record in ev.measures)


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


def test_langfuse_trace_metadata_carries_run_and_component(monkeypatch):
    from src.cli import eval_deepeval
    from src.infra.llm.trace_context import EvalTraceContext, current_eval_trace

    captured: dict = {}

    def _update(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(eval_deepeval.langfuse_context, "update_current_trace", _update)
    ctx = EvalTraceContext(
        run_id="20261019_000000",
        run_trace_id="eval_run_20261019_000000_abcd1234",
        judge_model="judge",
    )
    token = current_eval_trace.set(ctx)
    try:
        import asyncio

        asyncio.run(
            eval_deepeval._answer_one_question(
                graph=_FakeGraph(),
                kb_id="kb",
                session_id="s",
                query="q",
                trace_id="eval_t1",
            )
        )
    finally:
        current_eval_trace.reset(token)

    assert captured["metadata"] == {
        "run_trace_id": "eval_run_20261019_000000_abcd1234",
        "component": "sut",
    }


class _FakeGraph:
    async def ainvoke(self, state, **kwargs):
        return {"answer": "a", "tool_contexts": [], "messages": []}
