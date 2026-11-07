"""add diagnosis_reports

诊断报告表：结论落库、可回归；证据只存摘要与定位（原文不进库）。
created_at 建索引，180 天保留期的清理属运维动作，不在代码侧做定时删除。

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建诊断报告表与两个查询索引。"""
    op.create_table(
        "diagnosis_reports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("env", sa.String(length=16), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=True),
        sa.Column("tier", sa.String(length=16), nullable=True),
        sa.Column("root_cause", sa.String(length=32), nullable=True),
        sa.Column("owner", sa.String(length=16), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("assertions", postgresql.JSONB(), nullable=True),
        sa.Column("next_actions", sa.Text(), nullable=True),
        sa.Column("basis_rounds", sa.Integer(), nullable=True),
        sa.Column("cost_ms", sa.Integer(), nullable=True),
        sa.Column("confirmed", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_diag_trace", "diagnosis_reports", ["trace_id"])
    op.create_index("ix_diag_created", "diagnosis_reports", ["created_at"])


def downgrade() -> None:
    """删除诊断报告表。"""
    op.drop_index("ix_diag_created", table_name="diagnosis_reports")
    op.drop_index("ix_diag_trace", table_name="diagnosis_reports")
    op.drop_table("diagnosis_reports")
