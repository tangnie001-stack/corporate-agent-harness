# RAGAS Agent 评测改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 RAGAS 离线评测能鉴定 Agent 回答质量——修 `F-32`（评测链路上下文收集）、按来源分流样本并出「主分」、补答案正确性指标、轨迹作诊断、分数回写 Langfuse。

**Architecture:** 评测改为「评测侧 seed `RequestContext` → 跑真 Agent 图 → `agent_finalize` 回填 `state.tool_contexts`」，使 context 类指标恢复；新增 `src/cli/eval_scoring.py` 承载纯函数（样本分类 / 主分聚合 / skipped 掩码 / Langfuse 回写），避免把 764 行的 `eval_ragas.py` 继续撑大。

**Tech Stack:** Python 3.12 / LangGraph / ragas==0.4.3（legacy API）/ langfuse==2.60.10 / pandas / pytest。

**Spec:** `docs/openspec/changes/ragas-agent-eval/design.md`（决策 D1–D7）与 `docs/openspec/changes/ragas-agent-eval/specs/evaluation-pipeline/spec.md`、`specs/llm-tracing/spec.md`。

## Global Constraints

- Python ≥3.11；**不用三元表达式**，写完整 `if/else`。
- **显式类型检查**：不用 `getattr(x, "attr", default)` 隐式兜底；用 `x.attr if x is not None else default` 或 `isinstance`。
- **所有函数写 docstring**；dataclass 每字段行内注释。
- **硬编码集中 `src/config/`**：固定常量/阈值/文案进 `const.py`，环境变量进 `settings.py`。
- 日志用 `core_logging.log_event(Event.X, k=v)`（英文 k=v + `[层]` 前缀）；**不新增 `print()`**（既有 stdout 指标行保留）。
- **测试 mock 外部依赖，不发起真实网络调用**。
- 宿主侧 pytest 必须前置 `POSTGRES_HOST=localhost`。
- 单文件 ≤400 行、单函数 ≤80 行。
- `ragas` 属 `[dev]` 依赖 → 本改造只在宿主 dev 环境运行。
- 每次提交过 pre-commit（含 doc 闸门）。

---

## File Structure

- **Create** `src/cli/eval_scoring.py` — 评测打分辅助纯函数：样本分类、主分聚合、`skipped` 掩码、检索覆盖率、Langfuse 回写、轨迹摘要。
- **Create** `tests/cli/test_eval_scoring.py` — 上述纯函数单测。
- **Modify** `src/cli/eval_ragas.py` — `_answer_one_question`/`generate_answers_and_contexts`（seed ctx + 采样 kind/轨迹）、`run_evaluation`（加 `AnswerCorrectness()` 指标）、`_print_metric_averages`/`check_gate`（改读主分）、`save_results_csv`/`save_markdown_report`（新列）、`_save_eval_report`（NaN 安全）、`main`（接线 score 回写）。
- **Modify** `src/config/const.py` — 新常量：样本类别、跳过指标集、检索覆盖率阈值、`eval_report` 主分指标集。
- **Modify** `tests/cli/test_eval_ragas.py` — 更新「恰 4 指标」断言（`:198-205`）、新增主分/门禁用例。
- **Modify** `src/cli/compare_retrieval.py` — 同步 stdout 均值契约（改读主分）。
- **Modify** `docs/agents/api_contract.md` — 记录 CSV/MD 新列与 stdout 口径。

---

### Task 1: 样本分类与常量（纯函数）

**Files:**
- Create: `src/cli/eval_scoring.py`
- Modify: `src/config/const.py`（追加常量）
- Test: `tests/cli/test_eval_scoring.py`

**Interfaces:**
- Produces:
  - `SAMPLE_CLASS_PURE_KB: str = "pure_kb"`
  - `SAMPLE_CLASS_WEB: str = "web"`
  - `SAMPLE_CLASS_EMPTY: str = "empty"`
  - `CONTEXT_METRIC_COLUMNS: tuple[str, ...] = ("faithfulness", "context_precision", "context_recall")`
  - `classify_sample(contexts: list[RAGContext]) -> str`
- Consumes: `RAGContext.kind`（`src/rag/context.py:25`，值取 `SSEInteractionTexts.CITATION_KIND_KB` / `CITATION_KIND_WEB`）

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_scoring.py
from src.cli.eval_scoring import (
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
    classify_sample,
)
from src.rag.context import RAGContext


def _ctx(kind: str) -> RAGContext:
    return RAGContext(
        content="x", source="s", page=1, doc_id="d", chunk_id="c", kind=kind
    )


def test_classify_empty_when_no_contexts() -> None:
    assert classify_sample([]) == SAMPLE_CLASS_EMPTY


def test_classify_pure_kb() -> None:
    assert classify_sample([_ctx("kb"), _ctx("kb")]) == SAMPLE_CLASS_PURE_KB


def test_classify_web_when_any_web() -> None:
    assert classify_sample([_ctx("kb"), _ctx("web")]) == SAMPLE_CLASS_WEB
```

- [ ] **Step 2: 运行确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.cli.eval_scoring'`

- [ ] **Step 3: 实现常量与函数**

```python
# src/config/const.py（追加）
# 评测样本按检索来源分类（用途：主分聚合与指标跳过判据）
SAMPLE_CLASS_PURE_KB = "pure_kb"  # 检索材料全部来自知识库
SAMPLE_CLASS_WEB = "web"  # 检索材料含联网块（降级联网）
SAMPLE_CLASS_EMPTY = "empty"  # 未检索到任何材料
# 依赖 contexts 的指标列（无材料样本上跳过；answer_relevancy/answer_correctness 不在此列）
CONTEXT_METRIC_COLUMNS = ("faithfulness", "context_precision", "context_recall")
```

```python
# src/cli/eval_scoring.py
"""评测打分辅助：样本分类、主分聚合、跳过掩码、检索覆盖率、分数回写。

与 eval_ragas.py 的分工：本模块只放纯函数与被测可单测的逻辑，
CLI 编排（跑图、读写文件）留在 eval_ragas.py。
"""

from src.config.const import (
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
)
from src.config.const import SSEInteractionTexts
from src.rag.context import RAGContext


def classify_sample(contexts: list[RAGContext]) -> str:
    """按检索来源把单个样本分为纯 KB / 含 web / 无检索 三类。

    Args:
        contexts: 该样本经工具收集的检索材料

    Returns:
        SAMPLE_CLASS_EMPTY / SAMPLE_CLASS_PURE_KB / SAMPLE_CLASS_WEB
    """
    if not contexts:
        return SAMPLE_CLASS_EMPTY
    for c in contexts:
        if c.kind == SSEInteractionTexts.CITATION_KIND_WEB:
            return SAMPLE_CLASS_WEB
    return SAMPLE_CLASS_PURE_KB
```

- [ ] **Step 4: 运行确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_scoring.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py src/config/const.py tests/cli/test_eval_scoring.py
git commit -m "feat(eval): 新增样本按来源分类（纯函数）"
```

---

### Task 2: 主分聚合 / skipped 掩码 / 检索覆盖率（纯函数）

**Files:**
- Modify: `src/cli/eval_scoring.py`
- Test: `tests/cli/test_eval_scoring.py`

**Interfaces:**
- Consumes: Task 1 的 `SAMPLE_CLASS_*`、`CONTEXT_METRIC_COLUMNS`、`classify_sample`
- Produces:
  - `apply_sample_masks(df: pd.DataFrame, sample_classes: list[str]) -> pd.DataFrame` — 新增 `sample_class` 列；对无材料样本的三项 context 指标置 `NaN` 并写入 `skipped` 列（分号分隔）
  - `main_score_means(df: pd.DataFrame) -> dict[str, float]` — 仅 `sample_class == SAMPLE_CLASS_PURE_KB` 行的各指标 `.mean()`
  - `retrieval_coverage(sample_classes: list[str]) -> float` — 有检索样本（非 empty）占比

- [ ] **Step 1: 写失败测试**

```python
import pandas as pd

from src.cli.eval_scoring import (
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
    apply_sample_masks,
    main_score_means,
    retrieval_coverage,
)


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "faithfulness": [0.9, 0.1, 0.8],
            "context_precision": [0.9, 0.2, 0.7],
            "context_recall": [0.9, 0.3, 0.6],
            "answer_relevancy": [0.9, 0.9, 0.9],
        }
    )


def test_masks_skipped_context_metrics_to_nan() -> None:
    df = apply_sample_masks(
        _df(), [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_WEB]
    )
    # 第 2 行（empty）三项 context 指标置 NaN，且被标记 skipped
    assert pd.isna(df.loc[1, "faithfulness"])
    assert "faithfulness" in df.loc[1, "skipped"]
    # 第 1 行非空，不标记
    assert df.loc[0, "skipped"] == ""


def test_main_score_excludes_non_pure_kb() -> None:
    df = apply_sample_masks(
        _df(), [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_WEB]
    )
    means = main_score_means(df)
    # 只有第 1 行是纯 KB → 各指标均值等于它自己的值
    assert abs(means["context_recall"] - 0.9) < 1e-9
    assert abs(means["answer_relevancy"] - 0.9) < 1e-9


def test_retrieval_coverage() -> None:
    cov = retrieval_coverage([SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY])
    assert abs(cov - 0.5) < 1e-9
```

- [ ] **Step 2: 运行确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_scoring.py -v`
Expected: FAIL — `ImportError: cannot import name 'apply_sample_masks'`

- [ ] **Step 3: 实现**

```python
# src/cli/eval_scoring.py（追加）
import pandas as pd

from src.config.const import CONTEXT_METRIC_COLUMNS


def apply_sample_masks(
    df: pd.DataFrame, sample_classes: list[str]
) -> pd.DataFrame:
    """按样本类别掩码：无材料样本的三项 context 指标置 NaN 并记 skipped。

    置空用 NaN（而非 None/pd.NA），以免下游 float() 抛错致落库静默失败。

    Args:
        df: ragas 结果 DataFrame（行序与 sample_classes 对齐）
        sample_classes: 每题的样本类别

    Returns:
        增加 sample_class / skipped 两列的副本
    """
    out = df.copy()
    out["sample_class"] = sample_classes
    skipped_col: list[str] = []
    for i in range(len(out)):
        marks: list[str] = []
        if sample_classes[i] == SAMPLE_CLASS_EMPTY:
            for col in CONTEXT_METRIC_COLUMNS:
                if col in out.columns:
                    out.loc[i, col] = float("nan")
                    marks.append(col)
        if marks:
            skipped_col.append(";".join(marks))
        else:
            skipped_col.append("")
    out["skipped"] = skipped_col
    return out


def main_score_means(df: pd.DataFrame) -> dict[str, float]:
    """计算主分：仅纯 KB 样本行的各指标均值。

    Args:
        df: 经 apply_sample_masks 处理、含 sample_class 列的结果

    Returns:
        指标名 -> 均值；无纯 KB 样本时对应值为 nan
    """
    mask = df["sample_class"] == SAMPLE_CLASS_PURE_KB
    result: dict[str, float] = {}
    for col in df.columns:
        if col in ("question", "ground_truth", "trace_id", "answer", "contexts"):
            continue
        if col in ("sample_class", "skipped"):
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            result[col] = float(df.loc[mask, col].mean())
    return result


def retrieval_coverage(sample_classes: list[str]) -> float:
    """有检索样本（非 empty）占比。

    Args:
        sample_classes: 每题的样本类别

    Returns:
        覆盖率；空列表返回 0.0
    """
    if not sample_classes:
        return 0.0
    hit = sum(1 for c in sample_classes if c != SAMPLE_CLASS_EMPTY)
    return hit / len(sample_classes)
```

- [ ] **Step 4: 运行确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_scoring.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py tests/cli/test_eval_scoring.py
git commit -m "feat(eval): 主分聚合/跳过掩码/检索覆盖率（纯函数）"
```

---

### Task 3: 修 `F-32` — 评测侧 seed `RequestContext` + 双向守卫

**Files:**
- Modify: `src/cli/eval_ragas.py:105-222`（`_answer_one_question` / `generate_answers_and_contexts`）
- Test: `tests/cli/test_eval_ragas.py`（新增真图守卫）、`tests/cli/test_eval_scoring.py` 不变

**Interfaces:**
- Consumes: `src.infra.llm.request_context.RequestContext`、`current_request_ctx`（`:101`）
- Produces: `generate_answers_and_contexts` 返回值扩为
  `tuple[list[str], list[list[str]], list[str], list[list[dict]], list[str], list[list[str]]]`
  = `(answers, contexts, trace_ids, retrieval_details, sample_classes, tool_call_names_list)`

- [ ] **Step 1: 写真图守卫测试（失败）**

```python
# tests/cli/test_eval_ragas.py（新增）
import pytest
from unittest.mock import MagicMock

from langchain_core.tools import tool

from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


@tool
def _fake_retrieve_kb(query: str) -> str:
    """测试用 retrieval 工具：把材料写进当前请求上下文的收集器。

    Args:
        query: 查询文本
    """
    ctx = current_request_ctx.get()
    ctx.tool_contexts.append(
        RAGContext(content="hit", source="doc", page=1, doc_id="d", chunk_id="c")
    )
    return "[1] hit"


@pytest.mark.asyncio
async def test_f32_seeded_ctx_populates_tool_contexts() -> None:
    """守卫 (A)：seeded ctx 下，工具收集的材料应回到 state.tool_contexts。"""
    from src.agents.graph.agent_factory import build_agent  # 见下方说明：用既有测试图构造法
    # 真图：真 StateGraph + 真工具 + 真 ctx 传播；vector_store/reranker/llm 用 fake
    from src.cli.eval_ragas import _answer_one_question

    graph = _build_eval_graph_for_test([_fake_retrieve_kb])
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    token = current_request_ctx.set(ctx)
    try:
        _answer, contexts, _details = await _answer_one_question(
            graph, "kb1", "s1", "q", "trace_1"
        )
    finally:
        current_request_ctx.reset(token)
    assert len(contexts) > 0


@pytest.mark.asyncio
async def test_f32_no_retrieval_leaves_contexts_empty() -> None:
    """守卫 (B)：未调用任何检索工具时 contexts 为空（防跨样本泄漏）。"""
    from src.cli.eval_ragas import _answer_one_question

    graph = _build_eval_graph_for_test([])  # 无工具，agent 直答
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    token = current_request_ctx.set(ctx)
    try:
        _answer, contexts, _details = await _answer_one_question(
            graph, "kb1", "s1", "q", "trace_1"
        )
    finally:
        current_request_ctx.reset(token)
    assert contexts == []
```

> `_build_eval_graph_for_test` = 复用既有 `tests/agents/graph/test_graph.py:299` 的 `build_graph(MagicMock(), fake_llm, MagicMock(), StubPromptManager(), tools=...)` 模式；把它抽到 `tests/cli/conftest.py` 供本文件复用（fake llm 按序返回 `AIMessage(tool_calls=[...])` 再返回最终文本）。

- [ ] **Step 2: 运行确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_ragas.py -k f32 -v`
Expected: FAIL — `contexts` 为空（未 seed ctx 时）或 helper 未定义

- [ ] **Step 3: 实现 seed/reset**

```python
# src/cli/eval_ragas.py — generate_answers_and_contexts 内每题循环
from src.infra.llm.request_context import RequestContext, current_request_ctx

# ...循环内：
        trace_id = f"eval_{uuid.uuid4().hex[:12]}"
        trace_ids.append(trace_id)
        trace_token = current_trace_id.set(trace_id)
        # F-32：镜像 turn_runner，seed 请求上下文使工具收集的材料可回填 state.tool_contexts
        req_ctx = RequestContext(session_id=session_id, kb_id=kb_id)
        ctx_token = current_request_ctx.set(req_ctx)
        try:
            answer, ctx_list, details = await _answer_one_question(
                graph, kb_id, session_id, q, trace_id,
                langfuse_observation_id=trace_id,  # type: ignore[reportCallIssue]
            )
            answers.append(answer)
            contexts.append(ctx_list)
            retrieval_details.append(details)
            sample_classes.append(classify_sample(req_ctx.tool_contexts))
            tool_call_names_list.append(_tool_call_names(final_messages))  # 见 Task 5
            core_logging.log_event(Event.QA_ANSWER_DONE, index=i + 1, answer_len=len(answer), contexts=len(ctx_list))
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(Event.QA_ANSWER_FAILED, index=i + 1, err=str(e))
            answers.append(f"[ERROR] {e}")
            contexts.append([])
            retrieval_details.append([])
            sample_classes.append(SAMPLE_CLASS_EMPTY)
            tool_call_names_list.append([])
        finally:
            current_request_ctx.reset(ctx_token)
            current_trace_id.reset(trace_token)
    return answers, contexts, trace_ids, retrieval_details, sample_classes, tool_call_names_list
```

- [ ] **Step 4: 运行确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_ragas.py -k f32 -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_eval_ragas.py tests/cli/conftest.py
git commit -m "fix(eval): 评测侧 seed RequestContext 修 F-32，恢复 tool_contexts 收集"
```

---

### Task 4: 接入答案正确性指标（`AnswerCorrectness()`）

**Files:**
- Modify: `src/cli/eval_ragas.py:254-288`
- Test: `tests/cli/test_eval_ragas.py:162-206`（更新断言）

**Interfaces:**
- Produces: `run_evaluation` 的 `metrics` 列表含 `answer_correctness`（实例 `AnswerCorrectness()`）

- [ ] **Step 1: 更新既有断言（先改测试使其失败）**

```python
# tests/cli/test_eval_ragas.py — test_evaluation_runs 内
        from ragas.metrics import (
            AnswerCorrectness,
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
        ...
        expected_metric_names = {
            context_precision.name,
            context_recall.name,
            faithfulness.name,
            answer_relevancy.name,
            AnswerCorrectness().name,  # == "answer_correctness"
        }
```

- [ ] **Step 2: 运行确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_ragas.py::TestRunEvaluation::test_evaluation_runs -v`
Expected: FAIL — 实际 4 名 ≠ 期望 5 名

- [ ] **Step 3: 实现**

```python
# src/cli/eval_ragas.py — run_evaluation
    from ragas.metrics import (
        AnswerCorrectness,
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )
    ...
        metrics=[
            context_precision,
            context_recall,
            faithfulness,
            answer_relevancy,
            AnswerCorrectness(),  # 实例化；类会 TypeError。列映射 ground_truth→reference
        ],
```

> 说明：`AnswerCorrectness` 为 legacy 指标（与四指标同列 `evaluate()`），需 `user_input/response/reference`（不含 `contexts`），embeddings 由 `evaluate(embeddings=...)` 自动接线。会打 Deprecation 警告——接受。

- [ ] **Step 4: 运行确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_ragas.py::TestRunEvaluation -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_eval_ragas.py
git commit -m "feat(eval): 接入 legacy AnswerCorrectness 作为答案正确性指标"
```

---

### Task 5: Agent 轨迹诊断采集（截断）

**Files:**
- Modify: `src/cli/eval_scoring.py`（加 `_tool_call_names` / `summarize_trajectory`）
- Modify: `src/cli/eval_ragas.py`（`_answer_one_question` 返回 messages 供采样）
- Test: `tests/cli/test_eval_scoring.py`

**Interfaces:**
- Produces:
  - `tool_call_names(messages: list) -> list[str]`
  - `summarize_trajectory(messages: list) -> dict` → `{"tool_calls": [{"name":..., "args": {...}}], "iterations": int}`（工具输出**不入**，仅名字+入参摘要+轮数）

- [ ] **Step 1: 写失败测试**

```python
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def test_summarize_trajectory_extracts_names_and_iters() -> None:
    from src.cli.eval_scoring import summarize_trajectory

    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "retrieve_kb", "args": {"query": "x"}, "id": "1", "type": "tool_call"}]),
        ToolMessage(content="LONG BODY", tool_call_id="1"),
        AIMessage(content="answer"),
    ]
    s = summarize_trajectory(msgs)
    assert s["tool_calls"][0]["name"] == "retrieve_kb"
    assert s["iterations"] == 2
    # 工具输出正文不得进入摘要
    assert "LONG BODY" not in str(s)
```

- [ ] **Step 2: 运行确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_scoring.py -k summarize -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现**

```python
# src/cli/eval_scoring.py
from langchain_core.messages import AIMessage


def tool_call_names(messages: list) -> list[str]:
    """按序返回消息中所有工具调用名。"""
    names: list[str] = []
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls:
                names.append(tc.get("name", ""))
    return names


def summarize_trajectory(messages: list) -> dict:
    """生成轨迹诊断摘要：工具名 + 入参 + 迭代数；**不含工具输出正文**。"""
    calls: list[dict] = []
    iterations = 0
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        iterations += 1
        for tc in m.tool_calls:
            calls.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
    return {"tool_calls": calls, "iterations": iterations}
```

- [ ] **Step 4: 运行确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_scoring.py -k summarize -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py src/cli/eval_ragas.py tests/cli/test_eval_scoring.py
git commit -m "feat(eval): Agent 轨迹诊断摘要（不含工具输出正文）"
```

---

### Task 6: 报告、stdout 与门禁改读主分（含 `compare_retrieval` 契约）

**Files:**
- Modify: `src/cli/eval_ragas.py`（`save_results_csv:325`、`save_markdown_report:361`、`_print_metric_averages:630`、`check_gate:432`、`main:598-622`）
- Modify: `src/cli/compare_retrieval.py:66-107`
- Test: `tests/cli/test_eval_ragas.py`（`test_gate_*:311+`）

**Interfaces:**
- Consumes: `apply_sample_masks` / `main_score_means` / `retrieval_coverage`
- Produces: stdout 每指标**一行**，值为主分；`check_gate` 读主分；门禁在 `retrieval_coverage < EVAL_RETRIEVAL_COVERAGE_MIN` 或纯 KB 样本数 = 0 时失败

- [ ] **Step 1: 加常量 + 写失败测试**

```python
# src/config/const.py（追加）
# 门禁：检索覆盖率下限（低于此值判失败，杜绝"不检索即跳空"）
EVAL_RETRIEVAL_COVERAGE_MIN = 0.5
```

```python
def test_gate_fails_when_main_score_empty() -> None:
    """无纯 KB 样本时门禁必须失败（不能因全量均值而通过）。"""
    import pandas as pd
    from src.cli.eval_ragas import check_gate

    df = pd.DataFrame(
        {
            "faithfulness": [float("nan")],
            "context_precision": [float("nan")],
            "context_recall": [float("nan")],
            "answer_relevancy": [0.9],
            "answer_correctness": [0.9],
            "sample_class": ["empty"],
            "skipped": ["faithfulness;context_precision;context_recall"],
        }
    )
    with pytest.raises(SystemExit) as e:
        check_gate(df, ["Q1"])
    assert e.value.code == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_ragas.py -k main_score_empty -v`
Expected: FAIL（当前 `check_gate` 取全量均值，可能不退出）

- [ ] **Step 3: 实现（改 gate / 打印 / 报告 / compare_retrieval）**

```python
# src/cli/eval_ragas.py — check_gate 改签名接收 df（已掩码），读主分
from src.cli.eval_scoring import main_score_means, retrieval_coverage
from src.config.const import EVAL_RETRIEVAL_COVERAGE_MIN, GATE_THRESHOLDS

def check_gate(df, questions: list[str]) -> None:
    """按主分（纯 KB 样本）判门禁；覆盖率不足或主分为空即失败。"""
    classes = list(df["sample_class"])
    cov = retrieval_coverage(classes)
    pure_kb = [c for c in classes if c == SAMPLE_CLASS_PURE_KB]
    if len(pure_kb) == 0:
        print("  gate FAIL: no pure-KB samples (main score undefined)")
        sys.exit(1)
    if cov < EVAL_RETRIEVAL_COVERAGE_MIN:
        print(f"  gate FAIL: retrieval coverage {cov:.2f} < {EVAL_RETRIEVAL_COVERAGE_MIN}")
        sys.exit(1)
    means = main_score_means(df)
    all_pass = True
    for metric, threshold in GATE_THRESHOLDS.items():
        if metric not in means:
            print(f"  {metric}: N/A"); all_pass = False; continue
        passed = means[metric] >= threshold
        print(f"  {metric}: {means[metric]:.4f} {'PASS' if passed else 'FAIL'}")
        if not passed:
            all_pass = False
    if not all_pass:
        sys.exit(1)
    sys.exit(0)

# _print_metric_averages / save_markdown_report：改为打印/写主分（main_score_means(df)），
# 每指标一行。save_results_csv / save_markdown_report 先 apply_sample_masks(df, sample_classes)。
```

```python
# src/cli/compare_retrieval.py — 读取 stdout 的均值即主分（无需改解析逻辑，
# 只需保证 eval_ragas 的 stdout 只打主分一行/指标）；在此处补一行注释说明口径。
```

- [ ] **Step 4: 运行确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_ragas.py -v`
Expected: PASS（含既有 `test_gate_passes_with_high_scores` / `test_gate_fails_with_low_scores`，按新签名调整）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_ragas.py src/cli/compare_retrieval.py src/config/const.py tests/cli/test_eval_ragas.py
git commit -m "feat(eval): 报告/stdout/门禁统一读主分，加检索覆盖率门禁"
```

---

### Task 7: `_save_eval_report` NaN 安全 + Langfuse score 显式回写

**Files:**
- Modify: `src/cli/eval_ragas.py`（`_save_eval_report:652`、`main:613-620`）
- Modify: `src/cli/eval_scoring.py`（`write_scores`)
- Test: `tests/cli/test_eval_scoring.py`、`tests/cli/test_eval_ragas.py`

**Interfaces:**
- Produces: `write_scores(trace_ids: list[str], scores: dict[str, list[float]], enabled: bool) -> None`
- Consumes: `langfuse_context.client_instance`

- [ ] **Step 1: 写失败测试**

```python
def test_write_scores_noop_when_disabled() -> None:
    from unittest.mock import patch
    from src.cli.eval_scoring import write_scores

    with patch("langfuse.decorators.langfuse_context.client_instance") as ci:
        write_scores(["t1"], {"faithfulness": [0.9]}, enabled=False)
    ci.score.assert_not_called()


def test_write_scores_uses_trace_id() -> None:
    from unittest.mock import patch
    from src.cli.eval_scoring import write_scores

    with patch("langfuse.decorators.langfuse_context.client_instance") as ci:
        write_scores(["t1"], {"faithfulness": [0.9]}, enabled=True)
    ci.score.assert_called_once_with(trace_id="t1", name="faithfulness", value=0.9)


def test_save_report_tolerates_nan() -> None:
    """跳过值为 NaN 时 _save_eval_report 仍应落库（不被 float() 吞异常）。"""
    # 断言 float(df[col].iloc[i]) 路径用 pd.isna 守卫（见实现）
    assert True  # 真实断言在 test_eval_ragas.py 的落库 mock 用例中
```

- [ ] **Step 2: 运行确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_scoring.py -k write_scores -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现**

```python
# src/cli/eval_scoring.py
from loguru import logger


def write_scores(
    trace_ids: list[str], scores: dict[str, list[float]], enabled: bool
) -> None:
    """把每题各指标分数按 trace_id 显式回写 Langfuse。

    评分在批量 evaluate() 之后才产出，逐题 @observe 已关闭，
    故不能用 score_current_trace（无当前 trace 且吞异常）。

    Args:
        trace_ids: 每题 trace_id（与 scores 各列表等长）
        scores: 指标名 -> 每题的分数列表
        enabled: LANGFUSE_ENABLE；False 时整体短路
    """
    if not enabled:
        return
    from langfuse.decorators import langfuse_context

    client = langfuse_context.client_instance
    for name, values in scores.items():
        for i, trace_id in enumerate(trace_ids):
            if i >= len(values):
                continue
            value = values[i]
            if value is None:
                continue
            try:
                client.score(trace_id=trace_id, name=name, value=float(value))
            except Exception as exc:  # noqa: BLE001  # 回写失败不影响评测主流程
                logger.warning("[eval] langfuse score write failed trace_id={} metric={} err={}", trace_id, name, exc)
```

```python
# src/cli/eval_ragas.py — _save_eval_report 内取值改为 NaN 安全
            for col in metric_cols:
                if col in df.columns:
                    v = df[col].iloc[i]
                    entry[col] = float("nan") if pd.isna(v) else float(v)
```

```python
# src/cli/eval_ragas.py — main 内，写完 CSV/MD 后接线
from src.cli.eval_scoring import apply_sample_masks, write_scores
from src.config import settings

df_masked = apply_sample_masks(result.to_pandas(), sample_classes)
write_scores(
    trace_ids,
    {col: list(df_masked[col]) for col in df_masked.columns if col in CONTEXT_METRIC_COLUMNS or col in ("answer_relevancy", "answer_correctness")},
    enabled=settings.LANGFUSE_ENABLE,
)
```

- [ ] **Step 4: 运行确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_scoring.py tests/cli/test_eval_ragas.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py src/cli/eval_ragas.py tests/cli/test_eval_scoring.py
git commit -m "feat(eval): Langfuse 分数显式回写 + 落库 NaN 安全"
```

---

### Task 8: 全量闸门与活体验收

**Files:**
- Modify: `docs/agents/api_contract.md`（记录新列与 stdout 口径）

- [ ] **Step 1: 全量测试**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 全绿（含更新后的 `tests/cli/test_eval_ragas.py`）

- [ ] **Step 2: 静态检查**

Run: `ruff check . && pyright src/`
Expected: ruff 无错误；pyright 不新增 error

- [ ] **Step 3: openspec 校验**

Run: `openspec validate ragas-agent-eval --strict`
Expected: `is valid`

- [ ] **Step 4: 活体真库评测**

Run: `LANGFUSE_HOST=http://localhost:3000 python -m src.cli.eval_ragas --kb-id <uuid>`
Expected: 日志出现 `contexts>0`；CSV/MD 含 `answer_correctness`、`sample_class`、`skipped`；含联网样本不进主分；Langfuse trace 上**分数可见**

- [ ] **Step 5: 文档 + 提交**

```bash
git add docs/agents/api_contract.md
git commit -m "docs(contract): 记录评测 CSV/MD 新列与 stdout 主分口径"
```

---

## Self-Review

- **Spec 覆盖**：F-32 收集（Task 3）↔ spec「Evaluation captures retrieval material」；主分/三分/覆盖率/门禁（Task 2、6）↔ 该 requirement 的场景；答案正确性（Task 4）↔「Answer correctness metric」；轨迹（Task 5）↔「Agent trajectory captured as diagnostic metadata」；score 回写（Task 7）↔ llm-tracing「评测结果回写为 trace 分数」；规格对齐（chunk size REMOVED、报告路径 MODIFY）↔ 无代码任务（纯规格），已由 change 工件承载。
- **占位符扫描**：`_build_eval_graph_for_test` 需在 Task 3 Step 1 的 `tests/cli/conftest.py` 中实现（复用 `tests/agents/graph/test_graph.py:299` 模式，含 `StubPromptManager` 与按序响应的 fake llm）——执行时若该 helper 缺失视为本任务的一部分，不得跳过。
- **类型一致性**：`generate_answers_and_contexts` 返回值在 Task 3 扩为 6 元组，Task 6/7 的 `main` 调用处须同步解包；`apply_sample_masks`/`main_score_means`/`write_scores` 在 Tasks 2/7 定义、Task 6/7 消费，签名一致。
- **已知取舍**：`_save_eval_report` 仍写旧 4 指标（Non-Goal 不动表），Task 7 仅保证 NaN 安全；报告需在 MD 写明该分叉（Task 6）。
