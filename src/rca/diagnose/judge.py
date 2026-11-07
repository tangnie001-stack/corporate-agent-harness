"""诊断判定：把检索事实映射为根因。

判定顺序固定（后一条依赖前一条不成立）：
未检索 → 低相关 → 上下文被截断 → 未引用 → 未判定。
"""

from __future__ import annotations

import statistics

from src.config import settings
from src.rca.diagnose.history import HistoryRound
from src.rca.diagnose.models import DiagnosisKind, RetrievalCall, RootCause


def judge(
    calls: list[RetrievalCall],
    *,
    kind: DiagnosisKind = DiagnosisKind.ANSWER,
    cited: bool | None,
    has_context_budget_high: bool,
    prior: HistoryRound | None = None,
) -> tuple[RootCause, str]:
    """按分型分派判定，返回 (根因, 人可读依据)。

    Args:
        calls: 本轮全部 retrieve_kb 调用；空 = 没有检索。
        kind: 问题分型（入口判定）；错误类走 judge_error，不进入检索判定。
        cited: 回答是否引用了上下文；None = 未知（无法判定"未引用"）。
        has_context_budget_high: 本轮是否出现上下文预算告警。
        prior: 最近一轮追溯事实；仅在本轮未检索时参与"被裁"判定。
    """
    if kind is not DiagnosisKind.ANSWER:
        return judge_error(kind)
    return judge_answer(
        calls,
        cited=cited,
        has_context_budget_high=has_context_budget_high,
        prior=prior,
    )


def judge_error(kind: DiagnosisKind) -> tuple[RootCause, str]:
    """错误类判定：检索事实与错误条目无因果关系，只给归属判断。

    Args:
        kind: 5xx / 4xx

    Returns:
        恒为未判定；原因文案说明归属与缺失的证据项
    """
    if kind is DiagnosisKind.ERROR_4XX:
        return (
            RootCause.UNDETERMINED,
            "4xx 属客户端侧，归属 ops 受理；原因分类待 access 日志状态码",
        )
    return (
        RootCause.UNDETERMINED,
        "5xx 服务端错误，归属 dev 排查；具体原因待错误栈证据",
    )


def judge_answer(
    calls: list[RetrievalCall],
    *,
    cited: bool | None,
    has_context_budget_high: bool,
    prior: HistoryRound | None = None,
) -> tuple[RootCause, str]:
    """答错类判定（检索证据 → 根因）。

    Args:
        calls: 本轮全部 retrieve_kb 调用；空 = 没有检索。
        cited: 回答是否引用了上下文；None = 未知（无法判定"未引用"）。
        has_context_budget_high: 本轮是否出现上下文预算告警。
        prior: 最近一轮追溯事实；本轮未检索且上一轮被预算截断时归为"上下文被截断"。
    """
    if not calls:
        if prior is not None and prior.truncated:
            return (
                RootCause.CONTEXT_TRUNCATED,
                (
                    f"本轮未检索（追问沿用上一轮）；上一轮召回 {prior.result_count} 条"
                    f"仅 {prior.context_count} 条进入上下文"
                ),
            )
        return RootCause.NOT_RETRIEVED, "本轮没有发起检索"

    rerank_calls = [c for c in calls if c.score_kind == "rerank" and c.scores]
    if not rerank_calls and calls:
        return (
            RootCause.UNDETERMINED,
            "量纲不可比：分数来自降级路径（fallback/timeout），无法判低相关",
        )

    if rerank_calls and _all_flat_low(rerank_calls):
        top = max(max(c.scores) for c in rerank_calls)
        return RootCause.LOW_RELEVANCE, f"精排分数整体偏低且无尖峰（top1={top:.3f}）"

    truncated = [c for c in calls if c.context_count < c.result_count]
    if truncated and has_context_budget_high:
        c = truncated[0]
        return (
            RootCause.CONTEXT_TRUNCATED,
            f"召回 {c.result_count} 条，进入上下文仅 {c.context_count} 条",
        )

    if cited is None:
        return RootCause.UNDETERMINED, "无法判断回答是否引用上下文"

    if cited is False:
        top = max(max(c.scores) for c in calls)
        if top >= settings.DIAG_SCORE_FLOOR:
            return (
                RootCause.NOT_CITED,
                f"上下文分数达标（top1={top:.3f}），但回答未引用",
            )

    return RootCause.UNDETERMINED, "证据不足以归入已定义根因"


def _all_flat_low(calls: list[RetrievalCall]) -> bool:
    """每次调用都满足：top1 低于下界，且 top1 与 p50 间距过小。"""
    for call in calls:
        scores = sorted(call.scores, reverse=True)
        top = scores[0]
        if top >= settings.DIAG_SCORE_FLOOR:
            return False
        if top - statistics.median(scores) >= settings.DIAG_SCORE_GAP:
            return False
    return True
