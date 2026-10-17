"""`--limit` 的截断语义（冒烟轮次用，不改变全量默认行为）。"""

import pytest

from src.cli.eval_deepeval import _limit_testset


def test_none_returns_unchanged():
    q = ["q1", "q2", "q3"]
    g = ["g1", "g2", "g3"]
    out_q, out_g = _limit_testset(q, g, None)
    assert out_q == q
    assert out_g == g
    assert out_q is q  # 不复制，保持原对象


def test_limit_takes_prefix_of_both_lists():
    q = ["q1", "q2", "q3"]
    g = ["g1", "g2", "g3"]
    out_q, out_g = _limit_testset(q, g, 2)
    assert out_q == ["q1", "q2"]
    assert out_g == ["g1", "g2"]


def test_limit_larger_than_length_is_noop():
    q = ["q1", "q2"]
    g = ["g1", "g2"]
    out_q, out_g = _limit_testset(q, g, 5)
    assert out_q == q
    assert out_g == g


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValueError, match="--limit"):
        _limit_testset(["q1"], ["g1"], 0)
