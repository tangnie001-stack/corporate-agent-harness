# deepeval-only-eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给评测 CLI 一个 `--deepeval-only` 模式——只跑「跑图 + Agent 行为轴 + Markdown 报告」，跳过 RAGAS 四指标、CSV 与 `eval_report` 落库，让 DeepEval 功能可以在不背 56 分钟四指标评分的前提下快速迭代验收。

**Architecture:** 在 `main()` 里引入一个布尔 `ragas_enabled = not args.deepeval_only`，用它包住「四指标度量构造 → 评分 → 主分表 → CSV → 落库 → Langfuse 回写 → 门禁」整段；报告侧给 `save_markdown_report` 与 `build_report_meta` 增加「无 RAGAS」分支，显式标注而不是输出 0 值。默认路径（不加开关）逐字节不变。

**Tech Stack:** Python 3.12 · argparse · pytest 9 · DeepEval 4.2.9

**Spec:** `docs/openspec/changes/deepeval-only-eval/{proposal.md, design.md, specs/evaluation-pipeline/spec.md, tasks.md}`（**唯一权威**；本计划只做"怎么落地"）。

## Global Constraints

- 开关为 `--deepeval-only`，**默认关闭**；关闭时四指标、CSV、落库、Langfuse 回写、门禁**全部保持现状**。
- 开启时：**不构造**四指标度量、**不调用** `run_evaluation`、**不写** `eval_report`、**不写** CSV、**不回写** Langfuse（缺 `masked_df`）、**不执行**门禁。
- 开启时报告 SHALL 标注 `RAGAS: 未跑（--deepeval-only）`，SHALL NOT 输出四指标数值行，SHALL NOT 把缺失写成 `0`。
- `--deepeval-only` 与 `--gate` **互斥**，启动即 `parser.error`（非零退出）。
- 行为轴、`--limit` 冒烟、`.md` 落盘保持可用。
- **不改**：四指标实现、桥、`src/agents/**`、`kb_eval` API 形状、前端、`eval_report` schema。
- 质量门禁：`pytest tests/ -q` 全绿；`ruff format --check src/ tests/`；`ruff check src/ tests/` 与 `pyright src/` **不新增** error（既有：`engine.py:33` UP047 ×1、`eval_ragas_generate.py` ×2）；`openspec validate deepeval-only-eval --strict`；`check_docs`；`check_adr`。

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `src/cli/eval_ragas.py` | 评测 CLI 编排 | 加 `--deepeval-only` + 互斥；`main()` 用 `ragas_enabled` 包住四指标段；`build_report_meta` / `save_markdown_report` 增加无 RAGAS 分支 |
| `src/cli/README.md` | CLI 使用说明 | 补 `--deepeval-only` 用法与限制 |
| `docs/agents/ragas-eval-ledger.md` | 评测台账 | 注明该模式以「口径版本 = `deepeval-only`」单列 |
| `tests/cli/test_deepeval_only.py` | **新建** | 参数互斥 / meta 形状 / 无 RAGAS 报告 |
| `docs/superpowers/plans/2026-10-17-deepeval-only-eval.md` | 本计划 | 阶段末追加执行记录 |

---

### Task 1: `--deepeval-only` 参数、互斥与跳过四指标

**Files:**
- Modify: `src/cli/eval_ragas.py:103-131`（`parse_args`）
- Modify: `src/cli/eval_ragas.py:1059-1160`（`main()` 的四指标段）
- Test: `tests/cli/test_deepeval_only.py`（新建）

**Interfaces:**
- Produces: `args.deepeval_only: bool`；`main()` 内 `ragas_enabled: bool`。
- 互斥：`--deepeval-only` + `--gate` → `parser.error(...)`（`SystemExit.code == 2`）。

- [ ] **Step 1: 写失败测试（参数与互斥）**

新建 `tests/cli/test_deepeval_only.py`：

```python
"""`--deepeval-only` 的参数与互斥语义。"""

from __future__ import annotations

import sys

import pytest

from src.cli import eval_ragas


def _parse(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["eval_ragas", *argv])
    return eval_ragas.parse_args()


def test_deepeval_only_defaults_to_false(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb-x"])
    assert args.deepeval_only is False


def test_deepeval_only_flag_is_accepted(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb-x", "--deepeval-only"])
    assert args.deepeval_only is True


def test_deepeval_only_and_gate_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["eval_ragas", "--kb-id", "kb-x", "--deepeval-only", "--gate"]
    )
    with pytest.raises(SystemExit) as excinfo:
        eval_ragas.parse_args()
    assert excinfo.value.code == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_deepeval_only.py -v`
Expected: **FAIL** —— `AttributeError: 'Namespace' object has no attribute 'deepeval_only'`（前两条），第三条不会抛 `SystemExit`。

- [ ] **Step 3: 实现参数与互斥**

在 `src/cli/eval_ragas.py` 的 `parse_args` 中，`--testset-version` 之后、`--size` 之前插入：

```python
    parser.add_argument(
        "--deepeval-only",
        action="store_true",
        help="只跑 Agent 行为轴，跳过 RAGAS 四指标（无主分/门禁；不写 eval_report）",
    )
```

并在 `return parser.parse_args()` **之前**加：

```python
    args = parser.parse_args()
    if args.deepeval_only and args.gate:
        parser.error("--deepeval-only 与 --gate 不能同用：该模式没有主分可判")
    return args
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_deepeval_only.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 在 `main()` 跳过四指标评分**

在 `main()` 的评估段引入开关（`kb_id` 解析之后、`configure_tracing()` 之前的注释区）：

```python
    # ---- 评估模式 ----
    # --deepeval-only：只跑行为轴；四指标整段（度量/评分/主分表/CSV/落库/回写/门禁）跳过。
    ragas_enabled = not args.deepeval_only
```

把 `metrics = build_metrics(bridge_judge, build_embeddings())`（当前 `:1061`）改为：

```python
        metrics = None
        if ragas_enabled:
            metrics = build_metrics(bridge_judge, build_embeddings())
```

把 `result = run_evaluation(...)` 到 `_print_metric_averages(masked_df)` 这一整段（当前 `:1131-1160`）包进 `if ragas_enabled:`，并把 `result` / `masked_df` 预先置 `None`：

```python
        result = None
        masked_df = None
        if ragas_enabled:
            result = run_evaluation(
                questions,
                ground_truth,
                answers,
                contexts,
                metrics,
                trace_ids,
                evidence=judge_evidence,
            )
            masked_df = apply_sample_masks(result.to_pandas(), sample_classes)
            if not args.gate:
                _print_metric_averages(masked_df)
```

- [ ] **Step 6: 跑既有测试确认默认路径未变**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_ragas.py tests/cli/test_deepeval_only.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_deepeval_only.py
git commit -m "feat(eval): --deepeval-only 参数与互斥，main 跳过四指标评分"
```

---

### Task 2: 报告 / 落库 / CSV 的无 RAGAS 语义

**Files:**
- Modify: `src/cli/eval_ragas.py:894-935`（`build_report_meta`）
- Modify: `src/cli/eval_ragas.py:789-835`（`save_markdown_report`）
- Modify: `src/cli/eval_ragas.py:1147-1207`（`main()` 的报告与落库段）
- Test: `tests/cli/test_deepeval_only.py`（扩展）

**Interfaces:**
- `build_report_meta(*, judge_model: str, nan_stats: dict | None, runner_versions: dict, ragas_enabled: bool = True) -> dict`
  - `ragas_enabled=False` 时返回 `{"caliber": "deepeval-only", "judge_model": ..., "model_arg_explicit": True, "ragas_skipped": True, "runner_versions": {...}}`（**不含** `nan_*` / `temperature_*`）。
- `save_markdown_report(result: Any | None, ...)`：`result is None` 时只写头部 + 行为轴，不写逐题指标表/主分行。

- [ ] **Step 1: 写失败测试（meta 形状）**

在 `tests/cli/test_deepeval_only.py` 追加：

```python
def test_report_meta_without_ragas_marks_skipped():
    from src.cli.eval_ragas import build_report_meta

    meta = build_report_meta(
        judge_model="qwen-test",
        nan_stats=None,
        runner_versions={"ragas": "0.3.9", "deepeval": "4.2.9"},
        ragas_enabled=False,
    )
    assert meta["ragas_skipped"] is True
    assert meta["caliber"] == "deepeval-only"
    assert "nan_cells" not in meta
    assert "temperature_regime" not in meta
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_deepeval_only.py::test_report_meta_without_ragas_marks_skipped -v`
Expected: FAIL（`TypeError: build_report_meta() got an unexpected keyword argument 'ragas_enabled'`）

- [ ] **Step 3: 实现 `build_report_meta` 分支**

把 `build_report_meta` 改为：

```python
def build_report_meta(
    *,
    judge_model: str,
    nan_stats: dict | None,
    runner_versions: dict,
    ragas_enabled: bool = True,
) -> dict:
    """报告/台账元数据（口径版本、温度档、NaN 计数、模型显式性）。

    `ragas_enabled=False` 时（--deepeval-only）不产出温度档与 NaN 字段：
    四指标未跑，这些数字没有意义，报告头改为显式标注「RAGAS 未跑」。
    温度**不由本项目控制**（ragas 按 n 分档），故只记录事实，
    SHALL NOT 出现「temperature=0」的表述。
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

- [ ] **Step 4: 实现 `save_markdown_report` 的 `result=None` 路径**

把 `save_markdown_report` 的 `result` 参数类型改为 `Any | None`，并在函数体开头（`md_path` 之后）分叉：

```python
    md_path = output_path.rsplit(".", 1)[0] + ".md"
    lines: list[str] = []
    lines.append("# RAGAS Evaluation Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"**Configuration:** TOP_K_RETRIEVAL={settings.TOP_K_RETRIEVAL}, "
        f"TOP_K_RERANK={settings.TOP_K_RERANK}"
    )
    lines.append(f"**QA Pairs:** {len(questions)}")
    if report_meta is not None:
        if report_meta.get("ragas_skipped"):
            lines.append(
                "**RAGAS:** 未跑（--deepeval-only）——本报告只含 Agent 行为轴，"
                "无主分、无门禁语义"
            )
            versions = report_meta["runner_versions"]
            lines.append(
                "**Runner versions:** "
                f"ragas={versions['ragas']}, deepeval={versions['deepeval']}"
            )
        else:
            # 既有分支：逐字保留（原 `src/cli/eval_ragas.py:786-809`）
            lines.append(
                "**Caliber / Judge:** "
                f"caliber={report_meta['caliber']}, "
                f"judge={report_meta['judge_model']}, "
                f"model_arg_explicit={report_meta['model_arg_explicit']}"
            )
            regime = report_meta["temperature_regime"]
            lines.append(
                "**Temperature regime:** "
                f"n1_metrics={regime['n1_metrics']}, "
                f"answer_relevancy={regime['answer_relevancy']} "
                f"({report_meta['temperature_control']})"
            )
            versions = report_meta["runner_versions"]
            lines.append(
                "**Runner versions:** "
                f"ragas={versions['ragas']}, deepeval={versions['deepeval']}"
            )
            lines.append(
                "**NaN cells:** "
                f"{report_meta['nan_cells']}/{report_meta['nan_denominator_cells']} "
                f"(rate={report_meta['nan_rate']})"
            )
    lines.append("")

    if result is not None:
        df = apply_sample_masks(result.to_pandas(), sample_classes)
        actual_metrics = [c for c in RAGAS_METRIC_COLUMNS if c in df.columns]
        lines.extend(_build_markdown_table(df, actual_metrics, trace_ids, trajectories))
        lines.append("")
        means = main_score_means(df)
        avg_parts = ", ".join(f"{m}={means[m]:.4f}" for m in actual_metrics if m in means)
        lines.append(f"**Main scores (pure-KB):** {avg_parts}")
        lines.append("")

    if behaviour_rows is not None:
        lines.extend(_build_behaviour_section(behaviour_rows))
        lines.append("")
```

> 注意：`df = apply_sample_masks(...)` 原本在函数开头、`result.to_pandas()` 之后；重构后必须只在 `result is not None` 分支里求值，且 `_build_markdown_table` / `main_score_means` 都依赖它。

- [ ] **Step 5: 写失败测试（无 RAGAS 报告）**

在 `tests/cli/test_deepeval_only.py` 追加：

```python
def test_markdown_report_without_ragas_has_no_metric_rows(tmp_path):
    from src.cli.eval_ragas import build_report_meta, save_markdown_report

    out = tmp_path / "r.csv"
    save_markdown_report(
        None,
        questions=["Q0"],
        output_path=str(out),
        sample_classes=["pure_kb"],
        report_meta=build_report_meta(
            judge_model="qwen-test",
            nan_stats=None,
            runner_versions={"ragas": "0.3.9", "deepeval": "4.2.9"},
            ragas_enabled=False,
        ),
        behaviour_rows=[
            {"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}}
        ],
    )
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "RAGAS:** 未跑" in text
    assert "Main scores (pure-KB)" not in text
    assert "faithfulness=" not in text
    assert "Agent 行为轴" in text
```

- [ ] **Step 6: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_deepeval_only.py -v`
Expected: PASS（5 passed）

- [ ] **Step 7: 在 `main()` 接线（报告/CSV/落库/回写）**

把 `main()` 的报告与落库段（当前 `:1147-1207`）改为：

```python
        report_meta = build_report_meta(
            judge_model=bridge_judge.model_name,
            nan_stats=nan_cell_stats(masked_df) if masked_df is not None else None,
            runner_versions={
                "ragas": version("ragas"),
                "deepeval": version("deepeval"),
            },
            ragas_enabled=ragas_enabled,
        )

        if ragas_enabled:
            output_path = save_results_csv(
                result,
                questions,
                ground_truth,
                output_path,
                sample_classes=sample_classes,
                trace_ids=trace_ids,
                trajectories=trajectories,
            )

        save_markdown_report(
            result,
            questions,
            output_path,
            sample_classes=sample_classes,
            trace_ids=trace_ids,
            trajectories=trajectories,
            report_meta=report_meta,
            behaviour_rows=behaviour_rows,
        )

        if ragas_enabled:
            if len(masked_df) != len(trace_ids):
                raise RuntimeError(
                    f"eval rows ({len(masked_df)}) != trace_ids ({len(trace_ids)})"
                )
            _save_eval_report(
                kb_id,
                result.to_pandas(),
                len(questions),
                output_path,
                retrieval_details,
                sample_classes,
            )
            _write_langfuse_scores(masked_df, trace_ids, behaviour_rows)

        if args.gate:
            check_gate(masked_df, questions)
```

> `--deepeval-only` 与 `--gate` 已在 `parse_args` 互斥，故 `check_gate` 不会在无 `masked_df` 时被调用。

- [ ] **Step 8: 跑 CLI 测试并提交**

Run: `POSTGRES_HOST=localhost pytest tests/cli -q`
Expected: 全绿

```bash
git add src/cli/eval_ragas.py tests/cli/test_deepeval_only.py
git commit -m "feat(eval): --deepeval-only 的报告/落库/CSV 语义（不写假 0、不落库、不写 CSV）"
```

---

### Task 3: 文档与台账口径

**Files:**
- Modify: `src/cli/README.md`
- Modify: `docs/agents/ragas-eval-ledger.md`

- [ ] **Step 1: 补 CLI 文档**

在 `src/cli/README.md` 的运行方式段追加：

```markdown
python -m src.cli.eval_ragas --kb-id <kb-uuid> --testset-version 5 --deepeval-only   # 只跑行为轴（无主分/门禁）
```

并在其下补一句：

```markdown
`--deepeval-only` 跳过 RAGAS 四指标：**不产出主分、不写 CSV、不写 `eval_report`、不回写 Langfuse、不执行门禁**，报告只含 Agent 行为轴。它用于 DeepEval 功能的快速迭代验收，**不要用于 A/B 对比或发布判定**。与 `--gate` 互斥。
```

- [ ] **Step 2: 台账口径**

在 `docs/agents/ragas-eval-ledger.md` 的「公共配置」段之后、运行台账之前，插入：

```markdown
## 口径说明：`deepeval-only` 运行

以 `--deepeval-only` 跑的评测**不产出 RAGAS 四指标**，故不进入运行台账的分数对照；如需登记，口径版本列记 `deepeval-only`，并在「主要异常」注明"仅行为轴"。它只用于 DeepEval 功能的迭代验收，**不参与发布门禁与跨轮分数比较**。
```

- [ ] **Step 3: 校验并提交**

Run: `python -m src.cli.check_docs`
Expected: 0 error

```bash
git add src/cli/README.md docs/agents/ragas-eval-ledger.md
git commit -m "docs(eval): --deepeval-only 用法与台账口径"
```

---

### Task 4: 验收（静态闸门 + 1/5/21 活体）

**Files:**
- Modify: `docs/superpowers/plans/2026-10-17-deepeval-only-eval.md`（执行记录）

**固定值**
- KB：`4a1dcb8bb340473c8149ead5b7f75873`
- 题集版本：`5`（21 条）
- 前缀：`POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas --kb-id 4a1dcb8bb340473c8149ead5b7f75873 --testset-version 5 --deepeval-only`

- [ ] **Step 1: 静态闸门**

```bash
POSTGRES_HOST=localhost pytest tests/ -q
ruff format --check src/ tests/
ruff check src/ tests/
.venv/bin/pyright src/
openspec validate deepeval-only-eval --strict
python -m src.cli.check_docs
python -m src.cli.check_adr
```

Expected：pytest 全绿；ruff format clean；`ruff check` / `pyright` 仅既有 3 条；两个校验器 0 error。

- [ ] **Step 2: 1 题**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 5 --deepeval-only --limit 1
```

Expected：退出码 0；`data/ragas/reports/` 新增 `.md`（**无同名 `.csv`**）；md 含 `RAGAS:** 未跑` 与 `## Agent 行为轴`；`eval_report` 无新行。

校验无 CSV：

```bash
MD=$(ls -t data/ragas/reports/*.md | head -1)
ls "${MD%.md}.csv" 2>/dev/null && echo "FAIL: CSV 不应存在" || echo "OK: 无 CSV"
```

- [ ] **Step 3: 5 题**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 5 --deepeval-only --limit 5
```

Expected：`**QA Pairs:** 5`；行为轴 5 行；md 无主分行。

- [ ] **Step 4（可选，不阻塞验收）: 21 题全量（记录耗时）**

```bash
time POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 5 --deepeval-only
```

Expected：`**QA Pairs:** 21`；行为轴 21 行；耗时显著低于 RAGAS 全量的 55m42s（预期 10–15 分钟）。

- [ ] **Step 5: 默认路径回归（1 题，确认未被改坏）**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 5 --limit 1
```

Expected：有四指标行、有 `.csv`、落库有行（与改前一致）。

- [ ] **Step 6: 写执行记录并提交**

在本计划末尾追加 `## 执行记录与交接（2026-10-17）`：三条活体的耗时与报告路径、默认路径回归结果、静态闸门、**未证明**（行为轴稳定性、RAGAS 角色仍未定）、交接（下一步 `deepeval-synthesizer-testset`）。

```bash
git add docs/superpowers/plans/2026-10-17-deepeval-only-eval.md
git commit -m "docs(superpowers): deepeval-only-eval 验收记录"
```

---

## 完成判据

1. `--deepeval-only` 默认关闭；关闭时行为与现状一致（默认路径 1 题回归通过）。
2. 开启时不构造/不调用四指标，不写 `eval_report`、不写 CSV、不回写 Langfuse、不执行门禁。
3. 报告含 `RAGAS: 未跑（--deepeval-only）` 与行为轴段，**不含**任何四指标数值行或假 0。
4. `--deepeval-only` + `--gate` 启动即非零退出。
5. 1 / 5 题活体必做且成功；21 题为可选（若做，耗时显著低于 55m42s）。
6. 静态闸门全过，无新增 ruff/pyright error。

## 未纳入本计划

- **DeepEval 出题**（`Synthesizer`）：独立 change `deepeval-synthesizer-testset`。
- **RAGAS 四指标的角色决策**（继续走桥 / 独立跑）：待 DeepEval 自闭环稳定后另定。
- **行为分回写 Langfuse**：该模式缺 `masked_df`；如需，另做（不阻塞）。
- **`eval_report` schema 改 nullable**：不采纳（见 design D2）。
