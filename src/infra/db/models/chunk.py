"""分块表 ORM 模型。

chunks 由 P1 的 baseline 从零建立；本模型是它在 ORM 侧的**唯一映射**。
列名 / 类型 / nullability / server_default / 生成表达式 / **列注释**都必须与
alembic/versions/0001_pg_baseline.py 的 op.create_table("chunks", ...) 逐项一致，
否则下次 autogenerate 会把差异生成为变更 —— 包括看似无害的 comment。
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base

# 向量列维度；须与 registry 声明的 embedding_dimension 一致
# （一致性由 tests/infra/llm/provider/test_dimension_consistency.py 守护）。
# 改维度属于 DDL 变更，须配迁移，不得只改本常量。
EMBEDDING_DIMENSION = 1024


class ChunkModel(Base):
    """分块表：一张表承载全部知识库的分块，以 kb_id 列表达归属。"""

    __tablename__ = "chunks"

    # 分块 ID，格式 {doc_id}:{chunk_index}
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # 所属知识库（外键 → knowledge_base.id）
    kb_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 所属文档
    doc_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 该文档内的分块序号，0 起
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # 该文档的分块总数
    chunk_total: Mapped[int] = mapped_column(Integer, nullable=False)
    # 分块正文原文
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 词法检索文本；分词器变更时必须全量重写（P3 起为 jieba 输出）
    content_seg: Mapped[str] = mapped_column(Text, nullable=False)
    # 由 content_seg 自动生成的检索向量，不写入
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', content_seg)", persisted=True),
        nullable=True,
    )
    # DashScope text-embedding-v3；列注释与 baseline 逐字一致，改动会触发漂移
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSION),
        nullable=True,
        comment="DashScope text-embedding-v3",
    )
    # server_default 用 text() 传原始 SQL 片段，与 baseline 的 sa.text(...) 逐字对齐
    # （普通字符串会被 SQLAlchemy 加引号，例如 "'{}'::jsonb" 会变成带引号的字面量）
    # 来源文件名
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # 页码，无页码时为 0
    page: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # chunker 产出的自定义键（不含 doc_id/chunk_index/chunk_total/source/page 五个契约键）
    # 属性名不得叫 metadata（Base.metadata 是保留属性），列名保持 metadata 不改
    extra: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        ForeignKeyConstraint(["kb_id"], ["knowledge_base.id"]),
        UniqueConstraint("kb_id", "doc_id", "chunk_index", name="uq_chunks_kb_doc_idx"),
        Index("ix_chunks_kb_id", "kb_id"),
        Index("ix_chunks_doc_id", "doc_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )
