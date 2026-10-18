from src.cli.eval_scoring import link_behavior_traces


def test_links_by_q_index():
    rows = [{"q_index": 1, "input": "Q1"}, {"q_index": 0, "input": "Q0"}]
    linked = link_behavior_traces(rows, ["t0", "t1"])
    assert linked[0]["trace_id"] == "t1" and linked[0]["link_method"] == "order"
    assert linked[1]["trace_id"] == "t0" and linked[1]["link_method"] == "order"


def test_out_of_range_is_unmapped():
    linked = link_behavior_traces([{"q_index": 5}], ["t0"])
    assert linked[0]["trace_id"] is None
    assert linked[0]["link_method"] == "unmapped"
