"""MCP 日志后端：把 McpLogSource 的结果压成模型可读文本（裁剪 + 脱敏）。

工具层只做参数校验、白名单、超时与降级文案；本模块负责取数细节与输出形态：
- 聚合结果压成 ≤MAX_LATENCY_LINES 行摘要（样本量 / 分位 / 慢请求 / 节点分布 / 5xx）
- 明细按服务端取样上限取数、按展示上限裁剪，末行标注本次取样条数
- 节点地址不进入输出：明细按 upstream_addr 首次出现顺序给稳定别名，其它文本统一兜底替换 IPv4
- 缺失字段不输出对应的 0，「没采到」与「确实是 0」在文本上可区分

异常语义与取数层一致：查到但无数据抛 SourceEmptyError，调用失败抛 SourceFailedError，
本模块不吞异常，由工具层翻译成「无请求记录」或「查询失败」。
"""

from __future__ import annotations

import re
import time

from src.config.log_tools import (
    DETAIL_FETCH_LIMIT,
    MAX_LATENCY_LINES,
    MAX_LIMIT,
    MAX_NODE_PARTS,
    LogToolTexts,
)
from src.rca.sources.mcp import McpLogSource

_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")


def _as_int(value: object) -> int | None:
    """安全转整数；布尔、非数值与 None 一律返回 None（缺失 ≠ 0）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


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
        摘要文本；字段缺失或为 None 时不输出对应片段（不把「没采到」写成 0）；
        整段都取不到时返回「无可用统计」
    """
    lines: list[str] = []
    total = _as_int(data.get("total"))
    if total is not None:
        lines.append(LogToolTexts.LATENCY_TOTAL.format(total=total))
    latency = data.get("latency_ms")
    if isinstance(latency, dict):
        parts = []
        for key in ("p50", "p95", "max"):
            value = _as_int(latency.get(key))
            if value is None:
                continue
            parts.append(key + " " + str(value) + "ms")
        if parts:
            lines.append(LogToolTexts.LATENCY_SPAN.format(parts=" / ".join(parts)))
    slow = data.get("slow")
    if isinstance(slow, dict):
        count = _as_int(slow.get("count"))
        if count is not None:
            lines.append(LogToolTexts.LATENCY_SLOW.format(count=count))
    nodes = data.get("by_node")
    if isinstance(nodes, list):
        parts = []
        remaining = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            count = _as_int(node.get("cnt"))
            if count is None:
                continue
            if len(parts) >= MAX_NODE_PARTS:
                remaining += 1
                continue
            parts.append(
                LogToolTexts.NODE_PART.format(index=len(parts) + 1, count=count)
            )
        if remaining > 0:
            parts.append(LogToolTexts.NODE_MORE.format(count=remaining))
        if parts:
            lines.append(LogToolTexts.LATENCY_NODES.format(parts="；".join(parts)))
    errors = data.get("errors")
    if isinstance(errors, dict):
        count = _as_int(errors.get("5xx"))
        if count is not None:
            lines.append(LogToolTexts.LATENCY_ERRORS.format(count=count))
    if not lines:
        return LogToolTexts.NO_STATS
    return _mask("\n".join(lines[:MAX_LATENCY_LINES]))


def _node_aliases(rows: list[dict]) -> dict[str, str]:
    """按 upstream_addr 首次出现顺序建立 地址→node-N 映射。

    Args:
        rows: 待展示的明细行（已按耗时倒序）

    Returns:
        同一地址在全文中始终得到同一别名；地址本身不出现在返回值里
    """
    aliases: dict[str, str] = {}
    for row in rows:
        addr = _text(row.get("upstream_addr"))
        if addr == "" or addr in aliases:
            continue
        aliases[addr] = LogToolTexts.NODE_ALIAS.format(index=len(aliases) + 1)
    return aliases


def _rt_ms(row: dict) -> int:
    """取排序用的耗时（毫秒）；缺失按 0，排在最后。"""
    value = _as_int(row.get("rt_ms"))
    if value is None:
        return 0
    return value


def _int_text(value: object) -> str:
    """渲染数值字段；缺失显示 N/A，不把「没采到」写成 0。"""
    parsed = _as_int(value)
    if parsed is None:
        return LogToolTexts.MISSING_VALUE
    return str(parsed)


def format_detail(rows: list, limit: int = MAX_LIMIT) -> str:
    """明细最多 limit 行，按耗时倒序，节点别名在全文中稳定。

    节点别名按 upstream_addr 首次出现顺序编号（同一节点跨行同别名，地址不输出）；
    该编号与 format_latency 的节点分布编号各自独立，二者不可跨工具对照。

    Args:
        rows: McpLogSource.request_detail 的行列表（服务端已按取数上限取样）
        limit: 展示行数上限（内部再 clamp 到 MAX_LIMIT）

    Returns:
        每行「时间 node-N 耗时 状态/上游状态 [方法]」；取样条数超过 limit 时
        末行标注「本次取样 X 条，仅显示最慢 N 条」（服务端不返回总命中数）
    """
    limit = min(limit, MAX_LIMIT)
    valid = [row for row in rows if isinstance(row, dict)]
    ordered = sorted(valid, key=_rt_ms, reverse=True)
    shown = ordered[:limit]
    aliases = _node_aliases(shown)
    lines = []
    for row in shown:
        alias = aliases.get(_text(row.get("upstream_addr")), LogToolTexts.NODE_UNKNOWN)
        method = _text(row.get("method"))
        suffix = ""
        if method != "":
            suffix = " " + method
        lines.append(
            _text(row.get("ts"))
            + " "
            + alias
            + " "
            + _int_text(row.get("rt_ms"))
            + "ms "
            + _int_text(row.get("status"))
            + "/"
            + _int_text(row.get("upstream_status"))
            + suffix
        )
    if len(ordered) > limit:
        lines.append(
            LogToolTexts.DETAIL_TRUNCATED.format(sampled=len(ordered), shown=limit)
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
            limit: 展示行数上限（工具层已校验 1-MAX_LIMIT）

        Returns:
            最多 limit 行明细；取样条数超过 limit 时带「本次取样 / 仅显示」末行
        """
        start_ts, end_ts = _window(minutes)
        rows = await self._source.request_detail(
            uri, start_ts, end_ts, limit=DETAIL_FETCH_LIMIT
        )
        return format_detail(rows, limit)
