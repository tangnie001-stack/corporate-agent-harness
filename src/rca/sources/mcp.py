"""MCP 版取数实现（骨架）。

与 `GrafanaLogSource` 实现同一个 `LogSource` 协议，供 `sls-mcp-service` 就绪后切换。
本文件的职责只有三件：拼工具名与参数、发一次 MCP 调用、把返回规整成协议约定的结构。
**协议细节（initialize / tools/list / tools/call 的报文）尚未确定，集中在 `_call` 一处待补。**

切换方式（在编排层二选一，二选一之前不影响现状）：

    if cfg.source == "mcp":
        source = McpLogSource(cfg.mcp_base_url, cfg.mcp_token, cfg.mcp_datasource_id)
    else:
        source = GrafanaLogSource(client, context, cfg)

注意事项：
- 工具返回必须是结构化对象；文本型返回需要在这里解析成 dict，不要泄漏到判定层。
- "查到但没数据"抛 `SourceEmptyError`，"没查到"抛 `SourceFailedError` —— 判定层依赖这个区分。
- token 不进入日志。
"""

from __future__ import annotations

from src.rca.sources.base import SourceEmptyError, SourceFailedError


class McpLogSource:
    """经 MCP 工具取日志。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        datasource_id: str | None = None,
        timeout: float = 8.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._datasource_id = datasource_id
        self._timeout = timeout

    async def _call(self, tool: str, arguments: dict) -> dict:
        """调用一个 MCP 工具并返回其结构化结果。

        Args:
            tool: 工具名，与契约一致（latency_stats / request_detail / business_impact）
            arguments: 工具入参，已包含 datasource_id（若配置了）

        Returns:
            工具返回的结构化对象

        Raises:
            SourceEmptyError: 工具成功返回但无数据
            SourceFailedError: 调用失败、超时或返回不可解析
        """
        if self._base_url == "":
            raise SourceFailedError("mcp_base_url_missing")

        # TODO(mcp): 按 sls-mcp-service 的传输方式补全——
        #   1) 建立/复用 Streamable HTTP 会话（Bearer 鉴权，超时 self._timeout）
        #   2) 发送 tools/call 报文：{"name": tool, "arguments": arguments}
        #   3) 解析 MCP 响应：content 为文本时尝试 json.loads，失败按 SourceFailedError 处理
        #   4) 结果为空集合时抛 SourceEmptyError，其余失败抛 SourceFailedError
        raise NotImplementedError("McpLogSource._call 待接入 sls-mcp-service 后实现")

    def _with_datasource(self, arguments: dict) -> dict:
        """附加数据源标识；未配置时不加该字段。"""
        if self._datasource_id is None:
            return arguments
        return {**arguments, "datasource_id": self._datasource_id}

    async def latency_stats(
        self, uri: str, start_ts: int, end_ts: int, slow_ms: int = 2000
    ) -> dict:
        """返回窗口样本量、分位、慢请求计数、5xx 计数与节点分布。"""
        data = await self._call(
            "latency_stats",
            self._with_datasource(
                {"uri": uri, "start_ts": start_ts, "end_ts": end_ts, "slow_ms": slow_ms}
            ),
        )
        if not data.get("total"):
            raise SourceEmptyError("no_rows")
        return data

    async def request_detail(
        self, uri: str, start_ts: int, end_ts: int, limit: int = 100
    ) -> list:
        """返回窗口内的请求明细。"""
        data = await self._call(
            "request_detail",
            self._with_datasource(
                {"uri": uri, "start_ts": start_ts, "end_ts": end_ts, "limit": limit}
            ),
        )
        rows = data.get("rows") if isinstance(data, dict) else data
        if not rows:
            raise SourceEmptyError("no_rows")
        return list(rows)

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """返回核心接口的请求量与昨日同时段对比。"""
        data = await self._call(
            "business_impact",
            self._with_datasource({"start_ts": start_ts, "end_ts": end_ts}),
        )
        if not data.get("endpoints"):
            raise SourceEmptyError("no_rows")
        return data
