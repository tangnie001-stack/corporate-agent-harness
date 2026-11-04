"""卡片渲染：定量字段由证据填充，模型只提供定性段落。

字段来源固定：
- 档位/判定依据 ← TriageVerdict
- 量化指标、节点分布 ← stats 证据
- 业务影响 ← impact 证据
- 未取到/无数据 ← 各证据三态
"""

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


def _as_int(value: object) -> int:
    """安全转整数；非法值按 0 处理。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _as_float(value: object) -> float:
    """安全转浮点；非法值按 0.0 处理。"""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _latency_line(stats_data: dict) -> str | None:
    """拼接 p50 / p95 / max 与慢请求计数。"""
    latency = stats_data.get("latency_ms")
    if not isinstance(latency, dict) or not latency:
        return None
    parts = []
    for key in ("p50", "p95", "max"):
        if key in latency:
            parts.append(f"{key} {_as_int(latency[key])}ms")
    slow = stats_data.get("slow")
    if isinstance(slow, dict):
        parts.append(f"慢请求 {_as_int(slow.get('count'))} 条")
    if not parts:
        return None
    return "耗时 " + " / ".join(parts)


def _node_line(stats_data: dict) -> str | None:
    """按节点展示请求量与 p95；地址一律以序号代替。"""
    nodes = stats_data.get("by_node")
    if not isinstance(nodes, list) or not nodes:
        return None
    parts = []
    for index, node in enumerate(nodes, start=1):
        if not isinstance(node, dict):
            continue
        count = _as_int(node.get("cnt"))
        if "p95" in node:
            parts.append(f"node-{index}: {count} 条 / p95 {_as_int(node['p95'])}ms")
        else:
            parts.append(f"node-{index}: {count} 条")
    if not parts:
        return None
    return "分布 " + "；".join(parts)


def _short_uri(uri: str) -> str:
    """取 URI 最后一段，避免长路径撑爆一行。"""
    segments = [part for part in uri.split("/") if part]
    if not segments:
        return uri
    if len(segments) <= 2:
        return "/" + "/".join(segments)
    return ".../" + "/".join(segments[-2:])


def _impact_line(results: dict[str, EvidenceResult], max_items: int = 3) -> str | None:
    """拼接业务影响：当前量 vs 昨日同时段。"""
    impact = results.get("impact")
    if impact is None or impact.status is not EvidenceStatus.OK:
        return None
    data = impact.data
    if not isinstance(data, dict):
        return None
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        return None
    parts = []
    for item in endpoints[:max_items]:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri")
        if not isinstance(uri, str):
            continue
        pv = _as_int(item.get("pv"))
        base = _as_int(item.get("base_pv"))
        if base > 0:
            delta = (pv - base) * 100.0 / base
            parts.append(f"{_short_uri(uri)} {pv}（昨日 {base}，{delta:+.0f}%）")
        else:
            parts.append(f"{_short_uri(uri)} {pv}（昨日无数据）")
    if not parts:
        return None
    return "业务影响 " + "；".join(parts)


def render_card(
    verdict: TriageVerdict,
    results: dict[str, EvidenceResult],
    explanation: dict,
    max_chars: int,
    alert_line: str = "",
) -> str:
    """渲染三档卡片；超长截断。"""
    stats = results.get("stats")
    stats_data: dict = {}
    if stats is not None and isinstance(stats.data, dict):
        stats_data = stats.data

    lines = [
        f"**[{verdict.tier.value}]** 置信 {explanation.get('confidence', '-')}",
        f"判定 {verdict.reason}",
    ]
    if alert_line:
        lines.append(alert_line)
    lines.append(f"样本 {_as_int(stats_data.get('total'))} 条（窗口内）")

    latency = _latency_line(stats_data)
    if latency:
        lines.append(latency)
    nodes = _node_line(stats_data)
    if nodes:
        lines.append(nodes)
    impact = _impact_line(results)
    if impact:
        lines.append(impact)

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

    # 首行为标题，其余走无序列表——企微气泡按行渲染列表项，普通换行会被折叠成一行
    body = "\n".join(f"- {line}" for line in lines[1:])
    text = _mask(f"{lines[0]}\n\n{body}")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text
