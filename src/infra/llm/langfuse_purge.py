"""Langfuse trace 保留期清理的后端 —— 走 v4 官方删除 API。

**为什么不再直连数据库**：v4 把 traces / observations / scores 落在 ClickHouse，
PostgreSQL 只剩事务数据；ADR-0013 的级联 SQL 在 v4 下要么报错、要么恒为 0 条
（dry-run 会被误读成「没有超期数据」）。决策见 ADR-0029。

**删除面由服务端保证**：官方删除 API 会同步清理关联的 observations 与 scores，
故本模块不再维护删除顺序契约，也不再清理空 session（v4 无本地 trace_sessions 表）。

本模块只做「列超期 / 删超期」，不含任何护栏 —— dry-run、保留期下界、单次上限、
双重确认与审计输出都留在 CLI。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from src.infra.llm.tracing import get_client


class LangfusePurgeError(RuntimeError):
    """删除后端不可用或 API 调用失败。"""


@dataclass
class ExpiredTrace:
    """一条超期 trace 的最小标识。"""

    id: str  # trace id（32 位小写 hex）
    session_id: str | None  # 会话 id；无会话时为 None


@dataclass
class CascadeCounts:
    """一次删除的审计口径。

    v4 由服务端级联删除子表，故子表计数恒为 0；traces 记实际请求删除的条数。
    """

    observations: int  # 子表计数（v4 恒 0）
    scores: int  # 子表计数（v4 恒 0）
    trace_media: int  # 子表计数（v4 恒 0）
    observation_media: int  # 子表计数（v4 恒 0）
    traces: int  # 实际删除的 trace 条数
    sessions: int  # 空 session 清理数（v4 恒 0）


class PurgeBackend(Protocol):
    """清理后端的最窄接口：列超期 + 删超期（供 CLI 依赖与测试替身）。"""

    async def list_expired(self, cutoff: datetime, limit: int) -> list[ExpiredTrace]:
        """列出早于 cutoff 的超期 trace，最多 limit 条。"""
        ...

    async def delete(self, traces: list[ExpiredTrace]) -> CascadeCounts:
        """删除给定 trace，返回审计计数。"""
        ...


class LangfuseApiPurgeBackend:
    """基于 Langfuse v4 官方 API 的清理后端。"""

    async def list_expired(self, cutoff: datetime, limit: int) -> list[ExpiredTrace]:
        """按 root observation 的起始时间列出超期 trace。

        Args:
            cutoff: 超期界限；naive 值按 UTC 解释
            limit: 最多返回条数（CLI 传单次上限 + 1 用于判超限）

        Returns:
            按时间升序的 ExpiredTrace 列表

        Raises:
            LangfusePurgeError: 客户端不可用或 API 调用失败
        """
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        try:
            resp = get_client().api.observations.get_many(
                is_root_observation=True,
                to_start_time=cutoff,
                limit=limit,
            )
        except Exception as exc:  # SDK 异常族不稳定，统一收敛
            raise LangfusePurgeError(f"list expired failed: {exc}") from exc

        rows = getattr(resp, "data", None) or []
        out: list[ExpiredTrace] = []
        for row in rows:
            tid = getattr(row, "trace_id", "") or ""
            if tid:
                out.append(
                    ExpiredTrace(id=tid, session_id=getattr(row, "session_id", None))
                )
        return out

    async def delete(self, traces: list[ExpiredTrace]) -> CascadeCounts:
        """批量删除给定 trace。

        Args:
            traces: 待删 trace 列表

        Returns:
            审计计数：traces 为请求删除条数，其余为 0

        Raises:
            LangfusePurgeError: 客户端不可用或 API 调用失败
        """
        if not traces:
            return CascadeCounts(0, 0, 0, 0, 0, 0)
        try:
            get_client().api.trace.delete_multiple(trace_ids=[t.id for t in traces])
        except Exception as exc:
            raise LangfusePurgeError(f"delete traces failed: {exc}") from exc
        return CascadeCounts(
            observations=0,
            scores=0,
            trace_media=0,
            observation_media=0,
            traces=len(traces),
            sessions=0,
        )

    async def aclose(self) -> None:
        """保持与旧后端相同的收尾接口；HTTP 客户端由 SDK 单例管理。"""
