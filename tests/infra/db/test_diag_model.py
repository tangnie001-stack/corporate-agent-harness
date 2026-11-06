"""诊断报告表：列完整、索引存在、可被 PostgreSQL 方言渲染。"""

from sqlalchemy import Index, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from src.infra.db.models.diag import DiagnosisReportModel

_COLUMNS = {
    "id",
    "trace_id",
    "env",
    "trigger",
    "kind",
    "tier",
    "root_cause",
    "owner",
    "evidence",
    "assertions",
    "next_actions",
    "basis_rounds",
    "cost_ms",
    "confirmed",
    "created_at",
}


def test_columns_match_spec():
    """7.1 列清单：缺一列报告就落不全。"""
    assert set(DiagnosisReportModel.__table__.columns.keys()) == _COLUMNS


def test_trace_and_created_are_indexed():
    indexes = {
        arg.name
        for arg in DiagnosisReportModel.__table_args__
        if isinstance(arg, Index)
    }
    assert {"ix_diag_trace", "ix_diag_created"} <= indexes


def test_jsonb_renders_under_postgresql():
    """JSONB 是方言类型：渲染失败会在建表时直接报错。"""
    table = DiagnosisReportModel.__table__
    assert isinstance(table, Table)
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "JSONB" in ddl
