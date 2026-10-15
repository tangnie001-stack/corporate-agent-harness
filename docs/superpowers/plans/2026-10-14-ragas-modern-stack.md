# ragas-modern-stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 RAGAS 评测执行内核从已弃用的 legacy API 迁到现代栈（`@experiment` + `collections` + `llm_factory`/`embedding_factory`），并整改出题链路、裁判策略与 `overall_score` 口径。

**Architecture:** 新增 provider 层的「按角色构造 native `AsyncOpenAI`」出口（D9），在其上构造现代裁判 LLM（`temperature=0`、不发 `top_p`、显式超时，D6）与现代 embeddings；`run_evaluation` 改为 `@experiment` 逐行 `ascore`（`Dataset` 用 `inmemory`，**行级**信号量，D4/D5），**失败记 `error` 列不丢行**（D2），行带 `q_index` 以对抗 `arun` 的完成序乱序，返回 `Experiment`（**其 `.to_pandas()` 让产出层零改动**）；`overall_score` 统一到主分口径（D11）。出题侧换官方 `default_transforms` 全量 + `llm_context`（D8）。

**Tech Stack:** Python 3.12 · `ragas==0.4.3`（`ragas.experiment` / `ragas.metrics.collections` / `ragas.llms.llm_factory` / `ragas.embeddings.base.embedding_factory`）· `instructor 1.15.4` · pytest · langgraph。

**Spec:** `docs/openspec/changes/ragas-modern-stack/proposal.md` · `design.md`（D1–D11）· `specs/evaluation-pipeline/spec.md`（6 条 ADDED）

## Global Constraints

- 指标集**恒为五项**：`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall` / `answer_correctness`（`RAGAS_METRIC_COLUMNS`）。
- **stdout 形状永不变**：`_print_metric_averages` 只打**恰好五行** `  {metric}: {value:.4f}`（`src/cli/compare_retrieval.py:82-110` 是解析契约；`tests/cli/test_eval_ragas.py:659` 锁行数）。
- 裁判采样：`temperature=0`、**不传 `top_p`**；并**显式设超时**（思考模型慢）。
- `Dataset` 用 `backend="inmemory"`（不落盘）。
- 并发：**行级**信号量，`RAGAS_EVAL_CONCURRENCY` 默认 **5**；行内五项串行。
- **行序契约**：`arun` 按完成序 append，**必须**靠行内 `q_index` 回排，否则分数会写到别的 trace。
- 产出层（CSV/MD/DB/Langfuse/门禁）**不改签名**——`Experiment.to_pandas()` 已足够（`ragas/dataset.py:316`）。
- 不动检索栈：`src/rag/retrieval.py` / `src/infra/db/vector_store/` / `src/agents/tools/rag_tools.py`。
- 宿主跑测试须带 `POSTGRES_HOST=localhost` 前缀（`tests/conftest.py:50` 守卫）。
- 提交信息中文、`<type>(<scope>): <subject>` 形状。

---

### Task 1: provider 层新增「按角色构造 native client」出口

**Files:**
- Modify: `src/infra/llm/provider/factory.py`
- Test: `tests/infra/llm/provider/test_native_client.py`（新建）

**Interfaces:**
- Consumes: `factory._resolve_model(role)`（已存在，返回含 `.api_key` / `.base_url` / `.model` 的 `ResolvedModel`）
- Produces: `factory.build_native_client(role: str) -> AsyncOpenAI`

- [ ] **Step 1: Write the failing test**

```python
"""native client 出口：端点/密钥取自 resolve_role，与 build_chat 同源。"""

import pytest

from src.infra.llm.provider import factory


def test_build_native_client_uses_resolved_endpoint(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    client = factory.build_native_client("judge")

    assert str(client.base_url).rstrip("/").endswith("/compatible-mode/v1")
    assert client.api_key == "k"


def test_build_native_client_rejects_unknown_role(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    with pytest.raises(KeyError):
        factory.build_native_client("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/provider/test_native_client.py -v`
Expected: FAIL with `AttributeError: module 'src.infra.llm.provider.factory' has no attribute 'build_native_client'`

- [ ] **Step 3: Write minimal implementation**

`src/infra/llm/provider/factory.py` import 区加 `from openai import AsyncOpenAI`，在 `build_embeddings` 之后追加：

```python
def build_native_client(role: str) -> AsyncOpenAI:
    """按角色构造 native `AsyncOpenAI` 客户端（供现代 RAGAS 工厂使用）。

    现代 `llm_factory` / `embedding_factory` 的 `client` 必填且须 native 客户端；
    端点/密钥取自既有 `resolve_role`（与 `build_chat` 同源），避免 CLI 重复解析。

    Args:
        role: 角色名（main/classify/summary/judge/embedding）。

    Returns:
        AsyncOpenAI：指向该角色最终生效端点的异步客户端。

    Raises:
        KeyError: 角色未知或无可用模型（沿用 resolve_role 的 fail-fast）。
    """
    resolved = _resolve_model(role)
    return AsyncOpenAI(api_key=resolved.api_key, base_url=resolved.base_url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/provider/test_native_client.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/factory.py tests/infra/llm/provider/test_native_client.py
git commit -m "feat(llm): provider 层新增按角色构造 native AsyncOpenAI 的出口"
```

---

### Task 2: 现代裁判 LLM 与 embeddings 工厂（采样 + 超时）

**Files:**
- Modify: `src/infra/llm/provider/factory.py`
- Test: `tests/infra/llm/provider/test_ragas_modern_factories.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `factory.build_native_client(role)`
- Produces: `factory.build_ragas_judge() -> InstructorBaseRagasLLM`；`factory.build_ragas_embeddings() -> BaseRagasEmbedding`

- [ ] **Step 1: Write the failing test**

```python
"""现代 RAGAS 工厂：temperature=0、不发 top_p、显式超时（design D6）。"""

from src.infra.llm.provider import factory


def test_ragas_judge_sampling_and_timeout(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    llm = factory.build_ragas_judge()

    # instructor 的 model_args 构造期落定；RAGAS 内建默认是 0.01/0.1，会被部分模型 400
    assert llm.model_args["temperature"] == 0
    assert "top_p" not in llm.model_args
    # client 须带显式超时（思考模型偶尔 >30s）
    assert llm.client.timeout is not None


def test_ragas_embeddings_has_modern_interface(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    emb = factory.build_ragas_embeddings()

    assert hasattr(emb, "aembed_text")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/provider/test_ragas_modern_factories.py -v`
Expected: FAIL with `AttributeError: ... 'build_ragas_judge'`

- [ ] **Step 3: Write minimal implementation**

`.env` / `settings` 增 `RAGAS_JUDGE_TIMEOUT`（秒，默认 `300.0`；可写入 `src/config/settings.py` 与 `.env.template`/`.env.example`）。

在 `build_native_client` 之后追加：

```python
def build_ragas_judge():
    """构造现代 RAGAS 裁判 LLM（`llm_factory`）。

    采样策略（design D6）：`temperature=0` 且**不传 `top_p`** —— RAGAS 内建默认
    `temperature=0.01` / `top_p=0.1` 会被部分模型以 400 拒绝（实测百炼 kimi-k3）；
    `top_p=None` 会被 instructor 从请求里摘掉。client 另设**显式超时**（思考模型慢，
    超时不足会把分数变成失败）。

    Returns:
        InstructorBaseRagasLLM：供 `ragas.metrics.collections` 指标使用。
    """
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory

    resolved = _resolve_model("judge")
    client = AsyncOpenAI(
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        timeout=settings.RAGAS_JUDGE_TIMEOUT,
    )
    return llm_factory(resolved.model, client=client, temperature=0, top_p=None)


def build_ragas_embeddings():
    """构造现代 RAGAS embeddings（`embedding_factory`）。

    供 `answer_relevancy` / `answer_correctness` 计算语义相似度。

    Returns:
        BaseRagasEmbedding：带 `aembed_text` / `embed_text` 的现代嵌入实例。
    """
    from openai import AsyncOpenAI
    from ragas.embeddings.base import embedding_factory

    resolved = _resolve_model("embedding")
    client = AsyncOpenAI(
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        timeout=settings.RAGAS_JUDGE_TIMEOUT,
    )
    return embedding_factory("openai", model=resolved.model, client=client)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/provider/test_ragas_modern_factories.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/factory.py src/config/settings.py .env.template .env.example tests/infra/llm/provider/test_ragas_modern_factories.py
git commit -m "feat(llm): 现代 RAGAS 裁判/embedding 工厂（temperature=0、不发 top_p、显式超时）"
```

---

### Task 3: `run_evaluation` 迁到现代栈（`@experiment` + 失败可见 + 行级并发 + 行序回排）

**Files:**
- Modify: `src/cli/eval_ragas.py:314-416`（`run_evaluation` 整体重写）、`main()` 的裁判接线（`:706-777`）
- Modify: `src/cli/eval_scoring.py`（新增 `build_metrics`）
- Modify: `src/config/const.py`（新增 `RAGAS_EVAL_CONCURRENCY`）
- Test: `tests/cli/test_eval_modern_stack.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `build_ragas_judge()` / `build_ragas_embeddings()`
- Produces:
  - `eval_scoring.build_metrics(judge, embeddings) -> dict[str, Any]`
  - `eval_ragas._EvalResult`（持已排序 DataFrame，保留 `.to_pandas()` 契约）
  - `eval_ragas.run_evaluation(questions, ground_truth, answers, contexts, metrics) -> _EvalResult`

- [ ] **Step 1: Write the failing test**

```python
"""现代栈评分内核：逐行 ascore、失败记 error、不丢行、行序保持。"""

import asyncio

import pandas as pd

from src.config.const import RAGAS_EVAL_CONCURRENCY


class _FakeMetric:
    def __init__(self, value=None, exc=None):
        self._value, self._exc = value, exc

    async def ascore(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        outer = self

        class _R:
            value = outer._value

        return _R()


def _metrics(faith=1.0, relev=1.0, prec=1.0, rec=1.0, corr=1.0, relev_exc=None):
    return {
        "faithfulness": _FakeMetric(value=faith),
        "answer_relevancy": _FakeMetric(value=relev, exc=relev_exc),
        "context_precision": _FakeMetric(value=prec),
        "context_recall": _FakeMetric(value=rec),
        "answer_correctness": _FakeMetric(value=corr),
    }


def _run(metrics, n=2):
    from src.cli import eval_ragas

    return eval_ragas.run_evaluation(
        [f"q{i}" for i in range(n)],
        [f"g{i}" for i in range(n)],
        [f"a{i}" for i in range(n)],
        [[f"c{i}"] for i in range(n)],
        metrics,
    )


def test_run_evaluation_keeps_row_and_row_order_on_failure():
    df = _run(_metrics(relev_exc=TimeoutError("slow")), n=3).to_pandas()

    assert len(df) == 3  # 不丢行
    assert list(df["q_index"]) == [0, 1, 2]  # 行序回排（arun 按完成序 append）
    assert df.loc[0, "faithfulness"] == 1.0
    assert pd.isna(df.loc[0, "answer_relevancy"])  # 失败指标留空
    assert "TimeoutError" in df.loc[0, "error"]


def test_run_evaluation_clean_run_has_empty_error():
    df = _run(_metrics(), n=2).to_pandas()

    assert list(df["error"]) == ["", ""]
    assert df.loc[1, "context_recall"] == 1.0


def test_concurrency_constant_is_positive():
    assert RAGAS_EVAL_CONCURRENCY >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_modern_stack.py -v`
Expected: FAIL（`run_evaluation` 仍是旧签名 / `RAGAS_EVAL_CONCURRENCY` 不存在）

- [ ] **Step 3: Write minimal implementation**

`src/config/const.py` 追加：

```python
# 现代 RAGAS 评测的行级并发上限（N 题 × 5 指标 × 每题内部多次裁判调用，
# 全量并发会打爆裁判端点/额度；5 为实测安全的起点）。
RAGAS_EVAL_CONCURRENCY: int = 5
```

`src/cli/eval_scoring.py` 追加：

```python
def build_metrics(judge, embeddings) -> dict:
    """构造现代 collections 五指标实例（口径与 legacy 对齐，design D3）。

    Args:
        judge: 现代裁判 LLM（`llm_factory` 产物）
        embeddings: 现代 embeddings（`embedding_factory` 产物）

    Returns:
        指标名 -> 指标实例（顺序即评估顺序）
    """
    from ragas.metrics.collections import (
        AnswerCorrectness,
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
    )

    return {
        "faithfulness": Faithfulness(llm=judge),
        "answer_relevancy": AnswerRelevancy(llm=judge, embeddings=embeddings),
        "context_precision": ContextPrecision(llm=judge),
        "context_recall": ContextRecall(llm=judge),
        "answer_correctness": AnswerCorrectness(llm=judge, embeddings=embeddings),
    }
```

`src/cli/eval_ragas.py` 把 `run_evaluation` 整体替换为：

```python
class _EvalResult:
    """`Experiment` 的行序适配 + `.to_pandas()` 契约（产出层零改动）。

    `Experiment.to_pandas()` 的行序是 `arun` 的**完成序**，不保证与输入下标对齐
    （实测 `experiment.py:188` 用 `as_completed`）；本类持一个已按 `q_index` 排好
    的 DataFrame，并保留既有 `.to_pandas()` 调用面。
    """

    def __init__(self, df: pd.DataFrame) -> None:
        """保存已排序的结果表。

        Args:
            df: 已按 q_index 排序并重置索引的结果表
        """
        self._df = df

    def to_pandas(self) -> pd.DataFrame:
        """返回已排序的结果表（与旧 `EvaluationResult.to_pandas()` 同形）。"""
        return self._df


async def _score_metrics(metric_name: str, metric, row: dict):
    """按指标名调 `ascore`（各指标入参各不相同，在此集中映射）。"""
    if metric_name == "faithfulness":
        return await metric.ascore(
            user_input=row["question"],
            response=row["answer"],
            retrieved_contexts=row["contexts"],
        )
    if metric_name == "context_precision":
        return await metric.ascore(
            user_input=row["question"],
            reference=row["ground_truth"],
            retrieved_contexts=row["contexts"],
        )
    if metric_name == "context_recall":
        return await metric.ascore(
            user_input=row["question"],
            retrieved_contexts=row["contexts"],
            reference=row["ground_truth"],
        )
    if metric_name == "answer_correctness":
        return await metric.ascore(
            user_input=row["question"],
            response=row["answer"],
            reference=row["ground_truth"],
        )
    # answer_relevancy
    return await metric.ascore(user_input=row["question"], response=row["answer"])


def run_evaluation(
    questions: list[str],
    ground_truth: list[str],
    answers: list[str],
    contexts: list[list[str]],
    metrics: dict,
) -> Any:
    """用现代 RAGAS 栈评五项指标，返回 `Experiment`（`.to_pandas()` 供产出层）。

    逐行独立打分：任一指标失败在该行记 `error`（design D2）——**不丢行、不写 NaN
    冒充分数**。行级并发受 `RAGAS_EVAL_CONCURRENCY` 限制（design D5）。`arun` 按
    **完成序** append，故每行带 `q_index` 并在返回前按它回排，保证与
    questions/trace_ids 的下标对齐契约（写错 trace 是静默事故）。

    Args:
        questions: 问题列表
        ground_truth: 参考答案列表
        answers: 系统生成的回答列表
        contexts: 检索到的上下文列表
        metrics: `eval_scoring.build_metrics` 产物的指标实例表

    Returns:
        Experiment：行含五项指标 + `q_index` + `error`
    """
    from ragas import Dataset, experiment

    rows = [
        {
            "q_index": i,
            "question": q,
            "answer": a,
            "ground_truth": g,
            "contexts": c,
        }
        for i, (q, a, g, c) in enumerate(
            zip(questions, answers, ground_truth, contexts, strict=True)
        )
    ]

    dataset = Dataset(name="ragas_eval", backend="inmemory")
    for r in rows:
        dataset.append(r)

    sem = asyncio.Semaphore(max(1, RAGAS_EVAL_CONCURRENCY))

    @experiment()
    async def _score_row(row: dict) -> dict:
        out: dict = {"q_index": row["q_index"], "error": ""}
        errors: list[str] = []
        async with sem:
            for name, metric in metrics.items():
                try:
                    res = await _score_metrics(name, metric, row)
                    out[name] = float(res.value)
                except Exception as exc:  # noqa: BLE001  # 失败可见，不吞
                    out[name] = float("nan")
                    errors.append(f"{name}:{type(exc).__name__}")
        out["error"] = ";".join(errors)
        return out

    core_logging.log_event(Event.EVALUATION_START, samples=len(rows))
    view = asyncio.run(_score_row.arun(dataset))
    core_logging.log_event(Event.EVALUATION_DONE)
    # arun 按【完成序】append；按 q_index 回排后再交产出层，保证下标对齐契约
    # （不对齐会把分数静默写到别的 trace 上）。
    df = view.to_pandas().sort_values("q_index").reset_index(drop=True)
    return _EvalResult(df)
```

`main()` 里把裁判接线（`llm_wrapper` / `embeddings_wrapper` 两行与上方 `from ragas.embeddings import ...` / `from ragas.llms import ...`）替换为：

```python
        from src.infra.llm.provider import factory as _llm_factory

        metrics = eval_scoring.build_metrics(
            _llm_factory.build_ragas_judge(),
            _llm_factory.build_ragas_embeddings(),
        )
```

调用处改为 `result = run_evaluation(questions, ground_truth, answers, contexts, metrics)`；**产出层调用保持原样**（`result.to_pandas()` 依旧可用）。

- [ ] **Step 4: Run test to verify it passes**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_modern_stack.py tests/cli/ -v`
Expected: PASS（`tests/cli` 全绿；旧 `TestRunEvaluation` 里 mock `ragas.evaluate` 的用例需按新签名改掉——若仍失败，删其 `@patch("ragas.evaluate")` / `@patch("datasets.Dataset.from_dict")` 装饰器并改为传假 `metrics`）

- [ ] **Step 5: Commit**

```bash
git add src/cli/eval_ragas.py src/cli/eval_scoring.py src/config/const.py tests/cli/
git commit -m "feat(eval): 执行内核迁到现代 RAGAS @experiment（失败可见 + 行级并发 + 行序回排）"
```

---

### Task 4: `overall_score` 统一到主分口径 + 阈值单一来源

**Files:**
- Modify: `src/cli/eval_ragas.py:922-992`（`_save_eval_report`）、`:571-576`（删 MD Note）
- Modify: `src/config/const.py`（新增 `EVAL_PASS_THRESHOLD`）
- Test: `tests/cli/test_eval_overall_score.py`（新建）

**Interfaces:**
- Consumes: `eval_scoring.main_score_means(df)`、`eval_scoring.apply_sample_masks(df, classes)`
- Produces: `const.EVAL_PASS_THRESHOLD: float`；`_save_eval_report(kb_id, df, qa_count, output_path, retrieval_details, sample_classes)`

- [ ] **Step 1: Write the failing test**

```python
"""overall_score = 纯 KB 五项均值；passed 取单一常量。"""

import pandas as pd

from src.cli import eval_ragas
from src.config.const import EVAL_PASS_THRESHOLD


def test_overall_score_is_pure_kb_five_metric_mean(monkeypatch):
    captured = {}

    class _Svc:
        async def insert_eval_report(self, entity):
            captured["overall"] = entity.overall_score
            captured["passed"] = entity.passed

    class _Entity:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    monkeypatch.setattr(
        eval_ragas, "_eval_report_deps", lambda: (_Svc(), _Entity), raising=False
    )

    df = pd.DataFrame(
        {
            "faithfulness": [1.0, 0.0],
            "answer_relevancy": [1.0, 0.0],
            "context_precision": [1.0, 0.0],
            "context_recall": [1.0, 0.0],
            "answer_correctness": [1.0, 0.0],
            "error": ["", ""],
        }
    )
    # 仅 pure_kb 行参与主分 → 五项均 1.0 → overall=1.0
    eval_ragas._save_eval_report(
        "kb", df, 2, "r.csv", None, sample_classes=["pure_kb", "web"]
    )

    assert captured["overall"] == 1.0
    assert captured["passed"] is (1.0 >= EVAL_PASS_THRESHOLD)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_overall_score.py -v`
Expected: FAIL（`_save_eval_report` 参数不符 / `EVAL_PASS_THRESHOLD` 不存在）

- [ ] **Step 3: Write minimal implementation**

`src/config/const.py` 追加：

```python
# 评测「通过」阈值（单一来源：落库 passed 与 kb_eval API 下发同用此值）。
# 口径已由旧「四项加权、未掩码」改为「纯 KB 五项均值」，新口径系统性偏低
# 0.03~0.13（实测 n=7，区间 0.68~0.92）——此值**待标定**，暂沿用 0.70。
EVAL_PASS_THRESHOLD: float = 0.70
```

`_save_eval_report` 改为：

```python
def _eval_report_deps():
    """DB 落库依赖（独立函数便于测试替身注入）。"""
    from src.infra.db.models.eval_report import EvalReportModel as EvalReportEntity
    from src.services.app_service import AppService

    return AppService(), EvalReportEntity


def _save_eval_report(
    kb_id: str,
    df: pd.DataFrame,
    qa_count: int,
    output_path: str,
    retrieval_details: list[list[dict]] | None = None,
    sample_classes: list[str] | None = None,
) -> None:
    """把评估结果持久化到 eval_report 表。

    `overall_score` 统一到**主分口径**（纯 KB 五项均值，design D11），替换原先
    「四项加权、未掩码」的旧口径；`passed` 取常量 `EVAL_PASS_THRESHOLD`。
    """
    try:
        from src.cli import eval_scoring
        from src.infra.db.engine import run_and_dispose

        svc, eval_report_entity = _eval_report_deps()
        if sample_classes is not None:
            df = eval_scoring.apply_sample_masks(df, sample_classes)
        detail, _avg = _build_eval_detail(df, qa_count, retrieval_details)

        means = eval_scoring.main_score_means(df)
        present = [v for v in means.values() if pd.notna(v)]
        overall = float(sum(present) / len(present)) if present else None

        async def _do_insert():
            entity = eval_report_entity(
                id=str(uuid.uuid4()),
                kb_id=kb_id,
                run_type="manual",
                qa_count=qa_count,
                faithfulness=means.get("faithfulness"),
                answer_relevancy=means.get("answer_relevancy"),
                context_precision=means.get("context_precision"),
                context_recall=means.get("context_recall"),
                overall_score=overall,
                passed=overall is not None and overall >= EVAL_PASS_THRESHOLD,
                report_path=output_path,
                triggered_by=None,
                detail_json=detail,
            )
            await svc.insert_eval_report(entity)

        asyncio.run(run_and_dispose(_do_insert()))
        core_logging.log_event(Event.EVAL_REPORT_SAVED, kb_id=kb_id)
    except Exception as e:  # noqa: BLE001
        core_logging.log_event(Event.EVAL_SAVE_FAILED, err=str(e))
```

调用处补 `sample_classes`：`_save_eval_report(kb_id, result.to_pandas(), len(questions), output_path, retrieval_details, sample_classes)`。并删除 `eval_ragas.py:571-576` 那段「两口径分叉」Note。

- [ ] **Step 4: Run test to verify it passes**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_overall_score.py tests/cli/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/eval_ragas.py src/config/const.py tests/cli/test_eval_overall_score.py
git commit -m "feat(eval): overall_score 统一到主分口径 + 通过阈值单一来源"
```

---

### Task 5: `kb_eval` API 补 `answer_correctness` 并下发阈值；前端改读

**Files:**
- Modify: `src/api/kb_eval.py:36-62`
- Modify: `deploy/nginx/html/index.html:1113`
- Test: `tests/api/test_kb_eval_payload.py`（新建）

**Interfaces:**
- Consumes: `const.EVAL_PASS_THRESHOLD`
- Produces: `kb_eval._build_eval_payload(report: dict) -> dict`（含 `answer_correctness`、`pass_threshold`）

- [ ] **Step 1: Write the failing test**

```python
"""kb_eval 响应须含 answer_correctness 与 pass_threshold。"""

from src.api import kb_eval
from src.config.const import EVAL_PASS_THRESHOLD


def test_eval_payload_includes_correctness_and_threshold():
    payload = kb_eval._build_eval_payload(
        {
            "eval_date": "2026-10-14",
            "faithfulness": 0.9,
            "answer_relevancy": 0.9,
            "context_precision": 0.9,
            "context_recall": 0.9,
            "answer_correctness": 0.7,
            "overall_score": 0.86,
            "passed": True,
            "qa_count": 20,
        }
    )

    assert payload["answer_correctness"] == 0.7
    assert payload["pass_threshold"] == EVAL_PASS_THRESHOLD
```

- [ ] **Step 2: Run test to verify it fails**

Run: `POSTGRES_HOST=localhost pytest tests/api/test_kb_eval_payload.py -v`
Expected: FAIL with `AttributeError: ... '_build_eval_payload'`

- [ ] **Step 3: Write minimal implementation**

把 `kb_eval.py` 构造 `data={...}` 的内联 dict 抽成 `_build_eval_payload(report)`，并在其中补两项：

```python
from src.config.const import EVAL_PASS_THRESHOLD

            "answer_correctness": float(report["answer_correctness"])
            if report.get("answer_correctness")
            else None,
            "pass_threshold": EVAL_PASS_THRESHOLD,
```

前端 `deploy/nginx/html/index.html:1113`：

```javascript
    const overallCls = overall >= (data.pass_threshold ?? 0.7) ? 'pass' : 'fail';
```

- [ ] **Step 4: Run test to verify it passes**

Run: `POSTGRES_HOST=localhost pytest tests/api/test_kb_eval_payload.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/kb_eval.py deploy/nginx/html/index.html tests/api/test_kb_eval_payload.py
git commit -m "feat(api): kb_eval 下发 answer_correctness 与 pass_threshold；前端改读"
```

---

### Task 6: 出题换官方 `default_transforms` 全量

**Files:**
- Modify: `src/cli/eval_ragas_generate.py:368-400`
- Test: `tests/cli/test_generate_transforms.py`（新建）

**Interfaces:**
- Consumes: `ragas.testset.transforms.default.default_transforms(llm=, embedding_model=)`
- Produces: `eval_ragas_generate._build_transforms(generator, use_filter: bool) -> list | None`

- [ ] **Step 1: Write the failing test**

```python
"""出题 transforms 须为官方 default_transforms 全量（含 Themes / Cosine 两件）。"""


def test_build_transforms_contains_themes_and_cosine():
    from src.cli import eval_ragas_generate

    class _Gen:
        llm = object()
        embedding_model = object()

    steps = eval_ragas_generate._build_transforms(_Gen(), use_filter=False)
    names = [type(t).__name__ for t in steps]

    assert "ThemesExtractor" in names
    assert "CosineSimilarityBuilder" in names


def test_build_transforms_returns_none_when_use_filter():
    from src.cli import eval_ragas_generate

    assert eval_ragas_generate._build_transforms(object(), use_filter=True) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_generate_transforms.py -v`
Expected: FAIL with `AttributeError: ... '_build_transforms'`

- [ ] **Step 3: Write minimal implementation**

```python
def _build_transforms(generator, use_filter: bool) -> list | None:
    """出题 transforms：默认走官方 `default_transforms` 全量。

    原实现手搓 5 个 transforms，**缺 `ThemesExtractor` 与
    `CosineSimilarityBuilder`**，导致 `MultiHopAbstractQuerySynthesizer` 取不到
    聚类而被静默丢弃（design D8）。官方 `default_transforms()` 含全部 7 步。

    Args:
        generator: TestsetGenerator 实例（提供 llm / embedding_model）
        use_filter: True 时不设 transforms（交回 ragas 默认）

    Returns:
        transforms 列表；use_filter 为真时返回 None
    """
    if use_filter:
        return None

    from ragas.testset.transforms.default import default_transforms

    transforms = default_transforms(
        llm=generator.llm, embedding_model=generator.embedding_model
    )
    core_logging.log_event(
        Event.TRANSFORMS_SETUP, steps=[type(t).__name__ for t in transforms]
    )
    return transforms
```

原处内联段（`:368-400`）替换为 `transforms = _build_transforms(generator, use_filter)`。

- [ ] **Step 4: Run test to verify it passes**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_generate_transforms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/eval_ragas_generate.py tests/cli/test_generate_transforms.py
git commit -m "fix(eval): 出题改用官方 default_transforms 全量（恢复 abstract 出题器）"
```

---

### Task 7: `llm_context` 传入自足性约束

**Files:**
- Modify: `src/config/const.py`（新增常量）
- Modify: `src/cli/eval_ragas_generate.py:361`
- Test: `tests/cli/test_generate_llm_context.py`（新建）

**Interfaces:**
- Consumes: `const.RAGAS_SELF_CONTAINED_HINT`
- Produces: `eval_ragas_generate._make_generator(llm, embeddings) -> TestsetGenerator`

- [ ] **Step 1: Write the failing test**

```python
"""llm_context 必须传给 TestsetGenerator，常量须含公司/报告期要求。"""


def test_self_contained_hint_mentions_company_and_period():
    from src.config.const import RAGAS_SELF_CONTAINED_HINT

    assert "公司" in RAGAS_SELF_CONTAINED_HINT
    assert "报告期" in RAGAS_SELF_CONTAINED_HINT
    assert "company" in RAGAS_SELF_CONTAINED_HINT.lower()


def test_generator_receives_llm_context(monkeypatch):
    from src.config.const import RAGAS_SELF_CONTAINED_HINT

    captured = {}

    class _Gen:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("ragas.testset.synthesizers.generate.TestsetGenerator", _Gen)
    from src.cli import eval_ragas_generate

    eval_ragas_generate._make_generator(llm=object(), embeddings=object())

    assert captured["llm_context"] == RAGAS_SELF_CONTAINED_HINT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_generate_llm_context.py -v`
Expected: FAIL with `AttributeError: ... '_make_generator'`

- [ ] **Step 3: Write minimal implementation**

`src/config/const.py` 追加：

```python
# 出题自足性约束（design D8）：RAGAS 出题 prompt 对问题自足性零要求，官方唯一
# 软通道是 TestsetGenerator 的 llm_context（注入 prompt 的 "Additional Context"）。
# 中英各一句：英文对齐 RAGAS prompt 语境，中文对齐中文语料。
RAGAS_SELF_CONTAINED_HINT: str = (
    "Each question MUST be self-contained: name the company and the reporting "
    "period explicitly. Do NOT use context-dependent references such as "
    '"this reporting period" or "the company".\n'
    "每个问题必须自足：明确指出公司名称与报告期；禁止使用『本报告期』『该公司』"
    "等依赖原文的指代。"
)
```

`eval_ragas_generate.py` 新增并在原处调用：

```python
def _make_generator(llm, embeddings):
    """构造 TestsetGenerator，并带上自足性约束（design D8）。"""
    from ragas.testset.synthesizers.generate import TestsetGenerator

    from src.config.const import RAGAS_SELF_CONTAINED_HINT

    return TestsetGenerator(
        llm=llm, embedding_model=embeddings, llm_context=RAGAS_SELF_CONTAINED_HINT
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `POSTGRES_HOST=localhost pytest tests/cli/test_generate_llm_context.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/const.py src/cli/eval_ragas_generate.py tests/cli/test_generate_llm_context.py
git commit -m "feat(eval): 出题传 llm_context 约束题面自足（点名公司+报告期）"
```

---

### Task 8: registry 模型版图 + catalog 定价

**Files:**
- Modify: `src/infra/llm/provider/registry.yaml`（dashscope 段）
- Modify: `tests/infra/llm/provider/test_facade.py:12,20`
- Test: `tests/infra/llm/provider/test_registry_models.py`（新建）

**Interfaces:**
- Consumes: —
- Produces: dashscope 的 `main`/`classify`/`summary` = `qwen3.7-flash`、`judge` = `qwen3.8-flash`

- [ ] **Step 1: Write the failing test**

```python
"""dashscope 角色绑定：main 族用 qwen3.7-flash、judge 用 qwen3.8-flash（design D10）。"""

from src.infra.llm.provider.spec import load_registry, resolve_role


def test_dashscope_roles(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    reg = load_registry()
    for role in ("main", "classify", "summary"):
        assert resolve_role(role, reg).model == "qwen3.7-flash"
    assert resolve_role("judge", reg).model == "qwen3.8-flash"


def test_qwen_flash_models_have_pricing(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    catalog = load_registry()["dashscope"].catalog
    assert catalog["qwen3.7-flash"].pricing is not None
    assert catalog["qwen3.8-flash"].pricing is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/provider/test_registry_models.py -v`
Expected: FAIL（main 仍 `qwen3.8-flash`、judge 仍 `kimi-k3`；catalog 无 `qwen3.7-flash`）

- [ ] **Step 3: Write minimal implementation**

`registry.yaml` 的 dashscope `models` 段改为：

```yaml
      main: qwen3.7-flash
      classify: qwen3.7-flash
      summary: qwen3.7-flash
      judge: qwen3.8-flash
```

`catalog` 补两条（**数字须按实施当日百炼官方价目表填**，下列为占位示例）：

```yaml
      qwen3.7-flash:
        context_window: 262144
        pricing: { input: 0.0000003, output: 0.0000012 }  # 每 token；以百炼价目表为准
      qwen3.8-flash:
        context_window: 262144
        pricing: { input: 0.0000006, output: 0.0000024 }  # 每 token；以百炼价目表为准
```

同步 `tests/infra/llm/provider/test_facade.py`：`:12` 断言（judge 默认）改 `"qwen3.8-flash"`；`:20` 断言（main 默认）改 `"qwen3.7-flash"`。

- [ ] **Step 4: Run test to verify it passes**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/provider/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/registry.yaml tests/infra/llm/provider/
git commit -m "chore(llm): main 族降 qwen3.7-flash、judge 用 qwen3.8-flash；补定价"
```

---

### Task 9: 活体验收（非 TDD）

**Files:** 无代码改动（只跑与核对；记录写回本计划「验收记录」）

- [ ] **Step 1: 静态闸门**

Run:
```bash
POSTGRES_HOST=localhost pytest tests/ -q
ruff check .
pyright src/
openspec validate ragas-modern-stack --strict
```
Expected: 全绿；`pyright` 不新增 error。注意：worktree 缺 gitignored `data/test_docs/` → `tests/parsers` 的 `FileNotFoundError` 属**环境性**，不是回归。

- [ ] **Step 2: 迁移对照（一次性，不入库）**

拿 `data/ragas/reports/*.csv` 里的**已采集数据**（同题、同 answer/contexts），分别按 legacy 与 modern 口径逐指标对照，差异记入「验收记录」。

- [ ] **Step 3: 1 题冒烟**

Run:
```bash
POSTGRES_HOST=localhost LANGFUSE_HOST=http://localhost:3000 \
  python -m src.cli.eval_ragas --kb-id <kb> --testset-version <v>
```
Expected: **无 `DeprecationWarning`**；stdout **恰五行**且均为真值（非 nan）；CSV 含 `error` 列；MD **无**「两口径分叉」Note。
另核：`git diff --name-only dev-wsl` **不含**检索栈三处（`src/rag/retrieval.py` / `src/infra/db/vector_store/` / `src/agents/tools/rag_tools.py`）——即「评估检索镜像生产」这一条是靠**不改**满足的。

- [ ] **Step 4: 出题验收**

Run:
```bash
POSTGRES_HOST=localhost python -m src.cli.eval_ragas --kb-id <kb> --generate --size 20
```
Expected: 日志**无** `Skipping multi_hop_abstract_query_synthesizer`；生成 JSON **含** `multi_hop_abstract` 样本（≥1）；题面**含**公司名/报告期。

- [ ] **Step 5: 提交验收记录**

```bash
git add docs/openspec/changes/ragas-modern-stack/ docs/superpowers/plans/2026-10-14-ragas-modern-stack.md
git commit -m "docs(openspec): ragas-modern-stack 活体验收记录"
```

---

## 验收记录

**日期**：2026-10-14 · **分支**：`feat/ragas-modern-stack`
**模型版图**：`judge=qwen3.8-flash` · `main`/`classify`/`summary=qwen3.7-flash` · `embedding=qwen3.7-text-embedding-flash`

| 项 | 结果 |
|---|---|
| 静态闸门 | `openspec validate --strict` valid；`check_docs` 21 篇 0 error；`check_adr` 23 条 0 error；`ruff check .` 1 error = `src/infra/db/engine.py:33 UP047`，**既存**（主仓 dev-wsl 同样报，且该文件不在本分支 diff 内） |
| 迁移对照 | 同题三配置（legacy+qwen3.8-max-0902 / legacy+kimi-k3 / **modern+qwen3.8-flash**）五项**最大绝对差 0.0023**，全为真值 |
| 1 题冒烟 | ✅ 2m18s；stdout **恰五行**全真值；**无 `DeprecationWarning`**；CSV 含 `error` 列；MD **无**「两口径分叉」Note；均值行五项 |
| 出题验收 | ✅ **通过**（v4，见下）：abstract 恢复（7/7/7）；题面自足 **21/21（100%）** |

冒烟五行：`faithfulness=1.0000 · answer_relevancy=0.9357 · context_precision=0.9167 · context_recall=1.0000 · answer_correctness=0.7472`

### 出题验收结果（`--generate --size 20`，29m28s，产出 v3 / 21 条）

**① abstract 出题器：已恢复 ✅**

```
出题器分布：single_hop_specific 7 · multi_hop_abstract 7 · multi_hop_specific 7
```

整改前实测为 **0 条 abstract**（`MultiHopAbstractQuerySynthesizer` 因 `Cannot form clusters` 被静默丢弃）。真实 KG 里 `summary_similarity` 关系 **9 条**、`entities_overlap` 68 条，每节点含 `themes` —— 正是 abstract 找簇所缺的那一种关系。

**② 题面自足性：未达成 ❌**

粗筛（题面是否含已知公司名：东软 / 腾讯 / 灿坤）：**13 / 21 条不含公司名**。例：

- `某上市公司2025年3月31日净利润及归母净利润下降原因`
- `上市公司定期财务报告：营业收入增长1.06%，归属净利润为何因营业成本增加而下滑？`

⇒ **`llm_context` 是软引导，不足以把"题面点名公司"变成硬保证**（与 design D8 的既有判断一致：官方无问题级硬校验）。

### 出题验收结果（P1 修复后重出，2026-10-15，`--generate --size 20`，21m14s，产出 **v4** / 21 条）

**判据（对应 spec scenario）逐条核验：**

| 判据 | 结果 |
|---|---|
| ① 日志无 `Skipping multi_hop_abstract_query_synthesizer` | ✅ 无（`CosineSimilarityBuilder` / `ThemesExtractor` 均执行） |
| ② JSON 含 `multi_hop_abstract` ≥1 | ✅ **7 条**（分布 7 / 7 / 7） |
| ③ 题面 **100% 命中** KB 内公司名 | ✅ **21/21（0 条缺失）** |
| ④ `reference_contexts` 含【公司·报告期】锚点头 | ✅ **21/21**（多跳样本在 `<n-hop>` 标签后紧跟头部） |

**关键对照（P1 前后）：**

| | v3（P1 前） | v4（P1 后） |
|---|---|---|
| 题面不含公司名 | 12–13 / 21 | **0 / 21** |
| 禁用指代「本报告期」「该公司」 | 出现（如「本公司 2024…」） | **0** |

**结论**：**P1（源头锚定）单独即达成硬判据**——把实体前置进出题上下文后，题面自足率从 ~40% 提到 **100%**，印证了「自足与否取决于上下文是否携带实体」这一根因判断。

**残留观察（非阻断）**：
- 腾讯文档的锚点头为 `【tencent · 2024】`（源 `document_metadata.company` 即小写英文、无 `report_period` 故回落到 `year`）——自足但不够规范；如需统一，属**上游元数据**质量问题，不在本 change。
- `#21` 上下文跨「腾讯 + 东软」两家公司、题面**同时点名两家**（自足）；`#20` 出现非常规实体串 `TencentGlobal`（疑似语料/抽取产物），题面仍含 `Tencent`。


**③ 附带发现**：`--generate --size 20` 在此环境下需 **29 分钟**（KG transforms 约 12 分钟 + 场景/样本生成约 12 分钟），且**此前曾无超时无限挂起**（已由修复波给 `get_judge_llm` 补 `RAGAS_JUDGE_TIMEOUT`）。

## 未决

- `EVAL_PASS_THRESHOLD` 最终值**待标定**（新口径实测区间 0.68~0.92，n=7）——Task 4 先沿用 `0.70`。
- 出题环节的 `judge` 与评估**共用**同一角色（`get_judge_llm`）——本计划不改这一事实，仅随 Task 8 一并切到 `qwen3.8-flash`。

---

## 追加修复波（2026-10-15）：题面自足（D8 升级）

**背景**：Task 6/7 落实后，v3 出题的 abstract 已恢复，但**题面自足未达成**（12/21 题面不含公司名）——spec「Generated questions are self-contained」scenario 未满足。经查根因：自足与否取决于**上下文是否带公司名**（12 条不自足样本的 `reference_contexts` 全无公司名），而实体本就在同节点 `document_metadata`（`company`/`report_period`），只是没进 prompt；官方无改写步、`llm_context` 只是软引导。⇒ 升级为 **P1（源头锚定）+ P2（出题后门）**。

### Task 10: P1 —— 出题上下文注入实体锚点

**Files**：`src/cli/eval_ragas_generate.py`（+ 测试）

- [ ] **Step 1: 写失败测试**
  - 测 `_context_header(meta)`：给含 `company`/`report_period` 的 meta → 返回 `【公司 · 报告期】\n`；缺 `report_period` 时用 `year`+`quarter` 兜底；缺 `company` 时用 `source` 兜底；全缺 → 返回空串。
  - 测「组装 chunk 时 `page_content` 以头部开头」。
  - Run: `POSTGRES_HOST=localhost pytest tests/cli/test_eval_ragas_generate.py -q` → 预期 FAIL。
- [ ] **Step 2: 实现**
  - 新增 `_context_header(meta: dict) -> str`；在 `run_generate` 的 chunk→`LCDocument` 组装处，`page_content = header + safe_content`。
  - **不得**改 `document_metadata` 本身，**不得**动生产索引 / 检索栈。
- [x] **Step 3: 跑通 + 提交**
  - Run: `POSTGRES_HOST=localhost pytest tests/ -q && ruff check .`
  - 提交：`feat(eval): 出题上下文前置【公司·报告期】锚点（P1）`（`f326383f`）

### Task 11: P2 —— 出题后自足性门

- [x] **设计**：「自足」= 题面点名其上下文公司；判据来源 = `reference_contexts` 的【】锚点头 + KG 文档专属别名；唯一公司 → 保义改写，多公司/无锚点 → 丢弃。
- [x] **实现**：新增纯函数模块 `src/cli/eval_self_contained.py`（`header_entities` / `doc_exclusive_aliases` / `enforce_self_contained`），**无额外 LLM 调用**；`eval_ragas_generate.py` 接线（`_build_company_aliases(kg)`）+ 事件 `testset self-contained dropped`。
- [x] **测试**：`tests/cli/test_self_contained_gate.py` 9 条（命名/跨语言别名→保留；多公司无命名→丢弃；单公司无命名→改写后保留；无锚点→丢弃；KG 适配层）。
- [x] **v4 回放**：`kept=21 dropped=0`（零误伤）。
- [x] **端到端实跑（v5）**：11m42s（KG 缓存命中、跳过 transforms）→ v5 / 21 条；P2 在真实 `run_generate` 中执行**无异常**，丢弃 **0** 条（与回放一致）；题面自足 21/21、锚点头 21/21、分布 7/7/7。
- [x] 提交：`feat(eval): 出题后自足性门（P2）`

### Task 12: 出题验收（须重出）

- [x] **先删 KG 缓存**：旧 KG 已备份为 `data/ragas/kg_<kb>.json.bak`（否则 transforms 跳过、P1 不生效）。
- [x] Run: `POSTGRES_HOST=localhost python -m src.cli.eval_ragas --kb-id 4a1dcb… --generate --size 20` → 21m14s，产出 **v4 / 21 条**。
- [x] **判据**（对应 spec scenario）：① 无 `Skipping multi_hop_abstract_query_synthesizer` ✅；② `multi_hop_abstract` = 7 ✅；③ 题面 **21/21 命中** ✅；④ `reference_contexts` **21/21** 含【公司·报告期】✅。
- [x] 结果写入本文件「验收记录」（见上）。

---

## 追加修复波 2（2026-10-15）：裁判截断与结构失败（D6 修订）

**现象**：v5 首次活体评测（21 题全量被中断前，只跑 1 题 / 5 题）暴露裁判大面积失败。

**排查链**：

| 阶段 | 配置 | 5 题结果 |
|---|---|---|
| 初始 | 思考 ON + `max_tokens` **1024**（RAGAS 默认） | 2/5 行失败，`IncompleteOutputException`（**截断**）；13m33s |
| 试修 | 思考 **OFF** + 4096 | 2/5 行失败，转为 `InstructorRetryException`（**schema 形状错误**）；3m37s |
| 定稿 | **思考 ON + 8192** | **0/5 行失败** ✅；12m01s |

**关键证据**：
- `qwen3.8-flash` **默认开思考**（探测：一句「只回复 OK」→ `reasoning_tokens=51` + `reasoning_content`）；显式 `extra_body={"enable_thinking": False}` 可关。
- RAGAS 内建 `max_tokens=1024`（`ragas/llms/base.py:726`）→ 长上下文截断。
- 关思考后截断消失但**形状错误**出现（`statements.0` 非对象 / 缺 `statement`）；RAGAS 对 openai 兼容端**硬编码 instructor `Mode.JSON`**，而 `Mode.TOOLS` 在思考 ON 下被百炼 **400 拒**（`tool_choice` 不支持 thinking mode）⇒ 换模式此路不通。
- 合成 schema 对照（20 条陈述）复现不出失败 ⇒ 失败源于**特定真实输入**，非通用模式问题。

**改动**：
- `src/config/settings.py`：新增 `RAGAS_JUDGE_MAX_TOKENS`（默认 **8192**）。
- `src/infra/llm/provider/factory.py`：`build_ragas_judge` 传 `max_tokens` + `extra_body=thinking_extra_body(provider, True)`（保留思考）。
- `src/cli/eval_ragas.py`：`error` 字段补短消息（`metric:ExcType:msg`），提升可诊断性（D2）。
- 测试：`test_ragas_modern_factories.py` 增断言（`max_tokens>=8192`、`enable_thinking is True`）。

**已知取舍**：思考 ON 使 21 题全量预计 **~25–50 分钟**（离线评测可接受）。若要压成本，正路是调**思考预算**（`thinking_budget`）而非直接关思考。
