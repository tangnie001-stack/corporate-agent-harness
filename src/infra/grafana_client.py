"""Grafana HTTP 客户端：Bearer 鉴权、超时与错误归一。

只做传输，不做业务解析；调用方负责解释返回体。token 不进入日志。
"""

from __future__ import annotations

import httpx


class GrafanaError(RuntimeError):
    """Grafana 调用失败。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # HTTP 状态码；连接类错误为 None


class GrafanaClient:
    """异步 Grafana 客户端。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        """构造鉴权头；未配置 token 时不加。"""
        if self._token == "":
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict | list:
        """GET 并解析 JSON；非 2xx 抛 GrafanaError。"""
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            try:
                resp = await client.get(
                    self._base + path, params=params, headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise GrafanaError(f"request failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise GrafanaError(f"http {resp.status_code}", status=resp.status_code)
        return resp.json()

    async def post_json(self, path: str, body: dict) -> dict:
        """POST JSON 并解析返回；非 2xx 抛 GrafanaError。"""
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            try:
                resp = await client.post(
                    self._base + path, json=body, headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise GrafanaError(f"request failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise GrafanaError(f"http {resp.status_code}", status=resp.status_code)
        return resp.json()
