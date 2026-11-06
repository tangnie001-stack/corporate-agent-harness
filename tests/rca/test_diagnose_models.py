"""诊断事实模型：根因枚举闭合、值对象不可变。"""

import dataclasses

import pytest

from src.rca.diagnose.models import (
    DiagnosisReport,
    Evidence,
    RetrievalCall,
    RootCause,
)


def test_root_cause_enum_is_closed():
    """枚举必须闭合：每个值都有判据，kb_stale 待变更源落地后引入。"""
    assert {c.value for c in RootCause} == {
        "not_retrieved",
        "low_relevance",
        "context_truncated",
        "not_cited",
        "undetermined",
    }


def test_retrieval_call_is_frozen():
    call = RetrievalCall(
        call_index=1,
        query="q",
        top_k=5,
        iteration=1,
        result_count=46,
        context_count=4,
        score_kind="rerank",
        scores=(0.76, 0.2),
        chunk_ids=("c1", "c2"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        call.result_count = 0  # type: ignore[misc]


def test_evidence_requires_locator():
    e = Evidence(
        id="E1", kind="trace", locator="retrieve_kb#1", summary="result=46 ctx=4"
    )
    assert e.id == "E1"
    assert e.locator.startswith("retrieve_kb")


def test_report_carries_assertions_with_evidence_ids():
    rep = DiagnosisReport(
        trace_id="trace_x",
        root_cause=RootCause.NOT_CITED,
        evidence=(Evidence("E1", "trace", "retrieve_kb#1", "ok"),),
        assertions=(("模型没有引用上下文", ("E1",)),),
        next_actions=("检查 prompt 的引用要求",),
        basis_rounds=0,
    )
    assert rep.assertions[0][1] == ("E1",)
    assert rep.root_cause is RootCause.NOT_CITED
