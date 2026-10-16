# deepeval-bridge-eval · Phase 2（Agent 行为轴）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在上下文四指标之外，用 DeepEval 原生行为指标给 Agent 的**多轮行为**（任务是否完成 / 步骤是否高效 / 是否打转）打分，并把逐题分从 `test_run_*.json` 回读进报告 —— 让评测从"单轮问答口径"升级到"能测多轮 Agent"。

**Architecture:** 用一个自建的 `DeepEvalBaseLLM` 适配器把 **provider 层裁判**（`build_bridge_judge()`）接进 DeepEval 原生指标；评测的跑图循环改由 `EvaluationDataset.evals_iterator(metrics=[行为指标], display_config=DisplayConfig(results_folder=<OUT>))` **驱动**，循环体内 `await graph.ainvoke(..., config={"callbacks":[CallbackHandler()]})`；跑完从 `<OUT>/test_run_*.json` 的 `testCases[].metricsData[]` **逐题回读**。行为轴**不进主分/门禁**，只作独立观测。

**Tech Stack:** Python 3.12 · DeepEval 4.2.9 · langchain-openai · langgraph · httpx 0.28.1 · pytest 9

**Spec:** `docs/openspec/changes/deepeval-bridge-eval/{proposal.md, design.md, specs/evaluation-pipeline/spec.md, tasks.md}`（**唯一权威**；本计划只做"怎么落地"）。本阶段覆盖 **tasks.md 第 5 组**。

## Global Constraints

以下为硬约束，**每个任务都隐含包含**（数值/事实逐字取自 spec/design，或本阶段 spike 实测）：

- 行为指标取自 **DeepEval 原生族**；总数 **≤5**；本阶段定为 **3 项**：`TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`。
- **取分机制两半缺一不可**：`EvaluationDataset(goldens=[Golden(input=<题面>, expected_output=<参考答案>)])` 的 **`evals_iterator(metrics=[…], display_config=DisplayConfig(results_folder=<OUT>))`** 驱动；**逐题分 SHALL 取 `testCases[j].metricsData[k].score`**；**SHALL NOT** 读调用方持有的 `metric.score`；**SHALL NOT** 把 `metricsScores`（按指标聚合）当逐题分。
- `evals_iterator` 是 **generator**：**SHALL 跑到耗尽**（循环不得 `break`），否则 `test_run_*.json` 不落盘。
- **每题新建独立 `CallbackHandler` 实例**，SHALL NOT 跨题复用（handler 含实例态 ⇒ 复用会串 trace）。
- **⚠️ 行为轴 SHALL 走异步路径**（本阶段 spike 实测）：指标用默认 `async_mode=True`、iterator 用默认 `AsyncConfig`（`run_async=True`）。同步分支（`async_mode=False` / `run_async=False`）会经 `.invoke()` 落到 langchain **同步 client**，而桥裁判只挂 `http_async_client` ⇒ **hooks 零触发**（实测 `hook_events=0` vs 异步 `8`）。跑完 **SHALL 断言 `hook_events > 0`**。
- `evals_iterator` 嵌在项目**单个 `asyncio.run`** 内是可用的（Q1 探测：`nest_asyncio.apply()` 同一 loop 重入）；**SHALL NOT** 新建第二个事件循环。
- 行为指标的裁判 **SHALL** 经 provider 层构造（复用 `build_bridge_judge()`），**SHALL NOT** 直连 `OpenAIModel` 绕过 provider 层/hooks。
- 两条 **fail-loud**：① 轨迹非空 **且** 已取到分；② 生成器已耗尽（JSON 已落盘）。任一不成立 ⇒ 显式失败，**SHALL NOT** 让行为分静默为 0（`AgentLoopDetection` 无 trace 时源码直接置 `0.0`）。
- 行为轴 **SHALL NOT** 进主分、**SHALL NOT** 进门禁。
- `summarize_trajectory` **保留但只作报告诊断摘要**，SHALL NOT 作为行为指标数据源。
- 本阶段 **SHALL NOT** 触碰：`src/agents/**`、生产链路、`kb_eval` API 形状、前端；**SHALL NOT** 改变四指标路径的行为。

## 阶段划分

| 阶段 | 覆盖 tasks.md 组 | 状态 | 计划文档 |
|---|---|---|---|
| Phase 1（底座） | 组 1–4 | **已完成并合入 `dev-wsl`**（`de60d8d2`） | `2026-10-16-deepeval-bridge-eval-phase1.md` |
| **Phase 2（本文件）** | 组 5 | 进行中 | `2026-10-17-deepeval-bridge-eval-phase2.md` |
| Phase 3 | 组 6–9 | 待写 | — |
| Phase 4 | 组 10 | 待写（**须待新题集**，见 design R11） | — |

**本阶段的前置 spike（已完成，结论进约束）**：`docs/tmp/deepeval-poc/RESULTS.md`「Phase 2 前置 spike」节。关键结论三条：① 适配器只需实现 `generate`/`a_generate` 返回**内容文本**；② **必须走异步**否则 hooks 零触发；③ `TaskCompletionMetric(task=None)` 可**逐题**取任务。

## File Structure

| 文件 | 责任 | 本阶段动作 |
|---|---|---|
| `src/infra/llm/provider/deepeval_llm.py` | **新建** | `DeepEvalBaseLLM` 适配器：把 provider 层裁判接进 DeepEval 原生指标 |
| `src/cli/eval_scoring.py` | 评测纯函数 | **新增** `build_behaviour_metrics` / `read_behaviour_scores` |
| `src/cli/eval_ragas.py` | CLI 编排 | `_answer_one_question` 加 `callbacks`；**新增** 行为轴驱动循环；`main()` 接线；报告加行为轴段 |
| `src/config/const.py` | 常量 | 新增行为轴结果目录/文件名前缀常量 |
| `docs/openspec/changes/deepeval-bridge-eval/{tasks.md, design.md}` | 规格文档 | 阶段末回写勾选 + **D6 指标清单定稿** |

---

### Task 1: 行为轴判官适配器（`BridgeDeepEvalLLM`）

**Files:**
- Create: `src/infra/llm/provider/deepeval_llm.py`
- Test: `tests/infra/llm/provider/test_deepeval_llm.py`

**Interfaces:**
- Consumes: 任意 langchain `BaseChatModel`（本项目用 `factory.build_bridge_judge()` 的产物）
- Produces: `class BridgeDeepEvalLLM(DeepEvalBaseLLM)`，构造签名 `BridgeDeepEvalLLM(chat: Any)`；方法 `load_model() -> Any`、`get_model_name() -> str`、`generate(prompt: str, **kwargs) -> str`、`async a_generate(prompt: str, **kwargs) -> str`

- [ ] **Step 1: 写失败测试**

```python
# tests/infra/llm/provider/test_deepeval_llm.py
"""适配器：把 provider 层裁判接进 DeepEval 原生指标。

为何需要：`initialize_model` 只接受 `str | DeepEvalBaseLLM`
（deepeval `metrics/utils/models.py:170`），langchain 模型不被接受；
且非 native 模型会退回 `generate(prompt)`、由 DeepEval 自己解析 JSON
（`models/base_model.py:151`）⇒ 本类只需返回内容文本。
"""

from __future__ import annotations

import pytest
from deepeval.models import DeepEvalBaseLLM
from langchain_core.messages import AIMessage

from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM


class _FakeChat:
    """最小化身：记录调用并回固定 JSON 文本。

    返回**真实** `AIMessage`（langchain 实际返回的类型）：使 `_content` 走
    `isinstance(msg, BaseMessage)` 分支。用自定义假消息类会落到 `str(msg)` 分支、
    拿不到内容（这正是本计划首版的自相矛盾处）。
    """

    model_name = "fake-judge"

    def __init__(self) -> None:
        self.invoked: list[str] = []
        self.ainvoked: list[str] = []

    def invoke(self, prompt: str) -> AIMessage:
        self.invoked.append(prompt)
        return AIMessage(content='{"verdict": "yes", "reason": "ok"}')

    async def ainvoke(self, prompt: str) -> AIMessage:
        self.ainvoked.append(prompt)
        return AIMessage(content='{"verdict": "yes", "reason": "ok"}')


def test_adapter_is_a_deepeval_llm_and_reports_name():
    chat = _FakeChat()
    llm = BridgeDeepEvalLLM(chat)

    assert isinstance(llm, DeepEvalBaseLLM)
    assert llm.get_model_name() == "fake-judge"
    # `super().__init__()` 生效的证据：基类据 load_model() 置 self.model
    # （deepeval `models/base_model.py:75`）
    assert llm.load_model() is chat
    assert llm.model is chat


def test_generate_returns_content_text():
    chat = _FakeChat()
    llm = BridgeDeepEvalLLM(chat)

    out = llm.generate("prompt-A")

    assert out == '{"verdict": "yes", "reason": "ok"}'
    assert chat.invoked == ["prompt-A"]


@pytest.mark.asyncio
async def test_a_generate_returns_content_text():
    chat = _FakeChat()
    llm = BridgeDeepEvalLLM(chat)

    out = await llm.a_generate("prompt-B")

    assert out == '{"verdict": "yes", "reason": "ok"}'
    assert chat.ainvoked == ["prompt-B"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost python -m pytest tests/infra/llm/provider/test_deepeval_llm.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.infra.llm.provider.deepeval_llm'`

- [ ] **Step 3: 写最小实现**

```python
# src/infra/llm/provider/deepeval_llm.py
"""DeepEval 原生指标的 LLM 适配器：复用 provider 层裁判。

为什么需要它：`deepeval.metrics.utils.models.initialize_model` 只接受
`str | DeepEvalBaseLLM`（`metrics/utils/models.py:170`）——**langchain 模型不被接受**；
若改用 DeepEval 自带的 `OpenAIModel`，则绕过 provider 层、并失去
`http_async_client` 上的细粒度超时与请求级 hooks。
非 native 模型路径下 DeepEval 会退回 `generate(prompt)` 并自行解析 JSON
（`models/base_model.py:151`）⇒ 本类只返回**内容文本**即可。
"""

from __future__ import annotations

from typing import Any

from deepeval.models import DeepEvalBaseLLM


class BridgeDeepEvalLLM(DeepEvalBaseLLM):
    """把 langchain `BaseChatModel`（provider 层裁判）适配成 DeepEval LLM。

    `super().__init__()` 必须调用：基类会执行 `self.model = self.load_model()`
    并设置 `self.name`；不调用则任何读 `.name` / `.model` 的 DeepEval 代码会
    `AttributeError`（评审 Important）。
    """

    def __init__(self, chat: Any) -> None:
        self._chat = chat
        super().__init__()  # 基类据 load_model() 置 self.model / self.name

    def load_model(self) -> Any:
        return self._chat

    def get_model_name(self) -> str:
        # 显式类型检查，而非 `getattr(x, "attr", default)` 隐式兜底
        # （CLAUDE.md：类型不确定的值不做隐式兜底）——评审 Important
        name = self._chat.model_name
        return name if isinstance(name, str) else "bridge-judge"

    @staticmethod
    def _content(msg: Any) -> str:
        return str(msg.content) if isinstance(msg, BaseMessage) else str(msg)

    def generate(self, prompt: str, **kwargs: Any) -> str:
        return self._content(self._chat.invoke(prompt))

    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        return self._content(await self._chat.ainvoke(prompt))
```

> 顶部需 `from langchain_core.messages import BaseMessage`。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost python -m pytest tests/infra/llm/provider/test_deepeval_llm.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/infra/llm/provider/deepeval_llm.py tests/infra/llm/provider/test_deepeval_llm.py
git commit -m "feat(eval): 行为轴判官适配器（provider 层裁判 → DeepEvalBaseLLM）"
```

---

### Task 2: 行为指标构造（`build_behaviour_metrics`）

**Files:**
- Modify: `src/cli/eval_scoring.py`
- Test: `tests/cli/test_behaviour_metrics.py`

**Interfaces:**
- Consumes: T1 的 `BridgeDeepEvalLLM` 实例
- Produces: `def build_behaviour_metrics(judge: BridgeDeepEvalLLM) -> list`  —— 返回**顺序固定**的 3 项：`TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_behaviour_metrics.py
from __future__ import annotations

from deepeval.models import DeepEvalBaseLLM

from src.cli.eval_scoring import build_behaviour_metrics


class _FakeDeepEvalLLM(DeepEvalBaseLLM):
    """最小可接受的 DeepEval LLM；本测试不触发真实调用。

    必须是 `DeepEvalBaseLLM` 子类：`initialize_model` 只接受 `str | DeepEvalBaseLLM`，
    其它对象会被当成**模型名**回落到 `OpenAIModel(...)`（实测直接因缺 key 报错）。
    """

    def load_model(self):  # noqa: ANN201
        return self

    def get_model_name(self) -> str:
        return "fake"

    def generate(self, prompt: str, **kwargs) -> str:  # noqa: ANN003
        return "{}"

    async def a_generate(self, prompt: str, **kwargs) -> str:  # noqa: ANN003
        return "{}"


def test_build_behaviour_metrics_returns_three_in_fixed_order():
    metrics = build_behaviour_metrics(_FakeDeepEvalLLM())

    assert [m.__class__.__name__ for m in metrics] == [
        "TaskCompletionMetric",
        "StepEfficiencyMetric",
        "AgentLoopDetectionMetric",
    ]


def test_llm_backed_metrics_receive_the_judge_and_loop_detection_does_not():
    """LLM 驱动的**两项**都必须拿到**同一个**裁判实例；loop-detection 是纯 trace、不接裁判。

    ⚠️ 该类**确实有** `model` 属性（基类声明并置 None），故**不能**断言 `hasattr` 为假。
    断言用 `is judge`（身份）而非 `is not None`：后者在"工厂硬编码了别的模型"时也会通过。
    """
    judge = _FakeDeepEvalLLM()
    metrics = build_behaviour_metrics(judge)

    assert metrics[0].model is judge  # TaskCompletion
    assert metrics[1].model is judge  # StepEfficiency（评审 Important：原测试漏了这项）
    assert metrics[2].model is None  # AgentLoopDetection（纯 trace，不调 LLM）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_metrics.py -v`
Expected: FAIL —— `ImportError: cannot import name 'build_behaviour_metrics'`

- [ ] **Step 3: 写最小实现**（追加到 `src/cli/eval_scoring.py` 末尾）

```python
def build_behaviour_metrics(judge):
    """构造 DeepEval 原生行为指标（design D6；本阶段定稿为 3 项）。

    顺序即评分与报告顺序。三点实测依据（docs/tmp/deepeval-poc/RESULTS.md）：
    - `TaskCompletionMetric(task=None)`：**逐题**从 test case 取任务
      （deepeval `metrics/task_completion/task_completion.py:121`），故不必每题新建实例；
    - `StepEfficiencyMetric`：按轨迹算步骤效率，需裁判；
    - `AgentLoopDetectionMetric`：**纯 trace 分析、不调 LLM**（构造器无 model 参数）。

    `threshold=0.5` 只影响 JSON 里的 `success` 字段；行为轴**不进主分/门禁**。
    """
    from deepeval.metrics import (
        AgentLoopDetectionMetric,
        StepEfficiencyMetric,
        TaskCompletionMetric,
    )

    return [
        TaskCompletionMetric(model=judge, threshold=0.5),
        StepEfficiencyMetric(model=judge, threshold=0.5),
        AgentLoopDetectionMetric(threshold=0.5),
    ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_metrics.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py tests/cli/test_behaviour_metrics.py
git commit -m "feat(eval): 行为指标构造（三项定稿：TaskCompletion/StepEfficiency/AgentLoopDetection）"
```

---

### Task 3: 逐题行为分回读（`read_behaviour_scores`）

**Files:**
- Modify: `src/cli/eval_scoring.py`
- Test: `tests/cli/test_behaviour_scores_read.py`

**Interfaces:**
- Produces: `def read_behaviour_scores(results_folder: str) -> list[dict]`
  - 返回**按题序**的列表，每项形如 `{"q_index": int, "input": str, "metrics": {name: float}}`
  - 找不到 `test_run_*.json` ⇒ **raise `RuntimeError`**（fail-loud，见 T6 的第②条）

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_behaviour_scores_read.py
from __future__ import annotations

import json

import pytest

from src.cli.eval_scoring import read_behaviour_scores

_FIXTURE = {
    "testCases": [
        {
            "input": "Q0",
            "metricsData": [
                {"name": "Task Completion", "score": 1.0, "success": True},
                {"name": "Step Efficiency", "score": 0.5, "success": True},
            ],
        },
        {
            "input": "Q1",
            "metricsData": [
                {"name": "Task Completion", "score": 0.0, "success": False},
            ],
        },
    ]
}


def test_reads_per_question_scores_in_order(tmp_path):
    (tmp_path / "test_run_20261017_000000.json").write_text(
        json.dumps(_FIXTURE), encoding="utf-8"
    )

    rows = read_behaviour_scores(str(tmp_path))

    assert [r["q_index"] for r in rows] == [0, 1]
    assert [r["input"] for r in rows] == ["Q0", "Q1"]
    assert rows[0]["metrics"]["Task Completion"] == 1.0
    assert rows[1]["metrics"]["Task Completion"] == 0.0


def test_picks_the_newest_test_run_file(tmp_path):
    (tmp_path / "test_run_20260101_000000.json").write_text(
        json.dumps({"testCases": [{"input": "OLD", "metricsData": []}]}), encoding="utf-8"
    )
    (tmp_path / "test_run_20261017_000000.json").write_text(
        json.dumps(_FIXTURE), encoding="utf-8"
    )

    rows = read_behaviour_scores(str(tmp_path))

    assert rows[0]["input"] == "Q0"


def test_missing_json_fails_loud(tmp_path):
    with pytest.raises(RuntimeError, match="test_run"):
        read_behaviour_scores(str(tmp_path))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_scores_read.py -v`
Expected: FAIL —— `ImportError: cannot import name 'read_behaviour_scores'`

- [ ] **Step 3: 写最小实现**（追加到 `src/cli/eval_scoring.py` 末尾）

```python
def read_behaviour_scores(results_folder: str) -> list[dict]:
    """从 `<results_folder>/test_run_*.json` 回读**逐题**行为分。

    为什么必须回读文件：DeepEval 的 eval loop 用 `copy_metrics()` 的副本打分
    ⇒ 调用方持有的 `metric.score` 恒为 `None`（见 design D6）。
    逐题分在 `testCases[j].metricsData[k]`（含 `name`/`score`/`success`）；
    `metricsScores` 是**按指标聚合**，不作逐题分。

    Args:
        results_folder: `DisplayConfig(results_folder=…)` 指定的目录。

    Returns:
        按题序的 `[{"q_index", "input", "metrics": {name: score}}, …]`。

    Raises:
        RuntimeError: 目录下没有 `test_run_*.json`（生成器未耗尽 ⇒ 行为分不可信）。
    """
    import glob
    import json

    files = sorted(glob.glob(str(Path(results_folder) / "test_run_*.json")))
    if not files:
        raise RuntimeError(
            f"{results_folder} 下没有 test_run_*.json："
            "evals_iterator 未耗尽（或循环提前结束），行为分不可信"
        )
    raw = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    rows: list[dict] = []
    for idx, tc in enumerate(raw.get("testCases") or []):
        metrics = {
            md.get("name"): md.get("score") for md in (tc.get("metricsData") or [])
        }
        rows.append({"q_index": idx, "input": tc.get("input", ""), "metrics": metrics})
    return rows
```

> 顶部若未 import `Path`，请在 `src/cli/eval_scoring.py` 现有 import 段加入 `from pathlib import Path`。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_scores_read.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py tests/cli/test_behaviour_scores_read.py
git commit -m "feat(eval): 行为分逐题回读（test_run_*.json → testCases[].metricsData）"
```

---

### Task 4: 跑图循环改由 `evals_iterator` 驱动

**Files:**
- Modify: `src/cli/eval_ragas.py`（`_answer_one_question` 加 `callbacks`；新增 `generate_answers_and_contexts_with_behaviour`）
- Test: `tests/cli/test_behaviour_driver.py`

**Interfaces:**
- Consumes: T2 的行为指标列表
- Produces:
  - `_answer_one_question(graph, kb_id, session_id, query, trace_id, *, callbacks=None)` —— 新关键字参数，透传给 `graph.ainvoke(config={"callbacks": callbacks})`
  - `async def generate_answers_and_contexts_with_behaviour(graph, kb_id, session_id, questions, ground_truth, behaviour_metrics, results_folder) -> tuple` —— 返回与 `generate_answers_and_contexts` **同形**的 6 元组，外加 `list[CallbackHandler]`（每题一个，供断言与诊断）

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_behaviour_driver.py
"""驱动形态：每题一个 CallbackHandler，且 handler 被传进 graph.ainvoke。"""

from __future__ import annotations

import pytest

from src.cli import eval_ragas


class _FakeGraph:
    def __init__(self) -> None:
        self.configs: list[dict] = []

    async def ainvoke(self, state, config=None):  # noqa: ANN001, ANN201
        self.configs.append(config or {})
        return {"answer": "A", "tool_contexts": [], "messages": []}


@pytest.mark.asyncio
async def test_each_question_gets_its_own_handler(monkeypatch):
    graph = _FakeGraph()

    class _H:
        pass

    # CallbackHandler 在函数体内 import ⇒ 必须打**源模块**的属性，打 eval_ragas 的
    # 模块属性会 AttributeError（那里没有这个名字）。
    monkeypatch.setattr("deepeval.integrations.langchain.CallbackHandler", _H)

    # 假 dataset **必须实现 `evals_iterator`**（实现驱动的是它，不是 `__iter__`）
    class _FakeDataset:
        def evals_iterator(self, *, metrics, display_config):  # noqa: ANN001, ANN003
            from deepeval.dataset import Golden

            for q in ("Q0", "Q1"):
                yield Golden(input=q)

    monkeypatch.setattr(
        eval_ragas, "_build_behaviour_dataset", lambda *a, **k: _FakeDataset()
    )

    await eval_ragas.generate_answers_and_contexts_with_behaviour(
        graph,
        "kb",
        "sess",
        ["Q0", "Q1"],
        ["G0", "G1"],
        behaviour_metrics=[],
        results_folder="/tmp/x",
    )

    assert len(graph.configs) == 2
    handler_0 = graph.configs[0]["callbacks"][0]
    handler_1 = graph.configs[1]["callbacks"][0]
    assert handler_0 is not handler_1, "每题必须新建独立 handler（复用会串 trace）"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_driver.py -v`
Expected: FAIL —— `AttributeError: module 'src.cli.eval_ragas' has no attribute 'generate_answers_and_contexts_with_behaviour'`

- [ ] **Step 3: 实现**

3a. 给 `_answer_one_question` 加关键字参数并透传（`src/cli/eval_ragas.py`）：

```python
async def _answer_one_question(
    graph: Any,
    kb_id: str,
    session_id: str,
    query: str,
    trace_id: str,
    *,
    callbacks: list | None = None,
) -> tuple[str, list[str], list[dict], list]:
```
把 `graph.ainvoke(...)` 改为（**只在有 callbacks 时才传 `config`**）：

```python
    invoke_kwargs: dict = {}
    if callbacks:
        invoke_kwargs["config"] = {"callbacks": callbacks}
    final_state = await graph.ainvoke(
        {
            "kb_id": kb_id,
            "session_id": session_id,
            "query": query,
            "trace_id": trace_id,
            "_history": [],
        },
        **invoke_kwargs,
    )
```

> ⚠️ **不要**写成 `config={"callbacks": callbacks} if callbacks else None`：那会变成
> `ainvoke(state, config=None)`，而 `tests/cli/test_eval_ragas.py` 的既有 mock 是
> `mock_ainvoke(state: dict)`（单参）⇒ `TypeError`。省略 `config` 才是"逐字保持改前行为"。

3b. `_collect_one_question` 同样加 `*, callbacks: list | None = None` 并透传给 `_answer_one_question`。

3c. 在文件内新增（放在 `generate_answers_and_contexts` 之后）：

```python
def _build_behaviour_dataset(questions: list[str], ground_truth: list[str]):
    """构造 DeepEval 数据集：Golden(input=题面, expected_output=参考答案)。

    `expected_output` 供 `TaskCompletion` 判定任务是否完成；缺它会让该指标失真。
    """
    from deepeval.dataset import EvaluationDataset, Golden

    return EvaluationDataset(
        goldens=[
            Golden(input=q, expected_output=g)
            for q, g in zip(questions, ground_truth, strict=True)
        ]
    )


async def generate_answers_and_contexts_with_behaviour(
    graph: Any,
    kb_id: str,
    session_id: str,
    questions: list[str],
    ground_truth: list[str],
    behaviour_metrics: list,
    results_folder: str,
) -> tuple:
    """跑图循环由 `evals_iterator` 驱动（design D6 定案的唯一可用形态）。

    两半缺一不可：① 循环体在 iterator 内（否则不评分）；② 分数事后从
    `test_run_*.json` 回读（eval loop 用副本，`metric.score` 恒 None）。
    **每题新建独立 `CallbackHandler`**（复用会串 trace）；**循环不得 break**
    （generator 只有耗尽才落盘 JSON）。
    """
    from deepeval.evaluate.configs import DisplayConfig
    from deepeval.integrations.langchain import CallbackHandler

    ds = _build_behaviour_dataset(questions, ground_truth)
    answers: list[str] = []
    contexts: list[list[str]] = []
    trace_ids: list[str] = []
    retrieval_details: list[list[dict]] = []
    sample_classes: list[str] = []
    trajectories: list[dict] = []
    handlers: list = []

    for i, _golden in enumerate(
        ds.evals_iterator(
            metrics=behaviour_metrics,
            display_config=DisplayConfig(
                results_folder=results_folder, inspect_after_run=False
            ),
        )
    ):
        trace_id = f"eval_{uuid.uuid4().hex[:12]}"
        trace_ids.append(trace_id)
        handler = CallbackHandler()  # 每题独立实例
        handlers.append(handler)
        trace_token = current_trace_id.set(trace_id)
        try:
            (
                answer,
                ctx_list,
                details,
                sample_class,
                trajectory,
            ) = await _collect_one_question(
                graph, kb_id, session_id, questions[i], trace_id, i + 1,
                callbacks=[handler],
            )
        finally:
            current_trace_id.reset(trace_token)
        answers.append(answer)
        contexts.append(ctx_list)
        retrieval_details.append(details)
        sample_classes.append(sample_class)
        trajectories.append(trajectory)

    return (
        answers, contexts, trace_ids, retrieval_details, sample_classes,
        trajectories, handlers,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_driver.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_behaviour_driver.py
git commit -m "feat(eval): 跑图循环改由 evals_iterator 驱动（每题独立 handler + callbacks 透传）"
```

---

### Task 5: 两条 fail-loud 断言（轨迹非空 + 取到分 + JSON 已落盘）

**Files:**
- Modify: `src/cli/eval_scoring.py`（新增 `assert_behaviour_scores_complete`）
- Test: `tests/cli/test_behaviour_fail_loud.py`

**Interfaces:**
- Produces: `def assert_behaviour_scores_complete(questions: list[str], rows: list[dict], trajectories: list[dict]) -> None` —— 任一不成立即 raise `RuntimeError`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_behaviour_fail_loud.py
from __future__ import annotations

import pytest

from src.cli.eval_scoring import assert_behaviour_scores_complete


def _rows(n: int):
    return [{"q_index": i, "input": f"Q{i}", "metrics": {"Task Completion": 1.0}} for i in range(n)]


def _traj(n: int):
    return [{"tool_calls": ["lookup"], "iterations": 2} for _ in range(n)]


def test_all_good_passes():
    assert_behaviour_scores_complete(["Q0"], _rows(1), _traj(1))


def test_missing_score_row_fails_loud():
    with pytest.raises(RuntimeError, match="行为分"):
        assert_behaviour_scores_complete(["Q0", "Q1"], _rows(1), _traj(2))


def test_empty_trajectory_fails_loud():
    with pytest.raises(RuntimeError, match="轨迹"):
        assert_behaviour_scores_complete(["Q0"], _rows(1), [{"tool_calls": [], "iterations": 0}])


def test_no_metric_score_at_all_fails_loud():
    rows = [{"q_index": 0, "input": "Q0", "metrics": {}}]
    with pytest.raises(RuntimeError, match="行为分"):
        assert_behaviour_scores_complete(["Q0"], rows, _traj(1))


def test_trajectory_count_mismatch_fails_loud():
    """轨迹数偏短 ⇒ 循环体一次都不跑，会静默通过 —— 必须显式拦（评审 Important）。"""
    with pytest.raises(RuntimeError, match="轨迹"):
        assert_behaviour_scores_complete(["Q0", "Q1"], _rows(2), _traj(1))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_fail_loud.py -v`
Expected: FAIL —— ImportError

- [ ] **Step 3: 写最小实现**（追加到 `src/cli/eval_scoring.py`）

```python
def assert_behaviour_scores_complete(
    questions: list[str], rows: list[dict], trajectories: list[dict]
) -> None:
    """行为轴 fail-loud：题数对齐 + 每题至少一个非 None 分 + 轨迹非空。

    为什么必须 fail-loud：`AgentLoopDetection` 在**无 trace 时源码直接置 0.0**
    （静默假分数）；若 JSON 缺失或循环提前结束，回读会得到空表 ⇒ 必须炸掉而不是
    把"没测到"当成"行为良好"。
    """
    if len(rows) != len(questions):
        raise RuntimeError(
            f"行为分题数不齐：questions={len(questions)} rows={len(rows)}（JSON 缺失或循环未耗尽）"
        )
    if len(trajectories) != len(questions):
        # 缺这条守卫时，trajectories 偏短会让下面的循环体一次都不跑 ⇒ 静默通过，
        # 正是本函数要防的"没测到当成没异常"（评审 Important）
        raise RuntimeError(
            f"轨迹数与题数不齐：questions={len(questions)} trajectories={len(trajectories)}"
        )
    for r in rows:
        if not any(v is not None for v in (r.get("metrics") or {}).values()):
            raise RuntimeError(f"第 {r.get('q_index')} 题没有任何行为分（行为分不可信）")
    for i, t in enumerate(trajectories):
        if not (t or {}).get("tool_calls") and not (t or {}).get("iterations"):
            raise RuntimeError(f"第 {i} 题轨迹为空（行为指标无 trace 时源码直接置 0.0）")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_fail_loud.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_scoring.py tests/cli/test_behaviour_fail_loud.py
git commit -m "feat(eval): 行为轴两条 fail-loud（题数/分数对齐 + 轨迹非空）"
```

---

### Task 6: `main()` 接线 + 报告含行为轴 + `hook_events > 0` 断言

**Files:**
- Modify: `src/cli/eval_ragas.py`（`main()`；`save_markdown_report` 加行为段）
- Test: `tests/cli/test_behaviour_report.py`

**Interfaces:**
- Consumes: T1–T5 全部
- Produces: 报告含「Agent 行为轴」段；**不进** `overall_score` / `check_gate`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_behaviour_report.py
"""报告含行为段，且行为分不参与主分。"""

from __future__ import annotations

import pandas as pd

from src.cli.eval_ragas import save_markdown_report


class _FakeResult:
    """`save_markdown_report` 只要求 `result.to_pandas()`（`eval_ragas.py:620`）。"""

    def to_pandas(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"q_index": 0, "faithfulness": 1.0, "sample_class": "pure_kb"}]
        )


def test_report_contains_behaviour_section(tmp_path):
    out = tmp_path / "r.csv"

    save_markdown_report(
        _FakeResult(),
        questions=["Q0"],
        output_path=str(out),
        sample_classes=["pure_kb"],
        behaviour_rows=[
            {"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}}
        ],
    )

    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "Agent 行为轴" in text
    assert "Task Completion" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_report.py -v`
Expected: FAIL —— `TypeError: save_markdown_report() got an unexpected keyword argument 'behaviour_rows'`

- [ ] **Step 3: 实现**

3a. `save_markdown_report(...)` 增可选参 `behaviour_rows: list[dict] | None = None`，在报告末尾追加一段（标题固定为 `## Agent 行为轴`，逐题列 `input` 与各指标分；并注明「**不进主分/门禁**」）。

3b. `main()` 评估模式中，把生成段替换为行为轴驱动（仅在 `ground_truth` 可用时；不可用则保持原路径并记 warning）：

```python
        judge_evidence = HookEvidence()
        bridge_judge = build_bridge_judge(evidence=judge_evidence)
        metrics = build_metrics(bridge_judge, build_embeddings())
        from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM
        from src.cli.eval_scoring import (
            assert_behaviour_scores_complete,
            build_behaviour_metrics,
            read_behaviour_scores,
        )

        behaviour_eval_llm = BridgeDeepEvalLLM(bridge_judge)
        behaviour_metrics = build_behaviour_metrics(behaviour_eval_llm)
        behaviour_out = str(Path(settings.RAGAS_REPORT_DIR) / f"behaviour_{timestamp}")
```

`async def _ensure_store_and_generate()` 内改为调用 T4 的新函数并解包 7 元组；随后：

```python
        behaviour_rows = read_behaviour_scores(behaviour_out)
        assert_behaviour_scores_complete(questions, behaviour_rows, trajectories)
        if not any(e.get("phase") == "response" for e in judge_evidence.events):
            raise RuntimeError("行为轴 hooks 未触发（可能走了同步分支）")
```

3c. 把 `behaviour_rows=behaviour_rows` 传进 `save_markdown_report(...)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli/test_behaviour_report.py tests/cli -q 2>&1 | tail -5`
Expected: 新测试 PASS；`tests/cli` 全绿

- [ ] **Step 5: 提交**

```bash
git add src/cli/eval_ragas.py tests/cli/test_behaviour_report.py
git commit -m "feat(eval): 行为轴接线 main + 报告段 + hooks 触发断言（不进主分/门禁）"
```

---

### Task 7: 回归测试——只挂 handler 不驱动 iterator 时取不到分

**Files:**
- Test: `tests/cli/test_behaviour_regression_forms.py`

**Interfaces:** 只测"被否定的形态确实拿不到分"，防止回归（tasks 5.2）

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_behaviour_regression_forms.py
"""钉住被否定的形态及其后果（design D6 / R9）。

两条**实测**事实：
1. 未驱动任何评测时，指标实例上不会有分数（`score` 为构造默认 `None`）
   ⇒ 取分**只能**从 `test_run_*.json` 回读（eval loop 用 `copy_metrics()` 副本）。
2. `AgentLoopDetection` 在**无 trace** 时 `measure()` **不报错、直接给 `0.0`**（静默假分数）
   —— 这正是行为轴必须有 fail-loud（`assert_behaviour_scores_complete`）的理由。
"""

from __future__ import annotations

from deepeval.metrics import AgentLoopDetectionMetric
from deepeval.test_case import LLMTestCase


def test_fresh_metric_has_no_score_before_any_run():
    # 用 loop-detection：它**不需要裁判**，构造期不触网（TaskCompletion 需要 model，
    # 传 None 会回落到 OpenAIModel 并因缺 key 直接报错）。
    m = AgentLoopDetectionMetric(threshold=0.5)

    assert m.score is None


def test_loop_detection_silently_scores_zero_without_trace():
    """无 trace ⇒ `0.0` 且**不抛异常**：静默假分数，故必须有 fail-loud 兜住。"""
    m = AgentLoopDetectionMetric(threshold=0.5)

    score = m.measure(LLMTestCase(input="q", actual_output="a"))

    assert score == 0.0
    assert m.success is False
```

> ⚠️ 首版计划只写了"`score is None`"一句，docstring 却声称钉住"只挂 handler 不驱动 iterator ⇒ 不评分"
> —— **声称大于证据**（评审 Important）。上表两条断言是实测可验的替代，且第 2 条正是 R9 的静默假分数。

- [ ] **Step 5: 提交**

```bash
git add tests/cli/test_behaviour_regression_forms.py
git commit -m "test(eval): 钉住被否定的取分形态（未驱动 iterator ⇒ metric.score 恒 None）"
```

---

### Task 8: 阶段收口（清单写回 D6 + 全绿 + 1 题活体冒烟 + 回写 tasks.md）

**Files:**
- Modify: `docs/openspec/changes/deepeval-bridge-eval/{design.md, tasks.md}`

- [ ] **Step 1: 行为指标清单写回 design D6**（tasks 5.7 要求"清单在本组内定稿并写回 D6"）：把三项定稿（`TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`）与"≤5、先上 3"写明，并注明 `AgentLoopDetection` 不调 LLM、`TaskCompletion` 逐题取任务。

- [ ] **Step 2: `summarize_trajectory` 定位写回**（tasks 5.8）：在 `docs/agents/api_contract.md` / 代码 docstring 注明它**只作报告诊断摘要**、SHALL NOT 作行为指标数据源。

- [ ] **Step 3: 全量单测**

Run: `POSTGRES_HOST=localhost python -m pytest tests/cli tests/config tests/infra/llm/provider tests/api -q 2>&1 | tail -5`
Expected: 全绿（唯一 skip 仍是 `test_generate_transforms.py` 的 pin 致红用例）

- [ ] **Step 4: 静态闸门 + 文档闸门**

```bash
ruff check src/cli src/infra/llm/provider src/api && ruff format --check src/cli src/infra/llm/provider src/api
pyright src/cli/eval_ragas.py src/cli/eval_scoring.py src/infra/llm/provider/deepeval_llm.py
openspec validate deepeval-bridge-eval --strict
python -m src.cli.check_docs
```

- [ ] **Step 5: 1 题活体冒烟（真链路，验收核心）**

用真实问题经 `generate_answers_and_contexts_with_behaviour` 跑 1 题，断言：① `test_run_*.json` 落盘；② 三指标均有分；③ **`hook_events > 0`**（证明行为轴走异步、hooks 生效）。

Expected: 三指标有值；`hook_events > 0`；报告含「Agent 行为轴」段。

- [ ] **Step 6: 回写 `tasks.md`**：勾选 5.1–5.9，并把本阶段与 tasks.md 的差异记在组 5 抬头。

- [ ] **Step 7: 提交**

```bash
git add docs/openspec/changes/deepeval-bridge-eval src/
git commit -m "docs(openspec): deepeval-bridge-eval Phase 2 收口（D6 清单定稿 + tasks 5.x 勾选）"
```

---

## Phase 2 完成判据

1. 适配器 `BridgeDeepEvalLLM` 存在且有单测；行为指标由 **provider 层裁判**驱动（无 `OpenAIModel` 直连）。
2. 三项行为指标产出逐题分，且分从 `test_run_*.json` 的 `testCases[].metricsData` 回读。
3. 每题一个独立 `CallbackHandler`；循环跑至耗尽（JSON 落盘）。
4. 两条 fail-loud 生效（题数/分数对齐、轨迹非空）。
5. **1 题活体冒烟：`hook_events > 0`**，三指标有值。
6. `pytest tests/cli tests/config tests/infra/llm/provider tests/api` 全绿；`ruff` / `pyright` / `openspec validate --strict` / `check_docs` 全过。
7. 行为轴**未进**主分与门禁。

## 未纳入本阶段（Phase 3–4 才做，勿提前实现）

- `--generate` 硬护栏与出题路标注（组 6）；逃生门可替换边界（组 7）；Langfuse 回写口径含行为分（组 8）；`build_ragas_judge` / `build_ragas_embeddings` / `build_native_client` 的**删除** + `RAGAS_EVAL_CONCURRENCY` 处置 + 新 ADR（组 9）。
- 全量活体、阈值重标定、NaN 率复核、台账记 `bridge-0.3.9` 行（组 10）——**须待新题集**（design R11）。

---

## Phase 2 执行记录与交接（2026-10-17 完成）

**状态：8/8 任务完成。最终整支评审判 "Ready to merge"；评审提出的 1 Important + 1 spec 强制 Minor 已做收尾修波，其中 Important 的首次修法被复核推翻并已按裁决回退（见下）。**

- 交付分支 `feat/deepeval-bridge-eval`，基点 `854ddeac`，共 **22 个提交**，工作区干净。
- **验收证据**：`tests/cli tests/config tests/infra/llm/provider tests/api` = **497 passed / 1 skipped / 0 failed**；`ruff check`/`format --check`、`pyright`、`openspec validate --strict`、`check_docs`（0 error）全绿。
- **1 题活体冒烟**（真裁判、`generate_answers_and_contexts_with_behaviour`）：三指标各 **1.0**、`test_run_*.json` 落盘、`hook_events = 8`。

### 交付物

| 文件 | 内容 |
|---|---|
| `src/infra/llm/provider/deepeval_llm.py` | `BridgeDeepEvalLLM(DeepEvalBaseLLM)` —— 把 provider 层裁判接进 DeepEval 原生指标 |
| `src/cli/eval_scoring.py` | `build_behaviour_metrics`（三项定稿）/ `read_behaviour_scores`（逐题回读）/ `assert_behaviour_scores_complete`（两条 fail-loud） |
| `src/cli/eval_ragas.py` | 跑图循环改由 `evals_iterator` 驱动（每题独立 handler）/ `main()` 接线 / 报告「Agent 行为轴」段 / hooks 触发断言 |

### 关键裁决与偏离（判错即需返工处）

1. **计划自查 + 执行期共发现 3 处计划自相矛盾**，均已"先改计划、再施工"：T1 的 `_content`（`isinstance(BaseMessage)`）与假消息类不匹配；T4 的 `config=None` 写法会破坏既有 mock、以及假 dataset 应实现 `evals_iterator` 而非 `__iter__`。三处实现均比计划原稿更正确。
2. **T7 哨兵改写**：原哨兵只断言构造默认值却在 docstring 声称钉住"不驱动 iterator ⇒ 不评分"（声称大于证据）⇒ 改为两条**实测**事实（未驱动⇒无分；**无 trace 时 loop-detection 静默 0.0 且不报错**）。
3. **最终评审的 Important 经复核推翻并回退**：为让行为轴判官调用可 `trace_id` 归因，曾加逐题 `judge_evidence.mark()`；复核读 DeepEval 源码证明**行为指标在 `evals_iterator` 耗尽后才评分**（异步路径 `_a_evaluate_traces`），单槽证据桶届时只剩**最后一题**的 id ⇒ 会把所有行为轴判官调用盖成最后一题（**比 `None` 更糟**）⇒ 已回退，并把该边界写进 design **R9**，延后 Phase 3（组 8，可观测性/Langfuse 口径）。**注**：这一误判起于"评分随生成器推进发生"的心智模型 —— 那是**同步**路径的行为，本阶段明确走异步。
4. **冒烟的 `hook_events = 8` 是混合计数**（玩具 SUT 也建在同一 judge 上）⇒ 已在 `docs/tmp/deepeval-poc/RESULTS.md` 订正：判官走异步的**依据是"判官驱动的两项拿到分"**，不是事件计数。生产路径 SUT(`get_llm`) 与 judge(`build_bridge_judge`) 本就隔离。

### 本阶段**未**证明（勿误当已验）

- **真实 agent 的行为分分布/阈值/NaN 行为**：冒烟用的是**玩具图**（无 KB/检索/精排）⇒ 真 SUT 的全量分布属 Phase 3–4（组 10）。
- **"单一事件循环 + DB 连接池不跨 loop"在真引擎上的成立**：仅由 Q1 探测（玩具）支撑，真库验证留 Phase 4。
- **行为轴判官调用的逐题 `trace_id` 归因**：本阶段做不到（见上第 3 条），R9 已记，延后 Phase 3。

### 未勾选（诚实保留）

- **5.1（部分）**：`nest_asyncio` 注释**已补**；但"专门回归测试钉住异步事件循环形态"**未写** —— 异步形态改由运行时 `hook_events > 0` 断言守卫。
- **5.6**：并发负向验证**未做** —— 且当前**无并发可验**（行为轴循环是串行的）；将来若并行化该循环，须补此测试。

### 遗留 minor（最终评审判定：**无一项需在合并前修**；转交 Phase 3）

T1 `self.name=None`（被 override 掩盖）/ `"bridge-judge"` 字面量未入 `src/config/` / 非 `BaseMessage` 兜底分支无测；T2 `build_behaviour_metrics` 缺 `-> list`；T4 两个新参数缺 Args 文档、`list`/`tuple` 注解偏松；T5 一条用例 fixture 形状、非拒绝边界无测；T6 报告测试未断言分值、hooks 断言只看相位、`_handlers` 未用；T8 5.2 字面形态未单独测、`summarize_trajectory` 未更新 `api_contract.md`（判定 docstring 即恰当归属）、D6 一处措辞。
另：`generate_answers_and_contexts` 现为**生产死代码**（仅测试用），建议 Phase 3 在文件头注明，避免"哪个是真入口"的困惑。
