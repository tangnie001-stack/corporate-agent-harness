"""会话和消息表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class SessionModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(36), default="")
    agent: Mapped[str] = mapped_column(
        String(64),
        default="",
        comment="会话绑定的智能体预设名（ASCII slug；空=未绑定）",
    )
    title: Mapped[str] = mapped_column(String(256), default="新对话")
    is_deleted: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("idx_user", "user_id"),
        # 注意：不需要 DESC。MySQL 的旧 schema 写的是 updated_at DESC，
        # 但 PostgreSQL 能对 ASC btree 做反向扫描，get_sessions 的
        # ORDER BY updated_at DESC LIMIT 50 照样走索引。
        Index("idx_updated_at", "updated_at"),
    )


class MessageModel(Base):
    __tablename__ = "conversation_history"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex
    )
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(36), default="")
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="user/assistant/system"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[str | None] = mapped_column(Text, comment="来源引用 JSON")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model_name: Mapped[str | None] = mapped_column(String(64), comment="模型名称")

    trace_id: Mapped[str | None] = mapped_column(
        String(64), comment="产生该消息的请求 trace_id；存量行不回填"
    )
    process: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="过程事件JSON（历史回放）"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="complete", nullable=False, comment="complete/interrupted"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_session", "session_id", "created_at"),
        Index("ix_message_trace_id", "trace_id"),
    )
