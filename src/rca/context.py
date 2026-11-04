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
_HISTORY_PATH = "/api/annotations"

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


def _reduce_label(model: dict, models: dict[str, dict]) -> str:
    """给归约步骤起一个人能看懂的名字。"""
    expression = model.get("expression")
    source = models.get(expression) if isinstance(expression, str) else None
    if isinstance(source, dict):
        query = source.get("query")
        if isinstance(query, str) and "avg(" in query.lower():
            return "最近一分钟的平均耗时"
    return "归约后的指标值"


def extract_ref_labels(rule: dict) -> tuple[tuple[str, str], ...]:
    """把 refId 翻译成人类可读含义（告警值在卡片上要看得懂）。"""
    items = _parse_query_items(rule)
    models: dict[str, dict] = {}
    for item in items:
        ref = item.get("refId")
        model = item.get("model")
        if isinstance(ref, str) and isinstance(model, dict):
            models[ref] = model
    labels: list[tuple[str, str]] = []
    for item in items:
        ref_id = item.get("refId")
        if not isinstance(ref_id, str):
            continue
        model = item.get("model")
        if not isinstance(model, dict):
            labels.append((ref_id, "查询值"))
            continue
        kind = model.get("type")
        if kind == "reduce":
            labels.append((ref_id, _reduce_label(model, models)))
            continue
        if kind == "threshold":
            threshold = None
            for condition in model.get("conditions", []):
                params = condition.get("evaluator", {}).get("params", [])
                if params:
                    threshold = float(params[0])
                    break
            if threshold is None:
                labels.append((ref_id, "阈值判定"))
            else:
                labels.append((ref_id, f"阈值判定 > {threshold:g}"))
            continue
        query = model.get("query")
        if isinstance(query, str) and "avg(" in query.lower():
            labels.append((ref_id, "平均耗时"))
            continue
        labels.append((ref_id, "查询值"))
    return tuple(labels)


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
        return AlertContext(alertname, folder, None, None, None, None, None, ())

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
            ref_labels=extract_ref_labels(rule),
        )
    return AlertContext(alertname, folder, None, None, None, None, None, ())


def count_active_minutes(annotations: list[dict], alertname: str) -> int:
    """按分钟去重统计该告警出现的分钟数（annotations 是每分钟快照）。"""
    minutes = set()
    for item in annotations:
        text = item.get("text")
        if not isinstance(text, str) or alertname not in text:
            continue
        stamp = item.get("time")
        if not isinstance(stamp, (int, float)):
            continue
        minutes.add(int(stamp) // 60000)
    return len(minutes)


async def fetch_history(
    client: GrafanaClient, alertname: str, start_ms: int, end_ms: int, limit: int = 500
) -> dict[str, int | None]:
    """读取告警注释并统计出现分钟数。"""
    payload = await client.get_json(
        _HISTORY_PATH,
        params={
            "type": "alert",
            "from": str(start_ms),
            "to": str(end_ms),
            "limit": str(limit),
        },
    )
    if not isinstance(payload, list):
        return {"minutes": 0, "last_ts": None}
    minutes = count_active_minutes(payload, alertname)
    stamps = [
        item.get("time")
        for item in payload
        if isinstance(item.get("time"), (int, float))
    ]
    if stamps:
        last_ts = int(max(stamps) // 1000)
    else:
        last_ts = None
    return {"minutes": minutes, "last_ts": last_ts}


def reset_for_test() -> None:
    """清空缓存（仅测试使用）。"""
    _CACHE.clear()
