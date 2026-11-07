"""诊断报告表 ORM 模型。

证据只存摘要与定位（7.2）：原文不进库，展开走详情页。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, UTCDateTime


class DiagnosisReportModel(Base):
    __tablename__ = "diagnosis_reports"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex
    )
    trace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="被诊断的 trace"
    )
    env: Mapped[str] = mapped_column(
        String(16), default="", comment="部署环境标识（一套部署一个值）"
    )
    trigger: Mapped[str] = mapped_column(
        String(16), default="manual", comment="触发方式：manual / alert"
    )
    kind: Mapped[str] = mapped_column(String(16), default="answer", comment="问题分型")
    tier: Mapped[str] = mapped_column(String(16), default="", comment="档位")
    root_cause: Mapped[str] = mapped_column(String(32), default="", comment="根因")
    owner: Mapped[str] = mapped_column(String(16), default="", comment="归属")
    evidence: Mapped[list | None] = mapped_column(
        JSONB, comment="证据摘要（id/kind/locator/summary），不含原文"
    )
    assertions: Mapped[list | None] = mapped_column(
        JSONB, comment="断言与引用的 evidence id"
    )
    next_actions: Mapped[str] = mapped_column(
        Text, default="", comment="建议动作，换行分隔"
    )
    basis_rounds: Mapped[int] = mapped_column(Integer, default=0, comment="用到前几轮")
    cost_ms: Mapped[int] = mapped_column(Integer, default=0, comment="诊断耗时")
    confirmed: Mapped[int] = mapped_column(
        Integer, default=0, comment="人工确认：0 未确认 / 1 已确认"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_diag_trace", "trace_id"),
        Index("ix_diag_created", "created_at"),
    )
