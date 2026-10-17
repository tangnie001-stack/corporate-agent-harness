# deepeval-bridge-eval · Phase 4（验收：逐级活体 + v5 基线记录）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用新尺子（DeepEval 桥 + ragas 0.3.9）在 v5 题集上跑出**第一份可复现的全量分布**，逐级确认 1 → 5 → 21 题、记录台账，并把「阈值标定」明确推迟到 v6 定稿后 —— 让 change 在**不把阈值建在已知噪声上**的前提下闭环。

**Architecture:** 不改评分链路。只在 CLI 加一个 `--limit N` 冒烟参数（逐级活体的唯一代码改动），然后按 1 → 5 → 21 三级运行 `--no-gate`（默认即不 gate，不加 `--gate`）并校验运行产物；最终写台账 v5 行、改写 design D8 的 v6 入口条件（解除「标定 ↔ v6」的循环依赖），并勾选 tasks.md 组 10 的可完成项。

**Tech Stack:** Python 3.12 · DeepEval 4.2.9 · ragas 0.3.9 · pytest 9 · Docker Compose（postgres/redis/minio/langfuse）

**Spec:** `docs/openspec/changes/deepeval-bridge-eval/{design.md, specs/evaluation-pipeline/spec.md, tasks.md}`（**唯一权威**；本计划只做"怎么落地"）。本阶段覆盖 **tasks.md 组 10 的可执行部分**（10.1 / 10.2 / 10.3 / 10.6 / 10.7 / 10.8），**不含 10.4 / 10.5**。

## Global Constraints

- **本阶段口径（已与用户对齐，C 折中）**：v5 上**只记录分布与台账**，**SHALL NOT 标定阈值**。`10.4`（阈值重标定）与 `10.5`（NaN 阈值复核）**延后到 v6 定稿后**，本轮保持未勾并在 tasks.md 注明。原因：R11 已实测 `faithfulness` 在本判官下不可复现（同题 `[0,1,0,1]`），在其上取阈值等于把门禁建在噪声上。
- **阈值与常量本轮 SHALL NOT 改动**：`GATE_THRESHOLDS`（`src/cli/eval_ragas.py:67`）、`EVAL_PASS_THRESHOLD`（`src/config/const.py:553`）、`EVAL_NAN_RATE_MAX`（`src/config/const.py:546`）一律不动。
- **`--limit` 只用于冒烟**：SHALL NOT 与 `--gate` 同用（小样本门禁无意义），SHALL NOT 改变全量路径的默认行为。
- **台账只追加**：`docs/agents/ragas-eval-ledger.md` 历史行 SHALL NOT 改；新行必填「口径版本 = `bridge-0.3.9 / v5`」与「逃生门 = 未启用」。
- **不碰**：`src/agents/**`、生产链路、`kb_eval` API 形状、前端、`docs/adr/**`（已归档决策）。
- **运行前置**（2026-10-17 已核实）：容器 `corporate-rag-postgres` 等 6 个均 healthy；`.env` 软链 `/root/code/corporate_rag/.env`；v5 题集 `data/ragas/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v5.json` 共 **21** 条。
- **宿主机运行前缀**：`POSTGRES_HOST=localhost`（`.env` 里是容器服务名 `postgres`，宿主机跑 CLI 必须覆盖）；全量运行预期 **30–60 分钟**（四指标串行 + 每题目行为轴裁判）。
- **质量门禁**：`pytest tests/ -q` 全绿；`ruff format --check src/ tests/` clean；`ruff check src/ tests/` 与 `pyright src/` **不新增 error**（既有债务：`engine.py:33` UP047 ×1、`eval_ragas_generate.py` ×2，均 BASE `b99cbb0d` 即存在）。

## 阶段划分

| 阶段 | 覆盖 tasks.md 组 | 状态 | 计划文档 |
|---|---|---|---|
| Phase 1（底座） | 组 1–4 | 已完成并合入 `dev-wsl` | `2026-10-16-deepeval-bridge-eval-phase1.md` |
| Phase 2（Agent 行为轴） | 组 5 | 已完成并合入 `dev-wsl` | `2026-10-17-deepeval-bridge-eval-phase2.md` |
| Phase 3（外围收口） | 组 6–9 + 组 5 遗留 | 已完成（分支 `feat/deepeval-bridge-eval`，`6f4e1d0e`） | `2026-10-17-deepeval-bridge-eval-phase3.md` |
| **Phase 4（本文件）** | 组 10 的 10.1/10.2/10.3/10.6/10.7/10.8 | 进行中 | `2026-10-17-deepeval-bridge-eval-phase4.md` |
| 后续 change | v6 题集 + 10.4/10.5 标定 + R11 处置 | 待立项（`deepeval-synthesizer-testset`） | — |

## File Structure

| 文件 | 责任 | 本阶段动作 |
|---|---|---|
| `src/cli/eval_ragas.py` | 评测 CLI 编排 | **新增** `_limit_testset` 纯函数 + `--limit` 参数 + `main()` 截断 |
| `src/cli/README.md` | CLI 使用说明 | 补 `--limit` 用法与"仅冒烟、勿配 `--gate`"说明 |
| `tests/cli/test_eval_limit.py` | **新建** | `_limit_testset` 的四类边界（None / 5 / 超长 / ≤0） |
| `docs/tmp/deepeval-poc/PHASE4-RUNS.md` | **新建** | 三级活体的运行记录（命令 / 耗时 / 报告路径 / 校验结果 / NaN 与行为分摘要） |
| `docs/agents/ragas-eval-ledger.md` | 评测台账 | **追加** v5 行（口径 `bridge-0.3.9 / v5`，逃生门 `未启用`） |
| `docs/tmp/deepeval-poc/RESULTS.md` | 调研/实测记录 | 追加「Phase 4：v5 新尺子基线」节 |
| `docs/openspec/changes/deepeval-bridge-eval/design.md` | 设计 | 改写 D8 的 v6 入口条件与两步验收（解除标定循环依赖） |
| `docs/openspec/changes/deepeval-bridge-eval/tasks.md` | 执行清单 | 勾选 10.1/10.2/10.3/10.6/10.7/10.8；10.4/10.5 注"延后至 v6 后" |
| `docs/superpowers/plans/2026-10-17-deepeval-bridge-eval-phase4.md` | 本计划 | 阶段末追加执行记录 |

---

### Task 1: `--limit` 冒烟参数（逐级活体的唯一代码改动）

**Files:**
- Modify: `src/cli/eval_ragas.py:75-131`（`parse_args`）与 `main()` 的测试集加载后（约 `:981`）
- Modify: `src/cli/README.md`（CLI 用法段）
- Test: `tests/cli/test_eval_limit.py`（新建）

**Interfaces:**
- Produces: `_limit_testset(questions: list[str], ground_truth: list[str], limit: int | None) -> tuple[list[str], list[str]]`
  - `limit is None` → 原样返回；
  - `limit <= 0` → `ValueError`；
  - `limit >= len(questions)` → 原样返回（不报错，便于"5 题"在小题集上退化为全量）；
  - 否则返回前 `limit` 条，`questions` 与 `ground_truth` 同步截断。
- 消费方：`main()` 在 `_load_latest_testset(...)` 之后调用它，随后才 `log_event(TESTSET_LOADED)` 与后续流程。

- [ ] **Step 1: 写失败测试**

新建 `tests/cli/test_eval_limit.py`：

```python
"""`--limit` 的截断语义（冒烟轮次用，不改变全量默认行为）。"""

import pytest

from src.cli.eval_ragas import _limit_testset


def test_none_returns_unchanged():
    q = ["q1", "q2", "q3"]
    g = ["g1", "g2", "g3"]
    out_q, out_g = _limit_testset(q, g, None)
    assert out_q == q
    assert out_g == g
    assert out_q is q  # 不复制，保持原对象


def test_limit_takes_prefix_of_both_lists():
    q = ["q1", "q2", "q3"]
    g = ["g1", "g2", "g3"]
    out_q, out_g = _limit_testset(q, g, 2)
    assert out_q == ["q1", "q2"]
    assert out_g == ["g1", "g2"]


def test_limit_larger_than_length_is_noop():
    q = ["q1", "q2"]
    g = ["g1", "g2"]
    out_q, out_g = _limit_testset(q, g, 5)
    assert out_q == q
    assert out_g == g


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValueError, match="--limit"):
        _limit_testset(["q1"], ["g1"], 0)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_limit.py -v`
Expected: `ImportError: cannot import name '_limit_testset'`（4 errors during collection）

- [ ] **Step 3: 实现 `_limit_testset` 与 `--limit`**

在 `src/cli/eval_ragas.py` 的 `parse_args` **之前**（`main` 之外）新增：

```python
def _limit_testset(
    questions: list[str], ground_truth: list[str], limit: int | None
) -> tuple[list[str], list[str]]:
    """按冒烟轮次截取前 `limit` 题；`None` 表示不截断。

    仅用于逐级活体（1 → 5 → 21）。`limit` 超过题数时原样返回，便于同一命令
    在不同规模的题集上复用；`limit <= 0` 是调用错误，直接失败。

    Args:
        questions: 题面列表
        ground_truth: 参考答案列表（与 questions 等长同序）
        limit: 保留的题数；None 表示全量

    Returns:
        (questions, ground_truth) 的截断副本或原对象

    Raises:
        ValueError: `limit` 非正
    """
    if limit is None:
        return questions, ground_truth
    if limit <= 0:
        raise ValueError(f"--limit 必须为正整数，得到 {limit}")
    if limit >= len(questions):
        return questions, ground_truth
    return questions[:limit], ground_truth[:limit]
```

在 `parse_args` 的 `--testset-version` 之后新增参数：

```python
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只跑前 N 题（冒烟用；勿与 --gate 同用）",
    )
```

在 `main()` 中，`_load_latest_testset(...)` 成功返回后、`log_event(TESTSET_LOADED, ...)` 之前插入：

```python
        questions, ground_truth = _limit_testset(
            questions, ground_truth, args.limit
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_limit.py tests/cli/test_eval_ragas.py -v`
Expected: PASS（新 4 条 + 既有全过）

- [ ] **Step 5: 同步 CLI 文档**

在 `src/cli/README.md` 的运行方式段（`python -m src.cli.eval_ragas --kb-id ...` 附近）补一行：

```markdown
python -m src.cli.eval_ragas --kb-id <kb-uuid> --testset-version 5 --limit 1   # 冒烟：只跑前 1 题（勿配 --gate）
```

并在其下补一句说明：

```markdown
`--limit N` 只用于逐级活体（1 → 5 → 21），**不要与 `--gate` 同用**——少数题上的门禁没有统计意义；全量口径仍是不加 `--limit`。
```

- [ ] **Step 6: 提交**

```bash
git add src/cli/eval_ragas.py src/cli/README.md tests/cli/test_eval_limit.py
git commit -m "feat(eval): --limit 冒烟参数（逐级活体用，不改变全量默认行为）"
```

---

### Task 2: 逐级活体 1 → 5 → 21（10.2 + 10.3）

> **本任务不适用 TDD**：交付物是**运行产物 + 校验记录**，不是新代码。每一步都必须留下命令、耗时、报告路径与校验结论。

**Files:**
- Create: `docs/tmp/deepeval-poc/PHASE4-RUNS.md`（运行记录，本任务持续追加）
- 产出于运行时：`data/ragas/reports/ragas_eval_<timestamp>.{csv,md}`、`data/ragas/reports/behaviour_<timestamp>/test_run_*.json`

**Interfaces:**
- Consumes: Task 1 的 `--limit`；CLI 现有参数 `--kb-id` / `--testset-version` / `--gate`（本次**不加** `--gate`）。
- Produces: 每级运行的记录条目；全量运行的报告路径供 Task 3 / Task 4 引用。

**固定值（逐字使用）**
- KB id：`4a1dcb8bb340473c8149ead5b7f75873`
- 题集版本：`5`（21 条）
- 运行前缀：`POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas --kb-id 4a1dcb8bb340473c8149ead5b7f75873 --testset-version 5`

- [ ] **Step 1: 建运行记录文件**

新建 `docs/tmp/deepeval-poc/PHASE4-RUNS.md`：

```markdown
# Phase 4 运行记录（v5 · DeepEval 桥 + ragas 0.3.9）

> 口径：`bridge-0.3.9 / v5`。本阶段**只记录分布，不标定阈值**（C 折中，见 Phase 4 计划）。
> 每级一行：命令 / 耗时 / 报告路径 / 关键校验。

## 运行环境

- 日期：2026-10-17
- 宿主：WSL，`.venv/bin/python`
- 容器：postgres / redis / minio / langfuse / nginx / app 均 healthy
- 命令前缀：`POSTGRES_HOST=localhost`
```

- [ ] **Step 2: 1 题冒烟**

Run（前台，预期 1–3 分钟）：

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 5 --limit 1
```

Expected：
- stdout 打印 `faithfulness: …` / `answer_relevancy: …` / `context_precision: …` / `context_recall: …` 四行；
- 退出码 0；
- `data/ragas/reports/` 新增一对 `.csv` / `.md`，且 `data/ragas/reports/behaviour_<同 timestamp>/test_run_*.json` 存在。

校验：

```bash
ls -t data/ragas/reports/*.md | head -1
ls -dt data/ragas/reports/behaviour_* | head -1
```

把报告路径、耗时、四指标值、`NaN cells` 行、行为轴表填入 `PHASE4-RUNS.md` 的「1 题」条。

- [ ] **Step 3: 5 题**

Run（前台，预期 5–12 分钟）：

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 5 --limit 5
```

校验与记录同 Step 2。额外确认：
- 报告 `**QA Pairs:** 5`；
- 行为轴段行数 = 5；
- 无 `RuntimeError: 行为分题数不齐`。

- [ ] **Step 4: 全量 21 题（本次唯一一次全量）**

Run（**后台**，预期 30–60 分钟；用 ≥3600s 超时或后台任务）：

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 5
```

Expected：
- 退出码 0；
- `**QA Pairs:** 21`；
- 主分行四个指标均有数值（允许个别 NaN，但必须记数）；
- 报告头 `**NaN cells:** x/76 (rate=…)`（分母 76 = 19 纯 KB × 4，若纯 KB 数不同以实际为准）；
- `## Agent 行为轴` 段存在，三列（Task Completion / Step Efficiency / Agent Loop Detection）均有分；
- 报告中 `**Caliber / Judge:** caliber=bridge-0.3.9 …` 与 `**Runner versions:** ragas=0.3.9, deepeval=…`。

校验（逐条勾，全部通过才记录）：

```bash
MD=$(ls -t data/ragas/reports/*.md | head -1)
grep -E '^\*\*QA Pairs:\*\*|^\*\*NaN cells:\*\*|^\*\*Main scores|^## Agent 行为轴' "$MD"
grep -c '^|' "$MD"
```

- [ ] **Step 5: 校验「行为分来自 `testCases[].metricsData`」**

Run（把 `<behaviour_dir>` 换成 Step 4 的目录）：

```bash
python3 - <<'PY'
import glob, json
f = sorted(glob.glob("data/ragas/reports/behaviour_*/test_run_*.json"))[-1]
d = json.load(open(f))
tc = d["testCases"]
assert len(tc) == 21, len(tc)
for t in tc:
    assert "metricsData" in t, t.get("input")
    names = [m["name"] for m in t["metricsData"]]
    assert names, t.get("input")
print("OK testCases=21; metricsData present; sample names:", names)
print("aggregate metricsScores keys:", [m.get("metric") for m in d.get("metricsScores", [])])
PY
```

Expected：打印 `OK testCases=21; metricsData present; …`。若失败 ⇒ 停，按 `docs/agents/troubleshooting.md` 查；**不得**改用 `metricsScores` 当逐题分。

- [ ] **Step 6: 取证「NaN 重试是否触发过」**

Run（本轮运行的日志按天落盘在 `logs/app_<YYYY-MM-DD>.log`；`LOG_DIR` 默认 `logs`，文件名带横线；若宿主机无 `logs/`，改用容器内 `/data/logs/`）：

```bash
DAY=$(date +%F)
echo "retry events: $(grep -c 'metric retry' "logs/app_${DAY}.log" 2>/dev/null || echo 0)"
echo "failed judge calls: $(grep -cE 'judge call.*ok=(False|false)' "logs/app_${DAY}.log" 2>/dev/null || echo 0)"
grep -E 'metric retry|judge call.*ok=(False|false)' "logs/app_${DAY}.log" 2>/dev/null | tail -5
```

Expected（两种都可接受，但**必须如实记录**）：
- **触发过**：`metric retry` ≥ 1，且能对上报告里某题的 NaN/`error` 列；
- **未触发**：两者均为 0，且报告 `NaN cells` 为 0 或可解释。

在 `PHASE4-RUNS.md` 的「21 题」条记录：retry 次数、失败 judge 调用次数、是否与报告 NaN 对应。

- [ ] **Step 7: 补全运行记录并提交**

在 `PHASE4-RUNS.md` 的「21 题」条填入：报告路径、开始/结束时间、耗时、四指标均值、NaN 计数与率、行为轴各指标均值、上述校验输出摘要。

```bash
git add docs/tmp/deepeval-poc/PHASE4-RUNS.md
git commit -m "docs(eval): Phase 4 逐级活体运行记录（v5 · 1/5/21 题）"
```

---

### Task 3: 台账追加 v5 行（10.7）

**Files:**
- Modify: `docs/agents/ragas-eval-ledger.md`（运行台账表末尾追加一行）
- Consumes: Task 2 Step 4 的全量报告与 Step 6 的记录。

**Interfaces:**
- 台账列（当前顺序）：`# | 开始（CST） | 报告文件 | 裁判配置 | 有效纯 KB | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness | 门禁 | 主要异常 | 口径版本 | 逃生门`
- 新行必填：`answer_correctness` = `N/A`（本口径已移除该指标）、`门禁` = `未跑（首轮 --no-gate）`、`口径版本` = `bridge-0.3.9 / v5`、`逃生门` = `未启用`。

- [ ] **Step 1: 从报告提取数值**

Run：

```bash
MD=$(ls -t data/ragas/reports/*.md | head -1)
grep -E '^\*\*Main scores|^\*\*NaN cells|^\*\*Caliber' "$MD"
```

把四指标均值、NaN 计数、报告文件名（不带路径）抄下来。

- [ ] **Step 2: 追加台账行**

在 `docs/agents/ragas-eval-ledger.md` 的运行台账表**最后一行之后**追加（示例数值来自 Step 1；`#` 顺延为 `5`，开始时间取全量运行的实际开始时间）：

```markdown
| 5 | 2026-10-17 10:xx | `ragas_eval_20261017_xxxxxx` | 桥裁判（思考 OFF · cap 8192 · 细粒度 `http_async_client` 超时 · SDK retries=0 · 串行评分） | 19 | <faith> | <ar> | <cp> | <cr> | N/A | 未跑（首轮 --no-gate） | <一句话；无异常则写「无」> | bridge-0.3.9 / v5 | 未启用 |
```

**纪律**：历史 #1–#4 行**一字不改**；本行数值必须来自 Step 1 的报告，不得估算。

- [ ] **Step 3: 校验表格完整性**

Run：

```bash
python3 - <<'PY'
import re
p = "docs/agents/ragas-eval-ledger.md"
lines = open(p, encoding="utf-8").read().splitlines()
rows = [l for l in lines if l.startswith("|") and l.count("|") >= 10]
widths = {len([c for c in r.split("|")]) for r in rows}
print("pipe-field counts:", widths)
assert len(widths) == 1, "台账列数不一致"
PY
```

Expected：输出单一 `pipe-field counts: {16}`（14 列 + 首尾两个空段）；断言通过。

- [ ] **Step 4: 提交**

```bash
git add docs/agents/ragas-eval-ledger.md
git commit -m "docs(eval): 台账追加 v5 新尺子基线行（bridge-0.3.9 / v5，未启用逃生门）"
```

---

### Task 4: 验收记录 + 改写 D8 的 v6 入口条件（10.8 + C 的代价）

**Files:**
- Modify: `docs/tmp/deepeval-poc/RESULTS.md`（追加新节）
- Modify: `docs/openspec/changes/deepeval-bridge-eval/design.md`（D8「v6 接手点」段）
- Consumes: Task 2 / Task 3 的数值与台账行。

**Interfaces:**
- Produces: design D8 的新入口条件（供后续 change `deepeval-synthesizer-testset` 引用）；RESULTS 的 Phase 4 节（供 R11 处置时引用）。

- [ ] **Step 1: 追加 RESULTS 的 Phase 4 节**

在 `docs/tmp/deepeval-poc/RESULTS.md` **末尾**追加：

```markdown
## Phase 4：v5 新尺子基线（2026-10-17）

**口径**：`bridge-0.3.9 / v5`（DeepEval 桥 + ragas 0.3.9，四指标串行，行为轴三项）。
**运行**：1 → 5 → 21 题逐级，`--no-gate`；命令与耗时见同目录 `PHASE4-RUNS.md`。

**分布**：<四指标均值 + NaN 计数/率 + 行为轴三指标均值；从台账 #5 / 报告抄录>

**结论（本轮只记录、不标定）**：
1. 四指标与行为轴**均有值**，链路可用；
2. **阈值不标定**：R11 已证 `faithfulness` 在本判官下不可复现，本轮分布只作**基线锚点**，不作门禁阈值的推导依据；
3. **`answer_relevancy` 对残句答案敏感**（D3 语义：生成器看不到 context），v5 中此类题的低分是题集口径问题，不是检索问题；
4. 阈值标定与 NaN 率复核（tasks 10.4 / 10.5）**延后到 v6 定稿后**，届时以 `v6 + 新尺子` 的分布为准。
```

- [ ] **Step 2: 改写 design D8 的 v6 入口条件**

把 `docs/openspec/changes/deepeval-bridge-eval/design.md` 的「v6 接手点」段中：

```
  - **入口条件（三条，缺一不可）**：① 本 change 已归档且**新尺子的基线与阈值已标定**；② 台账已记入 `bridge-0.3.9 / v5` 一行；③ v6 生成后**与 v5 并存**（v5 不删，它是唯一的跨栈锚点）。
```

替换为：

```
  - **入口条件（三条，缺一不可）**：① 本 change 已归档，且 v5 的**新尺子分布**已记录（台账有 `bridge-0.3.9 / v5` 行）；**不要求阈值已标定**——标定推迟到 v6 之后（见下，解除「标定 ↔ v6」循环依赖）；② 台账已记入 `bridge-0.3.9 / v5` 一行；③ v6 生成后**与 v5 并存**（v5 不删，它是唯一的跨栈锚点）。
```

并把同段的验收形态第 1 步：

```
  - **验收形态（两步、不可颠倒）**：第 1 步 `v5 + DeepEval 尺子` → 新基线；第 2 步 `v6 + DeepEval 尺子` → 换卷子的对照。
```

替换为：

```
  - **验收形态（两步、不可颠倒）**：第 1 步 `v5 + DeepEval 尺子` → 新基线**与台账行**（任务已在 Phase 4 完成；本轮**不标定阈值**，见 R11）；第 2 步 `v6 + DeepEval 尺子` → 换卷子的对照，**并在此步之后标定阈值**（tasks 10.4 / 10.5 随之收口）。
```

- [ ] **Step 3: 校验文档与规格**

Run：

```bash
openspec validate deepeval-bridge-eval --strict
python -m src.cli.check_docs
```

Expected：`valid` / `0 error`。

- [ ] **Step 4: 提交**

```bash
git add docs/tmp/deepeval-poc/RESULTS.md docs/openspec/changes/deepeval-bridge-eval/design.md
git commit -m "docs(eval): Phase 4 验收记录 + 改写 D8 的 v6 入口条件（解除标定循环依赖）"
```

---

### Task 5: tasks.md 勾选与阶段收口

**Files:**
- Modify: `docs/openspec/changes/deepeval-bridge-eval/tasks.md`（组 10）
- Modify: `docs/superpowers/plans/2026-10-17-deepeval-bridge-eval-phase4.md`（本文件：执行记录段）

**Interfaces:**
- Consumes: Tasks 1–4 的全部产出。
- Produces: 组 10 的可完成项全勾、延后项注明的最终状态。

- [ ] **Step 1: 复跑静态闸门并记录**

Run：

```bash
POSTGRES_HOST=localhost pytest tests/ -q
ruff format --check src/ tests/
ruff check src/ tests/
.venv/bin/pyright src/
openspec validate deepeval-bridge-eval --strict
python -m src.cli.check_docs
python -m src.cli.check_adr
```

Expected：pytest 全绿；`ruff format` clean；`ruff check` / `pyright` 仅有既有债务（`engine.py:33` UP047 ×1；`eval_ragas_generate.py` ×2）；两个校验器 0 error。

- [ ] **Step 2: 核实 10.6（行为指标清单与实现一致）**

Run：

```bash
echo "=== 实现三项（顺序即评分顺序）==="
grep -n 'TaskCompletionMetric\|StepEfficiencyMetric\|AgentLoopDetectionMetric' src/cli/eval_scoring.py
echo "=== design D6 定稿清单 ==="
sed -n '/清单定稿（本组 tasks 5.7 定稿写回/,/两条已验证性质/p' docs/openspec/changes/deepeval-bridge-eval/design.md
echo "=== 行为轴不进主分/门禁 ==="
grep -n 'RAGAS_METRIC_COLUMNS' src/cli/eval_ragas.py | head -5
```

Expected：实现的三项与 D6 定稿清单**同名同序**（`TaskCompletion` → `StepEfficiency` → `AgentLoopDetection`）；`main_score_means` / `check_gate` 只读 `RAGAS_METRIC_COLUMNS`（不含行为指标名）。任一不符 ⇒ 停，先修再勾。

- [ ] **Step 3: 勾选 tasks.md**

- `10.1` → `[x]`，行尾补 `（Phase 4 复跑：pytest 全绿；ruff/pyright 仅 BASE 既有债务）`；
- `10.2` → `[x]`，补 `（v5 全量分布见 docs/tmp/deepeval-poc/PHASE4-RUNS.md）`；
- `10.3` → `[x]`，补 `（1 → 5 → 21 逐级通过）`；
- `10.4` → **保持 `[ ]`**，行尾补 `（**延后至 v6 定稿后**：R11 已证 faithfulness 不可复现，v5 上不标定；见 design D8 的 v6 入口条件）`；
- `10.5` → **保持 `[ ]`**，补 `（同上，延后至 v6 后）`；
- `10.6` → `[x]`（Phase 2 已定稿，本轮核实实现与 D6 一致）；
- `10.7` → `[x]`，补 `（台账 #5：bridge-0.3.9 / v5）`；
- `10.8` → `[x]`，补 `（验收记录见 docs/tmp/deepeval-poc/RESULTS.md「Phase 4」节）`。

- [ ] **Step 4: 写本计划的执行记录与交接**

在 `docs/superpowers/plans/2026-10-17-deepeval-bridge-eval-phase4.md` 末尾追加 `## Phase 4 执行记录与交接（2026-10-17）`，含：
- 三级活体的耗时与报告路径（引用 `PHASE4-RUNS.md`）；
- 四项均值、NaN 计数/率、行为轴三指标均值；
- 关键裁决（本轮不标定、D8 入口条件改写、`--limit` 仅冒烟）；
- **本阶段未证明**：阈值是否合理、NaN 10% 是否合适、行为轴分布是否稳定（均须 v6 后多轮）；
- 交接：下一步 = 立项 `deepeval-synthesizer-testset` 出 v6 → 标定 10.4/10.5 → R11 处置（D+双 judge / 降级）。

- [ ] **Step 5: 提交**

```bash
git add docs/openspec/changes/deepeval-bridge-eval/tasks.md \
        docs/superpowers/plans/2026-10-17-deepeval-bridge-eval-phase4.md
git commit -m "docs(superpowers): Phase 4 收口 —— 组 10 可完成项勾选 + 延后项注明 + 执行记录"
```

---

## Phase 4 完成判据

1. `--limit` 有单测（4 类边界）且不改变全量默认行为。
2. 1 / 5 / 21 三级运行全部退出码 0，报告与 `test_run_*.json` 落盘。
3. 全量报告含：`QA Pairs: 21`、四指标均值、`NaN cells` 行、`## Agent 行为轴` 段、`caliber=bridge-0.3.9`、`ragas=0.3.9`。
4. 行为分经 `testCases[].metricsData` 校验（非 `metricsScores`），21 条齐全。
5. 台账新增 #5 行（14 列对齐、口径 `bridge-0.3.9 / v5`、逃生门 `未启用`），历史行未动。
6. design D8 的 v6 入口条件已解除"标定 ↔ v6"循环；RESULTS 有 Phase 4 节。
7. tasks.md：10.1/10.2/10.3/10.6/10.7/10.8 勾选；10.4/10.5 未勾并注明延后。
8. 静态闸门：pytest 全绿；ruff/pyright 无**新增** error；`openspec validate --strict` / `check_docs` / `check_adr` 全过。

## 未纳入本阶段（后续 change）

- **v6 题集**（`deepeval-synthesizer-testset`）：DeepEval `Synthesizer` 出题、v5 并存、两步验收。
- **10.4 / 10.5**：阈值重标定与 NaN 率复核——**须 v6 定稿后**。
- **R11 处置**：`thinking=True` + 双 judge，或降级该指标——待 v6 的分布再拍板。
- **D13 完整请求级日志**（请求体/响应体摘要、成功路径日志）——需另立项。
- **需求池 D-14**：逃生门替代实现（首次启用时）。
