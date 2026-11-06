"""诊断报告读写：证据只存摘要，可按 id 取回。"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from src.infra.db.engine import session_factory
from src.infra.db.models.diag import DiagnosisReportModel
from src.infra.db.repos.diag_repo import DiagRepo
from src.rca.diagnose.models import (
    DiagnosisKind,
    DiagnosisReport,
    Evidence,
    Owner,
    RootCause,
)
from src.rca.models import Tier

pytestmark = pytest.mark.asyncio

_TRACE_ID = f"trace_{uuid.uuid4()}"


@pytest_asyncio.fixture
async def repo():
    yield DiagRepo(session_factory)
    async with session_factory() as session:
        await session.execute(
            delete(DiagnosisReportModel).where(
                DiagnosisReportModel.trace_id == _TRACE_ID
            )
        )


def _report():
    return DiagnosisReport(
        trace_id=_TRACE_ID,
        root_cause=RootCause.LOW_RELEVANCE,
        evidence=(Evidence("E1", "trace", "retrieve_kb#1", "result=30 ctx=4"),),
        assertions=(("精排分数整体偏低", ("E1",)),),
        next_actions=("补一份同义问法的样本",),
        basis_rounds=0,
        summary="精排分数整体偏低",
        kind=DiagnosisKind.ANSWER,
        tier=Tier.NEED_ACTION,
        owner=Owner.KB,
    )


async def test_save_then_get_roundtrip(repo):
    diag_id = await repo.save(_report(), env="local", cost_ms=12)
    row = await repo.get(diag_id)
    assert row is not None
    assert row.trace_id == _TRACE_ID
    assert row.tier == "需要处理"
    assert row.owner == "kb"
    assert row.evidence[0]["summary"] == "result=30 ctx=4"
    assert row.assertions[0]["evidence_ids"] == ["E1"]
    assert row.next_actions == "补一份同义问法的样本"


async def test_get_missing_returns_none(repo):
    assert await repo.get("nonexistent-id") is None
