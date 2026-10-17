"""overall_score = 纯 KB 四项均值；passed 取单一常量。"""

import pandas as pd

from src.cli import eval_deepeval
from src.config.const import EVAL_PASS_THRESHOLD


def test_overall_score_is_pure_kb_four_metric_mean(monkeypatch):
    """落库 overall_score 须为纯 KB 掩码后的四项均值，passed 由常量阈值派生。"""
    captured = {}

    class _Svc:
        async def insert_eval_report(self, entity):
            captured["overall"] = entity.overall_score
            captured["passed"] = entity.passed

    class _Entity:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    monkeypatch.setattr(
        eval_deepeval, "_eval_report_deps", lambda: (_Svc(), _Entity), raising=False
    )

    df = pd.DataFrame(
        {
            "faithfulness": [1.0, 0.0],
            "answer_relevancy": [1.0, 0.0],
            "context_precision": [1.0, 0.0],
            "context_recall": [1.0, 0.0],
            "error": ["", ""],
        }
    )
    # 仅 pure_kb 行参与主分 → 四项均 1.0 → overall=1.0
    eval_deepeval._save_eval_report(
        "kb", df, 2, "r.csv", None, sample_classes=["pure_kb", "web"]
    )

    assert captured["overall"] == 1.0
    assert captured["passed"] is (1.0 >= EVAL_PASS_THRESHOLD)
