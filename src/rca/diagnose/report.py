"""报告校验：断言必须引用存在的证据，否则降级。

降级不是失败——保留证据清单，把结论退回"未判定"，让人能复核。
"""

from __future__ import annotations

from dataclasses import replace

from src.rca.diagnose.models import DiagnosisReport, RootCause


def validate(report: DiagnosisReport) -> DiagnosisReport:
    """校验引用完整性；不通过时返回降级报告（同一 trace）。

    Args:
        report: 待校验报告。

    Returns:
        原报告（通过）或降级后的新报告（root_cause=UNDETERMINED、断言清空）。
    """
    if not report.assertions:
        return report
    known = {e.id for e in report.evidence}
    for _text, refs in report.assertions:
        if not refs or not set(refs) <= known:
            return replace(
                report,
                root_cause=RootCause.UNDETERMINED,
                assertions=(),
                next_actions=("补齐证据后重新诊断",),
            )
    return report
