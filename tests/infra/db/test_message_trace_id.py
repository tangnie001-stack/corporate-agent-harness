"""消息表 trace_id 列：可空、带索引（存量行不回填）。"""

from sqlalchemy import Index

from src.infra.db.models.chat import MessageModel


def test_trace_id_column_is_nullable():
    """存量消息没有 trace_id，列必须可空，否则迁移会失败。"""
    col = MessageModel.__table__.columns["trace_id"]
    assert col.nullable is True


def test_trace_id_is_indexed():
    """按 trace_id 反查「这个 trace 对应哪轮回答」是高频操作。"""
    indexes = [arg for arg in MessageModel.__table_args__ if isinstance(arg, Index)]
    assert any(index.name == "ix_message_trace_id" for index in indexes)
