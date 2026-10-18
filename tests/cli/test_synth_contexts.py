"""v6 上下文构造：分组、公司锚点、报告期与别名。"""

from __future__ import annotations

import pytest

from src.cli.synth_contexts import (
    build_contexts,
    company_aliases,
    derive_company,
    derive_period,
    render_context,
)
from src.infra.db.vector_store.types import ChunkResult


def _chunk(
    doc_id: str,
    source: str,
    index: int,
    *,
    company: str = "东软集团股份有限公司",
    period: str = "2025年第一季度",
    parent: str = "",
    content: str = "",
) -> ChunkResult:
    """构造最小 ChunkResult。"""
    return ChunkResult(
        id=f"{doc_id}:{index}",
        content=content or f"正文{index}",
        metadata={
            "doc_id": doc_id,
            "source": source,
            "chunk_index": index,
            "company": company,
            "report_period": period,
            "parent_content": parent,
            "year": "2024",
        },
    )


def test_grouping_drops_trailing_singleton():
    chunks = [_chunk("d1", "a.pdf", i) for i in range(13)]
    groups = build_contexts(chunks, size=2)
    assert len(groups) == 6
    used = [c.metadata["chunk_index"] for g in groups for c in g.chunks]
    assert 12 not in used
    assert len(used) == 12


def test_groups_are_keyed_by_doc_id_not_source():
    chunks = [
        _chunk("d1", "same.pdf", 0, company="甲公司股份有限公司"),
        _chunk("d1", "same.pdf", 1, company="甲公司股份有限公司"),
        _chunk("d2", "same.pdf", 0, company="乙公司股份有限公司"),
        _chunk("d2", "same.pdf", 1, company="乙公司股份有限公司"),
    ]
    groups = build_contexts(chunks, size=2)
    assert len(groups) == 2
    assert {g.doc_id for g in groups} == {"d1", "d2"}


def test_tencent_company_is_derived_from_any_chunk_in_document():
    chunks = []
    for i in range(38):
        parent = (
            "腾讯控股有限公司（港交所代码：00700）公布年度业绩"
            if i < 5
            else "腾讯公布二零二四年年度业绩"
        )
        chunks.append(
            _chunk(
                "d-tencent",
                "tencent_2024_annual.pdf",
                i,
                company="tencent",
                period="",
                parent=parent,
            )
        )
    name, source_kind = derive_company(chunks)
    assert name == "腾讯控股有限公司"
    assert source_kind == "parent_content"
    groups = build_contexts(chunks, size=2)
    assert all(g.company == "腾讯控股有限公司" for g in groups)
    assert all(g.period == "2024年年度" for g in groups)


def test_narrative_prefix_is_stripped():
    chunks = [
        _chunk(
            "d1",
            "neusoft.pdf",
            0,
            company="",
            parent="本公司的控股股东东软集团股份有限公司持有股份",
        ),
        _chunk("d1", "neusoft.pdf", 1, company="", parent="同上"),
    ]
    name, _ = derive_company(chunks)
    assert name == "东软集团股份有限公司"


def test_missing_chinese_company_raises():
    chunks = [
        _chunk("d1", "x.pdf", 0, company="acme"),
        _chunk("d1", "x.pdf", 1, company="acme"),
    ]
    with pytest.raises(ValueError):
        derive_company(chunks)


def test_period_uses_year_and_suffix_when_report_period_missing():
    chunks = [
        _chunk("d1", "tencent_2024_annual.pdf", 0, period=""),
        _chunk("d1", "tencent_2024_annual.pdf", 1, period=""),
    ]
    assert derive_period(chunks) == "2024年年度"


def test_aliases_strip_longest_suffix_first():
    assert company_aliases("腾讯控股有限公司", "tencent") == {
        "腾讯控股有限公司",
        "腾讯",
        "tencent",
    }
    assert "东软" in company_aliases("东软集团股份有限公司", "")


def test_render_context_prefixes_header_only_once():
    chunks = [
        _chunk("d1", "a.pdf", 0, content="第一块"),
        _chunk("d1", "a.pdf", 1, content="第二块"),
    ]
    group = build_contexts(chunks, size=2)[0]
    rendered = render_context(group)
    assert rendered[0].startswith("【")
    assert "·" in rendered[0]
    assert not rendered[1].startswith("【")
