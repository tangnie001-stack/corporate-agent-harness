"""流式生成运行管理 — 单 worker 进程内任务注册表与 abort 信号映射。

后台生成任务由本管理器持有强引用，防止被 GC；同时维护
session_id → abort_signal 映射，供 POST /api/sessions/cancel 触达任务。
本模块还承载 SSE 消费者（缓冲 → SSE 事件/帧）与共享单例 streaming_manager：
stream_chat 启动后台任务与 cancel 端点共用同一实例（进程内，见
docs/agents/defensive-patterns.md「流式生成状态必须留在进程内」）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from src.config.const import SSEInteractionTexts
from src.core import logging as core_logging
from src.core.log_events import Event
from src.utils.sse import SSEErrorEvent, SSEEvent, from_payload, to_sse


async def _subscribe_events(
    session_id: str,
    manager: StreamingRunManager,
    after_seq: int = 0,
    max_idle: float | None = 180.0,
) -> AsyncGenerator[SSEEvent, None]:
    """SSE 消费者（事件对象版）：回放缓冲中 seq>after_seq 的事件并 tail 新事件直到终态。

    Args:
        session_id: 会话 ID
        manager: StreamingRunManager
        after_seq: 起始 seq（新一轮 POST 为 0，同页重连为 lastSeq）
        max_idle: tail 空闲超时秒数（无新事件超时返回续传超时 error）；
            None = 不按空闲收流，终态由任务生命周期（done/error）提供，
            仅在 manager.has_terminal() 为 True 时结束。主 POST 流用 None，
            resume 端点保留默认值 180 作"无任务僵尸续接"兜底。

    Yields:
        SSEEvent: 从缓冲还原的事件对象（token / citation / status / error / done），
            已注入 seq 属性，序列化时 to_sse 会将其写入 data（缓冲 payload 不变）
    """
    emitted = after_seq
    idle_loops = 0
    while True:
        pending = manager.get_events_since(session_id, emitted)
        if pending:
            idle_loops = 0
            for seq, etype, payload in pending:
                emitted = max(emitted, seq)
                event = from_payload(etype, payload)
                # 注入帧序列号，to_sse 序列化进 data（缓冲 payload 不变）
                event.seq = seq
                yield event
        else:
            if manager.has_terminal(session_id):
                return
            idle_loops += 1
            if max_idle is not None and idle_loops * 0.3 > max_idle:
                yield SSEErrorEvent(SSEInteractionTexts.RESUME_TIMEOUT_TEXT)
                return
            await asyncio.sleep(0.3)


async def _subscribe_buffer(
    session_id: str,
    manager: StreamingRunManager,
    after_seq: int = 0,
    max_idle: float = 180.0,
) -> AsyncGenerator[str, None]:
    """SSE 消费者（帧文本版）：回放缓冲事件并 tail 新事件直到终态，产出 SSE 帧。

    与 _subscribe_events 同构，仅将事件对象格式化为 SSE 文本帧（resume 回放用）。

    Args:
        session_id: 会话 ID
        manager: StreamingRunManager
        after_seq: 起始 seq（新一轮 POST 为 0，同页重连为 lastSeq）
        max_idle: tail 空闲超时秒数（无新事件超时返回续传超时 error）

    Yields:
        str: SSE 格式文本帧（event: <type>\\ndata: {...}\\n\\n）
    """
    async for event in _subscribe_events(session_id, manager, after_seq, max_idle):
        yield to_sse(event)


class StreamingRunManager:
    """进程内任务注册表 + abort 信号映射 + 事件缓冲（单 worker 部署假设）。"""

    MAX_ITEMS = 2000
    TTL_SECONDS = 300
    _TERMINAL_TYPES = ("done", "error")

    def __init__(self) -> None:
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._abort_signals: dict[str, asyncio.Event] = {}
        # 进程内原子预留：try_reserve 判定与预留之间无 await，供编排闸门在
        # Redis 不可用时也能拒绝同 session 并发（预留由 register 替换为任务，
        # 或由 release_reservation 显式释放）
        self._reserved: set[str] = set()
        self._stream_buffers: dict[str, list[tuple[int, str, Any]]] = {}
        self._buffer_done_at: dict[str, float] = {}
        self._seq_counters: dict[str, int] = {}

    def try_reserve(self, session_id: str) -> bool:
        """同步原子预留：会话未运行且未预留时标记预留并返回 True。

        判定（is_running）与预留（加入 _reserved）之间无 await 点，保证同一
        事件循环内两个并发请求不会同时通过。预留由后续 register 替换为任务
        引用，或由 release_reservation 显式释放（幂等）。
        """
        if self.is_running(session_id):
            return False
        self._reserved.add(session_id)
        return True

    def release_reservation(self, session_id: str) -> None:
        """释放预留（幂等：无预留时为 no-op）。"""
        self._reserved.discard(session_id)

    def register(
        self, session_id: str, task: asyncio.Task, abort_signal: asyncio.Event
    ) -> None:
        """登记任务与对应 abort 信号；替换预留为任务引用；完成时由调用方 unregister。"""
        self._session_tasks[session_id] = task
        self._abort_signals[session_id] = abort_signal
        self._reserved.discard(session_id)
        core_logging.log_event(Event.TASK_REGISTERED)

    def unregister(self, session_id: str) -> None:
        """注销任务与 abort 信号（任务 done_callback 调用）。"""
        self._session_tasks.pop(session_id, None)
        self._abort_signals.pop(session_id, None)

    def unregister_if_current(self, session_id: str, task: asyncio.Task) -> None:
        """仅当注册表中登记的是该 task 时才注销。

        防止旧任务的 done_callback 误删同 session 新注册的任务
        （断连后后台任务继续跑，用户可能对同一 session 二次提问）。
        """
        if self._session_tasks.get(session_id) is task:
            self._session_tasks.pop(session_id, None)
            self._abort_signals.pop(session_id, None)
            self._reserved.discard(session_id)

    def is_running(self, session_id: str) -> bool:
        """该 session 是否已有活跃生成任务或处于预留中。"""
        if session_id in self._reserved:
            return True
        task = self._session_tasks.get(session_id)
        return task is not None and not task.done()

    def get_abort_signal(self, session_id: str) -> asyncio.Event | None:
        """取该 session 的 abort 信号；无活跃任务返回 None。"""
        return self._abort_signals.get(session_id)

    def set_abort(self, session_id: str) -> None:
        """置位该 session 的 abort 信号（cancel 接口调用）。"""
        signal = self._abort_signals.get(session_id)
        if signal is not None:
            signal.set()

    def cancel(self, session_id: str) -> bool:
        """硬取消该 session 的生成任务（进程内口子）。

        与 `set_abort` 的区别：`set_abort` 只置位软信号、仅在**事件检查点**被
        消费——模型或出网挂死时检查点永不到达，生成会一直跑并占着会话锁；
        `cancel` 直接 `task.cancel()`，让生成走自身的取消路径收尾（该轮落
        `interrupted`）。`POST /api/sessions/cancel` 因归属校验对固定 user_id
        的会话无效，故告警轮用本方法。

        Args:
            session_id: 会话 ID

        Returns:
            True 表示确有活跃任务被取消；False 表示无任务或已结束
        """
        task = self._session_tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def get_active_session_ids(self) -> list[str]:
        """返回所有活跃任务的 session_id（测试/清理用）。"""
        return [sid for sid, t in self._session_tasks.items() if not t.done()]

    def clear_buffer(self, session_id: str) -> None:
        """清空该 session 缓冲并重置 seq（新一轮 POST 时调用）。"""
        self._stream_buffers.pop(session_id, None)
        self._buffer_done_at.pop(session_id, None)
        self._seq_counters.pop(session_id, None)

    def add_event(self, session_id: str, etype: str, payload: Any) -> int:
        """追加一条事件，返回其 seq。终态事件登记完成时间供 TTL 清理。"""
        self.sweep_expired()
        seq = self._seq_counters.get(session_id, 0) + 1
        self._seq_counters[session_id] = seq
        buf = self._stream_buffers.setdefault(session_id, [])
        buf.append((seq, etype, payload))
        if len(buf) > self.MAX_ITEMS:
            del buf[:50]
        if etype in self._TERMINAL_TYPES:
            import time as _t

            self._buffer_done_at[session_id] = _t.time()
        return seq

    def get_events_since(
        self, session_id: str, after_seq: int
    ) -> list[tuple[int, str, Any]]:
        """返回缓冲中 seq 大于 after_seq 的事件（回放用）。"""
        return [
            it for it in self._stream_buffers.get(session_id, []) if it[0] > after_seq
        ]

    def buffer_exists(self, session_id: str) -> bool:
        """该 session 是否有缓冲。"""
        return session_id in self._stream_buffers

    def has_terminal(self, session_id: str) -> bool:
        """缓冲是否已含终态事件（done/error）。"""
        return any(
            et in self._TERMINAL_TYPES
            for _, et, _ in self._stream_buffers.get(session_id, [])
        )

    def get_buffer_max_seq(self, session_id: str) -> int:
        """当前缓冲最大 seq（status 接口返回）。"""
        buf = self._stream_buffers.get(session_id, [])
        return buf[-1][0] if buf else 0

    def sweep_expired(self) -> None:
        """惰性清理：已完成且超过 TTL 的会话缓冲。"""
        if not self._buffer_done_at:
            return
        import time as _t

        now = _t.time()
        expired = [
            sid
            for sid, ts in self._buffer_done_at.items()
            if now - ts > self.TTL_SECONDS
        ]
        for sid in expired:
            self.clear_buffer(sid)


# 模块级共享运行管理器：AgentService.stream_chat 与 cancel 端点共用。
# 单 worker 部署假设下，进程内模块单例即可满足跨请求共享；若放 app 级，
# 会导致 service 层反向依赖 api 层（循环导入），故置于本模块。
streaming_manager = StreamingRunManager()
