"""诊断证据源：从 trace / 日志取事实。

所有 Source 只做"取数与归一化"，不做判定；失败语义统一为
SourceEmptyError（查到但无数据）/ SourceFailedError（没查到）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from src.rca.diagnose.models import Evidence, RetrievalCall, TraceFacts
from src.rca.sources.base import SourceEmptyError, SourceFailedError

_CITATION_RE = re.compile(r"\[\d+\]")


def _default_fetcher(trace_id: str) -> Any:
    """默认读取器：走 Langfuse 只读客户端（懒 import，避免模块级耦合）。"""
    from src.cli.eval_trace_io import fetch_trace
    from src.config import settings

    return fetch_trace(trace_id, settings.LANGFUSE_HOST)


def parse_retrieval_calls(raw_calls: list[Any]) -> list[RetrievalCall]:
    """把 trace metadata 的 `retrieval_contexts` 归一成检索事实。

    Args:
        raw_calls: metadata 里的原始条目（每项是一次 retrieve_kb 调用）

    Returns:
        RetrievalCall 列表；字段缺失按 0 / 空串处理
    """
    calls: list[RetrievalCall] = []
    for raw in raw_calls:
        contexts = raw.get("contexts") or []
        calls.append(
            RetrievalCall(
                call_index=int(raw.get("call_index") or 0),
                query=str(raw.get("query") or ""),
                top_k=int(raw.get("top_k") or 0),
                iteration=int(raw.get("iteration") or 0),
                result_count=int(raw.get("result_count") or 0),
                context_count=int(raw.get("context_count") or len(contexts)),
                score_kind=str(raw.get("score_kind") or "none"),
                scores=tuple(float(c.get("score") or 0.0) for c in contexts),
                chunk_ids=tuple(str(c.get("chunk_id") or "") for c in contexts),
            )
        )
    return calls


class TraceSource:
    """从单条 trace 的 metadata 还原检索事实。"""

    def __init__(self, fetcher: Callable[[str], Any] | None = None) -> None:
        """Args:
        fetcher: 取 trace 的函数（测试注入）；默认用 Langfuse 只读客户端。
        """
        self._fetcher = fetcher or _default_fetcher

    def fetch(self, trace_id: str) -> TraceFacts:
        """返回该 trace 的检索事实与引用情况。

        Raises:
            SourceEmptyError: trace 存在但没有检索快照（未检索，或旧版本无字段）。
            SourceFailedError: 读取 trace 失败（不存在 / 网络 / 鉴权）。
        """
        try:
            trace = self._fetcher(trace_id)
        except (
            Exception
        ) as exc:  # 任何读取失败都归一为 failed（含 NotFound / 网络 / 鉴权）
            raise SourceFailedError(f"trace_fetch_failed:{type(exc).__name__}") from exc

        metadata = getattr(trace, "metadata", None) or {}
        raw_calls = metadata.get("retrieval_contexts") or []
        if not raw_calls:
            raise SourceEmptyError("no_retrieval_snapshot")

        calls = parse_retrieval_calls(raw_calls)
        evidence: list[Evidence] = []
        for index, raw in enumerate(raw_calls, 1):
            evidence.append(
                Evidence(
                    id=f"E{index}",
                    kind="trace",
                    locator=f"retrieve_kb#{raw.get('call_index')}",
                    summary=(
                        f"result={raw.get('result_count')} "
                        f"ctx={raw.get('context_count')} "
                        f"kind={raw.get('score_kind')}"
                    ),
                )
            )
        return TraceFacts(
            calls=tuple(calls),
            evidence=tuple(evidence),
            cited=_detect_cited(getattr(trace, "output", None)),
        )


def _detect_cited(output: Any) -> bool | None:
    """从 trace 输出判断回答是否带引用标记（格式 `[n]`，与 format 节点同一规则）。

    Args:
        output: trace 的最终输出

    Returns:
        True/False；输出为空或非文本时返回 None（不猜）
    """
    if not isinstance(output, str) or not output.strip():
        return None
    return _CITATION_RE.search(output) is not None
