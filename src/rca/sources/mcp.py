"""MCP 版取数实现：经 sls-mcp-service 的 Streamable HTTP 端点查询 SLS。

与 GrafanaLogSource 实现同一个 LogSource 协议，由 RcaConfig.rca_source 二选一。

协议要点（2026-11-04 对 beta 环境实测）：
- POST <base_url> 发 JSON-RPC；initialize 的响应头返回 mcp-session-id，后续请求必须带上
- 响应可能是纯 JSON，也可能是 SSE（event: message + data:{...}），两种都要能解析
- 工具结果在 result.content[0].text 里，是一段 JSON 字符串；result.isError 为真表示工具内部失败
- 工具名带 sls_ 前缀；business_impact 必须传 uris

可观测性：每次工具调用写一条 `[rca] mcp call` 日志，并在当前 Langfuse trace 下挂
`mcp:<tool>` span（观测关闭或无 trace 上下文时自动跳过，不影响取数）。

异常语义：无数据抛 SourceEmptyError，调用失败抛 SourceFailedError，供判定层区分 empty / failed。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from langfuse.decorators import langfuse_context
from loguru import logger

from src.config import settings
from src.core.logging import encode_value
from src.infra.llm.trace_context import current_trace_id
from src.rca.sources.base import SourceEmptyError, SourceFailedError

_PROTOCOL_VERSION = "2025-06-18"
_TOOL_LATENCY = "sls_latency_stats"
_TOOL_DETAIL = "sls_request_detail"
_TOOL_IMPACT = "sls_business_impact"


def _parse_body(text: str, content_type: str) -> dict:
    """解析 JSON-RPC 响应：兼容纯 JSON 与 SSE 两种形态。"""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SourceFailedError("invalid_json") from exc
    payload = ""
    for line in stripped.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
    if payload == "":
        raise SourceFailedError("empty_response:" + content_type)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SourceFailedError("invalid_sse_payload") from exc


def _extract_tool_result(message: dict) -> object:
    """取出工具结果：解析 content[0].text 里的 JSON。"""
    result = message.get("result")
    if not isinstance(result, dict):
        error = message.get("error")
        if isinstance(error, dict):
            raise SourceFailedError("rpc_error:" + str(error.get("code")))
        raise SourceFailedError("missing_result")
    if result.get("isError"):
        raise SourceFailedError("tool_error")
    for item in result.get("content", []):
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or text == "":
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise SourceFailedError("no_structured_content")


def _summary(data: object) -> dict[str, int]:
    """把结果压成少量计数，供日志与 span 共用，避免原始数据外泄。"""
    if isinstance(data, dict):
        total = data.get("total")
        if isinstance(total, int) and not isinstance(total, bool):
            return {"total": total}
        endpoints = data.get("endpoints")
        if isinstance(endpoints, list):
            return {"endpoints": len(endpoints)}
        return {}
    if isinstance(data, list):
        return {"rows": len(data)}
    return {}


def _format_summary(summary: dict[str, int]) -> str:
    """把计数拼成日志用的 k=v 片段；空字典返回空串。"""
    return "".join(f" {key}={value}" for key, value in summary.items())


def _open_span(tool: str, arguments: dict) -> Any | None:
    """在当前 trace 下开一个 mcp:<tool> span；不可用时返回 None。"""
    if not settings.LANGFUSE_ENABLE:
        return None
    trace_id = current_trace_id.get()
    if trace_id is None:
        return None
    try:
        return langfuse_context.client_instance.span(
            trace_id=trace_id,
            name="mcp:" + tool,
            input=arguments,
            start_time=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001  观测失败不得影响取数
        logger.warning(
            "[rca] mcp span open failed tool={} err={}", tool, type(exc).__name__
        )
        return None


def _close_span(span: Any | None, output: dict) -> None:
    """关闭 span；None 时是廉价 no-op。"""
    if span is None:
        return
    try:
        span.end(end_time=datetime.now(UTC), output=output)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[rca] mcp span close failed err={}", type(exc).__name__)


class McpLogSource:
    """经 MCP 工具取日志。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        datasource_id: str | None = None,
        timeout: float = 8.0,
        uris: tuple[str, ...] = (),
        slow_ms: int = 2000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._datasource_id = datasource_id
        self._timeout = timeout
        self._uris = uris
        self._slow_ms = slow_ms
        self._transport = transport
        self._session_id: str | None = None
        self._lock = asyncio.Lock()

    def _headers(self) -> dict[str, str]:
        """构造请求头；有会话时带上 session id。"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token != "":
            headers["Authorization"] = "Bearer " + self._token
        if self._session_id is not None:
            headers["mcp-session-id"] = self._session_id
        return headers

    async def _post(self, payload: dict) -> dict:
        """发送一条 JSON-RPC 报文并返回解析后的响应。"""
        if self._base_url == "":
            raise SourceFailedError("mcp_base_url_missing")
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            try:
                resp = await client.post(
                    self._base_url, json=payload, headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise SourceFailedError("transport:" + type(exc).__name__) from exc
        if resp.status_code == 401:
            raise SourceFailedError("unauthorized")
        if resp.status_code >= 400:
            raise SourceFailedError("http:" + str(resp.status_code))
        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        text = resp.text
        if text.strip() == "":
            return {}
        return _parse_body(text, resp.headers.get("content-type", ""))

    async def _ensure_session(self) -> None:
        """确保已完成 initialize 握手；并发下只握手一次。"""
        if self._session_id is not None:
            return
        async with self._lock:
            if self._session_id is not None:
                return
            await self._post(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "alert-triage", "version": "1.0.0"},
                    },
                }
            )
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _with_datasource(self, arguments: dict) -> dict:
        """附加数据源标识；未配置时不加该字段。"""
        if self._datasource_id is None or self._datasource_id == "":
            return arguments
        return {**arguments, "datasource_id": self._datasource_id}

    async def _call(self, tool: str, arguments: dict) -> object:
        """调用一个 MCP 工具并记录日志与 span。"""
        started = time.monotonic()
        span = _open_span(tool, arguments)
        try:
            data = await self._call_once(tool, arguments)
        except SourceFailedError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            logger.warning(
                "[rca] mcp call tool={} status=failed elapsed_ms={} reason={}",
                encode_value(tool),
                elapsed,
                encode_value(str(exc)),
            )
            _close_span(
                span, {"status": "failed", "reason": str(exc), "elapsed_ms": elapsed}
            )
            raise
        elapsed = int((time.monotonic() - started) * 1000)
        summary = _summary(data)
        logger.info(
            "[rca] mcp call tool={} status=ok elapsed_ms={}{}",
            encode_value(tool),
            elapsed,
            _format_summary(summary),
        )
        _close_span(span, {"status": "ok", "elapsed_ms": elapsed, **summary})
        return data

    async def _call_once(self, tool: str, arguments: dict) -> object:
        """单次调用；会话失效（400/404）时重建一次后重试。"""
        await self._ensure_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        try:
            message = await self._post(payload)
        except SourceFailedError as exc:
            reason = str(exc)
            if "http:404" not in reason and "http:400" not in reason:
                raise
            self._session_id = None
            await self._ensure_session()
            message = await self._post(payload)
        return _extract_tool_result(message)

    async def latency_stats(
        self, uri: str, start_ts: int, end_ts: int, slow_ms: int | None = None
    ) -> dict:
        """返回窗口样本量、分位、慢请求计数、5xx 计数与节点分布。"""
        effective = self._slow_ms if slow_ms is None else slow_ms
        data = await self._call(
            _TOOL_LATENCY,
            self._with_datasource(
                {
                    "uri": uri,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "slow_ms": effective,
                }
            ),
        )
        if not isinstance(data, dict) or not data.get("total"):
            raise SourceEmptyError("no_rows")
        return data

    async def request_detail(
        self, uri: str, start_ts: int, end_ts: int, limit: int = 100
    ) -> list:
        """返回窗口内的请求明细。"""
        data = await self._call(
            _TOOL_DETAIL,
            self._with_datasource(
                {
                    "uri": uri,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "limit": limit,
                }
            ),
        )
        rows: list = []
        if isinstance(data, dict):
            candidate = data.get("rows")
            if isinstance(candidate, list):
                rows = candidate
        elif isinstance(data, list):
            rows = data
        if not rows:
            raise SourceEmptyError("no_rows")
        return rows

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """返回核心接口的请求量与昨日同时段对比。"""
        if not self._uris:
            raise SourceEmptyError("no_business_uris")
        data = await self._call(
            _TOOL_IMPACT,
            self._with_datasource(
                {
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "uris": list(self._uris),
                }
            ),
        )
        if not isinstance(data, dict) or not data.get("endpoints"):
            raise SourceEmptyError("no_rows")
        return data
