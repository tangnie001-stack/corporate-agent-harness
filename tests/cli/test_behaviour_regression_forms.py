"""钉住被否定的形态及其后果（design D6 / R9）。

两条**实测**事实：
1. 未驱动任何评测时，指标实例上不会有分数（`score` 为构造默认 `None`）
   ⇒ 取分**只能**从 `test_run_*.json` 回读（eval loop 用 `copy_metrics()` 副本）。
2. `AgentLoopDetection` 在**无 trace** 时 `measure()` **不报错、直接给 `0.0`**（静默假分数）
   —— 这正是行为轴必须有 fail-loud（`assert_behaviour_scores_complete`）的理由。
"""

from __future__ import annotations

from deepeval.metrics import AgentLoopDetectionMetric
from deepeval.test_case import LLMTestCase


def test_fresh_metric_has_no_score_before_any_run():
    # 用 loop-detection：它**不需要裁判**，构造期不触网（TaskCompletion 需要 model，
    # 传 None 会回落到 OpenAIModel 并因缺 key 直接报错）。
    m = AgentLoopDetectionMetric(threshold=0.5)

    assert m.score is None


def test_loop_detection_silently_scores_zero_without_trace():
    """无 trace ⇒ `0.0` 且**不抛异常**：静默假分数，故必须有 fail-loud 兜住。"""
    m = AgentLoopDetectionMetric(threshold=0.5)

    score = m.measure(LLMTestCase(input="q", actual_output="a"))

    assert score == 0.0
    assert m.success is False
