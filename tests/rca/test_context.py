from src.rca.context import extract_logstore, extract_threshold, extract_uri

SLS_QUERY = (
    "* | select avg(upstream_response_time) * 1000 as x, __time__-__time__%60 as t "
    "where request_uri = '/welife-pay/pay/createOrder' group by t"
)


def test_extract_uri_from_sls_query():
    assert extract_uri(SLS_QUERY) == "/welife-pay/pay/createOrder"


def test_extract_uri_absent_returns_none():
    assert extract_uri("sum(increase(envoy_cluster_upstream_rq[1m]))") is None


def test_extract_logstore():
    rule = {"query": '[{"refId":"A","model":{"logstore":"welife-c53n"}}]'}
    assert extract_logstore(rule) == "welife-c53n"


def test_extract_threshold_from_threshold_step():
    rule = {
        "query": (
            '[{"refId":"C","model":{"type":"threshold","conditions":'
            '[{"evaluator":{"type":"gt","params":[1000]}}]}}]'
        )
    }
    assert extract_threshold(rule) == 1000.0
