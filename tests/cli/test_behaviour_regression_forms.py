"""钉住被否定的形态：只挂 CallbackHandler、不驱动 evals_iterator ⇒ 不评分。

依据：design D6 / R9 —— 此时 `metric.score is None`，
故取分**只能**从 test_run JSON 回读。
"""

from __future__ import annotations

from deepeval.metrics import AgentLoopDetectionMetric


def test_metric_score_is_none_without_iterator_driven_run():
    # 用 loop-detection：它**不需要裁判**，构造期不触网（TaskCompletion 需要 model，
    # 传 None 会回落到 OpenAIModel 并因缺 key 直接报错）。
    m = AgentLoopDetectionMetric(threshold=0.5)

    assert m.score is None
