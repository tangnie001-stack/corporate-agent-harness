import pytest

from src.cli.eval_deepeval import _limit_testset


def test_none_returns_unchanged():
    q = ["q1", "q2", "q3"]
    g = ["g1", "g2", "g3"]
    e = [["retrieve_kb"], None, []]
    out_q, out_g, out_e = _limit_testset(q, g, e, None)
    assert out_q is q
    assert out_g is g
    assert out_e is e


def test_limit_takes_prefix_of_all_three_lists():
    q = ["q1", "q2", "q3"]
    g = ["g1", "g2", "g3"]
    e = [["retrieve_kb"], None, []]
    out_q, out_g, out_e = _limit_testset(q, g, e, 2)
    assert out_q == ["q1", "q2"]
    assert out_g == ["g1", "g2"]
    assert out_e == [["retrieve_kb"], None]


def test_limit_larger_than_length_is_noop():
    q = ["q1", "q2"]
    g = ["g1", "g2"]
    e = [None, []]
    out_q, out_g, out_e = _limit_testset(q, g, e, 5)
    assert (out_q, out_g, out_e) == (q, g, e)


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValueError, match="--limit"):
        _limit_testset(["q1"], ["g1"], [None], 0)
