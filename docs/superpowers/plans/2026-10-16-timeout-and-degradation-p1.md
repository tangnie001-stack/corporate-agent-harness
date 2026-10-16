# timeout-and-degradation-hardening · P1（底座）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让生产链路三类模型（Chat / Embedding / Rerank）的客户端具备**显式超时**、**关闭 SDK 静默重试**、**本层有界重试（异常分类 + 抖动）**，并补上主链路 LLM「**首个 chunk 之前**」的重试能力 —— **不改变任何降级行为**。

**Architecture:** 三处分工（design D1）：① 超时/重试**入参**收敛到 provider 层的 `build_*`（唯一产地）；② 重试的**判定与退避**抽成无状态原语 `src/infra/retry_policy.py`（不持配置、不做降级）；③ 主链路 LLM 的重试以**模型对象子类**实现（在 `_astream` 内守卫"已产出即不重试"），定义在 provider 层**之外**。

**Tech Stack:** Python 3.12 / `langchain-openai` / `httpx` / `dashscope` SDK / `pytest`

**Spec（执行前必读，本计划从它推导）:** `docs/openspec/changes/timeout-and-degradation-hardening/`
- `proposal.md`（Why / What / 成功度量）
- `design.md` **D1–D17**（尤其 D1 不抽新层、D2 键名归属、D5 异常分类与适用范围、D11 取值表、D13 rerank 嵌套、D16 挂点）
- `specs/timeout-policy/spec.md`（5 条 SHALL）、`specs/provider-compat/spec.md`（ADDED + MODIFIED 配置覆盖规则）
- `tasks.md` **§0 前置 / §1 本阶段范围与验收（1.1–1.10）**

## Global Constraints

以下为**每个任务都隐含包含**的约束，逐字取自 spec / design：

- 超时与重试取值 SHALL **按角色**（`main` / `classify` / `summary` / `embedding` / `rerank`）住 `src/config/settings.py`，SHALL NOT 写入 `registry.yaml`（D2）。
- 生产侧 SHALL 使用**自有键名**，SHALL NOT 复用评测链的 `RAGAS_*` 键（D2「键名归属」，第三轮 I6）。
- SDK 自动重试 SHALL 关闭（`max_retries=0`），且 SHALL **成对**提供本层有界重试（spec「不得只关不加」）。
- **异步路径必须设 `http_async_client`** —— 主链路是 async 流式，只设 `http_client` 对本链路**不生效**（已实测 `ChatOpenAI` 同时有 `http_client` 与 `http_async_client`）。
- 一旦流已开始向用户输出，SHALL NOT 重试（spec「流式约束」）。
- `judge` 角色**显式排除**：`build_chat("judge", ...)` 不注入本阶段默认值（其超时真源归评测链，D17）。
- 本阶段**不改变任何降级行为**（tasks 1.9）。
- 验收范围：worktree 内只跑**非 DB 单测 + `ruff` + `pyright`**；`tests/parsers` 的环境性失败（worktree 缺 gitignored `data/`）**不是回归**；DB 用例在本机报 `gaierror`。
- worktree 的 `.venv` 是指向主工作区的**软链**（否则 pre-commit 报 `Executable .venv/bin/python not found`）。
- 提交信息用中文，沿用本仓前缀风格（`feat:` / `fix:` / `refactor:`）。

## File Structure（本阶段全部改动）

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/infra/retry_policy.py` | **新建** | 无状态重试原语：`is_retryable` / `call_with_retry` / `acall_with_retry`（不持配置、不做降级） |
| `src/infra/llm/retrying_chat.py` | **新建** | `RetryingReasoningChat`：`ReasoningPreservingChat` 子类，只在**首个 chunk 前**重试 |
| `src/models.py` | 删除 `with_retry` | 门面只剩 `get_*`（重试原语不再住模型门面） |
| `src/rag/retrieval.py` | 改 1 处调用 | rerank 调用改走 `retry_policy.call_with_retry`（唯一调用点） |
| `src/config/settings.py` | 新增角色级超时键 | 生产链路自有键名（不复用 `RAGAS_*`） |
| `src/infra/llm/provider/factory.py` | 三个 `build_*` | 显式超时（同步 + 异步两个 client）+ `max_retries=0`（`judge` 除外） |
| `src/infra/db/vector_store/pg_store.py` | `QueryEmbedder` | 查询路用更紧的 read 超时（D10 的唯一例外） |
| `src/agents/tools/rag_tools.py` | rerank 外层预算 | 外层 `wait_for` 由 `RERANK_TIMEOUT`(5s) → 新键 `RERANK_OUTER_TIMEOUT`(6s) |
| `src/core/log_events.py` + `src/core/log_event_specs.py` | 新事件两处登记 | `model retry`（重试留痕） |
| `tests/infra/test_retry_policy.py` | **新建** | 原语单测 |
| `tests/infra/llm/test_retrying_chat.py` | **新建** | 包装层单测（流式守卫） |
| `tests/infra/llm/provider/test_factory_timeouts.py` | **新建** | 构造即显式 + 关 SDK 重试 + judge 排除 |
| `tests/rag/test_retrieval.py` | 追加 | rerank 调用改走原语后行为不变 |

---

### Task 1: 重试原语 `retry_policy`（D1/D5）

**Files:**
- Create: `src/infra/retry_policy.py`
- Modify: `src/models.py`（删除 `with_retry` 及其 import）、`src/rag/retrieval.py:27`（import）、`src/rag/retrieval.py:171-176`（调用点）
- Test: `tests/infra/test_retry_policy.py`

**Interfaces:**
- Consumes: 无（本阶段第一个任务）
- Produces:
  - `is_retryable(exc: BaseException) -> bool`
  - `call_with_retry(fn, *args, attempts: int, initial_interval: float, backoff: float, jitter: float = 0.3, on_retry: Callable[[int, BaseException], None] | None = None, **kwargs) -> T`
  - `async acall_with_retry(fn, *args, attempts: int, initial_interval: float, backoff: float, jitter: float = 0.3, on_retry: Callable[[int, BaseException], None] | None = None, **kwargs) -> T`
  - `sleep_seconds(attempt: int, initial_interval: float, backoff: float, jitter: float) -> float`（供测试直接断言抖动范围）

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/test_retry_policy.py`：

```python
"""retry_policy 原语单测：异常分类 + 有界 + 抖动 + 异步版。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.infra import retry_policy


class _Fake429(Exception):
    """模拟 openai.RateLimitError 的形态（本测试不依赖 openai 包）。"""


def test_value_error_not_retryable():
    assert retry_policy.is_retryable(ValueError("检索上下文为空")) is False


def test_timeout_retryable():
    assert retry_policy.is_retryable(TimeoutError()) is True
    assert retry_policy.is_retryable(httpx.ReadTimeout("read timeout")) is True


def test_connection_retryable():
    assert retry_policy.is_retryable(ConnectionError("reset")) is True


def test_unknown_exception_not_retryable():
    """未列出的异常默认不重试（保守：不拿未知错误去放大调用）。"""

    class Weird(Exception):
        pass

    assert retry_policy.is_retryable(Weird("?")) is False


def test_call_with_retry_succeeds_after_transient_failure():
    calls: list[int] = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("boom")
        return "ok"

    result = retry_policy.call_with_retry(
        flaky, attempts=3, initial_interval=0.0, backoff=2.0, jitter=0.0
    )
    assert result == "ok"
    assert len(calls) == 3


def test_call_with_retry_stops_on_non_retryable():
    calls: list[int] = []

    def bad_input():
        calls.append(1)
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        retry_policy.call_with_retry(
            bad_input, attempts=3, initial_interval=0.0, backoff=2.0, jitter=0.0
        )
    assert len(calls) == 1, "输入校验类必须只尝试一次"


def test_call_with_retry_bounded_by_attempts():
    calls: list[int] = []

    def always_timeout():
        calls.append(1)
        raise TimeoutError("always")

    with pytest.raises(TimeoutError):
        retry_policy.call_with_retry(
            always_timeout, attempts=3, initial_interval=0.0, backoff=2.0, jitter=0.0
        )
    assert len(calls) == 3


def test_on_retry_callback_receives_attempt_and_exception():
    seen: list[tuple[int, str]] = []

    def flaky():
        if len(seen) < 2:
            raise TimeoutError("x")
        return "ok"

    retry_policy.call_with_retry(
        flaky,
        attempts=3,
        initial_interval=0.0,
        backoff=2.0,
        jitter=0.0,
        on_retry=lambda attempt, exc: seen.append((attempt, type(exc).__name__)),
    )
    assert seen == [(1, "TimeoutError"), (2, "TimeoutError")]


def test_sleep_seconds_has_jitter_within_range():
    base = retry_policy.sleep_seconds(2, 1.0, 2.0, jitter=0.0)
    assert base == 2.0
    lo = retry_policy.sleep_seconds(2, 1.0, 2.0, jitter=0.5)
    assert 1.0 <= lo <= 3.0, "±50% 抖动应落在 [base*(1-0.5), base*(1+0.5)]"


def test_acall_with_retry_uses_async_sleep():
    calls: list[int] = []

    async def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise TimeoutError("boom")
        return "ok"

    result = asyncio.run(
        retry_policy.acall_with_retry(
            flaky, attempts=3, initial_interval=0.0, backoff=2.0, jitter=0.0
        )
    )
    assert result == "ok"
    assert len(calls) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/code/corporate_rag-timeout-degradation && .venv/bin/python -m pytest tests/infra/test_retry_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.infra.retry_policy'`

- [ ] **Step 3: 写最小实现**

新建 `src/infra/retry_policy.py`：

```python
"""重试策略原语 —— 异常分类 + 有界退避 + 抖动（无状态、不持配置）。

归属：change `timeout-and-degradation-hardening` 的 design D1 ② / D5。
本模块只回答"**该不该重试**、**等多久**"：
- **不持配置**（阈值由调用方从 `settings` 传入，故可被 env 覆盖）；
- **不做降级动作**（那是各调用点的事，见 design D1 ③）；
- **不落 provider 层**（ADR-0021：该层不做 fallback / 重试策略）。

分类判据（D5，按"重试是否会改变结果"）：
- **不可重试**：输入校验类（`ValueError` / `TypeError`，特征为失败耗时 ≈ 0）、
  鉴权 / 权限类、请求格式类（400/422）；
- **可重试**：超时、连接错误、429、5xx；
- **未列出的异常默认不重试** —— 保守选择：不拿未知错误去放大调用次数。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from functools import lru_cache
from typing import TypeVar

import httpx
from loguru import logger

T = TypeVar("T")

# ── 不可重试：显式列出（便于阅读与测试；未知异常默认也不重试） ──
_NON_RETRYABLE: tuple[type[BaseException], ...] = (
    ValueError,  # 含输入校验类（如"检索上下文为空"）
    TypeError,
    PermissionError,
    NotImplementedError,
)

# ── 可重试：与 provider 无关的传输类 ──
_RETRYABLE_BASE: tuple[type[BaseException], ...] = (
    TimeoutError,  # 含 asyncio.TimeoutError
    ConnectionError,
    httpx.TimeoutException,  # 含 ConnectTimeout / ReadTimeout
    httpx.TransportError,  # 含 ConnectError / ReadError
)

# openai SDK 的异常类名（该包可能未安装或版本不同 ⇒ 按名取、缺则跳过）
_OPENAI_RETRYABLE = (
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
)
_OPENAI_NON_RETRYABLE = (
    "AuthenticationError",
    "PermissionDeniedError",
    "BadRequestError",
    "UnprocessableEntityError",
)


@lru_cache(maxsize=1)
def _tables() -> tuple[tuple[type[BaseException], ...], tuple[type[BaseException], ...]]:
    """合并基础表与 openai 异常类（进程内缓存一次）。"""
    retryable: list[type[BaseException]] = list(_RETRYABLE_BASE)
    non_retryable: list[type[BaseException]] = list(_NON_RETRYABLE)
    try:
        import openai
    except Exception:  # noqa: BLE001 - 缺依赖时只用基础表
        return tuple(retryable), tuple(non_retryable)
    for name in _OPENAI_RETRYABLE:
        cls = getattr(openai, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            retryable.append(cls)
    for name in _OPENAI_NON_RETRYABLE:
        cls = getattr(openai, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            non_retryable.append(cls)
    return tuple(retryable), tuple(non_retryable)


def is_retryable(exc: BaseException) -> bool:
    """该异常重试是否可能改变结果（判据见模块 docstring）。"""
    retryable, non_retryable = _tables()
    if isinstance(exc, tuple(non_retryable)):
        return False
    return isinstance(exc, tuple(retryable))


def sleep_seconds(attempt: int, initial_interval: float, backoff: float, jitter: float) -> float:
    """第 `attempt` 次重试前的等待秒数（attempt 从 1 计）。

    基础值 = `initial_interval * backoff ** (attempt - 1)`，再叠加 ±`jitter` 比例的
    随机抖动（防多请求同时重试的惊群）。`jitter=0` 时为确定性值（便于测试）。
    """
    base = initial_interval * (backoff ** (attempt - 1))
    if jitter <= 0:
        return max(0.0, base)
    return max(0.0, base * (1 + random.uniform(-jitter, jitter)))


def _log_retry(name: str, attempt: int, attempts: int, exc: BaseException, wait: float) -> None:
    logger.warning(
        "[retry] {} failed attempt={}/{} err={} wait={:.2f}s",
        name,
        attempt,
        attempts,
        type(exc).__name__,
        wait,
    )


def call_with_retry(
    fn: Callable[..., T],
    *args: object,
    attempts: int,
    initial_interval: float,
    backoff: float,
    jitter: float = 0.3,
    on_retry: Callable[[int, BaseException], None] | None = None,
    **kwargs: object,
) -> T:
    """同步调用 `fn`，失败按 `is_retryable` 有界重试。"""
    name = getattr(fn, "__name__", repr(fn))
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - 由 is_retryable 决定是否重试
            last = exc
            if attempt >= attempts or not is_retryable(exc):
                raise
            wait = sleep_seconds(attempt, initial_interval, backoff, jitter)
            if on_retry is not None:
                on_retry(attempt, exc)
            _log_retry(name, attempt, attempts, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"{name} failed after {attempts} attempts")  # pragma: no cover


async def acall_with_retry(
    fn: Callable[..., object],
    *args: object,
    attempts: int,
    initial_interval: float,
    backoff: float,
    jitter: float = 0.3,
    on_retry: Callable[[int, BaseException], None] | None = None,
    **kwargs: object,
) -> object:
    """异步版：等待用 `await asyncio.sleep`，**不阻塞事件循环**。"""
    name = getattr(fn, "__name__", repr(fn))
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            last = exc
            if attempt >= attempts or not is_retryable(exc):
                raise
            wait = sleep_seconds(attempt, initial_interval, backoff, jitter)
            if on_retry is not None:
                on_retry(attempt, exc)
            _log_retry(name, attempt, attempts, exc, wait)
            await asyncio.sleep(wait)
    raise RuntimeError(f"{name} failed after {attempts} attempts")  # pragma: no cover
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/test_retry_policy.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 迁移唯一调用点并删旧实现**

改 `src/rag/retrieval.py`：
- 删 `from src.models import with_retry`（第 27 行）
- 加 `from src.infra import retry_policy`
- 把 171-176 行的调用改为：

```python
        reranked = retry_policy.call_with_retry(
            reranker.rerank,
            docs,
            query,
            attempts=RETRY_MAX_ATTEMPTS,
            initial_interval=RETRY_INITIAL_INTERVAL,
            backoff=RETRY_BACKOFF_FACTOR,
        )
```

改 `src/models.py`：删掉 `with_retry` 整个函数（含 `functools` / `time` / `RETRY_*` 的 import，若不再使用）、删模块 docstring 里提到 `with_retry` 的那一行。
**不保留别名转发**（design D1：留别名会制造两个入口）。

- [ ] **Step 6: 跑受影响测试确认行为不变**

Run: `.venv/bin/python -m pytest tests/infra/test_retry_policy.py tests/rag/test_retrieval.py tests/rag/test_retrieval_dedup.py -q`
Expected: PASS（行为不变；rerank 失败仍走原有降级分支）

- [ ] **Step 7: 提交**

```bash
git add src/infra/retry_policy.py src/models.py src/rag/retrieval.py tests/infra/test_retry_policy.py
git commit -m "refactor(retry): 抽无状态重试原语 retry_policy，迁走 models.with_retry 的唯一调用点"
```

---

### Task 2: 角色级超时键 + 三个 `build_*` 显式超时与关 SDK 重试（D2/D11/D1①）

**Files:**
- Modify: `src/config/settings.py`（新增「生产链路角色级超时」段）
- Modify: `src/infra/llm/provider/factory.py`（`build_chat` / `build_embeddings` / `build_rerank`）
- Modify: `src/infra/db/vector_store/pg_store.py`（`QueryEmbedder` 用查询档 read）
- Test: `tests/infra/llm/provider/test_factory_timeouts.py`

**Interfaces:**
- Consumes: 无（不依赖 Task 1）
- Produces:
  - `settings.HTTP_TIMEOUT_CONNECT/_WRITE/_POOL: float`
  - `settings.MAIN_TIMEOUT_READ / CLASSIFY_TIMEOUT_READ / SUMMARY_TIMEOUT_READ: float`
  - `settings.EMBEDDING_TIMEOUT_READ_QUERY / EMBEDDING_TIMEOUT_READ_INGEST: float`
  - `settings.RERANK_TIMEOUT_READ: float`
  - `settings.ROLE_TIMEOUT_READ: dict[str, float]`（按角色查表；`judge` **不在表内**）
  - `build_chat(role, ...)` / `build_embeddings(*, read_timeout: float | None = None)` / `build_rerank(...)` 产出的客户端带显式超时

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/llm/provider/test_factory_timeouts.py`：

```python
"""构造即显式：三类 build_* 必须带超时且 SDK 重试为 0（spec timeout-policy）。"""

from __future__ import annotations

from src.infra.llm.provider import factory


def _read_of(client) -> float:
    return client.timeout.read


def test_build_chat_sets_explicit_timeouts_and_disables_sdk_retry():
    model = factory.build_chat("main")
    assert model.max_retries == 0, "SDK 自动重试必须关闭"
    assert model.http_async_client is not None, "主链路是异步流式 ⇒ 必须设 http_async_client"
    assert model.http_client is not None, "同步路径同样要显式超时"
    assert model.http_async_client.timeout.read > 0
    assert model.http_client.timeout.read > 0


def test_roles_can_have_different_read_timeouts():
    main = factory.build_chat("main")
    classify = factory.build_chat("classify")
    assert main.http_async_client.timeout.read != classify.http_async_client.timeout.read


def test_judge_role_is_not_touched_by_this_change():
    """judge 的超时真源归评测链（D17）；本变更不得注入默认值。"""
    model = factory.build_chat("judge")
    assert model.http_async_client is None or model.http_async_client.timeout.read != 180.0


def test_build_embeddings_explicit_and_query_override():
    ingest = factory.build_embeddings()
    query = factory.build_embeddings(read_timeout=15.0)
    assert ingest.max_retries == 0
    assert ingest.http_client is not None
    assert query.http_client.timeout.read == 15.0
    assert ingest.http_client.timeout.read > query.http_client.timeout.read


def test_build_rerank_has_timeout_client():
    reranker = factory.build_rerank()
    # 经 client 适配器注入 request_timeout（D13）
    assert getattr(reranker, "client", None) is not None
    assert reranker.client is not __import__("dashscope").TextReRank
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_factory_timeouts.py -q`
Expected: FAIL（`max_retries` 为 `None` / `http_async_client` 为 `None` / `build_embeddings` 无 `read_timeout` 形参）

- [ ] **Step 3: 加设置项**

在 `src/config/settings.py` 的「重试策略」段**之前**新增一段（键名**不含 `RAGAS_`**，与评测链隔离）：

```python
# ====== 生产链路角色级超时（自有键名；评测链用 RAGAS_* 族，两者不共享）======
# 依据：change timeout-and-degradation-hardening 的 design D2/D11。
# ⚠ 这些值待真实分位校准（D11 取值表标注"待校准"）；全部可被 .env 覆盖。
HTTP_TIMEOUT_CONNECT: float = float(os.getenv("HTTP_TIMEOUT_CONNECT", "10"))
HTTP_TIMEOUT_WRITE: float = float(os.getenv("HTTP_TIMEOUT_WRITE", "30"))
HTTP_TIMEOUT_POOL: float = float(os.getenv("HTTP_TIMEOUT_POOL", "10"))
# main 为流式：read 是「请求发出 → 字节到达」的**单一预算**（含首字节等待，D4）
MAIN_TIMEOUT_READ: float = float(os.getenv("MAIN_TIMEOUT_READ", "180"))
CLASSIFY_TIMEOUT_READ: float = float(os.getenv("CLASSIFY_TIMEOUT_READ", "60"))
SUMMARY_TIMEOUT_READ: float = float(os.getenv("SUMMARY_TIMEOUT_READ", "60"))
# embedding 两档：查询（在线、失败可退词法）要紧；入库（离线批量）可宽（D10 的唯一例外）
EMBEDDING_TIMEOUT_READ_QUERY: float = float(os.getenv("EMBEDDING_TIMEOUT_READ_QUERY", "15"))
EMBEDDING_TIMEOUT_READ_INGEST: float = float(os.getenv("EMBEDDING_TIMEOUT_READ_INGEST", "150"))
# rerank：内层唯一预算（外层 wait_for 见 RERANK_OUTER_TIMEOUT，必须 > 内层，D13）
RERANK_TIMEOUT_READ: float = float(os.getenv("RERANK_TIMEOUT_READ", "5"))
RERANK_OUTER_TIMEOUT: float = float(os.getenv("RERANK_OUTER_TIMEOUT", "6"))
# 按角色查表；judge **不在表内**（其真源归评测链，D17）
ROLE_TIMEOUT_READ: dict[str, float] = {
    "main": MAIN_TIMEOUT_READ,
    "classify": CLASSIFY_TIMEOUT_READ,
    "summary": SUMMARY_TIMEOUT_READ,
    "embedding": EMBEDDING_TIMEOUT_READ_INGEST,
    "rerank": RERANK_TIMEOUT_READ,
}
```

- [ ] **Step 4: 改 `factory.py` 的三个 `build_*`**

在 `src/infra/llm/provider/factory.py` 顶部加 import 与工具函数：

```python
def _http_timeout(read: float) -> httpx.Timeout:
    """细粒度 httpx 超时（ADR-0024：不用平铺标量，否则四维同值且看不出卡在哪段）。"""
    return httpx.Timeout(
        connect=settings.HTTP_TIMEOUT_CONNECT,
        read=read,
        write=settings.HTTP_TIMEOUT_WRITE,
        pool=settings.HTTP_TIMEOUT_POOL,
    )
```

`build_chat` 尾部改为（**注意：`judge` 不注入**；调用方显式传入的 `http_client` / `http_async_client` 优先）：

```python
    resolved = _resolve_model(role)
    extra_kwargs = _llm_kwargs()
    if thinking is not None:
        extra_kwargs["extra_body"] = thinking_extra_body(resolved.provider, thinking)
    extra_kwargs.update(overrides)
    if role in settings.ROLE_TIMEOUT_READ:
        tout = _http_timeout(settings.ROLE_TIMEOUT_READ[role])
        # 同步与异步两个 client 都要设：主链路走异步流式，只设 http_client 不生效
        extra_kwargs.setdefault("http_client", httpx.Client(timeout=tout))
        extra_kwargs.setdefault("http_async_client", httpx.AsyncClient(timeout=tout))
        # spec：SDK 自动重试必须关闭（不得被 env / 调用方打开）
        extra_kwargs["max_retries"] = 0
    return RetryingReasoningChat(
        model=model or resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        reasoning_field=resolved.provider.reasoning_field,
        **extra_kwargs,
    )
```

> `RetryingReasoningChat` 在 **Task 4** 定义。为让本任务可独立跑通，Task 2 先保持
> `ReasoningPreservingChat`，Task 4 再改这一行 —— **两步都留在本计划的提交序列里**，
> 不要合并成一次提交。

`build_embeddings` 改为带查询档覆盖：

```python
def build_embeddings(*, read_timeout: float | None = None) -> OpenAIEmbeddings:
    """构造向量化模型（角色固定 embedding）。

    Args:
        read_timeout: 覆盖 read 超时；None ⇒ 用入库档（离线批量，较宽）。
    """
    resolved = _resolve_model("embedding")
    facts = resolved.provider.catalog.get(resolved.model)
    chunk_size = 20
    if facts is not None and facts.embedding_batch_size:
        chunk_size = facts.embedding_batch_size
    read = (
        read_timeout
        if read_timeout is not None
        else settings.EMBEDDING_TIMEOUT_READ_INGEST
    )
    tout = _http_timeout(read)
    return OpenAIEmbeddings(
        model=resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        check_embedding_ctx_length=False,
        chunk_size=chunk_size,
        http_client=httpx.Client(timeout=tout),
        http_async_client=httpx.AsyncClient(timeout=tout),
        max_retries=0,
    )
```

- [ ] **Step 5: 查询路用查询档（D10 的唯一例外）**

改 `src/infra/db/vector_store/pg_store.py` 的 `QueryEmbedder`：把 `get_embeddings()` 换成查询档。

```python
    def embed_query(self, text: str) -> list[float]:
        from src.config import settings

        return get_embeddings(read_timeout=settings.EMBEDDING_TIMEOUT_READ_QUERY).embed_query(text)
```

（`embed_documents` 保持入库档不变。**客户端复用不属本变更**，已登记为 follow-up，不要顺手改。）

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/ tests/rag/ -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/config/settings.py src/infra/llm/provider/factory.py src/infra/db/vector_store/pg_store.py tests/infra/llm/provider/test_factory_timeouts.py
git commit -m "feat(timeout): 三类 build_* 显式超时 + 关 SDK 重试（角色级自有键名，judge 除外）"
```

---

### Task 3: rerank 超时下沉到客户端 + 三层超时嵌套（D13）

**Files:**
- Modify: `src/infra/llm/provider/factory.py`（`build_rerank` 注入 `client` 适配器）
- Modify: `src/agents/tools/rag_tools.py:154-156`（外层 `wait_for` 用 `RERANK_OUTER_TIMEOUT`）
- Test: `tests/infra/llm/provider/test_factory_timeouts.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `settings.RERANK_TIMEOUT_READ` / `RERANK_OUTER_TIMEOUT` / `_http_timeout`
- Produces: `_TimeoutReRankAdapter(inner, timeout)`（`call(**kwargs)` 注入 `request_timeout`）

**背景（为什么必须嵌套）**：现状是「外层 `wait_for(5s)` + 底层无超时」⇒ `wait_for` 只放弃等待、**不取消 `to_thread` 里的线程**。加了内层超时后若外层仍 5s < 内层预算（`5 + 1 + 5 + 2 + 5 ≈ 18s`），**外层仍会先放弃、线程仍跑到底**（评审 F5）。⇒ 内层 `attempts=1` 作唯一预算（5s），外层提到 6s 仅作双保险。

- [ ] **Step 1: 追加失败测试**

在 `tests/infra/llm/provider/test_factory_timeouts.py` 追加：

```python
def test_rerank_adapter_injects_request_timeout():
    """适配器必须把 request_timeout 注入底层 call（D13：SDK 无构造期超时字段）。"""
    from src.infra.llm.provider.factory import _TimeoutReRankAdapter

    seen: dict[str, object] = {}

    class _FakeClient:
        @staticmethod
        def call(**kwargs):
            seen.update(kwargs)
            return "resp"

    adapter = _TimeoutReRankAdapter(_FakeClient, timeout=5.0)
    assert adapter.call(model="m", query="q", documents=["d"]) == "resp"
    assert seen["request_timeout"] == 5.0


def test_rerank_outer_budget_exceeds_inner():
    from src.config import settings

    assert settings.RERANK_OUTER_TIMEOUT > settings.RERANK_TIMEOUT_READ, (
        "外层 wait_for 必须大于内层唯一预算，否则外层先放弃、底层线程跑到底"
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_factory_timeouts.py -q -k rerank`
Expected: FAIL — `ImportError: cannot import name '_TimeoutReRankAdapter'`

- [ ] **Step 3: 写适配器并接到 `build_rerank`**

在 `src/infra/llm/provider/factory.py` 内、`build_rerank` 之前加：

```python
class _TimeoutReRankAdapter:
    """给 DashScope TextReRank 注入请求级超时的薄适配器。

    为什么需要：`DashScopeRerank` 的 pydantic 字段只有
    `['client','model','top_n','dashscope_api_key']` 且 `model_config['extra']='forbid'`
    ⇒ **构造器传 timeout 会直接报错**；而它唯一的调用出口是 `self.client.call(...)`，
    dashscope SDK 又**原生支持 `request_timeout`**（`api_request_factory.py:33` 会 pop 出来
    交给 HTTP 层）。故在 `client` 这一个注入点上兜住超时。

    它**根治而非绕过**：有了 `request_timeout`，挂死的调用会自行结束
    （外层 `asyncio.wait_for` 只是放弃等待，**不终止** `to_thread` 里的线程）。
    """

    def __init__(self, inner: object, timeout: float) -> None:
        self._inner = inner
        self._timeout = timeout

    def call(self, **kwargs: object) -> object:
        kwargs.setdefault("request_timeout", self._timeout)
        return self._inner.call(**kwargs)  # type: ignore[attr-defined]
```

`build_rerank` 的返回改为：

```python
    return DashScopeRerank(
        model=model or resolved.model,
        top_n=effective_top_n,
        api_key=resolved.api_key,
        client=_TimeoutReRankAdapter(
            dashscope.TextReRank, timeout=settings.RERANK_TIMEOUT_READ
        ),
    )
```

- [ ] **Step 4: 外层预算改为 6s**

改 `src/agents/tools/rag_tools.py`：
- import 处把 `RERANK_TIMEOUT` 换成 `RERANK_OUTER_TIMEOUT`
- 154-156 行与日志字段用 `RERANK_OUTER_TIMEOUT`

```python
            contexts = await asyncio.wait_for(
                asyncio.to_thread(retrieval.rerank_results, query, results, reranker),
                timeout=RERANK_OUTER_TIMEOUT,
            )
```

> `src/config/const.py` 的 `RERANK_TIMEOUT = 5` **保留不动**（仍有其它引用时不要删；
> 若无引用，在 Task 5 统一清理并说明）。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/ tests/rag/ tests/agents/ -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/infra/llm/provider/factory.py src/agents/tools/rag_tools.py tests/infra/llm/provider/test_factory_timeouts.py
git commit -m "fix(rerank): 超时下沉到客户端（client 注入 request_timeout）+ 三层超时嵌套（内 5s / 外 6s）"
```

---

### Task 4: 主链路 LLM 本层重试（D16 + 「不得只关不加」）

**Files:**
- Create: `src/infra/llm/retrying_chat.py`
- Modify: `src/infra/llm/provider/factory.py`（`build_chat` 返回类型换成本子类）
- Modify: `src/core/log_events.py` + `src/core/log_event_specs.py`（新事件 `model retry` **两处登记**）
- Test: `tests/infra/llm/test_retrying_chat.py`

**Interfaces:**
- Consumes: Task 1 的 `retry_policy.is_retryable` / `retry_policy.sleep_seconds`；Task 2 的 settings 键
- Produces: `RetryingReasoningChat(reasoning_field=..., ...)`，对 `_astream` / `_agenerate` 在**首个 chunk 前**失败时重试

**为什么是子类而不是包装器（评审 I1，两条更省的路都不通）**：
- `Runnable.with_retry` **明确不重试 stream**（`langchain_core/runnables/retry.py:378` 原文注释）；
- 循环 middleware 的 `awrap_model_call` 只拿得到 `ModelResponse`、**拿不到 chunk**，实现不了"已产出即不重试"。
⇒ 子类继承 `ReasoningPreservingChat` 可直接获得 `bind_tools` / reasoning 能力，无需代理转发。

**归属**：定义在 `src/infra/llm/`（provider 层**之外**）—— ADR-0021 明确 provider 兼容层不做重试策略；
`build_chat` 只是**构造**它。

- [ ] **Step 1: 登记事件（两处同名，import 期校验会强制一致）**

`src/core/log_events.py` 的 `Event` 枚举加：

```python
    MODEL_RETRY = "model retry"
```

`src/core/log_event_specs.py` 的 `EVENT_SPECS` 加：

```python
    "model retry": EventSpec(
        "model retry", "agent", "warning", ("role", "attempt", "err")
    ),
```

- [ ] **Step 2: 写失败测试**

新建 `tests/infra/llm/test_retrying_chat.py`：

```python
"""RetryingReasoningChat：只在首个 chunk 之前重试，且不阻塞事件循环。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from src.infra.llm.provider.chat import ReasoningPreservingChat
from src.infra.llm.retrying_chat import RetryingReasoningChat

# ⚠ 关键：必须 patch **父类** `ReasoningPreservingChat._astream`，而不是被测的
# `RetryingReasoningChat._astream` —— 后者一 patch 就把"重试包装"本身换掉了，
# 测试会变成假通过。被测层调 `super()._astream(...)` ⇒ 落在被 patch 的父类上。


def _chunk(text: str) -> ChatGenerationChunk:
    return ChatGenerationChunk(message=AIMessageChunk(content=text))


def _model(**kwargs) -> RetryingReasoningChat:
    return RetryingReasoningChat(
        model="fake",
        api_key="k",  # type: ignore[arg-type]
        base_url="http://localhost:1",
        max_retries=0,
        **kwargs,
    )


def test_retries_before_first_chunk(monkeypatch):
    model = _model(model_retry_attempts=3, model_retry_initial_interval=0.0)
    calls: list[int] = []

    async def fake_astream(self, *args, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("boom")
        yield _chunk("ok")

    monkeypatch.setattr(ReasoningPreservingChat, "_astream", fake_astream, raising=True)

    async def run() -> list[str]:
        return [c.message.content async for c in model._astream([])]

    assert asyncio.run(run()) == ["ok"]
    assert len(calls) == 3


def test_does_not_retry_after_first_chunk(monkeypatch):
    model = _model(model_retry_attempts=3, model_retry_initial_interval=0.0)
    calls: list[int] = []

    async def fake_astream(self, *args, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        calls.append(1)
        yield _chunk("partial")
        raise TimeoutError("mid-stream")

    monkeypatch.setattr(ReasoningPreservingChat, "_astream", fake_astream, raising=True)

    async def run() -> list[str]:
        out: list[str] = []
        with pytest.raises(TimeoutError):
            async for c in model._astream([]):
                out.append(c.message.content)
        return out

    assert asyncio.run(run()) == ["partial"], "已产出后不得重试（否则重复输出）"
    assert len(calls) == 1


def test_does_not_retry_non_retryable(monkeypatch):
    model = _model(model_retry_attempts=3, model_retry_initial_interval=0.0)
    calls: list[int] = []

    async def fake_astream(self, *args, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        calls.append(1)
        raise ValueError("bad input")
        if False:  # pragma: no cover - 仅为让本函数成为 async generator
            yield _chunk("")

    monkeypatch.setattr(ReasoningPreservingChat, "_astream", fake_astream, raising=True)

    async def run() -> None:
        async for _ in model._astream([]):
            pass

    with pytest.raises(ValueError):
        asyncio.run(run())
    assert len(calls) == 1, "输入校验类不得不重试"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/test_retrying_chat.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.infra.llm.retrying_chat'`

- [ ] **Step 4: 写实现**

新建 `src/infra/llm/retrying_chat.py`：

```python
"""主链路 LLM 的本层有界重试：**只在首个 chunk 之前**重试。

归属与理由（change `timeout-and-degradation-hardening` 的 design D16）：
- 关掉 SDK 重试后若不补本层重试，主链路会从"重试 2 次"变成**零重试**（相对现状倒退）；
- 但**流已开始输出就不能重试**（会重复输出给用户）⇒ 重试窗口仅限首个 chunk 之前；
- 必须实现为**模型对象子类**：`Runnable.with_retry` 明确不重试 stream
  （`langchain_core/runnables/retry.py:378`），循环 middleware 又拿不到 chunk。
- 本类定义在 provider 兼容层**之外**（ADR-0021：该层不做重试策略）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from loguru import logger

from src.core.log_events import Event
from src.core.logging import log_event
from src.infra.llm.provider.chat import ReasoningPreservingChat
from src.infra import retry_policy


class RetryingReasoningChat(ReasoningPreservingChat):
    """在"首个 chunk 之前"失败时按 `retry_policy` 有界重试。"""

    # 两个 pydantic 字段：阈值由构造方（factory）从 settings 注入，本类不读环境
    model_retry_attempts: int = 3
    model_retry_initial_interval: float = 1.0
    model_retry_backoff: float = 2.0
    model_retry_jitter: float = 0.3
    model_retry_role: str = ""

    def _should_retry(self, exc: BaseException, attempt: int) -> bool:
        return attempt < self.model_retry_attempts and retry_policy.is_retryable(exc)

    async def _sleep_before_retry(self, attempt: int) -> None:
        await asyncio.sleep(
            retry_policy.sleep_seconds(
                attempt,
                self.model_retry_initial_interval,
                self.model_retry_backoff,
                self.model_retry_jitter,
            )
        )

    def _log_retry(self, attempt: int, exc: BaseException) -> None:
        log_event(
            Event.MODEL_RETRY,
            role=self.model_retry_role or "unknown",
            attempt=attempt,
            err=type(exc).__name__,
        )

    async def _astream(  # type: ignore[override]
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        """流式：仅在**尚未产出任何 chunk** 时重试。"""
        attempt = 0
        while True:
            produced = False
            try:
                async for chunk in super()._astream(*args, **kwargs):
                    produced = True
                    yield chunk
                return
            except BaseException as exc:  # noqa: BLE001 - 由 _should_retry 判定
                attempt += 1
                if produced or not self._should_retry(exc, attempt):
                    logger.warning(
                        "[llm] model retry skipped role={} produced={} err={}",
                        self.model_retry_role,
                        produced,
                        type(exc).__name__,
                    )
                    raise
                self._log_retry(attempt, exc)
                await self._sleep_before_retry(attempt)

    async def _agenerate(  # type: ignore[override]
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """非流式：同一策略（无"已产出"概念，整次失败才重试）。"""
        attempt = 0
        while True:
            try:
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            except BaseException as exc:  # noqa: BLE001
                attempt += 1
                if not self._should_retry(exc, attempt):
                    raise
                self._log_retry(attempt, exc)
                await self._sleep_before_retry(attempt)
```

- [ ] **Step 5: 把 factory 的返回类型换成本子类，并注入阈值**

`src/infra/llm/provider/factory.py`：把 `from ...chat import ReasoningPreservingChat` 之外加
`from src.infra.llm.retrying_chat import RetryingReasoningChat`（**注意**：`retrying_chat` 又 import
`provider.chat` ⇒ 若出现循环 import，把该 import 移到 `build_chat` 函数体内，保持模块级 import 干净）。

`build_chat` 里 `return` 换成：

```python
    return RetryingReasoningChat(
        model=model or resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        reasoning_field=resolved.provider.reasoning_field,
        model_retry_attempts=settings.RETRY_MAX_ATTEMPTS,
        model_retry_initial_interval=settings.RETRY_INITIAL_INTERVAL,
        model_retry_backoff=settings.RETRY_BACKOFF_FACTOR,
        model_retry_role=role,
        **extra_kwargs,
    )
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/ tests/core/ -q`
Expected: PASS（`tests/core/test_log_events.py` 会校验两处登记一致）

- [ ] **Step 7: 提交**

```bash
git add src/infra/llm/retrying_chat.py src/infra/llm/provider/factory.py src/core/log_events.py src/core/log_event_specs.py tests/infra/llm/test_retrying_chat.py
git commit -m "feat(llm): 主链路本层有界重试（模型对象子类 + 首个 chunk 守卫 + model retry 事件）"
```

---

### Task 5: P1 验收（tasks 1.6–1.10）+ 一致性自检

**Files:**
- Modify: 无需改产品代码；如发现 `const.py` 的 `RERANK_TIMEOUT` 已无引用则在本次清理
- Test: 无新增（复用既有 + 本阶段新增）

**Interfaces:**
- Consumes: Task 1–4 全部产物
- Produces: 本阶段的验收证据（命令输出必须贴进 PR / 台账）

- [ ] **Step 1: 静态检查**

Run: `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright`
Expected: 全过（`pyright` 0 error）

- [ ] **Step 2: 非 DB 单测（worktree 验收范围）**

Run: `.venv/bin/python -m pytest -q --ignore=tests/parsers -x`
Expected: PASS。
**注意**：`tests/parsers` 因 worktree 缺 gitignored `data/` 会环境性失败（不是回归，见 tasks 0.2）；
DB 相关用例在本机报 `gaierror`，若命中请单独排除并在台账注明。

- [ ] **Step 3: 逐条核对 tasks 1.6–1.10 的断言**

- [ ] 1.6 断言「构造即显式」：`.venv/bin/python -m pytest tests/infra/llm/provider/test_factory_timeouts.py -q`
- [ ] 1.7 断言分类行为：`.venv/bin/python -m pytest tests/infra/test_retry_policy.py -q`
- [ ] 1.8 断言 rerank 超时可终止 + 内层 < 外层：`.venv/bin/python -m pytest tests/infra/llm/provider/test_factory_timeouts.py -q -k rerank`
- [ ] 1.9 断言本阶段不改变降级行为：`.venv/bin/python -m pytest tests/rag/ tests/agents/ -q`
- [ ] 1.10 断言「不得只关不加」：`.venv/bin/python -m pytest tests/infra/llm/test_retrying_chat.py -q`

- [ ] **Step 4: 跨工件一致性机检**

Run: `.venv/bin/python docs/tmp/check-change-consistency.py docs/openspec/changes/timeout-and-degradation-hardening`
Expected: `0 条命中`。**若非 0**：说明本阶段改动与 design / spec / tasks 出现口径漂移（例如新增的 settings 键与 D11 取值表不一致）⇒ 先修文档再继续。

- [ ] **Step 5: 本地真实调用取分位（对照实测的载体，tasks 0.3）**

**不在生产做、也不看本机日志**（stub 流量）。在本机真实发起若干次问答，记录 `[agent] model turn … latency_ms=` 与 `[retrieval] retrieve done … latency_ms=`，回填 `design.md` D11 取值表的「待校准」项。

- [ ] **Step 6: 提交验收记录并收口 P1**

```bash
git add -u
git commit -m "test(timeout): P1 验收（静态检查 + 非 DB 单测 + 机检 + 分位回填）"
```

---

## Self-Review（写完计划后自查）

**1. Spec 覆盖**
- `timeout-policy`「按角色的超时与重试唯一产地」→ Task 2（`build_*` 显式 + 角色级 + `judge` 排除）
- `timeout-policy`「流式超时按传输层单一预算 + 业务层事件守护」→ **传输层在 Task 2**；**业务层（idle/首事件两档阈值）属 P3**，不在本计划
- `timeout-policy`「重试按异常类别分流 + jitter + 不阻塞事件循环」→ Task 1（`is_retryable` / `sleep_seconds` / `acall_with_retry`）
- `timeout-policy`「首版取值以保守值起步、可观测只补缺口」→ Task 2（键与默认值）；**embedding/rerank 单阶段耗时属 tasks §4**，不在本计划
- `timeout-policy`「流式重试窗口限于首个 chunk 之前」→ Task 4
- `provider-compat`「客户端超时与重试的显式入参与取值归属」→ Task 2
- `provider-compat`「配置覆盖规则（四类→五类）」→ Task 2（新增的设置项即可被 `.env` 覆盖）
- `provider-compat`「精排超时可终止」→ Task 3
- `degradation-policy` 全部 → **属 P2**，不在本计划（本阶段明确"不改变降级行为"）
- `hybrid-retrieval`「单路失败降级」→ **属 P2**
- ✅ 本阶段（P1）范围内无遗漏

**2. 占位符扫描**：无 "TBD / TODO / 适当处理" 之类；每个代码步骤都给了可直接粘贴的代码。
例外与理由：Task 4 Step 5 提示"若出现循环 import，把 import 移到函数体内"—— 这是**条件分支**而非占位符，两种写法都已给出。

**3. 类型/命名一致性**：
- `retry_policy.call_with_retry` / `acall_with_retry` / `is_retryable` / `sleep_seconds` 在 Task 1 定义，Task 4 使用，名称一致；
- `_TimeoutReRankAdapter`（Task 3 定义并测试，名称一致）；
- `RetryingReasoningChat` + `model_retry_*` 字段（Task 4 定义，Task 2 的 factory 在 Task 4 Step 5 才切换返回类型 —— **顺序已在两处注明**）；
- settings 键名在 Task 2/3 使用一致（`RERANK_TIMEOUT_READ` / `RERANK_OUTER_TIMEOUT`）。

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-10-16-timeout-and-degradation-p1.md`。两种执行方式：

1. **Subagent-Driven（推荐）** —— 每个任务派一个新的子代理，任务之间我做两阶段评审，迭代快；
2. **Inline Execution** —— 在当前会话里按 `executing-plans` 批量执行、在检查点停下评审。

选哪种？
