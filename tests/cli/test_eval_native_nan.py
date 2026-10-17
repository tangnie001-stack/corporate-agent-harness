import math

import pandas as pd

from src.cli.eval_scoring import nan_cell_stats
from src.infra.llm.provider.http_observability import (
    HookEvidence,  # noqa: F401 （同批断言用具名导入）
)


def _df():
    return pd.DataFrame(
        {
            "sample_class": ["pure_kb", "pure_kb", "empty", "web"],
            "faithfulness": [0.9, math.nan, math.nan, 0.5],
            "answer_relevancy": [0.8, 0.7, 0.6, 0.5],
            "context_precision": [0.9, 0.9, math.nan, 0.5],
            "context_recall": [0.9, 0.9, math.nan, 0.5],
        }
    )


def test_nan_rate_denominator_is_pure_kb_times_four_metrics():
    stats = nan_cell_stats(_df())

    assert stats["denominator_cells"] == 2 * 4, "分母 = 纯 KB 样本 × 四指标"
    assert stats["nan_cells"] == 1, (
        "只数纯 KB 格里的 NaN（empty 行的掩码 NaN 天然在分母外）"
    )
    assert stats["nan_rate"] == 0.125


def test_all_metrics_nan_gives_rate_one_without_crash():
    df = pd.DataFrame(
        {
            "sample_class": ["pure_kb"],
            "faithfulness": [math.nan],
            "answer_relevancy": [math.nan],
            "context_precision": [math.nan],
            "context_recall": [math.nan],
        }
    )
    assert nan_cell_stats(df)["nan_rate"] == 1.0
