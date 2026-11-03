"""告警上下文：从 Grafana 规则定义补齐 uri / 阈值 / for 时长。

规则查询是"字符串套 JSON"，需二次解析；网关类规则是 PromQL，解析不出 uri
时返回 None，由调用方降级为"语义未知"。
"""

from __future__ import annotations

import json
import re
import time

from src.config.rca import RcaConfig
from src.infra.grafana_client import GrafanaClient, GrafanaError
from src.rca.models import AlertContext

_URI_PATTERN = re.compile(r"request_uri\s*=\s*'([^']+)'")
_RULES_PATH = "/api/prometheus/grafana/api/v1/rules"

_CACHE: dict[str, tuple[float, list[dict]]] = {}


def extract_uri(query: str) -> str | None:
    """从 SLS 查询语句中提取 request_uri；提取不到返回 None。"""
    match = _URI_PATTERN.search(query)
    if match is None:
        return None
    return match.group(1)


def _parse_query_items(rule: dict) -> list[dict]:
    """把 rule['query'] 解析成条目列表；失败返回空列表。"""
    raw = rule.get("query")
    if not isinstance(raw, str) or raw == "":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def extract_logstore(rule: dict) -> str | None:
    """从查询条目里取 logstore。"""
    for item in _parse_query_items(rule):
        model = item.get("model")
        if isinstance(model, dict) and isinstance(model.get("logstore"), str):
            return model["logstore"]
    return None


def extract_datasource_uid(rule: dict) -> str | None:
    """从查询条目里取数据源 uid（跳过 __expr__）。"""
    for item in _parse_query_items(rule):
        uid = item.get("datasourceUid")
        if isinstance(uid, str) and uid != "__expr__":
            return uid
    return None


def extract_threshold(rule: dict) -> float | None:
    """从 threshold 类型的条目里取阈值。"""
    for item in _parse_query_items(rule):
        model = item.get("model")
        if not isinstance(model, dict) or model.get("type") != "threshold":
            continue
        for condition in model.get("conditions", []):
            params = condition.get("evaluator", {}).get("params", [])
            if params:
                return float(params[0])
    return None


def extract_sls_query(rule: dict) -> str:
    """取第一条 SLS 查询语句原文；没有返回空串。"""
    for item in _parse_query_items(rule):
        model = item.get("model")
        if isinstance(model, dict) and isinstance(model.get("query"), str):
            return model["query"]
    return ""


async def _fetch_rules(client: GrafanaClient, cfg: RcaConfig) -> list[dict]:
    """拉取全部规则并拍平（附文件夹名），带 TTL 缓存。"""
    now = time.monotonic()
    cached = _CACHE.get("rules")
    if cached is not None and now - cached[0] < cfg.cache_ttl_seconds:
        return cached[1]
    payload = await client.get_json(_RULES_PATH)
    rules: list[dict] = []
    if isinstance(payload, dict):
        data = payload.get("data", {})
    else:
        data = {}
    for group in data.get("groups", []):
        folder = group.get("file", "")
        for rule in group.get("rules", []):
            rule["_folder"] = folder
            rules.append(rule)
    _CACHE["rules"] = (now, rules)
    return rules


async def build_alert_context(
    client: GrafanaClient, alertname: str, folder: str, cfg: RcaConfig
) -> AlertContext:
    """按 alertname + folder 反查规则并补齐上下文；失败返回语义未知的上下文。"""
    try:
        rules = await _fetch_rules(client, cfg)
    except GrafanaError:
        return AlertContext(alertname, folder, None, None, None, None, None)

    for rule in rules:
        if rule.get("name") != alertname:
            continue
        if rule.get("_folder") != folder:
            continue
        sls_query = extract_sls_query(rule)
        return AlertContext(
            alertname=alertname,
            folder=folder,
            uri=extract_uri(sls_query),
            logstore=extract_logstore(rule),
            datasource_uid=extract_datasource_uid(rule),
            threshold=extract_threshold(rule),
            for_seconds=int(rule.get("duration", 0)) or None,
        )
    return AlertContext(alertname, folder, None, None, None, None, None)


def reset_for_test() -> None:
    """清空缓存（仅测试使用）。"""
    _CACHE.clear()
