# deepeval-bridge-eval · Phase 1（底座）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把评测打分入口从 RAGAS 0.4.x 现代栈换成 DeepEval 4.2.9 内建的 RAGAS 桥（经典四指标），并在同一次改动里把裁判构造、失败语义、NaN 口径与门禁落到实处 —— 产出一条能跑、口径自洽的评测链路。

**Architecture:** 依赖线由 `ragas==0.4.3` 下移到 `ragas==0.3.9` 并引入 `deepeval==4.2.9`；评测编排放弃 `@experiment`/`Dataset`，改为「逐样本构造 `LLMTestCase` → 串行调桥度量的 `measure()`」；裁判经项目 provider 层 `build_chat("judge", …)` 构造，HTTP 走 `http_async_client`（细粒度超时 + 请求级 hooks）；失败语义从「捕异常重试」改为「非掩码 NaN 重试」，门禁新增 NaN 率判据。

**Tech Stack:** Python 3.12 · DeepEval 4.2.9 · RAGAS 0.3.9 · langchain-openai 1.3.3 · httpx 0.28.1 · pandas · pytest 9 · Langfuse v2

**Spec:** `docs/openspec/changes/deepeval-bridge-eval/{proposal.md, design.md, specs/evaluation-pipeline/spec.md, tasks.md}`（设计与规格是唯一权威；本计划只做「怎么落地」）

## Global Constraints

以下为 project-wide 硬约束，**每个任务都隐含包含**（数值逐字取自 spec/design）：

- `ragas` pin = **`0.3.9`**（精确 pin，与仓库既有 pin 纪律一致）；`deepeval` pin = **`4.2.9`**；`click` / `huggingface_hub` SHALL 显式记录下限，防回落。
- 评测入口 = DeepEval；RAGAS 指标 = **经典四项**：`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`。
- **SHALL NOT** 使用聚合度量 `RagasMetric`，**SHALL NOT** 使用 `RAGASContextualEntitiesRecall`。
- 每个桥度量构造 **SHALL 显式传 `model=`**（桥的默认是已停服的 `gpt-3.5-turbo`）。
- 裁判温度**不由本项目控制**：`n=1` 的三项 = **0.01**，`answer_relevancy`（strictness=3）= **0.3**。报告/台账 **SHALL NOT** 出现「裁判 temperature=0」。
- 裁判 **SHALL NOT** 传 `temperature`；**SHALL NOT** 传 `top_p`。
- 裁判 client **SHALL** 经 `http_async_client` 传（**不是** `http_client`，后者只喂同步 client）；hook **SHALL** 为 `async def` + `await response.aread()`。
- 裁判思考开关 SHALL 经 `build_chat(thinking=…)`，**SHALL NOT** 硬写 `extra_body={"enable_thinking": …}`。
- **四指标评分 SHALL 串行（并发=1）**（桥复用 `ragas.metrics` 模块级单例 + `evaluate` 复位 `metric.llm` ⇒ 并发会间歇性错分）。
- `answer_correctness` **退出评测**：不参与 `overall_score`，`kb_eval` API **SHALL NOT** 再暴露。
- NaN 口径：**分母 = 纯 KB 样本 × 四项指标（格数）**；**分子 = 纯 KB 格里的全部 NaN**；阈值 = **经验初值 10%（待首轮复核）**；`>10%` 门禁 fail 且报告给「建议启用逃生门」。
- 重试判据 = **非掩码 NaN**（有界，`RAGAS_METRIC_MAX_ATTEMPTS`）；**唯一例外：HTTP 429 / `openai.RateLimitError` 不触发外层重试**。
- 环境变量 **SHALL** 显式设置 `DEEPEVAL_DISABLE_DOTENV=1` 与 `DEEPEVAL_TELEMETRY_OPT_OUT=1`。
- 报告/台账 SHALL 标注**口径版本**（`bridge-0.3.9`）；分数**跨口径不可直接比较**。
- 本阶段 **SHALL NOT** 触碰：`src/agents/**`、生产链路、`kb_eval` API 的**形状**（只删 `answer_correctness` 字段）、前端。

## 阶段划分（本计划只做 Phase 1）

| 阶段 | 覆盖 tasks.md 组 | 产物 | 计划文档 |
|---|---|---|---|
| **Phase 1（本文件）** | 组 1–4 | 四指标经桥跑通 + 裁判/可观测 + 失败语义/主分/门禁 | `2026-10-16-deepeval-bridge-eval-phase1.md` |
| Phase 2 | 组 5 | Agent 行为轴（`evals_iterator` 驱动、逐题分回读） | 待写 |
| Phase 3 | 组 6–9 | 出题下线 / 逃生门边界 / Langfuse 回写 / 0.4.x 删除 | 待写 |
| Phase 4 | 组 10 | 活体验收、阈值标定、NaN 复核、台账 | 待写 |

**与 tasks.md 的两处刻意差异（本计划生效后须回写 tasks.md）**：

1. tasks 9.6 的「删/重写现代栈测试」**提前到本阶段**（T11）——否则本阶段结束时树是红的，违背「每个任务以可独立测试的交付物收尾」。
2. tasks 1.3 的「评测路径无现代栈符号」自查**移到本阶段末尾**（T12）——它在 T1 之后必然失败（`build_metrics` 还是现代版），是阶段级断言而非任务级。

## File Structure

| 文件 | 责任 | 本阶段动作 |
|---|---|---|
| `pyproject.toml` | 依赖 pin | 改 `ragas==0.3.9`、加 `deepeval==4.2.9`、显式 `click`/`huggingface_hub` 下限 |
| `src/config/settings.py` | 运行时配置 + 进程级 env 兜底 | 顶部 `os.environ.setdefault` 设两个 `DEEPEVAL_*` |
| `src/infra/llm/provider/factory.py` | provider 层构造 | **新增** `build_bridge_judge()`（裁判：`build_chat` + `http_async_client` + hooks 挂点） |
| `src/infra/llm/provider/http_observability.py` | **新建** | httpx 日志 hooks 与证据桶（`make_logging_client` / `HookEvidence`） |
| `src/cli/eval_scoring.py` | 评测纯函数 | **改写** `build_metrics`（四桥指标）；**新增** `nan_cell_stats`；`apply_sample_masks` 保持 |
| `src/cli/eval_ragas.py` | CLI 编排 | **改写** 打分编排（去 `@experiment`/`Dataset`/并发）、重试判据、`check_gate`（加 NaN 率）、报告元数据 |
| `src/config/const.py` | 常量单一来源 | `RAGAS_METRIC_COLUMNS` 删 `answer_correctness`；注释订正；新增 `EVAL_NAN_RATE_MAX` |
| `src/api/kb_eval.py` | 报告 API | 删 `answer_correctness` 字段与其均值函数 |
| `docs/agents/api_contract.md` | 契约文档 | 7 处 `answer_correctness` 逐处订正 |
| `docs/agents/ragas-eval-ledger.md` | 跨轮台账 | 表头加「口径版本」列（保留 `answer_correctness` 列，历史行不动） |
| `tests/cli/test_eval_bridge_metrics.py` | **新建** | 四指标构造/串行/NaN 语义/门禁 的回归防线 |
| `tests/cli/test_eval_modern_stack.py`、`test_metric_retry.py`、`tests/infra/llm/provider/test_ragas_modern_factories.py` | 现代栈断言 | **删除或重写**（T11） |

---

### Task 1: 依赖换线（ragas 0.4.3 → 0.3.9 + deepeval）＋ DeepEval 环境变量

**Files:**
- Modify: `pyproject.toml:67`（`ragas==0.4.3` → `ragas==0.3.9`；新增 `deepeval==4.2.9`；显式记录 `click` / `huggingface_hub` 下限）
- Modify: `src/config/settings.py`（模块顶部加 `os.environ.setdefault`）
- Test: `tests/config/test_deepeval_env.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: 安装后的 `ragas==0.3.9` / `deepeval==4.2.9`；进程内 `DEEPEVAL_DISABLE_DOTENV=1`、`DEEPEVAL_TELEMETRY_OPT_OUT=1`

- [ ] **Step 1: 写失败测试（环境变量兜底）**

```python
# tests/config/test_deepeval_env.py
import os


def test_deepeval_env_defaults_are_set_on_settings_import():
    os.environ.pop("DEEPEVAL_DISABLE_DOTENV", None)
    os.environ.pop("DEEPEVAL_TELEMETRY_OPT_OUT", None)

    import src.config.settings  # noqa: F401  （import 即生效）

    assert os.environ["DEEPEVAL_DISABLE_DOTENV"] == "1"
    assert os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] == "1"


def test_autoload_dotenv_is_disabled(tmp_path, monkeypatch):
    """flag 为真时，`deepeval` 的 autoload 不得把 cwd 的 .env 注入 process env。

    直接调 `deepeval.config.settings.autoload_dotenv()`（`deepeval/__init__.py:12`
    在 import 时调的就是它），并在临时目录放一个真 .env + `chdir` 过去——
    否则断言会变成"恒真"的空测试（仓库里没有 `SENTINEL_FROM_DOTENV` 这种键）。
    """
    import src.config.settings  # noqa: F401  （先设好 DEEPEVAL_DISABLE_DOTENV=1）

    (tmp_path / ".env").write_text("SDD_DOTENV_SENTINEL=1\n")
    monkeypatch.chdir(tmp_path)
    os.environ.pop("SDD_DOTENV_SENTINEL", None)

    from deepeval.config.settings import autoload_dotenv

    autoload_dotenv()

    assert "SDD_DOTENV_SENTINEL" not in os.environ, "flag 为真时 autoload 必须早退"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/code/corporate_rag-deepeval-bridge && python -m pytest tests/config/test_deepeval_env.py -v`
Expected: FAIL（`KeyError: 'DEEPEVAL_DISABLE_DOTENV'`；`deepeval` 尚未安装 → ImportError）

- [ ] **Step 3: 改 pyproject 并安装**

```toml
# pyproject.toml —— [project.optional-dependencies].dev 内
dev = [
    "pytest==9.0.2",
    "pytest-asyncio==1.4.0",
    "pytest-cov==7.0.0",
    "ragas==0.3.9",            # was 0.4.3：为统一单环境主动下移（design D1）
    "deepeval==4.2.9",         # 评测入口；其内建 RAGAS 桥调经典四指标
    "click==8.3.3",            # 下限：与 deepeval 的 click 约束共存（design D1）
    "huggingface_hub==1.16.1", # 下限：防回落到与 deepeval 互斥的一侧
    "rapidfuzz==3.14.5",
    "datasets==4.0.0",
    "pyright==1.1.411",
    "pre-commit==4.6.1",
    "ruff==0.16.2",
]
```

Run: `pip install -e ".[dev]" && pip check`

Expected: 解析成功；实装 `ragas 0.3.9` / `deepeval 4.2.9` / `click 8.3.3` / `huggingface_hub 1.16.1`；`pip check` **相对既有脏基线无新增冲突**（基线含 `corporate-rag requires chromadb / rank-bm25` 两条历史噪声，出现它们属正常）。

- [ ] **Step 4: 加环境变量兜底**

```python
# src/config/settings.py —— 文件顶部（在 import 其它 src.* 之前）
import os

# DeepEval 在 import 时会自动加载 .env / .env.local 进 process env，并默认开启
# posthog 遥测；两者都会污染本项目配置解析。必须在 deepeval 被 import 之前设好。
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
```

> 说明：放 `settings.py` 而非 CLI 顶部，是因为 `settings` 是最早被 import 的模块之一，能覆盖「别处先 import deepeval」的路径。

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/config/test_deepeval_env.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml src/config/settings.py tests/config/test_deepeval_env.py
git commit -m "feat(eval): ragas 下移 0.3.9 + 引入 deepeval 4.2.9，并兜底 DeepEval 环境变量"
```

---

### Task 2: httpx 请求级观测 hooks（挂 async client）

**Files:**
- Create: `src/infra/llm/provider/http_observability.py`
- Test: `tests/infra/llm/provider/test_http_observability.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces:
  - `class HookEvidence`：`events: list[dict]`、`reset()`、`mark(trace_id)`、`append(rec)`、属性 `status_codes: list[int]`、`non_2xx: bool`、`abnormal: bool`
  - `def make_logging_client(*, timeout: httpx.Timeout, evidence: HookEvidence, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient`
  - ⚠️ **httpx 的 `AsyncClient` 没有 `max_retries` 参数**（实测 httpx 0.28.1 只有 `transport`）——SDK 侧重试由 Task 3 的 `build_chat(max_retries=…)` 单独控制，本模块不碰。
  - `transport` 是**测试注入点**（`httpx.MockTransport`），生产路径传 `None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/infra/llm/provider/test_http_observability.py
import httpx
import pytest

from src.infra.llm.provider.http_observability import HookEvidence, make_logging_client

TIMEOUT = httpx.Timeout(connect=1, read=1, write=1, pool=1)


def _client_with(evidence: HookEvidence, handler) -> httpx.AsyncClient:
    """把被测 hooks 装到一个带 MockTransport 的 client 上（避免真出网）。"""
    return make_logging_client(
        timeout=TIMEOUT, evidence=evidence, transport=httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_response_hook_reads_body_and_records_status():
    ev = HookEvidence()
    ev.mark("eval_deadbeef")
    client = _client_with(
        ev, lambda req: httpx.Response(200, json={"choices": [{"finish_reason": "stop"}]})
    )
    async with client:
        await client.post("https://example.invalid/v1/chat/completions")

    assert ev.status_codes == [200]
    assert ev.non_2xx is False
    assert ev.abnormal is False
    assert ev.events[-1]["json_ok"] is True
    assert ev.events[-1]["finish_reason"] == ["stop"]
    assert ev.events[-1]["trace_id"] == "eval_deadbeef"


@pytest.mark.asyncio
async def test_non_2xx_and_malformed_body_are_abnormal():
    ev = HookEvidence()
    for resp in (
        httpx.Response(429, json={"error": {"code": "Throttling"}}),
        httpx.Response(200, content=b"not-json"),
    ):
        client = _client_with(ev, lambda req, r=resp: r)
        async with client:
            await client.post("https://example.invalid/v1/chat/completions")

    assert ev.status_codes == [429, 200]
    assert ev.non_2xx is True
    assert ev.abnormal is True
    assert ev.events[-1]["json_ok"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/infra/llm/provider/test_http_observability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.infra.llm.provider.http_observability'`

- [ ] **Step 3: 实现**

```python
# src/infra/llm/provider/http_observability.py
"""裁判调用的请求级观测：httpx event hooks + 按 trace 归集的证据桶。

为什么必须挂 `http_async_client`：ragas 的裁判调用走**异步**（`evaluate` 提交
`metric.single_turn_ascore` → `LangchainLLMWrapper.agenerate_text` →
`await agenerate_chat_result`），而 langchain 的 `http_client` 字段**只喂同步
client**（`chat_models/base.py:806`）⇒ 挂错字段 hooks 一条都不会响（实测
A 臂 4 事件 / B 臂 0 事件，见 docs/tmp/deepeval-poc/RESULTS.md「Hook 冒烟」）。

hook 必须是 `async def`：异步 client 的调用形式是 `await hook(response)`
（`httpx/_client.py:1697`），同步 hook 会抛 TypeError；读体必须
`await response.aread()`（同步 read() 在异步流上抛 RuntimeError）。
"""

from __future__ import annotations

import json

import httpx
from loguru import logger


class HookEvidence:
    """按样本归集的观测证据（裁判 client 跨样本复用 ⇒ 用可变桶，不闭包固定 id）。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._trace_id: str | None = None

    def mark(self, trace_id: str | None) -> None:
        """切到某题的证据窗口（评分串行，并发=1，故单槽安全）。"""
        self._trace_id = trace_id

    def reset(self) -> None:
        self.events.clear()
        self._trace_id = None

    def append(self, rec: dict) -> None:
        rec["trace_id"] = self._trace_id
        self.events.append(rec)

    @property
    def status_codes(self) -> list[int]:
        return [e["status"] for e in self.events if e.get("phase") == "response"]

    @property
    def non_2xx(self) -> bool:
        return any(not (200 <= s < 300) for s in self.status_codes)

    @property
    def abnormal(self) -> bool:
        """响应侧异常：非 2xx / 响应体非合法 JSON / `finish_reason` 非 stop。

        **不覆盖传输层失败**：httpx 0.28.1 只派发 `request` / `response` 两个 hook，
        没有异常 hook；连接/池/DNS 失败会在响应 hook 循环**之前**抛出 ⇒ 表现为
        「有 request 事件、无 response 事件」（观测缺口）。靠本属性判"没事"是错的，
        调用方若要区分，须显式检查"该格是否有 response 事件"。
        """
        for e in self.events:
            if e.get("phase") != "response":
                continue
            if not (200 <= e["status"] < 300):
                return True
            if not e.get("json_ok"):
                return True
            if e.get("finish_reason") and any(fr != "stop" for fr in e["finish_reason"]):
                return True
        return False


def make_logging_client(
    *,
    timeout: httpx.Timeout,
    evidence: HookEvidence,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """构造带请求级日志的异步 client（同时承载细粒度超时）。

    Args:
        timeout: 细粒度超时（connect/read/write/pool 四段）。
        evidence: 证据桶（每次请求/响应写入；失败只记 warning，绝不阻断评测）。
        transport: 测试注入点（生产传 None，用 httpx 默认 transport）。
    """

    async def on_request(request: httpx.Request) -> None:
        evidence.append({"phase": "request", "url": str(request.url)})

    async def on_response(response: httpx.Response) -> None:
        rec: dict = {"phase": "response", "status": response.status_code}
        if not (200 <= response.status_code < 300):
            logger.warning(
                "[provider] bridge judge http non-2xx status={}", response.status_code
            )
        try:
            body = await response.aread()
            parsed = json.loads(body)
            rec["body_bytes"] = len(body)
            rec["json_ok"] = True
            choices = parsed.get("choices") or []
            rec["finish_reason"] = [c.get("finish_reason") for c in choices]
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
            rec["json_ok"] = False
            rec["body_exc"] = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[provider] bridge judge http body unreadable status={} err={}",
                response.status_code,
                rec["body_exc"],
            )
        evidence.append(rec)

    logger.debug("[provider] bridge judge http logging hooks attached")
    return httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        event_hooks={"request": [on_request], "response": [on_response]},
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/infra/llm/provider/test_http_observability.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/infra/llm/provider/http_observability.py tests/infra/llm/provider/test_http_observability.py
git commit -m "feat(provider): httpx 请求级观测 hooks（挂 async client，证据按 trace 归集）"
```

---

### Task 3: 裁判构造走 provider 层（`build_bridge_judge`）

**Files:**
- Modify: `src/infra/llm/provider/factory.py`（新增函数，**不删**旧 `build_ragas_judge`——删除属 Phase 3）
- Test: `tests/infra/llm/provider/test_bridge_judge.py`（新建）

**Interfaces:**
- Consumes: `HookEvidence` / `make_logging_client`（Task 2）
- Produces: `def build_bridge_judge(*, evidence: HookEvidence) -> ChatOpenAI`（返回 `ReasoningPreservingChat`；`http_async_client` 已置、`http_client is None`、`max_retries == 0`、**未传** `temperature`）

- [ ] **Step 1: 写失败测试**

```python
# tests/infra/llm/provider/test_bridge_judge.py
from src.infra.llm.provider.factory import build_bridge_judge
from src.infra.llm.provider.http_observability import HookEvidence


class _FakeChat:
    model_kwargs: dict = {}


def test_bridge_judge_binds_async_client_only():
    judge = build_bridge_judge(evidence=HookEvidence())

    assert judge.http_async_client is not None, "ragas 走异步：必须挂 http_async_client"
    assert judge.http_client is None, "不得只挂同步 client（hooks 与超时都会失效）"
    assert judge.max_retries == 0, "关掉 SDK 重试（防与 ragas 层重试乘法放大）"


def test_bridge_judge_does_not_pass_temperature_or_top_p(monkeypatch):
    """本函数自身不得声明采样参数（温度由 ragas 按 n 分档决定）。

    用 monkeypatch 钉住「传了什么」，不依赖 env 里 LLM_KWARGS 的取值——
    后者可能合法地注入 temperature，那是配置层的事，不属本函数的契约。
    """
    captured: dict = {}

    def fake_build_chat(role, **kwargs):
        captured["role"] = role
        captured.update(kwargs)
        return _FakeChat()

    monkeypatch.setattr("src.infra.llm.provider.factory.build_chat", fake_build_chat)

    build_bridge_judge(evidence=HookEvidence())

    assert captured["role"] == "judge"
    assert "temperature" not in captured
    assert "top_p" not in captured
    assert captured["http_async_client"] is not None
    assert "http_client" not in captured
    assert captured["thinking"] is not None  # 思考开关走 thinking=，不硬写 enable_thinking
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/infra/llm/provider/test_bridge_judge.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_bridge_judge'`

- [ ] **Step 3: 实现**

```python
# src/infra/llm/provider/factory.py —— 追加在 build_chat 之后
def build_bridge_judge(*, evidence: "HookEvidence"):
    """构造 DeepEval RAGAS 桥用的裁判（langchain `BaseChatModel`）。

    与 `build_ragas_judge()`（现代 `InstructorBaseRagasLLM`）的区别：桥要的是
    langchain chat model，故走 `build_chat`；同时把**细粒度超时**与**请求级观测**
    落在 `http_async_client` 上——ragas 的裁判调用走异步，`http_client` 只喂同步
    client（langchain `chat_models/base.py:806`）。

    Args:
        evidence: 请求级观测证据桶（挂到 hooks 上）。

    Returns:
        ChatOpenAI（`ReasoningPreservingChat`）：`http_async_client` 已置，
        `max_retries=0`，未传 `temperature` / `top_p`（温度由 ragas 分档决定）。
    """
    from src.config import settings

    timeout = httpx.Timeout(
        connect=settings.RAGAS_HTTP_TIMEOUT_CONNECT,
        read=settings.RAGAS_HTTP_TIMEOUT_READ,
        write=settings.RAGAS_HTTP_TIMEOUT_WRITE,
        pool=settings.RAGAS_HTTP_TIMEOUT_POOL,
    )
    client = make_logging_client(
        timeout=timeout,
        evidence=evidence,
    )
    return build_chat(
        "judge",
        thinking=settings.RAGAS_JUDGE_THINKING,
        max_tokens=settings.RAGAS_JUDGE_MAX_TOKENS,
        http_async_client=client,
        max_retries=settings.RAGAS_HTTP_MAX_RETRIES,
    )
```

> `thinking=` 只在值为 `None` 时不注入；本项目 `RAGAS_JUDGE_THINKING` 是 `bool`，恒非 `None`，故一定走 `thinking_extra_body` 归一化。`max_retries` 由 langchain 透传给 **OpenAI SDK**（httpx client 本身没有该参数）。

顶部补 import：`import httpx`，`from src.infra.llm.provider.http_observability import HookEvidence, make_logging_client`（`HookEvidence` 仅用于类型注解时可用 `TYPE_CHECKING`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/infra/llm/provider/test_bridge_judge.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/infra/llm/provider/factory.py tests/infra/llm/provider/test_bridge_judge.py
git commit -m "feat(provider): build_bridge_judge（provider 层 + http_async_client + 观测 hooks）"
```

---

### Task 4: 四指标构造（`build_metrics` 换成桥的经典四项）

**Files:**
- Modify: `src/cli/eval_scoring.py:23-47`（`build_metrics` 整体替换）
- Test: `tests/cli/test_eval_bridge_metrics.py`（新建）

**Interfaces:**
- Consumes: 裁判（Task 3）、`build_embeddings()`
- Produces: `def build_metrics(judge, embeddings) -> dict[str, object]`，键恒为 `("faithfulness", "answer_relevancy", "context_precision", "context_recall")`，顺序即评分顺序；`answer_relevancy` 实例带 `embeddings`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_bridge_metrics.py
from src.cli.eval_scoring import build_metrics


class _FakeJudge:
    pass


class _FakeEmbeddings:
    pass


def test_build_metrics_returns_four_classic_bridge_metrics():
    metrics = build_metrics(_FakeJudge(), _FakeEmbeddings())

    assert list(metrics) == [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]


def test_build_metrics_passes_judge_explicitly_and_never_uses_aggregate():
    judge = _FakeJudge()
    metrics = build_metrics(judge, _FakeEmbeddings())

    for name, m in metrics.items():
        assert m.model is judge, f"{name} 必须显式收裁判实例（防回落到 gpt-3.5-turbo）"
    assert type(metrics["answer_relevancy"]).__name__ == "RAGASAnswerRelevancyMetric"
    # 聚合度量与 entity recall 一律不用（源码 5 项 vs 文档 4 项不一致）
    assert not any(type(m).__name__ in {"RagasMetric", "RAGASContextualEntitiesRecall"} for m in metrics.values())


def test_build_metrics_rejects_no_embeddings_for_answer_relevancy():
    import pytest

    with pytest.raises((TypeError, AssertionError)):
        build_metrics(_FakeJudge(), None)  # answer_relevancy 是唯一需要 embedding 的
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/cli/test_eval_bridge_metrics.py -v`
Expected: FAIL（键序为现代五项、含 `answer_correctness`）

- [ ] **Step 3: 实现**

```python
# src/cli/eval_scoring.py —— 替换 build_metrics
def build_metrics(judge, embeddings) -> dict:
    """构造 DeepEval RAGAS 桥的**经典四项**（design D2）。

    只用单项、不用聚合：聚合 `RagasMetric` 在源码里是 5 项算术平均（含
    `ContextualEntitiesRecall`），与官方文档写四项不一致 ⇒ 口径不可控。

    Args:
        judge: langchain `BaseChatModel`（桥要求）
        embeddings: langchain Embeddings（`answer_relevancy` 唯一需要）

    Returns:
        指标名 -> 桥度量实例；**顺序即串行评分顺序**
    """
    from deepeval.metrics.ragas import (
        RAGASAnswerRelevancyMetric,
        RAGASContextualPrecisionMetric,
        RAGASContextualRecallMetric,
        RAGASFaithfulnessMetric,
    )

    if embeddings is None:
        raise AssertionError("answer_relevancy 需要显式 embeddings（不得为 None）")

    return {
        "faithfulness": RAGASFaithfulnessMetric(model=judge),
        "answer_relevancy": RAGASAnswerRelevancyMetric(model=judge, embeddings=embeddings),
        "context_precision": RAGASContextualPrecisionMetric(model=judge),
        "context_recall": RAGASContextualRecallMetric(model=judge),
    }
```

> 实装签名已核实：`RAGASAnswerRelevancyMetric(threshold=0.3, model=…, embeddings=None, _track=True)` ⇒ `embeddings=` 是**正式构造参数**（`site-packages/deepeval/metrics/ragas.py:334`）。四个类的 `model` 参数均接受 langchain `BaseChatModel`。

- [ ] **Step 3b: 同步调用点（`main()` 里那处仍用现代工厂，必须一起改）**

`src/cli/eval_ragas.py:846` 现为：

```python
        metrics = build_metrics(
            _llm_factory.build_ragas_judge(),
            _llm_factory.build_ragas_embeddings(),
        )
```

改为（裁判走 provider 层 + 证据桶；embeddings 复用既有 `build_embeddings()`）：

```python
        from src.infra.llm.provider.factory import build_bridge_judge, build_embeddings
        from src.infra.llm.provider.http_observability import HookEvidence

        judge_evidence = HookEvidence()          # 跨题复用；每题的 mark/reset 在 score_rows 内做
        metrics = build_metrics(build_bridge_judge(evidence=judge_evidence), build_embeddings())
```

并把该 evidence 透传下去（Task 5 的 `run_evaluation(..., evidence=judge_evidence)`）。

> `build_ragas_judge` / `build_ragas_embeddings` 的**删除**不在此任务（Phase 3 收尾），此处只是不再调用它们。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/cli/test_eval_bridge_metrics.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py tests/cli/test_eval_bridge_metrics.py
git commit -m "feat(eval): build_metrics 换成桥的经典四指标（显式 model=，拒绝聚合）"
```

---

### Task 5: 打分编排本地化 —— 去 `@experiment`/`Dataset`，改串行逐样本

**Files:**
- Modify: `src/cli/eval_ragas.py:318-336`（`_EvalResult` 保留亦可）、`:339-366`（`_score_metrics` → 桥 `measure`）、`:486-554`（`run_evaluation` 去 `@experiment`/`Dataset`/Semaphore）
- Test: `tests/cli/test_eval_bridge_scoring.py`（新建）

**Interfaces:**
- Consumes: `build_metrics`（Task 4）
- Produces:
  - `def score_rows(rows: list[dict], metrics: dict) -> list[dict]`（**同步**，串行；每行含四指标 + `error`）
  - `run_evaluation(...) -> Any` 保留签名与 `.to_pandas()` 契约（产出行序与输入同序）

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_bridge_scoring.py
import pandas as pd

from src.cli.eval_ragas import score_rows


class _RecordingMetric:
    """记录调用顺序；返回固定分值。"""

    def __init__(self, value, calls):
        self.value = value
        self.calls = calls

    def measure(self, test_case):
        self.calls.append(test_case.input)
        self.score = self.value
        return self.value


def _rows():
    return [
        {"q_index": 0, "trace_id": "t0", "question": "q0", "answer": "a0",
         "ground_truth": "g0", "contexts": ["c0"]},
        {"q_index": 1, "trace_id": "t1", "question": "q1", "answer": "a1",
         "ground_truth": "g1", "contexts": ["c1"]},
    ]


def test_score_rows_is_serial_and_keeps_question_order():
    calls: list[str] = []
    metrics = {"faithfulness": _RecordingMetric(0.5, calls)}

    out = score_rows(_rows(), metrics)

    assert calls == ["q0", "q1"], "桥复用模块级单例 ⇒ 必须串行且按题序"
    assert [r["q_index"] for r in out] == [0, 1]
    assert [r["faithfulness"] for r in out] == [0.5, 0.5]


def test_score_rows_maps_test_case_fields_for_the_bridge():
    seen: list[dict] = []

    class _CapturingMetric:
        def measure(self, test_case):
            seen.append(
                {
                    "input": test_case.input,
                    "actual_output": test_case.actual_output,
                    "expected_output": test_case.expected_output,
                    "retrieval_context": test_case.retrieval_context,
                }
            )
            self.score = 1.0
            return 1.0

    score_rows(_rows()[:1], {"context_precision": _CapturingMetric()})

    assert seen[0] == {
        "input": "q0",
        "actual_output": "a0",
        "expected_output": "g0",
        "retrieval_context": ["c0"],
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/cli/test_eval_bridge_scoring.py -v`
Expected: FAIL with `ImportError: cannot import name 'score_rows'`

- [ ] **Step 3: 实现**

```python
# src/cli/eval_ragas.py
# 1) 删除 _score_metrics（旧 ascore 映射）与 _EvalResult 的 @experiment 叙事，改为：

def _bridge_test_case(row: dict):
    """把一行评测数据包成桥要的 LLMTestCase（四字段恒全部提供）。"""
    from deepeval.test_case import LLMTestCase

    return LLMTestCase(
        input=row["question"],
        actual_output=row["answer"],
        expected_output=row["ground_truth"],
        retrieval_context=list(row["contexts"]),
    )


def score_rows(rows: list[dict], metrics: dict, *, evidence=None) -> list[dict]:
    """**串行**逐题逐指标打分（并发=1）。

    为何串行：桥复用 `ragas.metrics` 的**模块级指标实例**，`ragas.evaluate`
    会在执行期把 `metrics[i].llm` 置为该次裁判、结束时复位 `None`，且
    `LangchainLLMWrapper` 会改写底层 `chat_model.temperature` ⇒ 并发会互踩、
    表现为间歇性错分（design D7）。并发预算留给"跑图"那一侧。

    Args:
        rows: 每题一行（含 q_index/trace_id/question/answer/ground_truth/contexts）。
        metrics: `eval_scoring.build_metrics` 产物（顺序即评分顺序）。
        evidence: 可选证据桶（本题评分前 `mark(trace_id)`，用于 429 判定）。

    Returns:
        与输入同序的行副本，追加四指标值与 `error`（最终失败时非空）。
    """
    out: list[dict] = []
    for row in rows:
        if evidence is not None:
            evidence.reset()
            evidence.mark(row.get("trace_id"))
        scored: dict = {"q_index": row["q_index"], "error": ""}
        errors: list[str] = []
        test_case = _bridge_test_case(row)
        for name, metric in metrics.items():
            value, err = _score_metric_with_retry(name, metric, test_case, row, evidence)
            scored[name] = value
            if err:
                errors.append(err)
        scored["error"] = ";".join(errors)
        out.append(scored)
    return out


def run_evaluation(questions, ground_truth, answers, contexts, metrics, trace_ids, *, evidence=None) -> Any:
    """逐题打分并返回带 `.to_pandas()` 的结果（行序与输入一致）。"""
    rows = [
        {"q_index": i, "trace_id": t, "question": q, "answer": a,
         "ground_truth": g, "contexts": c}
        for i, (q, a, g, c, t) in enumerate(
            zip(questions, answers, ground_truth, contexts, trace_ids, strict=True)
        )
    ]
    core_logging.log_event(Event.EVALUATION_START, samples=len(rows))
    scored = score_rows(rows, metrics, evidence=evidence)
    core_logging.log_event(Event.EVALUATION_DONE)
    df = pd.DataFrame(scored).sort_values("q_index").reset_index(drop=True)
    return _EvalResult(df)
```

同时**删掉** `from ragas import Dataset, experiment`、`sem = asyncio.Semaphore(...)` 与 `asyncio.run(_score_row.arun(...))`；`RAGAS_EVAL_CONCURRENCY` 的 import 暂留（Phase 3 处置），但**不再使用**。

> **本任务只做「同步化 + 入参形态迁移」，判据仍是 `except Exception`。** 因此 `_score_metric_with_retry` 的签名要在本任务里改成同步 + 收 `test_case`：
>
> ```python
> def _score_metric_with_retry(metric_name: str, metric, test_case, row: dict, evidence=None):
>     """（本阶段临时形态）失败重试；判据在 Task 6 换成非掩码 NaN。"""
>     attempts = max(1, settings.RAGAS_METRIC_MAX_ATTEMPTS)
>     last_exc: Exception = RuntimeError("unreachable")
>     for attempt in range(1, attempts + 1):
>         started = time.monotonic()
>         try:
>             value = metric.measure(test_case)
>         except Exception as exc:  # noqa: BLE001  # 失败可见，不吞（Task 6 改判据）
>             last_exc = exc
>             core_logging.log_event(
>                 Event.JUDGE_CALL, metric=metric_name, q_index=row.get("q_index"),
>                 trace_id=row.get("trace_id"), attempt=attempt,
>                 seconds=round(time.monotonic() - started, 1), ok=False,
>                 note=_judge_failure_note(exc),
>             )
>             if attempt >= attempts:
>                 break
>             core_logging.log_event(
>                 Event.METRIC_RETRY, metric=metric_name, q_index=row.get("q_index"),
>                 trace_id=row.get("trace_id"), reason=_exc_chain_summary(exc, 120),
>             )
>             time.sleep(0.5 * attempt)
>             continue
>         core_logging.log_event(
>             Event.JUDGE_CALL, metric=metric_name, q_index=row.get("q_index"),
>             trace_id=row.get("trace_id"), attempt=attempt,
>             seconds=round(time.monotonic() - started, 1), ok=True, note="",
>         )
>         return float(value), ""
>     return float("nan"), f"{metric_name}:{_exc_chain_summary(last_exc)}"
> ```
>
> 注意 `asyncio.sleep` → `time.sleep`（本层已同步）。`_bridge_test_case` / `score_rows` 里的 `metric.measure(test_case)` 是**同步**调用（桥的 `measure` 是同步 API）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/cli/test_eval_bridge_scoring.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_eval_bridge_scoring.py
git commit -m "feat(eval): 打分编排本地化——去 @experiment/Dataset，改串行逐样本 measure()"
```

---

### Task 6: 失败语义 —— 重试判据换成「非掩码 NaN」，429 例外

**Files:**
- Modify: `src/cli/eval_ragas.py:390-420`（删 `_judge_failure_note`）、`:423-483`（`_score_metric_with_retry` 重写）
- Test: `tests/cli/test_eval_bridge_retry.py`（新建）

**Interfaces:**
- Consumes: `HookEvidence`（Task 2）
- Produces:
  - `def _score_metric_with_retry(metric_name, metric, test_case, row, evidence=None) -> tuple[float, str]`
  - 语义：返回 `(nan, "")` 表示**非掩码 NaN**（由调用方计入 NaN 率）；返回 `(nan, "指标:原因")` 表示重试耗尽；`(value, "")` 为成功

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_bridge_retry.py
import math

import pytest

from src.cli.eval_ragas import _score_metric_with_retry
from src.infra.llm.provider.http_observability import HookEvidence


class _Seq:
    """按序列返回分值；None 表示返回 NaN。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def measure(self, test_case):
        self.calls += 1
        v = self.seq.pop(0)
        self.score = math.nan if v is None else v
        return self.score


ROW = {"q_index": 0, "trace_id": "t0"}


def test_nan_triggers_bounded_retry_and_can_recover():
    m = _Seq([None, 0.8])
    value, err = _score_metric_with_retry("faithfulness", m, object(), ROW, HookEvidence())

    assert m.calls == 2, "首次非掩码 NaN 必须触发重试"
    assert value == pytest.approx(0.8) and err == ""


def test_success_does_not_retry():
    m = _Seq([0.9])
    value, err = _score_metric_with_retry("faithfulness", m, object(), ROW, HookEvidence())

    assert m.calls == 1 and value == pytest.approx(0.9) and err == ""


def test_exhausted_retries_report_error_and_return_nan():
    m = _Seq([None, None, None])
    value, err = _score_metric_with_retry("faithfulness", m, object(), ROW, HookEvidence())

    assert math.isnan(value)
    assert err.startswith("faithfulness:")
    assert m.calls == 3, "有界：RAGAS_METRIC_MAX_ATTEMPTS=3"


def test_rate_limit_evidence_skips_outer_retry():
    ev = HookEvidence()
    ev.mark("t0")
    ev.append({"phase": "response", "status": 429, "json_ok": True, "finish_reason": []})
    m = _Seq([None, 0.8])

    value, err = _score_metric_with_retry("faithfulness", m, object(), ROW, ev)

    assert m.calls == 1, "429 已由 ragas 层重试 ≤10 次，外层不得再叠（防 10×3 放大）"
    assert math.isnan(value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/cli/test_eval_bridge_retry.py -v`
Expected: FAIL（现实现捕异常、且签名无 `evidence`）

- [ ] **Step 3: 实现**

```python
# src/cli/eval_ragas.py —— 替换 _score_metric_with_retry；删除 _judge_failure_note
def _score_metric_with_retry(metric_name, metric, test_case, row, evidence=None) -> tuple[float, str]:
    """打分；**非掩码 NaN** 时重试至多 `settings.RAGAS_METRIC_MAX_ATTEMPTS` 次。

    为何判据是 NaN 而不是异常：桥调 `ragas.evaluate(...)` 时未传
    `raise_exceptions`（默认 False），`ragas/executor.py` 对**任何** Exception 都
    `return np.nan` ⇒ 桥不抛异常，旧的 `except Exception` 判据永不触发（design D4）。

    为何 429 不重试：ragas 框架层已对 `openai.RateLimitError`（HTTP 429）重试
    ≤10 次（`ragas/run_config.py:52` + `llms/base.py:325-335`），外层再叠会 10×3。

    为何不做"合法缺值 / 裁判失败"子分类：桥只回一个 float，HTTP 200 的 envelope
    恒为合法 JSON ⇒ 该分类不可实现（design D4，第 5 轮评审证实）。

    Returns:
        `(值, 错误说明)`；成功与"未被重试的 NaN"错误说明为 ""；
        重试耗尽时为 `"指标:原因"`。
    """
    attempts = max(1, settings.RAGAS_METRIC_MAX_ATTEMPTS)
    last_note = ""
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            value = metric.measure(test_case)
            note = ""
        except Exception as exc:  # noqa: BLE001  # 异常仅作留证，判据是 NaN
            value, note = float("nan"), _exc_chain_summary(exc)
        seconds = round(time.monotonic() - started, 1)

        is_nan = value is None or (isinstance(value, float) and math.isnan(value))
        core_logging.log_event(
            Event.JUDGE_CALL, metric=metric_name, q_index=row.get("q_index"),
            trace_id=row.get("trace_id"), attempt=attempt, seconds=seconds,
            ok=not is_nan, note=note,
        )
        if not is_nan:
            return float(value), ""
        last_note = note or "nan"

        if evidence is not None and evidence.non_2xx and 429 in evidence.status_codes:
            return float("nan"), ""      # 429：外层不重试（ragas 层已重试）
        if attempt >= attempts:
            break
        core_logging.log_event(
            Event.METRIC_RETRY, metric=metric_name, q_index=row.get("q_index"),
            trace_id=row.get("trace_id"), reason=last_note,
        )
        time.sleep(0.5 * attempt)        # 原为 await asyncio.sleep；改同步后同义
    return float("nan"), f"{metric_name}:{last_note}"
```

顶部补 `import math`；删除 `_judge_failure_note` 整个函数（失败证据改由 hooks 承载）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/cli/test_eval_bridge_retry.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_eval_bridge_retry.py
git commit -m "feat(eval): 重试判据改非掩码 NaN（429 例外）；删 _judge_failure_note（证据移交 hooks）"
```

---

### Task 7: NaN 计数与主分口径（纯 KB 且非 NaN）

**Files:**
- Modify: `src/cli/eval_scoring.py`（新增 `nan_cell_stats`；`main_score_means` 加"排除 NaN"的显式注释与断言）
- Modify: `src/cli/eval_ragas.py`（报告/stdout 增 NaN 计数行）
- Test: `tests/cli/test_eval_bridge_nan.py`（新建）

**Interfaces:**
- Produces: `def nan_cell_stats(df: pd.DataFrame) -> dict[str, float|int]` → `{"nan_cells": int, "denominator_cells": int, "nan_rate": float}`（分母 = 纯 KB 行数 × 四指标）

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_bridge_nan.py
import math

import pandas as pd

from src.cli.eval_scoring import nan_cell_stats
from src.infra.llm.provider.http_observability import HookEvidence  # noqa: F401（同批断言用具名导入）


def _df():
    return pd.DataFrame(
        {
            "sample_class": ["pure_kb", "pure_kb", "empty", "web"],
            "faithfulness": [0.9, math.nan, math.nan, 0.5],
            "answer_relevancy": [0.8, 0.7, 0.6, 0.5],
            "context_precision": [0.9, 0.9, math.nan, 0.5],
            "context_recall": [0.9, 0.9, math.nan, 0.5],
        }
    )


def test_nan_rate_denominator_is_pure_kb_times_four_metrics():
    stats = nan_cell_stats(_df())

    assert stats["denominator_cells"] == 2 * 4, "分母 = 纯 KB 样本 × 四指标"
    assert stats["nan_cells"] == 1, "只数纯 KB 格里的 NaN（empty 行的掩码 NaN 天然在分母外）"
    assert stats["nan_rate"] == 0.125


def test_all_metrics_nan_gives_rate_one_without_crash():
    df = pd.DataFrame(
        {
            "sample_class": ["pure_kb"],
            "faithfulness": [math.nan],
            "answer_relevancy": [math.nan],
            "context_precision": [math.nan],
            "context_recall": [math.nan],
        }
    )
    assert nan_cell_stats(df)["nan_rate"] == 1.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/cli/test_eval_bridge_nan.py -v`
Expected: FAIL with `ImportError: cannot import name 'nan_cell_stats'`

- [ ] **Step 3: 实现**

```python
# src/cli/eval_scoring.py —— 追加
def nan_cell_stats(df: pd.DataFrame) -> dict:
    """NaN 率（单位是**格**，不是题）：分母 = 纯 KB 样本 × 四指标。

    分子 = 纯 KB 格里的**全部** NaN（合法缺值与裁判失败不可区分，一并计入）。
    掩码产生的 NaN 只出现在 `empty` 样本 ⇒ 天然落在分母之外，无需排除。

    Returns:
        {"nan_cells": int, "denominator_cells": int, "nan_rate": float}
    """
    pure = df[df["sample_class"] == SAMPLE_CLASS_PURE_KB]
    cols = [c for c in RAGAS_METRIC_COLUMNS if c in df.columns]
    denom = len(pure) * len(cols)
    nan_cells = int(pure[cols].isna().to_numpy().sum()) if denom else 0
    return {
        "nan_cells": nan_cells,
        "denominator_cells": denom,
        "nan_rate": (nan_cells / denom) if denom else 0.0,
    }
```

`main_score_means` 的 docstring 追加一句：均值取"纯 KB 且非 NaN"（`Series.mean()` 默认 `skipna=True`，即本口径；**不得**改为 `skipna=False`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/cli/test_eval_bridge_nan.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py tests/cli/test_eval_bridge_nan.py
git commit -m "feat(eval): NaN 率口径（分母=纯KB×四指标、分子=纯KB格全部NaN）"
```

---

### Task 8: 门禁加 NaN 率判据 + 「建议启用逃生门」提示

**Files:**
- Modify: `src/config/const.py:515` 附近（新增 `EVAL_NAN_RATE_MAX: float = 0.10`）
- Modify: `src/cli/eval_ragas.py:717-762`（`check_gate` 加判据）
- Test: `tests/cli/test_eval_bridge_gate.py`（新建）

**Interfaces:**
- Consumes: `nan_cell_stats`（Task 7）
- Produces: `check_gate` 行为扩展 —— NaN 率 `> EVAL_NAN_RATE_MAX` ⇒ 打印「建议启用逃生门」并 `sys.exit(1)`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_bridge_gate.py
import math

import pandas as pd
import pytest

from src.cli.eval_ragas import check_gate


def _df(n_pure: int, n_nan: int, value: float = 0.99) -> pd.DataFrame:
    """n_pure 个纯 KB 样本；前 n_nan 个的 faithfulness 为 NaN（分母 = n_pure × 4）。"""
    return pd.DataFrame(
        [
            {
                "sample_class": "pure_kb",
                "faithfulness": math.nan if i < n_nan else value,
                "answer_relevancy": value,
                "context_precision": value,
                "context_recall": value,
            }
            for i in range(n_pure)
        ]
    )


def test_nan_rate_at_or_below_threshold_does_not_fail(capsys):
    df = _df(n_pure=20, n_nan=7)  # 7/80 = 8.75% ≤ 10%

    with pytest.raises(SystemExit) as e:
        check_gate(df, questions=["q"] * 20)

    assert e.value.code == 0, "NaN 率未超阈值时不得因 NaN 判失败"
    assert "NaN" in capsys.readouterr().out


def test_nan_rate_above_threshold_fails_and_hints_escape_hatch(capsys):
    df = _df(n_pure=20, n_nan=9)  # 9/80 = 11.25% > 10%

    with pytest.raises(SystemExit) as e:
        check_gate(df, questions=["q"] * 20)

    assert e.value.code == 1
    assert "建议启用逃生门" in capsys.readouterr().out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/cli/test_eval_bridge_gate.py -v`
Expected: FAIL（`check_gate` 目前不看 NaN；且 `GATE_THRESHOLDS_KEYS_FOR_TESTS` 不存在）

- [ ] **Step 3: 实现**

```python
# src/config/const.py —— 紧随 RAGAS_EVAL_CONCURRENCY 之后
# NaN 率门禁：分母 = 纯 KB 样本 × 四指标（格数）；分子 = 纯 KB 格里的全部 NaN。
# 10% 为**经验初值**（依据仅换尺子前 21 题 1 例 ≈4.8%，样本薄），首轮全量后复核定稿。
EVAL_NAN_RATE_MAX: float = 0.10
```

> `GATE_THRESHOLDS` 保持在 `eval_ragas.py:61` 不动（搬进 const 超本阶段范围）。

```python
# src/cli/eval_ragas.py —— check_gate 内，紧跟覆盖率检查之后插入
    stats = nan_cell_stats(df)
    rate = stats["nan_rate"]
    from src.config.const import EVAL_NAN_RATE_MAX

    print(
        f"  NaN: {stats['nan_cells']}/{stats['denominator_cells']} 格 "
        f"({rate:.2%}) 阈值 {EVAL_NAN_RATE_MAX:.0%}"
    )
    if rate > EVAL_NAN_RATE_MAX:
        print("  gate FAIL: NaN 率超阈值 —— 建议启用逃生门（见 design D10）")
        sys.exit(1)
```

顶部 import 补 `nan_cell_stats`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/cli/test_eval_bridge_gate.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/config/const.py src/cli/eval_ragas.py tests/cli/test_eval_bridge_gate.py
git commit -m "feat(eval): 门禁加 NaN 率判据（经验初值 10%）+ 逃生门提示"
```

---

### Task 9: `answer_correctness` 退出（常量 / API / 报告 / 契约文档 / 台账）

**Files:**
- Modify: `src/config/const.py:496`（注释）、`:504-510`（`RAGAS_METRIC_COLUMNS`）
- Modify: `src/api/kb_eval.py:24-52`（删 `_mean_answer_correctness`）、`:89`（删字段）
- Modify: `src/cli/eval_ragas.py:958-975`（均值行）、`:1001-1046`（`_build_eval_detail`）
- Modify: `docs/agents/api_contract.md:733 / :744 / :909 / :912 / :1353 / :1379 / :1400`
- Modify: `docs/agents/ragas-eval-ledger.md:26`（表头加「口径版本」列，**保留** `answer_correctness` 列）
- Test: `tests/api/test_kb_eval_payload.py`（既有用例改造）+ `tests/cli/test_eval_bridge_metrics.py`（追加断言）

**Interfaces:**
- Produces: `RAGAS_METRIC_COLUMNS == ("faithfulness", "answer_relevancy", "context_precision", "context_recall")`；`kb_eval` payload **无** `answer_correctness` 键

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_bridge_metrics.py —— 追加
def test_metric_columns_drop_answer_correctness():
    from src.config.const import CONTEXT_METRIC_COLUMNS, RAGAS_METRIC_COLUMNS

    assert RAGAS_METRIC_COLUMNS == (
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    )
    assert CONTEXT_METRIC_COLUMNS == ("faithfulness", "context_precision", "context_recall")


def test_kb_eval_payload_has_no_answer_correctness():
    import json

    from src.api.kb_eval import _build_eval_payload

    report = {
        "faithfulness": 0.8, "answer_relevancy": 0.8, "context_precision": 0.8,
        "context_recall": 0.8, "overall_score": 0.8, "passed": True,
        "pass_threshold": 0.7, "detail_json": json.dumps([{"q_index": 0}]),
    }
    assert "answer_correctness" not in _build_eval_payload(report)
```

> 实装函数名已核实为 `_build_eval_payload`（`src/api/kb_eval.py:64`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/cli/test_eval_bridge_metrics.py -v`
Expected: FAIL（`RAGAS_METRIC_COLUMNS` 仍含 `answer_correctness`）

- [ ] **Step 3: 实现（逐处）**

1. `const.py`：`RAGAS_METRIC_COLUMNS` 删最后一项；`:496` 注释改为 `# 依赖 contexts 的指标列（无材料样本上跳过；answer_relevancy 不在此列）`。
2. `kb_eval.py`：删 `_mean_answer_correctness` 函数与 payload 里的 `"answer_correctness": …` 行。
3. `eval_ragas.py`：均值行改为遍历 `RAGAS_METRIC_COLUMNS`（已如此则无需改）；`_build_eval_detail` 不再写 `answer_correctness` 键。
4. `api_contract.md`：7 处逐处删除/改写 `answer_correctness`（含 `:909` 的「五项均值」→「四项均值」）。
5. `ragas-eval-ledger.md` 表头**新增**「口径版本」列，`answer_correctness` 列**保留**（历史行不动，新行填 `N/A`）——**不得删列**（会让表头列数与历史行不等、表格破损）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/cli/test_eval_bridge_metrics.py tests/api/test_kb_eval_payload.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/config/const.py src/api/kb_eval.py src/cli/eval_ragas.py docs/agents/api_contract.md docs/agents/ragas-eval-ledger.md tests/
git commit -m "feat(eval): answer_correctness 退出（常量/API/报告/契约文档/台账表头）"
```

---

### Task 10: 报告元数据（温度档、版本、NaN 计数、口径版本）

**Files:**
- Modify: `src/cli/eval_ragas.py`（新增 `build_report_meta(...)`；报告头与台账条目调用）
- Test: `tests/cli/test_eval_bridge_meta.py`（新建）

**Interfaces:**
- Produces: `def build_report_meta(*, judge_model: str, nan_stats: dict, runner_versions: dict) -> dict`（键固定，见测试）

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_bridge_meta.py
import json

from src.cli.eval_ragas import build_report_meta


def test_report_meta_records_temperature_regime_but_never_zero():
    meta = build_report_meta(
        judge_model="qwen3.8-flash",
        nan_stats={"nan_cells": 1, "denominator_cells": 76, "nan_rate": 1 / 76},
        runner_versions={"ragas": "0.3.9", "deepeval": "4.2.9"},
    )

    assert meta["caliber"] == "bridge-0.3.9"
    assert meta["temperature_regime"] == {"n1_metrics": 0.01, "answer_relevancy": 0.3}
    assert meta["model_arg_explicit"] is True
    assert meta["judge_model"] == "qwen3.8-flash"
    assert "temperature=0" not in json.dumps(meta, ensure_ascii=False)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/cli/test_eval_bridge_meta.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_report_meta'`

- [ ] **Step 3: 实现**

```python
# src/cli/eval_ragas.py —— 追加（并在报告头/落库前调用）
def build_report_meta(*, judge_model: str, nan_stats: dict, runner_versions: dict) -> dict:
    """报告/台账元数据（口径版本、温度档、NaN 计数、模型显式性）。

    温度**不由本项目控制**（ragas 按 n 分档：n=1 → 0.01；answer_relevancy
    n=3 → 0.3），故只记录事实，SHALL NOT 出现「temperature=0」的表述。
    """
    return {
        "caliber": "bridge-0.3.9",
        "judge_model": judge_model,
        "model_arg_explicit": True,
        "temperature_regime": {"n1_metrics": 0.01, "answer_relevancy": 0.3},
        "temperature_control": "ragas_decides_by_n",
        "nan_cells": nan_stats["nan_cells"],
        "nan_denominator_cells": nan_stats["denominator_cells"],
        "nan_rate": round(nan_stats["nan_rate"], 4),
        "runner_versions": dict(runner_versions),
    }
```

`runner_versions` 取 `importlib.metadata.version("ragas")` / `("deepeval")`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/cli/test_eval_bridge_meta.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_eval_bridge_meta.py
git commit -m "feat(eval): 报告元数据（口径版本/温度档/NaN 计数/版本）"
```

---

### Task 11: 现代栈测试的下线与替换（把阶段末的树刷绿）

**Files:**
- Delete: `tests/cli/test_eval_modern_stack.py`、`tests/infra/llm/provider/test_ragas_modern_factories.py`
- Modify: `tests/cli/test_eval_ragas.py`（若其断言依赖 `run_evaluation` 的 `@experiment` 形态则同步）
- Test: 上述删除后的整批

> **计划修订（T6 修一轮时已提前收口）**：`tests/cli/test_metric_retry.py` **不再属于本任务** —— T6 删除 `_judge_failure_note` 时按 spec「删除动作 SHALL 与受影响测试同一提交」一并清理了该文件（删 2 个引用已删 helper 的死测试、把其余用例改写成同步 NaN 判据）。

**Interfaces:**
- Consumes: Task 5 的 `score_rows` / `run_evaluation` 新签名
- Produces: 无新增接口；`tests/` 不再引用被删的现代栈符号

- [ ] **Step 1: 先跑一遍，记录当前失败清单**

Run: `python -m pytest tests/cli tests/infra/llm/provider -q 2>&1 | tail -30`
Expected: 出现引用 `ragas.metrics.collections` / `@experiment` / `build_ragas_judge` 的失败与收集错误（这正是本任务要清的）

- [ ] **Step 2: 删除/改写**

```bash
git rm tests/cli/test_eval_modern_stack.py tests/cli/test_metric_retry.py tests/infra/llm/provider/test_ragas_modern_factories.py
```

对 `tests/cli/test_eval_ragas.py`：把断言对象从"`Experiment` 行序/`arun` 完成序"改为"`run_evaluation` 返回 `.to_pandas()` 与输入同序"。

- [ ] **Step 3: 跑整批确认绿**

Run: `python -m pytest tests/cli tests/infra/llm/provider -q`
Expected: **全绿**（本阶段新增的 5 个测试文件 + 既有用例）

- [ ] **Step 4: 提交**

```bash
git add -A tests/
git commit -m "test(eval): 下线现代栈用例，改钉桥的串行/NaN/门禁形态（阶段末刷绿）"
```

---

### Task 12: 阶段收口（自查 + 全量闸门）

**Files:**
- Modify: `docs/openspec/changes/deepeval-bridge-eval/tasks.md`（回写本阶段两处差异：9.6 前移、1.3 移到阶段末；并把 1.1–4.7 已完成的勾上）
- Test: 静态闸门 + 评测路径符号自查

**Interfaces:**
- Produces: 阶段级验收证据（命令与输出）

- [ ] **Step 1: 评测路径不得再引用现代栈符号**

Run:
```bash
grep -n "metrics.collections\|llm_factory\|embedding_factory\|@experiment\|from ragas import Dataset" src/cli/eval_ragas.py src/cli/eval_scoring.py || echo "OK: 评测路径无现代栈符号"
```
Expected: `OK: 评测路径无现代栈符号`

- [ ] **Step 2: 静态闸门**

Run:
```bash
ruff check src/cli src/infra/llm/provider src/api
ruff format --check src/cli src/infra/llm/provider src/api
pyright src/cli/eval_ragas.py src/cli/eval_scoring.py src/infra/llm/provider/factory.py
openspec validate deepeval-bridge-eval --strict
python -m src.cli.check_docs
```
Expected: 无新增 error（`pyright` 不新增 error；`check_docs` 0 error）

- [ ] **Step 3: 单测全绿**

Run: `python -m pytest tests/cli tests/config tests/infra/llm/provider tests/api -q`
Expected: 全绿（NOTE：worktree 缺 gitignored `data/`，若出现 `FileNotFoundError: data/test_docs/...` 属**环境性**，从主仓软链 `data` 后重跑）

- [ ] **Step 4: 冒烟一次真实链路（1 题，仅打分不打分行为轴）**

Run:
```bash
DEEPEVAL_DISABLE_DOTENV=1 DEEPEVAL_TELEMETRY_OPT_OUT=1 \
  python -c "
from src.config.settings import RAGAS_JUDGE_THINKING  # 触发 settings 侧 env 兜底
from src.infra.llm.provider.factory import build_bridge_judge
from src.infra.llm.provider.http_observability import HookEvidence
from src.infra.llm.provider.factory import build_embeddings
from src.cli.eval_scoring import build_metrics
from src.cli.eval_ragas import score_rows

ev = HookEvidence(); ev.mark('eval_smoke')
rows = [{'q_index': 0, 'trace_id': 'eval_smoke', 'question': '腾讯 2024 营业收入？',
         'answer': '约 6603 亿元', 'ground_truth': '6603 亿元',
         'contexts': ['腾讯控股 2024 年报：营业收入 6603 亿元。']}]
out = score_rows(rows, build_metrics(build_bridge_judge(evidence=ev), build_embeddings()), evidence=ev)
print({k: v for k, v in out[0].items() if k != 'error'})
print('hook_events=', len(ev.events), 'abnormal=', ev.abnormal)
"
```
Expected: 打印四指标数值（可能含 `nan`）；`hook_events` **> 0**（证明 hooks 挂在异步路径上生效）；`abnormal=False`。**这条是 tasks 3.3a 的 A 向断言**（B 向 `==0` 已由 Task 2 单测覆盖）。

- [ ] **Step 5: 把阶段结果写回 tasks.md 并提交**

```bash
git add docs/openspec/changes/deepeval-bridge-eval/tasks.md
git commit -m "docs(openspec): deepeval-bridge-eval Phase 1 收口（勾选 1.1–4.7 + 记录两处分组调整）"
```

---

## Phase 1 完成判据

1. `ragas==0.3.9` / `deepeval==4.2.9` 装成功且 `pip check` 无**新增**冲突；
2. 四指标由**桥**产出，`RAGAS_METRIC_COLUMNS` 与 `kb_eval` payload 均无 `answer_correctness`；
3. 评分**串行**、按题序对齐；非掩码 NaN 有界重试、429 不重试；
4. 门禁含 NaN 率（10%，格口径）且超限给逃生门提示；
5. 报告元数据含口径版本/温度档/NaN 计数，且**不含**「temperature=0」；
6. 真实链路 1 题冒烟：hooks 事件数 **> 0**；四个指标有值或为已解释的 NaN；
7. `pytest tests/cli tests/config tests/infra/llm/provider tests/api` 全绿；`openspec validate --strict` 过。

## 未纳入本阶段（Phase 2–4 才做，勿提前实现）

- Agent 行为轴（`evals_iterator` + `CallbackHandler` + `test_run_*.json` 逐题回读）——**Phase 2**；
- `--generate` 硬护栏与出题路标注、逃生门可替换边界、Langfuse 回写口径（score 后缀 / NaN 不写）、`build_ragas_judge` / `build_ragas_embeddings` / `build_native_client` 的**删除**、`RAGAS_EVAL_CONCURRENCY` 处置、新 ADR——**Phase 3**；
- 活体全量 21 题、阈值重标定、NaN 阈值复核、台账新增 `bridge-0.3.9 / v5` 行——**Phase 4**。
