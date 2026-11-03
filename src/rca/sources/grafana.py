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
            return await self._client.post_json(_QUERY_PATH, body)
        except GrafanaError as exc:
            raise SourceFailedError(f"grafana_error:{exc.status}") from exc

    async def latency_stats(
        self, uri: str, start_ts: int, end_ts: int, slow_ms: int = 2000
    ) -> dict:
        """按节点返回计数、p95 与慢请求数，并给出总量。"""
        sql = (
            "* | select upstream_addr, count(*) as cnt, "
            "approx_percentile(upstream_response_time, 0.95) * 1000 as p95, "
            f"count_if(upstream_response_time * 1000 >= {slow_ms}) as slow_cnt "
            f"where request_uri = '{uri}' group by upstream_addr"
        )
        payload = await self._query(sql, start_ts, end_ts)
        counts = parse_frame_column(payload, "A", "cnt")
        if not counts:
            raise SourceEmptyError("no_rows")
        p95_values = parse_frame_column(payload, "A", "p95")
        slow_values = parse_frame_column(payload, "A", "slow_cnt")
        addrs = parse_frame_column(payload, "A", "upstream_addr")
        p95 = 0.0
        if p95_values:
            p95 = float(max(p95_values))
        slow_count = 0
        if slow_values:
            slow_count = int(sum(slow_values))
        return {
            "total": int(sum(counts)),
            "latency_ms": {"p95": p95},
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
            f"where request_uri = '{uri}' order by ts desc limit {limit}"
        )
        payload = await self._query(sql, start_ts, end_ts)
        timestamps = parse_frame_column(payload, "A", "ts")
        if not timestamps:
            raise SourceEmptyError("no_rows")
        addrs = parse_frame_column(payload, "A", "upstream_addr")
        rt_values = parse_frame_column(payload, "A", "rt_ms")
        statuses = parse_frame_column(payload, "A", "status")
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
                }
            )
        return rows

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """对比核心接口当前窗口与昨日同时段的请求量。"""
        uris = self._cfg.business_uris
        if not uris:
            raise SourceEmptyError("no_business_uris")
        clauses = " or ".join(f"request_uri = '{uri}'" for uri in uris)
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
