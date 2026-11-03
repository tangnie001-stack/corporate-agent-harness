"""三档判定：档位由代码确定性计算，模型不参与。

无需处理必须同时满足：样本量 >= 1、无 5xx、慢请求 <= 1、最大耗时 < 严重阈值。
任何一条不满足都向上落档；证据不足落"建议观察"。
"""

from __future__ import annotations

from src.rca.models import EvidenceResult, EvidenceStatus, Tier, TriageVerdict


def _stats(results: dict[str, EvidenceResult]) -> dict | None:
    """取 stats 证据的数据；缺失或失败返回 None。"""
    result = results.get("stats")
    if result is None or result.status is not EvidenceStatus.OK:
        return None
    if not isinstance(result.data, dict):
        return None
    return result.data


def _rows(results: dict[str, EvidenceResult]) -> list[dict]:
    """取明细证据的行；缺失返回空列表。"""
    result = results.get("detail")
    if result is None or result.status is not EvidenceStatus.OK:
        return []
    if not isinstance(result.data, list):
        return []
    return [row for row in result.data if isinstance(row, dict)]


def _is_server_error(status: object) -> bool:
    """判断是否为 5xx。"""
    if isinstance(status, int):
        return status >= 500
    if isinstance(status, str):
        try:
            return int(status) >= 500
        except ValueError:
            return False
    return False


def judge(results: dict[str, EvidenceResult], severe_latency_ms: int) -> TriageVerdict:
    """按证据计算档位与定档依据。"""
    stats = _stats(results)
    if stats is None:
        return TriageVerdict(Tier.WATCH, "必需证据缺失", 0, False)

    total = int(stats.get("total", 0))
    if total == 0:
        return TriageVerdict(Tier.WATCH, "日志断流或服务不可用", 0, False)

    server_errors = [
        row for row in _rows(results) if _is_server_error(row.get("status"))
    ]
    if server_errors:
        return TriageVerdict(
            Tier.NEED_ACTION, f"出现 {len(server_errors)} 条 5xx", total, False
        )

    slow = stats.get("slow") or {}
    slow_count = int(slow.get("count", 0))
    p95 = float((stats.get("latency_ms") or {}).get("p95", 0.0))
    severe_hit = p95 >= severe_latency_ms

    if severe_hit:
        return TriageVerdict(Tier.WATCH, "耗时超过严重阈值", total, True)
    if slow_count <= 1:
        return TriageVerdict(Tier.NO_ACTION, "孤立慢请求，未超严重阈值", total, False)
    return TriageVerdict(Tier.WATCH, f"{slow_count} 条慢请求", total, False)
