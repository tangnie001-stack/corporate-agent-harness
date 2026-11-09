"""Langfuse trace 读取与字段归一（eval_trace / 诊断的 IO 层）。

数据来源是 v4 的 Observations API v2：trace 在 v4 里就是根 observation，
子 observation 平铺在同一响应里。默认字段组只含 core+basic，故必须显式声明
`fields`；metadata 默认按 200 字符截断，`retrieval_contexts` 这类大对象必须放进
`expand_metadata`，否则读取层会静默拿到半截数据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langfuse import Langfuse

from src.config import settings

#: Observations API v2 的字段组；缺一组就会拿到 None（不是空值，是没选）
_FIELDS = "core,basic,time,io,metadata,model,usage,metrics,trace_context"
#: 需要完整返回（不截断）的 metadata 键
_EXPAND_METADATA = "retrieval_contexts"


@dataclass(frozen=True)
class ObservationView:
    """单条 observation 的只读视图（属性式访问，兼容旧消费方）。"""

    id: str
    name: str
    type: str
    start_time: datetime | None
    end_time: datetime | None
    latency: float | None
    input: Any
    output: Any
    metadata: dict[str, Any]
    parent_observation_id: str | None
    model: str | None
    usage_details: dict[str, int] | None
    cost_details: dict[str, float] | None


@dataclass
class TraceView:
    """单条 trace 的只读视图（根 observation + 子 observation）。"""

    id: str
    timestamp: datetime | None
    input: Any
    output: Any
    metadata: dict[str, Any]
    session_id: str
    user_id: str
    tags: list[str]
    latency: float | None
    observations: list[ObservationView] = field(default_factory=list)


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


def _loads(value: Any) -> Any:
    """input/output 以原始字符串返回；能解析成 JSON 就解析，否则保留原串。"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return value


def _client(host: str) -> Langfuse:
    """构造只读 Langfuse 客户端（关闭上报，避免读取动作产生写流量）。"""
    return Langfuse(
        base_url=host,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        tracing_enabled=False,
    )


def _obs_view(o: Any) -> ObservationView:
    """把 API 的 observation 对象映射为视图。"""
    return ObservationView(
        id=str(getattr(o, "id", "") or ""),
        name=str(getattr(o, "name", "") or ""),
        type=str(getattr(o, "type", "") or ""),
        start_time=getattr(o, "start_time", None),
        end_time=getattr(o, "end_time", None),
        latency=getattr(o, "latency", None),
        input=_loads(getattr(o, "input", None)),
        output=_loads(getattr(o, "output", None)),
        metadata=dict(getattr(o, "metadata", None) or {}),
        parent_observation_id=getattr(o, "parent_observation_id", None),
        model=getattr(o, "model", None),
        usage_details=getattr(o, "usage_details", None),
        cost_details=getattr(o, "cost_details", None),
    )


def _trace_view(
    root: Any, items: list[Any], *, include_observations: bool
) -> TraceView:
    """用根 observation 构造 trace 视图；metadata 取根 observation 的（D12）。"""
    observations: list[ObservationView] = []
    if include_observations:
        observations = [_obs_view(o) for o in items if o is not root]
        observations.sort(
            key=lambda x: x.start_time or datetime.min.replace(tzinfo=UTC)
        )
    return TraceView(
        id=str(getattr(root, "trace_id", "") or ""),
        timestamp=getattr(root, "start_time", None),
        input=_loads(getattr(root, "input", None)),
        output=_loads(getattr(root, "output", None)),
        metadata=dict(getattr(root, "metadata", None) or {}),
        session_id=str(getattr(root, "session_id", "") or ""),
        user_id=str(getattr(root, "user_id", "") or ""),
        tags=list(getattr(root, "tags", None) or []),
        latency=getattr(root, "latency", None),
        observations=observations,
    )


def fetch_trace(trace_id: str, host: str) -> TraceView:
    """按 trace id 拉取完整 trace（根 + 子 observation）。

    Args:
        trace_id: Langfuse trace ID（32 位 hex）
        host: Langfuse 地址（本机调试通常传 http://localhost:3001）

    Returns:
        TraceView；找不到时抛 LookupError
    """
    client = _client(host)
    resp = client.api.observations.get_many(
        trace_id=trace_id,
        limit=1000,
        fields=_FIELDS,
        expand_metadata=_EXPAND_METADATA,
    )
    items = list(getattr(resp, "data", None) or [])
    if not items:
        raise LookupError(f"trace not found: {trace_id}")
    root = next(
        (o for o in items if getattr(o, "is_root_observation", False)), items[0]
    )
    return _trace_view(root, items, include_observations=True)


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


def fetch_recent(host: str, *, since: datetime | None, limit: int) -> list[TraceView]:
    """拉取最近的生产 chat trace 摘要（不含子 observation）。

    Args:
        host: Langfuse 地址
        since: 起始时间；None 表示不限
        limit: 最多返回条数

    Returns:
        TraceView 列表（observations 为空）
    """
    client = _client(host)
    resp = client.api.observations.get_many(
        name="chat_turn",
        is_root_observation=True,
        limit=limit,
        from_start_time=since,
        fields=_FIELDS,
        expand_metadata=_EXPAND_METADATA,
    )
    items = list(getattr(resp, "data", None) or [])
    return [_trace_view(o, [o], include_observations=False) for o in items]


def fetch_session_traces(
    trace_id: str, host: str, *, limit: int = 20
) -> tuple[datetime | None, list[TraceView]]:
    """取同 session 内早于该 trace 的历史 trace（按时间升序）。

    Args:
        trace_id: 目标 trace
        host: Langfuse 地址
        limit: 最多拉取的条数（服务端上限，调用方再截断）

    Returns:
        (目标 trace 的时间戳, 历史 trace 列表)；目标 trace 无 session 时列表为空
    """
    current = fetch_trace(trace_id, host)
    if not current.session_id or current.timestamp is None:
        return current.timestamp, []
    client = _client(host)
    resp = client.api.observations.get_many(
        session_id=current.session_id,
        is_root_observation=True,
        to_start_time=current.timestamp,
        limit=limit,
        fields=_FIELDS,
        expand_metadata=_EXPAND_METADATA,
    )
    items = list(getattr(resp, "data", None) or [])
    prior = [
        _trace_view(o, [o], include_observations=False)
        for o in items
        if getattr(o, "trace_id", "") != trace_id
    ]
    prior.sort(key=lambda t: t.timestamp or datetime.min.replace(tzinfo=UTC))
    return current.timestamp, prior
