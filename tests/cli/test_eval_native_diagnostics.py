import math

from deepeval.models import DeepEvalBaseLLM

from src.cli.eval_ragas import _bridge_test_case, score_rows
from src.cli.eval_scoring import build_diagnostic_metrics
from src.config.const import EVAL_RELEVANCY_MAX_CONTEXTS


class _FakeJudge(DeepEvalBaseLLM):
    """最小 DeepEval 裁判，避免真实网络调用。"""

    def load_model(self):
        """返回自身。"""
        return self

    def generate(self, prompt: str, **kwargs) -> str:
        """返回空对象。"""
        return "{}"

    async def a_generate(self, prompt: str, **kwargs) -> str:
        """异步版本。"""
        return "{}"

    def get_model_name(self) -> str:
        """固定模型名。"""
        return "fake-judge"


def test_diagnostic_metrics_use_project_judge():
    judge = _FakeJudge()
    metrics = build_diagnostic_metrics(judge)

    assert list(metrics) == ["contextual_relevancy", "non_advice"]
    for name, metric in metrics.items():
        assert metric.model is judge, f"{name} 必须显式收裁判"


def test_relevancy_truncates_without_mutating_shared_contexts():
    seen: dict[str, list[str]] = {}

    class _Capture:
        def __init__(self, key: str) -> None:
            self.key = key

        def measure(self, test_case) -> float:
            seen[self.key] = list(test_case.retrieval_context)
            return 0.5

    row = {
        "q_index": 0,
        "trace_id": "t0",
        "question": "q",
        "answer": "a",
        "ground_truth": "g",
        "contexts": [f"c{i}" for i in range(12)],
        "sample_class": "pure_kb",
    }
    metrics = {"context_precision": _Capture("precision")}
    diagnostics = {"contextual_relevancy": _Capture("relevancy")}

    out = score_rows([row], metrics, diagnostic_metrics=diagnostics)

    assert seen["precision"] == [f"c{i}" for i in range(12)]
    assert seen["relevancy"] == [f"c{i}" for i in range(EVAL_RELEVANCY_MAX_CONTEXTS)]
    assert out[0]["contextual_relevancy"] == 0.5
    assert out[0]["tool_correctness"] is None


def test_empty_sample_skips_all_metrics_and_diagnostics():
    class _Bomb:
        def measure(self, test_case) -> float:
            raise AssertionError("empty 样本不得调用任何指标")

    row = {
        "q_index": 0,
        "trace_id": "t0",
        "question": "q",
        "answer": "[ERROR] boom",
        "ground_truth": "g",
        "contexts": [],
        "sample_class": "empty",
    }

    out = score_rows(
        [row],
        {"faithfulness": _Bomb()},
        diagnostic_metrics={"contextual_relevancy": _Bomb(), "non_advice": _Bomb()},
    )

    assert math.isnan(out[0]["faithfulness"])
    assert math.isnan(out[0]["contextual_relevancy"])
    assert math.isnan(out[0]["non_advice"])
    assert out[0]["tool_correctness"] is None
    assert out[0]["error"] == "sample_class=empty"


def test_bridge_test_case_max_contexts_is_optional():
    row = {
        "question": "q",
        "answer": "a",
        "ground_truth": "g",
        "contexts": ["c0", "c1", "c2"],
    }
    assert _bridge_test_case(row).retrieval_context == ["c0", "c1", "c2"]
    assert _bridge_test_case(row, max_contexts=2).retrieval_context == ["c0", "c1"]
