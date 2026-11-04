from src.rca.judge import judge
from src.rca.models import EvidenceResult, EvidenceStatus, Tier


def _ok(eid: str, data) -> EvidenceResult:
    return EvidenceResult(eid, EvidenceStatus.OK, data, 10)


def _stats(
    total: int,
    slow: int,
    p95: float = 0.0,
    max_ms: float = 0.0,
    c5xx: int = 0,
) -> EvidenceResult:
    return _ok(
        "stats",
        {
            "total": total,
            "slow": {"count": slow},
            "latency_ms": {"p95": p95, "max": max_ms},
            "errors": {"5xx": c5xx},
        },
    )


def _rows(*statuses) -> EvidenceResult:
    return _ok("detail", [{"status": status} for status in statuses])


def test_zero_sample_is_watch():
    results = {"stats": _ok("stats", {"total": 0, "slow": {"count": 0}, "by_node": []})}
    assert judge(results, 3000).tier is Tier.WATCH


def test_single_slow_request_is_no_action():
    results = {
        "stats": _stats(2, 1, 1057.0, 1200.0),
        "detail": _rows(200, 200),
    }
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_all_requests_slow_is_watch():
    results = {
        "stats": _stats(2, 2, 1800.0, 1900.0),
        "detail": _rows(200, 200),
    }
    assert judge(results, 3000).tier is Tier.WATCH


def test_5xx_from_detail_is_need_action():
    results = {"stats": _stats(10, 0), "detail": _rows(502)}
    assert judge(results, 3000).tier is Tier.NEED_ACTION


def test_5xx_from_stats_is_need_action_without_detail():
    """明细按 when 未采集时，stats 的 5xx 计数仍必须拦下。"""
    results = {"stats": _stats(100, 0, c5xx=40)}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.NEED_ACTION
    assert "40" in verdict.reason


def test_upstream_status_5xx_is_need_action():
    """网关 200、后端 502 的请求也算 5xx。"""
    results = {
        "stats": _stats(10, 0),
        "detail": _ok("detail", [{"status": 200, "upstream_status": 502}]),
    }
    assert judge(results, 3000).tier is Tier.NEED_ACTION


def test_absent_upstream_status_is_ignored():
    results = {"stats": _stats(3, 0), "detail": _ok("detail", [{"status": 200}])}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def _stats_without_errors(total: int, slow: int, max_ms: float = 0.0) -> EvidenceResult:
    """构造不带 5xx 统计的 stats，用于验证降级判据。"""
    return _ok(
        "stats",
        {
            "total": total,
            "slow": {"count": slow},
            "latency_ms": {"p95": 0.0, "max": max_ms},
        },
    )


def test_missing_detail_with_errors_present_is_no_action():
    """stats 已带 5xx 计数时，明细缺失不构成阻断。"""
    results = {"stats": _stats(50, 0, max_ms=500.0)}
    verdict = judge(results, 3000, history_minutes=30)
    assert verdict.tier is Tier.NO_ACTION


def test_missing_detail_without_errors_is_watch():
    """stats 不带 5xx 统计且没有明细 ⇒ 无法确认无 5xx，不得判无需处理。"""
    results = {"stats": _stats_without_errors(50, 0, 500.0)}
    verdict = judge(results, 3000, history_minutes=30)
    assert verdict.tier is Tier.WATCH
    assert "明细缺失" in verdict.reason


def test_failed_detail_with_errors_present_is_no_action():
    """明细失败但 stats 有 5xx 计数 ⇒ 不阻断判定。"""
    results = {
        "stats": _stats(50, 0, max_ms=500.0),
        "detail": EvidenceResult("detail", EvidenceStatus.FAILED, None, 10, "timeout"),
    }
    assert judge(results, 3000, history_minutes=30).tier is Tier.NO_ACTION


def test_above_severe_is_watch():
    results = {"stats": _stats(2, 1, p95=200.0, max_ms=5000.0), "detail": _rows(200)}
    assert judge(results, 3000).tier is Tier.WATCH


def test_p95_above_severe_but_max_below_is_no_action():
    """严重阈值只看窗口最大耗时，p95 仅作展示。"""
    results = {
        "stats": _stats(2, 1, p95=5000.0, max_ms=1000.0),
        "detail": _rows(200),
    }
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.NO_ACTION
    assert verdict.severe_hit is False


def test_missing_stats_is_watch():
    verdict = judge({"detail": _rows(200)}, 3000)
    assert verdict.tier is Tier.WATCH
    assert verdict.sample_size == 0


def test_failed_stats_is_watch():
    results = {
        "stats": EvidenceResult("stats", EvidenceStatus.FAILED, None, 10, "timeout")
    }
    assert judge(results, 3000).tier is Tier.WATCH


def test_empty_stats_is_watch():
    results = {
        "stats": EvidenceResult("stats", EvidenceStatus.EMPTY, None, 10, "no_data")
    }
    assert judge(results, 3000).tier is Tier.WATCH


def test_empty_stats_no_rows_means_log_break():
    """no_rows 的 empty 表示查询成功但无行 ⇒ 日志断流，而非取数工具坏了。"""
    results = {
        "stats": EvidenceResult(
            "stats", EvidenceStatus.EMPTY, None, 10, "empty:no_rows"
        )
    }
    assert judge(results, 3000).reason == "日志断流或服务不可用"


def test_empty_stats_other_reason_means_missing_evidence():
    results = {
        "stats": EvidenceResult("stats", EvidenceStatus.EMPTY, None, 10, "no_data")
    }
    assert judge(results, 3000).reason == "必需证据缺失"


def test_stats_data_not_dict_is_watch():
    assert judge({"stats": _ok("stats", [{"total": 3}])}, 3000).tier is Tier.WATCH


def test_severe_boundary_hits_watch():
    results = {"stats": _stats(2, 1, max_ms=3000.0), "detail": _rows(200)}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.WATCH
    assert verdict.severe_hit is True


def test_slow_count_zero_is_no_action():
    results = {"stats": _stats(5, 0, max_ms=200.0), "detail": _rows(200, 200)}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.NO_ACTION
    assert verdict.severe_hit is False


def test_sample_size_is_total():
    results = {"stats": _stats(7, 0, max_ms=200.0), "detail": _rows(200)}
    assert judge(results, 3000).sample_size == 7


def test_missing_slow_section_defaults_to_zero():
    results = {"stats": _ok("stats", {"total": 3, "latency_ms": {"max": 100.0}})}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_missing_latency_section_defaults_to_zero():
    results = {"stats": _ok("stats", {"total": 3, "slow": {"count": 0}})}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.NO_ACTION
    assert verdict.severe_hit is False


def test_string_status_5xx_is_need_action():
    results = {"stats": _stats(3, 0, max_ms=100.0), "detail": _rows("502")}
    assert judge(results, 3000).tier is Tier.NEED_ACTION


def test_string_status_4xx_is_no_action():
    results = {"stats": _stats(3, 0, max_ms=100.0), "detail": _rows("404")}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_unparsable_status_is_ignored():
    results = {"stats": _stats(3, 0, max_ms=100.0), "detail": _rows("error")}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_null_status_is_ignored():
    results = {"stats": _stats(3, 0, max_ms=100.0), "detail": _rows(None)}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_4xx_status_is_not_need_action():
    results = {"stats": _stats(3, 0, max_ms=100.0), "detail": _rows(499)}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_missing_detail_with_small_sample_still_judges():
    """样本低于告警口径最小样本时，缺失明细不阻塞无需处理判定。"""
    assert judge({"stats": _stats(2, 1, max_ms=1057.0)}, 3000).tier is Tier.NO_ACTION


def test_failed_detail_with_small_sample_is_ignored():
    results = {
        "stats": _stats(2, 1, max_ms=1057.0),
        "detail": EvidenceResult("detail", EvidenceStatus.FAILED, None, 10, "timeout"),
    }
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_detail_not_list_is_treated_as_missing_small_sample():
    results = {
        "stats": _stats(2, 1, max_ms=1057.0),
        "detail": _ok("detail", {"status": 502}),
    }
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_detail_non_dict_rows_are_filtered():
    results = {
        "stats": _stats(2, 1, max_ms=1057.0),
        "detail": _ok("detail", ["oops", {"status": 200}]),
    }
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_5xx_wins_over_severe_latency():
    results = {"stats": _stats(10, 9, max_ms=9000.0), "detail": _rows(200, 503)}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.NEED_ACTION
    assert verdict.severe_hit is False


def test_no_recurrence_and_large_sample_is_watch():
    """history=0 且 total>=20：无正向证据，不得判无需处理。"""
    results = {"stats": _stats(30, 1, max_ms=1200.0), "detail": _rows(200)}
    verdict = judge(results, 3000, history_minutes=0)
    assert verdict.tier is Tier.WATCH
    assert "正向证据" in verdict.reason


def test_no_history_data_and_large_sample_is_watch():
    """history 未取到（None）同样不算正向证据。"""
    results = {"stats": _stats(30, 1, max_ms=1200.0), "detail": _rows(200)}
    assert judge(results, 3000).tier is Tier.WATCH


def test_recurrence_allows_no_action():
    results = {"stats": _stats(30, 1, max_ms=1200.0), "detail": _rows(200)}
    assert judge(results, 3000, history_minutes=7).tier is Tier.NO_ACTION


def test_small_sample_is_positive_evidence():
    results = {"stats": _stats(19, 1, max_ms=1200.0), "detail": _rows(200)}
    assert judge(results, 3000, history_minutes=0).tier is Tier.NO_ACTION


def _impact(pairs) -> EvidenceResult:
    """构造 impact 证据：(pv, base_pv) 列表。"""
    return _ok(
        "impact",
        {
            "endpoints": [
                {"uri": "/welife-pay/pay/createOrder", "pv": pv, "base_pv": base}
                for pv, base in pairs
            ]
        },
    )


def test_business_drop_blocks_no_action():
    """业务量较昨日同时段腰斩时不得判无需处理。"""
    results = {
        "stats": _stats(7, 0, 708.0, 754.0),
        "impact": _impact([(7, 14)]),
    }
    verdict = judge(results, 3000, business_min_base_pv=10)
    assert verdict.tier is Tier.WATCH
    assert "业务量较昨日同时段下降 50%" in verdict.reason
    assert "createOrder" in verdict.reason


def test_business_drop_keeps_slow_reason():
    """同时有多条慢请求时，两条依据都保留。"""
    results = {
        "stats": _stats(40, 5, 900.0, 1200.0),
        "impact": _impact([(10, 40)]),
    }
    verdict = judge(results, 3000, business_min_base_pv=10)
    assert verdict.tier is Tier.WATCH
    assert "5 条慢请求" in verdict.reason
    assert "下降 75%" in verdict.reason


def test_business_drop_ignored_below_min_base():
    """基线样本量过小不算骤降（避免小样本噪声）。"""
    results = {
        "stats": _stats(1, 0, 300.0, 300.0),
        "impact": _impact([(1, 3)]),
    }
    assert judge(results, 3000, business_min_base_pv=10).tier is Tier.NO_ACTION


def test_business_stable_is_no_action():
    """业务量正常时不影响原判定。"""
    results = {
        "stats": _stats(30, 0, 700.0, 900.0),
        "impact": _impact([(30, 31)]),
    }
    assert (
        judge(results, 3000, history_minutes=1, business_min_base_pv=10).tier
        is Tier.NO_ACTION
    )


def test_business_impact_failed_does_not_block():
    """impact 取不到时判定照旧。"""
    results = {
        "stats": _stats(30, 0, 700.0, 900.0),
        "impact": EvidenceResult("impact", EvidenceStatus.FAILED, None, 5, "timeout"),
    }
    assert judge(results, 3000, history_minutes=1).tier is Tier.NO_ACTION
