import pytest
from deepeval.config.eval_mode import EvalMode
from deepeval.models import DeepEvalBaseLLM

from src.cli.eval_deepeval import _score_tool_correctness
from src.cli.eval_scoring import build_diagnostic_metrics


class _FakeJudge(DeepEvalBaseLLM):
    def load_model(self):
        return self

    def generate(self, prompt: str, **kwargs) -> str:
        return "{}"

    async def a_generate(self, prompt: str, **kwargs) -> str:
        return "{}"

    def get_model_name(self) -> str:
        return "fake-judge"


def _row(expected, called, *, answer="answer", failed=False):
    return {
        "q_index": 0,
        "question": "question",
        "answer": answer,
        "expected_tools": expected,
        "trajectory": {
            "tool_calls": [{"name": name, "args": {"query": "x"}} for name in called],
            "iterations": 2,
            "failed": failed,
        },
    }


def _metric():
    judge = _FakeJudge()
    return judge, build_diagnostic_metrics(judge)["tool_correctness"]


def test_single_expected_tool_is_hit():
    _, metric = _metric()
    assert (
        _score_tool_correctness(_row(["search_web"], ["search_web"]), metric)[0] == 1.0
    )


def test_expected_tool_missing_is_zero():
    _, metric = _metric()
    score, reason = _score_tool_correctness(
        _row(["search_web"], ["retrieve_kb"]), metric
    )
    assert score == 0.0
    assert "search_web" in reason


def test_duplicate_and_extra_calls_do_not_reduce_score():
    _, metric = _metric()
    assert (
        _score_tool_correctness(
            _row(["retrieve_kb"], ["retrieve_kb", "retrieve_kb", "search_web"]), metric
        )[0]
        == 1.0
    )


def test_multiple_expected_tools_yield_fraction():
    _, metric = _metric()
    score, reason = _score_tool_correctness(
        _row(["retrieve_kb", "search_web"], ["retrieve_kb"]), metric
    )
    assert score == 0.5
    assert "search_web" in reason


def test_duplicate_labels_are_deduped():
    _, metric = _metric()
    score, _ = _score_tool_correctness(
        _row(["retrieve_kb", "retrieve_kb"], ["retrieve_kb"]), metric
    )
    assert score == 1.0


def test_explicit_empty_labels():
    _, metric = _metric()
    assert _score_tool_correctness(_row([], []), metric)[0] == 1.0
    assert _score_tool_correctness(_row([], ["retrieve_kb"]), metric)[0] == 0.0


def test_missing_labels_are_na():
    _, metric = _metric()
    assert _score_tool_correctness(_row(None, []), metric) == (None, "")


def test_failed_answer_is_na_even_with_labels():
    _, metric = _metric()
    assert _score_tool_correctness(_row(["retrieve_kb"], [], answer=""), metric) == (
        None,
        "failed",
    )
    assert _score_tool_correctness(
        _row(["retrieve_kb"], [], answer="[ERROR] boom"), metric
    ) == (None, "failed")


def test_delegation_is_not_exempt():
    _, metric = _metric()
    score, reason = _score_tool_correctness(
        _row(["retrieve_kb"], ["delegate_task"]), metric
    )
    assert score == 0.0
    assert "delegate_task" in reason


def test_reason_is_single_line_without_pipe():
    _, metric = _metric()
    _, reason = _score_tool_correctness(
        _row(["retrieve_kb"], ["delegate_task"]), metric
    )
    assert "\n" not in reason
    assert "|" not in reason


def test_empty_sample_with_labels_scores_zero():
    import math

    from src.cli.eval_deepeval import score_rows

    class _Bomb:
        def measure(self, test_case):
            raise AssertionError("empty 样本不应调用裁判诊断")

    _, metric = _metric()
    row = _row(["retrieve_kb"], [])
    row["sample_class"] = "empty"
    out = score_rows(
        [row],
        {"faithfulness": _Bomb()},
        diagnostic_metrics={
            "contextual_relevancy": _Bomb(),
            "non_advice": _Bomb(),
            "tool_correctness": metric,
        },
    )
    assert out[0]["tool_correctness"] == 0.0
    assert math.isnan(out[0]["contextual_relevancy"])


@pytest.mark.parametrize("trajectory", [{}, {"failed": True, "tool_calls": []}])
def test_failed_run_with_materials_masks_metrics_without_scoring(trajectory):
    import math

    from src.cli.eval_deepeval import score_rows

    invoked: list[str] = []

    class _Bomb:
        def __init__(self, name):
            self.name = name

        def measure(self, test_case):
            invoked.append(self.name)
            raise AssertionError(f"{self.name} 不应对跑图失败样本评分")

    _, tool_metric = _metric()
    row = {
        "q_index": 0,
        "question": "question",
        "answer": "",
        "ground_truth": "gt",
        "contexts": ["ctx"],
        "sample_class": "pure_kb",
        "expected_tools": ["retrieve_kb"],
        "trajectory": trajectory,
    }
    out = score_rows(
        [row],
        {
            "faithfulness": _Bomb("faithfulness"),
            "answer_correctness": _Bomb("answer_correctness"),
            "context_precision": _Bomb("context_precision"),
            "context_recall": _Bomb("context_recall"),
        },
        diagnostic_metrics={
            "contextual_relevancy": _Bomb("contextual_relevancy"),
            "non_advice": _Bomb("non_advice"),
            "tool_correctness": tool_metric,
        },
    )
    result = out[0]
    for name in (
        "faithfulness",
        "answer_correctness",
        "context_precision",
        "context_recall",
        "contextual_relevancy",
        "non_advice",
    ):
        assert math.isnan(result[name]), name
    assert result["tool_correctness"] is None
    assert result["tool_correctness_reason"] == "failed"
    assert result["error"] == "failed"
    assert invoked == []


def test_metric_is_constructed_with_fixed_mode():
    judge, metric = _metric()
    assert metric.model is judge
    assert metric.async_mode is False
    assert metric.eval_mode is EvalMode.LLM
    assert metric.available_tools is None
    assert metric.evaluation_params == []


def test_tool_correctness_is_not_a_behaviour_metric():
    from src.cli.eval_scoring import build_behaviour_metrics

    names = [type(metric).__name__ for metric in build_behaviour_metrics(_FakeJudge())]
    assert "ToolCorrectnessMetric" not in names


def test_apply_sample_masks_keeps_tool_correctness():
    import math

    import pandas as pd

    from src.cli.eval_scoring import apply_sample_masks

    out = apply_sample_masks(
        pd.DataFrame({"tool_correctness": [0.0], "faithfulness": [0.5]}),
        ["empty"],
    )
    assert out.loc[0, "tool_correctness"] == 0.0
    assert math.isnan(out.loc[0, "faithfulness"])
