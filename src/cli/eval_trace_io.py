"""Langfuse trace 读取与字段归一（eval_trace 的 IO 层）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from langfuse import Langfuse

from src.config import settings


def as_text(value: Any) -> str:
    """把 trace 字段归一成文本；None 返回空串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "output", "message"):
            if isinstance(value.get(key), str):
                return value[key]
    return json.dumps(value, ensure_ascii=False)


def latency_ms(value: Any) -> int | None:
    """Langfuse 的 latency 单位是秒；统一转成毫秒。"""
    if value is None:
        return None
    return int(float(value) * 1000)


def _client(host: str) -> Langfuse:
    """构造只读 Langfuse 客户端。"""
    return Langfuse(
        host=host,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
    )


def fetch_trace(trace_id: str, host: str) -> Any:
    """按 trace id 拉取完整 trace（含 observations）。

    Args:
        trace_id: Langfuse trace ID
        host: Langfuse 地址（本机调试通常传 http://localhost:3000）

    Returns:
        `TraceWithFullDetails`
    """
    client = _client(host)
    try:
        return client.fetch_trace(trace_id).data
    finally:
        client.shutdown()


def parse_since(value: str) -> datetime | None:
    """解析 --since；支持 ISO 日期/时间，空串表示不限。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def fetch_recent(host: str, *, since: datetime | None, limit: int) -> list[Any]:
    """拉取最近的生产 chat trace 摘要（不含 observations）。

    Args:
        host: Langfuse 地址
        since: 起始时间；None 表示不限
        limit: 最多返回条数

    Returns:
        trace 摘要列表；每条含 id / timestamp / input / output / tags
    """
    client = _client(host)
    kwargs: dict = {"limit": limit, "name": "chat_turn", "tags": ["chat"]}
    if since is not None:
        kwargs["from_timestamp"] = since
    try:
        return list(client.fetch_traces(**kwargs).data)
    finally:
        client.shutdown()
