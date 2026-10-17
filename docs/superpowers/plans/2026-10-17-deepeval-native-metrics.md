# DeepEval 原生指标与 RAGAS 退役 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把评测主分从 `deepeval.metrics.ragas` 桥换成 DeepEval 原生四指标，新增两项诊断指标，彻底删除 RAGAS 依赖/出题链路，并完成模块、数据目录、配置与报告的命名切换。

**Architecture:** 评分编排（`score_rows` 串行逐题、报告、落库、Langfuse 回写）保持不变，只替换指标来源与用例构造；诊断指标走独立的 `diagnostic_metrics` 通道，永不进入 `EVAL_METRIC_COLUMNS` 与门禁。先改指标（1–2）、再加诊断（3–4）、再退役出题（5–6）、再做改名/迁移（7–11）、最后文档与验收（12–13），确保验收跑在最终命名上。

**Tech Stack:** Python 3.11+ / DeepEval 4.2.9 / pandas / pytest / FastAPI / httpx / Langfuse v2 / Docker Compose

**Spec:** `docs/openspec/changes/deepeval-native-metrics/proposal.md`、`design.md`、`specs/evaluation-pipeline/spec.md`、`tasks.md`

## Global Constraints

- 所有函数必须写 docstring；dataclass 每个字段必须有行内注释。
- 不用三元表达式（`a if cond else b`），写完整 `if/else`。
- 新增常量/阈值/文案必须放 `src/config/`：运行参数进 `settings.py`，事件/固定阈值进 `const.py`。
- 单文件超过 400 行必须拆分；单函数超过 80 行必须拆分子函数。
- `api/` 不得直接 import `infra/` 或 `config/`；`api/chat.py` 不放 SSE 格式化函数。
- 测试 mock 外部依赖，不发起真实网络调用；宿主侧 pytest 一律用 `POSTGRES_HOST=localhost pytest ...`。
- 日志事件消息用英文 `k=v` + `[层名]` 前缀；中文只用于用户可见文案。
- 每个任务结束必须 `git add` 本任务文件并提交；不要提交 `.env`、`data/`、`__pycache__`。
- 改完代码后跑 `ruff format <改动的 py 文件>`，不要全仓 `ruff format .`（会误改计划/文档）。
- 诊断指标 `contextual_relevancy` / `non_advice` / `tool_correctness` **永不**进入 `EVAL_METRIC_COLUMNS`、`main_score_means`、`check_gate`。

---

## File Structure

**Create**
- `src/cli/testset_io.py` — 测试集文件加载（唯一入口，不依赖 ragas）
- `tests/cli/test_eval_native_diagnostics.py` — 诊断指标构造/截断/掩码
- `tests/cli/test_eval_deepeval_run_type.py` — `--run-type` 与 baseline 语义
- `docs/adr/0027-deepeval-native-metrics-ragas-retirement.md` — 本次架构决策
- `docs/superpowers/plans/2026-10-17-deepeval-native-metrics.md` — 本计划

**Modify（按任务顺序）**
- `src/cli/eval_scoring.py` — `build_metrics` / `build_diagnostic_metrics` / `apply_sample_masks` / NaN 统计
- `src/cli/eval_ragas.py` → `src/cli/eval_deepeval.py` — 评分编排、报告、参数、落库
- `src/infra/llm/provider/factory.py` — 裁判温度 0 / max_retries 0 / judge 剥离 top_p
- `src/infra/llm/provider/http_observability.py` — 记录 `Retry-After`
- `src/config/const.py` / `settings.py` — 指标白名单、诊断常量、配置改名
- `src/infra/llm/provider/spec.py` / `src/models.py` — judge 环境变量改名
- `src/api/ragas_generate.py` — 410 桩
- `src/core/log_events.py` / `log_event_specs.py` — 事件改名
- `deploy/nginx/html/index.html` — KB 徽标三态
- `docker-compose.yml` / `docker-compose.image.yml` / `scripts/deploy/deploy.sh` — 数据目录
- `.env.example` / `.env.template` — 只写新名
- 活动文档与 ADR — 见 Task 12

**Delete**
- `src/cli/eval_ragas_generate.py`
- `tests/cli/test_generate_llm_context.py` / `test_generate_entity_anchor.py` / `test_generate_transforms.py` / `test_generate_guard.py` / `test_eval_ragas_personas.py` / `test_self_contained_gate.py`
- `pyproject.toml` 的 `ragas==0.3.9`

---

### Task 1: 原生四指标与裁判采样参数

**Files:**
- Modify: `src/cli/eval_scoring.py:32-66`（`build_metrics`）
- Modify: `src/infra/llm/provider/factory.py:116-152`（`build_bridge_judge`）
- Modify: `src/infra/llm/provider/factory.py:57-79`（`build_chat` 对 judge 剥离 `top_p`）
- Modify: `src/cli/eval_ragas.py`（`main()` 中裁判包装与 `build_metrics` 调用）
- Test: `tests/cli/test_eval_bridge_metrics.py`（整文件替换）
- Test: `tests/infra/llm/provider/test_bridge_judge.py`（更新断言）

**Interfaces:**
- Consumes: `BridgeDeepEvalLLM(chat)`（已有）、`build_chat(role, **overrides)`
- Produces: `build_metrics(judge: Any) -> dict[str, BaseMetric]`（键序固定为 faithfulness / answer_relevancy / context_precision / context_recall）；`build_bridge_judge` 返回的 `ChatOpenAI` 具有 `temperature=0`、`max_retries=0`

- [ ] **Step 1: 写失败测试：四指标必须是原生实现且显式传裁判**

```python
# tests/cli/test_eval_bridge_metrics.py
from deepeval.models import DeepEvalBaseLLM

from src.cli.eval_scoring import build_metrics


class _FakeJudge(DeepEvalBaseLLM):
    """满足 DeepEval 类型检查的最小裁判（不发起网络调用）。"""

    def load_model(self):
        """返回自身，模拟已加载模型。"""
        return self

    def generate(self, prompt: str, **kwargs) -> str:
        """返回空 JSON 对象，测试不会真正执行评分。"""
        return "{}"

    async def a_generate(self, prompt: str, **kwargs) -> str:
        """异步版本，保持与同步实现一致。"""
        return "{}"

    def get_model_name(self) -> str:
        """固定模型名，便于断言。"""
        return "fake-judge"


def test_build_metrics_returns_four_native_metrics():
    metrics = build_metrics(_FakeJudge())

    assert list(metrics) == [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]
    assert [type(m).__name__ for m in metrics.values()] == [
        "FaithfulnessMetric",
        "AnswerRelevancyMetric",
        "ContextualPrecisionMetric",
        "ContextualRecallMetric",
    ]


def test_build_metrics_passes_judge_explicitly():
    judge = _FakeJudge()
    for name, metric in build_metrics(judge).items():
        assert metric.model is judge, f"{name} 必须显式收裁判（防回落到 gpt-3.5-turbo）"


def test_metric_columns_are_unchanged():
    from src.config.const import CONTEXT_METRIC_COLUMNS, RAGAS_METRIC_COLUMNS

    assert RAGAS_METRIC_COLUMNS == (
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    )
    assert CONTEXT_METRIC_COLUMNS == (
        "faithfulness",
        "context_precision",
        "context_recall",
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_bridge_metrics.py -v`
Expected: FAIL —— `build_metrics() missing 1 required positional argument: 'embeddings'`，且类型名仍是 `RAGAS*Metric`。

- [ ] **Step 3: 实现原生四指标**

```python
# src/cli/eval_scoring.py
from typing import Any


def build_metrics(judge: Any) -> dict:
    """构造 DeepEval 原生四项指标（design D1）。

    四项都不需要 embeddings；裁判必须是 `BridgeDeepEvalLLM`，否则
    `deepeval.metrics.utils.models.initialize_model` 会拒绝 langchain 模型。

    Args:
        judge: `BridgeDeepEvalLLM` 包装后的裁判实例

    Returns:
        指标名 -> 原生度量实例；**顺序即串行评分顺序**
    """
    # 确保 DEEPEVAL_* 兜底先于 deepeval 被 import（顺序即语义，故禁用 isort 重排）
    # isort: off
    import src.config.settings  # noqa: F401
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        ContextualPrecisionMetric,
        ContextualRecallMetric,
        FaithfulnessMetric,
    )
    # isort: on

    return {
        "faithfulness": FaithfulnessMetric(model=judge),
        "answer_relevancy": AnswerRelevancyMetric(model=judge),
        "context_precision": ContextualPrecisionMetric(model=judge),
        "context_recall": ContextualRecallMetric(model=judge),
    }
```

- [ ] **Step 4: 写失败测试：裁判温度 0、SDK 重试 0、不传 top_p**

```python
# tests/infra/llm/provider/test_bridge_judge.py
def test_bridge_judge_pins_temperature_zero_and_zero_retries(monkeypatch):
    """裁判必须显式 temperature=0，且不允许 top_p 从 LLM_KWARGS 漏进来。"""
    captured: dict = {}

    def fake_build_chat(role, **kwargs):
        captured["role"] = role
        captured.update(kwargs)
        return _FakeChat()

    monkeypatch.setattr("src.infra.llm.provider.factory.build_chat", fake_build_chat)

    build_bridge_judge(evidence=HookEvidence())

    assert captured["role"] == "judge"
    assert captured["temperature"] == 0
    assert "top_p" not in captured
    assert captured["max_retries"] == 0
    assert "extra_body" not in captured
```

- [ ] **Step 5: 实现裁判参数**

```python
# src/infra/llm/provider/factory.py
def build_bridge_judge(*, evidence: HookEvidence) -> ChatOpenAI:
    """构造 DeepEval 原生指标与行为轴共用的裁判（langchain `ChatOpenAI`）。

    两个 client 都挂、且与 `request_timeout` 同源：
    - `http_async_client`：原生指标的 `a_measure` 异步路径（hooks + 细粒度超时）；
    - `http_client`：`BridgeDeepEvalLLM.generate()` 的同步路径，缺它会回落到
      SDK 默认 `read=600s`（Phase 4 实测单题挂 12 分钟）。

    温度显式为 0：删掉 ragas 后不再有 `LangchainLLMWrapper` 按 `n` 改写，不设会
    落到 provider 默认值，裁判 JSON 判定不可复现。SDK 重试固定为 0，由评测层
    统一做有界重试。

    Args:
        evidence: 请求级观测证据桶（挂到异步 client 的 hooks 上）。

    Returns:
        ChatOpenAI（`ReasoningPreservingChat`）：两个 client 与 `request_timeout`
        共用同一 `httpx.Timeout`，`max_retries=0`。
    """
    from src.config import settings

    timeout = httpx.Timeout(
        connect=settings.RAGAS_HTTP_TIMEOUT_CONNECT,
        read=settings.RAGAS_HTTP_TIMEOUT_READ,
        write=settings.RAGAS_HTTP_TIMEOUT_WRITE,
        pool=settings.RAGAS_HTTP_TIMEOUT_POOL,
    )
    async_client = make_logging_client(timeout=timeout, evidence=evidence)
    sync_client = httpx.Client(timeout=timeout)
    return build_chat(
        "judge",
        temperature=0,
        thinking=settings.RAGAS_JUDGE_THINKING,
        max_tokens=settings.RAGAS_JUDGE_MAX_TOKENS,
        http_client=sync_client,
        http_async_client=async_client,
        request_timeout=timeout,
        max_retries=0,
    )
```

```python
# src/infra/llm/provider/factory.py 的 build_chat 内，extra_kwargs.update(overrides) 之前
    if role == "judge":
        # 裁判不接受项目级采样参数；top_p 会与显式 temperature=0 冲突且不可复现
        extra_kwargs.pop("top_p", None)
```

- [ ] **Step 6: 在 main 中复用同一个 `BridgeDeepEvalLLM`**

```python
# src/cli/eval_ragas.py，替换现有 BridgeDeepEvalLLM / build_metrics 构造段
        from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM
        from src.infra.llm.provider.factory import build_bridge_judge
        from src.infra.llm.provider.http_observability import HookEvidence

        judge_evidence = HookEvidence()
        bridge_judge = build_bridge_judge(evidence=judge_evidence)
        judge_llm = BridgeDeepEvalLLM(bridge_judge)
        metrics = None
        if ragas_enabled:
            metrics = build_metrics(judge_llm)
        core_logging.log_event(
            Event.EVAL_MODEL_INIT,
            sut=settings.LLM_MODEL,
            judge=bridge_judge.model_name,
        )

        behaviour_metrics = build_behaviour_metrics(judge_llm)
```

同时删除 `from src.infra.llm.provider.factory import build_embeddings` 与 `build_metrics(bridge_judge, build_embeddings())`，删除后面重复的 `behaviour_eval_llm = BridgeDeepEvalLLM(bridge_judge)`。

- [ ] **Step 7: 跑测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/cli/test_eval_bridge_metrics.py tests/infra/llm/provider/test_bridge_judge.py -v
```
Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add src/cli/eval_scoring.py src/cli/eval_ragas.py src/infra/llm/provider/factory.py tests/cli/test_eval_bridge_metrics.py tests/infra/llm/provider/test_bridge_judge.py
git commit -m "feat(eval): 四指标改为 DeepEval 原生实现，裁判显式 temperature=0"
```

---

### Task 2: 报告元数据去掉温度分档与 ragas 版本

**Files:**
- Modify: `src/cli/eval_ragas.py`（`build_report_meta`、`save_markdown_report`）
- Test: `tests/cli/test_eval_bridge_meta.py`（整文件替换）
- Test: `tests/cli/test_deepeval_only.py:38-76`（runner_versions / 文案更新）

**Interfaces:**
- Consumes: `nan_stats`、`runner_versions`
- Produces: `build_report_meta(...) -> dict`，键固定为 `caliber` / `judge_model` / `model_arg_explicit` / `nan_cells` / `nan_denominator_cells` / `nan_rate` / `runner_versions`；`runner_versions` 形如 `{"deepeval": "4.2.9", "metrics": "native"}`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_bridge_meta.py
import json

from src.cli.eval_ragas import build_report_meta


def test_report_meta_is_native_and_has_no_temperature_regime():
    meta = build_report_meta(
        judge_model="qwen3.8-flash",
        nan_stats={"nan_cells": 1, "denominator_cells": 76, "nan_rate": 1 / 76},
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
    )

    assert meta["caliber"] == "deep-eval-native"
    assert meta["runner_versions"] == {"deepeval": "4.2.9", "metrics": "native"}
    assert "temperature_regime" not in meta
    assert "temperature_control" not in meta
    assert "ragas" not in json.dumps(meta, ensure_ascii=False).lower()
    assert meta["model_arg_explicit"] is True


def test_report_meta_deepeval_only_has_no_nan_fields():
    meta = build_report_meta(
        judge_model="qwen3.8-flash",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
    )
    assert meta["caliber"] == "deepeval-only"
    assert meta["ragas_skipped"] is True
    assert "nan_cells" not in meta
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_bridge_meta.py -v`
Expected: FAIL —— `caliber` 仍是 `bridge-0.3.9`，且存在 `temperature_regime`。

- [ ] **Step 3: 重写 `build_report_meta` 与报告头两个分支**

```python
def build_report_meta(
    *,
    judge_model: str,
    nan_stats: dict | None,
    runner_versions: dict,
    ragas_enabled: bool = True,
) -> dict:
    """报告/台账元数据（口径版本、NaN 计数、模型显式性）。

    `ragas_enabled=False` 时（--deepeval-only）四指标未跑，不产出 NaN 字段，
    报告头显式标注「主分未跑」。

    Args:
        judge_model: 裁判模型名
        nan_stats: `nan_cell_stats` 的结果；`ragas_enabled=True` 时不得为 None
        runner_versions: 版本字典，形如 `{"deepeval": "4.2.9", "metrics": "native"}`
        ragas_enabled: 是否跑了四指标

    Returns:
        报告元数据字典
    """
    if not ragas_enabled:
        return {
            "caliber": "deepeval-only",
            "judge_model": judge_model,
            "model_arg_explicit": True,
            "ragas_skipped": True,
            "runner_versions": dict(runner_versions),
        }
    if nan_stats is None:
        raise ValueError("ragas_enabled=True 时 nan_stats 不得为 None")
    return {
        "caliber": "deep-eval-native",
        "judge_model": judge_model,
        "model_arg_explicit": True,
        "nan_cells": nan_stats["nan_cells"],
        "nan_denominator_cells": nan_stats["denominator_cells"],
        "nan_rate": round(nan_stats["nan_rate"], 4),
        "runner_versions": dict(runner_versions),
    }
```

`save_markdown_report` 中：

```python
    lines: list[str] = []
    lines.append("# Deepeval Evaluation Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"**Configuration:** TOP_K_RETRIEVAL={cfg_topk}, TOP_K_RERANK={cfg_rerank}"
    )
    lines.append(f"**QA Pairs:** {len(questions)}")
    if report_meta is not None:
        versions = report_meta["runner_versions"]
        if report_meta.get("ragas_skipped"):
            lines.append(
                "**Main score:** 未跑（--deepeval-only）——本报告只含 Agent 行为轴，"
                "无主分、无门禁语义"
            )
            lines.append(
                "**Runner versions:** "
                f"deepeval={versions['deepeval']}, metrics={versions['metrics']}"
            )
        else:
            lines.append(
                "**Caliber / Judge:** "
                f"caliber={report_meta['caliber']}, "
                f"judge={report_meta['judge_model']}, "
                f"model_arg_explicit={report_meta['model_arg_explicit']}"
            )
            lines.append(
                "**Runner versions:** "
                f"deepeval={versions['deepeval']}, metrics={versions['metrics']}"
            )
            lines.append(
                "**NaN cells:** "
                f"{report_meta['nan_cells']}/{report_meta['nan_denominator_cells']} "
                f"(rate={report_meta['nan_rate']})"
            )
    lines.append("")
```

`main()` 中 `runner_versions` 改为：

```python
            runner_versions={
                "deepeval": version("deepeval"),
                "metrics": "native",
            },
```

并删除 `from importlib.metadata import version` 之外的 ragas 调用。

- [ ] **Step 4: 更新 `test_deepeval_only.py` 的 runner_versions 与文案**

```python
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
```

```python
    assert "Main score:** 未跑" in text
```

- [ ] **Step 5: 跑测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/cli/test_eval_bridge_meta.py tests/cli/test_deepeval_only.py -v
```
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_eval_bridge_meta.py tests/cli/test_deepeval_only.py
git commit -m "feat(eval): 报告元数据改用 deep-eval-native 口径，删除温度分档"
```

---

### Task 3: 诊断指标的构造与评分接入

**Files:**
- Modify: `src/config/const.py:543-549`（新增诊断常量）
- Modify: `src/cli/eval_scoring.py`（新增 `build_diagnostic_metrics`）
- Modify: `src/cli/eval_ragas.py`（`_bridge_test_case`、`score_rows`、`run_evaluation`、`main`）
- Test: `tests/cli/test_eval_native_diagnostics.py`（新建）
- Test: `tests/cli/test_eval_bridge_scoring.py`（补诊断串行/截断）

**Interfaces:**
- Produces: `build_diagnostic_metrics(judge: Any) -> dict[str, BaseMetric]`，键固定 `contextual_relevancy` / `non_advice`
- Produces: `_bridge_test_case(row: dict, *, max_contexts: int | None = None)`
- Produces: `score_rows(rows, metrics, *, diagnostic_metrics=None, evidence=None)`
- Produces: `run_evaluation(..., *, evidence=None, diagnostic_metrics=None, sample_classes=None)`

- [ ] **Step 1: 加常量**

```python
# src/config/const.py，RAGAS_METRIC_COLUMNS 之后
# 诊断指标（不进主分/门禁）：逐题写入 detail_json，NaN 单独统计。
DIAGNOSTIC_METRIC_COLUMNS: tuple[str, ...] = (
    "contextual_relevancy",
    "non_advice",
)
# ContextualRelevancy 每条 context 单独调一次裁判；截断到前 8 条以控制成本与
# gather 的失败面。报告必须标注"仅判前 8 条（并集顺序，非最相关 8 条）"。
EVAL_RELEVANCY_MAX_CONTEXTS: int = 8
```

- [ ] **Step 2: 写失败测试：构造、截断、共享用例不被改写**

```python
# tests/cli/test_eval_native_diagnostics.py
import math

from deepeval.models import DeepEvalBaseLLM

from src.cli.eval_ragas import _bridge_test_case, score_rows
from src.cli.eval_scoring import build_diagnostic_metrics
from src.config.const import EVAL_RELEVANCY_MAX_CONTEXTS


class _FakeJudge(DeepEvalBaseLLM):
    """最小 DeepEval 裁判，避免真实网络调用。"""

    def load_model(self):
        """返回自身。"""
        return self

    def generate(self, prompt: str, **kwargs) -> str:
        """返回空对象。"""
        return "{}"

    async def a_generate(self, prompt: str, **kwargs) -> str:
        """异步版本。"""
        return "{}"

    def get_model_name(self) -> str:
        """固定模型名。"""
        return "fake-judge"


def test_diagnostic_metrics_use_project_judge():
    judge = _FakeJudge()
    metrics = build_diagnostic_metrics(judge)

    assert list(metrics) == ["contextual_relevancy", "non_advice"]
    for name, metric in metrics.items():
        assert metric.model is judge, f"{name} 必须显式收裁判"


def test_relevancy_truncates_without_mutating_shared_contexts():
    seen: dict[str, list[str]] = {}

    class _Capture:
        def __init__(self, key: str) -> None:
            self.key = key

        def measure(self, test_case) -> float:
            seen[self.key] = list(test_case.retrieval_context)
            return 0.5

    row = {
        "q_index": 0,
        "trace_id": "t0",
        "question": "q",
        "answer": "a",
        "ground_truth": "g",
        "contexts": [f"c{i}" for i in range(12)],
        "sample_class": "pure_kb",
    }
    metrics = {"context_precision": _Capture("precision")}
    diagnostics = {"contextual_relevancy": _Capture("relevancy")}

    out = score_rows([row], metrics, diagnostic_metrics=diagnostics)

    assert seen["precision"] == [f"c{i}" for i in range(12)]
    assert seen["relevancy"] == [f"c{i}" for i in range(EVAL_RELEVANCY_MAX_CONTEXTS)]
    assert out[0]["contextual_relevancy"] == 0.5
    assert out[0]["tool_correctness"] is None


def test_empty_sample_skips_all_metrics_and_diagnostics():
    class _Bomb:
        def measure(self, test_case) -> float:
            raise AssertionError("empty 样本不得调用任何指标")

    row = {
        "q_index": 0,
        "trace_id": "t0",
        "question": "q",
        "answer": "[ERROR] boom",
        "ground_truth": "g",
        "contexts": [],
        "sample_class": "empty",
    }

    out = score_rows(
        [row],
        {"faithfulness": _Bomb()},
        diagnostic_metrics={"contextual_relevancy": _Bomb(), "non_advice": _Bomb()},
    )

    assert math.isnan(out[0]["faithfulness"])
    assert math.isnan(out[0]["contextual_relevancy"])
    assert math.isnan(out[0]["non_advice"])
    assert out[0]["tool_correctness"] is None
    assert out[0]["error"] == "sample_class=empty"


def test_bridge_test_case_max_contexts_is_optional():
    row = {
        "question": "q",
        "answer": "a",
        "ground_truth": "g",
        "contexts": ["c0", "c1", "c2"],
    }
    assert _bridge_test_case(row).retrieval_context == ["c0", "c1", "c2"]
    assert _bridge_test_case(row, max_contexts=2).retrieval_context == ["c0", "c1"]
```

- [ ] **Step 3: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_native_diagnostics.py -v`
Expected: FAIL —— `build_diagnostic_metrics` 不存在。

- [ ] **Step 4: 实现诊断指标与评分通道**

```python
# src/cli/eval_scoring.py
def build_diagnostic_metrics(judge: Any) -> dict:
    """构造两项诊断指标（不进主分/门禁）。

    `ContextualRelevancy` 不需要 reference；`NonAdvice` 只判 input/actual_output。
    两者都必须显式传 `model=judge`，否则会初始化 DeepEval 默认的 gpt-3.5-turbo。

    Args:
        judge: `BridgeDeepEvalLLM` 包装后的裁判实例

    Returns:
        指标名 -> 诊断度量实例
    """
    # isort: off
    import src.config.settings  # noqa: F401
    from deepeval.metrics import ContextualRelevancyMetric, NonAdviceMetric
    # isort: on

    return {
        "contextual_relevancy": ContextualRelevancyMetric(model=judge),
        "non_advice": NonAdviceMetric(advice_types=["financial"], model=judge),
    }
```

```python
# src/cli/eval_ragas.py
def _bridge_test_case(row: dict, *, max_contexts: int | None = None):
    """把一行评测数据包成 LLMTestCase；可选截断 retrieval_context。

    Args:
        row: 含 question / answer / ground_truth / contexts 的行
        max_contexts: 仅诊断指标使用；None 表示使用全量 contexts

    Returns:
        LLMTestCase：四字段恒全部提供
    """
    from deepeval.test_case import LLMTestCase

    contexts = list(row["contexts"])
    if max_contexts is not None:
        contexts = contexts[:max_contexts]
    return LLMTestCase(
        input=row["question"],
        actual_output=row["answer"],
        expected_output=row["ground_truth"],
        retrieval_context=contexts,
    )
```

```python
# src/cli/eval_ragas.py
def score_rows(rows: list[dict], metrics: dict, *, diagnostic_metrics=None, evidence=None) -> list[dict]:
    """**串行**逐题逐指标打分（并发=1），诊断指标走独立通道。

    Args:
        rows: 每题一行（含 q_index / trace_id / question / answer /
            ground_truth / contexts / sample_class）
        metrics: `build_metrics` 产物（顺序即评分顺序）
        diagnostic_metrics: `build_diagnostic_metrics` 产物；不进主分
        evidence: 可选证据桶（本题评分前 `mark(trace_id)`）

    Returns:
        与输入同序的行副本，追加四指标、诊断值、`tool_correctness` 与 `error`
    """
    from src.config.const import EVAL_RELEVANCY_MAX_CONTEXTS

    out: list[dict] = []
    for row in rows:
        if evidence is not None:
            evidence.reset()
            evidence.mark(row.get("trace_id"))
        scored: dict = {"q_index": row["q_index"], "error": ""}
        errors: list[str] = []

        if row.get("sample_class") == SAMPLE_CLASS_EMPTY:
            for name in metrics:
                scored[name] = float("nan")
            if diagnostic_metrics is not None:
                for name in diagnostic_metrics:
                    scored[name] = float("nan")
            scored["tool_correctness"] = None
            scored["error"] = "sample_class=empty"
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

        scored["tool_correctness"] = None
        scored["error"] = ";".join(errors)
        out.append(scored)
    return out
```

`_score_metric_with_retry` 增加关键字参数：

```python
def _score_metric_with_retry(
    metric_name, metric, test_case, row, evidence=None, *, max_attempts: int | None = None
) -> tuple[float, str]:
```

```python
    if max_attempts is None:
        attempts = max(1, settings.RAGAS_METRIC_MAX_ATTEMPTS)
    else:
        attempts = max(1, max_attempts)
```

`run_evaluation` 签名与 rows 构造：

```python
def run_evaluation(
    questions,
    ground_truth,
    answers,
    contexts,
    metrics,
    trace_ids,
    *,
    evidence=None,
    diagnostic_metrics=None,
    sample_classes=None,
) -> _EvalResult:
```

```python
    rows = []
    for i, (q, a, g, c, t) in enumerate(
        zip(questions, answers, ground_truth, contexts, trace_ids, strict=True)
    ):
        sample_class = None
        if sample_classes is not None:
            sample_class = sample_classes[i]
        rows.append(
            {
                "q_index": i,
                "trace_id": t,
                "question": q,
                "answer": a,
                "ground_truth": g,
                "contexts": c,
                "sample_class": sample_class,
            }
        )
```

```python
    scored = score_rows(
        rows,
        metrics,
        diagnostic_metrics=diagnostic_metrics,
        evidence=evidence,
    )
```

`main()` 中：

```python
        diagnostic_metrics = None
        if ragas_enabled:
            diagnostic_metrics = build_diagnostic_metrics(judge_llm)
```

```python
            result = run_evaluation(
                questions,
                ground_truth,
                answers,
                contexts,
                metrics,
                trace_ids,
                evidence=judge_evidence,
                diagnostic_metrics=diagnostic_metrics,
                sample_classes=sample_classes,
            )
```

- [ ] **Step 5: 跑测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/cli/test_eval_native_diagnostics.py tests/cli/test_eval_bridge_scoring.py tests/cli/test_eval_bridge_retry.py -v
```
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/config/const.py src/cli/eval_scoring.py src/cli/eval_ragas.py tests/cli/test_eval_native_diagnostics.py tests/cli/test_eval_bridge_scoring.py
git commit -m "feat(eval): 接入 ContextualRelevancy / NonAdvice 诊断指标"
```

---

### Task 4: 诊断指标的报告、detail_json 与 empty 掩码

**Files:**
- Modify: `src/cli/eval_scoring.py`（`apply_sample_masks`）
- Modify: `src/cli/eval_ragas.py`（`_build_markdown_table`、`_build_diagnostic_section`、`save_markdown_report`、`_build_eval_detail`）
- Test: `tests/cli/test_eval_deepeval_detail.py`（新建）
- Test: `tests/cli/test_eval_scoring.py`（补诊断掩码）

**Interfaces:**
- Produces: `apply_sample_masks` 对 `empty` 行同时掩码 `DIAGNOSTIC_METRIC_COLUMNS`
- Produces: `_build_diagnostic_section(df) -> list[str]`
- Produces: `_build_eval_detail` 的逐题条目含 `contextual_relevancy` / `non_advice` / `tool_correctness=None` / `error`

- [ ] **Step 1: 写失败测试：empty 掩码 + detail_json 字段**

```python
# tests/cli/test_eval_deepeval_detail.py
import pandas as pd

from src.cli.eval_ragas import _build_eval_detail


def test_detail_includes_diagnostics_error_and_null_tool_correctness():
    df = pd.DataFrame(
        {
            "faithfulness": [0.8],
            "answer_relevancy": [0.7],
            "context_precision": [0.6],
            "context_recall": [0.5],
            "contextual_relevancy": [0.4],
            "non_advice": [1.0],
            "error": ["faithfulness:timeout"],
        }
    )

    detail, _ = _build_eval_detail(df, 1, None)

    assert detail[0]["contextual_relevancy"] == 0.4
    assert detail[0]["non_advice"] == 1.0
    assert detail[0]["tool_correctness"] is None
    assert detail[0]["error"] == "faithfulness:timeout"
```

```python
# tests/cli/test_eval_scoring.py 追加
def test_masks_diagnostics_for_empty_samples():
    df = pd.DataFrame(
        {
            "faithfulness": [0.9, 0.1],
            "contextual_relevancy": [0.5, 0.2],
            "non_advice": [1.0, 1.0],
        }
    )
    out = apply_sample_masks(df, [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY])
    assert pd.isna(out.loc[1, "contextual_relevancy"])
    assert pd.isna(out.loc[1, "non_advice"])
    assert "contextual_relevancy" in out.loc[1, "skipped"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_deepeval_detail.py tests/cli/test_eval_scoring.py -v`
Expected: FAIL —— `_build_eval_detail` 无诊断键；empty 行诊断列未被掩码。

- [ ] **Step 3: 实现掩码与报告**

```python
# src/cli/eval_scoring.py，apply_sample_masks 的 empty 分支
        if sample_classes[i] == SAMPLE_CLASS_EMPTY:
            for col in CONTEXT_METRIC_COLUMNS + DIAGNOSTIC_METRIC_COLUMNS:
                if col in out.columns:
                    out.loc[i, col] = float("nan")
                    marks.append(col)
```

同时把 `DIAGNOSTIC_METRIC_COLUMNS` 加入 `eval_scoring.py` 顶部的 `from src.config.const import (...)`。

```python
# src/cli/eval_ragas.py，_build_markdown_table
    header = ["Question"] + metric_cols
    if "error" in df.columns:
        header.append("error")
```

```python
    for i in range(len(df)):
        vals = [f"Q{i + 1}"] + [f"{df[m].iloc[i]:.4f}" for m in metric_cols]
        if "error" in df.columns:
            vals.append(str(df["error"].iloc[i]))
```

```python
def _build_diagnostic_section(df: pd.DataFrame) -> list[str]:
    """渲染「诊断指标」段；诊断不进主分/门禁。

    Args:
        df: 经 apply_sample_masks 处理的结果表

    Returns:
        Markdown 行列表；无诊断列时返回空列表
    """
    from src.config.const import DIAGNOSTIC_METRIC_COLUMNS

    cols = [c for c in DIAGNOSTIC_METRIC_COLUMNS if c in df.columns]
    if not cols:
        return []

    lines = ["## 诊断指标（不进主分/门禁）", ""]
    lines.append("> ContextualRelevancy 仅判前 8 条 context（并集顺序，非最相关 8 条）。")
    lines.append("")
    header = ["Question"] + cols
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for i in range(len(df)):
        vals = [f"Q{i + 1}"]
        for col in cols:
            value = df[col].iloc[i]
            if pd.isna(value):
                vals.append("N/A")
            else:
                vals.append(f"{value:.4f}")
        lines.append("| " + " | ".join(vals) + " |")

    pure = df[df["sample_class"] == SAMPLE_CLASS_PURE_KB]
    parts: list[str] = []
    for col in cols:
        mean = pure[col].mean()
        if pd.isna(mean):
            parts.append(f"{col}=NaN")
        else:
            parts.append(f"{col}={mean:.4f}")
    lines.append("")
    lines.append(f"**Diagnostic means (pure-KB):** {', '.join(parts)}")
    return lines
```

`save_markdown_report` 在主分行之后插入：

```python
        lines.append(f"**Main scores (pure-KB):** {avg_parts}")
        lines.append("")
        lines.extend(_build_diagnostic_section(df))
        lines.append("")
```

`_build_eval_detail` 增加：

```python
    from src.config.const import DIAGNOSTIC_METRIC_COLUMNS

    diag_cols = [col for col in DIAGNOSTIC_METRIC_COLUMNS if col in df.columns]
```

```python
        for col in diag_cols:
            v: Any = df[col].iloc[i]
            if pd.isna(v):
                entry[col] = float("nan")
            else:
                entry[col] = float(v)
        entry["tool_correctness"] = None
        if "error" in df.columns:
            entry["error"] = str(df["error"].iloc[i])
```

- [ ] **Step 4: 跑测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/cli/test_eval_deepeval_detail.py tests/cli/test_eval_scoring.py tests/cli/test_eval_ragas.py -v
```
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py src/cli/eval_ragas.py tests/cli/test_eval_deepeval_detail.py tests/cli/test_eval_scoring.py
git commit -m "feat(eval): 诊断指标进入报告与 detail_json，empty 样本统一掩码"
```

---

### Task 5: 退役出题链路与测试集加载器

**Files:**
- Create: `src/cli/testset_io.py`
- Delete: `src/cli/eval_ragas_generate.py`
- Modify: `src/cli/eval_ragas.py`（import、删除 `_ensure_vertexai_stub`）
- Modify: `src/api/ragas_generate.py`（改写 410）
- Modify: `src/cli/__init__.py` / `pyproject.toml`
- Test: `tests/test_tracing_order.py`、`tests/cli/test_deepeval_only.py`（patch 锚点）
- Delete: `tests/cli/test_generate_llm_context.py` / `test_generate_entity_anchor.py` / `test_generate_transforms.py` / `test_generate_guard.py` / `test_eval_ragas_personas.py` / `test_self_contained_gate.py`
- Test: `tests/api/test_ragas_generate.py`（新建）

**Interfaces:**
- Produces: `testset_io.load_testset_samples(kb_id, version=None) -> list[dict]`（完整 sample，含 `expected_tools`）
- Produces: `testset_io._load_latest_testset(kb_id, version=None) -> tuple[list[str], list[str]]`
- Produces: `POST /api/ragas/generate` → 410，`ResponseModel(code="GONE")`

- [ ] **Step 1: 创建 `testset_io.py`**

```python
"""测试集文件加载（评测侧唯一入口；不依赖 ragas）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import RAGAS_TESTSET_DIR  # Task 8 会统一改名为 EVAL_TESTSET_DIR


def load_testset_samples(kb_id: str, version: int | None = None) -> list[dict]:
    """加载指定知识库的测试集，返回完整 sample 列表。

    Args:
        kb_id: 知识库 UUID
        version: 指定版本号；None 表示自动取最新版本

    Returns:
        原始 sample 列表（含 `user_input` / `reference` / 可选的
        `expected_tools`），供评测与后续 v6 按需取字段

    Raises:
        FileNotFoundError: 没有找到该知识库的测试集文件
    """
    pattern = re.compile(rf"^testset_{re.escape(kb_id)}_v(\d+)\.json$")
    testset_dir = Path(RAGAS_TESTSET_DIR)

    if version is not None:
        target_file = testset_dir / f"testset_{kb_id}_v{version}.json"
        if not target_file.exists():
            raise FileNotFoundError(f"测试集文件不存在: {target_file}")
        latest_file = target_file
    else:
        max_version = 0
        latest_file = None
        if testset_dir.exists():
            for f in testset_dir.iterdir():
                m = pattern.match(f.name)
                if m:
                    ver = int(m.group(1))
                    if ver > max_version:
                        max_version = ver
                        latest_file = f
        if latest_file is None:
            raise FileNotFoundError(
                f"No testset found for kb_id={kb_id}. "
                "请先由 change deepeval-synthesizer-testset 生成测试集"
            )

    data = json.loads(latest_file.read_text(encoding="utf-8"))
    return list(data["samples"])


def _load_latest_testset(
    kb_id: str, version: int | None = None
) -> tuple[list[str], list[str]]:
    """加载测试集并适配评分的 (questions, ground_truth) 形状。

    Args:
        kb_id: 知识库 UUID
        version: 指定版本号；None 表示自动取最新版本

    Returns:
        (questions, ground_truth) 元组
    """
    samples = load_testset_samples(kb_id, version)
    questions = [s["user_input"] for s in samples]
    ground_truth = [s["reference"] for s in samples]
    return questions, ground_truth
```

- [ ] **Step 2: 改写 API 为 410 桩**

```python
"""RAGAS 出题 API 已下线：保留路由返回 410，避免调用方撞到 404/ImportError。"""

from fastapi import APIRouter
from pydantic import BaseModel

from src.api.schema import ResponseModel

router = APIRouter()


class RagasGenerateRequest(BaseModel):
    """兼容旧调用方的请求体（内容不再使用）。"""

    kb_name: str = ""
    size: int = 0


@router.post("/ragas/generate", response_model=ResponseModel, status_code=410)
async def ragas_generate(body: RagasGenerateRequest):
    """返回 410 与迁移指引。

    Args:
        body: 旧请求体（仅为兼容解析）

    Returns:
        ResponseModel：code=GONE，message 指向 DeepEval Synthesizer
    """
    return ResponseModel(
        code="GONE",
        message=(
            "RAGAS 出题已下线。前迁路径：DeepEval Synthesizer "
            "（deepeval.synthesizer.Synthesizer.generate_goldens_from_docs）。"
            "后续由 change deepeval-synthesizer-testset 提供。"
        ),
        data=None,
    )
```

- [ ] **Step 3: 删除出题模块、依赖与相关测试**

```bash
git rm src/cli/eval_ragas_generate.py
git rm tests/cli/test_generate_llm_context.py tests/cli/test_generate_entity_anchor.py \
  tests/cli/test_generate_transforms.py tests/cli/test_generate_guard.py \
  tests/cli/test_eval_ragas_personas.py tests/cli/test_self_contained_gate.py
```

`pyproject.toml` 删除：

```toml
    "ragas==0.3.9",
```

- [ ] **Step 4: 改评测入口的 import 与 CLI `--generate` 文案**

```python
# src/cli/eval_ragas.py
        from src.cli.testset_io import _load_latest_testset
```

删除 `_ensure_vertexai_stub` 的 import 与调用。

`--generate` 分支改为：

```python
    if args.generate:
        print(
            "error: --generate 已下线（出题生成入口不再经 CLI 提供）。\n"
            "前迁路径：DeepEval 自带的出题器 Synthesizer"
            "（deepeval.synthesizer.Synthesizer.generate_goldens_from_docs(...) -> List[Golden]），"
            "由后续 change deepeval-synthesizer-testset 提供。"
        )
        sys.exit(1)
```

- [ ] **Step 5: 更新测试锚点**

`tests/test_tracing_order.py`：

```python
    import src.cli.eval_ragas as cli
    import src.cli.testset_io as testset_io
```

```python
    monkeypatch.setattr(testset_io, "_load_latest_testset", _raise_file_not_found)
```

`tests/cli/test_deepeval_only.py`：

```python
    monkeypatch.setattr(
        "src.cli.testset_io._load_latest_testset",
        lambda kb_id, version=None: (["q"], ["a"]),
    )
```

删除 `_ensure_vertexai_stub` 与 `build_embeddings` 的 monkeypatch。

- [ ] **Step 6: 清理 `src/cli/__init__.py` 与 `src/cli/README.md` 的出题条目**

`src/cli/__init__.py` 删除这一行：

```python
  - eval_ragas_generate.py:      RAGAS 测试集自动生成（--generate 模式）
```

`src/cli/README.md` 删除 `eval_ragas_generate` 表格行与整段 `## eval_ragas_generate — 测试集生成模块`；保留的 `eval_ragas` 条目在 Task 7 统一改名为 `eval_deepeval`。

- [ ] **Step 7: 新建 API 410 测试**

```python
# tests/api/test_ragas_generate.py
def test_generate_endpoint_returns_410(auth_client):
    response = auth_client.post(
        "/api/ragas/generate", json={"kb_name": "kb", "size": 1}
    )
    assert response.status_code == 410
    assert response.json()["code"] == "GONE"
```

- [ ] **Step 8: 跑测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/test_tracing_order.py tests/cli/test_deepeval_only.py tests/api/test_ragas_generate.py -v
```
Expected: PASS。

- [ ] **Step 9: 提交**

```bash
git add -A src/cli/testset_io.py src/cli/eval_ragas.py src/api/ragas_generate.py src/cli/__init__.py pyproject.toml tests/test_tracing_order.py tests/cli/test_deepeval_only.py tests/api/test_ragas_generate.py
git commit -m "refactor(eval): 删除 RAGAS 出题链路，API 保留 410 桩"
```

---

### Task 6: 失败、429 与重试语义

**Files:**
- Modify: `src/cli/eval_ragas.py`（`_score_metric_with_retry`、`_retry_delay`）
- Modify: `src/infra/llm/provider/http_observability.py`（记录 `Retry-After`）
- Test: `tests/cli/test_eval_bridge_retry.py`（更新 429 用例）
- Test: `tests/infra/llm/provider/test_http_observability.py`（补 retry_after）

**Interfaces:**
- Produces: `_retry_delay(attempt: int, evidence) -> float`；429 优先用 `Retry-After`，否则指数 + jitter（上限 8s）
- Produces: `_score_metric_with_retry(..., max_attempts: int | None = None)`

- [ ] **Step 1: 写失败测试：429 必须重试而不是短路**

```python
# tests/cli/test_eval_bridge_retry.py，替换原 test_rate_limit_evidence_skips_outer_retry
def test_rate_limit_retries_with_retry_after(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.cli.eval_ragas.time.sleep", lambda s: sleeps.append(s))

    ev = HookEvidence()
    ev.mark("t0")
    ev.append(
        {
            "phase": "response",
            "status": 429,
            "json_ok": True,
            "finish_reason": [],
            "retry_after": "2",
        }
    )
    m = _Seq([None, 0.8])

    value, err = _score_metric_with_retry("faithfulness", m, object(), ROW, ev)

    assert m.calls == 2, "429 不再短路，必须由外层重试"
    assert value == pytest.approx(0.8)
    assert err == ""
    assert sleeps == [2.0], "优先使用 Retry-After"


def test_diagnostic_max_attempts_one(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.cli.eval_ragas.time.sleep", lambda s: sleeps.append(s))
    m = _Seq([None, 0.8])

    value, err = _score_metric_with_retry(
        "contextual_relevancy", m, object(), ROW, HookEvidence(), max_attempts=1
    )

    assert math.isnan(value)
    assert m.calls == 1
    assert sleeps == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_bridge_retry.py -v`
Expected: FAIL —— 429 仍返回 1 次。

- [ ] **Step 3: 实现退避与 Retry-After**

```python
# src/cli/eval_ragas.py
def _retry_delay(attempt: int, evidence) -> float:
    """计算第 attempt 次失败后的等待秒数。

    429 优先采用响应头 `Retry-After`；否则指数退避 + 0~0.25s jitter，上限 8s。

    Args:
        attempt: 1-based 尝试序号
        evidence: 可选证据桶（含 response 事件）

    Returns:
        等待秒数
    """
    if evidence is not None:
        for event in reversed(evidence.events):
            if event.get("status") != 429:
                continue
            retry_after = event.get("retry_after")
            if retry_after:
                try:
                    return min(float(retry_after), 30.0)
                except (TypeError, ValueError):
                    pass
    base = min(0.5 * (2 ** (attempt - 1)), 8.0)
    return base + random.uniform(0.0, 0.25)
```

```python
# _score_metric_with_retry 尾部
        if attempt >= attempts:
            break
        core_logging.log_event(
            Event.METRIC_RETRY,
            metric=metric_name,
            q_index=row.get("q_index"),
            trace_id=row.get("trace_id"),
            reason=last_note,
        )
        time.sleep(_retry_delay(attempt, evidence))
```

删除 429 短路分支：

```python
        if evidence is not None and evidence.non_2xx and 429 in evidence.status_codes:
            return float("nan"), ""
```

顶部加 `import random`。

- [ ] **Step 4: hook 记录 Retry-After**

```python
# src/infra/llm/provider/http_observability.py，on_response
        rec: dict = {"phase": "response", "status": response.status_code}
        rec["retry_after"] = response.headers.get("retry-after")
```

- [ ] **Step 5: 补 hook 测试**

```python
@pytest.mark.asyncio
async def test_response_hook_records_retry_after():
    ev = HookEvidence()
    client = _client_with(
        ev,
        lambda req: httpx.Response(429, headers={"Retry-After": "2"}),
    )
    async with client:
        await client.post("https://example.invalid/v1/chat/completions")

    assert ev.events[-1]["retry_after"] == "2"
```

- [ ] **Step 6: 跑测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/cli/test_eval_bridge_retry.py tests/infra/llm/provider/test_http_observability.py -v
```
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/cli/eval_ragas.py src/infra/llm/provider/http_observability.py tests/cli/test_eval_bridge_retry.py tests/infra/llm/provider/test_http_observability.py
git commit -m "fix(eval): 429 交由外层指数退避重试，诊断指标只尝试一次"
```

---

### Task 7: 模块与测试文件改名

**Files:**
- Rename: `src/cli/eval_ragas.py` → `src/cli/eval_deepeval.py`
- Rename tests（见命令）
- Modify: `src/cli/compare_retrieval.py`、`src/cli/README.md`、`src/infra/llm/tracing.py`、`tests/**`

**Interfaces:**
- Produces: CLI 入口 `python -m src.cli.eval_deepeval`
- Produces: 测试文件 `tests/cli/test_eval_deepeval*.py` / `tests/cli/test_eval_native_*.py`

- [ ] **Step 1: 改名**

```bash
git mv src/cli/eval_ragas.py src/cli/eval_deepeval.py
git mv tests/cli/test_eval_ragas.py tests/cli/test_eval_deepeval.py
git mv tests/cli/test_eval_ragas_tracing.py tests/cli/test_eval_deepeval_tracing.py
git mv tests/cli/test_eval_limit.py tests/cli/test_eval_deepeval_limit.py
for f in gate meta metrics nan retry scoring; do
  git mv "tests/cli/test_eval_bridge_${f}.py" "tests/cli/test_eval_native_${f}.py"
done
```

- [ ] **Step 2: 替换 import 与子进程命令**

```bash
grep -rl 'src\.cli\.eval_ragas\|src\.cli import eval_ragas' src tests docs/agents docs/openspec/specs README.md src/cli/README.md | while read -r f; do
  perl -pi -e 's/src\.cli\.eval_ragas/src.cli.eval_deepeval/g; s/src\.cli import eval_ragas/src.cli import eval_deepeval/g' "$f"
done
```

`src/cli/compare_retrieval.py` 的子进程命令：

```python
        ["python", "-m", "src.cli.eval_deepeval", "--kb-id", kb_id],
```

- [ ] **Step 3: 跑相关测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/cli tests/test_tracing_order.py -q
```
Expected: PASS（旧测试文件已改名、import 已同步）。

- [ ] **Step 4: 提交**

```bash
git add -A src/cli tests/cli src/infra/llm/tracing.py src/cli/compare_retrieval.py
git commit -m "refactor(eval): eval_ragas 模块与测试改名为 eval_deepeval / eval_native"
```

---

### Task 8: 配置改名、旧 env fail-fast 与日志事件

**Files:**
- Modify: `src/config/settings.py:161-220`
- Modify: `src/infra/llm/provider/spec.py:87-95`
- Modify: `src/models.py:112-131`
- Modify: `src/core/log_events.py:142` / `log_event_specs.py:241`
- Modify: `.env.example` / `.env.template`
- Modify: `tests/conftest.py`（如有 RAGAS 变量）
- Test: `tests/test_config_legacy_env.py`（新建）

**Interfaces:**
- Produces: `settings.JUDGE_MODEL` / `JUDGE_PROVIDER` / `JUDGE_TIMEOUT` / `JUDGE_HTTP_TIMEOUT_*` / `JUDGE_MAX_TOKENS` / `JUDGE_THINKING` / `EVAL_METRIC_MAX_ATTEMPTS` / `EVAL_DATA_DIR` / `EVAL_TESTSET_DIR` / `EVAL_REPORT_DIR` / `EVAL_LLM_CACHE_DIR` / `EVAL_USER_ID`
- Produces: 启动期检测到任何 `RAGAS_*` 抛 `RuntimeError`

- [ ] **Step 1: 写 fail-fast 测试（子进程）**

```python
# tests/test_config_legacy_env.py
import os
import subprocess
import sys
from pathlib import Path


def test_legacy_ragas_env_fails_fast():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["RAGAS_DATA_DIR"] = "data/ragas"

    proc = subprocess.run(
        [sys.executable, "-c", "import src.config.settings"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "RAGAS_DATA_DIR" in proc.stderr
    assert "EVAL_DATA_DIR" in proc.stderr
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/test_config_legacy_env.py -v`
Expected: FAIL —— 进程正常退出（当前仍读旧名）。

- [ ] **Step 3: 重命名 settings 变量**

在 `src/config/settings.py` 顶部、`load_dotenv()` 之后加入：

```python
# src/config/settings.py，load_dotenv() 之后
_LEGACY_ENV_MAP = {
    "RAGAS_LLM_MODEL": "JUDGE_MODEL",
    "RAGAS_PROVIDER": "JUDGE_PROVIDER",
    "RAGAS_JUDGE_TIMEOUT": "JUDGE_TIMEOUT",
    "RAGAS_HTTP_TIMEOUT_CONNECT": "JUDGE_HTTP_TIMEOUT_CONNECT",
    "RAGAS_HTTP_TIMEOUT_READ": "JUDGE_HTTP_TIMEOUT_READ",
    "RAGAS_HTTP_TIMEOUT_WRITE": "JUDGE_HTTP_TIMEOUT_WRITE",
    "RAGAS_HTTP_TIMEOUT_POOL": "JUDGE_HTTP_TIMEOUT_POOL",
    "RAGAS_METRIC_MAX_ATTEMPTS": "EVAL_METRIC_MAX_ATTEMPTS",
    "RAGAS_JUDGE_MAX_TOKENS": "JUDGE_MAX_TOKENS",
    "RAGAS_JUDGE_THINKING": "JUDGE_THINKING",
    "RAGAS_DATA_DIR": "EVAL_DATA_DIR",
    "RAGAS_TESTSET_DIR": "EVAL_TESTSET_DIR",
    "RAGAS_REPORT_DIR": "EVAL_REPORT_DIR",
    "RAGAS_LLM_CACHE_DIR": "EVAL_LLM_CACHE_DIR",
    "RAGAS_USER_ID": "EVAL_USER_ID",
    "RAGAS_METRIC_COLUMNS": "EVAL_METRIC_COLUMNS",
    "RAGAS_HTTP_MAX_RETRIES": "删除（裁判固定 max_retries=0）",
    "RAGAS_TEST_SIZE": "删除（出题路退役）",
    "RAGAS_DOC_WHITELIST": "删除（出题路退役）",
}
_LEGACY_RAGAS_ENV = sorted(key for key in os.environ if key.startswith("RAGAS_"))
if _LEGACY_RAGAS_ENV:
    hints = [f"{key} -> {_LEGACY_ENV_MAP.get(key, 'JUDGE_*/EVAL_*')}" for key in _LEGACY_RAGAS_ENV]
    raise RuntimeError(
        "检测到已废弃的 RAGAS_* 环境变量：" + "；".join(hints)
        + "（对照表见 docs/agents/deploy-runbook.md）"
    )
```

删除 settings 中的 `RAGAS_HTTP_MAX_RETRIES`、`RAGAS_TEST_SIZE`、`RAGAS_DOC_WHITELIST` 定义与相关注释。

变量改名：

| 旧 | 新 |
|---|---|
| `RAGAS_LLM_MODEL` | `JUDGE_MODEL` |
| `RAGAS_PROVIDER` | `JUDGE_PROVIDER` |
| `RAGAS_JUDGE_TIMEOUT` | `JUDGE_TIMEOUT` |
| `RAGAS_HTTP_TIMEOUT_CONNECT` | `JUDGE_HTTP_TIMEOUT_CONNECT` |
| `RAGAS_HTTP_TIMEOUT_READ` | `JUDGE_HTTP_TIMEOUT_READ` |
| `RAGAS_HTTP_TIMEOUT_WRITE` | `JUDGE_HTTP_TIMEOUT_WRITE` |
| `RAGAS_HTTP_TIMEOUT_POOL` | `JUDGE_HTTP_TIMEOUT_POOL` |
| `RAGAS_METRIC_MAX_ATTEMPTS` | `EVAL_METRIC_MAX_ATTEMPTS` |
| `RAGAS_JUDGE_MAX_TOKENS` | `JUDGE_MAX_TOKENS` |
| `RAGAS_JUDGE_THINKING` | `JUDGE_THINKING` |
| `RAGAS_DATA_DIR` | `EVAL_DATA_DIR`（默认 `data/deepeval`） |
| `RAGAS_TESTSET_DIR` | `EVAL_TESTSET_DIR`（默认 `data/deepeval/testset`） |
| `RAGAS_REPORT_DIR` | `EVAL_REPORT_DIR`（默认 `data/deepeval/reports`） |
| `RAGAS_LLM_CACHE_DIR` | `EVAL_LLM_CACHE_DIR`（默认 `data/deepeval/llm_cache`） |
| `RAGAS_USER_ID` | `EVAL_USER_ID` |
| `RAGAS_METRIC_COLUMNS` | `EVAL_METRIC_COLUMNS` |

删除 `RAGAS_HTTP_MAX_RETRIES` / `RAGAS_TEST_SIZE` / `RAGAS_DOC_WHITELIST`。

全局替换：

```bash
python - <<'PY'
from pathlib import Path

mapping = [
    ("RAGAS_HTTP_TIMEOUT_", "JUDGE_HTTP_TIMEOUT_"),
    ("RAGAS_JUDGE_TIMEOUT", "JUDGE_TIMEOUT"),
    ("RAGAS_JUDGE_MAX_TOKENS", "JUDGE_MAX_TOKENS"),
    ("RAGAS_JUDGE_THINKING", "JUDGE_THINKING"),
    ("RAGAS_LLM_MODEL", "JUDGE_MODEL"),
    ("RAGAS_PROVIDER", "JUDGE_PROVIDER"),
    ("RAGAS_METRIC_MAX_ATTEMPTS", "EVAL_METRIC_MAX_ATTEMPTS"),
    ("RAGAS_DATA_DIR", "EVAL_DATA_DIR"),
    ("RAGAS_TESTSET_DIR", "EVAL_TESTSET_DIR"),
    ("RAGAS_REPORT_DIR", "EVAL_REPORT_DIR"),
    ("RAGAS_LLM_CACHE_DIR", "EVAL_LLM_CACHE_DIR"),
    ("RAGAS_USER_ID", "EVAL_USER_ID"),
    ("RAGAS_METRIC_COLUMNS", "EVAL_METRIC_COLUMNS"),
]
for path in list(Path("src").rglob("*.py")) + list(Path("tests").rglob("*.py")):
    text = path.read_text(encoding="utf-8")
    new = text
    for old, repl in mapping:
        new = new.replace(old, repl)
    if new != text:
        path.write_text(new, encoding="utf-8")
PY
```

- [ ] **Step 4: 同步 provider 注册与模型门面**

`src/infra/llm/provider/spec.py`：

```python
    "judge": "JUDGE_PROVIDER",
```

```python
    "judge": "JUDGE_MODEL",
```

`src/models.py` 的 `get_judge_llm`：

```python
        timeout=settings.JUDGE_TIMEOUT,
```

并更新 docstring 中的 `JUDGE_PROVIDER` / `JUDGE_MODEL`。

- [ ] **Step 5: env 模板只写新名**

`.env.example`：

```dotenv
# 裁判请求超时秒数（思考模型偶尔 >30s）
JUDGE_TIMEOUT=300
# 裁判模型：可选覆盖（不填 → registry.models[judge] 默认）
JUDGE_MODEL=
# 裁判 provider：可选，缺省独立为 dashscope，不跟随 LLM_PROVIDER
JUDGE_PROVIDER=
```

`.env.template`：

```dotenv
# ====== 评测裁判（judge）======
# 裁判请求超时秒数；思考模型偶尔 >30s，超时不足会把分数变成失败
JUDGE_TIMEOUT=300
```

- [ ] **Step 6: 日志事件改名**

`src/core/log_events.py`：

```python
    JUDGE_MODEL_MISSING = "judge model missing"
```

`src/core/log_event_specs.py`：

```python
    "judge model missing": EventSpec("judge model missing", "cli", "error"),
```

- [ ] **Step 7: 跑测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/test_config_legacy_env.py tests/infra/llm/provider tests/cli -q
```
Expected: PASS。若本地 `.env` 仍有生效的 `RAGAS_*`，按 runbook 先改名再跑。

- [ ] **Step 8: 提交**

```bash
git add -A src tests .env.example .env.template
git commit -m "refactor(config): RAGAS_* 改名 JUDGE_*/EVAL_*，旧 env 启动期 fail-fast"
```

---

### Task 9: 数据目录、compose、部署脚本与 session id

**Files:**
- Rename: `data/ragas/` → `data/deepeval/`（文件系统）
- Modify: `docker-compose.yml:175`、`docker-compose.image.yml:227`
- Modify: `scripts/deploy/deploy.sh:339`
- Modify: `src/cli/eval_deepeval.py`（`--session-id` 默认值）
- Modify: `src/cli/README.md`（命令/默认值）
- Modify: `README.md`、`docs/agents/deploy-runbook.md`（路径）

**Interfaces:**
- Produces: 默认数据根 `data/deepeval/`；Langfuse `--session-id` 默认 `deepeval_eval_session`

- [ ] **Step 1: 迁移数据目录**

```bash
# 确认没有评测进程占用
pgrep -af 'src.cli.eval_' || true
mv data/ragas data/deepeval
rm -f data/deepeval/kg_*.json
rm -rf data/deepeval/llm_cache
ls data/deepeval
```

Expected: `testset/`、`reports/`、`README.md` 仍在；`kg_*.json`、`llm_cache` 已删除。

- [ ] **Step 2: 改两份 compose 挂载**

`docker-compose.yml`：

```yaml
      - ./data/deepeval:/app/data/deepeval
```

`docker-compose.image.yml`：

```yaml
      - ./data/deepeval:/app/data/deepeval
```

同时把文件头注释里的 `data/ragas/` 改为 `data/deepeval/`。

- [ ] **Step 3: 改部署脚本（只建新目录，不删旧目录）**

```bash
mkdir -p data/deepeval   # 运行期目录；旧 data/ragas 不自动删除，确认后再人工清理
```

- [ ] **Step 4: 改 session id 默认值**

```python
    parser.add_argument(
        "--session-id",
        type=str,
        default="deepeval_eval_session",
        help="评估用的会话 ID（默认: deepeval_eval_session）",
    )
```

`src/cli/README.md` 同步。

- [ ] **Step 5: 跑一次路径校验**

```bash
python -m src.cli.eval_deepeval --list-kbs
```
Expected: 正常列出知识库；不因目录改名报错。

- [ ] **Step 6: 提交**

```bash
git add docker-compose.yml docker-compose.image.yml scripts/deploy/deploy.sh src/cli/eval_deepeval.py src/cli/README.md README.md docs/agents/deploy-runbook.md
git commit -m "refactor(eval): 数据目录迁移 data/deepeval，session id 改 deepeval_eval_session"
```

---

### Task 10: `--run-type`、baseline API 语义与前端三态

**Files:**
- Modify: `src/cli/eval_deepeval.py`（`parse_args`、`_save_eval_report`、`main`）
- Modify: `src/api/kb_eval.py`（`_build_eval_payload`）
- Modify: `deploy/nginx/html/index.html`（KB 徽标）
- Test: `tests/cli/test_eval_deepeval_run_type.py`（新建）
- Test: `tests/api/test_kb_eval.py`（补 baseline / 0.0）

**Interfaces:**
- Produces: `--run-type {manual,baseline}`（默认 `manual`）；`baseline` 与 `--gate` 互斥
- Produces: `_save_eval_report(..., run_type: str = "manual")`
- Produces: `kb_eval` 对 `run_type="baseline"` 返回 `passed=None`，0.0 原样返回

- [ ] **Step 1: 写失败测试：参数与 API**

```python
# tests/cli/test_eval_deepeval_run_type.py
import sys

import pytest

from src.cli import eval_deepeval


def _parse(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["eval_deepeval", *argv])
    return eval_deepeval.parse_args()


def test_run_type_defaults_to_manual(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb"])
    assert args.run_type == "manual"
    assert args.session_id == "deepeval_eval_session"


def test_baseline_and_gate_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["eval_deepeval", "--kb-id", "kb", "--run-type", "baseline", "--gate"],
    )
    with pytest.raises(SystemExit) as excinfo:
        eval_deepeval.parse_args()
    assert excinfo.value.code == 2
```

```python
# tests/api/test_kb_eval.py 追加
from decimal import Decimal


def test_baseline_returns_passed_none(mock_app_service, auth_client):
    mock_app_service.get_latest_eval_report.return_value = make_eval_report(
        0.5, passed=False, run_type="baseline"
    )
    data = auth_client.post("/api/kbs/eval/latest", json={"kb_id": "kb-1"}).json()["data"]
    assert data["passed"] is None


def test_zero_scores_are_not_turned_into_none(mock_app_service, auth_client):
    mock_app_service.get_latest_eval_report.return_value = make_eval_report(
        0.0,
        passed=False,
        faithfulness=Decimal("0.0"),
        answer_relevancy=Decimal("0.0"),
    )
    data = auth_client.post("/api/kbs/eval/latest", json={"kb_id": "kb-1"}).json()["data"]
    assert data["faithfulness"] == 0.0
    assert data["answer_relevancy"] == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_deepeval_run_type.py tests/api/test_kb_eval.py -v`
Expected: FAIL —— 无 `--run-type`；baseline 返回 `false`；0.0 变成 `None`。

- [ ] **Step 3: 加 CLI 参数与落库参数**

```python
    parser.add_argument(
        "--run-type",
        choices=("manual", "baseline"),
        default="manual",
        help="运行类型：baseline=新口径首轮（不判门禁），manual=常规回归",
    )
```

```python
    if args.deepeval_only and args.gate:
        parser.error("--deepeval-only 与 --gate 不能同用：该模式没有主分可判")
    if args.run_type == "baseline" and args.gate:
        parser.error("--run-type baseline 与 --gate 不能同用：baseline 不判门禁")
```

`_save_eval_report`：

```python
def _save_eval_report(
    kb_id: str,
    df: pd.DataFrame,
    qa_count: int,
    output_path: str,
    retrieval_details: list[list[dict]] | None = None,
    sample_classes: list[str] | None = None,
    run_type: str = "manual",
) -> None:
```

```python
                run_type=run_type,
```

`main()` 调用：

```python
            _save_eval_report(
                kb_id,
                result.to_pandas(),
                len(questions),
                output_path,
                retrieval_details,
                sample_classes,
                run_type=args.run_type,
            )
```

同时给 `tests/test_tracing_order.py` 的 `argparse.Namespace` 补 `run_type="manual"`（否则 `main()` 读 `args.run_type` 会 `AttributeError`）。

- [ ] **Step 4: 改 API 组装**

```python
from typing import Any

from src.config.const import EVAL_PASS_THRESHOLD


def _optional_float(value: Any) -> float | None:
    """把 DB 数值转成 float；None 保持 None（0.0 不得被吞）。

    Args:
        value: Decimal / float / None

    Returns:
        float 或 None
    """
    if value is None:
        return None
    return float(value)


def _build_eval_payload(report: dict) -> dict:
    """把评估报告实体字段规整为接口响应 data。

    Args:
        report: 报告实体字段（见 `AppService.get_latest_eval_report`）

    Returns:
        响应 data；baseline 的 passed 为 None，0.0 分值保持 0.0
    """
    passed: bool | None
    if report.get("run_type") == "baseline":
        passed = None
    else:
        passed = report["passed"]

    return {
        "eval_date": report["eval_date"].isoformat()
        if hasattr(report["eval_date"], "isoformat")
        else str(report["eval_date"]),
        "faithfulness": _optional_float(report["faithfulness"]),
        "answer_relevancy": _optional_float(report["answer_relevancy"]),
        "context_precision": _optional_float(report["context_precision"]),
        "context_recall": _optional_float(report["context_recall"]),
        "overall_score": _optional_float(report["overall_score"]),
        "passed": passed,
        "qa_count": report["qa_count"],
        "run_type": report["run_type"],
        "pass_threshold": EVAL_PASS_THRESHOLD,
    }
```

- [ ] **Step 5: 前端 KB 徽标三态**

CSS（`.ragas-summary .ragas-fail` 之后）：

```css
  .ragas-summary .ragas-neutral { color: var(--text-muted); font-weight: 600; }
```

HTML 文案：

```html
              <span>评测</span>
```

JS：

```javascript
        if (passed === null) {
            iconEl.className = 'ragas-neutral';
            iconEl.textContent = '未标定';
        } else if (passed) {
            iconEl.className = 'ragas-pass';
            iconEl.textContent = '✓';
        } else {
            iconEl.className = 'ragas-fail';
            iconEl.textContent = '✗';
        }
```

- [ ] **Step 6: 跑测试**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/cli/test_eval_deepeval_run_type.py tests/api/test_kb_eval.py tests/cli/test_deepeval_only.py -v
```
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/cli/eval_deepeval.py src/api/kb_eval.py deploy/nginx/html/index.html tests/cli/test_eval_deepeval_run_type.py tests/api/test_kb_eval.py
git commit -m "feat(eval): 新增 --run-type，baseline 不判门禁，前端显示未标定"
```

---

### Task 11: 报告/观测命名收尾

**Files:**
- Modify: `src/config/const.py`（`LANGFUSE_SCORE_SUFFIX`）
- Modify: `src/cli/eval_deepeval.py`（默认 CSV 名、`--behaviour-only` 别名、注释/文案）
- Test: `tests/cli/test_langfuse_score_names.py`（补常量断言）
- Test: `tests/cli/test_deepeval_only.py`（别名）

**Interfaces:**
- Produces: `LANGFUSE_SCORE_SUFFIX = "@deep-eval-native"`；默认输出 `deepeval_eval_<ts>.csv`；`--behaviour-only` 与 `--deepeval-only` 同义

- [ ] **Step 1: 写失败测试**

```python
def test_behaviour_only_is_an_alias(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb", "--behaviour-only"])
    assert args.deepeval_only is True


def test_langfuse_suffix_is_native():
    from src.config.const import LANGFUSE_SCORE_SUFFIX

    assert LANGFUSE_SCORE_SUFFIX == "@deep-eval-native"
```

- [ ] **Step 2: 实现**

```python
LANGFUSE_SCORE_SUFFIX: str = "@deep-eval-native"
```

```python
    parser.add_argument(
        "--deepeval-only",
        "--behaviour-only",
        action="store_true",
        dest="deepeval_only",
        help="只跑 Agent 行为轴，跳过四指标（无主分/门禁；不写 eval_report）",
    )
```

```python
        output_path = (
            args.output or f"{settings.EVAL_REPORT_DIR}/deepeval_eval_{timestamp}.csv"
        )
```

同步 `--output` help、`--size` help、`--model` help 中的旧名与旧文案。

- [ ] **Step 3: 跑测试**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_langfuse_score_names.py tests/cli/test_deepeval_only.py -v`
Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add src/config/const.py src/cli/eval_deepeval.py tests/cli/test_langfuse_score_names.py tests/cli/test_deepeval_only.py
git commit -m "chore(eval): 口径后缀/报告文件名/CLI 别名收尾"
```

---

### Task 12: ADR-0027 与活动文档同步

**Files:**
- Create: `docs/adr/0027-deepeval-native-metrics-ragas-retirement.md`
- Modify: `docs/adr/0026-*.md` / `0022-*.md` / `0023-*.md`（仅 `Status` 一行）
- Modify: `docs/adr/README.md`（索引）
- Rename: `docs/agents/ragas-eval-ledger.md` → `docs/agents/eval-ledger.md`
- Modify: `CLAUDE.md`、`README.md`、`docs/agents/*.md`、`docs/openspec/specs/*.md`、`src/cli/README.md`、`src/api/README.md`

**Interfaces:**
- Produces: ADR-0027 完全取代 0026 / 0022 / 0023，并重述仍生效的约束
- Produces: 活动文档不再指向 `eval_ragas` / `data/ragas` / `RAGAS_*`

- [ ] **Step 1: 写 ADR-0027**

```markdown
# ADR-0027：评测主分改用 DeepEval 原生四指标，RAGAS 全链路退役

- **Status**：Accepted
- **Date**：2026-10-17
- **Deciders**：用户（拍板脱钩、改名与诊断指标范围）；Claude（spike、源码核对、实现）
- **Supersedes**：ADR-0026（完全）、ADR-0022（完全）、ADR-0023（完全）
- **关系**：落实 change `deepeval-native-metrics`；旧 ADR 中仍生效的约束在「继续生效的约束」重述。

## 背景与问题

评测主分原先由 `deepeval.metrics.ragas` 桥驱动 ragas 0.3.9 的经典四指标。
桥带来三处不可控：温度按 `n` 被改写、框架层 429 重试不可配、失败被吞成 NaN。
DeepEval 4.2.9 已原生提供输入同构的四指标，且不需要 embeddings；继续保留桥会
让裁判策略、依赖与命名长期分叉。

## 决策

### 决策 1：四指标改用 DeepEval 原生实现

`AnswerRelevancyMetric` / `FaithfulnessMetric` / `ContextualPrecisionMetric` /
`ContextualRecallMetric`，全部显式 `model=BridgeDeepEvalLLM(judge)`；`build_metrics`
删除 `embeddings` 参数。评分编排仍为题级串行。

**理由**：原生指标输入契约与桥一致；删掉 ragas 后少一层不可控包装。
**代价**：分数与旧口径不可比，必须重跑 baseline。

### 决策 2：裁判温度显式 0，SDK 重试固定 0

`build_bridge_judge` 显式 `temperature=0`、`max_retries=0`，并剥离 `LLM_KWARGS`
中的 `top_p`。

**理由**：删 ragas 后不再有按 `n` 分档；不设温度会落到 provider 默认值，
裁判 JSON 判定不可复现。SDK 重试可配会与外层有界重试叠乘。
**代价**：旧桥的 0.01/0.3 分数不可复用。

### 决策 3：诊断指标不参与主分

新增 `ContextualRelevancy`（仅判前 8 条 context）与 `NonAdvice(["financial"])`；
`ToolCorrectness` 等 v6 标注 `expected_tools` 后再启用。诊断值写入
`detail_json`，NaN 单独统计，不进入 `EVAL_METRIC_COLUMNS` 与门禁。

**理由**：诊断指标回答"检索是否贴题 / 是否越界给建议"，与主分的"答案质量"
不是同一问题；混入主分会改变口径。
**代价**：Relevancy 截断后不是全量语义，报告必须标注。

### 决策 4：彻底退役 RAGAS

删除 `ragas==0.3.9`、桥 import、出题模块与 `/api/ragas/generate` 的实现；
路由保留并返回 410。测试集加载器迁入 `src/cli/testset_io.py`。

**理由**：项目不再在任何运行路径使用 ragas；保留依赖与出题代码会误导维护者。
**代价**：出题能力暂时空缺，由后续 change `deepeval-synthesizer-testset` 承接。

### 决策 5：命名与路径全量切换

`eval_ragas.py` → `eval_deepeval.py`；`data/ragas/` → `data/deepeval/`；
`RAGAS_*` → `JUDGE_*` / `EVAL_*`；台账改 `eval-ledger.md`；Langfuse session
默认 `deepeval_eval_session`；score 后缀 `@deep-eval-native`。

**理由**：用户要求"项目里不再有 RAGAS"；改名与语义切换分开提交，便于回滚。
**代价**：部署侧 `.env` 必须一次性切换；旧 env 启动期 fail-fast。

### 决策 6：baseline 不判门禁

新口径首轮以 `--run-type baseline` 落库，`kb_eval` 返回 `passed=None`，
前端显示"未标定"；v6 阈值标定前所有全量运行都用 baseline。

**理由**：换尺子后 0.70 阈值未标定，判红/绿会误导。
**代价**：`eval_report.passed` 仍写 False，靠 `run_type` 约定区分。

## 继续生效的约束（来自被取代 ADR）

- 细粒度 HTTP 超时落在 `http_async_client`；hooks 必须 `async def` 且
  `await response.aread()`；`max_tokens` / `thinking` 经 provider 层透传；
  `max_retries=0`（ADR-0026）。
- 评估检索镜像生产、搜整库、不按白名单收窄（ADR-0023 的核心决策）；
  出题白名单随出题路删除，但评估侧不设白名单继续有效。

## 证据

- spike：2026-10-17，2 题真裁判 4m56s；Q0 全 1.0，Q14 0.33/0.54/0.20。
- 源码：`deepeval/metrics/utils/__init__.py::score_qag_verdicts` 的
  `empty_score=1`；`ContextualRelevancyMetric.a_measure` 的 per-context gather。
- 代码：`src/cli/eval_scoring.py`、`src/cli/eval_deepeval.py`、
  `src/infra/llm/provider/factory.py`。

## 代价与复查条件

- 空 verdict 返回 1.0/0 而非 NaN，主分可能偏乐观；v6 全量分布产出后复核。
- Relevancy 仅判前 8 条，不是全量语义。
- 若原生指标在大规模运行中出现不可接受的 NaN 率，按 spec 的逃生门流程评估替代实现。
```

- [ ] **Step 2: 三份旧 ADR 加反向指针**

```markdown
# 0026
- **Status**：Superseded by 0027

# 0023
- **Status**：Superseded by 0027

# 0022
- **Status**：Superseded by 0027
```

0022 原决策 C 先由 0023 取代的历史关系写在 0027 的 `Supersedes` 行与索引备注里，旧的 `Status` 行保持机械校验可识别的 `Superseded by 0027`。

只改 `Status` 一行，正文不动。

- [ ] **Step 3: 更新 ADR 索引**

在 `docs/adr/README.md` 表格末尾追加：

```markdown
| [0027](0027-deepeval-native-metrics-ragas-retirement.md) | 评测改用 DeepEval 原生四指标；诊断指标不进主分；RAGAS 依赖/出题/命名全量退役；baseline 不判门禁 | Accepted | 完全取代 0026 / 0022 / 0023；重述仍生效的超时/hooks/检索范围约束 |
```

并把 0022/0023/0026 的 Status 列改为 `Superseded by 0027`。

- [ ] **Step 4: 台账改名**

```bash
git mv docs/agents/ragas-eval-ledger.md docs/agents/eval-ledger.md
```

同步 `CLAUDE.md` 文档表、`README.md`、`docs/agents/best-practices.md` 等引用：

```bash
grep -rl 'ragas-eval-ledger' CLAUDE.md README.md docs/agents | while read -r f; do
  perl -pi -e 's/ragas-eval-ledger/eval-ledger/g' "$f"
done
```

- [ ] **Step 5: 批量替换活动文档中的路径/命令/变量名**

```bash
# 先清掉已删除的出题模块引用，避免下面把 eval_ragas_generate 误替换成 eval_deepeval_generate
grep -rl 'eval_ragas_generate' README.md CLAUDE.md src docs/agents docs/openspec/specs | while read -r f; do
  perl -pi -e 's/eval_ragas_generate/eval_deepeval_generate（已删除）/g' "$f"
done

# 再替换活动文档；不碰 docs/research、docs/tmp、已归档 change、docs/superpowers/specs
# 以及主 spec evaluation-pipeline（由 openspec archive 应用 delta 时更新）
grep -rl 'eval_ragas\|data/ragas\|RAGAS_' README.md CLAUDE.md src/cli/README.md src/api/README.md docs/agents docs/openspec/specs | grep -v 'docs/openspec/specs/evaluation-pipeline' | while read -r f; do
  perl -pi -e 's/src\.cli\.eval_ragas/src.cli.eval_deepeval/g; s/eval_ragas/eval_deepeval/g; s/data\/ragas/data\/deepeval/g; s/RAGAS_METRIC_COLUMNS/EVAL_METRIC_COLUMNS/g; s/RAGAS_METRIC_MAX_ATTEMPTS/EVAL_METRIC_MAX_ATTEMPTS/g; s/RAGAS_LLM_MODEL/JUDGE_MODEL/g; s/RAGAS_PROVIDER/JUDGE_PROVIDER/g; s/RAGAS_JUDGE_TIMEOUT/JUDGE_TIMEOUT/g; s/RAGAS_HTTP_TIMEOUT_/JUDGE_HTTP_TIMEOUT_/g; s/RAGAS_DATA_DIR/EVAL_DATA_DIR/g; s/RAGAS_TESTSET_DIR/EVAL_TESTSET_DIR/g; s/RAGAS_REPORT_DIR/EVAL_REPORT_DIR/g; s/RAGAS_LLM_CACHE_DIR/EVAL_LLM_CACHE_DIR/g; s/RAGAS_USER_ID/EVAL_USER_ID/g' "$f"
done
```

> `docs/openspec/specs/evaluation-pipeline/spec.md` 由后续 `openspec archive` 应用本 change 的 delta 更新，不要在这里手改，否则归档时会产生冲突。

- [ ] **Step 6: 在 runbook 补迁移对照表**

```markdown
## RAGAS → DeepEval 配置迁移（2026-10-17）

| 旧名 | 新名 |
|---|---|
| `RAGAS_LLM_MODEL` | `JUDGE_MODEL` |
| `RAGAS_PROVIDER` | `JUDGE_PROVIDER` |
| `RAGAS_JUDGE_TIMEOUT` | `JUDGE_TIMEOUT` |
| `RAGAS_HTTP_TIMEOUT_*` | `JUDGE_HTTP_TIMEOUT_*` |
| `RAGAS_METRIC_MAX_ATTEMPTS` | `EVAL_METRIC_MAX_ATTEMPTS` |
| `RAGAS_DATA_DIR` | `EVAL_DATA_DIR` |
| `RAGAS_TESTSET_DIR` | `EVAL_TESTSET_DIR` |
| `RAGAS_REPORT_DIR` | `EVAL_REPORT_DIR` |
| `RAGAS_LLM_CACHE_DIR` | `EVAL_LLM_CACHE_DIR` |
| `RAGAS_USER_ID` | `EVAL_USER_ID` |
| `RAGAS_HTTP_MAX_RETRIES` | 删除（裁判固定 `max_retries=0`） |
| 出题专用 `RAGAS_TEST_*` / `RAGAS_DOC_WHITELIST` | 删除（随出题路退役） |

启动期检测到任何 `RAGAS_*` 会 fail-fast；先改共享 `.env` 再部署。
旧 `data/ragas/` 不自动删除，确认无引用后人工清理；历史 `report_path` 不迁移。
```

- [ ] **Step 7: 跑文档校验**

```bash
openspec validate deepeval-native-metrics --strict
POSTGRES_HOST=localhost python -m src.cli.check_docs
POSTGRES_HOST=localhost python -m src.cli.check_adr
```
Expected: 0 error（warn 可接受，但不得新增死链）。

- [ ] **Step 8: 提交**

```bash
git add -A docs CLAUDE.md README.md src/cli/README.md src/api/README.md
git commit -m "docs(adr): 新增 ADR-0027，退役 RAGAS 命名并同步活动文档"
```

---

### Task 13: 验收（静态闸门 + 冒烟 + baseline）

**Files:**
- Modify: `docs/agents/eval-ledger.md`（追加运行行）
- Modify: `docs/superpowers/plans/2026-10-17-deepeval-native-metrics.md`（验收记录）

**Interfaces:**
- Consumes: 前 12 个任务的全部产物
- Produces: `deep-eval-native / v5` 的 baseline 行与验收记录

- [ ] **Step 1: 静态闸门**

```bash
POSTGRES_HOST=localhost pytest tests/ -q
ruff format --check src/ tests/
ruff check src/ tests/
pyright src/
```
Expected: pytest 全绿；ruff 无错误；pyright 不新增 error（存量第三方误报可忽略）。

- [ ] **Step 2: 造 5 题冒烟子集**

```bash
python - <<'PY'
import json
from pathlib import Path

kb = "4a1dcb8bb340473c8149ead5b7f75873"
src = Path(f"data/deepeval/testset/testset_{kb}_v5.json")
dst = Path(f"data/deepeval/testset/testset_{kb}_v99.json")
data = json.loads(src.read_text(encoding="utf-8"))
indexes = [0, 2, 7, 13, 14]
data["samples"] = [data["samples"][i] for i in indexes]
dst.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
print(dst)
PY
```

- [ ] **Step 3: 跑 5 题并核对报告**

```bash
python -m src.cli.eval_deepeval --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 99 --limit 5 --run-type baseline
```

检查最新 `data/deepeval/reports/deepeval_eval_*.md`：
- 四指标行存在；
- 「诊断指标」段存在，含 `contextual_relevancy` / `non_advice` 与截断说明；
- 失败样本的 `error` 列可见；
- `empty` 样本诊断为 `N/A`（NaN 掩码）；
- 无 400/超时。

- [ ] **Step 4: 删除临时子集**

```bash
rm -f data/deepeval/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v99.json
```

- [ ] **Step 5: 跑 21 题 baseline**

```bash
python -m src.cli.eval_deepeval --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --run-type baseline
```

记录：四项均值、诊断均值、NaN 格数/分母、耗时。

- [ ] **Step 6: 校验 API 与前端**

```bash
POSTGRES_HOST=localhost pytest tests/api/test_kb_eval.py -q
```

Expected: baseline 用例返回 `passed=None`；0.0 用例返回 `0.0`（不是 `null`）。

数据库抽查（可选，确认落库值）：

```bash
docker compose exec postgres psql -U corporate_rag -d corporate_rag \
  -c "select run_type, passed, overall_score from eval_report order by created_at desc limit 1;"
```

Expected: `run_type=baseline`、`passed=f`（DB 写 False，由 API 转换）、`overall_score` 非空。

前端：打开知识库页，确认徽标显示"评测"与"未标定"（灰色），不显示 ✗；分块质量弹窗不受影响。

- [ ] **Step 7: 追加台账**

在 `docs/agents/eval-ledger.md` 运行台账末尾追加一行：

```markdown
| 6 | 2026-10-17 | `deep-eval-native / v5` | 21 | 填写四项均值 | 填写诊断均值 | 填写 NaN 格数/分母 | 未启用 | 首轮 baseline，`--run-type baseline`，不判门禁 |
```

- [ ] **Step 8: 跑最终静态闸门并提交验收记录**

```bash
POSTGRES_HOST=localhost pytest tests/ -q
git add docs/agents/eval-ledger.md docs/superpowers/plans/2026-10-17-deepeval-native-metrics.md
git commit -m "chore(eval): 记录 deep-eval-native baseline 验收结果"
```

---

## Self-Review

**Spec coverage**
- 原生四指标 / 删 embeddings：Task 1。
- 温度 0 / `max_retries=0` / 不传 `top_p`：Task 1。
- 诊断指标（Relevancy 截断 8、NonAdvice、ToolCorrectness N/A、detail_json 裸键、空样本掩码、不重试）：Task 3 / 4 / 6。
- 退役 RAGAS（删出题模块、loader、410 路由、删依赖）：Task 5。
- 429 外层重试 / 空回答预检 / error 可见：Task 6 / 3 / 4。
- 改名（模块、测试、数据、env、台账、session、score 后缀、报告名）：Task 7 / 8 / 9 / 11 / 12。
- baseline / `--run-type` / API `passed=None` / 0.0 不吞 / 前端三态：Task 10。
- ADR-0027 完全取代 + 重述有效约束：Task 12。
- 验收：Task 13。

**Placeholder scan**
- 唯一需要执行者按实测填写的是 Task 13 的台账数值（四项/诊断/NaN/耗时），这是运行时产物，不是设计占位。
- 所有代码步骤均给出可执行代码或完整命令。

**Type consistency**
- `build_metrics(judge)` 与 `build_diagnostic_metrics(judge)` 均接收 `BridgeDeepEvalLLM`。
- `score_rows` 的 `diagnostic_metrics` 是 `dict[str, BaseMetric]`；键固定为 `contextual_relevancy` / `non_advice`。
- `run_evaluation` 的 `sample_classes` 与 `generate_answers_and_contexts_with_behaviour` 返回的列表等长同序。
- `_save_eval_report` 的 `run_type` 默认 `manual`，所有旧调用点不传也保持原行为。
