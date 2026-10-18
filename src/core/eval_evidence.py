"""评测证据模型与每题的 ContextVar 作用域。"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

EVIDENCE_CANDIDATE_CAP = 50
EVIDENCE_GOLD_CAP = 20


def parent_key(
    doc_id: str, chunk_id: str, parent_content: str | None
) -> tuple[str, str]:
    """返回 dedup/fact 等价用的 parent 键。

    Args:
        doc_id: 文档 ID
        chunk_id: 分块 ID（parent_content 为空时的兜底）
        parent_content: 父块内容

    Returns:
        `(doc_id, md5(parent_content))`；parent_content 为空时为 `(doc_id, chunk_id)`
    """
    if not parent_content:
        return (doc_id, chunk_id)
    digest = hashlib.md5(parent_content.encode("utf-8")).hexdigest()
    return (doc_id, digest)


@dataclass(slots=True)
class QuestionEvidence:
    """一道题的全部证据。"""

    q_index: int  # 题目在整批中的 0-based 序号
    trace_id: str | None = None  # 该题 trace id；未进入生成阶段时为 None
    answer: str = ""  # 被测系统的最终回答（answer_mentions_fact 的唯一来源）
    testset: dict = field(default_factory=dict)  # 测试集样本字段
    query: dict = field(default_factory=dict)  # 问题原文与改写/子查询等信息
    retrieval: dict = field(default_factory=dict)  # 检索候选与最终上下文
    rerank_abandoned: Any = field(
        default_factory=threading.Event, repr=False
    )  # 外层超时后置位；后台精排线程据此停写证据
    tool_calls: list[dict] = field(default_factory=list)  # 顶层工具调用补充
    trajectory: dict = field(default_factory=dict)  # summarize_trajectory 摘要
    judge: dict = field(default_factory=dict)  # 裁判调用与证据
    trace: dict = field(default_factory=dict)  # trace 观测信息
    behavior: dict = field(default_factory=dict)  # 行为分
    facts: dict = field(default_factory=dict)  # fact 等价比对
    attribution: dict = field(default_factory=dict)  # 低分归因


@dataclass(slots=True)
class EvidenceRun:
    """一轮评测的证据容器。"""

    run_id: str  # 整轮 run id
    run_trace_id: str | None  # 整轮 trace id；未开启时为 None
    enabled: bool  # 证据采集开关
    collectors: list[QuestionEvidence | None] = field(default_factory=list)
    corpus_chunks: list[Any] | None = None  # 本轮知识库全量分块
    captured_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )  # 采集时间

    def new_question(
        self, q_index: int, trace_id: str | None = None
    ) -> QuestionEvidence:
        """创建或复用某题的 collector。"""
        while len(self.collectors) <= q_index:
            self.collectors.append(None)
        collector = self.collectors[q_index]
        if collector is None:
            collector = QuestionEvidence(q_index=q_index, trace_id=trace_id)
            self.collectors[q_index] = collector
        else:
            collector.trace_id = trace_id
        return collector

    def collector(self, q_index: int) -> QuestionEvidence | None:
        """按题号取 collector；越界返回 None。"""
        if 0 <= q_index < len(self.collectors):
            return self.collectors[q_index]
        return None


current_evidence: ContextVar[QuestionEvidence | None] = ContextVar(
    "current_evidence", default=None
)


@contextmanager
def evidence_scope(collector: QuestionEvidence | None) -> Iterator[None]:
    """在生成/评分阶段设置当前题 collector，finally 恢复。"""
    token = current_evidence.set(collector)
    try:
        yield
    finally:
        current_evidence.reset(token)
