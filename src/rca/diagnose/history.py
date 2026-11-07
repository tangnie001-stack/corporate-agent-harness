"""D3 追溯：向前取最近 N 轮的检索事实，不重新判定。

触发条件（满足其一）：
- 本轮出现 `context budget high` 或 `history budget exceeded`；
- 本轮所有检索都 0 召回，且问题含指代（"它 / 这个 / 上面说的"）。

约束：最多 `DIAG_TRACE_LOOKBACK_ROUNDS` 轮；只取事实（检索条数、是否被裁、当时模型）；
追溯不再触发追溯（禁链式）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.config import settings
from src.rca.diagnose.logs import BudgetFlags, LocalLogSource
from src.rca.diagnose.models import RetrievalCall

# 指代词：命中才允许在"零召回"时追溯（避免对独立问题做无意义回溯）
_ANAPHORA = (
    "它",
    "这个",
    "那个",
    "上面",
    "上述",
    "刚才",
    "前面",
    "这条",
    "该项",
    "继续",
)


@dataclass(frozen=True)
class HistoryRound:
    """前序一轮的可核事实。"""

    offset: int  # 相对本轮的偏移：-1 = 上一轮
    trace_id: str  # 该轮的 trace
    model: str  # 当时生效模型；metadata 缺失时为空串（不猜）
    result_count: int  # 融合后候选条数
    context_count: int  # 进入 prompt 的条数
    score_kind: str  # 分数量纲
    context_budget_high: bool  # 该轮是否触发上下文预算告警
    history_budget_exceeded: bool  # 该轮是否触发历史预算告警

    @property
    def truncated(self) -> bool:
        """该轮是否"召回了但被预算截断"（下结论需预算告警作旁证）。"""
        return self.context_count < self.result_count and self.context_budget_high


def should_lookback(
    calls: list[RetrievalCall],
    *,
    has_context_budget_high: bool,
    has_history_budget_exceeded: bool,
    question: str,
) -> bool:
    """判断是否需要向前追溯（纯函数，路径判定不交给模型）。

    Args:
        calls: 本轮检索调用
        has_context_budget_high: 本轮是否出现上下文预算告警
        has_history_budget_exceeded: 本轮是否出现历史预算告警
        question: 本轮用户问题原文（用于指代判定）

    Returns:
        True = 应追溯
    """
    if has_context_budget_high or has_history_budget_exceeded:
        return True
    if any(call.result_count > 0 for call in calls):
        return False
    return any(word in question for word in _ANAPHORA)


class HistorySource:
    """取同 session 前序轮次的事实（Langfuse 只读 + 本地日志预算标记）。"""

    def __init__(
        self,
        session_fetcher: Callable[[str], tuple[datetime | None, list[Any]]]
        | None = None,
        budget_reader: Callable[[str, datetime], BudgetFlags] | None = None,
    ) -> None:
        """Args:
        session_fetcher: (trace_id) -> (时间戳, 历史 trace 列表)；默认走 Langfuse
        budget_reader: (trace_id, 时间戳) -> 预算标记；默认读本地应用日志
        """
        self._fetch_session = session_fetcher or _default_session_fetcher
        self._budget = budget_reader or _default_budget_reader

    def rounds_before(self, trace_id: str, *, limit: int) -> tuple[HistoryRound, ...]:
        """取最近 limit 轮事实（最近的为 offset=-1）。

        Args:
            trace_id: 目标 trace
            limit: 最多轮数（调用方传配置值）

        Returns:
            轮次元组，按时间升序（最后一轮是最靠近本轮的）；无历史时空元组
        """
        if limit <= 0:
            return ()
        ts, prior = self._fetch_session(trace_id)
        if not prior:
            return ()
        chosen = prior[-limit:]
        rounds: list[HistoryRound] = []
        total = len(chosen)
        for index, item in enumerate(chosen):
            metadata = getattr(item, "metadata", None) or {}
            calls = _last_call(metadata.get("retrieval_contexts") or [])
            flags = BudgetFlags()
            round_ts = getattr(item, "timestamp", None) or ts
            if round_ts is not None:
                flags = self._budget(str(getattr(item, "id", "")), round_ts)
            rounds.append(
                HistoryRound(
                    offset=index - total,  # 最后一轮 = -1
                    trace_id=str(getattr(item, "id", "")),
                    model=str(metadata.get("model") or ""),
                    result_count=calls.result_count if calls is not None else 0,
                    context_count=calls.context_count if calls is not None else 0,
                    score_kind=calls.score_kind if calls is not None else "none",
                    context_budget_high=flags.context_budget_high,
                    history_budget_exceeded=flags.history_budget_exceeded,
                )
            )
        return tuple(rounds)


def _last_call(raw_calls: list[Any]) -> RetrievalCall | None:
    """取该轮最后一次检索（决定实际进入 prompt 的内容）。"""
    if not raw_calls:
        return None
    from src.rca.diagnose.sources import parse_retrieval_calls

    parsed = parse_retrieval_calls(raw_calls)
    if not parsed:
        return None
    return parsed[-1]


def _default_session_fetcher(trace_id: str) -> tuple[datetime | None, list[Any]]:
    """默认读取器：走 Langfuse 只读客户端（懒 import）。"""
    from src.cli.eval_trace_io import fetch_session_traces

    return fetch_session_traces(
        trace_id, settings.LANGFUSE_HOST, limit=settings.DIAG_TRACE_LOOKBACK_ROUNDS * 5
    )


def _default_budget_reader(trace_id: str, at: datetime) -> BudgetFlags:
    """默认预算标记读取器：本地应用日志。"""
    return LocalLogSource(settings.DIAG_LOG_DIR).flags_for_trace(trace_id, at)
