"""评测打分辅助：样本分类、主分聚合、跳过掩码、检索覆盖率、分数回写。

与 eval_ragas.py 的分工：本模块只放纯函数与被测可单测的逻辑，
CLI 编排（跑图、读写文件）留在 eval_ragas.py。
"""

import pandas as pd

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
