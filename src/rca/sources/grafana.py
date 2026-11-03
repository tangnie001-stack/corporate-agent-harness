"""Grafana ds/query 取数实现。

实测：成功查询的 status 也可能是 500，数据在 frames[].data.values（列式）。
故成功判定只看 frames 与 values 是否非空，不看 status。
"""

from __future__ import annotations

from src.config.rca import RcaConfig
from src.infra.grafana_client import GrafanaClient, GrafanaError
from src.rca.models import AlertContext
from src.rca.sources.base import SourceEmptyError, SourceFailedError

_QUERY_PATH = "/api/ds/query"


def escape_sql_literal(value: str) -> str:
    """转义 SLS SQL 单引号字面量（单引号翻倍）。"""
    return value.replace("'", "''")


def ensure_query_ok(payload: object) -> None:
    """查询失败时 Grafana 在 payload 里回 error（顶层或 results.<refId>）。

    frames 为空既可能是无数据也可能是查询失败，故先看 error，再由调用方判空。
    """
    if not isinstance(payload, dict):
        return
    if payload.get("error"):
        raise SourceFailedError("grafana_error")
    results = payload.get("results")
    if not isinstance(results, dict):
        return
    for result in results.values():
        if isinstance(result, dict) and result.get("error"):
            raise SourceFailedError("grafana_error")


def parse_frame_column(payload: dict, ref_id: str, field: str) -> list:
    """按字段名从列式 frames 中取值；不存在返回空列表。"""
    result = payload.get("results", {}).get(ref_id, {})
    frames = result.get("frames", [])
    if not frames:
        return []
    schema_fields = frames[0].get("schema", {}).get("fields", [])
    values = frames[0].get("data", {}).get("values", [])
    for index, item in enumerate(schema_fields):
        if item.get("name") == field and index < len(values):
            return list(values[index])
    return []


class GrafanaLogSource:
    """经 Grafana 代理查询 SLS。"""

    def __init__(
        self, client: GrafanaClient, context: AlertContext, cfg: RcaConfig
    ) -> None:
        self._client = client
        self._context = context
        self._cfg = cfg

    async def _query(self, sql: str, start_ts: int, end_ts: int) -> dict:
        """执行一条 SLS SQL 并返回原始 payload。"""
        if self._context.datasource_uid is None:
            raise SourceFailedError("datasource_uid_missing")
        body = {
            "from": str(start_ts * 1000),
            "to": str(end_ts * 1000),
            "queries": [
                {
                    "refId": "A",
                    "datasource": {
                        "type": "aliyun-log-service-datasource",
                        "uid": self._context.datasource_uid,
                    },
                    "logstore": self._context.logstore or "",
                    "query": sql,
                    "type": "all",
                    "xcol": "t",
                    "ycol": "",
                }
            ],
        }
        try:
            payload = await self._client.post_json(_QUERY_PATH, body)
        except GrafanaError as exc:
            raise SourceFailedError(f"grafana_error:{exc.status}") from exc
        ensure_query_ok(payload)
        return payload

    async def latency_stats(
        self, uri: str, start_ts: int, end_ts: int, slow_ms: int = 2000
    ) -> dict:
        """按节点返回计数、p95、最大耗时、5xx 数与慢请求数，并给出总量。

        慢请求阈值以告警规则的阈值为准（context.threshold 非空即覆盖入参
        默认值），避免所有接口共用 2000ms 这一与告警口径无关的默认值。
        """
        if self._context.threshold is not None:
            slow_ms = int(self._context.threshold)
        sql = (
            "* | select upstream_addr, count(*) as cnt, "
            "approx_percentile(upstream_response_time, 0.95) * 1000 as p95, "
            "max(upstream_response_time) * 1000 as max_ms, "
            f"count_if(upstream_response_time * 1000 >= {slow_ms}) as slow_cnt, "
            "count_if(status >= 500) as c5xx "
            f"where request_uri = '{escape_sql_literal(uri)}' group by upstream_addr"
        )
        payload = await self._query(sql, start_ts, end_ts)
        counts = parse_frame_column(payload, "A", "cnt")
        if not counts:
            raise SourceEmptyError("no_rows")
        p95_values = parse_frame_column(payload, "A", "p95")
        max_values = parse_frame_column(payload, "A", "max_ms")
        slow_values = parse_frame_column(payload, "A", "slow_cnt")
        c5xx_values = parse_frame_column(payload, "A", "c5xx")
        addrs = parse_frame_column(payload, "A", "upstream_addr")
        p95 = 0.0
        if p95_values:
            p95 = float(max(p95_values))
        max_ms = 0.0
        if max_values:
            max_ms = float(max(max_values))
        slow_count = 0
        if slow_values:
            slow_count = int(sum(slow_values))
        c5xx = 0
        if c5xx_values:
            c5xx = int(sum(c5xx_values))
        return {
            "total": int(sum(counts)),
            "latency_ms": {"p95": p95, "max": max_ms},
            "errors": {"5xx": c5xx},
            "slow": {"threshold": slow_ms, "count": slow_count},
            "by_node": [
                {"addr": addr, "cnt": int(cnt)} for addr, cnt in zip(addrs, counts)
            ],
        }

    async def request_detail(
        self, uri: str, start_ts: int, end_ts: int, limit: int = 100
    ) -> list:
        """返回窗口内的请求明细（白名单字段）。"""
        sql = (
            "* | select __time__ as ts, upstream_addr, request_time * 1000 as rt_ms, "
            "upstream_response_time * 1000 as urt_ms, status, upstream_status "
            f"where request_uri = '{escape_sql_literal(uri)}' order by ts desc limit {limit}"
        )
        payload = await self._query(sql, start_ts, end_ts)
        timestamps = parse_frame_column(payload, "A", "ts")
        if not timestamps:
            raise SourceEmptyError("no_rows")
        addrs = parse_frame_column(payload, "A", "upstream_addr")
        rt_values = parse_frame_column(payload, "A", "rt_ms")
        statuses = parse_frame_column(payload, "A", "status")
        upstream_statuses = parse_frame_column(payload, "A", "upstream_status")
        rows = []
        for index in range(len(timestamps)):
            rows.append(
                {
                    "ts": timestamps[index],
                    "upstream_addr": addrs[index] if index < len(addrs) else "",
                    "request_time_ms": rt_values[index]
                    if index < len(rt_values)
                    else 0,
                    "status": statuses[index] if index < len(statuses) else 0,
                    "upstream_status": upstream_statuses[index]
                    if index < len(upstream_statuses)
                    else None,
                }
            )
        return rows

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """对比核心接口当前窗口与昨日同时段的请求量。"""
        uris = self._cfg.business_uris
        if not uris:
            raise SourceEmptyError("no_business_uris")
        clauses = " or ".join(
            f"request_uri = '{escape_sql_literal(uri)}'" for uri in uris
        )
        sql = f"* | select request_uri, count(*) as cnt where {clauses} group by request_uri"
        current = await self._query(sql, start_ts, end_ts)
        baseline = await self._query(sql, start_ts - 86400, end_ts - 86400)
        current_counts = parse_frame_column(current, "A", "cnt")
        if not current_counts:
            raise SourceEmptyError("no_rows")
        endpoints = parse_frame_column(current, "A", "request_uri")
        base_counts = parse_frame_column(baseline, "A", "cnt")
        base_map = dict(
            zip(parse_frame_column(baseline, "A", "request_uri"), base_counts)
        )
        rows = []
        for uri, count in zip(endpoints, current_counts):
            base = base_map.get(uri, 0)
            rows.append({"uri": uri, "pv": int(count), "base_pv": int(base)})
        return {"endpoints": rows}
