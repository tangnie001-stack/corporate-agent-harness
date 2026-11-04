from src.rca.models import EvidenceResult, EvidenceStatus, Tier, TriageVerdict
from src.rca.render import render_card


def test_card_contains_tier_and_missing():
    verdict = TriageVerdict(Tier.WATCH, "证据不足", 2, False)
    results = {
        "stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 2}, 10),
        "detail": EvidenceResult("detail", EvidenceStatus.FAILED, None, 10, "timeout"),
        "history": EvidenceResult("history", EvidenceStatus.EMPTY, None, 5, "no_data"),
    }
    text = render_card(verdict, results, {"summary": "疑似噪声"}, 1500)
    assert "建议观察" in text
    assert "未取到 detail" in text
    assert "无数据 history" in text


def test_ip_masked():
    verdict = TriageVerdict(Tier.NO_ACTION, "孤立慢请求", 2, False)
    results = {
        "stats": EvidenceResult(
            "stats",
            EvidenceStatus.OK,
            {"total": 2, "by_node": [{"addr": "172.18.0.123:8083", "cnt": 2}]},
            10,
        )
    }
    text = render_card(verdict, results, {}, 1500)
    assert "172.18.0.123" not in text


def test_text_ip_with_port_is_masked():
    verdict = TriageVerdict(Tier.WATCH, "证据不足", 2, False)
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 2}, 10)}
    text = render_card(
        verdict, results, {"summary": "节点 172.18.0.123:8083 疑似 OOM"}, 1500
    )
    assert "172.18.0.123" not in text
    assert "node-***" in text


def test_text_ip_without_port_is_masked():
    verdict = TriageVerdict(Tier.WATCH, "证据不足", 2, False)
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 2}, 10)}
    text = render_card(
        verdict, results, {"next_actions": "登录 10.0.0.7 查看进程"}, 1500
    )
    assert "10.0.0.7" not in text


def test_missing_stats_renders_zero_sample():
    text = render_card(
        TriageVerdict(Tier.WATCH, "必需证据缺失", 0, False), {}, {}, 1500
    )
    assert "样本 0 条" in text


def test_stats_not_dict_renders_zero_sample():
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, None, 10)}
    text = render_card(
        TriageVerdict(Tier.WATCH, "证据不足", 0, False), results, {}, 1500
    )
    assert "样本 0 条" in text


def test_confidence_rendered():
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 3}, 10)}
    text = render_card(
        TriageVerdict(Tier.WATCH, "证据不足", 3, False),
        results,
        {"confidence": "high"},
        1500,
    )
    assert "置信 high" in text


def test_confidence_absent_renders_dash():
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 3}, 10)}
    text = render_card(
        TriageVerdict(Tier.WATCH, "证据不足", 3, False), results, {}, 1500
    )
    assert "置信 -" in text


def test_optional_sections_rendered():
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 3}, 10)}
    explanation = {
        "summary": "下游超时",
        "hypotheses": ["依赖慢"],
        "counter_evidence": ["无 5xx"],
        "next_actions": ["看依赖"],
    }
    text = render_card(
        TriageVerdict(Tier.WATCH, "证据不足", 3, False), results, explanation, 1500
    )
    assert "结论 下游超时" in text
    assert "假设 [" in text
    assert "反证 [" in text
    assert "下一步 [" in text


def test_absent_optional_sections_omitted():
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 3}, 10)}
    text = render_card(
        TriageVerdict(Tier.NO_ACTION, "孤立慢请求", 3, False),
        results,
        {"summary": "", "hypotheses": [], "counter_evidence": None, "next_actions": []},
        1500,
    )
    assert "结论" not in text
    assert "假设" not in text
    assert "反证" not in text
    assert "下一步" not in text


def test_ok_evidence_not_listed_as_missing():
    results = {
        "stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 3}, 10),
        "detail": EvidenceResult("detail", EvidenceStatus.OK, [{"status": 200}], 10),
    }
    text = render_card(
        TriageVerdict(Tier.NO_ACTION, "孤立慢请求", 3, False), results, {}, 1500
    )
    assert "未取到" not in text
    assert "无数据" not in text


def test_truncated_to_max_chars():
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 3}, 10)}
    explanation = {"summary": "很长的结论" * 20}
    text = render_card(
        TriageVerdict(Tier.WATCH, "证据不足", 3, False), results, explanation, 50
    )
    assert len(text) == 50
    assert text.endswith("…")


def test_within_limit_not_truncated():
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 3}, 10)}
    text = render_card(
        TriageVerdict(Tier.WATCH, "证据不足", 3, False),
        results,
        {"summary": "短"},
        1500,
    )
    assert not text.endswith("…")


def test_renders_latency_and_nodes():
    """量化指标与节点分布要进卡片，且地址替换为序号。"""
    verdict = TriageVerdict(Tier.NO_ACTION, "孤立慢请求", 2, False)
    results = {
        "stats": EvidenceResult(
            "stats",
            EvidenceStatus.OK,
            {
                "total": 723,
                "latency_ms": {"p50": 557, "p95": 691, "max": 1869},
                "slow": {"threshold": 1000, "count": 1},
                "by_node": [
                    {"addr": "172.18.0.123:8083", "cnt": 362, "p95": 688},
                    {"addr": "172.18.0.124:8083", "cnt": 361, "p95": 690},
                ],
            },
            10,
        )
    }
    text = render_card(verdict, results, {}, 1500)
    assert "p95 691ms" in text
    assert "node-1: 362 条" in text
    assert "172.18" not in text


def test_renders_business_impact_delta():
    """业务影响要带昨日同口径与涨跌幅。"""
    verdict = TriageVerdict(Tier.WATCH, "x", 10, False)
    results = {
        "stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 10}, 10),
        "impact": EvidenceResult(
            "impact",
            EvidenceStatus.OK,
            {
                "endpoints": [
                    {"uri": "/welife-pay/pay/createOrder", "pv": 3601, "base_pv": 3849}
                ]
            },
            10,
        ),
    }
    text = render_card(verdict, results, {}, 1500)
    assert "业务影响" in text
    assert "3601" in text
    assert "-6%" in text


def test_impact_missing_not_rendered():
    """影响证据缺失时不留空行。"""
    verdict = TriageVerdict(Tier.WATCH, "x", 10, False)
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 10}, 10)}
    text = render_card(verdict, results, {}, 1500)
    assert "业务影响" not in text


def test_alert_values_line_rendered():
    """告警自带的 refId 值要出现在卡片上。"""
    verdict = TriageVerdict(Tier.NO_ACTION, "x", 10, False)
    results = {"stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 10}, 10)}
    text = render_card(
        verdict, results, {}, 1500, "告警值 B=1057（平均耗时）；C=1（阈值判定 > 1000）"
    )
    assert "告警值 B=1057" in text
    assert "阈值判定 > 1000" in text
