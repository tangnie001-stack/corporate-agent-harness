from src.rca.context import count_active_minutes


def test_counts_distinct_minutes():
    annotations = [
        {
            "time": 1000,
            "text": "支付单createOrder响应时间上升 {alertname=支付单createOrder响应时间上升}",
        },
        {
            "time": 61000,
            "text": "支付单createOrder响应时间上升 {alertname=支付单createOrder响应时间上升}",
        },
        {
            "time": 61000,
            "text": "支付单createOrder响应时间上升 {alertname=支付单createOrder响应时间上升}",
        },
        {"time": 121000, "text": "其他告警 {alertname=其他告警}"},
    ]
    assert count_active_minutes(annotations, "支付单createOrder响应时间上升") == 2
