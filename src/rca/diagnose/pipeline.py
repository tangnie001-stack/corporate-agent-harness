"""诊断流水线：证据 → 判定 → 报告（含分型/档位/有界追溯，不含投递）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger

from src.config import settings
from src.rca.diagnose.history import HistoryRound, should_lookback
from src.rca.diagnose.judge import judge
from src.rca.diagnose.models import (
    DiagnosisKind,
    DiagnosisReport,
    Evidence,
    RootCause,
)
from src.rca.diagnose.report import validate
from src.rca.diagnose.tier import assess
from src.rca.sources.base import SourceEmptyError, SourceFailedError

# 日志窗口：与告警时间窗一致，取 trace 发生时前后各 15 分钟
_LOG_WINDOW = timedelta(minutes=15)
# 日志证据上限：卡片只放摘要，其余走详情页
_MAX_LOG_EVIDENCE = 5


def diagnose(
    trace_id: str,
    *,
    kind: DiagnosisKind = DiagnosisKind.ANSWER,
    cited: bool | None = None,
    has_context_budget_high: bool = False,
    has_history_budget_exceeded: bool = False,
    question: str = "",
    trace_source: object | None = None,
    log_source: object | None = None,
    history_source: object | None = None,
    now: datetime | None = None,
) -> DiagnosisReport:
    """对单个 trace 产出诊断报告。

    Args:
        trace_id: 目标 trace。
        kind: 问题分型（入口判定）；错误类不依赖检索快照。
        cited: 回答是否引用了上下文；None 表示未知（不判"未引用"）。
        has_context_budget_high: 本轮是否出现上下文预算告警。
        has_history_budget_exceeded: 本轮是否出现历史预算告警。
        question: 本轮用户问题原文（只用于指代判定，不交给模型）。
        trace_source: 具备 `fetch(trace_id)` 的证据源；None 表示无 trace 证据。
        log_source: 具备 `errors_between(start, end)` 的证据源；None 表示不查日志。
        history_source: 具备 `rounds_before(trace_id, limit=...)` 的追溯源；None 不追溯。
        now: 当前时间（测试注入），默认取 UTC 当前时刻。

    Returns:
        DiagnosisReport（已过引用校验）。
    """
    if trace_source is None and kind is DiagnosisKind.ANSWER:
        return _undetermined(trace_id, "未提供 trace 证据源", kind)

    calls = []
    evidence: list[Evidence] = []
    no_snapshot = False
    if trace_source is not None:
        try:
            calls, evidence = trace_source.fetch(trace_id)  # type: ignore[attr-defined]
        except SourceEmptyError:
            # 错误类不依赖检索快照：5xx 的 trace 可能根本没有 retrieve_kb 观测；
            # 答错类先记下"无快照"，等追溯结果决定是"未检索"还是"上一轮被截"
            no_snapshot = kind is DiagnosisKind.ANSWER
        except SourceFailedError as exc:
            return _undetermined(trace_id, f"trace 不可用：{exc}", kind)

    rows = []
    if log_source is not None:
        end = now or datetime.now(UTC)
        try:
            rows = log_source.errors_between(end - _LOG_WINDOW, end)  # type: ignore[attr-defined]
        except SourceFailedError as exc:
            return _undetermined(trace_id, f"日志源不可用：{exc}", kind)
        except SourceEmptyError:
            rows = []

    if kind is not DiagnosisKind.ANSWER:
        evidence = evidence + _log_evidence(rows)

    rounds: tuple[HistoryRound, ...] = ()
    if history_source is not None and should_lookback(
        calls,
        has_context_budget_high=has_context_budget_high,
        has_history_budget_exceeded=has_history_budget_exceeded,
        question=question,
    ):
        try:
            rounds = history_source.rounds_before(  # type: ignore[attr-defined]
                trace_id, limit=settings.DIAG_TRACE_LOOKBACK_ROUNDS
            )
        except Exception as exc:  # noqa: BLE001  追溯失败只降级，不影响主结论
            logger.warning(
                "[diagnose] lookback failed trace_id={} err={}", trace_id, exc
            )
            rounds = ()
    if rounds:
        evidence = evidence + _history_evidence(rounds)

    if no_snapshot and not rounds:
        return _undetermined(
            trace_id,
            "无检索快照：本轮可能未检索，或该 trace 早于字段上线",
            kind,
        )

    prior = rounds[-1] if rounds else None
    cause, reason = judge(
        calls,
        kind=kind,
        cited=cited,
        has_context_budget_high=has_context_budget_high,
        prior=prior,
    )
    assertions = _assertions(kind, cause, reason, evidence, rows, rounds)
    summary = reason
    if kind is not DiagnosisKind.ANSWER and not rows:
        summary = "窗口内无 ERROR 记录：可能未落到应用层"
    tier, owner = assess(kind, cause)
    report = DiagnosisReport(
        trace_id=trace_id,
        root_cause=cause,
        evidence=tuple(evidence),
        assertions=assertions,
        next_actions=(),
        basis_rounds=len(rounds),
        summary=summary,
        kind=kind,
        tier=tier,
        owner=owner,
    )
    return validate(report)


def _log_evidence(rows: list[object]) -> list[Evidence]:
    """把错误日志行转成证据（只留事件名，不带原文）。"""
    out: list[Evidence] = []
    for index, row in enumerate(rows[:_MAX_LOG_EVIDENCE], 1):
        event = getattr(row, "event", "") or getattr(row, "level", "")
        ts = getattr(row, "ts", None)
        locator = ""
        if isinstance(ts, datetime):
            locator = f"log:{ts.strftime('%Y-%m-%d %H:%M:%S')}"
        out.append(Evidence(id=f"L{index}", kind="log", locator=locator, summary=event))
    return out


def _history_evidence(rounds: tuple[HistoryRound, ...]) -> list[Evidence]:
    """把前序轮次事实转成证据（只留计数与模型名，不带原文）。"""
    out: list[Evidence] = []
    for index, item in enumerate(rounds, 1):
        model = item.model or "N/A"
        out.append(
            Evidence(
                id=f"H{index}",
                kind="history",
                locator=f"trace:{item.trace_id}",
                summary=(
                    f"round{item.offset} result={item.result_count} "
                    f"ctx={item.context_count} kind={item.score_kind} model={model}"
                ),
            )
        )
    return out


def _assertions(
    kind: DiagnosisKind,
    cause: RootCause,
    reason: str,
    evidence: list[Evidence],
    rows: list[object],
    rounds: tuple[HistoryRound, ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """按分型生成断言；每条必须引用存在的证据 id。"""
    if not evidence:
        return ()
    if kind is DiagnosisKind.ANSWER:
        first: tuple[str, tuple[str, ...]] = (reason, tuple(e.id for e in evidence))
        if not rounds:
            return (first,)
        prior = rounds[-1]
        history_text = (
            f"结论基于前 {len(rounds)} 轮上下文；最近一轮召回 {prior.result_count} 条，"
            f"{prior.context_count} 条进入上下文"
        )
        history_ids = tuple(e.id for e in evidence if e.kind == "history")
        return (first, (history_text, history_ids))
    first_event = "unknown"
    if rows:
        first_event = getattr(rows[0], "event", "") or "unknown"
    text = f"窗口内 ERROR {len(rows)} 条，首条事件 {first_event}"
    log_ids = tuple(e.id for e in evidence if e.kind == "log")
    if not log_ids:
        return ()
    return ((text, log_ids),)


def _undetermined(
    trace_id: str, summary: str, kind: DiagnosisKind = DiagnosisKind.ANSWER
) -> DiagnosisReport:
    """构造"未判定"报告：无断言，原因写进 summary。"""
    tier, owner = assess(kind, RootCause.UNDETERMINED)
    return DiagnosisReport(
        trace_id=trace_id,
        root_cause=RootCause.UNDETERMINED,
        evidence=(),
        assertions=(),
        next_actions=(summary,),
        basis_rounds=0,
        summary=summary,
        kind=kind,
        tier=tier,
        owner=owner,
    )
