"""两个只读日志查询工具（仅 log 能力档装配）。

后端由启动期（lifespan）注入：生产为 MCP，测试注入假实现；未注入或调用失败时
统一返回「查询失败」，**不得**表述为「未发现异常」。工具内部不重试，单次调用
超时即失败（重试权交给模型换查询）。

工具只做参数校验、白名单校验、时间窗换算超时与降级文案；取数细节、输出裁剪与
脱敏由后端负责。
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from langchain_core.tools import BaseTool, tool
from loguru import logger

from src.config.log_tools import (
    DEFAULT_MINUTES,
    DEFAULT_SLOW_MS,
    LOG_TOOL_TIMEOUT_SECONDS,
    MAX_LIMIT,
    MAX_MINUTES,
    MIN_MINUTES,
    LogToolTexts,
)
from src.core.logging import encode_value
from src.infra.llm.request_context import current_request_ctx
from src.rca.sources.base import SourceEmptyError, SourceFailedError


class LogToolBackend(Protocol):
    """日志取数后端（生产为 MCP 客户端；测试注入假实现）。"""

    async def latency(self, uri: str, minutes: int, slow_ms: int) -> str:
        """返回聚合摘要文本；无数据抛 SourceEmptyError，失败抛 SourceFailedError。"""
        ...

    async def detail(self, uri: str, minutes: int, limit: int) -> str:
        """返回明细文本；无数据抛 SourceEmptyError，失败抛 SourceFailedError。"""
        ...


_backend: LogToolBackend | None = None
_whitelist: tuple[str, ...] = ()


def configure(backend: LogToolBackend | None, whitelist: tuple[str, ...]) -> None:
    """注入后端与接口白名单（启动期一次；None 表示日志服务不可用）。

    Args:
        backend: 取数后端；None = 启动期连接失败，工具进入降级态（返回查询失败）
        whitelist: 可查询接口（精确匹配）；空 = 全部拒绝（启动期 fail-fast 在 lifespan）
    """
    global _backend, _whitelist
    _backend = backend
    _whitelist = whitelist


def _session_id() -> str:
    """当前轮会话 id（日志关联用）；无请求上下文返回空串。"""
    ctx = current_request_ctx.get()
    if ctx is None:
        return ""
    return ctx.session_id


def _minutes_error(minutes: int) -> str | None:
    """校验回看窗口；越界返回参数错误文案，合法返回 None。"""
    if minutes < MIN_MINUTES or minutes > MAX_MINUTES:
        return LogToolTexts.MINUTES_OUT_OF_RANGE.format(
            min_minutes=MIN_MINUTES, max_minutes=MAX_MINUTES
        )
    return None


def _limit_error(limit: int) -> str | None:
    """校验明细行数上限；越界返回参数错误文案，合法返回 None。"""
    if limit < 1 or limit > MAX_LIMIT:
        return LogToolTexts.LIMIT_OUT_OF_RANGE.format(max_limit=MAX_LIMIT)
    return None


def _uri_error(uri: str) -> str | None:
    """白名单校验：命中返回 None，未命中返回错误文案并列出可选接口。"""
    if uri in _whitelist:
        return None
    if _whitelist:
        options = "、".join(_whitelist)
    else:
        options = LogToolTexts.NO_URI_CONFIGURED
    return LogToolTexts.URI_NOT_ALLOWED.format(uri=uri, options=options)


def _failure_text(exc: BaseException, minutes: int) -> str:
    """把异常映射为失败文案（空结果 ≠ 失败）。"""
    if isinstance(exc, SourceEmptyError):
        return LogToolTexts.NO_ROWS.format(minutes=minutes)
    if isinstance(exc, TimeoutError):
        return LogToolTexts.TIMEOUT
    return LogToolTexts.FAILED_PREFIX + str(exc)


@tool("sls_query_latency")
async def sls_query_latency(
    uri: str, minutes: int = DEFAULT_MINUTES, slow_ms: int = DEFAULT_SLOW_MS
) -> str:
    """查询某接口最近 N 分钟的分位耗时、慢请求与节点分布。

    何时调用：被问到"接口慢不慢""有没有错误""哪个节点异常"时；只查配置白名单内的接口。

    Args:
        uri: 接口路径（必须与白名单精确相等）
        minutes: 回看分钟数（1-360，默认 15）
        slow_ms: 慢请求阈值（毫秒，默认 1000）

    Returns:
        不超过 5 行的文本摘要；参数/白名单/服务不可用时返回错误文案
    """
    error = _minutes_error(minutes)
    if error is not None:
        return error
    if _backend is None:
        return LogToolTexts.UNAVAILABLE
    error = _uri_error(uri)
    if error is not None:
        return error
    started = time.monotonic()
    try:
        text = await asyncio.wait_for(
            _backend.latency(uri, minutes, slow_ms), timeout=LOG_TOOL_TIMEOUT_SECONDS
        )
    except (TimeoutError, SourceFailedError, SourceEmptyError) as exc:
        return _failure_text(exc, minutes)
    logger.info(
        "[tool] mcp call tool=sls_query_latency uri={} minutes={} elapsed_ms={} session={}",
        encode_value(uri),
        minutes,
        int((time.monotonic() - started) * 1000),
        encode_value(_session_id()),
    )
    return text


@tool("sls_query_requests")
async def sls_query_requests(
    uri: str, minutes: int = DEFAULT_MINUTES, limit: int = MAX_LIMIT
) -> str:
    """查询某接口最近 N 分钟的请求明细（最多 20 行）。

    何时调用：需要逐条请求证据（时间、节点、耗时、状态码）时；只查配置白名单内的接口。

    Args:
        uri: 接口路径（必须与白名单精确相等）
        minutes: 回看分钟数（1-360，默认 15）
        limit: 返回行数上限（1-20，默认 20）

    Returns:
        每行"时间 节点 耗时 状态 上游状态 方法"；超上限时按耗时倒序截断并标注总数
    """
    error = _minutes_error(minutes)
    if error is not None:
        return error
    error = _limit_error(limit)
    if error is not None:
        return error
    if _backend is None:
        return LogToolTexts.UNAVAILABLE
    error = _uri_error(uri)
    if error is not None:
        return error
    started = time.monotonic()
    try:
        text = await asyncio.wait_for(
            _backend.detail(uri, minutes, limit), timeout=LOG_TOOL_TIMEOUT_SECONDS
        )
    except (TimeoutError, SourceFailedError, SourceEmptyError) as exc:
        return _failure_text(exc, minutes)
    logger.info(
        "[tool] mcp call tool=sls_query_requests uri={} minutes={} elapsed_ms={} rows={} session={}",
        encode_value(uri),
        minutes,
        int((time.monotonic() - started) * 1000),
        len(text.splitlines()),
        encode_value(_session_id()),
    )
    return text


def make_log_tools() -> list[BaseTool]:
    """返回两个只读日志工具（装配给 log 能力档）。

    Returns:
        [sls_query_latency, sls_query_requests]；两者在注册表内以 readonly=True 登记，
        skill 双轴推导据此认定只读
    """
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("sls_query_latency", sls_query_latency, readonly=True)
    registry.register("sls_query_requests", sls_query_requests, readonly=True)
    return registry.enabled_tools()
