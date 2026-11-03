"""取数源协议与异常。"""

from __future__ import annotations

from typing import Protocol


class SourceEmptyError(LookupError):
    """查到但无数据（对应 empty 态）。"""


class SourceFailedError(RuntimeError):
    """未查到（对应 failed 态）。"""


class LogSource(Protocol):
    """日志取数能力；实现可替换为 fixture / Grafana / 未来的 MCP。"""

    async def latency_stats(
        self, uri: str, start_ts: int, end_ts: int, slow_ms: int = 2000
    ) -> dict:
        """返回窗口样本量、分位、慢请求计数与节点分布。"""
        ...

    async def request_detail(
        self, uri: str, start_ts: int, end_ts: int, limit: int = 100
    ) -> list:
        """返回窗口内请求明细。"""
        ...

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """返回核心接口的请求量与昨日同时段对比。"""
        ...
