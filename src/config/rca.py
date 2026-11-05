"""告警三档判定配置（change: alert-triage）。

独立于 settings.py（已接近 400 行红线），解析与校验合一，首次使用时加载并缓存。
错误信息只定位到键名，绝不回显 GRAFANA_TOKEN。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

_DEFAULT_COLLECT_BUDGET_SECONDS: float = 25.0
_DEFAULT_SOURCE_TIMEOUT_SECONDS: float = 8.0
_DEFAULT_RULE_TIMEOUT_SECONDS: float = 5.0
_DEFAULT_HISTORY_TIMEOUT_SECONDS: float = 5.0
_DEFAULT_CONCURRENCY: int = 4
_DEFAULT_SEVERE_LATENCY_MS: int = 3000
_DEFAULT_CACHE_TTL_SECONDS: float = 600.0
_DEFAULT_WINDOW_MINUTES: int = 15
_DEFAULT_BUSINESS_DROP_RATIO: float = 0.5
_DEFAULT_BUSINESS_MIN_BASE_PV: int = 10


@dataclass(frozen=True)
class RcaConfig:
    """本能力的全部可配置项。"""

    enabled: bool  # 总开关；False 时报警轮走原有自由分析
    alert_whitelist: tuple[str, ...]  # 灰度白名单（告警名精确匹配）
    grafana_base_url: str  # 形如 https://host:80
    grafana_token: str  # Service Account Token；空串 = 未配置
    collect_budget_seconds: float  # 采集总预算
    source_timeout_seconds: float  # 单个取数工具超时
    rule_timeout_seconds: float  # 规则反查超时
    history_timeout_seconds: float  # 告警历史超时
    concurrency: int  # 采集并发上限
    severe_latency_ms: int  # 严重阈值（独立于告警阈值）
    cache_ttl_seconds: float  # 规则反查缓存有效期
    business_uris: tuple[str, ...]  # 业务影响要看的核心接口
    rca_source: str = (
        "grafana"  # 取数实现：grafana（经 Grafana 代理）| mcp（经 MCP 服务）
    )
    mcp_base_url: str = ""  # MCP 端点，如 https://host/welife-mcp/mcp
    mcp_token: str = ""  # MCP Bearer token；空串 = 未配置
    mcp_datasource_id: str = ""  # MCP 侧数据源 id；空串 = 用服务默认
    explain_enabled: bool = True  # 是否调用模型生成定性解释
    explain_timeout_seconds: float = 12.0  # 解释调用超时
    window_minutes: int = (
        _DEFAULT_WINDOW_MINUTES  # 取证窗口（分钟）：end_ts=now，start_ts=now-窗口
    )
    business_drop_ratio: float = (
        _DEFAULT_BUSINESS_DROP_RATIO  # 业务量跌幅阈值（对昨日同时段，0.5=跌一半即升档）
    )
    business_min_base_pv: int = (
        _DEFAULT_BUSINESS_MIN_BASE_PV  # 基线最小样本量：昨日同时段低于此值不参与判定
    )
    # 日志工具可查询接口（精确匹配）；空 = 全部拒绝（引用日志档时启动期 fail-fast）
    tool_uri_whitelist: tuple[str, ...] = ()


def _env_bool(name: str, default: bool) -> bool:
    """读布尔环境变量；非法值 fail-fast。"""
    raw = os.getenv(name, "").strip().lower()
    if raw == "":
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_float(name: str, default: float) -> float:
    """读正数有限浮点；非法值 fail-fast，错误只回显键名。"""
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_int(name: str, default: int) -> int:
    """读正整数；非法值 fail-fast，错误只回显键名。"""
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_list(name: str) -> tuple[str, ...]:
    """读逗号分隔列表，兼容全角逗号，去空白并丢弃空项。"""
    raw = os.getenv(name, "").strip()
    if raw == "":
        return ()
    raw = raw.replace("，", ",")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


_CACHE: RcaConfig | None = None


def load_rca_config() -> RcaConfig:
    """加载并缓存配置；首次调用后复用。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    _CACHE = RcaConfig(
        enabled=_env_bool("RCA_ENABLED", False),
        alert_whitelist=_env_list("RCA_ALERT_WHITELIST"),
        grafana_base_url=os.getenv("GRAFANA_BASE_URL", "").strip(),
        grafana_token=os.getenv("GRAFANA_TOKEN", "").strip(),
        collect_budget_seconds=_env_float(
            "RCA_COLLECT_BUDGET_SECONDS", _DEFAULT_COLLECT_BUDGET_SECONDS
        ),
        source_timeout_seconds=_env_float(
            "RCA_SOURCE_TIMEOUT_SECONDS", _DEFAULT_SOURCE_TIMEOUT_SECONDS
        ),
        rule_timeout_seconds=_env_float(
            "RCA_RULE_TIMEOUT_SECONDS", _DEFAULT_RULE_TIMEOUT_SECONDS
        ),
        history_timeout_seconds=_env_float(
            "RCA_HISTORY_TIMEOUT_SECONDS", _DEFAULT_HISTORY_TIMEOUT_SECONDS
        ),
        concurrency=_env_int("RCA_CONCURRENCY", _DEFAULT_CONCURRENCY),
        severe_latency_ms=_env_int("RCA_SEVERE_LATENCY_MS", _DEFAULT_SEVERE_LATENCY_MS),
        cache_ttl_seconds=_env_float(
            "RCA_CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS
        ),
        business_uris=_env_list("RCA_BUSINESS_URIS"),
        window_minutes=_env_int("RCA_WINDOW_MINUTES", _DEFAULT_WINDOW_MINUTES),
        rca_source=os.getenv("RCA_SOURCE", "grafana").strip() or "grafana",
        mcp_base_url=os.getenv("MCP_BASE_URL", "").strip(),
        mcp_token=os.getenv("MCP_AUTH_TOKEN", "").strip(),
        mcp_datasource_id=os.getenv("MCP_DATASOURCE_ID", "").strip(),
        explain_enabled=_env_bool("RCA_EXPLAIN_ENABLED", True),
        explain_timeout_seconds=_env_float("RCA_EXPLAIN_TIMEOUT_SECONDS", 12.0),
        business_drop_ratio=_env_float(
            "RCA_BUSINESS_DROP_RATIO", _DEFAULT_BUSINESS_DROP_RATIO
        ),
        business_min_base_pv=_env_int(
            "RCA_BUSINESS_MIN_BASE_PV", _DEFAULT_BUSINESS_MIN_BASE_PV
        ),
        tool_uri_whitelist=_env_list("RCA_TOOL_URI_WHITELIST"),
    )
    return _CACHE


def reset_for_test() -> None:
    """清空缓存（仅测试使用）。"""
    global _CACHE
    _CACHE = None
