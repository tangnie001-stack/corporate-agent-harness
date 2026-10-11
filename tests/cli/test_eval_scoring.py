import pandas as pd

from src.cli.eval_scoring import (
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
    apply_sample_masks,
    classify_sample,
    main_score_means,
    retrieval_coverage,
)
from src.rag.context import RAGContext


def _ctx(kind: str) -> RAGContext:
    """构造一条指定来源类型的最小检索材料，供分类测试使用。

    Args:
        kind: 检索来源类型（"kb" 或 "web"）

    Returns:
        仅填必要字段的 RAGContext
    """
    return RAGContext(
        content="x", source="s", page=1, doc_id="d", chunk_id="c", kind=kind
    )


def test_classify_empty_when_no_contexts() -> None:
    """无任何材料时归类为 empty。"""
    assert classify_sample([]) == SAMPLE_CLASS_EMPTY


def test_classify_pure_kb() -> None:
    """全部材料来自知识库时归类为 pure_kb。"""
    assert classify_sample([_ctx("kb"), _ctx("kb")]) == SAMPLE_CLASS_PURE_KB


def test_classify_web_when_any_web() -> None:
    """只要含一条联网材料就归类为 web。"""
    assert classify_sample([_ctx("kb"), _ctx("web")]) == SAMPLE_CLASS_WEB


def _df() -> pd.DataFrame:
    """构造三行、行序对应 pure_kb/empty/web 的指标 DataFrame。

    Returns:
        含四项指标列、无 sample_class 列的原始结果表
    """
    return pd.DataFrame(
        {
            "faithfulness": [0.9, 0.1, 0.8],
            "context_precision": [0.9, 0.2, 0.7],
            "context_recall": [0.9, 0.3, 0.6],
            "answer_relevancy": [0.9, 0.9, 0.9],
        }
    )


def test_masks_skipped_context_metrics_to_nan() -> None:
    """empty 行的三项 context 指标置 NaN 并记 skipped，非空行不标记。"""
    df = apply_sample_masks(
        _df(), [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_WEB]
    )
    # 第 2 行（empty）三项 context 指标置 NaN，且被标记 skipped
    assert pd.isna(df.loc[1, "faithfulness"])
    assert "faithfulness" in df.loc[1, "skipped"]
    # 第 1 行非空，不标记
    assert df.loc[0, "skipped"] == ""


def test_main_score_excludes_non_pure_kb() -> None:
    """主分只统计 pure_kb 行：此处仅第 1 行为纯 KB，均值等于其自身值。"""
    df = apply_sample_masks(
        _df(), [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_WEB]
    )
    means = main_score_means(df)
    # 只有第 1 行是纯 KB → 各指标均值等于它自己的值
    assert abs(means["context_recall"] - 0.9) < 1e-9
    assert abs(means["answer_relevancy"] - 0.9) < 1e-9


def test_retrieval_coverage() -> None:
    """有检索样本占比：1 个非 empty / 2 个样本 = 0.5。"""
    cov = retrieval_coverage([SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY])
    assert abs(cov - 0.5) < 1e-9
