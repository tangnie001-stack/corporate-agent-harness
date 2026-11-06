"""诊断事实模型 — 值对象，全部不可变。

RootCause 枚举即"允许输出的根因全集"：每个值都必须有可证伪的判据；
无法判定时给 undetermined，不做推测。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RootCause(str, Enum):
    """根因枚举（闭合集合）。"""

    NOT_RETRIEVED = "not_retrieved"  # 本轮没有 retrieve_kb 观测
    LOW_RELEVANCE = "low_relevance"  # 精排分数整体低且无尖峰
    CONTEXT_TRUNCATED = "context_truncated"  # 有召回但进 prompt 的条数被截
    NOT_CITED = "not_cited"  # 有高分上下文但回答未引用
    UNDETERMINED = "undetermined"  # 证据不足，未判定


@dataclass(frozen=True)
class RetrievalCall:
    """一次 retrieve_kb 调用的检索事实（来源：trace metadata.retrieval_contexts）。"""

    call_index: int  # 本请求内第几次检索（1 起）
    query: str  # 本次实际检索的查询词
    top_k: int  # 请求的 top_k
    iteration: int  # 所属迭代轮次（来自工具入参）
    result_count: int  # 融合后的候选条数
    context_count: int  # 精排后进入 prompt 的条数
    score_kind: str  # 分数量纲：rerank / fallback / timeout / none
    scores: tuple[float, ...]  # 进入 prompt 的上下文分数，降序
    chunk_ids: tuple[str, ...]  # 进入 prompt 的 chunk id，与 scores 同序


@dataclass(frozen=True)
class Evidence:
    """一条证据：可定位、可点开、可被断言引用。"""

    id: str  # 全局唯一标识（报告内引用用），形如 E1
    kind: str  # 证据类型：trace / log / probe / change
    locator: str  # 定位串：retrieve_kb#1、log:<文件>:<行号>、chunk:<id>
    summary: str  # 一句话摘要（不含原文，供报告引用）


@dataclass(frozen=True)
class DiagnosisReport:
    """一次诊断的结论与依据。"""

    trace_id: str  # 被诊断的 trace
    root_cause: RootCause  # 根因（或未判定）
    evidence: tuple[Evidence, ...]  # 全部证据
    assertions: tuple[
        tuple[str, tuple[str, ...]], ...
    ]  # (断言文本, 引用的 evidence id)
    next_actions: tuple[str, ...]  # 建议动作（文本，不执行）
    basis_rounds: int  # 本次结论用到了前几轮上下文（0 = 仅本 trace）


def reason_text(cause: RootCause) -> str:
    """根因的人类可读说明（用于报告首行）。"""
    return {
        RootCause.NOT_RETRIEVED: "本轮没有发起检索",
        RootCause.LOW_RELEVANCE: "检索结果相关度过低",
        RootCause.CONTEXT_TRUNCATED: "召回后被截断，进入上下文的条数不足",
        RootCause.NOT_CITED: "上下文已给到模型，但回答未引用",
        RootCause.UNDETERMINED: "证据不足，未判定",
    }[cause]
