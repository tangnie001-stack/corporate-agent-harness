# tests/cli/test_behaviour_metrics.py
from __future__ import annotations

from deepeval.models import DeepEvalBaseLLM

from src.cli.eval_scoring import build_behaviour_metrics


class _FakeDeepEvalLLM(DeepEvalBaseLLM):
    """最小可接受的 DeepEval LLM；本测试不触发真实调用。

    必须是 `DeepEvalBaseLLM` 子类：`initialize_model` 只接受 `str | DeepEvalBaseLLM`，
    其它对象会被当成**模型名**回落到 `OpenAIModel(...)`（实测直接因缺 key 报错）。
    """

    def load_model(self):
        return self

    def get_model_name(self) -> str:
        return "fake"

    def generate(self, prompt: str, **kwargs) -> str:
        return "{}"

    async def a_generate(self, prompt: str, **kwargs) -> str:
        return "{}"


def test_build_behaviour_metrics_returns_three_in_fixed_order():
    metrics = build_behaviour_metrics(_FakeDeepEvalLLM())

    assert [m.__class__.__name__ for m in metrics] == [
        "TaskCompletionMetric",
        "StepEfficiencyMetric",
        "AgentLoopDetectionMetric",
    ]


def test_loop_detection_is_not_wired_to_the_judge():
    """loop-detection 是纯 trace 分析：构造时不传裁判 ⇒ 其 `model` 为 `None`。

    ⚠️ 该类**确实有** `model` 属性（基类声明并置 None），故**不能**断言 `hasattr` 为假。
    """
    metrics = build_behaviour_metrics(_FakeDeepEvalLLM())

    assert metrics[2].model is None
    assert metrics[0].model is not None
