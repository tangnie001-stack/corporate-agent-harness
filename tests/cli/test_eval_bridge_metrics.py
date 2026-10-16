from src.cli.eval_scoring import build_metrics


class _FakeJudge:
    pass


class _FakeEmbeddings:
    pass


def test_build_metrics_returns_four_classic_bridge_metrics():
    metrics = build_metrics(_FakeJudge(), _FakeEmbeddings())

    assert list(metrics) == [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]


def test_build_metrics_passes_judge_explicitly_and_never_uses_aggregate():
    judge = _FakeJudge()
    metrics = build_metrics(judge, _FakeEmbeddings())

    for name, m in metrics.items():
        assert m.model is judge, f"{name} 必须显式收裁判实例（防回落到 gpt-3.5-turbo）"
    assert type(metrics["answer_relevancy"]).__name__ == "RAGASAnswerRelevancyMetric"
    # 聚合度量与 entity recall 一律不用（源码 5 项 vs 文档 4 项不一致）
    assert not any(
        type(m).__name__ in {"RagasMetric", "RAGASContextualEntitiesRecall"}
        for m in metrics.values()
    )


def test_build_metrics_rejects_no_embeddings_for_answer_relevancy():
    import pytest

    with pytest.raises((TypeError, AssertionError)):
        build_metrics(_FakeJudge(), None)  # answer_relevancy 是唯一需要 embedding 的
