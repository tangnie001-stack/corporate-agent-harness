import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.cli.eval_scoring import (
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
    apply_sample_masks,
    classify_sample,
    main_score_means,
    retrieval_coverage,
    summarize_trajectory,
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


def test_apply_sample_masks_tolerates_non_default_index() -> None:
    """传入非默认索引（如以 trace_id 为索引）时仍按行位置掩码，不抛 KeyError。"""
    df = _df()
    df.index = ["trace_a", "trace_b", "trace_c"]
    out = apply_sample_masks(
        df, [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_WEB]
    )
    # 索引重置为 RangeIndex，掩码落到第 2 行（empty）
    assert list(out.index) == [0, 1, 2]
    assert pd.isna(out.loc[1, "faithfulness"])
    assert "faithfulness" in out.loc[1, "skipped"]
    # 非空行不标记
    assert out.loc[0, "skipped"] == ""


def test_main_score_means_narrows_to_metric_whitelist() -> None:
    """主分只聚合 RAGAS 指标白名单列，额外数值列（如 chunk_size）不得混入。"""
    df = apply_sample_masks(
        _df(), [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_WEB]
    )
    df["chunk_size"] = [512, 512, 512]
    means = main_score_means(df)
    assert set(means.keys()) == {
        "faithfulness",
        "context_precision",
        "context_recall",
        "answer_relevancy",
    }
    assert "chunk_size" not in means


def test_masks_diagnostics_for_empty_samples():
    """empty 样本的诊断指标列同样置 NaN 并记入 skipped。"""
    df = pd.DataFrame(
        {
            "faithfulness": [0.9, 0.1],
            "contextual_relevancy": [0.5, 0.2],
        }
    )
    out = apply_sample_masks(df, [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY])
    assert pd.isna(out.loc[1, "contextual_relevancy"])
    assert "contextual_relevancy" in out.loc[1, "skipped"]


def test_summarize_trajectory_extracts_names_and_iters() -> None:
    """轨迹摘要应含工具名/入参/迭代数，且不含工具输出正文。"""
    msgs = [
        HumanMessage(content="q"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "retrieve_kb",
                    "args": {"query": "x"},
                    "id": "1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="LONG BODY", tool_call_id="1"),
        AIMessage(content="answer"),
    ]
    s = summarize_trajectory(msgs)
    assert s["tool_calls"][0]["name"] == "retrieve_kb"
    assert s["tool_calls"][0]["args"] == {"query": "x"}
    assert s["iterations"] == 2
    # 工具输出正文不得进入摘要
    assert "LONG BODY" not in str(s)


# ---- write_scores：按 trace_id 显式回写 Langfuse ----
# 客户端是只读 property（LangfuseDecorator.client_instance 无 setter），
# 实例上无法 setattr，故 patch 其所属类的同名属性（断言语义与 brief 一致）。


def test_write_scores_noop_when_disabled() -> None:
    """enabled=False 时整体短路：不得触碰 Langfuse 客户端。"""
    from unittest.mock import patch

    from src.cli.eval_scoring import write_scores

    with patch(
        "langfuse.decorators.langfuse_decorator.LangfuseDecorator.client_instance"
    ) as ci:
        write_scores(["t1"], {"faithfulness": [0.9]}, enabled=False)
    ci.score.assert_not_called()


def test_write_scores_uses_trace_id() -> None:
    """enabled=True 时按 trace_id/name/value 逐条显式回写。"""
    from unittest.mock import patch

    from src.cli.eval_scoring import write_scores

    with patch(
        "langfuse.decorators.langfuse_decorator.LangfuseDecorator.client_instance"
    ) as ci:
        write_scores(["t1"], {"faithfulness": [0.9]}, enabled=True)
    ci.score.assert_called_once_with(trace_id="t1", name="faithfulness", value=0.9)


def test_write_scores_skips_none_and_nan() -> None:
    """None 与 NaN（掩码的跳过语义）均不得写出 score。"""
    from typing import Any
    from unittest.mock import patch

    from src.cli.eval_scoring import write_scores

    # 生产路径只出现 NaN；None 为防御分支，故用 Any 承载
    values: list[Any] = [None, float("nan")]

    with patch(
        "langfuse.decorators.langfuse_decorator.LangfuseDecorator.client_instance"
    ) as ci:
        write_scores(["t1", "t2"], {"context_recall": values}, enabled=True)
    ci.score.assert_not_called()


def test_write_scores_swallows_client_error() -> None:
    """单条回写失败只记 warning，不向上抛（不影响评测主流程）。"""
    from unittest.mock import patch

    from src.cli.eval_scoring import write_scores

    with patch(
        "langfuse.decorators.langfuse_decorator.LangfuseDecorator.client_instance"
    ) as ci:
        ci.score.side_effect = RuntimeError("boom")
        # 不应抛出
        write_scores(["t1"], {"faithfulness": [0.9]}, enabled=True)


def test_write_scores_swallows_client_acquisition_error() -> None:
    """客户端获取失败只记 warning、直接返回，不向上抛（观测旁路不阻断主流程）。"""
    from unittest.mock import PropertyMock, patch

    from src.cli.eval_scoring import write_scores

    with patch(
        "langfuse.decorators.langfuse_decorator.LangfuseDecorator.client_instance",
        new_callable=PropertyMock,
        side_effect=RuntimeError("no client"),
    ):
        # 不应抛出
        write_scores(["t1"], {"faithfulness": [0.9]}, enabled=True)
