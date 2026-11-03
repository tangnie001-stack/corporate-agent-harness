"""三档判定：档位由代码确定性计算，模型不参与。

无需处理必须同时满足：样本量 >= 1、stats 的 5xx 计数为 0、明细可用且无 5xx、
慢请求 <= 1、最大耗时 < 严重阈值，并至少命中一条正向证据（近 7 天反复出现
或样本量低于告警口径最小样本）。任何一条不满足都向上落档；证据不足落"建议观察"。
"""

from __future__ import annotations

from src.rca.models import EvidenceResult, EvidenceStatus, Tier, TriageVerdict

# 告警口径的最小样本量：低于此值不足以谈趋势，作为正向证据之一。
MIN_SAMPLES = 20


def _stats(results: dict[str, EvidenceResult]) -> dict | None:
    """取 stats 证据的数据；缺失或失败返回 None。"""
    result = results.get("stats")
    if result is None or result.status is not EvidenceStatus.OK:
        return None
    if not isinstance(result.data, dict):
        return None
    return result.data


def _no_stats_reason(results: dict[str, EvidenceResult]) -> str:
    """stats 不可用时的定档依据：无行视为断流，其余视为必需证据缺失。"""
    result = results.get("stats")
    if (
        result is not None
        and result.status is EvidenceStatus.EMPTY
        and "no_rows" in result.reason
    ):
        return "日志断流或服务不可用"
    return "必需证据缺失"


def _rows(results: dict[str, EvidenceResult]) -> list[dict]:
    """取明细证据的行；缺失返回空列表。"""
    result = results.get("detail")
    if result is None or result.status is not EvidenceStatus.OK:
        return []
    if not isinstance(result.data, list):
        return []
    return [row for row in result.data if isinstance(row, dict)]


def _detail_available(results: dict[str, EvidenceResult]) -> bool:
    """明细是否真的取到了结构化行（ok 且为列表）。"""
    result = results.get("detail")
    if result is None or result.status is not EvidenceStatus.OK:
        return False
    return isinstance(result.data, list)


def _is_server_error(status: object) -> bool:
    """判断是否为 5xx。"""
    if isinstance(status, bool):
        return False
    if isinstance(status, int):
        return status >= 500
    if isinstance(status, str):
        try:
            return int(status) >= 500
        except ValueError:
            return False
    return False


def _has_positive_evidence(total: int, history_minutes: int | None) -> bool:
    """判无需处理前必须命中的正向证据，二者其一。"""
    if history_minutes is not None and history_minutes >= 1:
        return True
    return total < MIN_SAMPLES


def judge(
    results: dict[str, EvidenceResult],
    severe_latency_ms: int,
    history_minutes: int | None = None,
) -> TriageVerdict:
    """按证据计算档位与定档依据。

    Args:
        results: 证据结果（stats / detail / history）
        severe_latency_ms: 严重耗时阈值
        history_minutes: 近 7 天同告警出现的分钟数；None 表示未取到
    """
    stats = _stats(results)
    if stats is None:
        return TriageVerdict(Tier.WATCH, _no_stats_reason(results), 0, False)

    total = int(stats.get("total", 0))
    if total == 0:
        return TriageVerdict(Tier.WATCH, "日志断流或服务不可用", 0, False)

    errors = stats.get("errors") or {}
    c5xx = int(errors.get("5xx", 0))
    if c5xx > 0:
        return TriageVerdict(Tier.NEED_ACTION, f"窗口出现 {c5xx} 条 5xx", total, False)

    if not _detail_available(results):
        if total >= MIN_SAMPLES:
            return TriageVerdict(Tier.WATCH, "明细缺失，无法确认无 5xx", total, False)
    else:
        server_errors = [
            row
            for row in _rows(results)
            if _is_server_error(row.get("status"))
            or _is_server_error(row.get("upstream_status"))
        ]
        if server_errors:
            return TriageVerdict(
                Tier.NEED_ACTION, f"明细出现 {len(server_errors)} 条 5xx", total, False
            )

    slow = stats.get("slow") or {}
    slow_count = int(slow.get("count", 0))
    max_ms = float((stats.get("latency_ms") or {}).get("max", 0.0))
    severe_hit = max_ms >= severe_latency_ms

    if severe_hit:
        return TriageVerdict(Tier.WATCH, "耗时超过严重阈值", total, True)
    if slow_count <= 1:
        if not _has_positive_evidence(total, history_minutes):
            return TriageVerdict(
                Tier.WATCH, "缺少正向证据，无法确认为噪声", total, False
            )
        return TriageVerdict(Tier.NO_ACTION, "孤立慢请求，未超严重阈值", total, False)
    return TriageVerdict(Tier.WATCH, f"{slow_count} 条慢请求", total, False)
