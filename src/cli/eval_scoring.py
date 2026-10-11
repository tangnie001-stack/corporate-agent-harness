"""评测打分辅助：样本分类、主分聚合、跳过掩码、检索覆盖率、分数回写。

与 eval_ragas.py 的分工：本模块只放纯函数与被测可单测的逻辑，
CLI 编排（跑图、读写文件）留在 eval_ragas.py。
"""

import pandas as pd
from langchain_core.messages import AIMessage
from loguru import logger

from src.config.const import (
    CONTEXT_METRIC_COLUMNS,
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
    SSEInteractionTexts,
)
from src.rag.context import RAGContext


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

    Args:
        df: ragas 结果 DataFrame（行序与 sample_classes 对齐）
        sample_classes: 每题的样本类别

    Returns:
        增加 sample_class / skipped 两列的副本
    """
    out = df.copy()
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


def main_score_means(df: pd.DataFrame) -> dict[str, float]:
    """计算主分：仅纯 KB 样本行的各指标均值。

    Args:
        df: 经 apply_sample_masks 处理、含 sample_class 列的结果

    Returns:
        指标名 -> 均值；无纯 KB 样本时对应值为 nan
    """
    mask = df["sample_class"] == SAMPLE_CLASS_PURE_KB
    result: dict[str, float] = {}
    for col in df.columns:
        if col in ("question", "ground_truth", "trace_id", "answer", "contexts"):
            continue
        if col in ("sample_class", "skipped"):
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
    LANGFUSE_ENABLE 开关与关停 flush）。逐条回写失败只记 warning，不向上抛，
    以免影响评测主流程。

    Args:
        trace_ids: 每题 trace_id（与 scores 各列表等长）
        scores: 指标名 -> 每题的分数列表；值为 None 或 NaN（掩码的跳过语义）
            时该题该指标不写出
        enabled: LANGFUSE_ENABLE 取值；False 时整体短路、不触碰客户端
    """
    if not enabled:
        return
    from langfuse.decorators import langfuse_context

    client = langfuse_context.client_instance
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
