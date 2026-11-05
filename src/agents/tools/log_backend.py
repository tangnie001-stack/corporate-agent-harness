"""MCP 日志后端：把 McpLogSource 的结果压成模型可读文本（裁剪 + 脱敏）。

工具层只做参数校验、白名单、超时与降级文案；本模块负责取数细节与输出形态：
- 聚合结果压成 ≤MAX_LATENCY_LINES 行摘要（样本量 / 分位 / 慢请求 / 节点分布 / 5xx）
- 明细按耗时倒序、最多 limit 行，末行标注总命中数
- 节点地址不进入输出：明细按排名给 node-N 别名，其它文本统一兜底替换 IPv4

异常语义与取数层一致：查到但无数据抛 SourceEmptyError，调用失败抛 SourceFailedError，
本模块不吞异常，由工具层翻译成「无请求记录」或「查询失败」。
"""

from __future__ import annotations

import re
import time

from src.config.log_tools import MAX_LATENCY_LINES, MAX_LIMIT, LogToolTexts
from src.rca.sources.mcp import McpLogSource

_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")


def _as_int(value: object) -> int:
    """安全转整数；非法值按 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _text(value: object) -> str:
    """取单行文本：非字符串按空串；换行压成空格，保证行数上限不被撑破。"""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _mask(text: str) -> str:
    """把漏出的 IPv4[:port] 替换成节点别名（与 RCA 卡片同口径）。"""
    return _IPV4.sub(LogToolTexts.MASKED_NODE, text)


def format_latency(data: dict) -> str:
    """把聚合结果压成 ≤MAX_LATENCY_LINES 行摘要。

    Args:
        data: McpLogSource.latency_stats 的结构化结果

    Returns:
        摘要文本；缺失的字段不输出对应行，避免把「没采到」写成 0
    """
    lines = [LogToolTexts.LATENCY_TOTAL.format(total=_as_int(data.get("total")))]
    latency = data.get("latency_ms")
    if isinstance(latency, dict):
        parts = []
        for key in ("p50", "p95", "max"):
            if key in latency:
                parts.append(key + " " + str(_as_int(latency[key])) + "ms")
        if parts:
            lines.append(LogToolTexts.LATENCY_SPAN.format(parts=" / ".join(parts)))
    slow = data.get("slow")
    if isinstance(slow, dict):
        lines.append(LogToolTexts.LATENCY_SLOW.format(count=_as_int(slow.get("count"))))
    nodes = data.get("by_node")
    if isinstance(nodes, list) and nodes:
        parts = []
        for index, node in enumerate(nodes, start=1):
            if not isinstance(node, dict):
                continue
            parts.append(
                LogToolTexts.NODE_PART.format(
                    index=index, count=_as_int(node.get("cnt"))
                )
            )
        if parts:
            lines.append(LogToolTexts.LATENCY_NODES.format(parts="；".join(parts)))
    errors = data.get("errors")
    if isinstance(errors, dict):
        lines.append(
            LogToolTexts.LATENCY_ERRORS.format(count=_as_int(errors.get("5xx")))
        )
    return _mask("\n".join(lines[:MAX_LATENCY_LINES]))


def format_detail(rows: list, limit: int = MAX_LIMIT) -> str:
    """明细最多 limit 行，按耗时倒序，节点用排名别名。

    Args:
        rows: McpLogSource.request_detail 的行列表
        limit: 行数上限（工具层已校验 1-MAX_LIMIT）

    Returns:
        每行「时间 node-N 耗时 状态/上游状态 [方法]」；命中数超过 limit 时末行标注总数
    """
    valid = [row for row in rows if isinstance(row, dict)]
    ordered = sorted(valid, key=lambda row: _as_int(row.get("rt_ms")), reverse=True)
    lines = []
    for index, row in enumerate(ordered[:limit], start=1):
        method = _text(row.get("method"))
        suffix = ""
        if method != "":
            suffix = " " + method
        lines.append(
            _text(row.get("ts"))
            + " "
            + LogToolTexts.NODE_ALIAS.format(index=index)
            + " "
            + str(_as_int(row.get("rt_ms")))
            + "ms "
            + str(_as_int(row.get("status")))
            + "/"
            + str(_as_int(row.get("upstream_status")))
            + suffix
        )
    if len(ordered) > limit:
        lines.append(
            LogToolTexts.DETAIL_TRUNCATED.format(total=len(ordered), shown=limit)
        )
    return _mask("\n".join(lines))


def _window(minutes: int) -> tuple[int, int]:
    """把回看分钟数换算为 epoch 半开区间 [now-minutes, now)。

    Args:
        minutes: 回看分钟数（工具层已校验范围）

    Returns:
        (start_ts, end_ts)；end 取当前整秒，区间含 start 不含 end
    """
    end_ts = int(time.time())
    return end_ts - minutes * 60, end_ts


class McpLogToolBackend:
    """把日志工具调用转成 McpLogSource 查询并格式化。"""

    def __init__(self, source: McpLogSource) -> None:
        """初始化后端。

        Args:
            source: MCP 取数客户端（超时由工具层统一控制）
        """
        self._source = source

    async def latency(self, uri: str, minutes: int, slow_ms: int) -> str:
        """聚合查询并格式化。

        Args:
            uri: 接口路径（工具层已完成白名单校验）
            minutes: 回看分钟数
            slow_ms: 慢请求阈值（毫秒）

        Returns:
            ≤MAX_LATENCY_LINES 行摘要
        """
        start_ts, end_ts = _window(minutes)
        data = await self._source.latency_stats(uri, start_ts, end_ts, slow_ms=slow_ms)
        return format_latency(data)

    async def detail(self, uri: str, minutes: int, limit: int) -> str:
        """明细查询并格式化。

        Args:
            uri: 接口路径（工具层已完成白名单校验）
            minutes: 回看分钟数
            limit: 返回行数上限

        Returns:
            最多 limit 行明细（超限时带总命中数末行）
        """
        start_ts, end_ts = _window(minutes)
        rows = await self._source.request_detail(uri, start_ts, end_ts, limit=limit)
        return format_detail(rows, limit)
