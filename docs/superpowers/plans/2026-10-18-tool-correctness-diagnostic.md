# Tool Correctness Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 DeepEval 评测中逐题计算 `tool_correctness`（0.0–1.0），写入 `detail_json` 与报告，作为工具选择归因与回归哨兵；失败样本不再中止整轮评测。

**Architecture:** 在现有 `eval_deepeval` 同步评分链路中增加第三个诊断指标。题集加载层把 `expected_tools` 以三态（缺失/`[]`/非空）传到 `run_evaluation` 的 rows；评分器用顶层 `trajectory.tool_calls` 构造 `LLMTestCase`，调用 `ToolCorrectnessMetric(async_mode=False, eval_mode="llm")`；结果写入 DataFrame，再进入 detail/CSV/Markdown。失败样本由 `failed` 标记、`[ERROR]` 或空回答识别，行为轴放行并显示 N/A。

**Tech Stack:** Python 3.12 / pytest / pandas / DeepEval 4.2.9 / LangGraph

**Spec:** `docs/openspec/changes/tool-correctness-diagnostic/design.md`、`specs/evaluation-pipeline/spec.md`、`specs/testset-generation-deepeval/spec.md`

## Global Constraints

- 工作区：`/root/code/corporate_rag-tool-correctness`，分支 `feat/tool-correctness-diagnostic`。`.env` / `.venv` / `data` 已是指向主工作区的 symlink。
- 严禁 `git add -A` / `git add .`（会把 `.venv` symlink 带进提交）；每次只加显式路径。
- 宿主侧测试一律加 `POSTGRES_HOST=localhost`：`POSTGRES_HOST=localhost .venv/bin/python -m pytest ...`。
- `tool_correctness` ∈ `[0.0, 1.0]`，= 命中的期望工具数 / 去重后的期望工具数；不得假设 `retrieve_kb`，不得假设只有一项。
- 标签三态：字段缺失/`None` → N/A；显式 `[]` → 期望不调用工具；非空 `list[str]` → 评分。非空元素必须是非空字符串。
- 指标构造固定 `model=judge, async_mode=False, eval_mode="llm"`；不传 `available_tools`；`evaluation_params=[]`。
- 空样本（`sample_class=empty` 且跑图成功）照常评分；失败 = `trajectory.failed` / `[ERROR]` / 空回答 → `null`。
- 失败样本不得中止整轮：行为轴对 `failed=True` 放行并显示 N/A，其余空轨迹仍 fail-loud。
- `tool_correctness` 不进主分、门禁、Langfuse；`tool_correctness_reason` 只进 detail 与 Markdown，不进 CSV。
- `--deepeval-only` 不产出 `tool_correctness`，报告显式写「工具选择：未跑（--deepeval-only）」。
- 每个 Task 结束前跑该 Task 的测试并提交；提交消息用 `feat(eval): ...` / `fix(eval): ...` / `docs(eval): ...`。

---

## File Structure

| 文件 | 责任 |
|---|---|
| `src/cli/testset_io.py` | 题集加载；`expected_tools` 三态校验；返回三元组 |
| `src/cli/eval_scoring.py` | 诊断指标构造；失败判定；标签去重；行为轴断言放行 |
| `src/cli/eval_deepeval.py` | 数据流、逐题评分、detail/CSV/Markdown 报告、`--deepeval-only` |
| `tests/cli/test_testset_io_expected_tools.py` | 标签三态与校验（新建） |
| `tests/cli/test_eval_tool_correctness.py` | 指标语义矩阵与构造参数（新建） |
| `tests/cli/test_eval_deepeval_limit.py` | 三元组截断 |
| `tests/cli/test_behaviour_fail_loud.py` | 失败轨迹放行 |
| `tests/cli/test_eval_deepeval.py` | `run_evaluation` 签名、失败轨迹、CSV schema |
| `tests/cli/test_eval_deepeval_detail.py` | detail null/score、报告计数 |
| `tests/cli/test_deepeval_only.py` | 三元组 mock、未跑文案 |
| `src/cli/README.md` / `docs/agents/eval-ledger.md` / `docs/agents/glossary.md` | 口径与术语 |

---

### Task 1: Load `expected_tools` with three-state validation

**Files:**
- Modify: `src/cli/testset_io.py:110-125`
- Modify: `src/cli/eval_deepeval.py:78-105`（`_limit_testset`）、`src/cli/eval_deepeval.py:1294-1307`（主流程加载）
- Test: `tests/cli/test_testset_io_expected_tools.py`（新建）
- Test: `tests/cli/test_eval_deepeval_limit.py`
- Test: `tests/cli/test_deepeval_only.py:107-110`

**Interfaces:**
- Produces: `_load_latest_testset(kb_id, version) -> tuple[list[str], list[str], list[list[str] | None]]`
- Produces: `_normalize_expected_tools(raw, q_index) -> list[str] | None`
- Produces: `_limit_testset(questions, ground_truth, expected_tools, limit) -> tuple[list[str], list[str], list[list[str] | None]]`

- [ ] **Step 1: Write the failing validation tests**

Create `tests/cli/test_testset_io_expected_tools.py`:

```python
import pytest

from src.cli.testset_io import _normalize_expected_tools


def test_missing_labels_are_preserved_as_none():
    assert _normalize_expected_tools(None, 0) is None


def test_explicit_empty_labels_are_preserved():
    assert _normalize_expected_tools([], 1) == []


def test_non_empty_labels_are_preserved():
    assert _normalize_expected_tools(["search_web"], 2) == ["search_web"]


def test_string_label_fails_loud():
    with pytest.raises(ValueError, match="第 0 题"):
        _normalize_expected_tools("retrieve_kb", 0)


def test_non_string_element_fails_loud():
    with pytest.raises(ValueError, match="第 1 题"):
        _normalize_expected_tools([1], 1)


def test_empty_string_element_fails_loud():
    with pytest.raises(ValueError, match="第 2 题"):
        _normalize_expected_tools([""], 2)
```

- [ ] **Step 2: Run it and verify it fails**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_testset_io_expected_tools.py -v`
Expected: FAIL with `ImportError: cannot import name '_normalize_expected_tools'`.

- [ ] **Step 3: Implement `_normalize_expected_tools` and the 3-tuple**

In `src/cli/testset_io.py`, add above `_load_latest_testset`:

```python
def _normalize_expected_tools(raw: object, q_index: int) -> list[str] | None:
    """校验题集标签；None 表示无标签，[] 表示显式期望不调用工具。

    Args:
        raw: 原始 expected_tools 值
        q_index: 0-based 题号（仅用于报错）

    Returns:
        None 或字符串列表（保留空列表，不去重）

    Raises:
        ValueError: 值不是 None/list[str]，或含空/非字符串元素
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(
            f"第 {q_index} 题 expected_tools 必须是 list[str] 或 None，"
            f"得到 {type(raw).__name__}"
        )
    normalized: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"第 {q_index} 题 expected_tools 含非法元素: {item!r}")
        normalized.append(item)
    return normalized
```

Replace `_load_latest_testset` with:

```python
def _load_latest_testset(
    kb_id: str, version: int | None = None
) -> tuple[list[str], list[str], list[list[str] | None]]:
    """加载测试集并适配评分的 (questions, ground_truth, expected_tools) 形状。

    Args:
        kb_id: 知识库 UUID
        version: 指定版本号；None 表示自动取最新版本

    Returns:
        (questions, ground_truth, expected_tools) 三元组；expected_tools 每项
        为 None（字段缺失）、[]（显式不调工具）或非空字符串列表
    """
    samples = load_testset_samples(kb_id, version)
    questions = [s["user_input"] for s in samples]
    ground_truth = [s["reference"] for s in samples]
    expected_tools = [
        _normalize_expected_tools(s.get("expected_tools"), i)
        for i, s in enumerate(samples)
    ]
    return questions, ground_truth, expected_tools
```

In `src/cli/eval_deepeval.py`, change `_limit_testset`:

```python
def _limit_testset(
    questions: list[str],
    ground_truth: list[str],
    expected_tools: list[list[str] | None],
    limit: int | None,
) -> tuple[list[str], list[str], list[list[str] | None]]:
    """按冒烟轮次截取前 `limit` 题；`None` 表示不截断。

    Args:
        questions: 题面列表
        ground_truth: 参考答案列表（与 questions 等长同序）
        expected_tools: 每题的标签（与 questions 等长同序）
        limit: 保留的题数；None 表示不截断

    Returns:
        (questions, ground_truth, expected_tools) 的截断副本或原对象

    Raises:
        ValueError: `limit` 非正
    """
    if limit is None:
        return questions, ground_truth, expected_tools
    if limit <= 0:
        raise ValueError(f"--limit 必须为正整数，得到 {limit}")
    if limit >= len(questions):
        return questions, ground_truth, expected_tools
    return (
        questions[:limit],
        ground_truth[:limit],
        expected_tools[:limit],
    )
```

In `main()`（`src/cli/eval_deepeval.py:1294-1307`）改为：

```python
            resolved_version = resolve_testset_version(kb_id, args.testset_version)
            questions, ground_truth, expected_tools = _load_latest_testset(
                kb_id, version=resolved_version
            )
        except FileNotFoundError as e:
            print(f"error: {e}")
            sys.exit(1)

        questions, ground_truth, expected_tools = _limit_testset(
            questions, ground_truth, expected_tools, args.limit
        )
```

此时 `expected_tools` 尚未消费，但类型链路完整；Task 3 再接入评分。

- [ ] **Step 4: Update `_limit_testset` tests**

Rewrite `tests/cli/test_eval_deepeval_limit.py` to pass three lists:

```python
import pytest

from src.cli.eval_deepeval import _limit_testset


def test_none_returns_unchanged():
    q = ["q1", "q2", "q3"]
    g = ["g1", "g2", "g3"]
    e = [["retrieve_kb"], None, []]
    out_q, out_g, out_e = _limit_testset(q, g, e, None)
    assert out_q is q
    assert out_g is g
    assert out_e is e


def test_limit_takes_prefix_of_all_three_lists():
    q = ["q1", "q2", "q3"]
    g = ["g1", "g2", "g3"]
    e = [["retrieve_kb"], None, []]
    out_q, out_g, out_e = _limit_testset(q, g, e, 2)
    assert out_q == ["q1", "q2"]
    assert out_g == ["g1", "g2"]
    assert out_e == [["retrieve_kb"], None]


def test_limit_larger_than_length_is_noop():
    q = ["q1", "q2"]
    g = ["g1", "g2"]
    e = [None, []]
    out_q, out_g, out_e = _limit_testset(q, g, e, 5)
    assert (out_q, out_g, out_e) == (q, g, e)


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValueError, match="--limit"):
        _limit_testset(["q1"], ["g1"], [None], 0)
```

- [ ] **Step 5: Update the `--deepeval-only` loader mock**

In `tests/cli/test_deepeval_only.py:107-110`, change:

```python
    monkeypatch.setattr(
        "src.cli.testset_io._load_latest_testset",
        lambda kb_id, version=None: (["q"], ["a"], [["retrieve_kb"]]),
    )
```

- [ ] **Step 6: Run the targeted tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_testset_io_expected_tools.py tests/cli/test_eval_deepeval_limit.py tests/cli/test_deepeval_only.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cli/testset_io.py src/cli/eval_deepeval.py tests/cli/test_testset_io_expected_tools.py tests/cli/test_eval_deepeval_limit.py tests/cli/test_deepeval_only.py
git commit -m "feat(eval): load expected_tools with three-state validation"
```

---

### Task 2: Make failed runs observable

**Files:**
- Modify: `src/cli/eval_scoring.py:343-371`（`assert_behaviour_scores_complete`）
- Modify: `src/cli/eval_deepeval.py:303-319`（异常轨迹）、`src/cli/eval_deepeval.py:984-1030`（`_build_behaviour_section`）、`src/cli/eval_deepeval.py:1134`（调用）
- Test: `tests/cli/test_behaviour_fail_loud.py`
- Test: `tests/cli/test_eval_deepeval.py`（异常轨迹断言）

**Interfaces:**
- Produces: `is_failed_run(answer, trajectory) -> bool`
- Produces: exception trajectory 带 `"failed": True`
- Produces: `_build_behaviour_section(behaviour_rows, trajectories=None)`

- [ ] **Step 1: Write the failing behavior-axis tests**

Append to `tests/cli/test_behaviour_fail_loud.py`:

```python
def test_failed_trajectory_is_exempt():
    rows = [{"q_index": 0, "input": "Q0", "metrics": {}}]
    assert_behaviour_scores_complete(
        ["Q0"],
        rows,
        [{"tool_calls": [], "iterations": 0, "failed": True}],
    )


def test_failed_trajectory_with_other_row_still_checks_the_rest():
    rows = [
        {"q_index": 0, "input": "Q0", "metrics": {}},
        {"q_index": 1, "input": "Q1", "metrics": {"Task Completion": 1.0}},
    ]
    trajectories = [
        {"tool_calls": [], "iterations": 0, "failed": True},
        {"tool_calls": ["lookup"], "iterations": 2},
    ]
    assert_behaviour_scores_complete(["Q0", "Q1"], rows, trajectories)


def test_fill_missing_failed_row():
    from src.cli.eval_scoring import fill_missing_behaviour_rows

    rows = fill_missing_behaviour_rows(
        [],
        ["Q0"],
        [{"tool_calls": [], "iterations": 0, "failed": True}],
    )
    assert rows == [{"q_index": 0, "input": "Q0", "metrics": {}, "reasons": {}}]


def test_non_failed_empty_trajectory_still_fails_loud():
    with pytest.raises(RuntimeError, match="轨迹"):
        assert_behaviour_scores_complete(
            ["Q0"],
            [{"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}}],
            [{"tool_calls": [], "iterations": 0}],
        )
```

- [ ] **Step 2: Run it and verify it fails**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_behaviour_fail_loud.py -v`
Expected: FAIL — `test_failed_trajectory_is_exempt` raises `RuntimeError: 第 0 题没有任何行为分`（旧逻辑不豁免 failed）。

- [ ] **Step 3: Add `is_failed_run` and update the assertion**

In `src/cli/eval_scoring.py`, add above `assert_behaviour_scores_complete`:

```python
def is_failed_run(answer: object, trajectory: dict | None) -> bool:
    """判断一题是否属于跑图失败（而非“未检索到材料”）。

    三种失败形态必须统一：异常分支的 failed 标记、[ERROR] 占位回答、
    空回答/纯空白（迭代触顶的已知生产故障）。

    Args:
        answer: 被测系统的回答
        trajectory: 轨迹摘要（可能带 failed 键）

    Returns:
        True 表示失败，应记 N/A；False 表示跑图成功
    """
    if (trajectory or {}).get("failed") is True:
        return True
    text = "" if answer is None else str(answer)
    if text.startswith("[ERROR]"):
        return True
    return not text.strip()
```

Add `fill_missing_behaviour_rows` and replace `assert_behaviour_scores_complete` with:

```python
def fill_missing_behaviour_rows(
    rows: list[dict], questions: list[str], trajectories: list[dict]
) -> list[dict]:
    """按 q_index 补齐行为分行；只允许 `failed=True` 的题缺行。

    补齐后用空 metrics/reasons 表示“该题行为分 N/A”，使后续断言、
    Langfuse 回写与报告都能按 q_index 稳定对齐。
    """
    by_index = {r.get("q_index"): r for r in rows}
    out: list[dict] = []
    for i, question in enumerate(questions):
        row = by_index.get(i)
        if row is None:
            failed = (
                i < len(trajectories)
                and bool((trajectories[i] or {}).get("failed"))
            )
            if not failed:
                raise RuntimeError(f"第 {i} 题缺少行为分行（行为分不可信）")
            row = {"q_index": i, "input": question, "metrics": {}, "reasons": {}}
        out.append(row)
    return out


def assert_behaviour_scores_complete(
    questions: list[str], rows: list[dict], trajectories: list[dict]
) -> None:
    """行为轴 fail-loud：题数对齐 + 每题至少一个非 None 分 + 轨迹非空。

    `trajectory.failed=True` 的题豁免：失败由评测层转成 N/A，不能让整轮 abort。
    调用前应先用 `fill_missing_behaviour_rows` 补齐 failed 题的空行。
    """
    if len(trajectories) != len(questions):
        raise RuntimeError(
            f"轨迹数与题数不齐：questions={len(questions)} trajectories={len(trajectories)}"
        )
    if len(rows) != len(questions):
        raise RuntimeError(
            f"行为分题数不齐：questions={len(questions)} rows={len(rows)}"
            "（JSON 缺失或循环未耗尽）"
        )
    failed_indexes = {
        i
        for i, t in enumerate(trajectories)
        if bool((t or {}).get("failed"))
    }
    row_by_index = {r.get("q_index"): r for r in rows}
    for i in range(len(questions)):
        if i in failed_indexes:
            continue
        row = row_by_index.get(i)
        if row is None:
            raise RuntimeError(f"第 {i} 题缺少行为分行（行为分不可信）")
        if not any(v is not None for v in (row.get("metrics") or {}).values()):
            raise RuntimeError(f"第 {i} 题没有任何行为分（行为分不可信）")
    for i, t in enumerate(trajectories):
        if i in failed_indexes:
            continue
        if not (t or {}).get("tool_calls") and not (t or {}).get("iterations"):
            raise RuntimeError(
                f"第 {i} 题轨迹为空（行为指标无 trace 时源码直接置 0.0）"
            )
```

- [ ] **Step 4: Mark the exception trajectory**

In `src/cli/eval_deepeval.py:303-319`, change the exception return to:

```python
        return (
            f"[ERROR] {e}",
            [],
            [],
            SAMPLE_CLASS_EMPTY,
            {"tool_calls": [], "iterations": 0, "failed": True},
        )
```

Update `_collect_one_question`'s Returns docstring to say the exception branch additionally carries `failed: True`; `summarize_trajectory` itself remains unchanged (success trajectories never get the key).

In `main()` (`src/cli/eval_deepeval.py:1401-1406`), change:

```python
        behaviour_rows = read_behaviour_scores(behaviour_out)
        assert_behaviour_scores_complete(questions, behaviour_rows, trajectories)
```

to:

```python
        behaviour_rows = fill_missing_behaviour_rows(
            read_behaviour_scores(behaviour_out), questions, trajectories
        )
        assert_behaviour_scores_complete(questions, behaviour_rows, trajectories)
```

Add `fill_missing_behaviour_rows` to the `from src.cli.eval_scoring import (...)` list.

- [ ] **Step 5: Render failed behavior rows as N/A**

Change `_build_behaviour_section` signature to:

```python
def _build_behaviour_section(
    behaviour_rows: list[dict], trajectories: list[dict] | None = None
) -> list[str]:
```

Replace the existing row loop (from `for i, row in enumerate(behaviour_rows):` through its final `lines.append(...)`) with:

```python
    for i, row in enumerate(behaviour_rows):
        vals = [str(row.get("input") or f"Q{i + 1}")]
        failed = bool(
            trajectories
            and i < len(trajectories)
            and (trajectories[i] or {}).get("failed")
        )
        if failed:
            vals.extend(["N/A（failed）"] * len(metric_names))
            lines.append("| " + " | ".join(vals) + " |")
            continue
        reasons = row.get("reasons") or {}
        for name in metric_names:
            value = (row.get("metrics") or {}).get(name)
            if isinstance(value, (int, float)):
                vals.append(f"{value:.4f}")
            else:
                note = " ".join(str(reasons.get(name) or "").split()).replace("|", "/")[
                    :60
                ]
                if note:
                    vals.append(f"N/A（{note}）")
                else:
                    vals.append("N/A")
        lines.append("| " + " | ".join(vals) + " |")
```

```python
        lines.extend(_build_behaviour_section(behaviour_rows, trajectories))
```

- [ ] **Step 6: Update exception-trajectory assertions**

Run: `grep -n '"tool_calls": \[\], "iterations": 0' tests/cli/test_eval_deepeval.py`

只改**异常分支**的断言（`test_generate_handles_errors` 附近，紧邻 `assert "[ERROR]" in answers[1]` 的那条，约 line 302）。成功分支里 `mock 图未回填 messages` 造成的空轨迹（约 line 235）**保持** `{"tool_calls": [], "iterations": 0}`，不要加 `failed`。

```python
        assert trajectories[1] == {
            "tool_calls": [],
            "iterations": 0,
            "failed": True,
        }
```

Do not change fabricated rows that are not produced by `_collect_one_question` (e.g., lines ~464 / ~557) unless the test expects failed semantics.

- [ ] **Step 7: Run the targeted tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_behaviour_fail_loud.py tests/cli/test_behaviour_report.py tests/cli/test_eval_deepeval.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/cli/eval_scoring.py src/cli/eval_deepeval.py tests/cli/test_behaviour_fail_loud.py tests/cli/test_behaviour_report.py tests/cli/test_eval_deepeval.py
git commit -m "fix(eval): keep failed runs observable instead of aborting"
```

---

### Task 3: Score `tool_correctness` per sample

**Files:**
- Modify: `src/cli/eval_scoring.py:65-90`（`build_diagnostic_metrics`）、新增 `dedupe_tool_names`
- Modify: `src/cli/eval_deepeval.py:671-760`（`score_rows`）、`762-820`（`run_evaluation`）、`1408-1427`（主流程调用）
- Test: `tests/cli/test_eval_tool_correctness.py`（新建）
- Test: `tests/cli/test_eval_native_diagnostics.py`
- Test: `tests/cli/test_eval_deepeval.py`（`run_evaluation` 调用）

**Interfaces:**
- Consumes: Task 1 的 `expected_tools` 与 `trajectory`
- Produces: `dedupe_tool_names(names, q_index) -> list[str]`
- Produces: `_score_tool_correctness(row, metric) -> tuple[float | None, str]`
- Produces: `run_evaluation(..., *, expected_tools, trajectories, ...)`

- [ ] **Step 1: Write the failing metric tests**

Create `tests/cli/test_eval_tool_correctness.py`:

```python
from deepeval.config.eval_mode import EvalMode
from deepeval.models import DeepEvalBaseLLM

from src.cli.eval_deepeval import _score_tool_correctness
from src.cli.eval_scoring import build_diagnostic_metrics


class _FakeJudge(DeepEvalBaseLLM):
    def load_model(self):
        return self

    def generate(self, prompt: str, **kwargs) -> str:
        return "{}"

    async def a_generate(self, prompt: str, **kwargs) -> str:
        return "{}"

    def get_model_name(self) -> str:
        return "fake-judge"


def _row(expected, called, *, answer="answer", failed=False):
    return {
        "q_index": 0,
        "question": "question",
        "answer": answer,
        "expected_tools": expected,
        "trajectory": {
            "tool_calls": [{"name": name, "args": {"query": "x"}} for name in called],
            "iterations": 2,
            "failed": failed,
        },
    }


def _metric():
    judge = _FakeJudge()
    return judge, build_diagnostic_metrics(judge)["tool_correctness"]


def test_single_expected_tool_is_hit():
    judge, metric = _metric()
    assert _score_tool_correctness(_row(["search_web"], ["search_web"]), metric)[0] == 1.0


def test_expected_tool_missing_is_zero():
    _, metric = _metric()
    score, reason = _score_tool_correctness(_row(["search_web"], ["retrieve_kb"]), metric)
    assert score == 0.0
    assert "search_web" in reason


def test_duplicate_and_extra_calls_do_not_reduce_score():
    _, metric = _metric()
    assert _score_tool_correctness(
        _row(["retrieve_kb"], ["retrieve_kb", "retrieve_kb", "search_web"]), metric
    )[0] == 1.0


def test_multiple_expected_tools_yield_fraction():
    _, metric = _metric()
    score, reason = _score_tool_correctness(
        _row(["retrieve_kb", "search_web"], ["retrieve_kb"]), metric
    )
    assert score == 0.5
    assert "search_web" in reason


def test_duplicate_labels_are_deduped():
    _, metric = _metric()
    score, _ = _score_tool_correctness(
        _row(["retrieve_kb", "retrieve_kb"], ["retrieve_kb"]), metric
    )
    assert score == 1.0


def test_explicit_empty_labels():
    _, metric = _metric()
    assert _score_tool_correctness(_row([], []), metric)[0] == 1.0
    assert _score_tool_correctness(_row([], ["retrieve_kb"]), metric)[0] == 0.0


def test_missing_labels_are_na():
    _, metric = _metric()
    assert _score_tool_correctness(_row(None, []), metric) == (None, "")


def test_failed_answer_is_na_even_with_labels():
    _, metric = _metric()
    assert _score_tool_correctness(_row(["retrieve_kb"], [], answer=""), metric) == (
        None,
        "failed",
    )
    assert _score_tool_correctness(
        _row(["retrieve_kb"], [], answer="[ERROR] boom"), metric
    ) == (None, "failed")


def test_delegation_is_not_exempt():
    _, metric = _metric()
    score, reason = _score_tool_correctness(
        _row(["retrieve_kb"], ["delegate_task"]), metric
    )
    assert score == 0.0
    assert "delegate_task" in reason


def test_reason_is_single_line_without_pipe():
    _, metric = _metric()
    _, reason = _score_tool_correctness(
        _row(["retrieve_kb"], ["delegate_task"]), metric
    )
    assert "\n" not in reason
    assert "|" not in reason


def test_empty_sample_with_labels_scores_zero():
    import math

    from src.cli.eval_deepeval import score_rows

    class _Bomb:
        def measure(self, test_case):
            raise AssertionError("empty 样本不应调用裁判诊断")

    _, metric = _metric()
    row = _row(["retrieve_kb"], [])
    row["sample_class"] = "empty"
    out = score_rows(
        [row],
        {"faithfulness": _Bomb()},
        diagnostic_metrics={
            "contextual_relevancy": _Bomb(),
            "non_advice": _Bomb(),
            "tool_correctness": metric,
        },
    )
    assert out[0]["tool_correctness"] == 0.0
    assert math.isnan(out[0]["contextual_relevancy"])


def test_metric_is_constructed_with_fixed_mode():
    judge, metric = _metric()
    assert metric.model is judge
    assert metric.async_mode is False
    assert metric.eval_mode is EvalMode.LLM
    assert metric.available_tools is None
    assert metric.evaluation_params == []


def test_tool_correctness_is_not_a_behaviour_metric():
    from src.cli.eval_scoring import build_behaviour_metrics

    names = [type(metric).__name__ for metric in build_behaviour_metrics(_FakeJudge())]
    assert "ToolCorrectnessMetric" not in names


def test_apply_sample_masks_keeps_tool_correctness():
    import math

    import pandas as pd

    from src.cli.eval_scoring import apply_sample_masks

    out = apply_sample_masks(
        pd.DataFrame({"tool_correctness": [0.0], "faithfulness": [0.5]}),
        ["empty"],
    )
    assert out.loc[0, "tool_correctness"] == 0.0
    assert math.isnan(out.loc[0, "faithfulness"])
```

- [ ] **Step 2: Run it and verify it fails**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_tool_correctness.py -v`
Expected: FAIL with `ImportError: cannot import name '_score_tool_correctness'`.

- [ ] **Step 3: Add the metric and dedupe helper**

In `src/cli/eval_scoring.py`, update `build_diagnostic_metrics`:

```python
def build_diagnostic_metrics(judge: Any) -> dict:
    """构造三项诊断指标（不进主分/门禁）。

    `ToolCorrectness` 是确定性指标，不调裁判；仍需显式 model 与
    `async_mode=False`（默认异步分支的 measure 返回 None）、`eval_mode="llm"`。
    """
    # isort: off
    import src.config.settings  # noqa: F401
    from deepeval.metrics import (
        ContextualRelevancyMetric,
        NonAdviceMetric,
        ToolCorrectnessMetric,
    )
    # isort: on

    return {
        "contextual_relevancy": ContextualRelevancyMetric(model=judge),
        "non_advice": NonAdviceMetric(advice_types=["financial"], model=judge),
        "tool_correctness": ToolCorrectnessMetric(
            model=judge, async_mode=False, eval_mode="llm"
        ),
    }
```

Add above `build_diagnostic_metrics` or next to `summarize_trajectory`:

```python
def dedupe_tool_names(names: list[str], q_index: int) -> list[str]:
    """按首次出现去重期望工具名；重复项记 warning。

    DeepEval 会把重复项当成多次独立期望，导致一次调用只得 0.5。
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name in seen:
            logger.warning(
                "[eval] duplicate expected tool q_index={} tool={}", q_index, name
            )
            continue
        seen.add(name)
        out.append(name)
    return out
```

- [ ] **Step 4: Add `_score_tool_correctness`**

In `src/cli/eval_deepeval.py`, add above `score_rows`:

```python
def _score_tool_correctness(row: dict, metric) -> tuple[float | None, str]:
    """按顶层轨迹计算一题的工具选择分数。

    Args:
        row: 含 question / answer / expected_tools / trajectory / q_index
        metric: `ToolCorrectnessMetric`；None 表示构造失败

    Returns:
        (score, reason)：score 为 0.0–1.0 或 None；reason 单行、无 "|"
    """
    from deepeval.test_case import LLMTestCase, ToolCall

    trajectory = row.get("trajectory") or {}
    if is_failed_run(row.get("answer"), trajectory):
        return None, "failed"
    expected = row.get("expected_tools")
    if expected is None:
        return None, ""
    if metric is None:
        return None, "metric_unavailable"

    q_index = int(row.get("q_index", 0))
    expected_names = dedupe_tool_names(expected, q_index)
    calls = trajectory.get("tool_calls") or []
    actual_names = [str(call.get("name", "")) for call in calls]

    test_case = LLMTestCase(
        input=row.get("question", ""),
        tools_called=[
            ToolCall(name=name, input_parameters=(call.get("args") or {}))
            for call, name in zip(calls, actual_names, strict=True)
        ],
        expected_tools=[ToolCall(name=name) for name in expected_names],
    )
    try:
        value = metric.measure(test_case)
    except Exception as exc:  # noqa: BLE001  # 诊断失败记 null，不重试
        return None, f"tool_correctness:{type(exc).__name__}"
    if value is None:
        return None, "tool_correctness:returned_none"

    score = float(value)
    missing = [name for name in expected_names if name not in actual_names]
    if score >= 1.0:
        reason = ""
    elif not expected_names:
        reason = "expected no tools but called " + ",".join(sorted(set(actual_names)))
    elif missing:
        reason = "missing " + ",".join(missing)
    else:
        reason = "partial match"
    if "delegate_task" in actual_names and missing:
        reason += "; delegate_task present but expected tools not called at top level"
    return score, " ".join(reason.replace("|", "/").split())
```

Update imports at the top of `eval_deepeval.py`:

```python
from src.cli.eval_scoring import (
    ...
    dedupe_tool_names,
    is_failed_run,
    ...
)
```

- [ ] **Step 5: Wire `score_rows`**

At the start of the per-row loop (before the empty branch), add:

```python
        tool_metric = None
        if diagnostic_metrics is not None:
            tool_metric = diagnostic_metrics.get("tool_correctness")
        tool_value, tool_reason = _score_tool_correctness(row, tool_metric)
        scored["tool_correctness"] = tool_value
        scored["tool_correctness_reason"] = tool_reason
```

In the empty branch, change the diagnostic loop to skip the tool key:

```python
            if diagnostic_metrics is not None:
                for name in diagnostic_metrics:
                    if name == "tool_correctness":
                        continue
                    scored[name] = float("nan")
```

Delete the existing `scored["tool_correctness"] = None` at the end of both branches. Update `score_rows`'s docstring: `empty` 样本不再“不调用任何指标”，而是“四项与两项裁判诊断不调用，`tool_correctness` 照常评分”。

- [ ] **Step 6: Make `run_evaluation` require labels/trajectories**

Change the signature to:

```python
def run_evaluation(
    questions,
    ground_truth,
    answers,
    contexts,
    metrics,
    trace_ids,
    *,
    expected_tools,
    trajectories,
    evidence=None,
    diagnostic_metrics=None,
    sample_classes=None,
) -> _EvalResult:
```

Add at the top of the body:

```python
    if len(expected_tools) != len(questions):
        raise ValueError(
            f"expected_tools ({len(expected_tools)}) != questions ({len(questions)})"
        )
    if len(trajectories) != len(questions):
        raise ValueError(
            f"trajectories ({len(trajectories)}) != questions ({len(questions)})"
        )
```

In the row construction, add:

```python
                "expected_tools": expected_tools[i],
                "trajectory": trajectories[i],
```

Update the docstring Args accordingly.

- [ ] **Step 7: Pass the data from `main()`**

In `src/cli/eval_deepeval.py:1417`, change the call to:

```python
            result = run_evaluation(
                questions,
                ground_truth,
                answers,
                contexts,
                metrics,
                trace_ids,
                expected_tools=expected_tools,
                trajectories=trajectories,
                evidence=judge_evidence,
                diagnostic_metrics=diagnostic_metrics,
                sample_classes=sample_classes,
            )
```

- [ ] **Step 8: Update existing tests**

In `tests/cli/test_eval_deepeval.py`, both `run_evaluation` calls get:

```python
            expected_tools=[None, None],
            trajectories=[
                {"tool_calls": [], "iterations": 2},
                {"tool_calls": [], "iterations": 2},
            ],
```

For `test_evaluation_empty_contexts`, use `expected_tools=[None]` and `trajectories=[{"tool_calls": [], "iterations": 2}]`.

In `tests/cli/test_eval_native_diagnostics.py`:

- `build_diagnostic_metrics` list becomes `["contextual_relevancy", "non_advice", "tool_correctness"]`.
- Empty-sample test now needs `expected_tools` and `trajectory` on the row. For a failed row (`answer="[ERROR] boom"`), assert `tool_correctness is None` and `tool_correctness_reason == "failed"`.
- 不要在这个文件里加“empty + 有标签 = 0”的用例；该路径已由 `tests/cli/test_eval_tool_correctness.py::test_empty_sample_with_labels_scores_zero` 覆盖（它需要真实的 tool 指标实例，而本文件的 `_Bomb` 只适合断言不调用）。

- [ ] **Step 9: Run the targeted tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_tool_correctness.py tests/cli/test_eval_native_diagnostics.py tests/cli/test_eval_deepeval.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/cli/eval_scoring.py src/cli/eval_deepeval.py tests/cli/test_eval_tool_correctness.py tests/cli/test_eval_native_diagnostics.py tests/cli/test_eval_deepeval.py
git commit -m "feat(eval): score tool correctness per sample"
```

---

### Task 4: Persist and report tool correctness

**Files:**
- Modify: `src/cli/eval_deepeval.py:823-870`（`save_results_csv`）、`1582-1640`（`_build_eval_detail`）、`1033-1145`（`save_markdown_report`）、新增 `_build_tool_correctness_section`
- Test: `tests/cli/test_eval_deepeval_detail.py`
- Test: `tests/cli/test_eval_deepeval.py`（CSV schema）
- Test: `tests/cli/test_deepeval_only.py`

**Interfaces:**
- Consumes: Task 3 的 `tool_correctness` / `tool_correctness_reason` 列
- Produces: `_build_tool_correctness_section(df) -> list[str]`

- [ ] **Step 1: Write the failing detail/report tests**

In `tests/cli/test_eval_deepeval_detail.py`, replace the first test with:

```python
def test_detail_includes_tool_correctness_score_reason_and_null_na():
    df = pd.DataFrame(
        {
            "faithfulness": [0.8, 0.7],
            "answer_relevancy": [0.7, 0.6],
            "context_precision": [0.6, 0.5],
            "context_recall": [0.5, 0.4],
            "contextual_relevancy": [0.4, float("nan")],
            "non_advice": [1.0, 1.0],
            "tool_correctness": [1.0, float("nan")],
            "tool_correctness_reason": ["", "failed"],
            "error": ["", "sample_class=empty"],
        }
    )

    detail, _ = _build_eval_detail(df, 2, None)

    assert detail[0]["tool_correctness"] == 1.0
    assert detail[0]["tool_correctness_reason"] == ""
    assert detail[1]["tool_correctness"] is None
    assert detail[1]["tool_correctness_reason"] == "failed"
```

Add a report test:

```python
def test_report_tool_section_counts_and_fully_matched(tmp_path):
    from unittest.mock import MagicMock

    from src.cli.eval_deepeval import save_markdown_report

    df = pd.DataFrame(
        {
            "faithfulness": [0.9, 0.8, 0.7],
            "answer_relevancy": [0.9, 0.8, 0.7],
            "context_precision": [0.9, 0.8, 0.7],
            "context_recall": [0.9, 0.8, 0.7],
            "contextual_relevancy": [0.5, 0.5, 0.5],
            "non_advice": [1.0, 1.0, 1.0],
            "tool_correctness": [1.0, 0.0, float("nan")],
            "tool_correctness_reason": ["", "missing search_web", ""],
        }
    )
    mock_result = MagicMock()
    mock_result.to_pandas.return_value = df
    csv_path = str(tmp_path / "report.csv")

    save_markdown_report(
        mock_result,
        ["Q1", "Q2", "Q3"],
        csv_path,
        sample_classes=["pure_kb", "pure_kb", "pure_kb"],
    )

    content = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "工具选择" in content
    assert "Fully matched:** 1/2" in content
    assert "no_label=1" in content
    assert "failed=0" in content
    assert "metric_error=0" in content
    assert "scored=2" in content
    assert "missing search_web" in content
    main_line = next(
        line for line in content.splitlines() if line.startswith("**Main scores")
    )
    assert "tool_correctness" not in main_line
```

- [ ] **Step 2: Run it and verify it fails**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_deepeval_detail.py -v`
Expected: FAIL — `tool_correctness` still None / no 工具选择 section.

- [ ] **Step 3: Update `_build_eval_detail`**

Replace the hardcoded line:

```python
        entry["tool_correctness"] = None
```

with:

```python
        if "tool_correctness" in df.columns:
            tool_value: Any = df["tool_correctness"].iloc[i]
            if pd.isna(tool_value):
                entry["tool_correctness"] = None
            else:
                entry["tool_correctness"] = float(tool_value)
        else:
            entry["tool_correctness"] = None
        if "tool_correctness_reason" in df.columns:
            entry["tool_correctness_reason"] = str(
                df["tool_correctness_reason"].iloc[i]
            )
        else:
            entry["tool_correctness_reason"] = ""
```

Update the docstring return text to remove "恒 None".

- [ ] **Step 4: Drop the reason column from CSV**

In `save_results_csv`, after `df = apply_sample_masks(...)`, add:

```python
    # reason 只进 detail/Markdown，不进 CSV，避免无意改变消费方 schema
    if "tool_correctness_reason" in df.columns:
        df = df.drop(columns=["tool_correctness_reason"])
```

- [ ] **Step 5: Add the report section**

Add above `_build_behaviour_section`:

```python
def _build_tool_correctness_section(df: pd.DataFrame) -> list[str]:
    """渲染「工具选择」诊断段：逐题分数、原因、均值与计数。"""
    lines = ["## 工具选择（期望工具调用命中，诊断）", ""]
    lines.append(
        "> 只比较工具名与类型（类型固定 FUNCTION）；不比较参数、顺序、调用次数。"
    )
    lines.append("")
    if "tool_correctness" not in df.columns:
        lines.append("工具选择：N/A（无标签或未产出）")
        return lines

    scores = df["tool_correctness"]
    reasons = (
        df["tool_correctness_reason"]
        if "tool_correctness_reason" in df.columns
        else pd.Series([""] * len(df), index=df.index)
    )
    reason_text = reasons.fillna("").astype(str)
    total = len(df)
    scored = int(scores.notna().sum())
    failed = int((reason_text == "failed").sum())
    metric_error = int(
        reason_text.str.startswith(("tool_correctness:", "metric_unavailable")).sum()
    )
    no_label = total - scored - failed - metric_error

    lines.append("| Question | tool_correctness | reason |")
    lines.append("|---|---|---|")
    for i in range(total):
        raw = scores.iloc[i]
        value = "N/A" if pd.isna(raw) else f"{float(raw):.4f}"
        reason = " ".join(str(reason_text.iloc[i]).split()).replace("|", "/")
        lines.append(f"| Q{i + 1} | {value} | {reason} |")

    mean_text = "N/A" if scored == 0 else f"{float(scores.mean()):.4f}"
    fully = int((scores == 1.0).sum())
    lines.append("")
    lines.append(f"**Tool correctness mean (scored):** {mean_text}")
    lines.append(f"**Fully matched:** {fully}/{scored}")
    lines.append(
        "**Counts:** "
        f"qa_count={total}, no_label={no_label}, failed={failed}, "
        f"metric_error={metric_error}, scored={scored}"
    )
    violations = [
        f"Q{i + 1} ({reason_text.iloc[i]})"
        for i in range(total)
        if not pd.isna(scores.iloc[i]) and float(scores.iloc[i]) < 1.0
    ]
    if violations:
        lines.append("**Violations:** " + "; ".join(violations))
    return lines
```

- [ ] **Step 6: Wire the section into `save_markdown_report`**

After `lines.extend(_build_diagnostic_section(df))` and its blank line, add:

```python
        lines.extend(_build_tool_correctness_section(df))
        lines.append("")
```

For the `result is None` branch, add before the behaviour section:

```python
    if result is None:
        lines.extend(
            [
                "## 工具选择（诊断）",
                "",
                "工具选择：未跑（--deepeval-only）",
                "",
            ]
        )
```

- [ ] **Step 7: Update CSV tests**

In `tests/cli/test_eval_deepeval.py` `test_save_csv`, add these columns to the existing `mock_df`:

```python
                "tool_correctness": [1.0, 0.0],
                "tool_correctness_reason": ["", "missing search_web"],
```

Then add after reading the CSV back:

```python
        assert "tool_correctness" in df.columns
        assert "tool_correctness_reason" not in df.columns
```

Use the test's existing file-reading path; if it currently reads via pandas, the `df` above is the read-back frame.

- [ ] **Step 8: Update `--deepeval-only` test**

In `tests/cli/test_deepeval_only.py`, assert the report contains:

```python
    assert "工具选择：未跑（--deepeval-only）" in text
```

- [ ] **Step 9: Run the targeted tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_deepeval_detail.py tests/cli/test_eval_deepeval.py tests/cli/test_deepeval_only.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/cli/eval_deepeval.py tests/cli/test_eval_deepeval_detail.py tests/cli/test_eval_deepeval.py tests/cli/test_deepeval_only.py
git commit -m "feat(eval): persist and report tool correctness"
```

---

### Task 5: Document and verify

**Files:**
- Modify: `src/cli/README.md`
- Modify: `docs/agents/eval-ledger.md`
- Modify: `docs/agents/glossary.md`
- Test: full targeted suite + offline replay

- [ ] **Step 1: Update the CLI README**

In `src/cli/README.md`:

- 内部流程第 1 步改为“加载 questions / ground_truth / expected_tools”。
- 诊断说明补：`tool_correctness` = 命中期望工具数 / 去重后期望数；只比较工具名与类型；`[]` 表示期望不调工具；缺失/失败为 N/A。
- CSV 列说明：保留 `tool_correctness`，不新增 `tool_correctness_reason`。
- `--deepeval-only` 说明补“工具选择：未跑”。

- [ ] **Step 2: Update the evaluation ledger**

In `docs/agents/eval-ledger.md`:

- 把“`tool_correctness` 未启用”改为启用口径。
- 记录本轮基线：`scored=22`、`Fully matched=22/22`、`no_label=0`、`failed=0`、`metric_error=0`。
- 注明数字来源：用 `deepeval_eval_20261018_041404.csv` 的 22 条轨迹 + v6 标签离线重放，不是一次新的 agent 运行。

- [ ] **Step 3: Add the glossary term**

In `docs/agents/glossary.md`, add a `tool_correctness` entry:

```markdown
- **tool_correctness**：命中的期望工具数 / 去重后的期望工具数（0.0–1.0）。只比较工具名与类型（类型固定 FUNCTION），不比较参数、顺序、调用次数；`expected_tools=[]` 表示期望不调工具，字段缺失或跑图失败/空回答为 N/A。属诊断指标，不进主分/门禁/Langfuse。
```

- [ ] **Step 4: Run the full targeted suite**

Run:

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest \
  tests/cli/test_testset_io_expected_tools.py \
  tests/cli/test_eval_tool_correctness.py \
  tests/cli/test_eval_deepeval.py \
  tests/cli/test_eval_deepeval_detail.py \
  tests/cli/test_eval_deepeval_limit.py \
  tests/cli/test_eval_native_diagnostics.py \
  tests/cli/test_behaviour_fail_loud.py \
  tests/cli/test_behaviour_report.py \
  tests/cli/test_deepeval_only.py \
  tests/test_tracing_order.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the offline 22-question replay**

Use the existing baseline CSV and v6 labels; do not run a new agent evaluation. Run:

```bash
POSTGRES_HOST=localhost .venv/bin/python - <<'PY'
import json
import pandas as pd
from deepeval.config.eval_mode import EvalMode
from deepeval.metrics import ToolCorrectnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, ToolCall

class Fake(DeepEvalBaseLLM):
    def load_model(self): return self
    def generate(self, prompt, **kwargs): return "{}"
    async def a_generate(self, prompt, **kwargs): return "{}"
    def get_model_name(self): return "fake"

samples = json.load(open("data/deepeval/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v6.json"))["samples"]
df = pd.read_csv("data/deepeval/reports/deepeval_eval_20261018_041404.csv")
metric = ToolCorrectnessMetric(model=Fake(), async_mode=False, eval_mode="llm")
scores = []
for sample, (_, row) in zip(samples, df.iterrows(), strict=True):
    traj = json.loads(row["trajectory"])
    case = LLMTestCase(
        input=sample["user_input"],
        tools_called=[ToolCall(name=c["name"], input_parameters=c.get("args") or {}) for c in traj["tool_calls"]],
        expected_tools=[ToolCall(name=n) for n in sample["expected_tools"]],
    )
    scores.append(metric.measure(case))
print({"n": len(scores), "fully_matched": sum(s == 1.0 for s in scores), "mean": sum(scores) / len(scores)})
PY
```

Expected: `{'n': 22, 'fully_matched': 22, 'mean': 1.0}`.

- [ ] **Step 6: Run formatting and lint**

```bash
.venv/bin/ruff format src/cli/testset_io.py src/cli/eval_scoring.py src/cli/eval_deepeval.py tests/cli/test_testset_io_expected_tools.py tests/cli/test_eval_tool_correctness.py
.venv/bin/ruff check src/cli tests/cli
```

Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add src/cli/README.md docs/agents/eval-ledger.md docs/agents/glossary.md
git commit -m "docs(eval): document tool correctness and record baseline"
```

---

## Self-Review Checklist

- [ ] 每个 spec scenario 都有实现/测试：三态、非空标签、去重、多工具、空标签、失败、无标签、委派、非行为轴、不进主分、detail null、报告计数、CSV schema、`--deepeval-only`。
- [ ] 没有 `retrieve_kb` 特殊分支；测试至少用了 `search_web`。
- [ ] `tool_correctness` 不进入 `DIAGNOSTIC_METRIC_COLUMNS`，`apply_sample_masks` 不会覆盖。
- [ ] `metric.reason` 没有被持久化；reason 是自行计算的单行字符串。
- [ ] `tools_called` / `expected_tools` 空集合传 `[]`，不是 `None`。
- [ ] `assert_behaviour_scores_complete` 只对 `failed=True` 放行；非 failed 空轨迹仍然抛错。
- [ ] CSV 不含 `tool_correctness_reason`；detail 含该键。
- [ ] `--deepeval-only` 报告有“未跑”文案。
- [ ] 所有命令都带 `POSTGRES_HOST=localhost`（宿主侧）。
