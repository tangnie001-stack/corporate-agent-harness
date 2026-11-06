"""add conversation_history.trace_id

按 trace_id 反查「这个 trace 对应哪一轮回答」是诊断入口的第二公里。
列可空且**不回填存量**：老会话无法诊断（日志亦已轮转），强行回填只能猜。

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为会话历史表增加 trace_id 列与索引（存量行留空）。"""
    op.add_column(
        "conversation_history",
        sa.Column(
            "trace_id",
            sa.String(length=64),
            nullable=True,
            comment="产生该消息的请求 trace_id；存量行不回填",
        ),
    )
    op.create_index(
        "ix_message_trace_id", "conversation_history", ["trace_id"], unique=False
    )


def downgrade() -> None:
    """回退：删索引与列。"""
    op.drop_index("ix_message_trace_id", table_name="conversation_history")
    op.drop_column("conversation_history", "trace_id")
