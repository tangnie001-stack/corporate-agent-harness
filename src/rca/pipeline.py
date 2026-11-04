"""报警轮的判定编排：开关 → 上下文 → 采集 → 判定 → 渲染。

任何环节失败都不抛给调用方：返回原 query + 空判定，由调用方走原行为与兜底。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.config.const import RCA_CARD_MAX_CHARS
from src.config.rca import RcaConfig
from src.core.logging import encode_value
from src.infra.grafana_client import GrafanaClient
from src.rca.collector import collect_evidence
from src.rca.context import build_alert_context, fetch_history
from src.rca.judge import judge
from src.rca.loader import load_skills, match_skill
from src.rca.models import EvidenceResult, EvidenceStatus, TriageVerdict
from src.rca.render import render_card
from src.rca.sources.grafana import GrafanaLogSource
from src.rca.sources.mcp import McpLogSource

if TYPE_CHECKING:
    from src.services.alert_service import AlertEvent

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


@dataclass(frozen=True)
class TriageOutcome:
    """一次判定编排的产物。"""

    query: str  # 喂给 Agent 的文本；未判定时为原 query
    verdict: TriageVerdict | None  # 判定结果；未判定或失败为 None
    card: str = ""  # 卡片文本；未判定为空串


HISTORY_DAYS = 7


def should_triage(alertname: str, cfg: RcaConfig) -> bool:
    """开关与白名单判断。"""
    if not cfg.enabled:
        return False
    return alertname in cfg.alert_whitelist


async def collect_history(
    client: GrafanaClient,
    alertname: str,
    end_ts: int,
    cfg: RcaConfig,
) -> EvidenceResult:
    """取近 7 天同告警的出现分钟数，作为"反复出现"的正向证据。

    Args:
        client: Grafana 客户端
        alertname: 告警名
        end_ts: 取证结束时刻（秒）
        cfg: 判定配置（用 history_timeout_seconds 限制耗时）

    Returns:
        EvidenceResult：取到为 OK（data={"minutes": N}），失败为 EMPTY。
        失败不抛给调用方——history 只是正向证据之一，缺失时判定自然落观察档。
    """
    started = time.monotonic()
    start_ms = (end_ts - HISTORY_DAYS * 86400) * 1000
    try:
        data = await asyncio.wait_for(
            fetch_history(client, alertname, start_ms=start_ms, end_ms=end_ts * 1000),
            timeout=cfg.history_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001  历史取不到不得中断判定
        logger.warning(
            "[rca] history failed alert={} err={}",
            encode_value(alertname),
            encode_value(type(exc).__name__),
        )
        return EvidenceResult(
            "history",
            EvidenceStatus.EMPTY,
            None,
            int((time.monotonic() - started) * 1000),
            f"history_failed:{type(exc).__name__}",
        )
    minutes = int(data.get("minutes") or 0)
    return EvidenceResult(
        "history",
        EvidenceStatus.OK,
        {"minutes": minutes},
        int((time.monotonic() - started) * 1000),
    )


async def run_triage(
    event: AlertEvent,
    base_query: str,
    cfg: RcaConfig,
    skills_root: Path = SKILLS_ROOT,
) -> TriageOutcome:
    """执行判定并返回增强 query + 判定结果；未判定或失败时只回原 query。

    取证窗口由本层按告警时刻统一注入（end_ts=now，start_ts=now-window），
    与告警的实际触发时间对齐；窗口不是 skill 需要声明的参数。

    Args:
        event: 解析后的告警事件（用 alertname / folder 反查规则）
        base_query: 原有喂给 Agent 的文本
        cfg: 判定配置
        skills_root: rca skill 根目录

    Returns:
        TriageOutcome；verdict 为 None 表示本次未判定（开关关闭 / 白名单不中 /
        上下文或 skill 缺失 / 任何环节异常），调用方据此走原行为。
    """
    if not should_triage(event.alertname, cfg):
        return TriageOutcome(base_query, None)

    client = GrafanaClient(
        cfg.grafana_base_url, cfg.grafana_token, cfg.source_timeout_seconds
    )
    try:
        context = await build_alert_context(client, event.alertname, event.folder, cfg)
        if context.uri is None:
            logger.info(
                "[rca] context unresolved alert={}", encode_value(event.alertname)
            )
            return TriageOutcome(base_query, None)

        skill = match_skill(load_skills(skills_root), event.alertname)
        if skill is None:
            logger.info("[rca] no skill alert={}", encode_value(event.alertname))
            return TriageOutcome(base_query, None)

        end_ts = int(time.time())
        start_ts = end_ts - cfg.window_minutes * 60
        source = _build_source(client, context, cfg)
        fields = {
            "uri": context.uri,
            "alertname": context.alertname,
            "start_ts": start_ts,
            "end_ts": end_ts,
        }
        results = await collect_evidence(skill.evidence, source, fields, cfg)
        history = await collect_history(client, event.alertname, end_ts, cfg)
        results["history"] = history
        history_minutes: int | None = None
        if history.status is EvidenceStatus.OK and isinstance(history.data, dict):
            history_minutes = int(history.data.get("minutes", 0))
        verdict = judge(results, cfg.severe_latency_ms, history_minutes)
        card = render_card(
            verdict, results, {}, RCA_CARD_MAX_CHARS, _alert_values_line(event, context)
        )
        query = base_query + "\n\n" + skill.body + "\n\n[证据]\n" + card
        return TriageOutcome(query, verdict, card)
    except Exception as exc:  # noqa: BLE001  判定失败不得影响报警轮
        logger.warning(
            "[rca] triage failed alert={} err={}",
            encode_value(event.alertname),
            encode_value(type(exc).__name__),
        )
        return TriageOutcome(base_query, None)


def _build_source(client: GrafanaClient, context, cfg):
    """按配置选择取数实现：mcp 走独立服务，其余走 Grafana 代理。"""
    if cfg.rca_source == "mcp":
        slow_ms = 2000
        if context.threshold is not None:
            slow_ms = int(context.threshold)
        return McpLogSource(
            cfg.mcp_base_url,
            cfg.mcp_token,
            datasource_id=cfg.mcp_datasource_id or None,
            timeout=cfg.source_timeout_seconds,
            uris=cfg.business_uris,
            slow_ms=slow_ms,
        )
    return GrafanaLogSource(client, context, cfg)


def _alert_values_line(event, context) -> str:
    """把告警自带的 refId 值翻译成一行；无值或无标签时返回空串。"""
    values = getattr(event, "values", ())
    if not values:
        return ""
    labels = dict(context.ref_labels)
    parts = []
    for ref_id, value in values:
        label = labels.get(ref_id, "")
        if label:
            parts.append(f"{ref_id}={value}（{label}）")
        else:
            parts.append(f"{ref_id}={value}")
    return "告警值 " + "；".join(parts)
