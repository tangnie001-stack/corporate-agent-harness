"""测试用固定数据源：按方法名返回预置结构，缺失视为 empty。"""

from __future__ import annotations

from src.rca.sources.base import SourceEmptyError


class FixtureLogSource:
    """从 dict 读取数据；仅用于测试与非生产环境。"""

    def __init__(self, data: dict) -> None:
        self._data = data

    def _get(self, key: str):
        """取预置数据；不存在抛 empty。"""
        if key not in self._data:
            raise SourceEmptyError(key)
        return self._data[key]

    async def latency_stats(
        self, uri: str, start_ts: int, end_ts: int, slow_ms: int = 2000
    ) -> dict:
        """返回预置的窗口统计。"""
        return dict(self._get("latency_stats"))

    async def request_detail(
        self, uri: str, start_ts: int, end_ts: int, limit: int = 100
    ) -> list:
        """返回预置的请求明细。"""
        return list(self._get("request_detail"))

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """返回预置的业务影响。"""
        return dict(self._get("business_impact"))
