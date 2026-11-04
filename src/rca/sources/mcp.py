"""MCP 版取数实现：经 sls-mcp-service 的 Streamable HTTP 端点查询 SLS。

与 GrafanaLogSource 实现同一个 LogSource 协议，由 RcaConfig.rca_source 二选一。

协议要点（2026-11-04 对 beta 环境实测）：
- POST <base_url> 发 JSON-RPC；initialize 的响应头返回 mcp-session-id，后续请求必须带上
- 响应可能是纯 JSON，也可能是 SSE（event: message + data:{...}），两种都要能解析
- 工具结果在 result.content[0].text 里，是一段 JSON 字符串；result.isError 为真表示工具内部失败
- 工具名带 sls_ 前缀；business_impact 必须传 uris

异常语义：无数据抛 SourceEmptyError，调用失败抛 SourceFailedError，供判定层区分 empty / failed。
"""

from __future__ import annotations

import asyncio
import json

import httpx

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
        """调用一个 MCP 工具；会话失效时重建一次后重试。"""
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
