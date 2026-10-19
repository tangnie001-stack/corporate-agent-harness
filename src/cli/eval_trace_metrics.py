"""DeepEval 指标构造与单条 trace 评分。"""

from __future__ import annotations

from typing import Any


def metric_instances(judge: Any, names: list[str]) -> dict:
    """按名称构造 DeepEval 指标。

    Args:
        judge: `BridgeDeepEvalLLM`
        names: 指标名列表

    Returns:
        指标名 -> 度量实例
    """
    # 确保 DEEPEVAL_* 环境兜底先于 deepeval import（与 eval_scoring 同序）
    # isort: off
    import src.config.settings  # noqa: F401
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        ContextualPrecisionMetric,
        ContextualRecallMetric,
        ContextualRelevancyMetric,
        FaithfulnessMetric,
    )
    # isort: on

    builders = {
        "faithfulness": lambda: FaithfulnessMetric(model=judge),
        "answer_relevancy": lambda: AnswerRelevancyMetric(model=judge),
        "contextual_relevancy": lambda: ContextualRelevancyMetric(model=judge),
        "context_precision": lambda: ContextualPrecisionMetric(model=judge),
        "context_recall": lambda: ContextualRecallMetric(model=judge),
    }
    return {name: builders[name]() for name in names if name in builders}


def run_metrics(parsed: dict, names: list[str]) -> dict:
    """跑选中指标；单指标失败只记 error，不中断其余指标。

    Args:
        parsed: `parse_trace` 产物
        names: 指标名列表

    Returns:
        指标名 -> {score, reason, error}
    """
    from deepeval.test_case import LLMTestCase

    from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM
    from src.infra.llm.provider.factory import build_bridge_judge
    from src.infra.llm.provider.http_observability import HookEvidence

    judge = BridgeDeepEvalLLM(build_bridge_judge(evidence=HookEvidence()))
    contexts = [item["text"] for item in parsed["contexts"]]
    test_case = LLMTestCase(
        input=parsed["query"],
        actual_output=parsed["answer"],
        retrieval_context=contexts,
        expected_output=parsed.get("reference") or None,
    )
    out: dict[str, dict] = {}
    for name, metric in metric_instances(judge, names).items():
        try:
            score = metric.measure(test_case)
            reason = getattr(metric, "reason", None)
            out[name] = {"score": score, "reason": reason, "error": None}
        except Exception as exc:  # noqa: BLE001  # 单指标失败不阻断其余指标
            out[name] = {
                "score": None,
                "reason": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return out
