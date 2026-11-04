"""告警判读的模型解释：只产出定性字段，档位仍由代码决定。

输出经结构校验后交给渲染层；任何异常（超时、JSON 不合法、字段缺失）都退化为空字典，
卡片照常输出定量内容，不因模型失败而阻塞。
"""

from __future__ import annotations

import asyncio
import json
import re

from loguru import logger

from src.config.prompts import loader
from src.core.logging import encode_value
from src.models import get_summary_llm
from src.rca.models import EvidenceResult, EvidenceStatus, TriageVerdict

_ALLOWED_KEYS = (
    "summary",
    "hypotheses",
    "counter_evidence",
    "next_actions",
    "confidence",
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def build_digest(verdict: TriageVerdict, results: dict[str, EvidenceResult]) -> str:
    """把证据压成一段短文本交给模型；不含原始日志与内网地址。"""
    lines = [
        f"档位: {verdict.tier.value}",
        f"判定依据: {verdict.reason}",
        f"样本量: {verdict.sample_size}",
    ]
    stats = results.get("stats")
    if stats is not None and isinstance(stats.data, dict):
        latency = stats.data.get("latency_ms")
        if isinstance(latency, dict):
            parts = [
                f"{key}={latency[key]}"
                for key in ("p50", "p95", "max")
                if key in latency
            ]
            if parts:
                lines.append("耗时(ms): " + " ".join(parts))
        slow = stats.data.get("slow")
        if isinstance(slow, dict):
            lines.append(
                f"慢请求数: {slow.get('count')}（阈值 {slow.get('threshold')}ms）"
            )
        errors = stats.data.get("errors")
        if isinstance(errors, dict):
            lines.append(f"5xx: {errors.get('5xx')}")
        nodes = stats.data.get("by_node")
        if isinstance(nodes, list) and len(nodes) > 1:
            lines.append(f"节点数: {len(nodes)}")
    impact = results.get("impact")
    if (
        impact is not None
        and impact.status is EvidenceStatus.OK
        and isinstance(impact.data, dict)
    ):
        endpoints = impact.data.get("endpoints")
        if isinstance(endpoints, list) and endpoints:
            item = endpoints[0]
            if isinstance(item, dict):
                lines.append(
                    f"业务量: 当前 {item.get('pv')}，昨日 {item.get('base_pv')}"
                )
    missing = [
        item.id for item in results.values() if item.status is not EvidenceStatus.OK
    ]
    if missing:
        lines.append("未取到: " + "、".join(missing))
    return "\n".join(lines)


def _parse_json(text: str) -> dict:
    """解析模型输出：容忍代码围栏，字段缺失即失败。"""
    cleaned = _FENCE.sub("", text.strip())
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise TypeError("not a json object")
    result = {}
    for key in _ALLOWED_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip() != "":
            result[key] = value.strip()[:200]
    return result


async def explain(
    verdict: TriageVerdict,
    results: dict[str, EvidenceResult],
    timeout: float,
) -> dict:
    """调用模型产出定性解释；失败返回空字典。"""
    system = loader.get_content("task-rca-explain-system").strip()
    digest = build_digest(verdict, results)

    async def _invoke() -> dict:
        llm = get_summary_llm()
        message = await llm.ainvoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": digest},
            ]
        )
        content = message.content
        if not isinstance(content, str):
            raise TypeError("non-text content")
        return _parse_json(content)

    try:
        return await asyncio.wait_for(_invoke(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001  解释失败不得影响卡片
        logger.warning("[rca] explain failed err={}", encode_value(type(exc).__name__))
        return {}
