from deepeval.models import DeepEvalBaseLLM

from src.cli.eval_scoring import build_metrics


class _FakeJudge(DeepEvalBaseLLM):
    """满足 DeepEval 类型检查的最小裁判（不发起网络调用）。"""

    def load_model(self):
        """返回自身，模拟已加载模型。"""
        return self

    def generate(self, prompt: str, **kwargs) -> str:
        """返回空 JSON 对象，测试不会真正执行评分。"""
        return "{}"

    async def a_generate(self, prompt: str, **kwargs) -> str:
        """异步版本，保持与同步实现一致。"""
        return "{}"

    def get_model_name(self) -> str:
        """固定模型名，便于断言。"""
        return "fake-judge"


def test_build_metrics_returns_four_native_metrics():
    metrics = build_metrics(_FakeJudge())

    assert list(metrics) == [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]
    assert [type(m).__name__ for m in metrics.values()] == [
        "FaithfulnessMetric",
        "AnswerRelevancyMetric",
        "ContextualPrecisionMetric",
        "ContextualRecallMetric",
    ]


def test_build_metrics_passes_judge_explicitly():
    judge = _FakeJudge()
    for name, metric in build_metrics(judge).items():
        assert metric.model is judge, f"{name} 必须显式收裁判（防回落到 gpt-3.5-turbo）"


def test_metric_columns_are_unchanged():
    from src.config.const import CONTEXT_METRIC_COLUMNS, RAGAS_METRIC_COLUMNS

    assert RAGAS_METRIC_COLUMNS == (
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    )
    assert CONTEXT_METRIC_COLUMNS == (
        "faithfulness",
        "context_precision",
        "context_recall",
    )
