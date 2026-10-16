"""评测打分辅助：样本分类、主分聚合、跳过掩码、检索覆盖率、分数回写。

与 eval_ragas.py 的分工：本模块放评测辅助逻辑与可单测的纯函数；
`write_scores` 是唯一含外部 I/O 的函数（把每题分数回写 Langfuse），
CLI 编排（跑图、读写报告文件）留在 eval_ragas.py。
"""

import pandas as pd
from langchain_core.messages import AIMessage
from loguru import logger

from src.config.const import (
    CONTEXT_METRIC_COLUMNS,
    RAGAS_METRIC_COLUMNS,
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
    SSEInteractionTexts,
)
from src.rag.context import RAGContext


def build_metrics(judge, embeddings) -> dict:
    """构造 DeepEval RAGAS 桥的**经典四项**（design D2）。

    只用单项、不用聚合：聚合 `RagasMetric` 在源码里是 5 项算术平均（含
    `ContextualEntitiesRecall`），与官方文档写四项不一致 ⇒ 口径不可控。

    Args:
        judge: langchain `BaseChatModel`（桥要求）
        embeddings: langchain Embeddings（`answer_relevancy` 唯一需要）

    Returns:
        指标名 -> 桥度量实例；**顺序即串行评分顺序**
    """
    # 确保 DEEPEVAL_* 兜底先于 deepeval 被 import（顺序即语义，故禁用 isort 重排）
    # isort: off
    import src.config.settings  # noqa: F401
    from deepeval.metrics.ragas import (
        RAGASAnswerRelevancyMetric,
        RAGASContextualPrecisionMetric,
        RAGASContextualRecallMetric,
        RAGASFaithfulnessMetric,
    )
    # isort: on

    if embeddings is None:
        raise AssertionError("answer_relevancy 需要显式 embeddings（不得为 None）")

    return {
        "faithfulness": RAGASFaithfulnessMetric(model=judge),
        "answer_relevancy": RAGASAnswerRelevancyMetric(
            model=judge, embeddings=embeddings
        ),
        "context_precision": RAGASContextualPrecisionMetric(model=judge),
        "context_recall": RAGASContextualRecallMetric(model=judge),
    }


def classify_sample(contexts: list[RAGContext]) -> str:
    """按检索来源把单个样本分为纯 KB / 含 web / 无检索 三类。

    Args:
        contexts: 该样本经工具收集的检索材料

    Returns:
        SAMPLE_CLASS_EMPTY / SAMPLE_CLASS_PURE_KB / SAMPLE_CLASS_WEB
    """
    if not contexts:
        return SAMPLE_CLASS_EMPTY
    for c in contexts:
        if c.kind == SSEInteractionTexts.CITATION_KIND_WEB:
            return SAMPLE_CLASS_WEB
    return SAMPLE_CLASS_PURE_KB


def apply_sample_masks(df: pd.DataFrame, sample_classes: list[str]) -> pd.DataFrame:
    """按样本类别掩码：无材料样本的三项 context 指标置 NaN 并记 skipped。

    置空用 NaN（而非 None/pd.NA），以免下游 float() 抛错致落库静默失败。
    返回值重建为从 0 起的 RangeIndex：掩码用 `.loc[i, col]` 定位行，
    若传入表带非默认索引（如按 trace_id 建索引）会 KeyError。

    Args:
        df: ragas 结果 DataFrame（行序与 sample_classes 对齐）
        sample_classes: 每题的样本类别

    Returns:
        增加 sample_class / skipped 两列的副本（索引重置为 RangeIndex）
    """
    out = df.copy().reset_index(drop=True)
    out["sample_class"] = sample_classes
    skipped_col: list[str] = []
    for i in range(len(out)):
        marks: list[str] = []
        if sample_classes[i] == SAMPLE_CLASS_EMPTY:
            for col in CONTEXT_METRIC_COLUMNS:
                if col in out.columns:
                    out.loc[i, col] = float("nan")
                    marks.append(col)
        if marks:
            skipped_col.append(";".join(marks))
        else:
            skipped_col.append("")
    out["skipped"] = skipped_col
    return out


def nan_cell_stats(df: pd.DataFrame) -> dict:
    """NaN 率（单位是**格**，不是题）：分母 = 纯 KB 样本 × 四指标。

    分子 = 纯 KB 格里的**全部** NaN（合法缺值与裁判失败不可区分，一并计入）。
    掩码产生的 NaN 只出现在 `empty` 样本 ⇒ 天然落在分母之外，无需排除。

    Returns:
        {"nan_cells": int, "denominator_cells": int, "nan_rate": float}
    """
    pure = df[df["sample_class"] == SAMPLE_CLASS_PURE_KB]
    cols = [c for c in RAGAS_METRIC_COLUMNS if c in df.columns]
    denom = len(pure) * len(cols)
    nan_cells = int(pure.loc[:, cols].isna().to_numpy().sum()) if denom else 0
    return {
        "nan_cells": nan_cells,
        "denominator_cells": denom,
        "nan_rate": (nan_cells / denom) if denom else 0.0,
    }


def main_score_means(df: pd.DataFrame) -> dict[str, float]:
    """计算主分：仅纯 KB 样本行的各指标均值。

    指标集合在函数内收窄到 `RAGAS_METRIC_COLUMNS` 白名单（不依赖调用方过滤），
    以免结果表混入额外数值列（如 chunk_size）时被静默计入主分。
    均值口径为"纯 KB 且非 NaN"（`Series.mean()` 默认 `skipna=True`，即本口径；
    **不得**改为 `skipna=False`）。

    Args:
        df: 经 apply_sample_masks 处理、含 sample_class 列的结果

    Returns:
        指标名 -> 均值；无纯 KB 样本时对应值为 nan
    """
    mask = df["sample_class"] == SAMPLE_CLASS_PURE_KB
    result: dict[str, float] = {}
    for col in RAGAS_METRIC_COLUMNS:
        if col not in df.columns:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            result[col] = float(df.loc[mask, col].mean())
    return result


def retrieval_coverage(sample_classes: list[str]) -> float:
    """有检索样本（非 empty）占比。

    Args:
        sample_classes: 每题的样本类别

    Returns:
        覆盖率；空列表返回 0.0
    """
    if not sample_classes:
        return 0.0
    hit = sum(1 for c in sample_classes if c != SAMPLE_CLASS_EMPTY)
    return hit / len(sample_classes)


def summarize_trajectory(messages: list) -> dict:
    """生成轨迹诊断摘要：工具名 + 入参 + 迭代数；**不含工具输出正文**。

    Args:
        messages: LangGraph 状态中的消息列表

    Returns:
        {"tool_calls": [{"name": str, "args": dict}, ...], "iterations": int}；
        iterations 为 AIMessage 条数（即 Agent 迭代轮数）
    """
    calls: list[dict] = []
    iterations = 0
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        iterations += 1
        for tc in m.tool_calls:
            calls.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
    return {"tool_calls": calls, "iterations": iterations}


def write_scores(
    trace_ids: list[str], scores: dict[str, list[float]], enabled: bool
) -> None:
    """把每题各指标分数按 trace_id 显式回写 Langfuse。

    评分在批量 evaluate() 之后才产出，逐题 @observe 已关闭，
    故不能用 score_current_trace（无当前 trace 且吞异常）。客户端实例统一取
    `langfuse_context.client_instance`（**不得** new Langfuse()：那会绕过
    LANGFUSE_ENABLE 开关与关停 flush）。客户端获取失败或逐条回写失败都只记
    warning、不向上抛：回写属观测旁路，绝不阻断评测主流程（DB 落库与门禁）。

    Args:
        trace_ids: 每题 trace_id（与 scores 各列表等长）
        scores: 指标名 -> 每题的分数列表；值为 None 或 NaN（掩码的跳过语义）
            时该题该指标不写出
        enabled: LANGFUSE_ENABLE 取值；False 时整体短路、不触碰客户端
    """
    if not enabled:
        return
    from langfuse.decorators import langfuse_context

    try:
        client = langfuse_context.client_instance
    except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响评测主流程
        logger.warning(
            "[cli] langfuse client unavailable, scores not written err={}", exc
        )
        return
    for name, values in scores.items():
        for i, trace_id in enumerate(trace_ids):
            if i >= len(values):
                continue
            value = values[i]
            if value is None:
                continue
            if pd.isna(value):
                continue
            try:
                client.score(trace_id=trace_id, name=name, value=float(value))
            except Exception as exc:  # noqa: BLE001  # 回写失败不影响评测主流程
                logger.warning(
                    "[cli] langfuse score write failed trace_id={} metric={} err={}",
                    trace_id,
                    name,
                    exc,
                )
