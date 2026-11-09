"""卡片渲染：档位、归属、根因与依据全部由代码拼装（模型不产出数字）。

不输出原文：证据只给计数与摘要，展开走需登录的详情页（D10）。
"""

from __future__ import annotations

from src.config import diag as diag_texts
from src.rca.diagnose.models import DiagnosisReport, reason_text
from src.utils.mask import mask_nodes


def render_card(report: DiagnosisReport, *, detail_url: str = "") -> str:
    """把报告渲染成企微 markdown 卡片。

    Args:
        report: 诊断报告
        detail_url: 详情页地址；空串表示不附链接（CLI 场景）

    Returns:
        不超过详情页所能承载的摘要文本；节点地址已脱敏
    """
    lines = [
        diag_texts.CARD_HEADER.format(
            kind=diag_texts.KIND_LABELS.get(report.kind.value, report.kind.value)
        ),
        diag_texts.CARD_TIER.format(
            tier=report.tier.value,
            owner=diag_texts.OWNER_LABELS.get(report.owner.value, report.owner.value),
        ),
        diag_texts.CARD_CAUSE.format(cause=_cause_text(report)),
    ]
    if report.basis_rounds > 0:
        lines.append(diag_texts.CARD_BASIS.format(rounds=report.basis_rounds))

    basis = [text for text, _refs in report.assertions]
    for text in basis[: diag_texts.CARD_EVIDENCE_LIMIT]:
        lines.append(diag_texts.CARD_BASIS_ITEM.format(text=text))
    if len(basis) > diag_texts.CARD_EVIDENCE_LIMIT:
        lines.append(
            diag_texts.CARD_BASIS_MORE.format(
                count=len(basis) - diag_texts.CARD_EVIDENCE_LIMIT
            )
        )

    footer = diag_texts.CARD_EVIDENCE.format(count=len(report.evidence))
    if detail_url:
        footer = footer + " ｜ " + diag_texts.CARD_DETAIL_LINK.format(url=detail_url)
    lines.append(footer)
    return mask_nodes("\n".join(lines))


def _cause_text(report: DiagnosisReport) -> str:
    """优先用报告自带的说明（含未判定原因），否则退回根因的中文名。"""
    if report.summary:
        return report.summary
    return reason_text(report.root_cause)
