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


def test_llm_backed_metrics_receive_the_judge_and_loop_detection_does_not():
    """LLM 驱动的**两项**都必须拿到**同一个**裁判实例；loop-detection 是纯 trace、不接裁判。

    ⚠️ 该类**确实有** `model` 属性（基类声明并置 None），故**不能**断言 `hasattr` 为假。
    断言用 `is judge`（身份）而非 `is not None`：后者在"工厂硬编码了别的模型"时也会通过。
    """
    judge = _FakeDeepEvalLLM()
    metrics = build_behaviour_metrics(judge)

    assert metrics[0].model is judge  # TaskCompletion
    assert metrics[1].model is judge  # StepEfficiency（评审 Important：原测试漏了这项）
    assert metrics[2].model is None  # AgentLoopDetection（纯 trace，不调 LLM）
