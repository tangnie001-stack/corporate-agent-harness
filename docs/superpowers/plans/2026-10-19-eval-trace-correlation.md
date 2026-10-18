# Eval Trace Correlation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 DeepEval 评测加上 run / 题 / judge measure / judge HTTP call 四级 trace，使每次裁判调用都能按 ID 追溯，且不改变任何评分。

**Architecture:** `run_trace_id` 由 CLI 启动时生成并写入 `EVALUATION_RUN`、题级事件与 Langfuse metadata；`HookEvidence` 在裁判 HTTP client 的 request/response hook 上按 measure 分配 `judge_call_id`、解析 usage/status，并产出 `JUDGE_HTTP`；`_score_metric_with_retry` 产出 measure 级 `JUDGE_CALL` 并把完整 measure 记录放进 `HookEvidence.measures`，供后续 evidence change 消费。行为轴从 `test_run` 的 `order` 归一化题号，保证行为分、trajectory、trace_id 三者同序。

**Tech Stack:** Python 3.11+ / httpx 0.28 / loguru / DeepEval 4.2.9 / pytest / pytest-asyncio

**Spec:**
- `docs/openspec/changes/eval-trace-correlation/proposal.md`
- `docs/openspec/changes/eval-trace-correlation/design.md`
- `docs/openspec/changes/eval-trace-correlation/specs/eval-trace/spec.md`
- `docs/openspec/changes/eval-trace-correlation/specs/evaluation-pipeline/spec.md`
- `docs/openspec/changes/eval-trace-correlation/tasks.md`

## Global Constraints

- 工作区：`/root/code/corporate_rag-eval-trace`（分支 `feat/eval-trace-correlation`）；所有 Bash 命令以 `cd /root/code/corporate_rag-eval-trace && ...` 开头。
- `.env` / `.venv` / `data` 是指向主工作区的 symlink；**禁止 `git add -A`**，只 add 本任务列出的文件。
- 宿主侧测试统一前置 `POSTGRES_HOST=localhost`：`POSTGRES_HOST=localhost .venv/bin/python -m pytest ...`。
- 不改变任何评分、主分、门禁、CSV 列、`eval_report` schema。
- 不记录 prompt / response 原文；`reason` 只记 `reason_len` / `reason_hash`，完整值只进 `HookEvidence.measures`。
- 任何 trace/日志逻辑失败只 `logger.warning`，不得向 httpx 或评分路径抛异常。
- `JUDGE_CALL` 的 `note` 更名为 `error`、`seconds` 更名为 `latency_ms`，不保留别名。
- 行为轴 `order` 必须是 `[0, n)` 内唯一整数；不合法直接 `RuntimeError`，不按列表位置兜底。
- 提交前若 pre-commit 修改文件，重新 `git add` 再提交。

---

## File Structure

| 文件 | 责任 |
|---|---|
| `src/infra/llm/trace_context.py` | `EvalTraceContext`（run_id / run_trace_id / judge_model）与 `current_eval_trace` ContextVar |
| `src/infra/llm/provider/http_observability.py` | `HookEvidence` 的 run/measure 生命周期、request/response hook、`JUDGE_HTTP`、异常隔离 |
| `src/cli/eval_deepeval.py` | run trace 启动、score_rows trace scope、`JUDGE_CALL`、报告头、Langfuse metadata |
| `src/cli/eval_scoring.py` | `read_behaviour_scores` 的 order 校验/归一化、`link_behavior_traces` |
| `src/core/log_events.py` / `src/core/log_event_specs.py` | `JUDGE_HTTP` 事件与所有新增字段登记 |
| `tests/infra/llm/provider/test_http_observability.py` | 用 `httpx.MockTransport` 测真实 hook（200/429/ConnectError/非 JSON/无 active measure/异常隔离） |
| `tests/cli/test_eval_trace_run.py` | run trace 格式、`EVALUATION_RUN`、报告头 |
| `tests/cli/test_eval_trace_judge_call.py` | `JUDGE_CALL` 汇总、statuses、reason 不复用、evidence=None |
| `tests/cli/test_behaviour_scores_read.py` | order 校验/归一化/fail loud |
| `tests/cli/test_behaviour_trace_link.py` | `link_behavior_traces` 映射 |
| `tests/cli/test_eval_native_retry.py` | 重试测试适配新的 mesure 事件窗口 |
| `tests/cli/test_deepeval_only.py` | fake HookEvidence 增加 `set_run` |

---

### Task 1: Run trace、EvalTraceContext 与报告锚点

**Files:**
- Modify: `src/infra/llm/trace_context.py`
- Modify: `src/cli/eval_deepeval.py:1442-1510, 1650-1700, 1396-1440, 1212-1290`
- Modify: `src/core/log_events.py:136,145-146`
- Modify: `src/core/log_event_specs.py:234-252`
- Test: `tests/cli/test_eval_trace_run.py`（新建）

**Interfaces:**
- Produces: `EvalTraceContext(run_id: str, run_trace_id: str, judge_model: str | None)`；`current_eval_trace: ContextVar[EvalTraceContext | None]`；`begin_eval_run(kb_id: str) -> tuple[str, str]`；`build_report_meta(..., run_trace_id: str | None = None)`。

- [ ] **Step 1: 写失败测试**

新建 `tests/cli/test_eval_trace_run.py`：

```python
"""run trace：格式、启动事件、报告头、meta。"""

from __future__ import annotations

import re

from loguru import logger

from src.cli.eval_deepeval import begin_eval_run, build_report_meta, save_markdown_report
from src.infra.llm.trace_context import EvalTraceContext, current_eval_trace


def test_begin_eval_run_emits_event_and_returns_ids():
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        run_id, run_trace_id = begin_eval_run("kb-1")
    finally:
        logger.remove(sink_id)

    assert re.fullmatch(r"\d{8}_\d{6}", run_id)
    assert re.fullmatch(rf"eval_run_{run_id}_[0-9a-f]{{8}}", run_trace_id)
    assert any("evaluation run" in m and f"run_trace_id={run_trace_id}" in m for m in messages)


def test_eval_trace_context_roundtrip():
    ctx = EvalTraceContext(run_id="20261019_000000", run_trace_id="eval_run_x_abcd1234", judge_model="judge")
    token = current_eval_trace.set(ctx)
    try:
        assert current_eval_trace.get() is ctx
    finally:
        current_eval_trace.reset(token)


def test_report_meta_carries_run_trace_in_both_branches():
    normal = build_report_meta(
        judge_model="qwen",
        nan_stats={"nan_cells": 0, "denominator_cells": 4, "nan_rate": 0.0},
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        run_trace_id="eval_run_x_abcd1234",
    )
    skipped = build_report_meta(
        judge_model="qwen",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
        run_trace_id="eval_run_x_abcd1234",
    )
    assert normal["run_trace_id"] == "eval_run_x_abcd1234"
    assert skipped["run_trace_id"] == "eval_run_x_abcd1234"


def test_markdown_report_renders_run_trace(tmp_path):
    meta = build_report_meta(
        judge_model="qwen",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
        run_trace_id="eval_run_x_abcd1234",
    )
    save_markdown_report(
        None,
        questions=["Q0"],
        output_path=str(tmp_path / "r.csv"),
        sample_classes=["pure_kb"],
        report_meta=meta,
    )
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "**Run trace:** eval_run_x_abcd1234" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_trace_run.py -v`

Expected: `ImportError: cannot import name 'begin_eval_run'` / `EvalTraceContext`.

- [ ] **Step 3: 实现 EvalTraceContext**

在 `src/infra/llm/trace_context.py` 顶部加 `from dataclasses import dataclass`，并在四个 ContextVar 后追加：

```python
@dataclass(frozen=True)
class EvalTraceContext:
    """评测 run 级上下文。

    Args:
        run_id: 时间戳 run id（与报告/CSV 同源）
        run_trace_id: 整轮 trace id（`eval_run_<timestamp>_<8hex>`）
        judge_model: 裁判模型名；构造 bridge judge 后才有值
    """

    run_id: str
    run_trace_id: str
    judge_model: str | None


current_eval_trace: ContextVar[EvalTraceContext | None] = ContextVar(
    "current_eval_trace", default=None
)
```

- [ ] **Step 4: 实现 begin_eval_run 并接入 main**

在 `src/cli/eval_deepeval.py` 的 `build_report_meta` 前新增：

```python
def begin_eval_run(kb_id: str) -> tuple[str, str]:
    """生成并记录一次评测的 run id / run trace id。

    Args:
        kb_id: 知识库 UUID（仅用于事件字段）

    Returns:
        `(run_id, run_trace_id)`；`run_id` 为 UTC 时间戳，`run_trace_id` 为
        `eval_run_<run_id>_<8hex>`。
    """
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_trace_id = f"eval_run_{run_id}_{uuid.uuid4().hex[:8]}"
    core_logging.log_event(
        Event.EVALUATION_RUN,
        kb_id=kb_id,
        run_id=run_id,
        run_trace_id=run_trace_id,
    )
    return run_id, run_trace_id
```

`main()` 中在 `configure_tracing()` 之后、`try:` 之前插入：

```python
    run_id, run_trace_id = begin_eval_run(kb_id)
    eval_trace_token = None
```

把 `try:` 内原来的：

```python
        session_id = args.session_id
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
```

改为：

```python
        session_id = args.session_id
        timestamp = run_id
```

删除 testset 加载后的旧 `EVALUATION_RUN` 日志行：

```python
        core_logging.log_event(Event.EVALUATION_RUN, kb_id=kb_id)
```

在 `bridge_judge = build_bridge_judge(...)` 之后插入：

```python
        eval_trace = EvalTraceContext(
            run_id=run_id,
            run_trace_id=run_trace_id,
            judge_model=bridge_judge.model_name,
        )
        eval_trace_token = current_eval_trace.set(eval_trace)
        judge_evidence.set_run(run_trace_id, bridge_judge.model_name)
```

`main()` 的 `finally:` 改为：

```python
    finally:
        if eval_trace_token is not None:
            current_eval_trace.reset(eval_trace_token)
        flush_tracing()
```

imports 增加：

```python
from src.infra.llm.trace_context import EvalTraceContext, current_eval_trace, current_trace_id
```

- [ ] **Step 5: 报告头与 meta**

`build_report_meta` 签名加 `run_trace_id: str | None = None`，两个 return dict 都加 `"run_trace_id": run_trace_id`。

`save_markdown_report` 在 `lines.append(f"**QA Pairs:** {len(questions)}")` 后加：

```python
    if report_meta is not None and report_meta.get("run_trace_id"):
        lines.append(f"**Run trace:** {report_meta['run_trace_id']}")
```

`main()` 调用 `build_report_meta` 时加 `run_trace_id=run_trace_id`。

- [ ] **Step 6: 题级事件带 run_trace_id**

`_collect_one_question` 内三处日志改为：

```python
    trace_ctx = current_eval_trace.get()
    run_trace_id = trace_ctx.run_trace_id if trace_ctx is not None else ""
    core_logging.log_event(
        Event.QA_ANSWER_START, index=index, query=query, run_trace_id=run_trace_id
    )
    ...
        core_logging.log_event(
            Event.QA_ANSWER_DONE,
            index=index,
            answer_len=len(answer),
            contexts=len(ctx_list),
            run_trace_id=run_trace_id,
        )
    ...
        core_logging.log_event(
            Event.QA_ANSWER_FAILED,
            index=index,
            err=str(e),
            err_type=type(e).__name__,
            err_repr=repr(e)[:200],
            run_trace_id=run_trace_id,
        )
```

`run_evaluation` 的 `EVALUATION_START` 改为：

```python
    trace_ctx = current_eval_trace.get()
    run_trace_id = trace_ctx.run_trace_id if trace_ctx is not None else ""
    core_logging.log_event(
        Event.EVALUATION_START, samples=len(rows), run_trace_id=run_trace_id
    )
```

- [ ] **Step 7: 登记事件字段**

`src/core/log_event_specs.py` 改为：

```python
    "evaluation run": EventSpec(
        "evaluation run", "cli", "info", ("kb_id", "run_id", "run_trace_id")
    ),
    "evaluation start": EventSpec(
        "evaluation start", "cli", "info", ("samples", "run_trace_id")
    ),
    "question answer start": EventSpec(
        "question answer start", "cli", "info", ("index", "query", "run_trace_id")
    ),
    "question answer done": EventSpec(
        "question answer done",
        "cli",
        "info",
        ("index", "answer_len", "contexts", "run_trace_id"),
    ),
    "question answer failed": EventSpec(
        "question answer failed",
        "cli",
        "warning",
        ("index", "err", "err_type", "err_repr", "run_trace_id"),
    ),
```

- [ ] **Step 8: 跑测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_trace_run.py tests/cli/test_eval_native_meta.py tests/cli/test_deepeval_only.py -v`

Expected: PASS。若 `test_deepeval_only` 因 fake HookEvidence 缺 `set_run` 失败，在 `_FakeHookEvidence` 中加：

```python
        def set_run(self, run_trace_id, judge_model=None):
            self.run_trace_id = run_trace_id
```

- [ ] **Step 9: 提交**

```bash
git add src/infra/llm/trace_context.py src/cli/eval_deepeval.py src/core/log_events.py src/core/log_event_specs.py tests/cli/test_eval_trace_run.py tests/cli/test_deepeval_only.py
git commit -m "feat(eval): add run trace and question anchors"
```

---

### Task 2: HookEvidence 的 measure 生命周期、JUDGE_HTTP 与异常隔离

**Files:**
- Modify: `src/infra/llm/provider/http_observability.py`
- Modify: `src/core/log_events.py:171`
- Modify: `src/core/log_event_specs.py:307-312`
- Test: `tests/infra/llm/provider/test_http_observability.py`

**Interfaces:**
- Produces:
  - `HookEvidence.set_run(run_trace_id: str | None) -> None`
  - `HookEvidence.begin_measure(*, measure_id: str, metric: str, attempt: int, trace_id: str | None, q_index: int | None) -> None`
  - `HookEvidence.end_measure(*, reason: str | None, error: str) -> dict`
  - `HookEvidence.has_active_measure -> bool`
  - `HookEvidence.measures: list[dict]`

- [ ] **Step 1: 写失败测试**

在 `tests/infra/llm/provider/test_http_observability.py` 顶部 import 增加 `import asyncio` / `import json`；在文件末尾追加（复用顶部已有的 `TIMEOUT` / `_client_with`）：

```python
def _begin(ev, measure_id="eval_t1:faithfulness:1"):
    ev.set_run("eval_run_20261019_000000_abcd1234")
    ev.mark("eval_t1")
    ev.begin_measure(
        measure_id=measure_id,
        metric="faithfulness",
        attempt=1,
        trace_id="eval_t1",
        q_index=0,
    )


@pytest.mark.asyncio
async def test_success_records_usage_and_one_http_event():
    ev = HookEvidence()
    _begin(ev)
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        client = _client_with(
            ev,
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                },
            ),
        )
        async with client:
            await client.post("https://example.invalid/v1/chat/completions")
        measure = ev.end_measure(reason="ok", error="")
    finally:
        logger.remove(sink_id)

    assert measure["request_count"] == 1
    assert measure["response_count"] == 1
    assert measure["statuses"] == [200]
    assert measure["tokens_in"] == 10
    assert measure["tokens_out"] == 3
    assert measure["calls"][0]["judge_call_id"] == "eval_t1:faithfulness:1:1"
    assert measure["calls"][0]["json_ok"] is True
    assert measure["calls"][0]["finish_reasons"] == ["stop"]

    http_line = next(m for m in messages if "judge http" in m)
    assert "judge_call_id=eval_t1:faithfulness:1:1" in http_line
    assert "status=200" in http_line
    assert "tokens_in=10" in http_line


@pytest.mark.asyncio
async def test_out_of_order_responses_are_sorted_by_call_index():
    ev = HookEvidence()
    _begin(ev)

    async def handler(request):
        kind = json.loads(request.content)["kind"]
        if kind == "slow":
            await asyncio.sleep(0.02)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop"}]})
        return httpx.Response(429, json={"error": "rate"})

    client = _client_with(ev, handler)
    async with client:
        slow = asyncio.create_task(
            client.post("https://example.invalid/v1/chat/completions", json={"kind": "slow"})
        )
        await asyncio.sleep(0.005)
        fast = asyncio.create_task(
            client.post("https://example.invalid/v1/chat/completions", json={"kind": "fast"})
        )
        await asyncio.gather(slow, fast)

    measure = ev.end_measure(reason=None, error="")
    assert [c["call_index"] for c in measure["calls"]] == [1, 2]
    assert measure["statuses"] == [200, 429]


@pytest.mark.asyncio
async def test_non_2xx_keeps_real_status():
    ev = HookEvidence()
    _begin(ev)
    client = _client_with(ev, lambda req: httpx.Response(429, json={"error": "rate"}))
    async with client:
        await client.post("https://example.invalid/v1/chat/completions")

    measure = ev.end_measure(reason=None, error="")
    assert measure["statuses"] == [429]
    assert measure["calls"][0]["status"] == 429


@pytest.mark.asyncio
async def test_transport_failure_gets_a_failure_record():
    ev = HookEvidence()
    _begin(ev)

    def boom(req):
        raise httpx.ConnectError("no route", request=req)

    client = _client_with(ev, boom)
    async with client:
        with pytest.raises(httpx.ConnectError):
            await client.post("https://example.invalid/v1/chat/completions")

    measure = ev.end_measure(reason=None, error="ConnectError: no route")
    assert measure["request_count"] == 1
    assert measure["response_count"] == 0
    assert measure["statuses"] == [None]
    assert measure["calls"][0]["status"] is None
    assert measure["calls"][0]["error"] == "no_response"


@pytest.mark.asyncio
async def test_no_active_measure_does_not_emit_http_event():
    ev = HookEvidence()
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        client = _client_with(
            ev, lambda req: httpx.Response(200, json={"choices": []})
        )
        async with client:
            await client.post("https://example.invalid/v1/chat/completions")
    finally:
        logger.remove(sink_id)

    assert ev.measures == []
    assert any(e.get("phase") == "response" for e in ev.events)
    assert not any("judge http" in m for m in messages)


@pytest.mark.asyncio
async def test_observability_failure_does_not_fail_request(monkeypatch):
    ev = HookEvidence()
    _begin(ev)

    def boom(*args, **kwargs):
        raise RuntimeError("log sink down")

    monkeypatch.setattr(
        "src.infra.llm.provider.http_observability.core_logging.log_event", boom
    )
    client = _client_with(ev, lambda req: httpx.Response(200, json={"choices": []}))
    async with client:
        resp = await client.post("https://example.invalid/v1/chat/completions")

    assert resp.status_code == 200
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/llm/provider/test_http_observability.py -v`

Expected: `AttributeError: 'HookEvidence' object has no attribute 'begin_measure'`.

- [ ] **Step 3: 实现 HookEvidence**

把 `src/infra/llm/provider/http_observability.py` 的 import 改为：

```python
from __future__ import annotations

import json
import time

import httpx
from loguru import logger

from src.core import logging as core_logging
from src.core.log_events import Event
```

把 `HookEvidence` 替换为：

```python
class HookEvidence:
    """按样本归集的观测证据（裁判 client 跨样本复用 ⇒ 用可变桶，不闭包固定 id）。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.measures: list[dict] = []
        self._trace_id: str | None = None
        self._run_trace_id: str | None = None
        self._judge_model: str | None = None
        self._measure: dict | None = None
        self._calls: dict[int, dict] = {}
        self._call_index = 0
        self._event_start = 0

    def set_run(
        self, run_trace_id: str | None, judge_model: str | None = None
    ) -> None:
        """绑定整轮 run trace 与裁判模型；`reset()` 不会清除它们。"""
        self._run_trace_id = run_trace_id
        self._judge_model = judge_model

    def mark(self, trace_id: str | None) -> None:
        """切到某题的证据窗口（评分串行，并发=1，故单槽安全）。"""
        self._trace_id = trace_id

    def reset(self) -> None:
        """清当前题窗口；保留 run trace。"""
        self.events.clear()
        self.measures.clear()
        self._trace_id = None
        self._measure = None
        self._calls = {}
        self._call_index = 0
        self._event_start = 0

    @property
    def has_active_measure(self) -> bool:
        return self._measure is not None

    def begin_measure(
        self,
        *,
        measure_id: str,
        metric: str,
        attempt: int,
        trace_id: str | None,
        q_index: int | None,
    ) -> None:
        """开始一次指标打分；后续 HTTP 调用归到该 measure。"""
        self._trace_id = trace_id
        self._measure = {
            "judge_measure_id": measure_id,
            "metric": metric,
            "attempt": attempt,
            "q_index": q_index,
        }
        self._calls = {}
        self._call_index = 0
        self._event_start = len(self.events)

    def end_measure(self, *, reason: str | None, error: str) -> dict:
        """结束 measure：补齐无 response 的调用、写 JUDGE_HTTP、返回汇总。"""
        measure = self._measure or {
            "judge_measure_id": None,
            "metric": None,
            "attempt": None,
            "q_index": None,
        }
        for idx in range(1, self._call_index + 1):
            self._calls.setdefault(
                idx,
                {
                    "call_index": idx,
                    "judge_call_id": f"{measure['judge_measure_id']}:{idx}",
                    "status": None,
                    "finish_reasons": None,
                    "tokens_in": None,
                    "tokens_out": None,
                    "latency_ms": None,
                    "json_ok": None,
                    "error": "no_response",
                },
            )
        calls = [self._calls[i] for i in sorted(self._calls)]

        for call in calls:
            if call.get("status") is None:
                self._log_http(call)

        responses = [c for c in calls if c.get("status") is not None]
        tokens_ok = bool(responses) and all(
            c.get("tokens_in") is not None and c.get("tokens_out") is not None
            for c in responses
        )
        record = {
            "judge_measure_id": measure["judge_measure_id"],
            "metric": measure["metric"],
            "attempt": measure["attempt"],
            "model": self._judge_model,
            "trace_id": self._trace_id,
            "q_index": measure["q_index"],
            "run_trace_id": self._run_trace_id,
            "request_count": len(calls),
            "response_count": len(responses),
            "statuses": [c.get("status") for c in calls],
            "tokens_in": sum(c["tokens_in"] for c in responses) if tokens_ok else None,
            "tokens_out": sum(c["tokens_out"] for c in responses) if tokens_ok else None,
            "error_count": sum(
                1
                for c in calls
                if c.get("status") is None
                or not (200 <= c["status"] < 300)
                or c.get("json_ok") is False
            ),
            "calls": [
                {k: v for k, v in c.items() if not k.startswith("_")}
                for c in calls
            ],
            "reason": reason,
            "error": error,
        }
        self.measures.append(record)
        self._measure = None
        self._calls = {}
        return record

    def _log_http(self, call: dict) -> None:
        measure = self._measure or {}
        try:
            core_logging.log_event(
                Event.JUDGE_HTTP,
                run_trace_id=self._run_trace_id,
                trace_id=self._trace_id,
                q_index=measure.get("q_index"),
                judge_measure_id=measure.get("judge_measure_id"),
                judge_call_id=call.get("judge_call_id"),
                component="judge",
                metric=measure.get("metric"),
                attempt=measure.get("attempt"),
                call_index=call.get("call_index"),
                status=call.get("status"),
                finish_reasons=call.get("finish_reasons"),
                tokens_in=call.get("tokens_in"),
                tokens_out=call.get("tokens_out"),
                latency_ms=call.get("latency_ms"),
                json_ok=call.get("json_ok"),
                error=call.get("error"),
            )
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
            logger.warning("[provider] judge http log failed err={}", exc)

    def append(self, rec: dict) -> None:
        """追加一条原始事件（保留 trace_id，供异常判据与重试退避使用）。"""
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
        for e in self.events:
            if e.get("phase") != "response":
                continue
            if not (200 <= e["status"] < 300):
                return True
            if not e.get("json_ok"):
                return True
            if e.get("finish_reason") and any(
                fr != "stop" for fr in e["finish_reason"]
            ):
                return True
        return False
```

- [ ] **Step 4: 改 make_logging_client 的 hooks**

把 `on_request` / `on_response` 替换为：

```python
    async def on_request(request: httpx.Request) -> None:
        try:
            if evidence.has_active_measure:
                evidence._call_index += 1
                call = {
                    "call_index": evidence._call_index,
                    "judge_call_id": (
                        f"{evidence._measure['judge_measure_id']}:"
                        f"{evidence._call_index}"
                    ),
                    "status": None,
                    "finish_reasons": None,
                    "tokens_in": None,
                    "tokens_out": None,
                    "latency_ms": None,
                    "json_ok": None,
                    "error": None,
                    "_started": time.monotonic(),
                }
                evidence._calls[evidence._call_index] = call
                request.extensions["cb_judge"] = call
                evidence.append(
                    {
                        "phase": "request",
                        "url": str(request.url),
                        "call_index": call["call_index"],
                        "judge_call_id": call["judge_call_id"],
                    }
                )
            else:
                evidence.append({"phase": "request", "url": str(request.url)})
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
            logger.warning("[provider] judge request hook failed err={}", exc)

    async def on_response(response: httpx.Response) -> None:
        try:
            rec: dict = {"phase": "response", "status": response.status_code}
            rec["retry_after"] = response.headers.get("retry-after")
            if not (200 <= response.status_code < 300):
                logger.warning(
                    "[provider] bridge judge http non-2xx status={}",
                    response.status_code,
                )
            tokens_in = None
            tokens_out = None
            try:
                body = await response.aread()
                parsed = json.loads(body)
                rec["body_bytes"] = len(body)
                rec["json_ok"] = True
                choices = parsed.get("choices") or []
                rec["finish_reason"] = [c.get("finish_reason") for c in choices]
                usage = parsed.get("usage") or {}
                if isinstance(usage.get("prompt_tokens"), int):
                    tokens_in = usage["prompt_tokens"]
                if isinstance(usage.get("completion_tokens"), int):
                    tokens_out = usage["completion_tokens"]
            except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
                rec["json_ok"] = False
                rec["body_exc"] = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[provider] bridge judge http body unreadable status={} err={}",
                    response.status_code,
                    rec["body_exc"],
                )

            call = response.request.extensions.get("cb_judge")
            if call is not None:
                call["status"] = response.status_code
                call["finish_reasons"] = rec.get("finish_reason")
                call["tokens_in"] = tokens_in
                call["tokens_out"] = tokens_out
                call["latency_ms"] = int(
                    (time.monotonic() - call["_started"]) * 1000
                )
                call["json_ok"] = rec.get("json_ok")
                if not (200 <= response.status_code < 300):
                    call["error"] = f"http_{response.status_code}"
                elif rec.get("json_ok") is False:
                    call["error"] = "json_error"
                evidence._log_http(call)

            evidence.append(rec)
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响主流程
            logger.warning("[provider] judge response hook failed err={}", exc)
```

- [ ] **Step 5: 登记 JUDGE_HTTP 与 JUDGE_CALL 字段**

`src/core/log_events.py` 在 `JUDGE_CALL` 后加：

```python
    JUDGE_HTTP = "judge http"
```

`src/core/log_event_specs.py` 的 `judge call` 元组改为：

```python
    "judge call": EventSpec(
        "judge call",
        "cli",
        "info",
        (
            "trace_id",
            "run_trace_id",
            "judge_measure_id",
            "component",
            "metric",
            "attempt",
            "q_index",
            "model",
            "latency_ms",
            "ok",
            "request_count",
            "response_count",
            "statuses",
            "tokens_in",
            "tokens_out",
            "error_count",
            "error",
            "reason_len",
            "reason_hash",
        ),
    ),
    "judge http": EventSpec(
        "judge http",
        "cli",
        "info",
        (
            "run_trace_id",
            "trace_id",
            "q_index",
            "judge_measure_id",
            "judge_call_id",
            "component",
            "metric",
            "attempt",
            "call_index",
            "status",
            "finish_reasons",
            "tokens_in",
            "tokens_out",
            "latency_ms",
            "json_ok",
            "error",
        ),
    ),
```

- [ ] **Step 6: 跑测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/llm/provider/test_http_observability.py tests/infra/llm/provider/test_bridge_judge.py -v`

Expected: PASS。若 `test_no_active_measure_does_not_emit_http_event` 收到 `judge http` 日志，检查 `on_response` 是否只在 `call is not None` 时调用 `_log_http`。

- [ ] **Step 7: 提交**

```bash
git add src/infra/llm/provider/http_observability.py src/core/log_events.py src/core/log_event_specs.py tests/infra/llm/provider/test_http_observability.py
git commit -m "feat(eval): record judge http calls per measure"
```

---

### Task 3: `JUDGE_CALL` 汇总、score_rows trace scope 与 reason 处理

**Files:**
- Modify: `src/cli/eval_deepeval.py:553-684, 746-860, 1150-1210`
- Test: `tests/cli/test_eval_trace_judge_call.py`（新建）
- Test: `tests/cli/test_eval_native_retry.py`（适配事件窗口）
- Test: `tests/cli/test_eval_tool_correctness.py`（无 trace_id 的 row）

**Interfaces:**
- Consumes: `HookEvidence.begin_measure` / `end_measure` / `measures`（Task 2）
- Produces: `_score_metric_with_retry` 写 `JUDGE_CALL`；`_trace_scope(trace_id)`；`_metric_reason(metric) -> str | None`；`_reason_hash(reason: str) -> str`

- [ ] **Step 1: 写失败测试**

新建 `tests/cli/test_eval_trace_judge_call.py`：

```python
"""JUDGE_CALL 汇总：多 HTTP、失败记录、reason 不复用。"""

from __future__ import annotations

import math

import pytest
from loguru import logger

from src.cli.eval_deepeval import _score_metric_with_retry
from src.infra.llm.provider.http_observability import HookEvidence


class _Metric:
    def __init__(self, value=0.9, reason="because"):
        self.value = value
        self.reason = reason
        self.calls = 0

    def measure(self, test_case):
        self.calls += 1
        return self.value


class _BadMetric:
    def __init__(self):
        self.calls = 0

    def measure(self, test_case):
        self.calls += 1
        raise RuntimeError("outer") from TimeoutError("boom")


ROW = {"q_index": 0, "trace_id": "eval_t1"}


def _capture():
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    return messages, sink_id


def test_judge_call_carries_measure_summary_and_reason_hash():
    ev = HookEvidence()
    ev.set_run("eval_run_x_abcd1234")
    messages, sink_id = _capture()
    try:
        value, err = _score_metric_with_retry(
            "faithfulness", _Metric(), object(), ROW, ev
        )
    finally:
        logger.remove(sink_id)

    assert value == pytest.approx(0.9) and err == ""
    line = next(m for m in messages if "judge call" in m)
    assert "judge_measure_id=eval_t1:faithfulness:1" in line
    assert "component=judge" in line
    assert "request_count=0" in line
    assert "statuses=[]" in line
    assert "reason_len=7" in line
    assert "reason_hash=" in line
    assert "because" not in line  # 不落完整 reason


def test_failed_measure_does_not_read_stale_reason():
    ev = HookEvidence()
    ev.set_run("eval_run_x_abcd1234")
    metric = _BadMetric()
    messages, sink_id = _capture()
    try:
        value, err = _score_metric_with_retry(
            "faithfulness", metric, object(), ROW, ev
        )
    finally:
        logger.remove(sink_id)

    assert math.isnan(value)
    assert "TimeoutError" in err
    lines = [m for m in messages if "judge call" in m]
    assert lines
    assert all("reason_len=null" in m for m in lines)
    assert all("reason_hash=null" in m for m in lines)


def test_evidence_none_is_noop():
    value, err = _score_metric_with_retry(
        "faithfulness", _Metric(), object(), ROW, None
    )
    assert value == pytest.approx(0.9) and err == ""


def test_row_without_trace_id_does_not_keyerror():
    ev = HookEvidence()
    value, err = _score_metric_with_retry(
        "faithfulness", _Metric(), object(), {"q_index": 0}, ev
    )
    assert value == pytest.approx(0.9) and err == ""
```

在 `tests/cli/test_eval_native_retry.py` 的 `_seq_with_429` 中，把 `_record` 改为同时写 request 与 response：

```python
    def _record(call: int) -> None:
        if call != 1:
            return
        ev.append(
            {
                "phase": "request",
                "call_index": 1,
                "judge_call_id": "t0:faithfulness:1:1",
            }
        )
        ev.append(
            {
                "phase": "response",
                "status": 429,
                "json_ok": True,
                "finish_reason": [],
                "finish_reasons": [],
                "call_index": 1,
                "retry_after": retry_after,
            }
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_trace_judge_call.py tests/cli/test_eval_native_retry.py -v`

Expected: `JUDGE_CALL` 仍用旧字段 / `begin_measure` 未调用。

- [ ] **Step 3: 实现 trace scope、reason 辅助与 JUDGE_CALL**

在 `src/cli/eval_deepeval.py` 顶部 import：

```python
import hashlib
from contextlib import contextmanager

from loguru import logger
```

在 `_exc_chain_summary` 后新增：

```python
@contextmanager
def _trace_scope(trace_id: str | None):
    """在评分期间把 current_trace_id 设为当前题；缺失时 no-op。"""
    token = None
    if trace_id:
        try:
            token = current_trace_id.set(trace_id)
        except Exception as exc:  # noqa: BLE001  # 观测旁路
            logger.warning("[cli] trace set failed err={}", exc)
    try:
        yield
    finally:
        if token is not None:
            current_trace_id.reset(token)


def _metric_reason(metric) -> str | None:
    """只在 measure 成功后读取 reason；无属性或非字符串返回 None。"""
    if not hasattr(metric, "reason"):
        return None
    reason = metric.reason
    if isinstance(reason, str) and reason:
        return reason
    return None


def _reason_hash(reason: str) -> str:
    return hashlib.sha256(reason.encode("utf-8")).hexdigest()[:16]
```

把 `_score_metric_with_retry` 的 attempt 循环改为：

```python
    trace_ctx = current_eval_trace.get()
    run_trace_id = trace_ctx.run_trace_id if trace_ctx is not None else None
    judge_model = trace_ctx.judge_model if trace_ctx is not None else None

    for attempt in range(1, attempts + 1):
        trace_id = row.get("trace_id")
        measure_id = f"{trace_id or 'no_trace'}:{metric_name}:{attempt}"
        event_start = 0 if evidence is None else len(evidence.events)
        if evidence is not None:
            try:
                evidence.begin_measure(
                    measure_id=measure_id,
                    metric=metric_name,
                    attempt=attempt,
                    trace_id=trace_id,
                    q_index=row.get("q_index"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[cli] begin measure failed err={}", exc)

        started = time.monotonic()
        value: float = float("nan")
        note = ""
        reason: str | None = None
        measure: dict | None = None
        try:
            try:
                value = metric.measure(test_case)
                note = ""
            except Exception as exc:  # noqa: BLE001
                value, note = float("nan"), _exc_chain_summary(exc)

            is_nan = value is None or (
                isinstance(value, float) and math.isnan(value)
            )
            if not is_nan and not note:
                reason = _metric_reason(metric)
        finally:
            if evidence is not None:
                try:
                    measure = evidence.end_measure(reason=reason, error=note)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[cli] end measure failed err={}", exc)

        latency_ms = int((time.monotonic() - started) * 1000)
        is_nan = value is None or (
            isinstance(value, float) and math.isnan(value)
        )
        try:
            core_logging.log_event(
                Event.JUDGE_CALL,
                metric=metric_name,
                q_index=row.get("q_index"),
                trace_id=trace_id,
                run_trace_id=run_trace_id,
                judge_measure_id=measure_id,
                component="judge",
                attempt=attempt,
                model=judge_model,
                latency_ms=latency_ms,
                ok=not is_nan,
                request_count=(measure or {}).get("request_count", 0),
                response_count=(measure or {}).get("response_count", 0),
                statuses=(measure or {}).get("statuses", []),
                tokens_in=(measure or {}).get("tokens_in"),
                tokens_out=(measure or {}).get("tokens_out"),
                error_count=(measure or {}).get("error_count", 0),
                error=note,
                reason_len=len(reason) if reason else None,
                reason_hash=_reason_hash(reason) if reason else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cli] judge call log failed err={}", exc)

        if value is not None and not is_nan:
            return float(value), ""
        last_note = note or "nan"
        if attempt >= attempts:
            break
        core_logging.log_event(
            Event.METRIC_RETRY,
            metric=metric_name,
            q_index=row.get("q_index"),
            trace_id=trace_id,
            reason=last_note,
        )
        time.sleep(_retry_delay(attempt, evidence, event_start))
    return float("nan"), f"{metric_name}:{last_note}"
```

注意：`event_start` 仍需在循环最开始计算（`len(evidence.events)`），与 `_retry_delay` 兼容。

- [ ] **Step 4: score_rows 使用 trace scope**

把 `score_rows` 的 `for row in rows:` 循环体改成：

```python
    for row in rows:
        if evidence is not None:
            evidence.reset()
            evidence.mark(row.get("trace_id"))
        with _trace_scope(row.get("trace_id")):
            scored: dict = {"q_index": row["q_index"], "error": ""}
            errors: list[str] = []

            tool_metric = None
            if diagnostic_metrics is not None:
                tool_metric = diagnostic_metrics.get("tool_correctness")
            tool_value, tool_reason = _score_tool_correctness(row, tool_metric)
            scored["tool_correctness"] = tool_value
            scored["tool_correctness_reason"] = tool_reason

            if row.get("sample_class") == SAMPLE_CLASS_EMPTY or is_failed_run(
                row.get("answer"), row.get("trajectory")
            ):
                for name in metrics:
                    scored[name] = float("nan")
                if diagnostic_metrics is not None:
                    for name in diagnostic_metrics:
                        if name == "tool_correctness":
                            continue
                        scored[name] = float("nan")
                if row.get("sample_class") == SAMPLE_CLASS_EMPTY:
                    scored["error"] = "sample_class=empty"
                else:
                    scored["error"] = "failed"
                out.append(scored)
                continue

            test_case = _bridge_test_case(row)
            for name, metric in metrics.items():
                value, err = _score_metric_with_retry(
                    name, metric, test_case, row, evidence
                )
                scored[name] = value
                if err:
                    errors.append(err)

            if diagnostic_metrics is not None:
                relevancy = diagnostic_metrics.get("contextual_relevancy")
                if relevancy is not None:
                    relevancy_case = _bridge_test_case(
                        row, max_contexts=EVAL_RELEVANCY_MAX_CONTEXTS
                    )
                    value, err = _score_metric_with_retry(
                        "contextual_relevancy",
                        relevancy,
                        relevancy_case,
                        row,
                        evidence,
                        max_attempts=1,
                    )
                    scored["contextual_relevancy"] = value
                    if err:
                        errors.append(err)
                non_advice = diagnostic_metrics.get("non_advice")
                if non_advice is not None:
                    value, err = _score_metric_with_retry(
                        "non_advice",
                        non_advice,
                        test_case,
                        row,
                        evidence,
                        max_attempts=1,
                    )
                    scored["non_advice"] = value
                    if err:
                        errors.append(err)

            scored["error"] = ";".join(errors)
            out.append(scored)
```

- [ ] **Step 5: 运行目标测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_trace_judge_call.py tests/cli/test_eval_native_retry.py tests/cli/test_eval_tool_correctness.py tests/cli/test_behaviour_fail_loud.py -v`

Expected: PASS。若 `test_evidence_none_is_noop` 因 `JUDGE_CALL` 日志字段 `model=None` 报编码错误，确认 `encode_value(None)` 输出 `null`。

- [ ] **Step 6: 提交**

```bash
git add src/cli/eval_deepeval.py tests/cli/test_eval_trace_judge_call.py tests/cli/test_eval_native_retry.py
git commit -m "feat(eval): emit judge measure summaries with reason hashes"
```

---

### Task 4: 行为轴 order 归一化与 trace 映射

**Files:**
- Modify: `src/cli/eval_scoring.py:330-375`
- Test: `tests/cli/test_behaviour_scores_read.py`
- Test: `tests/cli/test_behaviour_trace_link.py`（新建）
- Test: `tests/cli/test_behaviour_fail_loud.py`（如顺序断言受影响）

**Interfaces:**
- Produces: `read_behaviour_scores` 返回行含 `q_index`（=order）、`order`；`link_behavior_traces(behaviour_rows, trace_ids) -> list[dict]`

- [ ] **Step 1: 写失败测试**

把 `tests/cli/test_behaviour_scores_read.py` 的 `_FIXTURE` 与所有 payload 加 `order`：

```python
_FIXTURE = {
    "testCases": [
        {
            "order": 0,
            "input": "Q0",
            "metricsData": [
                {"name": "Task Completion", "score": 1.0, "success": True},
                {"name": "Step Efficiency", "score": 0.5, "success": True},
            ],
        },
        {
            "order": 1,
            "input": "Q1",
            "metricsData": [
                {"name": "Task Completion", "score": 0.0, "success": False},
            ],
        },
    ]
}
```

同一文件里另外两个 payload 也要加 `order`：

- `test_picks_the_newest_test_run_file`：`{"input": "OLD", "metricsData": []}` → 加 `"order": 0`
- `test_read_behaviour_scores_carries_reason`：`{"input": "Q1", ...}` → 加 `"order": 0`

新增测试：

```python
def test_shuffled_cases_are_sorted_and_q_index_is_order(tmp_path):
    payload = {
        "testCases": [
            {"order": 1, "input": "Q1", "metricsData": []},
            {"order": 0, "input": "Q0", "metricsData": []},
        ]
    }
    (tmp_path / "test_run_20261019.json").write_text(json.dumps(payload), encoding="utf-8")
    rows = read_behaviour_scores(str(tmp_path))
    assert [r["q_index"] for r in rows] == [0, 1]
    assert [r["order"] for r in rows] == [0, 1]
    assert [r["input"] for r in rows] == ["Q0", "Q1"]


@pytest.mark.parametrize(
    "payload",
    [
        {"testCases": [{"input": "Q0", "metricsData": []}]},
        {"testCases": [{"order": 2, "input": "Q0", "metricsData": []}]},
        {
            "testCases": [
                {"order": 0, "input": "Q0", "metricsData": []},
                {"order": 0, "input": "Q1", "metricsData": []},
            ]
        },
    ],
)
def test_invalid_order_fails_loud(tmp_path, payload):
    (tmp_path / "test_run_20261019.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="order"):
        read_behaviour_scores(str(tmp_path))
```

新建 `tests/cli/test_behaviour_trace_link.py`：

```python
from src.cli.eval_scoring import link_behavior_traces


def test_links_by_q_index():
    rows = [{"q_index": 1, "input": "Q1"}, {"q_index": 0, "input": "Q0"}]
    linked = link_behavior_traces(rows, ["t0", "t1"])
    assert linked[0]["trace_id"] == "t1" and linked[0]["link_method"] == "order"
    assert linked[1]["trace_id"] == "t0" and linked[1]["link_method"] == "order"


def test_out_of_range_is_unmapped():
    linked = link_behavior_traces([{"q_index": 5}], ["t0"])
    assert linked[0]["trace_id"] is None
    assert linked[0]["link_method"] == "unmapped"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_behaviour_scores_read.py tests/cli/test_behaviour_trace_link.py -v`

Expected: `ImportError: link_behavior_traces` / `KeyError: 'order'`.

- [ ] **Step 3: 实现 read_behaviour_scores 校验与排序**

把 `read_behaviour_scores` 的 `raw = json.loads(...)` 之后到 `return rows` 替换为：

```python
    raw = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    cases = raw.get("testCases") or []
    if not cases:
        return []

    seen: set[int] = set()
    for idx, tc in enumerate(cases):
        order = tc.get("order")
        if not isinstance(order, int) or isinstance(order, bool):
            raise RuntimeError(
                f"test_run testCases[{idx}] 缺少合法 order：{order!r}"
            )
        if order < 0 or order >= len(cases):
            raise RuntimeError(
                f"test_run testCases[{idx}] order={order} 越界（n={len(cases)}）"
            )
        if order in seen:
            raise RuntimeError(f"test_run testCases[{idx}] order={order} 重复")
        seen.add(order)

    rows: list[dict] = []
    for idx, tc in sorted(enumerate(cases), key=lambda pair: pair[1]["order"]):
        metrics = {
            md.get("name"): md.get("score") for md in (tc.get("metricsData") or [])
        }
        reasons = {
            md.get("name"): md.get("reason") for md in (tc.get("metricsData") or [])
        }
        rows.append(
            {
                "q_index": tc["order"],
                "order": tc["order"],
                "input": tc.get("input", ""),
                "metrics": metrics,
                "reasons": reasons,
            }
        )
    return rows
```

- [ ] **Step 4: 实现 link_behavior_traces**

在 `read_behaviour_scores` 后新增：

```python
def link_behavior_traces(
    behaviour_rows: list[dict], trace_ids: list[str]
) -> list[dict]:
    """把行为行按 q_index 映射到 trace_id。

    Args:
        behaviour_rows: `read_behaviour_scores` 产物（q_index 已是权威题号）
        trace_ids: 每题 trace_id，顺序与 q_index 一致

    Returns:
        行副本，追加 `trace_id` 与 `link_method`（`order` / `unmapped`）
    """
    out: list[dict] = []
    for row in behaviour_rows:
        item = dict(row)
        q_index = item.get("q_index")
        if (
            isinstance(q_index, int)
            and not isinstance(q_index, bool)
            and 0 <= q_index < len(trace_ids)
        ):
            item["trace_id"] = trace_ids[q_index]
            item["link_method"] = "order"
        else:
            item["trace_id"] = None
            item["link_method"] = "unmapped"
        out.append(item)
    return out
```

- [ ] **Step 5: 确认下游按 q_index 顺序消费**

在 `fill_missing_behaviour_rows` 的 docstring 增加一句：

```python
    """按 q_index 补齐行为分行；只允许 `failed=True` 的题缺行。

    `q_index` 由 `read_behaviour_scores` 从 `testCase.order` 归一化，列表顺序
    与 q_index 一致；补齐后 Langfuse 逐位回写与报告取 trajectories[i] 均按同一题号。
    """
```

`_write_langfuse_scores` 与 `_build_behaviour_section` 无需改代码；新增测试 `test_shuffled_cases_are_sorted_and_q_index_is_order` 已锁住顺序。

- [ ] **Step 6: 跑测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_behaviour_scores_read.py tests/cli/test_behaviour_trace_link.py tests/cli/test_behaviour_fail_loud.py tests/cli/test_behaviour_report.py tests/cli/test_langfuse_score_names.py -v`

Expected: PASS。若 `test_deepeval_only` mock 的 rows 无 order，不影响（它 mock 了整个函数）。

- [ ] **Step 7: 提交**

```bash
git add src/cli/eval_scoring.py tests/cli/test_behaviour_scores_read.py tests/cli/test_behaviour_trace_link.py
git commit -m "fix(eval): normalize behaviour order before trace mapping"
```

---

### Task 5: Langfuse metadata 与评测文档

**Files:**
- Modify: `src/cli/eval_deepeval.py:197-225`
- Modify: `src/cli/README.md`（评测章节）
- Modify: `docs/agents/logging-rules.md:134-148`
- Modify: `docs/agents/troubleshooting.md:130-185`
- Modify: `docs/agents/eval-metrics-troubleshooting.md`（新增 trace 小节）
- Modify: `src/cli/trace_logs.py`（模块 docstring）
- Test: `tests/cli/test_eval_trace_judge_call.py`（加 metadata 测试）

**Interfaces:**
- Consumes: `current_eval_trace`（Task 1）、`JUDGE_CALL` / `JUDGE_HTTP` 字段（Task 2/3）

- [ ] **Step 1: 写失败测试**

在 `tests/cli/test_eval_trace_judge_call.py` 追加：

```python
def test_langfuse_trace_metadata_carries_run_and_component(monkeypatch):
    from src.cli import eval_deepeval
    from src.infra.llm.trace_context import EvalTraceContext, current_eval_trace

    captured: dict = {}

    def _update(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(eval_deepeval.langfuse_context, "update_current_trace", _update)
    ctx = EvalTraceContext(
        run_id="20261019_000000",
        run_trace_id="eval_run_20261019_000000_abcd1234",
        judge_model="judge",
    )
    token = current_eval_trace.set(ctx)
    try:
        import asyncio

        asyncio.run(
            eval_deepeval._answer_one_question(
                graph=_FakeGraph(),
                kb_id="kb",
                session_id="s",
                query="q",
                trace_id="eval_t1",
            )
        )
    finally:
        current_eval_trace.reset(token)

    assert captured["metadata"] == {
        "run_trace_id": "eval_run_20261019_000000_abcd1234",
        "component": "sut",
    }


class _FakeGraph:
    async def ainvoke(self, state, **kwargs):
        return {"answer": "a", "tool_contexts": [], "messages": []}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_trace_judge_call.py::test_langfuse_trace_metadata_carries_run_and_component -v`

Expected: FAIL — `metadata` 缺失。

- [ ] **Step 3: 实现 metadata**

`_answer_one_question` 开头改为：

```python
    trace_ctx = current_eval_trace.get()
    metadata = {"component": "sut"}
    if trace_ctx is not None:
        metadata["run_trace_id"] = trace_ctx.run_trace_id
    langfuse_context.update_current_trace(
        input={"query": query},
        session_id=session_id,
        metadata=metadata,
    )
```

- [ ] **Step 4: 更新日志与排障文档**

`docs/agents/logging-rules.md` 的 `judge call` 条目改为：

```markdown
- `judge call`（cli / info）——裁判**每次 attempt** 记一条。`ok=false` 表示**该次尝试失败**，
  不等于该指标最终失败（`error` 列只在最终失败时非空）；`error` 在异常路径上为**异常链摘要**
  （`外层类<-根因类: 根因消息`，见 `_exc_chain_summary`），成功时为空串；桥把失败吞成 NaN
  而无异常时 `error` 亦为空串（此时判据是 NaN）。`request_count` / `response_count` /
  `statuses` 汇总本次 measure 的所有 HTTP 调用；`statuses` 按 `call_index` 排序，无 response
  位为 null。`reason_len` / `reason_hash` 只在 measure 成功后出现；**不记录 reason 原文**。
- `judge http`（cli / info）——每次裁判 HTTP 调用（含无 response 的失败记录）记一条。
  `judge_call_id = <trace_id>:<metric>:<attempt>:<call_index>`；`status=null` +
  `error=no_response` 表示连接失败/超时。`trace_id` / `q_index` 显式写入，不依赖日志 patcher。
```

同文件 `metric retry` 条目里的“口径与 `judge call` 的 `note` 同源”改为“与 `judge call` 的 `error` 同源”。

`docs/agents/troubleshooting.md` 的评测段增加：

```markdown
### 评测 trace 链

1. `EVALUATION_RUN` 拿 `run_trace_id`（testset 加载前就有）。
2. `trace_logs <run_trace_id>` 拿每题 `QA_ANSWER_START/DONE` 的 `trace_id`。
3. `trace_logs <trace_id> --grep "judge call"` 拿 `judge_measure_id`。
4. `trace_logs <trace_id> --grep "judge http"` 拿每次 HTTP 的 `judge_call_id` / status / tokens。

行为轴 judge 没有 active measure：它会在 `judge http` 中缺席，这是 v1 的已知边界。
```

`docs/agents/eval-metrics-troubleshooting.md` 增加“裁判/被测者 trace 怎么看”小节，内容与上面一致，并注明 `status=null` 是传输失败、不是裁判判错。

`src/cli/trace_logs.py` 模块 docstring 把“hooks 仅失败时落日志”改为“hooks 成功/失败都落 `judge http`”。

`src/cli/README.md` 评测章节增加四组 ID 与 `--limit 1` 冒烟说明：

```markdown
### 评测 trace

- `run_trace_id`：每次评测一个（`eval_run_<timestamp>_<8hex>`），报告头 `Run trace`。
- `trace_id`：每题一个，CSV 已有。
- `judge_measure_id`：一次裁判指标打分一个。
- `judge_call_id`：一次裁判 HTTP 调用一个。

`judge call` 是 measure 汇总，`judge http` 是每次 HTTP 明细。
```

- [ ] **Step 5: 跑文档闸门与目标测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_trace_judge_call.py -v`

Run: `.venv/bin/python -m src.cli.check_docs`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/cli/eval_deepeval.py src/cli/README.md docs/agents/logging-rules.md docs/agents/troubleshooting.md docs/agents/eval-metrics-troubleshooting.md src/cli/trace_logs.py tests/cli/test_eval_trace_judge_call.py
git commit -m "docs(eval): document trace ids and judge http logs"
```

---

### Task 6: 全量验证与活体冒烟

**Files:**
- 无代码改动（仅验证）

- [ ] **Step 1: 全量测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q`

Expected: 全部通过；记录 passed/skipped 数。

- [ ] **Step 2: lint**

Run: `.venv/bin/ruff check src/cli src/infra src/core`

Expected: 无错误。

- [ ] **Step 3: 活体冒烟**

Run:

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_deepeval --kb-id 4a1dcb8bb340473c8149ead5b7f75873 --testset-version 6 --limit 1
```

Expected:
- 报告头含 `Run trace: eval_run_...`；
- 日志中同一题 `trace_id` 下能看到 `judge measure`、多条 `judge call` / `judge http`；
- `judge http` 至少一条含 `status=200` 与 tokens（或 `null`）；
- 评分与 CSV 结构不变（仍只有 `trace_id`，没有 `run_trace_id` 列）。

- [ ] **Step 4: 记录结果**

把命令、通过数、冒烟证据（日志片段/报告头）写入任务报告；若冒烟发现 `judge http` 缺失，先检查：
1. `build_bridge_judge` 是否仍只给 `http_async_client` 挂 hooks；
2. `score_rows` 是否设置了 `begin_measure`；
3. `on_response` 是否因 `response.request.extensions` 缺失而走了 no-active-measure 分支。

- [ ] **Step 5: 最终提交（若冒烟只产生 gitignored 产物，则无需提交）**

```bash
git status --short
```

Expected: 工作区干净（`data/` 是 gitignored symlink 指向的目录）。

---

## Self-Review

**Spec coverage:**

| Requirement | Task |
|---|---|
| Every run has run trace + question trace | Task 1 |
| Judge work at measure/HTTP granularity | Task 2、3 |
| Judge execution carries trace + metadata | Task 2、3 |
| Observation failures never change scoring | Task 2、3 |
| Behaviour scores linked by order | Task 4 |
| Always on / non-scoring / failure tolerant | Task 1、3、6 |
| Langfuse metadata | Task 5 |
| MODIFIED evaluation-pipeline delta | Task 1、2、5 |

**Placeholder scan:** 无 TBD/TODO；“按需”“适当”等模糊词未用于实现步骤。

**Type consistency:**
- `HookEvidence.begin_measure` / `end_measure` 签名在 Task 2 与 Task 3 一致；
- `measure` 记录的 `calls[]` 字段与 Task 2 测试、Task 3 `JUDGE_CALL` 消费一致；
- `read_behaviour_scores` 的 `q_index=order` 与 `link_behavior_traces` / `fill_missing` / `_write_langfuse_scores` 一致；
- `JUDGE_CALL` / `JUDGE_HTTP` 字段名与 `log_event_specs` 元组一致。
