"""诊断报告 Repo — diagnosis_reports 读写。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.infra.db.models.diag import DiagnosisReportModel

if TYPE_CHECKING:
    from src.rca.diagnose.models import DiagnosisReport


class DiagRepo:
    """诊断报告仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def save(
        self,
        report: DiagnosisReport,
        *,
        env: str,
        trigger: str = "manual",
        cost_ms: int = 0,
    ) -> str:
        """写入一条诊断报告，返回 diag_id。

        Args:
            report: 诊断报告（证据与断言只取摘要与定位）
            env: 部署环境标识
            trigger: 触发方式（manual / alert）
            cost_ms: 诊断耗时

        Returns:
            新报告的 id
        """
        row = DiagnosisReportModel(
            trace_id=report.trace_id,
            env=env,
            trigger=trigger,
            kind=report.kind.value,
            tier=report.tier.value,
            root_cause=report.root_cause.value,
            owner=report.owner.value,
            evidence=[
                {
                    "id": e.id,
                    "kind": e.kind,
                    "locator": e.locator,
                    "summary": e.summary,
                }
                for e in report.evidence
            ],
            assertions=[
                {"text": text, "evidence_ids": list(refs)}
                for text, refs in report.assertions
            ],
            next_actions="\n".join(report.next_actions),
            basis_rounds=report.basis_rounds,
            cost_ms=cost_ms,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            return str(row.id)

    async def get(self, diag_id: str) -> DiagnosisReportModel | None:
        """按 id 取报告；不存在返回 None。"""
        async with self._sf() as session:
            return await session.get(DiagnosisReportModel, diag_id)
