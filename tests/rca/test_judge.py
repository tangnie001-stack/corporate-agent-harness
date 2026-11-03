from src.rca.judge import judge
from src.rca.models import EvidenceResult, EvidenceStatus, Tier


def _ok(eid: str, data) -> EvidenceResult:
    return EvidenceResult(eid, EvidenceStatus.OK, data, 10)


def _stats(total: int, slow: int, p95: float = 0.0) -> EvidenceResult:
    return _ok(
        "stats", {"total": total, "slow": {"count": slow}, "latency_ms": {"p95": p95}}
    )


def _rows(*statuses) -> EvidenceResult:
    return _ok("detail", [{"status": status} for status in statuses])


def test_zero_sample_is_watch():
    results = {"stats": _ok("stats", {"total": 0, "slow": {"count": 0}, "by_node": []})}
    assert judge(results, 3000).tier is Tier.WATCH


def test_single_slow_request_is_no_action():
    results = {
        "stats": _ok(
            "stats", {"total": 2, "slow": {"count": 1}, "latency_ms": {"p95": 1057.0}}
        ),
        "detail": _ok("detail", [{"status": 200}, {"status": 200}]),
    }
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_all_requests_slow_is_watch():
    results = {
        "stats": _ok(
            "stats", {"total": 2, "slow": {"count": 2}, "latency_ms": {"p95": 1800.0}}
        ),
        "detail": _ok("detail", [{"status": 200}, {"status": 200}]),
    }
    assert judge(results, 3000).tier is Tier.WATCH


def test_5xx_is_need_action():
    results = {
        "stats": _ok("stats", {"total": 10, "slow": {"count": 0}, "by_node": []}),
        "detail": _ok("detail", [{"status": 502}]),
    }
    assert judge(results, 3000).tier is Tier.NEED_ACTION


def test_above_severe_is_watch():
    results = {
        "stats": _ok(
            "stats", {"total": 2, "slow": {"count": 1}, "latency_ms": {"p95": 5000.0}}
        ),
        "detail": _ok("detail", [{"status": 200}]),
    }
    assert judge(results, 3000).tier is Tier.WATCH


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


def test_stats_data_not_dict_is_watch():
    assert judge({"stats": _ok("stats", [{"total": 3}])}, 3000).tier is Tier.WATCH


def test_severe_boundary_hits_watch():
    results = {"stats": _stats(2, 1, 3000.0), "detail": _rows(200)}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.WATCH
    assert verdict.severe_hit is True


def test_slow_count_zero_is_no_action():
    results = {"stats": _stats(5, 0, 200.0), "detail": _rows(200, 200)}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.NO_ACTION
    assert verdict.severe_hit is False


def test_sample_size_is_total():
    results = {"stats": _stats(7, 0, 200.0), "detail": _rows(200)}
    assert judge(results, 3000).sample_size == 7


def test_missing_slow_section_defaults_to_zero():
    results = {"stats": _ok("stats", {"total": 3, "latency_ms": {"p95": 100.0}})}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_missing_latency_section_defaults_to_zero():
    results = {"stats": _ok("stats", {"total": 3, "slow": {"count": 0}})}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.NO_ACTION
    assert verdict.severe_hit is False


def test_string_status_5xx_is_need_action():
    results = {"stats": _stats(3, 0, 100.0), "detail": _rows("502")}
    assert judge(results, 3000).tier is Tier.NEED_ACTION


def test_string_status_4xx_is_no_action():
    results = {"stats": _stats(3, 0, 100.0), "detail": _rows("404")}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_unparsable_status_is_ignored():
    results = {"stats": _stats(3, 0, 100.0), "detail": _rows("error")}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_null_status_is_ignored():
    results = {"stats": _stats(3, 0, 100.0), "detail": _rows(None)}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_4xx_status_is_not_need_action():
    results = {"stats": _stats(3, 0, 100.0), "detail": _rows(499)}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_missing_detail_still_judges():
    assert judge({"stats": _stats(2, 1, 1057.0)}, 3000).tier is Tier.NO_ACTION


def test_failed_detail_is_ignored():
    results = {
        "stats": _stats(2, 1, 1057.0),
        "detail": EvidenceResult("detail", EvidenceStatus.FAILED, None, 10, "timeout"),
    }
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_detail_not_list_is_ignored():
    results = {"stats": _stats(2, 1, 1057.0), "detail": _ok("detail", {"status": 502})}
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_detail_non_dict_rows_are_filtered():
    results = {
        "stats": _stats(2, 1, 1057.0),
        "detail": _ok("detail", ["oops", {"status": 200}]),
    }
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_5xx_wins_over_severe_latency():
    results = {"stats": _stats(10, 9, 9000.0), "detail": _rows(200, 503)}
    verdict = judge(results, 3000)
    assert verdict.tier is Tier.NEED_ACTION
    assert verdict.severe_hit is False
