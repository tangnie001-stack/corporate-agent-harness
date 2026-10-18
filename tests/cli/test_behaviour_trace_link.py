import pytest

from src.cli.eval_scoring import link_behavior_traces


def test_links_by_q_index():
    rows = [{"q_index": 1, "input": "Q1"}, {"q_index": 0, "input": "Q0"}]
    linked = link_behavior_traces(rows, ["t0", "t1"])
    assert linked[0]["trace_id"] == "t1" and linked[0]["link_method"] == "order"
    assert linked[1]["trace_id"] == "t0" and linked[1]["link_method"] == "order"


@pytest.mark.parametrize(
    "row",
    [
        {"q_index": 5},  # 越界
        {},  # 缺 q_index
        {"q_index": "0"},  # 非 int
    ],
)
def test_unmappable_rows_fall_back_to_unmapped(row):
    linked = link_behavior_traces([row], ["t0"])
    assert linked[0]["trace_id"] is None
    assert linked[0]["link_method"] == "unmapped"
