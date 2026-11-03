# 告警三档判定（alert-triage）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 把报警轮从"自由分析"改为"证据驱动的三档判定"，让值班人看到「无需处理」即可关单。

**Architecture:** 代码负责取数与定档（确定性），skill 负责声明证据清单与判断规则（可配置），模型只产出解释文本。取数统一经 Grafana 的 /api/ds/query 代理，规则与历史分别走 ruler API 与 annotations API。

**Tech Stack:** Python 3.11 / FastAPI / pytest / httpx / PyYAML

**Spec:** docs/openspec/changes/alert-triage/（proposal.md / design.md / specs/alert-triage/spec.md / specs/alert-notify/spec.md）

## Global Constraints

- 采集 SHALL 在生成之前**完全收口**，与生成串行；采集总预算 25s、单源 8s、并发上限 4。
- 档位 SHALL 由代码计算；模型只输出 summary / hypotheses / counter_evidence / next_actions / confidence。
- POST /api/ds/query 的成功判定只看 frames[].data.values 是否非空，**不看 status**（实测成功也返回 500）。
- 进入提示词的日志字段白名单：ts / upstream_addr / request_time / status / upstream_status / method / uri；不含 UA、referer、查询串。
- 单文件 ≤ 400 行、单函数 ≤ 80 行；不用三元表达式；函数必须有中文 docstring。
- 日志用 [rca] 前缀 + 英文 k=v，值经 encode_value。
- 配置进 src/config/rca.py（不追加到已超 400 行的 settings.py / const.py）。
- 能力默认关闭；关闭时行为与变更前一致。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| src/config/rca.py | 本能力全部配置（开关、白名单、超时、阈值、核心接口） |
| src/rca/models.py | AlertContext / EvidenceSpec / EvidenceResult / TriageVerdict |
| src/rca/context.py | 规则反查（ruler API）+ 解析 uri/阈值/for + 缓存 |
| src/rca/sources/base.py | LogSource 协议与两类异常 |
| src/rca/sources/fixture.py | 测试用固定数据源 |
| src/rca/sources/grafana.py | ds/query 组装与列式解析 |
| src/rca/loader.py | skill frontmatter 解析与 alertname 匹配 |
| src/rca/collector.py | 两波并行、超时、三态、required 短路 |
| src/rca/judge.py | 样本量校验 + 四条件 + 档位 |
| src/rca/render.py | 卡片渲染、定量注入、脱敏 |
| src/rca/pipeline.py | 编排：开关 → 上下文 → 采集 → 判定 → 渲染 |
| src/infra/grafana_client.py | Grafana HTTP 客户端 |
| skills/rca-latency/SKILL.md | 证据清单与判定正文 |

---

### Task 1: 配置与数据模型

**Files:**
- Create: src/config/rca.py
- Create: src/rca/__init__.py
- Create: src/rca/models.py
- Test: tests/config/test_rca_config.py

**Interfaces:**
- Produces: RcaConfig、load_rca_config()、reset_for_test()、Tier、EvidenceStatus、EvidenceSpec、EvidenceResult、AlertContext、TriageVerdict

- [ ] **Step 1: 写失败测试**

~~~python
# tests/config/test_rca_config.py
from src.config.rca import load_rca_config, reset_for_test


def test_defaults_disabled(monkeypatch):
    reset_for_test()
    monkeypatch.delenv("RCA_ENABLED", raising=False)
    cfg = load_rca_config()
    assert cfg.enabled is False
    assert cfg.collect_budget_seconds == 25.0


def test_whitelist_parsed(monkeypatch):
    reset_for_test()
    monkeypatch.setenv("RCA_ENABLED", "true")
    monkeypatch.setenv("RCA_ALERT_WHITELIST", "支付单createOrder响应时间上升, 用户登录userlogin响应时间上升")
    cfg = load_rca_config()
    assert cfg.enabled is True
    assert "支付单createOrder响应时间上升" in cfg.alert_whitelist
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/config/test_rca_config.py -v
Expected: ModuleNotFoundError: src.config.rca

- [ ] **Step 3: 实现配置与模型**

~~~python
# src/config/rca.py
"""告警三档判定配置（change: alert-triage）。

独立于 settings.py（已接近 400 行红线），解析与校验合一，首次使用时加载并缓存。
错误信息只定位到键名，绝不回显 GRAFANA_TOKEN。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_COLLECT_BUDGET_SECONDS: float = 25.0
_DEFAULT_SOURCE_TIMEOUT_SECONDS: float = 8.0
_DEFAULT_RULE_TIMEOUT_SECONDS: float = 5.0
_DEFAULT_HISTORY_TIMEOUT_SECONDS: float = 5.0
_DEFAULT_CONCURRENCY: int = 4
_DEFAULT_SEVERE_LATENCY_MS: int = 3000
_DEFAULT_CACHE_TTL_SECONDS: float = 600.0


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
    """读正数浮点；非法值 fail-fast。"""
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_int(name: str, default: int) -> int:
    """读正整数；非法值 fail-fast。"""
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_list(name: str) -> tuple[str, ...]:
    """读逗号分隔列表，去空白并丢弃空项。"""
    raw = os.getenv(name, "").strip()
    if raw == "":
        return ()
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
        collect_budget_seconds=_env_float("RCA_COLLECT_BUDGET_SECONDS", _DEFAULT_COLLECT_BUDGET_SECONDS),
        source_timeout_seconds=_env_float("RCA_SOURCE_TIMEOUT_SECONDS", _DEFAULT_SOURCE_TIMEOUT_SECONDS),
        rule_timeout_seconds=_env_float("RCA_RULE_TIMEOUT_SECONDS", _DEFAULT_RULE_TIMEOUT_SECONDS),
        history_timeout_seconds=_env_float("RCA_HISTORY_TIMEOUT_SECONDS", _DEFAULT_HISTORY_TIMEOUT_SECONDS),
        concurrency=_env_int("RCA_CONCURRENCY", _DEFAULT_CONCURRENCY),
        severe_latency_ms=_env_int("RCA_SEVERE_LATENCY_MS", _DEFAULT_SEVERE_LATENCY_MS),
        cache_ttl_seconds=_env_float("RCA_CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS),
        business_uris=_env_list("RCA_BUSINESS_URIS"),
    )
    return _CACHE


def reset_for_test() -> None:
    """清空缓存（仅测试使用）。"""
    global _CACHE
    _CACHE = None
~~~

~~~python
# src/rca/models.py
"""告警三档判定的值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    """判定档位。"""

    NO_ACTION = "无需处理"
    WATCH = "建议观察"
    NEED_ACTION = "需要处理"


class EvidenceStatus(str, Enum):
    """证据三态。"""

    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass(frozen=True)
class EvidenceSpec:
    """skill 声明的一条证据需求。"""

    id: str  # 证据标识，渲染与日志引用
    tool: str  # 工具名（对应 LogSource 方法）
    params: dict[str, str] = field(default_factory=dict)  # 含 alert.* 占位符
    required: bool = True  # 必需源失败则短路
    timeout: float = 8.0  # 单源超时（秒）
    when: str | None = None  # 简易条件，如 "stats.total < 20"


@dataclass(frozen=True)
class EvidenceResult:
    """一条证据的执行结果。"""

    id: str  # 与 EvidenceSpec.id 对应
    status: EvidenceStatus  # 三态
    data: dict | list | None  # 结构化结果；failed 时为 None
    elapsed_ms: int  # 执行耗时
    reason: str = ""  # failed 时的原因码


@dataclass(frozen=True)
class AlertContext:
    """补齐后的告警上下文。"""

    alertname: str  # 告警名
    folder: str  # Grafana 文件夹
    uri: str | None  # 接口路径；解析失败为 None
    logstore: str | None  # SLS logstore
    datasource_uid: str | None  # Grafana 数据源 uid
    threshold: float | None  # 告警阈值
    for_seconds: int | None  # 持续时间


@dataclass(frozen=True)
class TriageVerdict:
    """判定结果（档位由代码计算，解释由模型补充）。"""

    tier: Tier  # 档位
    reason: str  # 定档依据（代码生成，人可读）
    sample_size: int  # 窗口样本量
    severe_hit: bool  # 是否触及严重阈值
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/config/test_rca_config.py -v
Expected: 2 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/config/rca.py src/rca/__init__.py src/rca/models.py tests/config/test_rca_config.py
git commit -m "feat(rca): 配置与值对象"
~~~


### Task 2: Grafana HTTP 客户端

**Files:**
- Create: src/infra/grafana_client.py
- Test: tests/infra/test_grafana_client.py

**Interfaces:**
- Produces: GrafanaClient(base_url, token, timeout, transport=None)；get_json(path, params=None)；post_json(path, body)；GrafanaError(message, status)

- [ ] **Step 1: 写失败测试**

~~~python
# tests/infra/test_grafana_client.py
import httpx
import pytest
from src.infra.grafana_client import GrafanaClient, GrafanaError


@pytest.mark.asyncio
async def test_bearer_header_sent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    client = GrafanaClient("https://g:80", "glsa_x", timeout=5.0, transport=httpx.MockTransport(handler))
    assert await client.get_json("/api/health") == {"ok": True}
    assert captured["auth"] == "Bearer glsa_x"


@pytest.mark.asyncio
async def test_401_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    client = GrafanaClient("https://g:80", "bad", timeout=5.0, transport=httpx.MockTransport(handler))
    with pytest.raises(GrafanaError) as exc:
        await client.get_json("/api/health")
    assert exc.value.status == 401
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/infra/test_grafana_client.py -v
Expected: ModuleNotFoundError: src.infra.grafana_client

- [ ] **Step 3: 实现客户端**

~~~python
# src/infra/grafana_client.py
"""Grafana HTTP 客户端：Bearer 鉴权、超时与错误归一。

只做传输，不做业务解析；调用方负责解释返回体。token 不进入日志。
"""

from __future__ import annotations

import httpx


class GrafanaError(RuntimeError):
    """Grafana 调用失败。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # HTTP 状态码；连接类错误为 None


class GrafanaClient:
    """异步 Grafana 客户端。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        """构造鉴权头；未配置 token 时不加。"""
        if self._token == "":
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> dict | list:
        """GET 并解析 JSON；非 2xx 抛 GrafanaError。"""
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            try:
                resp = await client.get(self._base + path, params=params, headers=self._headers())
            except httpx.HTTPError as exc:
                raise GrafanaError(f"request failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise GrafanaError(f"http {resp.status_code}", status=resp.status_code)
        return resp.json()

    async def post_json(self, path: str, body: dict) -> dict:
        """POST JSON 并解析返回；非 2xx 抛 GrafanaError。"""
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            try:
                resp = await client.post(self._base + path, json=body, headers=self._headers())
            except httpx.HTTPError as exc:
                raise GrafanaError(f"request failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise GrafanaError(f"http {resp.status_code}", status=resp.status_code)
        return resp.json()
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/infra/test_grafana_client.py -v
Expected: 2 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/infra/grafana_client.py tests/infra/test_grafana_client.py
git commit -m "feat(rca): Grafana HTTP 客户端"
~~~

---

### Task 3: 规则反查与解析

**Files:**
- Create: src/rca/context.py
- Test: tests/rca/test_context.py

**Interfaces:**
- Consumes: GrafanaClient.get_json
- Produces: extract_uri(query)、extract_logstore(rule)、extract_datasource_uid(rule)、extract_threshold(rule)、build_alert_context(client, alertname, folder, cfg)

- [ ] **Step 1: 写失败测试**

~~~python
# tests/rca/test_context.py
from src.rca.context import extract_logstore, extract_threshold, extract_uri

SLS_QUERY = (
    "* | select avg(upstream_response_time) * 1000 as x, __time__-__time__%60 as t "
    "where request_uri = '/welife-pay/pay/createOrder' group by t"
)


def test_extract_uri_from_sls_query():
    assert extract_uri(SLS_QUERY) == "/welife-pay/pay/createOrder"


def test_extract_uri_absent_returns_none():
    assert extract_uri("sum(increase(envoy_cluster_upstream_rq[1m]))") is None


def test_extract_logstore():
    rule = {"query": '[{"refId":"A","model":{"logstore":"welife-c53n"}}]'}
    assert extract_logstore(rule) == "welife-c53n"


def test_extract_threshold_from_threshold_step():
    rule = {
        "query": (
            '[{"refId":"C","model":{"type":"threshold","conditions":'
            '[{"evaluator":{"type":"gt","params":[1000]}}]}}]'
        )
    }
    assert extract_threshold(rule) == 1000.0
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_context.py -v
Expected: ModuleNotFoundError: src.rca.context

- [ ] **Step 3: 实现解析与反查**

~~~python
# src/rca/context.py
"""告警上下文：从 Grafana 规则定义补齐 uri / 阈值 / for 时长。

规则查询是"字符串套 JSON"，需二次解析；网关类规则是 PromQL，解析不出 uri
时返回 None，由调用方降级为"语义未知"。
"""

from __future__ import annotations

import json
import re
import time

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


async def _fetch_rules(client: GrafanaClient, cfg) -> list[dict]:
    """拉取全部规则并拍平（附文件夹名），带 TTL 缓存。"""
    now = time.monotonic()
    cached = _CACHE.get("rules")
    if cached is not None and now - cached[0] < cfg.cache_ttl_seconds:
        return cached[1]
    payload = await client.get_json(_RULES_PATH)
    rules: list[dict] = []
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    for group in data.get("groups", []):
        folder = group.get("file", "")
        for rule in group.get("rules", []):
            rule["_folder"] = folder
            rules.append(rule)
    _CACHE["rules"] = (now, rules)
    return rules


async def build_alert_context(
    client: GrafanaClient, alertname: str, folder: str, cfg
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
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_context.py -v
Expected: 4 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/context.py tests/rca/test_context.py
git commit -m "feat(rca): 规则反查与 uri/阈值解析"
~~~


---

### Task 3B: 告警历史（annotations）

**Files:**
- Modify: src/rca/context.py（追加函数）
- Test: tests/rca/test_history.py

**Interfaces:**
- Produces: fetch_history(client, alertname, start_ms, end_ms, cfg) -> dict，形如 {"minutes": 12, "last_ts": 1792521360}

- [ ] **Step 1: 写失败测试**

~~~python
# tests/rca/test_history.py
import pytest
from src.rca.context import count_active_minutes


def test_counts_distinct_minutes():
    annotations = [
        {"time": 1000, "text": "支付单createOrder响应时间上升 {alertname=支付单createOrder响应时间上升}"},
        {"time": 61000, "text": "支付单createOrder响应时间上升 {alertname=支付单createOrder响应时间上升}"},
        {"time": 61000, "text": "支付单createOrder响应时间上升 {alertname=支付单createOrder响应时间上升}"},
        {"time": 121000, "text": "其他告警 {alertname=其他告警}"},
    ]
    assert count_active_minutes(annotations, "支付单createOrder响应时间上升") == 2
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_history.py -v
Expected: ImportError: cannot import name count_active_minutes

- [ ] **Step 3: 实现历史读取**

~~~python
# 追加到 src/rca/context.py

_HISTORY_PATH = "/api/annotations"


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
) -> dict:
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
    stamps = [item.get("time") for item in payload if isinstance(item.get("time"), (int, float))]
    last_ts = int(max(stamps) // 1000) if stamps else None
    return {"minutes": minutes, "last_ts": last_ts}
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_history.py -v
Expected: 1 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/context.py tests/rca/test_history.py
git commit -m "feat(rca): 告警历史分钟数统计"
~~~

---


### Task 4: LogSource 协议与 fixture

**Files:**
- Create: src/rca/sources/__init__.py
- Create: src/rca/sources/base.py
- Create: src/rca/sources/fixture.py
- Test: tests/rca/test_fixture_source.py

**Interfaces:**
- Produces: LogSource 协议、SourceEmptyError、SourceFailedError、FixtureLogSource(data)

- [ ] **Step 1: 写失败测试**

~~~python
# tests/rca/test_fixture_source.py
import pytest
from src.rca.sources.base import SourceEmptyError
from src.rca.sources.fixture import FixtureLogSource


@pytest.mark.asyncio
async def test_returns_configured_stats():
    src = FixtureLogSource({"latency_stats": {"total": 2, "latency_ms": {"p95": 1057.0}, "by_node": []}})
    out = await src.latency_stats(uri="/x", start_ts=1, end_ts=2)
    assert out["total"] == 2


@pytest.mark.asyncio
async def test_missing_key_raises():
    src = FixtureLogSource({})
    with pytest.raises(SourceEmptyError):
        await src.latency_stats(uri="/x", start_ts=1, end_ts=2)
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_fixture_source.py -v
Expected: ModuleNotFoundError: src.rca.sources

- [ ] **Step 3: 实现协议与 fixture**

~~~python
# src/rca/sources/base.py
"""取数源协议与异常。"""

from __future__ import annotations

from typing import Protocol


class SourceEmptyError(LookupError):
    """查到但无数据（对应 empty 态）。"""


class SourceFailedError(RuntimeError):
    """未查到（对应 failed 态）。"""


class LogSource(Protocol):
    """日志取数能力；实现可替换为 fixture / Grafana / 未来的 MCP。"""

    async def latency_stats(self, uri: str, start_ts: int, end_ts: int, slow_ms: int = 2000) -> dict:
        """返回窗口样本量、分位、慢请求计数与节点分布。"""
        ...

    async def request_detail(self, uri: str, start_ts: int, end_ts: int, limit: int = 100) -> list:
        """返回窗口内请求明细。"""
        ...

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """返回核心接口的请求量与昨日同时段对比。"""
        ...
~~~

~~~python
# src/rca/sources/fixture.py
"""测试用固定数据源：按方法名返回预置结构，缺失视为 empty。"""

from __future__ import annotations

from src.rca.sources.base import SourceEmptyError


class FixtureLogSource:
    """从 dict 读取数据；仅用于测试与非生产环境。"""

    def __init__(self, data: dict) -> None:
        self._data = data

    def _get(self, key: str):
        """取预置数据；不存在抛 empty。"""
        if key not in self._data:
            raise SourceEmptyError(key)
        return self._data[key]

    async def latency_stats(self, uri: str, start_ts: int, end_ts: int, slow_ms: int = 2000) -> dict:
        """返回预置的窗口统计。"""
        return dict(self._get("latency_stats"))

    async def request_detail(self, uri: str, start_ts: int, end_ts: int, limit: int = 100) -> list:
        """返回预置的请求明细。"""
        return list(self._get("request_detail"))

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """返回预置的业务影响。"""
        return dict(self._get("business_impact"))
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_fixture_source.py -v
Expected: 2 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/sources tests/rca/test_fixture_source.py
git commit -m "feat(rca): 取数协议与 fixture 实现"
~~~

---

### Task 5: GrafanaLogSource（ds/query 代理）

**Files:**
- Create: src/rca/sources/grafana.py
- Test: tests/rca/test_grafana_source.py

**Interfaces:**
- Consumes: GrafanaClient.post_json、AlertContext
- Produces: parse_frame_column(payload, ref_id, field)、GrafanaLogSource(client, context, cfg)

- [ ] **Step 1: 写失败测试**

~~~python
# tests/rca/test_grafana_source.py
from src.rca.sources.grafana import parse_frame_column


def test_parse_frame_column_by_name():
    payload = {
        "results": {"A": {"status": 500, "frames": [{
            "schema": {"fields": [{"name": "time"}, {"name": "cnt"}]},
            "data": {"values": [[1000, 2000], [3, 4]]},
        }]}}
    }
    assert parse_frame_column(payload, "A", "cnt") == [3, 4]


def test_parse_frame_column_empty_frames():
    payload = {"results": {"A": {"status": 500, "frames": []}}}
    assert parse_frame_column(payload, "A", "cnt") == []
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_grafana_source.py -v
Expected: ModuleNotFoundError: src.rca.sources.grafana

- [ ] **Step 3: 实现取数**

~~~python
# src/rca/sources/grafana.py
"""Grafana ds/query 取数实现。

实测：成功查询的 status 也可能是 500，数据在 frames[].data.values（列式）。
故成功判定只看 frames 与 values 是否非空，不看 status。
"""

from __future__ import annotations

from src.infra.grafana_client import GrafanaClient, GrafanaError
from src.rca.models import AlertContext
from src.rca.sources.base import SourceEmptyError, SourceFailedError

_QUERY_PATH = "/api/ds/query"


def parse_frame_column(payload: dict, ref_id: str, field: str) -> list:
    """按字段名从列式 frames 中取值；不存在返回空列表。"""
    result = payload.get("results", {}).get(ref_id, {})
    frames = result.get("frames", [])
    if not frames:
        return []
    schema_fields = frames[0].get("schema", {}).get("fields", [])
    values = frames[0].get("data", {}).get("values", [])
    for index, item in enumerate(schema_fields):
        if item.get("name") == field and index < len(values):
            return list(values[index])
    return []


class GrafanaLogSource:
    """经 Grafana 代理查询 SLS。"""

    def __init__(self, client: GrafanaClient, context: AlertContext, cfg) -> None:
        self._client = client
        self._context = context
        self._cfg = cfg

    async def _query(self, sql: str, start_ts: int, end_ts: int) -> dict:
        """执行一条 SLS SQL 并返回原始 payload。"""
        if self._context.datasource_uid is None:
            raise SourceFailedError("datasource_uid_missing")
        body = {
            "from": str(start_ts * 1000),
            "to": str(end_ts * 1000),
            "queries": [{
                "refId": "A",
                "datasource": {
                    "type": "aliyun-log-service-datasource",
                    "uid": self._context.datasource_uid,
                },
                "logstore": self._context.logstore or "",
                "query": sql,
                "type": "all",
                "xcol": "t",
                "ycol": "",
            }],
        }
        try:
            return await self._client.post_json(_QUERY_PATH, body)
        except GrafanaError as exc:
            raise SourceFailedError(f"grafana_error:{exc.status}") from exc

    async def latency_stats(self, uri: str, start_ts: int, end_ts: int, slow_ms: int = 2000) -> dict:
        """按节点返回计数、p95 与慢请求数，并给出总量。"""
        sql = (
            "* | select upstream_addr, count(*) as cnt, "
            "approx_percentile(upstream_response_time, 0.95) * 1000 as p95, "
            f"count_if(upstream_response_time * 1000 >= {slow_ms}) as slow_cnt "
            f"where request_uri = '{uri}' group by upstream_addr"
        )
        payload = await self._query(sql, start_ts, end_ts)
        counts = parse_frame_column(payload, "A", "cnt")
        if not counts:
            raise SourceEmptyError("no_rows")
        p95_values = parse_frame_column(payload, "A", "p95")
        slow_values = parse_frame_column(payload, "A", "slow_cnt")
        addrs = parse_frame_column(payload, "A", "upstream_addr")
        return {
            "total": int(sum(counts)),
            "latency_ms": {"p95": float(max(p95_values)) if p95_values else 0.0},
            "slow": {"threshold": slow_ms, "count": int(sum(slow_values)) if slow_values else 0},
            "by_node": [{"addr": addr, "cnt": int(cnt)} for addr, cnt in zip(addrs, counts)],
        }

    async def request_detail(self, uri: str, start_ts: int, end_ts: int, limit: int = 100) -> list:
        """返回窗口内的请求明细（白名单字段）。"""
        sql = (
            "* | select __time__ as ts, upstream_addr, request_time * 1000 as rt_ms, "
            "upstream_response_time * 1000 as urt_ms, status, upstream_status "
            f"where request_uri = '{uri}' order by ts desc limit {limit}"
        )
        payload = await self._query(sql, start_ts, end_ts)
        timestamps = parse_frame_column(payload, "A", "ts")
        if not timestamps:
            raise SourceEmptyError("no_rows")
        addrs = parse_frame_column(payload, "A", "upstream_addr")
        rt_values = parse_frame_column(payload, "A", "rt_ms")
        statuses = parse_frame_column(payload, "A", "status")
        rows = []
        for index in range(len(timestamps)):
            rows.append({
                "ts": timestamps[index],
                "upstream_addr": addrs[index] if index < len(addrs) else "",
                "request_time_ms": rt_values[index] if index < len(rt_values) else 0,
                "status": statuses[index] if index < len(statuses) else 0,
            })
        return rows

    async def business_impact(self, start_ts: int, end_ts: int) -> dict:
        """对比核心接口当前窗口与昨日同时段的请求量。"""
        uris = self._cfg.business_uris
        if not uris:
            raise SourceEmptyError("no_business_uris")
        clauses = " or ".join(f"request_uri = '{uri}'" for uri in uris)
        sql = f"* | select request_uri, count(*) as cnt where {clauses} group by request_uri"
        current = await self._query(sql, start_ts, end_ts)
        baseline = await self._query(sql, start_ts - 86400, end_ts - 86400)
        current_counts = parse_frame_column(current, "A", "cnt")
        if not current_counts:
            raise SourceEmptyError("no_rows")
        endpoints = parse_frame_column(current, "A", "request_uri")
        base_counts = parse_frame_column(baseline, "A", "cnt")
        base_map = dict(zip(parse_frame_column(baseline, "A", "request_uri"), base_counts))
        rows = []
        for uri, count in zip(endpoints, current_counts):
            base = base_map.get(uri, 0)
            rows.append({"uri": uri, "pv": int(count), "base_pv": int(base)})
        return {"endpoints": rows}
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_grafana_source.py -v
Expected: 2 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/sources/grafana.py tests/rca/test_grafana_source.py
git commit -m "feat(rca): 经 Grafana 代理取数"
~~~

---

### Task 6: Skill 加载与匹配

**Files:**
- Create: src/rca/loader.py
- Create: skills/rca-latency/SKILL.md
- Create: skills/rca-latency/references/tools.md
- Test: tests/rca/test_loader.py

**Interfaces:**
- Produces: RcaSkill(name, match, body, evidence)、load_skills(root)、match_skill(skills, alertname)

- [ ] **Step 1: 写失败测试**

~~~python
# tests/rca/test_loader.py
from pathlib import Path
from src.rca.loader import load_skills, match_skill

SKILL = """---
name: rca-latency
match: ".*响应时间.*"
evidence:
  - id: stats
    tool: latency_stats
    required: true
    timeout: 8
    params:
      uri: "${alert.uri}"
---

判定正文
"""


def test_load_and_match(tmp_path: Path):
    path = tmp_path / "rca-latency"
    path.mkdir()
    (path / "SKILL.md").write_text(SKILL, encoding="utf-8")
    skills = load_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].evidence[0].tool == "latency_stats"
    assert match_skill(skills, "支付单createOrder响应时间上升") is not None
    assert match_skill(skills, "磁盘使用率") is None


def test_bad_skill_skipped(tmp_path: Path):
    path = tmp_path / "broken"
    path.mkdir()
    (path / "SKILL.md").write_text("---\nname: x\nmatch: '['\n---\n", encoding="utf-8")
    assert load_skills(tmp_path) == []
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_loader.py -v
Expected: ModuleNotFoundError: src.rca.loader

- [ ] **Step 3: 实现加载器与 skill 内容**

~~~python
# src/rca/loader.py
"""rca-* skill 的加载与匹配。

与 src/agents/skills/loader.py 并存但互不影响：rca skill 只被本模块读取，
且 SKILL.md 声明 user-invocable: false / disable-model-invocation: true，
不会出现在普通问答的可用技能列表里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.rca.models import EvidenceSpec


@dataclass(frozen=True)
class RcaSkill:
    """一份可执行的判定 skill。"""

    name: str  # skill 名（目录名）
    match: str  # alertname 正则
    body: str  # 判定正文（Markdown）
    evidence: tuple[EvidenceSpec, ...]  # 证据清单


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """拆分 YAML frontmatter 与正文。"""
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("unterminated frontmatter")
    meta = yaml.safe_load(parts[1]) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a mapping")
    return meta, parts[2].strip()


def _build_evidence(items: object) -> tuple[EvidenceSpec, ...]:
    """把 frontmatter 的 evidence 列表转成 EvidenceSpec。"""
    if not isinstance(items, list) or not items:
        raise ValueError("evidence must be a non-empty list")
    specs = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("evidence item must be a mapping")
        specs.append(EvidenceSpec(
            id=str(item["id"]),
            tool=str(item["tool"]),
            params={str(k): str(v) for k, v in (item.get("params") or {}).items()},
            required=bool(item.get("required", True)),
            timeout=float(item.get("timeout", 8)),
            when=item.get("when"),
        ))
    return tuple(specs)


def load_skills(root: Path) -> list[RcaSkill]:
    """扫描 root 下的 skill 目录；非法 skill 跳过。"""
    skills: list[RcaSkill] = []
    if not root.exists():
        return skills
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        skill_file = directory / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            meta, body = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            skills.append(RcaSkill(
                name=str(meta.get("name", directory.name)),
                match=str(meta["match"]),
                body=body,
                evidence=_build_evidence(meta.get("evidence")),
            ))
        except (ValueError, KeyError, yaml.YAMLError):
            continue
    return skills


def match_skill(skills: list[RcaSkill], alertname: str) -> RcaSkill | None:
    """按 alertname 正则匹配；命中返回第一个，否则 None。"""
    for skill in skills:
        if re.search(skill.match, alertname):
            return skill
    return None
~~~

~~~markdown
<!-- skills/rca-latency/SKILL.md -->
---
name: rca-latency
description: 响应时间类告警的三档判定
user-invocable: false
disable-model-invocation: true
match: ".*响应时间.*"
evidence:
  - id: stats
    tool: latency_stats
    required: true
    timeout: 8
    params:
      uri: "${alert.uri}"
  - id: detail
    tool: request_detail
    required: false
    timeout: 8
    when: "stats.total < 20"
    params:
      uri: "${alert.uri}"
  - id: impact
    tool: business_impact
    required: false
    timeout: 8
---

## 判定顺序

1. 先看样本量：0 条判"日志断流或服务不可用"；1–19 条逐条比对明细；≥20 条才谈趋势。
2. 看节点分布：单节点显著高于其他节点 → 单容器问题；全节点一致 → 共享依赖或流量。
3. 看上游耗时占比：接近总耗时 → 瓶颈在后端；远小于总耗时 → 网关排队。
4. 查近 7 天同告警是否反复出现且自愈。

## 禁止

- 不得以"未找到原因"推断无需处理。
- 不得引用"语义未知"的数值。
- 不得在文本中复述证据里的数字（数字由卡片渲染层填充）。
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_loader.py -v
Expected: 2 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/loader.py skills/rca-latency tests/rca/test_loader.py
git commit -m "feat(rca): skill 加载与匹配"
~~~


---

### Task 7: 证据采集器

**Files:**
- Create: src/rca/collector.py
- Test: tests/rca/test_collector.py

**Interfaces:**
- Consumes: EvidenceSpec、LogSource、RcaConfig
- Produces: collect_evidence(specs, source, alert_fields, cfg)

- [ ] **Step 1: 写失败测试**

~~~python
# tests/rca/test_collector.py
import asyncio
import pytest
from src.config.rca import RcaConfig
from src.rca.collector import collect_evidence
from src.rca.models import EvidenceSpec, EvidenceStatus


def _cfg() -> RcaConfig:
    return RcaConfig(
        enabled=True, alert_whitelist=(), grafana_base_url="", grafana_token="",
        collect_budget_seconds=2.0, source_timeout_seconds=1.0, rule_timeout_seconds=1.0,
        history_timeout_seconds=1.0, concurrency=4, severe_latency_ms=3000,
        cache_ttl_seconds=600.0, business_uris=(),
    )


class SlowSource:
    async def latency_stats(self, uri, start_ts, end_ts, slow_ms=2000):
        await asyncio.sleep(5)
        return {}

    async def request_detail(self, uri, start_ts, end_ts, limit=100):
        return []

    async def business_impact(self, start_ts, end_ts):
        return {}


class FastSource:
    async def latency_stats(self, uri, start_ts, end_ts, slow_ms=2000):
        return {"total": 2}

    async def request_detail(self, uri, start_ts, end_ts, limit=100):
        return [{"ts": 1}]

    async def business_impact(self, start_ts, end_ts):
        return {}


@pytest.mark.asyncio
async def test_timeout_marks_failed():
    specs = [EvidenceSpec(id="stats", tool="latency_stats", params={"uri": "${alert.uri}"}, timeout=0.2)]
    out = await collect_evidence(specs, SlowSource(), {"uri": "/x"}, _cfg())
    assert out["stats"].status is EvidenceStatus.FAILED
    assert out["stats"].reason == "timeout"


@pytest.mark.asyncio
async def test_when_skips_second_wave():
    specs = [
        EvidenceSpec(id="stats", tool="latency_stats", params={"uri": "${alert.uri}"}),
        EvidenceSpec(id="detail", tool="request_detail", params={"uri": "${alert.uri}"},
                     required=False, when="stats.total >= 100"),
    ]
    out = await collect_evidence(specs, FastSource(), {"uri": "/x"}, _cfg())
    assert out["stats"].status is EvidenceStatus.OK
    assert "detail" not in out
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_collector.py -v
Expected: ModuleNotFoundError: src.rca.collector

- [ ] **Step 3: 实现采集器**

~~~python
# src/rca/collector.py
"""证据采集：两波并行、单源超时、总预算、三态归一。

第一波跑无 when 的条目；第二波跑 when 成立（依赖前一波结果）的条目。
任何异常归一为 failed，不向上抛。
"""

from __future__ import annotations

import asyncio
import re
import time

from loguru import logger

from src.core.logging import encode_value
from src.rca.models import EvidenceResult, EvidenceSpec, EvidenceStatus
from src.rca.sources.base import SourceEmptyError, SourceFailedError

_PLACEHOLDER = re.compile(r"\$\{alert\.([a-zA-Z0-9_]+)\}")
_WHEN = re.compile(r"^([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+)?)$")


def _resolve_params(params: dict[str, str], fields: dict) -> dict:
    """替换 alert 占位符；缺字段抛 KeyError（严格模式）。"""
    resolved = {}
    for key, raw in params.items():
        def _sub(match: re.Match) -> str:
            name = match.group(1)
            if name not in fields or fields[name] is None:
                raise KeyError(name)
            return str(fields[name])

        resolved[key] = _PLACEHOLDER.sub(_sub, raw)
    return resolved


def _eval_when(expr: str | None, results: dict[str, EvidenceResult]) -> bool:
    """求值 when 简易表达式；无法解析时视为不成立。"""
    if expr is None:
        return True
    match = _WHEN.match(expr.strip())
    if match is None:
        return False
    source_id, field, op, raw_value = match.groups()
    result = results.get(source_id)
    if result is None or not isinstance(result.data, dict):
        return False
    left = result.data.get(field)
    if not isinstance(left, (int, float)):
        return False
    right = float(raw_value)
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return left == right


def _elapsed_ms(started: float) -> int:
    """把起算点换算成已耗时毫秒。"""
    return int((time.monotonic() - started) * 1000)


async def _run_one(
    spec: EvidenceSpec, source, fields: dict, semaphore: asyncio.Semaphore
) -> EvidenceResult:
    """执行单条证据并归一三态。"""
    started = time.monotonic()
    try:
        params = _resolve_params(spec.params, fields)
    except KeyError as exc:
        return EvidenceResult(spec.id, EvidenceStatus.FAILED, None, 0, f"missing_field:{exc}")

    method = getattr(source, spec.tool, None)
    if method is None:
        return EvidenceResult(spec.id, EvidenceStatus.FAILED, None, 0, "unknown_tool")

    async with semaphore:
        try:
            data = await asyncio.wait_for(method(**params), timeout=spec.timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return EvidenceResult(spec.id, EvidenceStatus.FAILED, None, _elapsed_ms(started), "timeout")
        except SourceEmptyError as exc:
            return EvidenceResult(spec.id, EvidenceStatus.EMPTY, None, _elapsed_ms(started), f"empty:{exc}")
        except SourceFailedError as exc:
            return EvidenceResult(spec.id, EvidenceStatus.FAILED, None, _elapsed_ms(started), str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[rca] evidence error id={} err={}", encode_value(spec.id), type(exc).__name__)
            return EvidenceResult(spec.id, EvidenceStatus.FAILED, None, _elapsed_ms(started), "internal_error")

    elapsed = _elapsed_ms(started)
    if data == {} or data == []:
        return EvidenceResult(spec.id, EvidenceStatus.EMPTY, None, elapsed, "no_data")
    return EvidenceResult(spec.id, EvidenceStatus.OK, data, elapsed)


def _is_required(specs, evidence_id: str) -> bool:
    """该证据是否标记为必需。"""
    for spec in specs:
        if spec.id == evidence_id:
            return spec.required
    return False


async def collect_evidence(
    specs: tuple[EvidenceSpec, ...] | list[EvidenceSpec],
    source,
    alert_fields: dict,
    cfg,
) -> dict[str, EvidenceResult]:
    """按两波策略采集全部证据。"""
    semaphore = asyncio.Semaphore(cfg.concurrency)
    results: dict[str, EvidenceResult] = {}

    first_wave = [spec for spec in specs if spec.when is None]
    if first_wave:
        gathered = await asyncio.wait_for(
            asyncio.gather(*(_run_one(spec, source, alert_fields, semaphore) for spec in first_wave)),
            timeout=cfg.collect_budget_seconds,
        )
        for result in gathered:
            results[result.id] = result
            if result.status is EvidenceStatus.FAILED and _is_required(specs, result.id):
                return results

    second_wave = [spec for spec in specs if spec.when is not None and _eval_when(spec.when, results)]
    if second_wave:
        gathered = await asyncio.wait_for(
            asyncio.gather(*(_run_one(spec, source, alert_fields, semaphore) for spec in second_wave)),
            timeout=cfg.collect_budget_seconds,
        )
        for result in gathered:
            results[result.id] = result
    return results
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_collector.py -v
Expected: 2 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/collector.py tests/rca/test_collector.py
git commit -m "feat(rca): 证据采集两波并行与三态"
~~~

---

### Task 8: 判定（样本量 + 四条件）

**Files:**
- Create: src/rca/judge.py
- Test: tests/rca/test_judge.py

**Interfaces:**
- Produces: judge(results, severe_latency_ms) -> TriageVerdict

- [ ] **Step 1: 写失败测试**

~~~python
# tests/rca/test_judge.py
from src.rca.judge import judge
from src.rca.models import EvidenceResult, EvidenceStatus, Tier


def _ok(eid: str, data) -> EvidenceResult:
    return EvidenceResult(eid, EvidenceStatus.OK, data, 10)


def test_zero_sample_is_watch():
    results = {"stats": _ok("stats", {"total": 0, "slow": {"count": 0}, "by_node": []})}
    assert judge(results, 3000).tier is Tier.WATCH


def test_single_slow_request_is_no_action():
    results = {
        "stats": _ok("stats", {"total": 2, "slow": {"count": 1}, "latency_ms": {"p95": 1057.0}}),
        "detail": _ok("detail", [{"status": 200}, {"status": 200}]),
    }
    assert judge(results, 3000).tier is Tier.NO_ACTION


def test_all_requests_slow_is_watch():
    results = {
        "stats": _ok("stats", {"total": 2, "slow": {"count": 2}, "latency_ms": {"p95": 1800.0}}),
        "detail": _ok("detail", [{"status": 200}, {"status": 200}]),
    }
    assert judge(results, 3000).tier is Tier.WATCH


def test_5xx_is_need_action():
    results = {
        "stats": _ok("stats", {"total": 10, "slow": {"count": 0}, "by_node": []}),
        "detail": _ok("detail", [{"status": 502}]),
    }
    assert judge(results, 3000).tier is Tier.NEED_ACTION


def test_above_severe_is_watch():
    results = {
        "stats": _ok("stats", {"total": 2, "slow": {"count": 1}, "latency_ms": {"p95": 5000.0}}),
        "detail": _ok("detail", [{"status": 200}]),
    }
    assert judge(results, 3000).tier is Tier.WATCH
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_judge.py -v
Expected: ModuleNotFoundError: src.rca.judge

- [ ] **Step 3: 实现判定**

~~~python
# src/rca/judge.py
"""三档判定：档位由代码确定性计算，模型不参与。

无需处理必须同时满足：样本量 >= 1、无 5xx、慢请求 <= 1、最大耗时 < 严重阈值。
任何一条不满足都向上落档；证据不足落"建议观察"。
"""

from __future__ import annotations

from src.rca.models import EvidenceResult, EvidenceStatus, Tier, TriageVerdict


def _stats(results: dict[str, EvidenceResult]) -> dict | None:
    """取 stats 证据的数据；缺失或失败返回 None。"""
    result = results.get("stats")
    if result is None or result.status is not EvidenceStatus.OK:
        return None
    if not isinstance(result.data, dict):
        return None
    return result.data


def _rows(results: dict[str, EvidenceResult]) -> list[dict]:
    """取明细证据的行；缺失返回空列表。"""
    result = results.get("detail")
    if result is None or result.status is not EvidenceStatus.OK:
        return []
    if not isinstance(result.data, list):
        return []
    return [row for row in result.data if isinstance(row, dict)]


def _is_server_error(status: object) -> bool:
    """判断是否为 5xx。"""
    if isinstance(status, int):
        return status >= 500
    if isinstance(status, str):
        try:
            return int(status) >= 500
        except ValueError:
            return False
    return False


def judge(results: dict[str, EvidenceResult], severe_latency_ms: int) -> TriageVerdict:
    """按证据计算档位与定档依据。"""
    stats = _stats(results)
    if stats is None:
        return TriageVerdict(Tier.WATCH, "必需证据缺失", 0, False)

    total = int(stats.get("total", 0))
    if total == 0:
        return TriageVerdict(Tier.WATCH, "日志断流或服务不可用", 0, False)

    server_errors = [row for row in _rows(results) if _is_server_error(row.get("status"))]
    if server_errors:
        return TriageVerdict(Tier.NEED_ACTION, f"出现 {len(server_errors)} 条 5xx", total, False)

    slow = stats.get("slow") or {}
    slow_count = int(slow.get("count", 0))
    p95 = float((stats.get("latency_ms") or {}).get("p95", 0.0))
    severe_hit = p95 >= severe_latency_ms

    if severe_hit:
        return TriageVerdict(Tier.WATCH, "耗时超过严重阈值", total, True)
    if slow_count <= 1:
        return TriageVerdict(Tier.NO_ACTION, "孤立慢请求，未超严重阈值", total, False)
    return TriageVerdict(Tier.WATCH, f"{slow_count} 条慢请求", total, False)
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_judge.py -v
Expected: 5 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/judge.py tests/rca/test_judge.py
git commit -m "feat(rca): 三档判定与四条件"
~~~


---

### Task 9: 卡片渲染与脱敏

**Files:**
- Create: src/rca/render.py
- Test: tests/rca/test_render.py

**Interfaces:**
- Produces: render_card(verdict, results, explanation, max_chars) -> str

- [ ] **Step 1: 写失败测试**

~~~python
# tests/rca/test_render.py
from src.rca.models import EvidenceResult, EvidenceStatus, Tier, TriageVerdict
from src.rca.render import render_card


def test_card_contains_tier_and_missing():
    verdict = TriageVerdict(Tier.WATCH, "证据不足", 2, False)
    results = {
        "stats": EvidenceResult("stats", EvidenceStatus.OK, {"total": 2}, 10),
        "detail": EvidenceResult("detail", EvidenceStatus.FAILED, None, 10, "timeout"),
        "history": EvidenceResult("history", EvidenceStatus.EMPTY, None, 5, "no_data"),
    }
    text = render_card(verdict, results, {"summary": "疑似噪声"}, 1500)
    assert "建议观察" in text
    assert "未取到 detail" in text
    assert "无数据 history" in text


def test_ip_masked():
    verdict = TriageVerdict(Tier.NO_ACTION, "孤立慢请求", 2, False)
    results = {
        "stats": EvidenceResult(
            "stats", EvidenceStatus.OK,
            {"total": 2, "by_node": [{"addr": "172.18.0.123:8083", "cnt": 2}]}, 10,
        )
    }
    text = render_card(verdict, results, {}, 1500)
    assert "172.18.0.123" not in text
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_render.py -v
Expected: ModuleNotFoundError: src.rca.render

- [ ] **Step 3: 实现渲染**

~~~python
# src/rca/render.py
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
~~~

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/rca/test_render.py -v
Expected: 2 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/render.py tests/rca/test_render.py
git commit -m "feat(rca): 卡片渲染与脱敏"
~~~

---

### Task 10: 编排与接入报警轮

**Files:**
- Create: src/rca/pipeline.py
- Modify: src/services/alert_service.py（_run_analysis 内，start_turn 之前）
- Test: tests/services/test_alert_triage_gate.py

**Interfaces:**
- Produces: should_triage(alertname, cfg) -> bool；run_triage(event, base_query, cfg, skills_root) -> tuple[str, TriageVerdict | None]

- [ ] **Step 1: 写失败测试**

~~~python
# tests/services/test_alert_triage_gate.py
from src.config.rca import RcaConfig
from src.rca.pipeline import should_triage


def _cfg(enabled: bool, whitelist: tuple[str, ...]) -> RcaConfig:
    return RcaConfig(
        enabled=enabled, alert_whitelist=whitelist, grafana_base_url="", grafana_token="",
        collect_budget_seconds=25.0, source_timeout_seconds=8.0, rule_timeout_seconds=5.0,
        history_timeout_seconds=5.0, concurrency=4, severe_latency_ms=3000,
        cache_ttl_seconds=600.0, business_uris=(),
    )


def test_disabled_returns_false():
    assert should_triage("任意告警", _cfg(False, ("任意告警",))) is False


def test_not_in_whitelist_returns_false():
    assert should_triage("其他告警", _cfg(True, ("支付单createOrder响应时间上升",))) is False


def test_whitelisted_returns_true():
    assert should_triage("支付单createOrder响应时间上升", _cfg(True, ("支付单createOrder响应时间上升",))) is True
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/services/test_alert_triage_gate.py -v
Expected: ModuleNotFoundError: src.rca.pipeline

- [ ] **Step 3: 实现编排并接入**

~~~python
# src/rca/pipeline.py
"""报警轮的判定编排：开关 → 上下文 → 采集 → 判定 → 渲染。

任何环节失败都不抛给调用方：返回原 query + None，由调用方走原行为与兜底。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from src.core.logging import encode_value
from src.infra.grafana_client import GrafanaClient
from src.rca.collector import collect_evidence
from src.rca.context import build_alert_context
from src.rca.judge import judge
from src.rca.loader import load_skills, match_skill
from src.rca.models import TriageVerdict
from src.rca.render import render_card
from src.rca.sources.grafana import GrafanaLogSource

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def should_triage(alertname: str, cfg) -> bool:
    """开关与白名单判断。"""
    if not cfg.enabled:
        return False
    return alertname in cfg.alert_whitelist


def _folder(event) -> str:
    """取告警所属 folder；载荷缺失时返回空串。"""
    folder = getattr(event, "folder", "")
    if isinstance(folder, str):
        return folder
    return ""


async def run_triage(
    event, base_query: str, cfg, skills_root: Path = SKILLS_ROOT
) -> tuple[str, TriageVerdict | None]:
    """执行判定并返回增强后的 query 与判定结果；失败时原样返回。"""
    if not should_triage(event.alertname, cfg):
        return base_query, None

    client = GrafanaClient(cfg.grafana_base_url, cfg.grafana_token, cfg.source_timeout_seconds)
    try:
        context = await build_alert_context(client, event.alertname, _folder(event), cfg)
        if context.uri is None:
            logger.info("[rca] context unresolved alert={}", encode_value(event.alertname))
            return base_query, None

        skill = match_skill(load_skills(skills_root), event.alertname)
        if skill is None:
            logger.info("[rca] no skill alert={}", encode_value(event.alertname))
            return base_query, None

        source = GrafanaLogSource(client, context, cfg)
        fields = {"uri": context.uri, "alertname": context.alertname}
        results = await collect_evidence(skill.evidence, source, fields, cfg)
        verdict = judge(results, cfg.severe_latency_ms)
        card = render_card(verdict, results, {}, 1500)
        query = base_query + "\n\n" + skill.body + "\n\n[证据]\n" + card
        return query, verdict
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[rca] triage failed alert={} err={}",
            encode_value(event.alertname),
            type(exc).__name__,
        )
        return base_query, None
~~~

在 src/services/alert_service.py 的 _run_analysis 中，把 start_turn 调用改为：

~~~python
query_text = build_query(event)
triage_query, verdict = await run_triage(event, query_text, load_rca_config())
handle = await turn_runner.start_turn(
    svc,
    session_id=session_id,
    kb_id="",
    query=triage_query,
    user_id=ALERT_USER_ID,
    title=f"[企微·{target.bot_key}] 报警 {event.alertname}",
    clarify=False,
)
~~~

并在文件头部加 import：from src.config.rca import load_rca_config；from src.rca.pipeline import run_triage

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/services/test_alert_triage_gate.py tests/services/test_alert_parse.py -v
Expected: 全部 passed（原告警解析用例不受影响）

- [ ] **Step 5: 提交**

~~~bash
git add src/rca/pipeline.py src/services/alert_service.py tests/services/test_alert_triage_gate.py
git commit -m "feat(rca): 接入报警轮（开关 + 白名单）"
~~~

---

### Task 10B: 结论落库与推送同源

**Files:**
- Modify: src/services/alert_service.py
- Test: tests/services/test_alert_persist_same.py

- [ ] **Step 1: 定位现有落库点**

Run: grep -n "save_assistant\|process_json\|purified" src/services/turn_runner.py src/services/alert_service.py
Expected: 找到分析完成后写入助手消息的那处调用

- [ ] **Step 2: 写失败测试**

~~~python
# tests/services/test_alert_persist_same.py
from src.rca.models import Tier, TriageVerdict
from src.services.alert_service import choose_final_text


def test_card_wins_when_verdict_present():
    verdict = TriageVerdict(Tier.NO_ACTION, "孤立慢请求", 2, False)
    assert choose_final_text("模型的自由文本", "卡片文本", verdict) == "卡片文本"


def test_falls_back_without_verdict():
    assert choose_final_text("模型的自由文本", "卡片文本", None) == "模型的自由文本"
~~~

- [ ] **Step 3: 实现同源选择**

在 alert_service.py 增加并接入：

~~~python
def choose_final_text(model_text: str, card_text: str, verdict) -> str:
    """有判定结果时用卡片文本，否则用原有净化正文。"""
    if verdict is None:
        return model_text
    return card_text
~~~

在 _run_analysis 收尾处，推送与落库都统一用 choose_final_text 的返回值，保证两者同源。

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/services/test_alert_persist_same.py -v
Expected: 2 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/services/alert_service.py tests/services/test_alert_persist_same.py
git commit -m "feat(alert): 推送与落库同源"
~~~

---


### Task 11: 占位后异常必须兜底

**Files:**
- Modify: src/services/alert_service.py（_run_analysis 的 try 段）
- Test: tests/services/test_alert_fallback_guard.py

**Interfaces:**
- Consumes: 现有 _safe_send / render_fallback

- [ ] **Step 1: 写失败测试**

~~~python
# tests/services/test_alert_fallback_guard.py
import pytest
from src.config.alert import AlertConfig, AlertTarget
from src.services import alert_service


@pytest.mark.asyncio
async def test_triage_error_triggers_fallback(monkeypatch):
    sent = []

    async def fake_send(target, content):
        sent.append(content)

    async def boom(*args, **kwargs):
        raise RuntimeError("triage exploded")

    monkeypatch.setattr(alert_service, "_safe_send", fake_send)
    monkeypatch.setattr(alert_service, "_get_service", boom)

    cfg = AlertConfig(
        token="t", target=AlertTarget(bot_key="bot", chatid="chat"),
        dedup_window_seconds=300.0, dedup_capacity=10,
        turn_timeout_seconds=5.0, fallback_max_chars=100,
    )
    event = alert_service.AlertEvent(
        alarm_id="a1", alertname="测试告警", level="P1",
        details="详情", fingerprints=("fp1",), status="firing",
    )
    await alert_service._run_analysis(cfg, event, target=cfg.target)
    assert len(sent) == 2
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: POSTGRES_HOST=localhost pytest tests/services/test_alert_fallback_guard.py -v
Expected: 断言失败（当前只发占位，或异常直接抛出）

- [ ] **Step 3: 把占位之后整段包进 try**

在 _run_analysis 中，await _safe_send(target, render_placeholder(event)) 之后：

~~~python
try:
    # 原有的 session / start_turn / consume_events 逻辑整体缩进到这里
    ...
except Exception as exc:  # noqa: BLE001
    logger.exception(
        "[alert] analysis crashed alarm_id={} err={}",
        encode_value(event.alarm_id),
        type(exc).__name__,
    )
    await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
~~~

同时在 run_triage 返回 verdict 为 None 时，不向 process_json 写判定字段（保持原有落库形态）。

- [ ] **Step 4: 跑测试确认通过**

Run: POSTGRES_HOST=localhost pytest tests/services/test_alert_fallback_guard.py -v
Expected: 1 passed

- [ ] **Step 5: 提交**

~~~bash
git add src/services/alert_service.py tests/services/test_alert_fallback_guard.py
git commit -m "fix(alert): 占位后异常必须兜底转发"
~~~

---

### Task 12: 文档登记与全量校验

**Files:**
- Modify: docs/agents/glossary.md
- Modify: docs/agents/defensive-patterns.md
- Modify: docs/agents/code-map.md

- [ ] **Step 1: 登记术语**

在 glossary.md 增加五行：三档判定、证据三态、证据包、严重阈值、语义未知。

- [ ] **Step 2: 登记防御性模式**

在 defensive-patterns.md 增加三条：

~~~markdown
### 低流量接口不得用分钟级分位数做告警口径
每分钟个位数请求时，分钟级 p95 等于最大值、avg 等于单条耗时。告警应改用计数或放大窗口。

### 不得把"未找到原因"等同于"无异常"
证据不足必须落到"建议观察"，禁止推断"无需处理"。

### Grafana ds/query 的 status 不可信
实测成功查询也返回 status: 500，必须按 frames[].data.values 是否非空判定。
~~~

- [ ] **Step 3: 更新代码地图**

在 code-map.md 的模块表增加 src/rca/（告警三档判定）与 src/infra/grafana_client.py。

- [ ] **Step 4: 全量校验**

~~~bash
POSTGRES_HOST=localhost pytest tests/ -v
ruff check .
~~~

Expected: 全部通过，无 lint 错误

- [ ] **Step 5: 提交**

~~~bash
git add docs/agents/glossary.md docs/agents/defensive-patterns.md docs/agents/code-map.md
git commit -m "docs: 登记告警三档判定相关术语与防御模式"
~~~
