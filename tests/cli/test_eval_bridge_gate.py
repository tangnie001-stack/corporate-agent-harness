# tests/cli/test_eval_bridge_gate.py
import math

import pandas as pd
import pytest

from src.cli.eval_ragas import check_gate


def _df(n_pure: int, n_nan: int, value: float = 0.99) -> pd.DataFrame:
    """n_pure 个纯 KB 样本；前 n_nan 个的 faithfulness 为 NaN（分母 = n_pure × 4）。"""
    return pd.DataFrame(
        [
            {
                "sample_class": "pure_kb",
                "faithfulness": math.nan if i < n_nan else value,
                "answer_relevancy": value,
                "context_precision": value,
                "context_recall": value,
            }
            for i in range(n_pure)
        ]
    )


def test_nan_rate_at_or_below_threshold_does_not_fail(capsys):
    df = _df(n_pure=20, n_nan=7)  # 7/80 = 8.75% ≤ 10%

    with pytest.raises(SystemExit) as e:
        check_gate(df, questions=["q"] * 20)

    assert e.value.code == 0, "NaN 率未超阈值时不得因 NaN 判失败"
    assert "NaN" in capsys.readouterr().out


def test_nan_rate_exactly_at_threshold_passes(capsys):
    df = _df(n_pure=20, n_nan=8)  # 8/80 = 10.0% == 阈值 ⇒ 不判失败

    with pytest.raises(SystemExit) as e:
        check_gate(df, questions=["q"] * 20)

    assert e.value.code == 0, "NaN 率恰等于阈值时不得判失败（> 才失败，== 通过）"
    assert "NaN" in capsys.readouterr().out


def test_nan_rate_above_threshold_fails_and_hints_escape_hatch(capsys):
    df = _df(n_pure=20, n_nan=9)  # 9/80 = 11.25% > 10%

    with pytest.raises(SystemExit) as e:
        check_gate(df, questions=["q"] * 20)

    assert e.value.code == 1
    assert "建议启用逃生门" in capsys.readouterr().out
