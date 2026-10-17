# deepeval-bridge-eval · Phase 3（出题路下线 / 逃生门 / Langfuse 口径 / 0.4.x 遗留）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Phase 1 换入口后留下的"外围"逐项收口 —— 出题生成入口下线并加硬护栏、逃生门预留可替换边界、Langfuse 回写纳入行为轴且带口径后缀、0.4.x 现代栈残留（工厂函数 / 测试 / ADR）显式处置，并诚实处置 Phase 2 的两条未勾选项。

**Architecture:** 不动已定型的桥评分链路（`build_metrics` + `score_rows` + 四指标串行）；本阶段只在**外围**加/删/改——① `main()` 的 `--generate` 分支改为硬护栏（显式失败 + 前迁路径），出题生成代码原样保留、测试集加载器继续被评测路径使用；② 把现有用例构造/评分点**显式标注**为可替换边界（不引入新抽象）；③ `write_scores` 通道扩到行为轴 + 统一加口径后缀；④ 删除 `build_ragas_judge` / `build_ragas_embeddings` / `build_native_client` 三个已无消费方的现代栈工厂，并追加新 ADR 取代 ADR-0024。

**Tech Stack:** Python 3.12 · DeepEval 4.2.9 · ragas 0.3.9 · langchain-openai · httpx 0.28.1 · pytest 9

**Spec:** `docs/openspec/changes/deepeval-bridge-eval/{proposal.md, design.md, specs/evaluation-pipeline/spec.md, tasks.md}`（**唯一权威**；本计划只做"怎么落地"）。本阶段覆盖 **tasks.md 组 6–9**，外加组 5 的两条未勾选项（5.1 / 5.6）与 design R9 的行为轴归因边界收敛。

## Global Constraints

以下为硬约束，**每个任务都隐含包含**（数值/事实逐字取自 spec / design / tasks，或本仓已核实）：

- **出题路**：`src/cli/eval_ragas_generate.py` 模块**保留不可删**（其测试集加载器 `_load_latest_testset` / `_ensure_vertexai_stub` 仍被评测主流程使用）；只有**出题生成入口**（`run_generate` / CLI `--generate`）下线。文档标注 **SHALL NOT** 写成"整个模块不再被调用"。
- **`--generate` 硬护栏**：该路径 SHALL **显式失败**并给出指引（原因 = 依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`；前迁路径 = DeepEval `Synthesizer`），SHALL NOT 让用户撞到裸 `ImportError`，SHALL NOT 写出测试集文件。
- **逃生门**：本轮**只预留可替换边界**，桥为默认实现；替代实现（DeepEval 采样 + 项目侧直调 ragas 0.3）SHALL **在首次启用时**才实现并冒烟，该前置 SHALL 登记在台账与需求池。逃生门 SHALL NOT 作为默认路径常驻；启用 SHALL 在台账记录**原因与生效轮次**。
- **Langfuse 回写**：SHALL 复用现有 `write_scores` 通道（客户端取 `langfuse_context.client_instance`，**SHALL NOT** `new Langfuse()`）；**行为轴指标 SHALL 一并回写**，与四项同级、共用同一 `trace_id`；`NaN` SHALL **不写**（SHALL NOT 以 `0` 代替）；新口径 score 名 SHALL 带**口径后缀**（如 `faithfulness@bridge-0.3.9`），SHALL NOT 与历史同名覆盖。
- **0.4.x 遗留**：三分——复用 / 删除 / 改写，判据 = **最终态无悬空引用**（不要求每个中间提交都干净）；删除动作 SHALL 与"调用点改写 + 受影响测试"**同一提交**。
- **ADR**：不可变（正文不改）；新 ADR SHALL 取代 `docs/adr/0024-ragas-judge-client-policy.md`，旧 ADR 的 `Status` SHALL 加**反向指针**，并更新 ADR 索引表；`python -m src.cli.check_adr` 须通过。
- **行为轴归因边界（design R9）**：行为轴判官调用**不**经 `http_async_client` 的 hooks 做逐题 `trace_id` 归因（DeepEval 在 `evals_iterator` **耗尽后**才评分，届时循环上下文已结束）；行为轴逐题失败证据 SHALL 由其 `test_run_*.json` 的 `testCases[].metricsData[].reason` 承载。
- **不改**：Agent 图与生产链路（`src/agents/**`）、`kb_eval` API 形状、前端；四指标评分路径的行为（`build_metrics` 四指标、`score_rows` 串行、NaN 判据重试、429 不重试）。
- **质量门禁**：`POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff format --check . && ruff check .` 无错；`pyright src/` 不新增 error；`openspec validate deepeval-bridge-eval --strict`、`python -m src.cli.check_docs`、`python -m src.cli.check_adr` 全过；无遗留 `print()`（CLI 的 stdout 契约除外）/TODO/调试代码。
- **本阶段不做**：全量活体、阈值重标定、NaN 率复核、台账记 `bridge-0.3.9 / v5` 行（属组 10，**须待新题集**，见 design R11）；题集重出（R11 的处置挂起）。

## 阶段划分

| 阶段 | 覆盖 tasks.md 组 | 状态 | 计划文档 |
|---|---|---|---|
| Phase 1（底座） | 组 1–4 | **已完成并合入 `dev-wsl`** | `2026-10-16-deepeval-bridge-eval-phase1.md` |
| Phase 2（Agent 行为轴） | 组 5 | **已完成并合入 `dev-wsl`**（`b99cbb0d`） | `2026-10-17-deepeval-bridge-eval-phase2.md` |
| **Phase 3（本文件）** | 组 6–9 + 组 5 遗留 | **已完成**（2026-10-17） | `2026-10-17-deepeval-bridge-eval-phase3.md` |
| Phase 4 | 组 10 | 待写（**须待新题集**，见 design R11） | — |

**范围裁决（已与用户对齐）**：① 严格取 tasks.md 组 6–9 + 组 5 遗留（5.1 补回归测试、5.6 因行为轴循环串行而无并发可验 ⇒ 诚实记明免做）+ design R9 归因边界；② **不**纳入 Phase 2 遗留 minor 清尾（单列后续）。

## File Structure

| 文件 | 责任 | 本阶段动作 |
|---|---|---|
| `src/cli/eval_ragas.py` | 评测 CLI 编排 | `--generate` 分支改硬护栏；`run_evaluation` docstring 标注可替换边界；`_write_langfuse_scores` 纳入行为轴 + 口径后缀；`_build_behaviour_section` 呈现失败 `reason` |
| `src/cli/eval_ragas_generate.py` | 出题生成（保留未调用） | **仅改文件头 docstring**（标注调用状态 + 前迁路径） |
| `src/cli/eval_scoring.py` | 评测纯函数 | 模块 docstring 标注评分后端边界；`read_behaviour_scores` 回读 `reason` |
| `src/config/const.py` | 常量 | 新增 `LANGFUSE_SCORE_SUFFIX` |
| `src/infra/llm/provider/factory.py` | 模型工厂 | **删** `build_ragas_judge` / `build_ragas_embeddings` / `build_native_client` 与随之失引用的 import |
| `src/core/log_event_specs.py` | 事件规格 | 重述 `judge call` / `metric retry` 的埋点口径（注释） |
| `src/cli/trace_logs.py` | 按 trace 拉日志 | 重述 docstring 的适用范围 |
| `docs/agents/ragas-eval-ledger.md` | 评测台账 | 增「逃生门」列；登记"出题生成保留未调用" |
| `docs/agents/requirements_pool.md` | 需求池 | 登记"逃生门替代实现待做" |
| `docs/adr/0026-*.md` | **新建** ADR | 取代 ADR-0024（裁判 client 政策） |
| `docs/adr/0024-ragas-judge-client-policy.md` | 旧 ADR | **仅改 Status** 加反向指针（正文不动） |
| `docs/adr/README.md` | ADR 索引 | 更新 0024 行 + 新增 0026 行 |
| `docs/openspec/changes/deepeval-bridge-eval/specs/evaluation-pipeline/spec.md` | 规格 delta | 收敛「Framework-internal LLM calls」对行为轴的措辞（R9 边界） |
| `docs/openspec/changes/deepeval-bridge-eval/{tasks.md, design.md}` | 规格文档 | 阶段末回写勾选与 R9 收口 |
| `tests/` | 测试 | 见各任务（新增 `test_generate_guard.py` / `test_scoring_boundary.py` / `test_langfuse_score_names.py` / `test_no_modern_factories.py` / `test_evals_iterator_loop.py`；删 `test_native_client.py`；扩 behaviour 相关三件） |

---

### Task 1: 出题路 CLI 硬护栏与标注（tasks 6.1–6.5）

**Files:**
- Modify: `src/cli/eval_ragas.py:930-937`（`main()` 的 generate 分支）
- Modify: `src/cli/eval_ragas_generate.py:1-12`（文件头 docstring）
- Modify: `docs/agents/ragas-eval-ledger.md`（新增「出题路状态」小节）
- Test: `tests/cli/test_generate_guard.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `main()` 在 `args.generate` 为真时打印指引并以退出码 1 结束；**不再** import / 调用 `src.cli.eval_ragas_generate.run_generate`。当前代码（`src/cli/eval_ragas.py:930-937`）：

  ```python
      # ---- generate 模式 ----
      if args.generate:
          from src.cli.eval_ragas_generate import run_generate

          size = args.size or settings.RAGAS_TEST_SIZE
          model = args.model or ""
          run_generate(kb_id, size, model)
          return
  ```

- [ ] **Step 1: 写失败测试（护栏方向 + 入口不再引用出题生成）**

新建 `tests/cli/test_generate_guard.py`：

```python
"""`--generate` 的硬护栏：显式失败 + 前迁指引，不得撞裸 ImportError（design D8）。"""

import inspect
import sys

import pytest

from src.cli import eval_ragas


def test_generate_path_fails_with_forward_path(monkeypatch, capsys):
    """--generate 必须以明确错误信息退出，给出原因与前迁路径（DeepEval Synthesizer）。"""
    monkeypatch.setattr(
        sys, "argv", ["eval_ragas", "--kb-id", "kb-x", "--generate"]
    )
    with pytest.raises(SystemExit) as excinfo:
        eval_ragas.main()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "0.4.x" in out
    assert "ragas==0.3.9" in out
    assert "Synthesizer" in out
    assert "ImportError" not in out


def test_evaluation_entry_no_longer_references_run_generate():
    """评测入口不得再引用出题生成入口 run_generate（tasks 6.1）。"""
    src = inspect.getsource(eval_ragas)
    assert "run_generate" not in src


def test_testset_loader_and_vertexai_stub_stay_referenced():
    """测试集加载器与 vertexai stub 仍被评测主流程使用，SHALL NOT 一并移除（tasks 6.1）。"""
    src = inspect.getsource(eval_ragas)
    assert "_load_latest_testset" in src
    assert "_ensure_vertexai_stub" in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_generate_guard.py -v`
Expected: `test_generate_path_fails_with_forward_path` **FAIL**（当前实现会 import 并调用 `run_generate`，不会 `SystemExit(1)` 也不打印指引）；`test_evaluation_entry_no_longer_references_run_generate` **FAIL**（源码含 `run_generate`）。

- [ ] **Step 3: 把 generate 分支改为硬护栏**

把 `src/cli/eval_ragas.py:930-937` 替换为：

```python
    # ---- generate 模式（已下线：硬护栏，design D8 / spec「The testset generation path
    #      is guarded against accidental invocation」）----
    # 出题链依赖 ragas 0.4.x 的 default_transforms_for_prechunked，而评测侧统一下移到
    # ragas==0.3.9 ⇒ 该路径在当前依赖线下必然失败。此处显式拦下并给出前迁路径，
    # SHALL NOT 让用户撞到裸 ImportError（design D8 硬护栏）。
    if args.generate:
        print(
            "error: --generate 已下线（出题生成入口不再经 CLI 提供）。\n"
            "原因：出题链依赖 ragas 0.4.x 的 default_transforms_for_prechunked，"
            "而评测侧已统一下移到 ragas==0.3.9，该路径在当前依赖线下必然失败。\n"
            "前迁路径：DeepEval 自带的出题器 Synthesizer"
            "（deepeval.synthesizer.Synthesizer.generate_goldens_from_docs(...) -> List[Golden]），"
            "其产出字段（input / expected_output / retrieval_context）与评测输入同构。\n"
            "若确需暂用旧出题路，须在临时环境装回 ragas==0.4.3。"
        )
        sys.exit(1)
```

> 注：`--size` / `--model` 的 argparse 定义保留（历史兼容，不在本次清理范围）；`settings` 的 `RAGAS_TEST_SIZE` 在本分支不再被读取。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_generate_guard.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 标注出题模块的文件头**

把 `src/cli/eval_ragas_generate.py:1-12` 的 docstring 替换为：

```python
"""RAGAS 测试集生成模块 — 从知识库文档自动生成 QA 测试集.

**调用状态（design D8 / spec「Testset generation exercises every query path」）**：
  - **出题生成入口**（`run_generate` / CLI `--generate`）**保留未调用** —— 评测入口已不再
    触发它（CLI `--generate` 显式失败，见 `eval_ragas.py` 的 `main()` 硬护栏）；
  - **测试集加载器**（`_load_latest_testset`）与 **VertexAI stub**（`_ensure_vertexai_stub`）
    **仍被评测主流程使用**（`eval_ragas.py` 的评估模式）。
  ⇒ 不得把本模块读成"整个模块不再被调用"。
  - 前迁路径：DeepEval 自带的出题器 `Synthesizer`
    （`generate_goldens_from_docs(...) -> List[Golden]`，字段与评测输入同构）。

本模块包含：
  - vertexai stub 自动修复（ragas 兼容性）
  - 从分块存储读取已有分块数据
  - 分步构建 KG + DiskCacheBackend 缓存（支持中断恢复）
  - TestsetGenerator 编排
  - 测试集版本管理与 JSON 写入
"""
```

- [ ] **Step 6: 台账登记出题路状态**

在 `docs/agents/ragas-eval-ledger.md` 的「已知口径与坑」小节**之前**插入一个小节：

```markdown
## 出题路状态（2026-10-17）

- **出题生成入口已下线**：CLI `--generate` 显式失败（依赖 ragas 0.4.x 的
  `default_transforms_for_prechunked`，而评测侧为 ragas 0.3.9）；出题**代码保留未调用**，
  前迁路径 = DeepEval `Synthesizer`（见 change `deepeval-bridge-eval` design D8）。
- **测试集加载器仍在用**：`_load_latest_testset` / `_ensure_vertexai_stub` 被评测主流程
  调用 ⇒ `src/cli/eval_ragas_generate.py` **不可删**。
- 评测输入 = **固定版本文件**（`testset_<kb>_v5.json`），与出题生成解耦。
```

- [ ] **Step 7: 提交**

```bash
git add src/cli/eval_ragas.py src/cli/eval_ragas_generate.py \
        docs/agents/ragas-eval-ledger.md tests/cli/test_generate_guard.py
git commit -m "feat(eval): --generate 硬护栏 + 出题路调用状态标注（tasks 组 6）"
```

（tasks 6.1–6.5 在本任务内一并完成：6.1 = Step 3 + 测试；6.2 = Step 5 + Step 6；6.3 = Step 3 + Step 1；6.4 = 现有 `--testset-version` 已支持固定版本，由 Step 3 的护栏不再触发生成即可保证；6.5 = Step 7。）

---

### Task 2: 逃生门可替换边界显式化 + 台账/需求池登记（tasks 7.1–7.3）

**Files:**
- Modify: `src/cli/eval_scoring.py:1-7`（模块 docstring 增「可替换评分边界」段）
- Modify: `src/cli/eval_ragas.py:576-579`（`run_evaluation` docstring）
- Modify: `docs/agents/ragas-eval-ledger.md`（「公共配置」表加「逃生门」列）
- Modify: `docs/agents/requirements_pool.md`（D 节登记"逃生门替代实现待做"）
- Test: `tests/cli/test_scoring_boundary.py`（新建）

**Interfaces:**
- Consumes: `build_metrics(judge, embeddings) -> dict[str, metric]`（键序 = `RAGAS_METRIC_COLUMNS`）；`score_rows(rows, metrics, *, evidence=None) -> list[dict]`（每行含各指标名 + `"error"`）
- Produces: 边界契约由测试钉住；文档明确"替代实现（DeepEval 采样 + 项目侧直调 ragas 0.3）在此接入"

- [ ] **Step 1: 写边界契约测试（失败）**

新建 `tests/cli/test_scoring_boundary.py`：

```python
"""逃生门可替换边界的契约测试（design D10）。

边界 = ① 用例构造 `_bridge_test_case`；② 评分 `build_metrics` + `score_rows`。
本轮只**预留边界**、不实现替代实现；本测试把边界的两个可替换点钉成契约。
"""

from src.cli import eval_ragas
from src.config.const import RAGAS_METRIC_COLUMNS


class _FakeMetric:
    """最小桥度量替身：`measure` 恒回 1.0（模拟桥的 float 出口）。"""

    def measure(self, test_case):
        return 1.0


def test_score_rows_output_shape_is_replaceable_contract():
    """评分侧边界：`score_rows` 每行含各指标名 + `"error"`，行序与输入一致。

    `metrics` 由调用方注入 ⇒ 替代实现只需提供同形入参，无需改这里的编排。
    """
    metrics = {name: _FakeMetric() for name in RAGAS_METRIC_COLUMNS}
    rows = [
        {
            "q_index": 0,
            "trace_id": "t0",
            "question": "q",
            "answer": "a",
            "ground_truth": "g",
            "contexts": ["c"],
        }
    ]

    scored = eval_ragas.score_rows(rows, metrics)
    assert len(scored) == 1
    assert scored[0]["q_index"] == 0
    assert "error" in scored[0]
    for name in RAGAS_METRIC_COLUMNS:
        assert name in scored[0]


def test_bridge_test_case_is_the_case_construction_boundary():
    """用例构造侧边界：`_bridge_test_case` 产出桥要的四字段 LLMTestCase。"""
    tc = eval_ragas._bridge_test_case(
        {
            "question": "Q",
            "answer": "A",
            "ground_truth": "G",
            "contexts": ["c1", "c2"],
        }
    )
    assert tc.input == "Q"
    assert tc.actual_output == "A"
    assert tc.expected_output == "G"
    assert list(tc.retrieval_context or []) == ["c1", "c2"]


def test_scoring_boundary_functions_are_module_level():
    """两个边界点须是可替换的模块级函数（替代实现据此接入）。"""
    assert callable(eval_ragas._bridge_test_case)
    assert callable(eval_ragas.score_rows)
    from src.cli.eval_scoring import build_metrics

    assert callable(build_metrics)
```

- [ ] **Step 2: 跑测试确认现状**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_scoring_boundary.py -v`
Expected: **PASS**（本测试钉的是**现有**解耦点，不是新行为）。若 FAIL，说明边界形态已漂移，先核实现再继续。

- [ ] **Step 3: 在 `eval_scoring.py` 模块 docstring 标注边界**

把 `src/cli/eval_scoring.py:1-7` 的 docstring 替换为：

```python
"""评测打分辅助：样本分类、主分聚合、跳过掩码、检索覆盖率、分数回写/回读。

与 eval_ragas.py 的分工：本模块放评测辅助逻辑与可单测的纯函数；
含外部 I/O 的有两个：`write_scores`（把每题分数回写 Langfuse）与
`read_behaviour_scores`（从 `test_run_*.json` 回读逐题行为分）；
CLI 编排（跑图、写报告文件）留在 eval_ragas.py。

**可替换评分边界（逃生门接入点，design D10）**：评测的「用例构造」与「评分」两步
各自落在具名函数上——用例构造 = `eval_ragas._bridge_test_case`；评分 = 本模块的
`build_metrics`（构造桥的四项指标）+ `eval_ragas.score_rows`（串行逐题打分）。
桥为**默认实现**；逃生门替代实现（DeepEval 采样 + 项目侧直调 ragas 0.3 线评分）
应在**这两个点**接入、保持同签名。本轮只预留边界、不实现替代实现。
"""
```

- [ ] **Step 4: 在 `run_evaluation` docstring 补边界说明**

把 `src/cli/eval_ragas.py:576-579` 的 `run_evaluation` docstring 首行改为：

```python
    """逐题打分并返回带 `.to_pandas()` 的结果（行序与输入一致）。

    本函数是评测**评分后端边界**（逃生门接入点，design D10）：`metrics` 由调用方注入，
    替代实现只需提供同签名的 `metrics`（及用例构造）即可替换，无需改这里的编排。
    """
```

- [ ] **Step 5: 台账加「逃生门」列**

把 `docs/agents/ragas-eval-ledger.md` 的运行台账表头由

```
| # | 开始（CST） | 报告文件 | 裁判配置 | 有效纯 KB | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness | 门禁 | 主要异常 | 口径版本 |
```

改为（**末尾新增「逃生门」列**）：

```
| # | 开始（CST） | 报告文件 | 裁判配置 | 有效纯 KB | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness | 门禁 | 主要异常 | 口径版本 | 逃生门 |
```

表头分隔行同步加一个 `---`；**历史 4 行**（#1–#4）在行尾各补一个 `未启用`（`answer_correctness` 列**保留**、历史值不动，见 design D9「历史行不改」）。

- [ ] **Step 6: 需求池登记替代实现待做**

在 `docs/agents/requirements_pool.md` 的「D. 代码质量与工程化」小节表格末尾追加一行：

```markdown
| D-14 | **评测逃生门的替代实现** | 桥不可用时启用：DeepEval 只做采样与用例构造、项目侧直调 ragas 0.3 线评分。**边界已预留**（`eval_ragas._bridge_test_case` / `eval_scoring.build_metrics` / `eval_ragas.score_rows`，见 `deepeval-bridge-eval` design D10）；**首次启用时**才实现并冒烟。触发条件：桥产出 NaN 比例 > 阈值且重试无效，或桥在目标环境无法稳定运行 | `deepeval-bridge-eval` D10 / spec「A bounded escape hatch」 | P2 | 中 | 桥不可用且需继续评测时 |
```

（该节现有编号止于 D-13，故新条目取 **D-14**。）

- [ ] **Step 7: 跑通并提交**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_scoring_boundary.py -v` → PASS
Run: `python -m src.cli.check_docs` → 0 error

```bash
git add src/cli/eval_scoring.py src/cli/eval_ragas.py \
        docs/agents/ragas-eval-ledger.md docs/agents/requirements_pool.md \
        tests/cli/test_scoring_boundary.py
git commit -m "docs(eval): 逃生门可替换边界显式化 + 台账逃生门列 + 需求池登记（tasks 组 7）"
```

---

### Task 3: 行为轴 Langfuse 回写与 score 口径（tasks 8.1–8.4）

**Files:**
- Modify: `src/config/const.py`（新增 `LANGFUSE_SCORE_SUFFIX`）
- Modify: `src/cli/eval_ragas.py:1170-1191`（`_write_langfuse_scores`）
- Modify: `src/cli/eval_ragas.py:1140`（调用处传 `behaviour_rows`）
- Test: `tests/cli/test_langfuse_score_names.py`（新建）

**Interfaces:**
- Consumes: `write_scores(trace_ids: list[str], scores: dict[str, list[float]], enabled: bool) -> None`（`src/cli/eval_scoring.py:198`）；`behaviour_rows: list[dict]`（`read_behaviour_scores` 返回，形如 `[{"q_index", "input", "metrics": {name: score}}, …]`）
- Produces: `_write_langfuse_scores(masked_df, trace_ids, behaviour_rows=None)` —— 所有 score 名带 `LANGFUSE_SCORE_SUFFIX` 后缀；行为轴指标并入 `scores`

- [ ] **Step 1: 写失败测试**

新建 `tests/cli/test_langfuse_score_names.py`：

```python
"""Langfuse 回写口径：新 score 名带后缀、行为轴一并回写、NaN 不写（design D9）。"""

import math

import pandas as pd

from src.cli import eval_ragas
from src.cli.eval_scoring import write_scores
from src.config.const import LANGFUSE_SCORE_SUFFIX


def _df_with_one_row():
    return pd.DataFrame(
        {
            "faithfulness": [0.9],
            "answer_relevancy": [0.8],
            "context_precision": [0.7],
            "context_recall": [0.6],
            "sample_class": ["pure_kb"],
        }
    )


def test_scores_carry_caliber_suffix_and_include_behaviour(monkeypatch):
    captured: dict = {}

    def _fake_write(trace_ids, scores, enabled):
        captured["trace_ids"] = trace_ids
        captured["scores"] = scores
        captured["enabled"] = enabled

    monkeypatch.setattr(eval_ragas, "write_scores", _fake_write)
    monkeypatch.setattr(eval_ragas.settings, "LANGFUSE_ENABLE", True)

    behaviour_rows = [
        {"q_index": 0, "input": "q", "metrics": {"Task Completion": 1.0}}
    ]
    eval_ragas._write_langfuse_scores(_df_with_one_row(), ["t0"], behaviour_rows)

    names = set(captured["scores"])
    assert f"faithfulness{LANGFUSE_SCORE_SUFFIX}" in names
    assert f"Task Completion{LANGFUSE_SCORE_SUFFIX}" in names
    # 裸名不出现（否则会覆盖历史同名 score）
    assert "faithfulness" not in names
    assert "Task Completion" not in names
    assert captured["trace_ids"] == ["t0"]


def test_behaviour_row_count_must_match_trace_ids(monkeypatch):
    """行为轴行数与 trace_ids 不齐即 fail-loud（防分数写到别的 trace）。"""
    monkeypatch.setattr(eval_ragas, "write_scores", lambda *a, **k: None)
    monkeypatch.setattr(eval_ragas.settings, "LANGFUSE_ENABLE", True)

    import pytest

    with pytest.raises(RuntimeError):
        eval_ragas._write_langfuse_scores(
            _df_with_one_row(), ["t0", "t1"], [{"q_index": 0, "metrics": {}}]
        )


def test_nan_is_not_written_even_under_suffixed_name(monkeypatch):
    """NaN 维持「缺失」语义：不写 score、也不写 0（后缀名不改变该语义）。"""
    written: list = []

    class _Client:
        def score(self, **kwargs):
            written.append(kwargs["name"])

    class _Ctx:
        client_instance = _Client()

    import sys
    import types

    fake = types.ModuleType("langfuse.decorators")
    fake.langfuse_context = _Ctx()
    monkeypatch.setitem(sys.modules, "langfuse.decorators", fake)

    write_scores(
        ["t0"],
        {f"faithfulness{LANGFUSE_SCORE_SUFFIX}": [math.nan]},
        enabled=True,
    )
    assert written == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_langfuse_score_names.py -v`
Expected: `test_scores_carry_caliber_suffix_and_include_behaviour` **FAIL**（`LANGFUSE_SCORE_SUFFIX` 未定义 ⇒ collection error；且 `_write_langfuse_scores` 不接 `behaviour_rows`）；`test_nan_is_not_written_even_under_suffixed_name` 亦因常量缺失而 error。

- [ ] **Step 3: 新增后缀常量**

在 `src/config/const.py` 的 `EVAL_NAN_RATE_MAX` 定义**之后**追加：

```python
# Langfuse score 名的口径后缀：新口径（DeepEval 桥 + ragas 0.3.9）与历史
# （modern-0.4.x）并存，SHALL NOT 同名覆盖（design D9）。四项与行为轴共用此后缀。
LANGFUSE_SCORE_SUFFIX: str = "@bridge-0.3.9"
```

- [ ] **Step 4: 改 `_write_langfuse_scores`**

把 `src/cli/eval_ragas.py:1170-1191` 替换为：

```python
def _write_langfuse_scores(
    masked_df: pd.DataFrame,
    trace_ids: list[str],
    behaviour_rows: list[dict] | None = None,
) -> None:
    """把已掩码表的逐题指标分数（+ 行为轴）按 trace_id 显式回写 Langfuse（观测旁路）。

    评分在批量 evaluate() 之后才产出、逐题 @observe 已关闭，故回写必须显式定位
    trace，不能依赖「当前 trace」的隐式上下文。值取已掩码表（空材料样本的
    context 指标为 NaN=跳过语义，write_scores 内跳过不写）；列取指标白名单，
    避免额外数值列混入。回写失败（客户端获取或逐条写）由 write_scores 吞掉，
    不影响评测主流程。

    score 名统一带口径后缀（`LANGFUSE_SCORE_SUFFIX`，design D9）：新口径与历史同名
    score 并存、SHALL NOT 覆盖；行为轴指标与四项同级、共用同一 trace_id；
    NaN 由 write_scores 跳过（SHALL NOT 写 0）。

    Args:
        masked_df: 经 apply_sample_masks 掩码、含 sample_class 列的结果表
        trace_ids: 每题的 trace_id（行序与 masked_df / behaviour_rows 对齐）
        behaviour_rows: 逐题行为分（见 read_behaviour_scores）；传入后一并回写。
            行数必须与 trace_ids 等长，否则快速失败（防分数写到别的 trace）
    """
    scores: dict[str, list] = {
        f"{col}{LANGFUSE_SCORE_SUFFIX}": list(masked_df[col])
        for col in RAGAS_METRIC_COLUMNS
        if col in masked_df.columns
    }
    if behaviour_rows is not None:
        if len(behaviour_rows) != len(trace_ids):
            raise RuntimeError(
                f"行为轴行数 ({len(behaviour_rows)}) != trace_ids ({len(trace_ids)})"
            )
        names: list[str] = []
        for row in behaviour_rows:
            for name in row.get("metrics") or {}:
                if name not in names:
                    names.append(name)
        for name in names:
            scores[f"{name}{LANGFUSE_SCORE_SUFFIX}"] = [
                (row.get("metrics") or {}).get(name) for row in behaviour_rows
            ]
    write_scores(trace_ids, scores, enabled=settings.LANGFUSE_ENABLE)
```

并在 `import` 区（`src/cli/eval_ragas.py:49-56`）的 `const` 导入中加入 `LANGFUSE_SCORE_SUFFIX`（`RAGAS_EVAL_CONCURRENCY` 一行**本轮先保留**，其删除归 Task 6 Step 5，避免同一文件两个 owner）：

```python
from src.config.const import (
    EVAL_PASS_THRESHOLD,
    EVAL_RETRIEVAL_COVERAGE_MIN,
    LANGFUSE_SCORE_SUFFIX,
    RAGAS_EVAL_CONCURRENCY,  # noqa: F401  # 删除归 Phase 3 Task 6（处置失义并发常量）
    RAGAS_METRIC_COLUMNS,
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
)
```

- [ ] **Step 5: 调用处传 `behaviour_rows`**

把 `src/cli/eval_ragas.py:1140` 的

```python
        _write_langfuse_scores(masked_df, trace_ids)
```

改为

```python
        _write_langfuse_scores(masked_df, trace_ids, behaviour_rows)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_langfuse_score_names.py tests/cli/test_eval_scoring.py -v`
Expected: PASS（新 3 条 + 既有 `write_scores` 测试全过）

- [ ] **Step 7: 全量回归并提交**

Run: `POSTGRES_HOST=localhost pytest tests/cli -q` → 全绿

```bash
git add src/config/const.py src/cli/eval_ragas.py tests/cli/test_langfuse_score_names.py
git commit -m "feat(eval): Langfuse 回写纳入行为轴并统一带口径后缀（tasks 组 8）"
```

---

### Task 4: 行为轴归因边界收敛（design R9）

**Files:**
- Modify: `docs/openspec/changes/deepeval-bridge-eval/specs/evaluation-pipeline/spec.md:576-592`（收敛「Framework-internal LLM calls」措辞）
- Modify: `src/cli/eval_scoring.py:270-303`（`read_behaviour_scores` 回读 `reason`）
- Modify: `src/cli/eval_ragas.py:696-732`（`_build_behaviour_section` 呈现失败 `reason`）
- Test: `tests/cli/test_behaviour_scores_read.py`、`tests/cli/test_behaviour_report.py`（扩展）

**Interfaces:**
- Consumes: `read_behaviour_scores(results_folder) -> list[dict]`，元素形如 `{"q_index", "input", "metrics": {name: score}}`
- Produces: 元素**新增** `"reasons": {name: reason}` 字段（`metrics` 形状不变，向后兼容）；`_build_behaviour_section` 在指标缺分（`N/A`）时以 `reason` 摘要替代

- [ ] **Step 1: 写失败测试（reason 回读）**

在 `tests/cli/test_behaviour_scores_read.py` 追加：

```python
def test_read_behaviour_scores_carries_reason(tmp_path):
    """逐题失败证据由 test_run JSON 的 metricsData[].reason 承载（design R9）。"""
    payload = {
        "testCases": [
            {
                "input": "Q1",
                "metricsData": [
                    {"name": "Task Completion", "score": 1.0, "success": True, "reason": "ok"},
                    {"name": "Step Efficiency", "score": None, "success": False, "reason": "no trace"},
                ],
            }
        ]
    }
    (tmp_path / "test_run_20260101.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    rows = read_behaviour_scores(str(tmp_path))
    assert rows[0]["metrics"] == {"Task Completion": 1.0, "Step Efficiency": None}
    assert rows[0]["reasons"]["Step Efficiency"] == "no trace"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_behaviour_scores_read.py::test_read_behaviour_scores_carries_reason -v`
Expected: **FAIL**（`KeyError: 'reasons'`）

- [ ] **Step 3: `read_behaviour_scores` 回读 reason**

把 `src/cli/eval_scoring.py:298-302` 的循环体改为：

```python
    for idx, tc in enumerate(raw.get("testCases") or []):
        metrics = {
            md.get("name"): md.get("score") for md in (tc.get("metricsData") or [])
        }
        reasons = {
            md.get("name"): md.get("reason") for md in (tc.get("metricsData") or [])
        }
        rows.append(
            {
                "q_index": idx,
                "input": tc.get("input", ""),
                "metrics": metrics,
                "reasons": reasons,
            }
        )
```

并把该函数 docstring 的 Returns 段更新为：

```
    Returns:
        按题序的 `[{"q_index", "input", "metrics": {name: score},
        "reasons": {name: reason}}, …]`。`reasons` 是行为轴的逐题失败证据承载点
        （design R9：行为轴判官调用不经 hooks 逐题归因，失败证据取此处）。
```

- [ ] **Step 4: 报告行为轴段呈现失败 reason**

在 `src/cli/eval_ragas.py:723-731` 的渲染循环中，把 `N/A` 分支改为附带 `reason` 摘要。替换该 `for` 循环为：

```python
    for i, row in enumerate(behaviour_rows):
        vals = [str(row.get("input") or f"Q{i + 1}")]
        reasons = row.get("reasons") or {}
        for name in metric_names:
            value = (row.get("metrics") or {}).get(name)
            if isinstance(value, (int, float)):
                vals.append(f"{value:.4f}")
            else:
                note = str(reasons.get(name) or "").strip().replace("|", "/")[:60]
                if note:
                    vals.append(f"N/A（{note}）")
                else:
                    vals.append("N/A")
        lines.append("| " + " | ".join(vals) + " |")
```

并在 `_build_behaviour_section` docstring 补一句：

```
    指标缺分时以 test_run JSON 的 `reason` 摘要标注（design R9：行为轴不走 hooks
    逐题归因，失败证据由 JSON 承载）。
```

- [ ] **Step 5: 扩展报告测试**

在 `tests/cli/test_behaviour_report.py` 追加：

```python
def test_behaviour_section_shows_reason_for_missing_metric():
    from src.cli.eval_ragas import _build_behaviour_section

    rows = [
        {
            "q_index": 0,
            "input": "Q1",
            "metrics": {"Task Completion": 1.0, "Step Efficiency": None},
            "reasons": {"Step Efficiency": "no trace"},
        }
    ]
    lines = _build_behaviour_section(rows)
    body = "\n".join(lines)
    assert "1.0000" in body
    assert "N/A（no trace）" in body
```

- [ ] **Step 6: 收敛 spec 措辞**

把 `docs/openspec/changes/deepeval-bridge-eval/specs/evaluation-pipeline/spec.md:576-578` 的段落

```
### Requirement: Framework-internal LLM calls are observable by trace id

评测 SHALL 使**框架内部的 LLM 调用**（ragas 桥内部的重试、`answer_relevancy` 的多次生成、DeepEval 行为指标的 judge 调用）在日志中可按该题的 `trace_id` 追溯。
```

改为

```
### Requirement: Framework-internal LLM calls are observable by trace id

评测 SHALL 使**框架内部的 LLM 调用**（ragas 桥内部的重试、`answer_relevancy` 的多次生成）在日志中可按该题的 `trace_id` 追溯。

**行为轴判官调用的归因边界（明写，避免误期待；design R9）**：DeepEval 行为指标在
`evals_iterator` **耗尽之后**才评分（异步路径），届时循环上下文已结束 ⇒ 其判官调用
**不**经 `http_async_client` 的 hooks 做逐题 `trace_id` 归因（该层 `trace_id` 记为空）；
行为轴的逐题失败证据 SHALL 由其 `test_run_*.json` 的 `testCases[].metricsData[].reason`
承载，并在报告「Agent 行为轴」段呈现。本条对行为轴**只**要求"证据可从 JSON 获得"，
SHALL NOT 读成"行为轴判官调用也须带逐题 trace_id"。
```

- [ ] **Step 7: 跑通并提交**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_behaviour_scores_read.py tests/cli/test_behaviour_report.py tests/cli/test_behaviour_fail_loud.py -v` → PASS
Run: `openspec validate deepeval-bridge-eval --strict` → PASS

```bash
git add src/cli/eval_scoring.py src/cli/eval_ragas.py \
        docs/openspec/changes/deepeval-bridge-eval/specs/evaluation-pipeline/spec.md \
        tests/cli/test_behaviour_scores_read.py tests/cli/test_behaviour_report.py
git commit -m "docs+feat(eval): 行为轴归因边界收敛 —— 失败证据走 test_run JSON reason（design R9）"
```

---

### Task 5: 删除现代栈工厂与遗留测试（tasks 9.3 + 9.6）

**Files:**
- Modify: `src/infra/llm/provider/factory.py`（删 3 个函数 + 1 个 import）
- Delete: `tests/infra/llm/provider/test_native_client.py`
- Test: `tests/infra/llm/provider/test_no_modern_factories.py`（新建）

**Interfaces:**
- Consumes: 现有消费方核实（已做）：`build_native_client` **仅**被 `build_ragas_judge`（`:252`）/ `build_ragas_embeddings`（`:277`）调用；`build_ragas_judge` / `build_ragas_embeddings` 无其他消费方
- Produces: `src.infra.llm.provider.factory` 不再暴露这三个名字

- [ ] **Step 1: 写失败测试（断言残留已删）**

新建 `tests/infra/llm/provider/test_no_modern_factories.py`：

```python
"""0.4.x 现代栈工厂已删除（tasks 9.3 / design D11）——防悬空引用回流。"""


def test_modern_ragas_factories_are_gone():
    import src.infra.llm.provider.factory as factory

    assert not hasattr(factory, "build_ragas_judge")
    assert not hasattr(factory, "build_ragas_embeddings")
    assert not hasattr(factory, "build_native_client")


def test_bridge_factories_remain():
    """桥工厂保留（评测链的裁判构造入口）。"""
    import src.infra.llm.provider.factory as factory

    assert hasattr(factory, "build_bridge_judge")
    assert hasattr(factory, "build_embeddings")
    assert hasattr(factory, "build_chat")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/provider/test_no_modern_factories.py -v`
Expected: `test_modern_ragas_factories_are_gone` **FAIL**（三个函数仍在）

- [ ] **Step 3: 删除三个工厂函数与失引用 import**

在 `src/infra/llm/provider/factory.py`：
1. 删 `from openai import AsyncOpenAI`（`:10`，删除后无其他使用——已核）。
2. 删 `build_native_client`（`:188-226`，含 docstring）。
3. 删 `build_ragas_judge`（`:229-263`）。
4. 删 `build_ragas_embeddings`（`:266-278`）。
5. 保留 `build_bridge_judge` / `build_embeddings` / `build_chat` / `build_rerank` / `_TimeoutReRankAdapter`。

> 若 `build_bridge_judge` docstring 提及 `build_ragas_judge()`（`:120`），**改写该句**为不引用已删函数（例：`与旧现代栈裁判（InstructorBaseRagasLLM，已删）的区别`），避免留悬空引用。

- [ ] **Step 4: 删除 `test_native_client.py`**

```bash
git rm tests/infra/llm/provider/test_native_client.py
```

> `tests/infra/llm/provider/test_registry_models.py` **保留**：它断言的是 `resolve_role("judge")` 的角色绑定，该绑定仍被 `build_bridge_judge` 使用（已核，非死测试）。`tests/cli/test_metric_retry.py` **保留**：`_exc_chain_summary` / `_score_metric_with_retry` 是**活代码**（见 Task 6 Step 1）。

- [ ] **Step 5: 跑通并提交**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/provider -v` → PASS
Run: `ruff format . && ruff check . --fix && pyright src/infra/llm/provider/factory.py` → 无新 error

```bash
git add src/infra/llm/provider/factory.py tests/infra/llm/provider/test_no_modern_factories.py
git rm tests/infra/llm/provider/test_native_client.py
git commit -m "refactor(provider): 删除现代 RAGAS 工厂（build_ragas_judge/embeddings/native_client）（tasks 9.3/9.6）"
```

---

### Task 6: 日志事件口径重述与残留核实（tasks 9.4 + 9.5）

**Files:**
- Modify: `src/core/log_event_specs.py:296-304`（`judge call` / `metric retry` 注释）
- Modify: `src/cli/trace_logs.py:1-13`（适用范围重述）
- Modify: `src/cli/eval_ragas.py:261` 附近（`generate_answers_and_contexts` 死代码标注）
- Modify: `src/config/const.py`（`RAGAS_EVAL_CONCURRENCY` 处置）
- Test: `tests/cli/test_trace_logs.py`（若既有）；否则无（本任务主体为文档/注释）

**Interfaces:**
- Consumes: 无
- Produces: 无签名变化

- [ ] **Step 1: 核实 9.4 的残留物（写结论，不改代码）**

已核实（全仓 grep，2026-10-17）：
- `_score_metrics` / `@experiment` —— **不存在**（Phase 1 已删），9.4 无对象；
- `_EvalResult`（`eval_ragas.py:427`）—— **仍被 `run_evaluation`（`:597`）使用**（保留 `.to_pandas()` 产出契约）⇒ **保留**；
- `_exc_chain_summary`（`:459`）—— **仍被 `_score_metric_with_retry`（`:507`）使用**（异常留证）⇒ **保留**；
- `_judge_failure_note` —— 不存在（Phase 1 删除）。

把该结论作为注释补进 `src/cli/eval_ragas.py` 的 `run_evaluation` 上方（保持"现状陈述"，不写变更史）：

```python
# `_EvalResult` 与 `_exc_chain_summary` 是当前桥路径的**活件**（非现代栈残留）：
# 前者承载产出层 `.to_pandas()` 契约，后者承载评分失败的异常链留证。
```

- [ ] **Step 2: 重述 `judge call` / `metric retry` 口径**

把 `src/core/log_event_specs.py:296-304` 改为（补注释说明埋点层）：

```python
    # [cli] 评测打分 —— 埋点层是「外层对桥度量 metric.measure 的调用」（我们这层，
    # 逐题串行）。桥内部真正的 HTTP 请求级证据（状态码 / 响应体摘要 / finish_reason）
    # 由裁判 http_async_client 的 event hooks 记录（provider 层，见 design D13），
    # 两者分工：本层记"我们第几次尝试、耗时、是否拿到分"；hooks 记"发出去/收回来的是什么"。
    "metric retry": EventSpec(
        "metric retry", "cli", "info", ("metric", "q_index", "trace_id", "reason")
    ),
    "judge call": EventSpec(
        "judge call",
        "cli",
        "info",
        ("metric", "q_index", "trace_id", "attempt", "seconds", "ok", "note"),
    ),
```

- [ ] **Step 3: 重述 `trace_logs.py` 适用范围**

把 `src/cli/trace_logs.py:1-13` 的 docstring 改为：

```python
"""trace_logs — 按 trace_id 拉取该次请求的完整日志（含裁判调用与框架内部请求级证据）。

用法：
    python -m src.cli.trace_logs eval_19128e15c446          # 报告里那列 trace_id
    python -m src.cli.trace_logs trace_eeed43a3 --grep "judge call"

为什么需要它：报告只给出每题的 trace_id；要定位"为什么这题失败"，需要把该 trace 下
**所有**日志串起来看。按本仓现有分工，判官相关日志有两类：
  - `judge call` / `metric retry` 事件：**外层**对桥度量 `measure` 的调用（尝试次数 /
    耗时 / 是否拿到分 / 异常链摘要 `note`），由 `src/cli/eval_ragas.py` 埋点；
  - `[provider] bridge judge http ...` 日志：**框架内部**（ragas 桥）真实 HTTP 请求级
    证据（状态码 / 响应体摘要 / `finish_reason`），由裁判 `http_async_client` 的
    event hooks 记录（design D13）。
> 说明：`note` 现为异常链摘要（`_exc_chain_summary`），**不含** `finish_reason` /
> 输出 token 数——那两样在 hooks 日志里（换桥后原 `_judge_failure_note` 已不适用）。

实现：段位无关（不依赖 `|` 分段），在 `logs/app_*.log` 与 `logs/error_*.log` 里按
trace_id **子串**过滤（与 `replay_trace` 同一套约定；trace 可跨天）。
"""
```

- [ ] **Step 4: 标注 `generate_answers_and_contexts` 为死代码**

在 `src/cli/eval_ragas.py:261` 的 `generate_answers_and_contexts` docstring 首行补一句：

```python
    """**生产死代码**：生产入口是 `generate_answers_and_contexts_with_behaviour`
    （行为轴驱动）；本函数仅被测试使用，保留以复用其"逐题 seed RequestContext +
    工具产出收集"的参考实现。新增生产调用请走 `_with_behaviour` 版本。
```

- [ ] **Step 5: 处置 `RAGAS_EVAL_CONCURRENCY`**

该常量（`const.py:546`）注释写"现代 RAGAS 评测的行级并发上限"，四指标改**串行**后失义（tasks 4.6 尾注归本阶段）。

1. 删除 `src/config/const.py:544-546` 的定义（含其注释块）。
2. 删除 `src/cli/eval_ragas.py:52` 的 `RAGAS_EVAL_CONCURRENCY,  # noqa: F401` 导入行。
3. 台账 `docs/agents/ragas-eval-ledger.md` 的「公共配置」里的并发项（原为 `- 评测并发：RAGAS_EVAL_CONCURRENCY=5（行级）`）**改为**：

```markdown
- 评测并发：四指标评分**串行（并发=1）**（桥复用 ragas 模块级指标实例，并发会互踩，见 design D7）
```

（若 Task 2 已改过该文件，注意顺序，避免同文件双改冲突。）

- [ ] **Step 6: 跑通并提交**

Run: `grep -rn "RAGAS_EVAL_CONCURRENCY" src/ tests/ docs/agents/` → **无输出**（已删净；`docs/adr/` 不可变、`docs/superpowers/plans/`、`docs/openspec/*/archive/`、`docs/research/` 为历史记录，不在判据内）
Run: `POSTGRES_HOST=localhost pytest tests/cli tests/config -q` → 全绿
Run: `python -m src.cli.check_docs` → 0 error

```bash
git add src/core/log_event_specs.py src/cli/trace_logs.py src/cli/eval_ragas.py src/config/const.py docs/agents/ragas-eval-ledger.md
git commit -m "docs(eval): 日志事件口径重述 + 残留核实 + 删失义并发常量（tasks 9.4/9.5 + 4.6 尾注）"
```

---

### Task 7: 新 ADR 取代 ADR-0024（tasks 9.7）

**Files:**
- Create: `docs/adr/0026-ragas-judge-client-policy-under-bridge.md`
- Modify: `docs/adr/0024-ragas-judge-client-policy.md:3`（仅 Status）
- Modify: `docs/adr/README.md:161`（0024 行 + 新增 0026 行）

**Interfaces:**
- Consumes: `python -m src.cli.check_adr`（校验：缺字段 / 指针不对称 / 索引漏登记 拦提交）
- Produces: ADR-0026（Accepted），ADR-0024 状态改为 `Superseded by 0026`

- [ ] **Step 1: 写新 ADR**

新建 `docs/adr/0026-ragas-judge-client-policy-under-bridge.md`：

```markdown
# ADR-0026：DeepEval 桥下的 RAGAS 裁判客户端策略（细粒度超时承载点换位 / 关 SDK 重试 / NaN 判据重试 / 两处桥不可控旋钮）

- **Status**：Accepted
- **Date**：2026-10-17
- **Deciders**：用户（议题提出与拍板）；Claude（换入口后的逐条复核、源码取证与实现）
- **Supersedes**：ADR-0024
- **关系**：落实 change `deepeval-bridge-eval` 的 design D3 / D4 / D9 / D11 / D13；观测事件字段口径以 `src/core/log_event_specs.py` 的 `EVENT_SPECS` 为唯一归属。

## 背景与问题

评测打分入口由 RAGAS 0.4.x 现代栈改为 **DeepEval 桥**（`deepeval/metrics/ragas.py`
驱动 ragas 0.3.9 的经典四指标）。ADR-0024 的六条裁判 client 决策建立在**现代栈**
（`llm_factory` + `InstructorBaseRagasLLM` + instructor 异常）之上，换入口后须**逐条复核**：
哪些仍成立、哪些承载点换位、哪些失效。

复核结论（逐条对 ADR-0024 的六条决策）：

| ADR-0024 决策 | 桥下状态 |
|---|---|
| 决策 1 细粒度 HTTP 超时 | **仍成立，承载点换位**：从 `build_native_client` 的 `AsyncOpenAI(timeout=…)` 换到 **`build_chat("judge", http_async_client=httpx.AsyncClient(timeout=…))`** |
| 决策 2 关 SDK 重试（`max_retries=0`） | **仍成立**，但**只关 SDK 重试**——ragas 框架层自带重试（见下「桥不可控旋钮②」） |
| 决策 3 本层有界重试 | **判据换位**：桥把失败吞成 `NaN` 不抛异常 ⇒ 判据从 `except Exception` 改为**非掩码 `NaN`** |
| 决策 4 `max_tokens=8192` | **仍成立**：经 `build_chat("judge", max_tokens=…)` 透传 |
| 决策 5 关思考 | **仍成立**：经 `build_chat("judge", thinking=False)`（`thinking_extra_body` 归一化） |
| 决策 6 失败必留原始响应 | **失效并改**：桥不给 instructor 异常、无 `last_completion` ⇒ 证据改由 `http_async_client` 的**请求级 event hooks** 承载 |

## 决策

### 决策 1：细粒度 HTTP 超时落在 `http_async_client`（非 `http_client`）

`build_chat("judge", http_async_client=httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=150, write=30, pool=10), event_hooks=…), max_retries=0)`。

**理由**：ragas 的裁判调用走**异步**（`evaluate` 提交 `metric.single_turn_ascore`），而
langchain 的 `http_client` **只喂同步 client**（`langchain_openai/chat_models/base.py:806`；
异步用 `:1273` 的 `http_async_client`）⇒ 只传同步字段会让超时与 hooks **双双失效**。
细粒度四维超时的理由同 ADR-0024 决策 1（httpx 无"整请求总预算"，平铺标量会四维同值、看不出卡在哪段）。

### 决策 2：`max_retries=0` 关 SDK 重试；但**不得声称"重试维度完整保住"**

`max_retries=0` 只关掉 **OpenAI SDK** 的重试；**ragas 框架层自带重试**——`ragas/llms/base.py:113`
用 `add_async_retry` 包裹每次生成，`ragas/run_config.py:52` 默认 `max_retries=10`，其重试
**只针对 `openai.RateLimitError`（HTTP 429）**。**桥不暴露 `RunConfig`** ⇒ 该项**登记为接受的偏离**。
防倍增效的口径：**外层 `RAGAS_METRIC_MAX_ATTEMPTS` SHALL NOT 重试限流类**（429 已由 ragas 重试 ≤10 次，外层再叠会 10×3 放大）；其余失败（超时 / 畸形响应 / 403 额度）仍由外层重试。

### 决策 3：本层有界重试的**判据是「非掩码 NaN」**，不是异常

桥调 `ragas.evaluate(...)` 未传 `raise_exceptions`（默认 `False`），`ragas/executor.py` 对
**任何** `Exception` 都 `return np.nan` ⇒ 桥不抛异常，原 `except Exception` 判据**永不触发**。
故判据改为**非掩码 `NaN`**（`RAGAS_METRIC_MAX_ATTEMPTS` 保留）。**不做**"合法缺值 / 裁判失败"
子分类——桥只回一个 `float`，HTTP 200 的 envelope 恒为合法 JSON，该分类不可实现。

### 决策 4 / 决策 5：`max_tokens=8192`、关思考 —— 保留

经 provider 层 `build_chat("judge", max_tokens=…, thinking=…)` 透传（`thinking` 经
`thinking_extra_body` 归一化，**SHALL NOT** 硬写 `enable_thinking`）。理由同 ADR-0024 决策 4/5。

### 决策 6：失败留证改由**请求级 hooks** 承载

换桥后无 instructor 异常、无 `last_completion` ⇒ 原 `_judge_failure_note` 不适用。
证据改由裁判 `http_async_client` 的 httpx event hooks 记录：**模型 / 耗时 / 状态码 /
请求体摘要 / 响应体摘要 + `finish_reason`**。**响应体必须记**——主因失败是 HTTP 200 +
畸形响应体，只记状态码看不到它。**hook SHALL 为 `async def` 且 `await response.aread()`**
（异步 client 是 `await hook(response)`，同步 hook 抛 `TypeError`）。**该日志只做留证**，
SHALL NOT 被当作 NaN 子分类依据。

## 两处「桥不可控的旋钮」（明写，避免后人误以为仍全可控）

1. **温度档由 ragas 按 `n` 决定**：`n=1` 的三指标为 0.01，`answer_relevancy`（`n=3`）为 0.3；
   `LangchainLLMWrapper` 会改写底层 `chat_model.temperature`，**`bypass_temperature` 默认 False
   且桥不暴露** ⇒ 拿不回温度 0。
2. **ragas 框架层 `RunConfig.max_retries=10`（限流类）不可配置**：见决策 2。

## 代价与复查触发条件

**代价**：① 温度不可控（评测侧接受，见 change design D3）；② ragas 框架层重试不可配置，
靠"外层不重试 429"避免乘法放大；③ 失败证据从"异常对象自带的原始响应"退化为"hooks 捕获的
HTTP 层响应体摘要"，粒度略降（但仍是唯一可得的一手证据）。

**复查触发条件**：① 桥升级 ragas 版本（0.3 → 0.4 动过 API）⇒ 复核本 ADR 全条；
② 换裁判模型（尤其换 provider 或换回思考档）⇒ 重跑超时/预算对照；
③ 若上游 ragas 暴露 `RunConfig` 或改为可配置重试 ⇒ 可移除"外层不重试 429"的补偿口径。
```

- [ ] **Step 2: 旧 ADR 加反向指针（仅改 Status）**

把 `docs/adr/0024-ragas-judge-client-policy.md:3` 的

```markdown
- **Status**：Accepted
```

改为

```markdown
- **Status**：Superseded by 0026
```

> ADR 正文**不动**（不可变纪律）。

- [ ] **Step 3: 更新 ADR 索引表**

在 `docs/adr/README.md`：
1. 把第 161 行（`| [0024](0024-…) | … | Accepted | … |`）的 **Status 列**改为 `Superseded by 0026`；
2. 在表末追加 0026 行：

```markdown
| [0026](0026-ragas-judge-client-policy-under-bridge.md) | DeepEval 桥下的裁判 client 政策：细粒度超时落 `http_async_client` / 关 SDK 重试 / NaN 判据重试 / 失败证据走请求级 hooks；记明两处桥不可控旋钮（温度分档 / ragas 框架层 max_retries=10） | Accepted | 完全取代 0024（换 DeepEval 桥后逐条复核） |
```

- [ ] **Step 4: 跑 ADR 校验**

Run: `python -m src.cli.check_adr` → PASS（指针对称 + 索引登记齐全）
Run: `POSTGRES_HOST=localhost pytest tests/cli/test_adr_consistency.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add docs/adr/0026-ragas-judge-client-policy-under-bridge.md \
        docs/adr/0024-ragas-judge-client-policy.md docs/adr/README.md
git commit -m "docs(adr): 新增 ADR-0026 取代 ADR-0024 的裁判 client 政策（tasks 9.7）"
```

---

### Task 8: 组 5 遗留处置 + 死引用自查（tasks 5.1 / 5.6 / 9.8）

**Files:**
- Test: `tests/cli/test_evals_iterator_loop.py`（新建）
- Modify: `docs/superpowers/plans/2026-10-17-deepeval-bridge-eval-phase3.md`（本文件执行记录段，Task 9 一并填）

**Interfaces:**
- Consumes: `deepeval.utils.get_or_create_event_loop()`（`deepeval/utils.py:209`，`loop.is_running()` 时调 `nest_asyncio.apply()`）
- Produces: 回归测试钉住"驱动 `evals_iterator` 不新建第二个事件循环"

- [ ] **Step 1: 写 5.1 回归测试**

新建 `tests/cli/test_evals_iterator_loop.py`：

```python
"""钉住 evals_iterator 在项目单个 asyncio.run 内重入同一事件循环（tasks 5.1 / design D6）。

机制：deepeval 的 `get_or_create_event_loop()` 在 `loop.is_running()` 时调
`nest_asyncio.apply()`，使内层 `run_until_complete` 在**同一个** loop 上重入、
不新建第二个 loop ⇒ 项目"单事件循环 + 引擎连接池"假设不破。

⚠️ `nest_asyncio.apply()` 是**进程级 monkeypatch**（替换 asyncio.run / Task / policy）；
仅因评测 CLI 是短命进程可接受（design D6）。
"""

import asyncio


def test_evals_iterator_reenters_running_loop_without_new_loop():
    """重入点：取到 loop 后再 `run_until_complete` 不得抛「already running」。

    若无 `nest_asyncio.apply()`，内层 `run_until_complete` 会抛
    `RuntimeError: This event loop is already running` ⇒ 本测试失败。
    """
    from deepeval.utils import get_or_create_event_loop

    seen: dict = {}

    async def _noop() -> None:
        return None

    async def _drive() -> None:
        loop = get_or_create_event_loop()
        seen["same_loop"] = loop is asyncio.get_running_loop()
        # 真正的重入点：未 apply nest_asyncio 时这里抛 "already running"
        loop.run_until_complete(_noop())

    asyncio.run(_drive())

    assert seen["same_loop"] is True, "须重入同一运行中的 loop，不得新建第二个事件循环"
```

- [ ] **Step 2: 跑测试**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_evals_iterator_loop.py -v`
Expected: PASS（Q1 探测已证该形态；若 FAIL，说明 deepeval 内部实现已变，须回到 design D6 复核）。

- [ ] **Step 3: 记录 5.6 的诚实处置（无代码）**

5.6「并发负向验证」**免做**，理由写入 tasks.md 勾选旁注（Task 9 Step 2）：
- 行为轴循环现为**串行**（每题独立 handler + 逐题 `ainvoke`，无并发）⇒ **无并发可验**；
- Phase 2 已有"每题独立 handler"的前置结构断言（`tests/cli/test_behaviour_driver.py`）；
- 将来若并行化行为轴循环，**须补**此负向验证（缺口已登记，不静默丢弃）。

- [ ] **Step 4: 死引用自查（9.8）**

Run:
```bash
grep -rn "ragas.metrics.collections\|llm_factory\|embedding_factory\|@experiment\|build_ragas_judge\|build_ragas_embeddings\|build_native_client\|RAGAS_EVAL_CONCURRENCY" src/ tests/ --include='*.py' | grep -v 'test_no_modern_factories.py'
```
Expected: **无输出**（`test_no_modern_factories.py` 的 `assert not hasattr(...)` 反向断言天然包含这些名字，排除；出题路的 `default_transforms_for_prechunked` 是 0.3.x 例外，已在 `eval_ragas_generate.py:294/307` 函数级导入处保留、由 Task 1 的文件头标注说明，**不计入本条违反**）。

- [ ] **Step 5: 跑通并提交**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_evals_iterator_loop.py tests/cli/test_behaviour_driver.py -v` → PASS

```bash
git add tests/cli/test_evals_iterator_loop.py
git commit -m "test(eval): 钉住 evals_iterator 单 loop 重入形态（tasks 5.1；5.6 免做已记明）"
```

---

### Task 9: 阶段收口（tasks 6.5/7.3/8.4/9.9 + 文档回写）

**Files:**
- Modify: `docs/openspec/changes/deepeval-bridge-eval/tasks.md`（勾选 6.x/7.x/8.x/9.x + 5.1/5.6 注）
- Modify: `docs/openspec/changes/deepeval-bridge-eval/design.md`（R9 收口，若 Task 4 未回写）
- Modify: `docs/superpowers/plans/2026-10-17-deepeval-bridge-eval-phase3.md`（本文件：执行记录与交接段）

**Interfaces:**
- Consumes: 前 8 个任务的全部产出
- Produces: tasks.md 组 6–9 全勾、组 5 的两条状态明确；本文件含执行记录

- [ ] **Step 1: 全量质量门禁**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/ -v
ruff format --check . && ruff check .
pyright src/
openspec validate deepeval-bridge-eval --strict
python -m src.cli.check_docs
python -m src.cli.check_adr
```
Expected: 全部通过（pytest 0 failed；ruff/pyright 无新 error；两个校验器 0 error）。

- [ ] **Step 2: 勾选 tasks.md**

- 组 6：`6.1`–`6.5` 全部 `[x]`；
- 组 7：`7.1`–`7.3` 全部 `[x]`；
- 组 8：`8.1`–`8.4` 全部 `[x]`；
- 组 9：`9.1`/`9.2`（Phase 1 已完成）与 `9.3`–`9.9` 全部 `[x]`；
- 组 5：`5.1` 勾选并在行尾补注"（回归测试已补：`tests/cli/test_evals_iterator_loop.py`）"；`5.6` **保持未勾**并在行尾补注免做理由（见 Task 8 Step 3）。

- [ ] **Step 3: design R9 收口（若 Task 4 未改）**

确认 `docs/openspec/changes/deepeval-bridge-eval/design.md` 的 R9 已含"行为轴逐题失败证据取 `test_run_*.json` 的 `testCases[].metricsData[].reason`"；若 Task 4 只改了 spec 未动 design，在此补一句指向 spec 的收敛措辞。

- [ ] **Step 4: 写执行记录与交接段**

在本计划文件末尾追加「Phase 3 执行记录与交接」段，包含：交付分支与基点、提交数、验收证据（pytest 计数 + 静态闸门结果）、关键裁决与偏离、**本阶段未证明**的事项（无全量活体 / 无阈值标定 ⇒ 未验证真实分布）、未勾选项的诚实保留（5.6）、遗留 minor（移交后续）。

- [ ] **Step 5: 提交**

```bash
git add docs/openspec/changes/deepeval-bridge-eval/tasks.md \
        docs/openspec/changes/deepeval-bridge-eval/design.md \
        docs/superpowers/plans/2026-10-17-deepeval-bridge-eval-phase3.md
git commit -m "docs(superpowers): Phase 3 收口 —— tasks 勾选 + 执行记录与交接（组 6–9 + 组 5 遗留）"
```

---

## Phase 3 完成判据

1. `--generate` 显式失败并给出原因 + 前迁路径（DeepEval `Synthesizer`）；评测入口不再引用 `run_generate`；测试集加载器与 vertexai stub **仍在用**。
2. 出题模块文件头标注"出题生成保留未调用、加载器仍在用"；台账有出题路状态条目。
3. 逃生门边界已显式标注（`_bridge_test_case` / `build_metrics` / `score_rows`）并有契约测试；台账有「逃生门」列；需求池有替代实现待做登记。
4. Langfuse 回写含行为轴、score 名带 `@bridge-0.3.9` 后缀、NaN 不写；行为轴行数与 trace_ids 不齐时 fail-loud。
5. 行为轴归因边界已收敛（spec 措辞 + `read_behaviour_scores` 带 `reason` + 报告呈现）。
6. `build_ragas_judge` / `build_ragas_embeddings` / `build_native_client` 已删且无悬空引用；`test_native_client.py` 已删。
7. 新 ADR-0026 取代 ADR-0024，旧 ADR Status 有反向指针，索引已更新，`check_adr` 通过。
8. `RAGAS_EVAL_CONCURRENCY` 已处置（删除或改串行语义）；`judge call` / `metric retry` / `trace_logs` 口径已重述。
9. 5.1 回归测试已补；5.6 免做理由已在 tasks.md 记明。
10. 全量 `pytest` / `ruff` / `pyright` / `openspec validate --strict` / `check_docs` / `check_adr` 全过。

## 未纳入本阶段（Phase 4 / 后续，勿提前实现）

- **组 10**（全量活体 1→5→21 题、阈值重标定、NaN 率复核、台账记 `bridge-0.3.9 / v5` 行）——**须待新题集**（design R11）。
- **题集重出（v6）**——独立 change（建议名 `deepeval-synthesizer-testset`），入口条件见 design D8「v6 接手点」。
- **R11 的处置**（faithfulness 不可复现：①D+双 judge / ②降级）——待新题集定稿后拍板。
- **Phase 2 遗留 minor 清尾**——本轮范围已明确排除，单列后续。
- **逃生门替代实现**（DeepEval 采样 + 项目侧直调 ragas 0.3）——首次启用时实现并冒烟。

## Phase 3 执行记录与交接（2026-10-17 完成）

**状态**：9/9 任务完成（Task 1–9）。分支 `feat/deepeval-bridge-eval`；基点 `b99cbb0d`；收口前 **18** 个提交（清单见下），含本收口提交共 **19** 个。

**提交清单（收口前 18 条：`git log --oneline b99cbb0d..7f414099^`）**：

```text
2f45c8e3 fix(test): Task 8 的 5.1 回归测试改为真重入守卫（run_until_complete）
bfacb811 docs(superpowers): Phase 3 计划 Task 8 的 5.1 回归测试改为真重入守卫
89a7d7af docs(superpowers): Phase 3 计划 Task 8 的死引用判据排除反向断言 guard
43674810 test(eval): 钉住 evals_iterator 单 loop 重入形态（tasks 5.1；5.6 免做已记明）
24959e88 docs(adr): 修正 ADR-0026 代价③与复查编号（响应体摘要未落地）
a4235037 docs(adr): 修正 ADR-0026 决策 6 的落地现状 + 旋钮①取证 + 索引单表
26efdc71 docs(adr): 新增 ADR-0026 取代 ADR-0024 的裁判 client 政策（tasks 9.7）
aa53521b docs(eval): 修正 Task 6 复核发现 —— provider 日志描述说真话 + 三处旧口径同步 + 台账分代
ee0c327a docs(superpowers): Phase 3 计划 Task 6 的 grep 判据收窄到当代文档
585c09ce docs(eval): 日志事件口径重述 + 残留核实 + 删失义并发常量（tasks 9.4/9.5 + 4.6 尾注）
3980bf7e docs(superpowers): Phase 3 计划 Task 4 修正沉淀（去三元 + 去测试内重复 import）
795a419d refactor(provider): 删除现代 RAGAS 工厂（build_ragas_judge/embeddings/native_client）（tasks 9.3/9.6）
d7e73472 fix(eval): Task 4 风格修正 —— 去三元表达式与测试内重复 import
baef1528 docs+feat(eval): 行为轴归因边界收敛 —— 失败证据走 test_run JSON reason（design R9）
501cf3c5 feat(eval): Langfuse 回写纳入行为轴并统一带口径后缀（tasks 组 8）
a7ad31ee docs(superpowers): 修正 Phase 3 计划 Task 2 的 pyright 隐患（retrieval_context Optional）
7714ff76 docs(eval): 逃生门可替换边界显式化 + 台账逃生门列 + 需求池登记（tasks 组 7）
0d0a107c feat(eval): --generate 硬护栏 + 出题路调用状态标注（tasks 组 6）
```

**闸门证据（2026-10-17 实测）**：

| 闸门 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `POSTGRES_HOST=localhost pytest tests/ -q` | **1880 passed, 3 skipped, 39 warnings in 80.63s** |
| 格式（改动域） | `ruff format --check src/ tests/` | `499 files already formatted`（exit 0） |
| Lint（改动域） | `ruff check src/ tests/` | exit 1，仅 **1** 条：`src/infra/db/engine.py:33:11` UP047（BASE 既有，见下节） |
| 类型（全量） | `.venv/bin/pyright src/` | `2 errors, 0 warnings`；均在 `eval_ragas_generate.py`（BASE 既有，见下节） |
| 规格校验 | `openspec validate deepeval-bridge-eval --strict` | `Change 'deepeval-bridge-eval' is valid`（exit 0） |
| 文档校验 | `python -m src.cli.check_docs` | `23 篇文档，0 error, 95 warn`（exit 0） |
| ADR 校验 | `python -m src.cli.check_adr` | `26 条 ADR，0 error`（exit 0） |

3 条 skip 均为既知条件：2 条 dashscope 未安装、1 条为 `ragas 0.3.9` 无 `default_transforms_for_prechunked`（出题路保留未调用）。
`pyright src/`（全量，已运行）：2 errors —— 均在 `src/cli/eval_ragas_generate.py`（`:290` `llm_context` / `:313` `default_transforms_for_prechunked`），属出题路保留的 ragas 0.4.x 引用，BASE `b99cbb0d` 即存在；Phase 3 无新增 error。

**BASE 既有闸门欠账（非本阶段引入，未修）**：

1. `ruff format --check .` → 报 `docs/agents/api_contract.md:469`（Markdown 代码围栏内被本版 ruff 当作 Python 排版），全仓 `112 files would be reformatted, 1165 files already formatted`（exit 1）。BASE `b99cbb0d` 同样报出；对全仓跑 format 会波及 112 篇文档，超出本阶段范围。
2. `ruff check .` → `src/infra/db/engine.py:33:11` UP047（`run_and_dispose` 未用 PEP 695 类型参数），`Found 1 error`（exit 1）。BASE 同样报出，单点存量。
3. `pyright src/` → `src/cli/eval_ragas_generate.py:290:46`（`llm_context` 无此参数）与 `:313:50`（`default_transforms_for_prechunked` 未知符号）。BASE 原行为 `284` / `307`，属出题路 0.4.x 保留引用的必然产物，随 R11 / v6 前迁一并处置。

**关键裁决（逐条，避免后人重开）**：

- **Task 1 接受 `SKIP=pyright` 提交**：上述两处 pyright error 在 BASE `b99cbb0d` 即存在，该任务 diff 只触及文件头 docstring，未引入新 error；pre-commit 只查改动文件正是为不让存量 error 阻塞所有提交。代价：清零须待出题路前迁（R11 / v6）。
- **计划文件曾被全仓 `ruff format` 破坏并重建（`3980bf7e`）**：Task 5 实施者的 `ruff format .` 波及 112 篇 docs，并误复原 Task 4 的 if/else 修正；已从 HEAD 恢复、重新应用并单独提交，内容已核对。此后禁止对全仓跑 `ruff format`（会改写 Markdown 代码围栏）；对 `docs/superpowers/plans/` 只允许本计划自身的文字修正，不得跑格式化工具。
- **死引用 grep 排除 `tests/infra/llm/provider/test_no_modern_factories.py`**：该 guard 的 `assert not hasattr(...)` 反向断言天然含被删符号名，排除后只查 `*.py`，避免把防线当残留。
- **`RAGAS_EVAL_CONCURRENCY` 判据限定 `src/ tests/ docs/agents/`**：ADR-0024（不可变）与 plans / archive / research 属历史记录，SHALL NOT 改；残留只允许出现在标注了口径版本的历史文档里。

**本阶段未证明（勿当成已验证）**：

- **无全量活体**（1 题 → 5 题 → 21 题）：四项指标与行为指标的真实分布、行为分是否稳定从 `test_run_*.json` 回读，均未在真实题集上跑过。
- **无阈值重标定**：`check_gate` 仍用换尺子前的阈值，新尺子的合理下界未知。
- **无 NaN 率复核**：4.7 的 `10%` 是经验初值，未用实测 NaN 率验证。
- 以上三项同属组 10，**须待新题集**（design R11），不构成本阶段（组 6–9）的欠账。

**诚实的遗留**：

- **5.6 未勾选（免做）**：行为轴循环现为串行（每题独立 handler + 逐题 `ainvoke`），无并发可验；Phase 2 已有"每题独立 handler"的结构断言。将来并行化该循环时**必须**补此负向验证（已在 tasks.md 原行写明）。
- **D13 落地现状**：D13 的落地现状（hooks 实记字段、失败才产日志、未落地项）见 `design.md` D13 的「落地现状（2026-10-17）」小节；此处不复制。
- **各任务 deferred minor（一行一条，均不阻塞交付）**：
  - Task 1：护栏位于 `kb_id` 必填校验之后（不带 `--kb-id` 会先撞必填）；`test_generate_guard` 用 `inspect.getsource` 文本包含断言；`--size` / `--model` 的 help 仍引用 `settings.RAGAS_TEST_SIZE`。
  - Task 2：契约测试单行输入 ⇒ "行序"断言实际不可验；`build_metrics` 仅以 `callable()` 钉住、键序未断言；台账「逃生门」列无取值图例。
  - Task 3：对齐测试单行、无法检出重排；NaN 测试直调 `write_scores`，未证明 `_write_langfuse_scores` 不把 NaN 替换为 0（靠阅读确认）；`scores: dict[str, list]` 注解偏松；行为行 ↔ `trace_ids` 仅长度校验（依赖 DeepEval JSON 顺序）；1 条 warning 未溯源。
  - Task 4：`reason` 内换行未消毒；N/A 回退分支（缺 `reasons` / `None` / 空串 / 截断）无测试；`eval_scoring.py` 的 `metricsData` 字段清单未含 `reason`；`metricsData` 双遍历；`test_behaviour_report.py` 函数内 import。**原"design R9 旧措辞"一项已在本阶段消除**。
  - Task 5：删除 `test_native_client` 后 `RAGAS_HTTP_TIMEOUT_*` 细粒度值在测试中零覆盖；`factory.py` docstring 措辞不准；warning 未溯源；回归闸门未覆盖 `build_rerank` / `_TimeoutReRankAdapter`（spec 标记 SHALL NOT 删）。
  - Task 6：三处文档「…`finish_reason`…仅失败时进日志」可被读成失败日志含 `finish_reason`；`logging-rules` 的 note「成功时为空串」与桥 NaN 失败路径不符；`troubleshooting` §10 案例与现机制不一致；`troubleshooting.md:133` 残留旧口径（**必交最终 review triage**）。
  - Task 7：无剩余（ADR-0026 决策 6 的字段清单与落地范围不符，已由 D13「落地现状」+ 复查触发条件 ④ 承接）。
  - Task 8：无（review 后 clean）。

**交接**：

- **下一步 = Phase 4（组 10）**，前置是 **v6 题集**（独立 change，建议名 `deepeval-synthesizer-testset`，入口条件见 design D8「v6 接手点」）。Phase 4 需完成：`--no-gate` 首轮全量分布 → 阈值重标定 → NaN 阈值复核 → 台账记 `bridge-0.3.9 / v6` 行 → 验收记录回写。
- **R11（faithfulness 不可复现）的处置仍待拍板**：①D+双 judge / ②降级，须在新题集定稿后决定；本阶段未触碰。
- Phase 2 遗留 minor 清尾与逃生门替代实现，按「未纳入本阶段」清单另行安排。
