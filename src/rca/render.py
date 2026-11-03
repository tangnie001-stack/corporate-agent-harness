"""卡片渲染：定量字段由证据填充，模型只提供定性段落。"""

from __future__ import annotations

import re

from src.rca.models import EvidenceResult, EvidenceStatus, TriageVerdict

_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?")


def _mask(text: str) -> str:
    """把 IPv4[:port] 替换成节点别名。"""
    return _IP.sub("node-***", text)


def _missing_lines(results: dict[str, EvidenceResult]) -> list[str]:
    """列出未取到与无数据的证据。"""
    lines = []
    for evidence_id, result in results.items():
        if result.status is EvidenceStatus.FAILED:
            lines.append(f"未取到 {evidence_id}（{result.reason}）")
        if result.status is EvidenceStatus.EMPTY:
            lines.append(f"无数据 {evidence_id}")
    return lines


def render_card(
    verdict: TriageVerdict,
    results: dict[str, EvidenceResult],
    explanation: dict,
    max_chars: int,
) -> str:
    """渲染三档卡片；超长截断。"""
    stats = results.get("stats")
    total = 0
    if stats is not None and isinstance(stats.data, dict):
        total = int(stats.data.get("total", 0))

    lines = [
        f"[{verdict.tier.value}] 置信 {explanation.get('confidence', '-')}",
        f"判定 {verdict.reason}",
        f"样本 {total} 条（窗口内）",
    ]
    summary = explanation.get("summary")
    if summary:
        lines.append(f"结论 {summary}")
    for key, label in (
        ("hypotheses", "假设"),
        ("counter_evidence", "反证"),
        ("next_actions", "下一步"),
    ):
        value = explanation.get(key)
        if value:
            lines.append(f"{label} {value}")
    lines.extend(_missing_lines(results))

    text = _mask("\n".join(lines))
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text
