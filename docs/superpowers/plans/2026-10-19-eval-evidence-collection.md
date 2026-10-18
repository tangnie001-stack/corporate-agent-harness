# Eval Evidence Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `eval_deepeval` 上增加 opt-in 的逐题证据采集与确定性根因归因：用运行期候选链、gold chunk、裁判/行为分和 trace，回答“事实在哪一层丢失”。

**Architecture:** `EvidenceRun` 持有每题的 `QuestionEvidence`；生成阶段用 `current_evidence` ContextVar 采集 dense/lexical/RRF/rerank/dedup/top-k/工具链，评分阶段用显式 `evidence_run` 把 `HookEvidence.measures` 归到对应题；评分后按低分规则选题，做 chunk 探针、事实阶段归因，写本地 `data/deepeval/evidence/<run_id>/`。默认关闭，旁路失败不影响评分。

**Tech Stack:** Python 3.11+ / httpx / DeepEval 4.2.9 / pytest / pandas

**Spec:**
- `docs/openspec/changes/eval-evidence-collection/proposal.md`
- `docs/openspec/changes/eval-evidence-collection/design.md`
- `docs/openspec/changes/eval-evidence-collection/specs/eval-evidence/spec.md`
- `docs/openspec/changes/eval-evidence-collection/tasks.md`

## Global Constraints

- 工作区：`/root/code/corporate_rag-evidence`（分支 `feat/eval-evidence-collection`）；所有 Bash 命令以 `cd /root/code/corporate_rag-evidence && ...` 开头。
- `.env` / `.venv` / `data` 是指向主工作区的 symlink；**禁止 `git add -A`**，只 add 本任务列出的文件。
- 宿主侧测试统一前置 `POSTGRES_HOST=localhost`：`POSTGRES_HOST=localhost .venv/bin/python -m pytest ...`。
- 不改变任何评分、主分、门禁、CSV、`eval_report` 或 Langfuse。
- 证据只写本地 `data/deepeval/evidence/<run_id>/`，目录必须 gitignored；不记录 prompt/response 原文，完整 reason 只进本地证据。
- `current_evidence` 默认 `None`；关闭时检索/工具路径只多一次 `get() is None`。
- 任何证据/探针/归因/写盘异常只 `logger.warning`，不得中断评测或改变退出码。
- `fact_in_* is None` 必须先短路 `inconclusive`；只有 `is False` 才进入事实阶段。
- `faithfulness` v1 不接入 gold 事实链，只判 `judge_failure` / 空回答，其余 `inconclusive`。
- `sample_class=web` 不得做 KB 链归因；`judge_failed` 优先于 web→inconclusive；web fallback 不判 `tool_routing`。
- 低分选择使用**掩码前带 `sample_class` 的逐题 rows**，不用 `masked_df`；web 主分不作低分触发。
- `source_chunk_ids` 使用 `{doc_id}:{chunk_index}` 空间；`match_scope="exact"` 才 high，`group`/`partial` 最高 medium。
- `unit_missing` 只作信号，不单独触发 `testset_error`。
- 证据写盘必须在 `check_gate` 之前；`--gate` 的 `sys.exit` 不丢证据。

---

## File Structure

| 文件 | 责任 |
|---|---|
| `src/core/eval_evidence.py` | `EvidenceRun` / `QuestionEvidence` / `current_evidence` / `evidence_scope` / `parent_key` / 常量；不 import rag/infra |
| `src/cli/eval_evidence_probe.py` | token 归一化、gold chunk 探针、source_chunk_ids 校验、testset_conflict |
| `src/cli/eval_evidence_attribution.py` | 事实阶段 helper、metric-scoped 规则、summary 计数 |
| `src/cli/eval_evidence_report.py` | 选题、组装证据 JSON、原子写、summary 写盘 |
| `src/cli/eval_deepeval.py` | CLI 开关、EvidenceRun 生命周期、score_rows/run_evaluation 接线、写盘位置 |
| `src/infra/db/vector_store/types.py` | `ChunkResult.rrf_score` |
| `src/rag/fusion.py` | RRF 分数回填 |
| `src/rag/retrieval.py` | dense/lexical/RRF/rerank/dedup/top-k 证据 |
| `src/agents/tools/rag_tools.py` | `retrieve_kb` 工具/最终 contexts 证据；rerank timeout 分支证据 |
| `src/agents/skills/executor.py` | 子代理执行时清空 `current_evidence` |
| `tests/core/test_eval_evidence.py` | 模型/ContextVar/scope |
| `tests/agents/skills/test_fork_context_isolation.py` | 子代理不采集证据 |
| `tests/cli/test_eval_evidence_flags.py` | CLI 开关 |
| `tests/cli/test_eval_evidence_probe.py` | 归一化/探针/gold/conflict |
| `tests/cli/test_eval_evidence_attribution.py` | 归因规则/覆盖/Web/F |
| `tests/cli/test_eval_evidence_report.py` | 选题/写盘/summary/atomic |
| `tests/rag/test_retrieval_evidence.py` | 检索链证据 |
| `tests/rag/test_rrf_score.py` | RRF 分数 |

---

### Task 1: Evidence models, context scope, CLI flags, lifecycle, subagent isolation

**Files:**
- Create: `src/core/eval_evidence.py`
- Modify: `src/cli/eval_deepeval.py:121-201, 1582-1738`
- Modify: `src/agents/skills/executor.py:190-290`
- Test: `tests/core/test_eval_evidence.py`
- Test: `tests/cli/test_eval_evidence_flags.py`
- Test: `tests/agents/skills/test_fork_context_isolation.py`

**Interfaces:**
- Produces: `QuestionEvidence`, `EvidenceRun`, `current_evidence: ContextVar[QuestionEvidence | None]`, `evidence_scope(collector)`, `parent_key(doc_id, chunk_id, parent_content)`.
- Produces: `generate_answers_and_contexts_with_behaviour(..., evidence_run: EvidenceRun | None = None)`; `EvidenceRun.new_question(q_index, trace_id=None)`.

- [ ] **Step 1: Write core model test**

Create `tests/core/test_eval_evidence.py`:

```python
from __future__ import annotations

import pytest

from src.core.eval_evidence import (
    EvidenceRun,
    current_evidence,
    evidence_scope,
    parent_key,
)


def test_new_question_keeps_index_and_trace():
    run = EvidenceRun(run_id="r1", run_trace_id="eval_run_r1_abcd1234", enabled=True)
    collector = run.new_question(2, "eval_t2")
    assert collector.q_index == 2
    assert collector.trace_id == "eval_t2"
    assert run.collectors[2] is collector


def test_scope_restores_previous_collector():
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    first = run.new_question(0)
    second = run.new_question(1)
    token = current_evidence.set(first)
    try:
        with evidence_scope(second):
            assert current_evidence.get() is second
        assert current_evidence.get() is first
    finally:
        current_evidence.reset(token)


def test_scope_restores_on_exception():
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    collector = run.new_question(0)
    token = current_evidence.set(None)
    try:
        with pytest.raises(RuntimeError):
            with evidence_scope(collector):
                raise RuntimeError("boom")
        assert current_evidence.get() is None
    finally:
        current_evidence.reset(token)


def test_parent_key_falls_back_to_chunk_id():
    assert parent_key("d1", "d1:3", None) == ("d1", "d1:3")
    assert parent_key("d1", "d1:3", "") == ("d1", "d1:3")
    key = parent_key("d1", "d1:3", "parent text")
    assert key[0] == "d1" and key[1] != "d1:3"
```

- [ ] **Step 2: Run it to confirm failure**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/core/test_eval_evidence.py -v`

Expected: `ModuleNotFoundError: src.core.eval_evidence`.

- [ ] **Step 3: Implement `src/core/eval_evidence.py`**

```python
"""评测证据模型与每题的 ContextVar 作用域。"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterator

EVIDENCE_CANDIDATE_CAP = 50
EVIDENCE_GOLD_CAP = 20


def parent_key(
    doc_id: str, chunk_id: str, parent_content: str | None
) -> tuple[str, str]:
    """返回 dedup/fact 等价用的 parent 键。

    Args:
        doc_id: 文档 ID
        chunk_id: 分块 ID（parent_content 为空时的兜底）
        parent_content: 父块内容

    Returns:
        `(doc_id, md5(parent_content))`；parent_content 为空时为 `(doc_id, chunk_id)`
    """
    if not parent_content:
        return (doc_id, chunk_id)
    digest = hashlib.md5(parent_content.encode("utf-8")).hexdigest()
    return (doc_id, digest)


@dataclass(slots=True)
class QuestionEvidence:
    """一道题的全部证据。"""

    q_index: int
    trace_id: str | None = None
    answer: str = ""
    testset: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    retrieval: dict = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    trajectory: dict = field(default_factory=dict)
    judge: dict = field(default_factory=dict)
    trace: dict = field(default_factory=dict)
    behavior: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    attribution: dict = field(default_factory=dict)


@dataclass(slots=True)
class EvidenceRun:
    """一轮评测的证据容器。"""

    run_id: str
    run_trace_id: str | None
    enabled: bool
    collectors: list[QuestionEvidence | None] = field(default_factory=list)
    corpus_chunks: list[Any] | None = None
    captured_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def new_question(
        self, q_index: int, trace_id: str | None = None
    ) -> QuestionEvidence:
        """创建或复用某题的 collector。"""
        while len(self.collectors) <= q_index:
            self.collectors.append(None)
        collector = self.collectors[q_index]
        if collector is None:
            collector = QuestionEvidence(q_index=q_index, trace_id=trace_id)
            self.collectors[q_index] = collector
        else:
            collector.trace_id = trace_id
        return collector

    def collector(self, q_index: int) -> QuestionEvidence | None:
        """按题号取 collector；越界返回 None。"""
        if 0 <= q_index < len(self.collectors):
            return self.collectors[q_index]
        return None


current_evidence: ContextVar[QuestionEvidence | None] = ContextVar(
    "current_evidence", default=None
)


@contextmanager
def evidence_scope(collector: QuestionEvidence | None) -> Iterator[None]:
    """在生成/评分阶段设置当前题 collector，finally 恢复。"""
    token = current_evidence.set(collector)
    try:
        yield
    finally:
        current_evidence.reset(token)
```

- [ ] **Step 4: Add CLI flags and test**

In `parse_args()` after `--run-type`:

```python
    parser.add_argument(
        "--evidence",
        action="store_true",
        help="采集低分/异常题的证据到 data/deepeval/evidence/<run_id>/",
    )
    parser.add_argument(
        "--evidence-all",
        action="store_true",
        help="采集全部题证据（隐含 --evidence）",
    )
```

After `args = parser.parse_args()`:

```python
    if args.evidence_all:
        args.evidence = True
```

Create `tests/cli/test_eval_evidence_flags.py`:

```python
import sys

from src.cli.eval_deepeval import parse_args


def test_evidence_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["eval", "--kb-id", "kb", "--evidence"])
    args = parse_args()
    assert args.evidence is True
    assert args.evidence_all is False


def test_evidence_all_implies_evidence(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["eval", "--kb-id", "kb", "--evidence-all"])
    args = parse_args()
    assert args.evidence is True
    assert args.evidence_all is True
```

- [ ] **Step 5: Wire EvidenceRun into generation + corpus + samples**

In `main()` after `_limit_testset`:

```python
        evidence_run = None
        evidence_samples: list[dict] = []
        if args.evidence:
            from src.cli.testset_io import load_testset_samples
            from src.core.eval_evidence import EvidenceRun

            evidence_run = EvidenceRun(
                run_id=run_id, run_trace_id=run_trace_id, enabled=True
            )
            try:
                evidence_samples = load_testset_samples(
                    kb_id, version=resolved_version
                )[: len(questions)]
            except Exception as exc:  # noqa: BLE001  # 观测旁路
                logger.warning("[cli] evidence samples load failed err={}", exc)
            for i, sample in enumerate(evidence_samples):
                collector = evidence_run.new_question(i)
                collector.testset = {
                    "reference": sample.get("reference"),
                    "reference_contexts": sample.get("reference_contexts"),
                    "source_file": sample.get("source_file"),
                    "expected_tools": sample.get("expected_tools"),
                    "source_chunk_ids": sample.get("source_chunk_ids"),
                    "match_scope": sample.get("match_scope"),
                    "chunking_version": sample.get("chunking_version"),
                    "numeric_risk": sample.get("numeric_risk"),
                }
            for i, question in enumerate(questions):
                collector = evidence_run.new_question(i)
                collector.query = {
                    "original": question,
                    "tool_queries": [],
                    "rewritten": None,
                    "sub_queries": None,
                    "intent_route": None,
                    "history_len": None,
                }
```

In `_ensure_store_and_generate` after `chunks = await vector_store.get_all_chunks(kb_id)`:

```python
            if evidence_run is not None:
                evidence_run.corpus_chunks = chunks
```

Pass `evidence_run=evidence_run` to `generate_answers_and_contexts_with_behaviour`.

Change the signature:

```python
async def generate_answers_and_contexts_with_behaviour(
    graph: Any,
    kb_id: str,
    session_id: str,
    questions: list[str],
    ground_truth: list[str],
    behaviour_metrics: list,
    results_folder: str,
    evidence_run: Any | None = None,
) -> tuple:
```

Inside the `for i, _golden in enumerate(...)` loop, after `trace_id`:

```python
        collector = (
            evidence_run.new_question(i, trace_id)
            if evidence_run is not None
            else None
        )
```

Wrap `_collect_one_question` call:

```python
        with evidence_scope(collector):
            (
                answer, ctx_list, details, sample_class, trajectory
            ) = await _collect_one_question(...)
        if collector is not None:
            collector.answer = answer
            collector.trajectory = trajectory
```

`answer` 是 spec `answer_mentions_fact` 的唯一来源；`trajectory` 供补齐非 `retrieve_kb` 的顶层工具调用。两者必须写入 collector，不能只留在局部变量。

Import `evidence_scope` at the top of `eval_deepeval.py`.

- [ ] **Step 6: Isolate subagent evidence**

In `src/agents/skills/executor.py`, import:

```python
from src.core.eval_evidence import current_evidence
```

In `_run_fork`, immediately after `token_ctx = current_request_ctx.set(child_ctx)`:

```python
        token_evidence = current_evidence.set(None)
```

In the `finally` block, before/after `current_request_ctx.reset(token_ctx)`:

```python
            current_evidence.reset(token_evidence)
```

Extend `tests/agents/skills/test_fork_context_isolation.py`:

```python
from src.core.eval_evidence import EvidenceRun, current_evidence


class _EvidenceProbeSubAgent(_FakeSubAgent):
    def __init__(self):
        super().__init__()
        self.seen_evidence = "unset"

    async def astream_events(self, inputs, config=None, version="v2"):
        self.seen_evidence = current_evidence.get()
        async for event in super().astream_events(inputs, config, version):
            yield event


@pytest.mark.asyncio
async def test_fork_clears_and_restores_current_evidence(monkeypatch):
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token_ctx = current_request_ctx.set(parent)
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    collector = run.new_question(0)
    token_ev = current_evidence.set(collector)
    fake = _EvidenceProbeSubAgent()
    executor = SkillExecutor(main_llm=object())
    monkeypatch.setattr(executor, "_build_sub_agent", lambda *a, **k: fake)
    monkeypatch.setattr(executor, "_fork_tools", lambda *a, **k: [])
    record = SkillRecord(
        name="x", description="d", context=SkillContext.FORK, fork_body="b"
    )
    try:
        await executor.execute(
            record, "t", DelegateRun(delegate_id="d1", skill_name="x", ctx=parent.child())
        )
        assert fake.seen_evidence is None
        assert current_evidence.get() is collector
    finally:
        current_evidence.reset(token_ev)
        current_request_ctx.reset(token_ctx)
```

- [ ] **Step 7: Run tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/core/test_eval_evidence.py tests/cli/test_eval_evidence_flags.py tests/agents/skills/test_fork_context_isolation.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/core/eval_evidence.py src/cli/eval_deepeval.py src/agents/skills/executor.py tests/core/test_eval_evidence.py tests/cli/test_eval_evidence_flags.py tests/agents/skills/test_fork_context_isolation.py
git commit -m "feat(eval): add evidence models and per-question scope"
```

---

### Task 2: RRF score on `ChunkResult`

**Files:**
- Modify: `src/infra/db/vector_store/types.py`
- Modify: `src/rag/fusion.py`
- Test: `tests/rag/test_rrf_score.py`

**Interfaces:**
- Produces: `ChunkResult.rrf_score: float | None`；`rrf_fusion` / `rrf_fusion_multi` 回填。

- [ ] **Step 1: Write failing test**

Create `tests/rag/test_rrf_score.py`:

```python
from src.infra.db.vector_store.types import ChunkResult
from src.rag.fusion import rrf_fusion


def _chunk(cid: str) -> ChunkResult:
    return ChunkResult(id=cid, content=cid, metadata={"doc_id": "d1"})


def test_rrf_fusion_sets_score_and_keeps_ranks():
    dense = [_chunk("a"), _chunk("b")]
    sparse = [_chunk("b"), _chunk("c")]
    out = rrf_fusion(dense, sparse, k=60, top_n=10)
    by_id = {c.id: c for c in out}
    assert by_id["b"].rrf_score > by_id["a"].rrf_score
    assert by_id["a"].dense_rank == 0
    assert by_id["b"].dense_rank == 1
    assert by_id["b"].sparse_rank == 0
    assert by_id["c"].sparse_rank == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_rrf_score.py -v`

Expected: `AttributeError: 'ChunkResult' object has no attribute 'rrf_score'`.

- [ ] **Step 3: Add field and set score**

In `ChunkResult`, after `sparse_rank`:

```python
    rrf_score: float | None = None
    """RRF 融合得分；仅 RRF 输出有值，dense/lexical 单路为 None。"""
```

In `rrf_fusion`, replace the final return:

```python
    result: list[ChunkResult] = []
    for doc_id, score in ranked[:top_n]:
        item = merged[doc_id]
        item.rrf_score = score
        result.append(item)
    return result
```

In `rrf_fusion_multi`, apply the same `item.rrf_score = score` before append.

- [ ] **Step 4: Run tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_rrf_score.py tests/rag -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infra/db/vector_store/types.py src/rag/fusion.py tests/rag/test_rrf_score.py
git commit -m "feat(rag): carry rrf score on chunk results"
```

---

### Task 3: Retrieval chain evidence (search / rerank / dedup / top-k / timeout)

**Files:**
- Modify: `src/rag/retrieval.py:86-155, 194-280`
- Modify: `src/agents/tools/rag_tools.py:153-198`
- Test: `tests/rag/test_retrieval_evidence.py`

**Interfaces:**
- Consumes: `current_evidence`, `EVIDENCE_CANDIDATE_CAP`, `parent_key`（Task 1）
- Produces: `QuestionEvidence.retrieval["dense"|"lexical"|"rrf"|"rerank_before"|"rerank_after"|"dedup"|"topk"]`.
- Produces helper `candidate_dict(chunk, rank, score, score_kind, distance=None)`.

- [ ] **Step 1: Write failing test**

Create `tests/rag/test_retrieval_evidence.py`:

```python
from src.core.eval_evidence import EvidenceRun, current_evidence
from src.infra.db.vector_store.types import ChunkResult
from src.rag.retrieval import _evidence_candidate, _evidence_stage


def test_candidate_has_identity_and_score_kind():
    chunk = ChunkResult(
        id="d1:0",
        content="x",
        metadata={"doc_id": "d1", "source": "a.pdf", "page": 2},
        distance=0.2,
        dense_rank=0,
    )
    item = _evidence_candidate(
        chunk, rank=0, score=0.8, score_kind="cosine_similarity"
    )
    assert item["chunk_id"] == "d1:0"
    assert item["parent_key"] == ("d1", "d1:0")
    assert item["score_kind"] == "cosine_similarity"
    assert item["distance"] == 0.2


def test_stage_truncates_and_reports_total():
    chunks = [
        ChunkResult(id=f"d1:{i}", content="x", metadata={"doc_id": "d1"})
        for i in range(60)
    ]
    stage = _evidence_stage(chunks, "lexical", score_fn=lambda c: 1.0)
    assert stage["count"] == 50
    assert stage["total_count"] == 60
    assert stage["truncated"] is True
```

- [ ] **Step 2: Run to confirm failure**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_retrieval_evidence.py -v`

Expected: `ImportError: cannot import name '_evidence_candidate'`.

- [ ] **Step 3: Implement helpers and `search` evidence**

At top of `retrieval.py`:

```python
from src.core.eval_evidence import (
    EVIDENCE_CANDIDATE_CAP,
    current_evidence,
    parent_key,
)
```

Add helpers before `search`:

```python
def _evidence_candidate(
    chunk: ChunkResult,
    *,
    rank: int,
    score: float | None,
    score_kind: str,
) -> dict:
    """把 ChunkResult 转成证据候选（不保留正文）。"""
    meta = chunk.metadata or {}
    doc_id = str(meta.get("doc_id") or "")
    return {
        "chunk_id": chunk.id,
        "parent_key": parent_key(
            doc_id, chunk.id, str(meta.get("parent_content") or "")
        ),
        "doc_id": doc_id,
        "source": str(meta.get("source") or ""),
        "page": int(meta.get("page") or 0),
        "rank": rank,
        "score": score,
        "score_kind": score_kind,
        "distance": chunk.distance,
    }


def _evidence_stage(
    chunks: list[ChunkResult],
    score_kind: str,
    *,
    score_fn=None,
    failed: bool = False,
) -> dict:
    """构造单阶段证据；上限 EVIDENCE_CANDIDATE_CAP。"""
    total = len(chunks)
    kept = chunks[:EVIDENCE_CANDIDATE_CAP]
    items = []
    for rank, chunk in enumerate(kept):
        if score_fn is not None:
            score = score_fn(chunk)
        elif score_kind == "rrf":
            score = chunk.rrf_score
        elif score_kind == "lexical":
            score = chunk.lexical_score
        elif chunk.distance is not None:
            score = 1 - chunk.distance
        else:
            score = None
        items.append(
            _evidence_candidate(
                chunk, rank=rank, score=score, score_kind=score_kind
            )
        )
    return {
        "items": items,
        "count": len(kept),
        "total_count": total,
        "truncated": total > len(kept),
        "failed": failed,
    }
```

In `search`, after `dense_results` / `sparse_results` / `results` are computed and before `return results`:

```python
        collector = current_evidence.get()
        if collector is not None:
            collector.retrieval["dense"] = _evidence_stage(
                dense_results, "cosine_similarity", failed=dense_failed
            )
            collector.retrieval["lexical"] = _evidence_stage(
                sparse_results, "lexical", failed=sparse_failed
            )
            collector.retrieval["rrf"] = _evidence_stage(results, "rrf")
```

For the non-hybrid branch, after `results`:

```python
    collector = current_evidence.get()
    if collector is not None:
        collector.retrieval["dense"] = _evidence_stage(
            results, "cosine_similarity"
        )
        collector.retrieval["rrf"] = {
            "items": [],
            "count": 0,
            "total_count": 0,
            "truncated": False,
            "failed": False,
        }
```

- [ ] **Step 4: Add `rerank_results` evidence**

After `contexts` is built and before dedup, add:

```python
    collector = current_evidence.get()
    if collector is not None:
        collector.retrieval["rerank_before"] = _evidence_stage(results, "rrf")
        collector.retrieval["rerank_after"] = {
            "items": [
                _evidence_candidate(
                    results[item["index"]],
                    rank=rank,
                    score=float(item.get("relevance_score", 0)),
                    score_kind="fallback" if used_fallback else "rerank",
                )
                for rank, item in enumerate(reranked[:EVIDENCE_CANDIDATE_CAP])
            ],
            "count": min(len(reranked), EVIDENCE_CANDIDATE_CAP),
            "total_count": len(reranked),
            "truncated": len(reranked) > EVIDENCE_CANDIDATE_CAP,
            "failed": used_fallback,
            "reason": "rerank_failed" if used_fallback else None,
        }
```

Then replace the dedup/top-k tail with:

```python
    before_contexts = list(contexts)
    contexts = _dedup_by_parent(contexts)
    final_contexts = contexts[:TOP_K_RERANK]

    collector = current_evidence.get()
    if collector is not None:
        kept_ids = {c.chunk_id for c in contexts}
        dropped = [c.chunk_id for c in before_contexts if c.chunk_id not in kept_ids]
        collector.retrieval["dedup"] = {
            "dropped_chunk_ids": dropped,
            "kept_chunk_ids": [c.chunk_id for c in contexts],
        }
        final_ids = {c.chunk_id for c in final_contexts}
        collector.retrieval["topk"] = {
            "dropped_chunk_ids": [
                c.chunk_id for c in contexts if c.chunk_id not in final_ids
            ],
            "kept_chunk_ids": [c.chunk_id for c in final_contexts],
            "rank_before_topk": len(contexts),
            "rank_after_topk": len(final_contexts),
        }
    return final_contexts
```

This replaces the old `contexts = _dedup_by_parent(contexts)` / `return contexts[:TOP_K_RERANK]` lines; do not keep the earlier `before_keys` block (it is superseded by this sequence).

- [ ] **Step 5: Add timeout branch evidence**

In `rag_tools.py` `except TimeoutError` after `contexts` is built and deduped:

```python
            collector = current_evidence.get()
            if collector is not None:
                before_ids = [c.chunk_id for c in contexts_before_dedup]
                after_ids = [c.chunk_id for c in contexts]
                collector.retrieval["rerank"] = {
                    "failed": True,
                    "reason": "timeout",
                    "score_kind": "fallback",
                }
                collector.retrieval["dedup"] = {
                    "dropped_chunk_ids": [
                        cid for cid in before_ids if cid not in set(after_ids)
                    ],
                    "kept_chunk_ids": after_ids,
                }
```

Capture `contexts_before_dedup = list(contexts)` before `_dedup_by_parent`.

同时写 `collector.retrieval["rerank_after"]`（否则事实链读 `rerank_after` 会缺段）：

```python
                collector.retrieval["rerank_after"] = {
                    "items": [
                        _evidence_candidate(
                            chunk,
                            rank=i,
                            score=chunk.score,
                            score_kind="fallback",
                        )
                        for i, chunk in enumerate(
                            contexts_before_dedup[:EVIDENCE_CANDIDATE_CAP]
                        )
                    ],
                    "count": min(
                        len(contexts_before_dedup), EVIDENCE_CANDIDATE_CAP
                    ),
                    "total_count": len(contexts_before_dedup),
                    "truncated": len(contexts_before_dedup)
                    > EVIDENCE_CANDIDATE_CAP,
                    "failed": True,
                    "reason": "timeout",
                }
```

`_evidence_candidate` 接收的是 `ChunkResult`；超时分支若只有 `RAGContext`，就地构造同构 dict（`chunk_id` / `parent_key` / `doc_id` / `source` / `page` / `score` / `score_kind` / `rank` / `distance=None`），不要为凑类型重新检索。

- [ ] **Step 6: Run tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_rrf_score.py tests/rag/test_retrieval_evidence.py tests/rag -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/rag/retrieval.py src/agents/tools/rag_tools.py tests/rag/test_retrieval_evidence.py
git commit -m "feat(rag): record retrieval stage evidence"
```

---

### Task 4: `retrieve_kb` tool/query/final-context evidence

**Files:**
- Modify: `src/agents/tools/rag_tools.py:86-260`
- Test: `tests/rag/test_retrieve_kb_evidence.py`

**Interfaces:**
- Consumes: `current_evidence`（Task 1）
- Produces: `QuestionEvidence.query["tool_queries"]`、`QuestionEvidence.tool_calls[]`、`retrieval.final_contexts`.

- [ ] **Step 1: Write failing test**

Create `tests/rag/test_retrieve_kb_evidence.py`:

```python
from types import SimpleNamespace

import pytest

from src.core.eval_evidence import EvidenceRun, current_evidence
from src.rag.context import RAGContext


@pytest.mark.asyncio
async def test_retrieve_kb_records_call_and_global_order(monkeypatch):
    from src.agents.tools import rag_tools

    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    collector = run.new_question(0)
    token = current_evidence.set(collector)

    async def fake_search(query, kb_id, vector_store):
        return []

    def fake_rerank(query, results, reranker):
        return [
            RAGContext(
                content="c1",
                source="a.pdf",
                page=1,
                doc_id="d1",
                chunk_id="d1:0",
                score=0.9,
            )
        ]

    monkeypatch.setattr(rag_tools.retrieval, "search", fake_search)
    monkeypatch.setattr(rag_tools.retrieval, "rerank_results", fake_rerank)
    tools = rag_tools.make_rag_tools(
        vector_store=object(), reranker=object(), include_ask_user=False
    )
    retrieve = next(t for t in tools if t.name == "retrieve_kb")
    try:
        await retrieve.ainvoke({"query": "q", "top_k": 1})
        assert collector.query["tool_queries"] == [{"call_index": 1, "query": "q"}]
        assert collector.tool_calls[0]["result_count"] == 1
        assert collector.retrieval["final_contexts"][0]["chunk_id"] == "d1:0"
        assert collector.retrieval["final_contexts"][0]["order_in_prompt"] == 1
    finally:
        current_evidence.reset(token)
```

- [ ] **Step 2: Run to confirm failure**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_retrieve_kb_evidence.py -v`

Expected: `KeyError: 'tool_queries'` / `IndexError`.

- [ ] **Step 3: Implement evidence capture**

At top of `rag_tools.py`（只此一行，勿重复 import 同一模块）:

```python
from src.core.eval_evidence import current_evidence, parent_key
```

Inside `retrieve_kb`, after `start = time.monotonic()`:

```python
        collector = current_evidence.get()
        call_index = (
            len(collector.tool_calls) + 1 if collector is not None else 0
        )
```

After `contexts = contexts[:top_k]` and before `ctx.tool_contexts` extend:

```python
        if collector is not None:
            offset = len(ctx.tool_contexts) if ctx is not None else 0
            final_contexts = [
                {
                    "chunk_id": c.chunk_id,
                    "parent_key": parent_key(c.doc_id, c.chunk_id, c.parent_content),
                    "source": c.source,
                    "page": c.page,
                    "score": c.score,
                    "order_in_prompt": offset + i + 1,
                }
                for i, c in enumerate(contexts)
            ]
            collector.query.setdefault("tool_queries", []).append(
                {"call_index": call_index, "query": query}
            )
            collector.tool_calls.append(
                {
                    "name": "retrieve_kb",
                    "args": {"query": query, "top_k": top_k},
                    "result_count": len(results),
                    "context_count": len(contexts),
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "iteration": iteration,
                    "call_index": call_index,
                    "final_contexts": final_contexts,
                }
            )
            collector.retrieval.setdefault("final_contexts", []).extend(final_contexts)
```

Import `parent_key` from core. Note `iteration` is computed before this block; move the block after the `iteration` assignment.

- [ ] **Step 4: Run tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_retrieve_kb_evidence.py tests/rag -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/tools/rag_tools.py tests/rag/test_retrieve_kb_evidence.py
git commit -m "feat(rag): record retrieve_kb evidence"
```

---

### Task 5: Normalization, gold chunk probe, source ids, testset conflict

**Files:**
- Create: `src/cli/eval_evidence_probe.py`
- Test: `tests/cli/test_eval_evidence_probe.py`

**Interfaces:**
- Produces: `normalize_number_token(text) -> str | None`、`extract_fact_tokens(reference) -> list[str]`、`normalize_text(text) -> str`、`probe_reference_chunks(reference, chunks, cap=20) -> dict`、`resolve_gold(sample, chunks, reference) -> dict`、`compute_testset_conflict(reference, probe) -> dict`。

- [ ] **Step 1: Write failing tests**

Create `tests/cli/test_eval_evidence_probe.py`:

```python
from src.cli.eval_evidence_probe import (
    extract_fact_tokens,
    normalize_number_token,
    probe_reference_chunks,
    resolve_gold,
)
from src.infra.db.vector_store.types import ChunkResult


def _chunk(cid: str, content: str, parent: str | None = None) -> ChunkResult:
    meta = {"doc_id": "d1", "source": "a.pdf", "page": 1}
    if parent is not None:
        meta["parent_content"] = parent
    return ChunkResult(id=cid, content=content, metadata=meta)


def test_number_normalization():
    assert normalize_number_token("-12,090,412元") == "-12090412元"
    assert normalize_number_token("８０，３４８") == "80348"
    assert normalize_number_token("330.18 %") == "330.18%"


def test_extract_fact_tokens_dedupes():
    assert extract_fact_tokens("A 80,348，B 80,348，C 42%") == ["80348", "42%"]


def test_probe_hits_content_and_parent():
    chunks = [
        _chunk("d1:0", "noise"),
        _chunk("d1:1", "非控制性权益 80,348", parent="表头：单位千元；80,348"),
    ]
    result = probe_reference_chunks("非控制性权益 80,348", chunks)
    assert result["fact_in_corpus"] is True
    assert "d1:1" in result["gold_chunk_ids"]


def test_probe_partial_coverage():
    chunks = [_chunk("d1:0", "只有 80,348")]
    result = probe_reference_chunks("80,348 与 42%", chunks)
    assert result["fact_in_corpus"] is True
    assert result["fact_coverage"]["corpus"] < 1.0
    assert result["missing_fact_tokens"] == ["42%"]


def test_probe_no_tokens_is_null():
    result = probe_reference_chunks("没有数值", [])
    assert result["fact_in_corpus"] is None
    assert result["fact_coverage"]["corpus"] is None


def test_source_chunk_ids_exact_and_group():
    chunks = [_chunk("d1:0", "x"), _chunk("d1:1", "y")]
    exact = resolve_gold(
        {"source_chunk_ids": ["d1:0"], "match_scope": "exact"}, chunks, "x"
    )
    group = resolve_gold(
        {"source_chunk_ids": ["d1:0", "d1:1"], "match_scope": "group"}, chunks, "x"
    )
    assert exact["gold_chunk_confidence"] == "high"
    assert group["gold_chunk_confidence"] == "medium"


def test_stale_source_ids_degrade():
    chunks = [_chunk("d1:0", "x")]
    out = resolve_gold(
        {"source_chunk_ids": ["d1:9"], "match_scope": "exact"}, chunks, "x"
    )
    assert out["gold_chunk_source"] in ("probe", None)
    assert out["gold_chunk_confidence"] != "high"
```

- [ ] **Step 2: Run to confirm failure**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_evidence_probe.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement probe module**

Create `src/cli/eval_evidence_probe.py` with:

```python
"""证据探针：token 归一化、gold chunk 定位、testset conflict。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from loguru import logger

from src.core.eval_evidence import EVIDENCE_GOLD_CAP, parent_key
from src.infra.db.vector_store.types import ChunkResult

_NUMBER_RE = re.compile(r"[-−－]?\d[\d,，]*(?:\.\d+)?\s*(?:%|万元|亿元|元|百分点)?")
_UNITS = ("万元", "亿元", "百分点", "%", "元")


def normalize_text(text: str) -> str:
    """去 `【...】` 头、压缩空白、转小写。"""
    text = re.sub(r"^【[^】]*】\s*", "", text.strip())
    return re.sub(r"\s+", " ", text).lower()


def normalize_number_token(text: str) -> str | None:
    """全角转半角、去千分位/空白、统一负号；无数值返回 None。"""
    token = unicodedata.normalize("NFKC", text).strip()
    token = token.replace("−", "-").replace("－", "-")
    match = _NUMBER_RE.search(token)
    if match is None:
        return None
    raw = match.group(0).replace(",", "").replace("，", "")
    raw = re.sub(r"\s+", "", raw)
    return raw


def extract_fact_tokens(reference: str) -> list[str]:
    """抽取并去重参考数值 token（保留首次顺序）。"""
    out: list[str] = []
    for match in _NUMBER_RE.finditer(unicodedata.normalize("NFKC", reference)):
        token = normalize_number_token(match.group(0))
        if token and token not in out:
            out.append(token)
    return out


def answer_mentions_fact(answer: str, tokens: list[str]) -> bool | None:
    """回答是否包含 gold 的任一数值 token；无 token 时返回 None。

    Args:
        answer: 被测系统回答原文
        tokens: `extract_fact_tokens` 产物

    Returns:
        True/False；tokens 为空时为 None（无法判定）
    """
    if not tokens:
        return None
    text = normalize_text(answer)
    return any(token in text for token in tokens)


def _chunk_text(chunk: ChunkResult) -> str:
    meta = chunk.metadata or {}
    return normalize_text(f"{chunk.content}\n{meta.get('parent_content') or ''}")


def probe_reference_chunks(
    reference: str,
    chunks: list[ChunkResult],
    *,
    cap: int = EVIDENCE_GOLD_CAP,
) -> dict:
    """在 content/parent_content 中匹配参考数值/文本。"""
    tokens = extract_fact_tokens(reference)
    if not tokens:
        return {
            "gold_chunk_ids": [],
            "gold_parent_keys": [],
            "fact_in_corpus": None,
            "fact_coverage": {"corpus": None},
            "fact_hit_tokens": {"corpus": []},
            "missing_fact_tokens": [],
            "probe_tokens": [],
            "truncated": False,
        }
    hits: dict[str, list[str]] = {token: [] for token in tokens}
    matched: list[ChunkResult] = []
    for chunk in chunks:
        text = _chunk_text(chunk)
        chunk_hit = False
        for token in tokens:
            if token in text:
                hits[token].append(chunk.id)
                chunk_hit = True
        if chunk_hit:
            matched.append(chunk)
            if len(matched) >= cap:
                break
    hit_tokens = [token for token in tokens if hits[token]]
    by_id = {c.id: c for c in chunks}
    return {
        "gold_chunk_ids": [c.id for c in matched],
        "gold_parent_keys": [
            parent_key(
                str((c.metadata or {}).get("doc_id") or ""),
                c.id,
                str((c.metadata or {}).get("parent_content") or ""),
            )
            for c in matched
        ],
        "fact_in_corpus": bool(hit_tokens),
        "fact_coverage": {"corpus": len(hit_tokens) / len(tokens)},
        "fact_hit_tokens": {"corpus": hit_tokens},
        "token_hits": hits,
        "token_parent_keys": {
            token: [
                parent_key(
                    str((by_id[cid].metadata or {}).get("doc_id") or ""),
                    cid,
                    str((by_id[cid].metadata or {}).get("parent_content") or ""),
                )
                for cid in ids
            ]
            for token, ids in hits.items()
        },
        "missing_fact_tokens": [t for t in tokens if t not in hit_tokens],
        "probe_tokens": tokens,
        "truncated": len(matched) >= cap,
    }


def resolve_gold(
    sample: dict, chunks: list[ChunkResult], reference: str
) -> dict:
    """优先使用 source_chunk_ids；失败/组级降级。"""
    source_ids = sample.get("source_chunk_ids") or []
    scope = sample.get("match_scope")
    by_id = {c.id: c for c in chunks}
    valid: list[ChunkResult] = []
    for cid in source_ids:
        chunk = by_id.get(cid)
        if chunk is None:
            logger.warning("[cli] evidence source_chunk_id missing id={}", cid)
            continue
        expected_doc = str((chunk.metadata or {}).get("doc_id") or "")
        if str(cid).split(":", 1)[0] != expected_doc:
            logger.warning(
                "[cli] evidence source_chunk_id doc mismatch id={} doc={}",
                cid,
                expected_doc,
            )
            continue
        valid.append(chunk)
    if source_ids and len(valid) == len(source_ids):
        confidence = "high" if scope == "exact" else "medium"
        return {
            "gold_chunk_ids": [c.id for c in valid],
            "gold_parent_keys": [
                parent_key(
                    str((c.metadata or {}).get("doc_id") or ""),
                    c.id,
                    str((c.metadata or {}).get("parent_content") or ""),
                )
                for c in valid
            ],
            "gold_chunk_source": "testset",
            "gold_chunk_confidence": confidence,
        }
    probe = probe_reference_chunks(reference, chunks)
    if probe["gold_chunk_ids"]:
        return {
            **probe,
            "gold_chunk_source": "probe",
            "gold_chunk_confidence": "medium",
        }
    return {
        **probe,
        "gold_chunk_source": None,
        "gold_chunk_confidence": None,
    }


def compute_testset_conflict(
    reference: str, probe: dict, *, numeric_risk: bool = False
) -> dict:
    """数值存在性冲突与单位信号；period 恒 null。"""
    tokens = probe.get("probe_tokens") or []
    missing = probe.get("missing_fact_tokens") or []
    return {
        "numeric_conflict": (
            bool(tokens) and numeric_risk and not probe.get("fact_in_corpus")
        ),
        "unit_missing": bool(tokens) and all(
            not any(token.endswith(unit) for unit in _UNITS) for token in tokens
        ),
        "period_conflict": None,
        "missing_fact_tokens": missing,
    }
```

- [ ] **Step 4: Run tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_evidence_probe.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli/eval_evidence_probe.py tests/cli/test_eval_evidence_probe.py
git commit -m "feat(eval): add gold chunk probe and token normalization"
```

---

### Task 6: Deterministic attribution engine and summary counts

**Files:**
- Create: `src/cli/eval_evidence_attribution.py`
- Test: `tests/cli/test_eval_evidence_attribution.py`

**Interfaces:**
- Consumes: `QuestionEvidence.facts` / `judge` / `retrieval`（Task 4/5）
- Produces: `attribute_question(collector, *, sample_class, sample_failed) -> dict[str, dict]`、`summarize_attribution(collectors) -> dict`.

- [ ] **Step 1: Write failing tests**

Create `tests/cli/test_eval_evidence_attribution.py`:

```python
from src.cli.eval_evidence_attribution import attribute_question
from src.core.eval_evidence import EvidenceRun


def _collector(facts, judge=None, retrieval=None):
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    c = run.new_question(0)
    c.facts = facts
    c.judge = judge or {}
    c.retrieval = retrieval or {}
    return c


def test_q21_retrieval_miss():
    c = _collector(
        {
            "fact_in_corpus": True,
            "fact_in_candidates": False,
            "fact_in_reranked": False,
            "fact_in_final": False,
            "fact_coverage": {"corpus": 1.0, "candidates": 0.0, "reranked": 0.0, "final": 0.0},
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "retrieval_miss"
    assert out["context_precision"]["root_cause"] == "retrieval_miss"
    assert out["faithfulness"]["root_cause"] == "inconclusive"


def test_faithfulness_does_not_use_fact_chain():
    c = _collector(
        {
            "fact_in_corpus": False,
            "fact_in_final": False,
            "fact_coverage": {"corpus": 0.0, "final": 0.0},
            "metric_attempted": True,
            "judge_failed": False,
        },
        judge={"faithfulness": {"score": 0.2, "error": ""}},
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["faithfulness"]["root_cause"] == "inconclusive"


def test_null_fact_does_not_become_parsing():
    c = _collector(
        {
            "fact_in_corpus": None,
            "fact_in_candidates": None,
            "fact_in_final": None,
            "fact_coverage": {"corpus": None, "final": None},
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="web", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "inconclusive"


def test_empty_sample_is_not_judge_failure():
    c = _collector(
        {
            "metric_attempted": False,
            "skipped_reason": "sample_class=empty",
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="empty", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "inconclusive"
    assert "skipped" in out["context_recall"]["evidence"]


def test_web_judge_failure_wins():
    c = _collector(
        {
            "metric_attempted": True,
            "judge_failed": True,
            "fact_in_corpus": None,
        },
        judge={"context_recall": {"score": float("nan"), "error": "boom"}},
    )
    out = attribute_question(c, sample_class="web", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "judge_failure"


def test_web_tool_fallback_is_not_routing():
    c = _collector(
        {
            "metric_attempted": True,
            "judge_failed": False,
            "tool_called": ["search_web"],
            "tool_correctness_reason": "missing retrieve_kb",
        },
        judge={"tool_correctness": {"score": 0.0, "error": "", "reason": "missing retrieve_kb"}},
    )
    out = attribute_question(c, sample_class="web", sample_failed=False)
    assert out["tool_correctness"]["root_cause"] == "inconclusive"
```

- [ ] **Step 2: Implement attribution module**

Create `src/cli/eval_evidence_attribution.py`:

```python
"""确定性归因：事实阶段 + metric-scoped 规则。"""

from __future__ import annotations

from src.core.eval_evidence import QuestionEvidence

_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "contextual_relevancy",
    "non_advice",
    "tool_correctness",
)
_BEHAVIOR_ALIASES = {
    "Task Completion": "TaskCompletion",
    "Step Efficiency": "StepEfficiency",
    "Agent Loop Detection": "AgentLoopDetection",
}


def _fact_stage(facts: dict) -> tuple[str, str] | None:
    if facts.get("fact_in_corpus") is None:
        return ("inconclusive", "fact_in_corpus=null")
    if facts.get("fact_in_corpus") is False:
        return ("parsing_or_chunking", "fact_in_corpus=false")
    if facts.get("fact_in_candidates") is None:
        return ("inconclusive", "fact_in_candidates=null")
    if facts.get("fact_in_candidates") is False:
        return ("retrieval_miss", "fact_in_candidates=false")
    if facts.get("fact_in_reranked") is None:
        return ("inconclusive", "fact_in_reranked=null")
    if facts.get("fact_in_reranked") is False:
        return ("rerank_or_topk", "fact_in_reranked=false")
    if facts.get("fact_in_final") is None:
        return ("inconclusive", "fact_in_final=null")
    if facts.get("fact_in_final") is False:
        return ("rerank_or_topk", "fact_in_final=false")
    return None


def _result(root: str, evidence: str, confidence: str = "medium") -> dict:
    return {"root_cause": root, "confidence": confidence, "evidence": evidence}


def attribute_question(
    collector: QuestionEvidence, *, sample_class: str, sample_failed: bool
) -> dict[str, dict]:
    """按指标返回 attribution；不抛异常，调用方负责兜底。"""
    facts = collector.facts or {}
    judge = collector.judge or {}
    out: dict[str, dict] = {}
    attempted = bool(facts.get("metric_attempted", True))
    skipped = facts.get("skipped_reason")

    for metric in _METRICS:
        item = judge.get(metric) or {}
        if not attempted and metric != "tool_correctness":
            out[metric] = _result(
                "inconclusive", f"skipped:{skipped or sample_class}", "inconclusive"
            )
            continue
        if facts.get("judge_failed"):
            out[metric] = _result("judge_failure", "judge_failed=true", "high")
            continue
        if metric == "faithfulness":
            if sample_failed:
                out[metric] = _result("prompt_or_generation", "run failed", "low")
            elif item.get("score") is None:
                out[metric] = _result("inconclusive", "no score", "inconclusive")
            else:
                out[metric] = _result(
                    "inconclusive", "F 需要 claim 级证据，v1 不做", "inconclusive"
                )
            continue
        if metric == "answer_relevancy":
            if sample_failed:
                out[metric] = _result("prompt_or_generation", "run failed", "low")
            else:
                out[metric] = _result("inconclusive", "无 answer 级证据", "inconclusive")
            continue
        if metric == "tool_correctness":
            reason = facts.get("tool_correctness_reason") or ""
            if reason == "failed":
                out[metric] = _result("inconclusive", "run failed", "inconclusive")
            elif sample_class == "web" and reason:
                out[metric] = _result("inconclusive", "web fallback", "inconclusive")
            elif facts.get("tool_called") and (
                item.get("score") is not None and item["score"] < 1.0
            ):
                out[metric] = _result("tool_routing", "expected tool missing", "high")
            else:
                out[metric] = _result("inconclusive", "no routing signal", "inconclusive")
            continue
        if metric == "non_advice":
            if item.get("score") is not None and item["score"] < 1.0:
                out[metric] = _result("prompt_or_generation", "advice detected", "medium")
            else:
                out[metric] = _result("inconclusive", "no violation", "inconclusive")
            continue
        if facts.get("testset_conflict", {}).get("numeric_conflict"):
            out[metric] = _result("testset_error", "numeric_conflict=true", "medium")
            continue
        final_cov = facts.get("fact_coverage", {}).get("final")
        if final_cov is not None and 0 < final_cov < 1.0:
            out[metric] = _result(
                "inconclusive",
                f"partial_fact_coverage missing={facts.get('missing_fact_tokens')}",
                "inconclusive",
            )
            continue
        if (
            metric == "context_precision"
            and facts.get("fact_in_final") is True
            and item.get("score") is not None
            and float(item["score"]) < 1.0
        ):
            out[metric] = _result(
                "rerank_or_topk", "gold in final but precision<1", "medium"
            )
            continue
        stage = _fact_stage(facts)
        if stage is not None:
            root, evidence = stage
            out[metric] = _result(
                root, evidence, "high" if root == "testset_error" else "medium"
            )
            continue
        out[metric] = _result("inconclusive", "no rule", "inconclusive")

    for name, score in (collector.behavior.get("metrics") or {}).items():
        key = _BEHAVIOR_ALIASES.get(name, name)
        if sample_failed or score is None:
            out[key] = _result("prompt_or_generation", "run failed", "low")
        elif key in ("StepEfficiency", "AgentLoopDetection") and score < 0.5:
            out[key] = _result("agent_orchestration", f"score={score}", "medium")
        elif key == "TaskCompletion" and score < 0.5:
            out[key] = _result("inconclusive", f"score={score}", "inconclusive")
    return out


def summarize_attribution(collectors: list[QuestionEvidence | None]) -> dict:
    """汇总 root_cause_counts 与 scored/skipped/inconclusive。"""
    scored = skipped = inconclusive = 0
    by_metric: dict[str, dict[str, int]] = {}
    total: dict[str, int] = {}
    for collector in collectors:
        if collector is None:
            continue
        for metric, item in (collector.attribution or {}).items():
            root = item.get("root_cause")
            if root is None:
                continue
            scored += 1
            by_metric.setdefault(metric, {})
            by_metric[metric][root] = by_metric[metric].get(root, 0) + 1
            total[root] = total.get(root, 0) + 1
            if root == "inconclusive":
                inconclusive += 1
        skipped += sum(
            1
            for metric, item in (collector.judge or {}).items()
            if not item.get("metric_attempted", True)
        )
    return {
        "root_cause_counts": {"by_metric": by_metric, "total": total},
        "scored_metric_count": scored,
        "skipped_metric_count": skipped,
        "inconclusive_count": inconclusive,
    }
```

- [ ] **Step 3: Run tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_evidence_attribution.py -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cli/eval_evidence_attribution.py tests/cli/test_eval_evidence_attribution.py
git commit -m "feat(eval): add deterministic evidence attribution"
```

---

### Task 7: Selection, JSON writing, summary, and main wiring

**Files:**
- Create: `src/cli/eval_evidence_report.py`
- Modify: `src/cli/eval_deepeval.py:1755-1858`
- Test: `tests/cli/test_eval_evidence_report.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `EvidenceRun`（Task 1）、probe（Task 5）、attribution（Task 6）、`link_behavior_traces`（trace change）。
- Produces: `should_write_evidence(...)`、`write_evidence_run(...)`。

**Preflight rulings（binding，优先于下方代码块）:**

- `build_facts` 追加参数 `tool_correctness_reason: str`、`answer: str`、`tool_calls: list[dict]`；输出必须含 `tool_correctness_reason`、`tool_called`（工具名列表，顶层）、`call_count`、`answer_mentions_fact`（调 `eval_evidence_probe.answer_mentions_fact(answer, tokens)`）。
- `sample_class=web` 短路：`collector.retrieval["status"]="not_applicable"`，`fact_in_corpus/candidates/reranked/final=null`，`fact_coverage` 四阶段 null，不跑探针、不做阶段交集。
- `tokens=[]` 时 `fact_in_candidates/reranked/final` 写 `null`（不是 `False`），`fact_coverage` 对应 null。
- `sample_failed` 由 `row.get("error") == "failed"` 判定（`sample_class` 只有 `pure_kb`/`web`/`empty`）；failed 时 `metric_attempted=false`、`skipped_reason="failed"`；`should_write_evidence` 对 `row.get("error") == "failed"` 也返回 True。
- `_snapshot_judge` 同时深拷 `evidence.measures` 到 `collector.trace["judge_measures"]`；`write_evidence_run` 的 `trace` 段输出 `run_trace_id` / `sut_trace_id` / `judge_measures`（每条把 `calls` 改名为 `judge_calls`）/ `behavior: {"trace_id": ..., "link_method": ...}`。
- `QuestionEvidence` 增加 `trajectory: dict = field(default_factory=dict)`；Task 1 生成循环写入 `collector.trajectory = trajectory`。`write_evidence_run` 在写盘前把 trajectory 中非 `retrieve_kb` 的顶层工具调用补进 `collector.tool_calls`（`name` / `args` 来自轨迹，`result_count` / `latency_ms` / `iteration` 取不到写 null，`call_index` 按出现顺序续号）；`retrieve_kb` 已由 Task 4 记录，不得重复。

- [ ] **Step 1: Write failing test**

Create `tests/cli/test_eval_evidence_report.py`:

```python
import json

import pandas as pd

from src.cli.eval_evidence_report import should_write_evidence, write_evidence_run
from src.core.eval_evidence import EvidenceRun


def _df(**scores):
    return pd.DataFrame([{"q_index": 0, **scores}])


def test_low_score_selection():
    row = _df(faithfulness=0.5, answer_relevancy=0.9, context_precision=0.9, context_recall=0.9).iloc[0]
    behavior = [{
        "q_index": 0,
        "metrics": {"Task Completion": 1.0, "Step Efficiency": 1.0, "Agent Loop Detection": 1.0},
    }]
    assert should_write_evidence(row, "pure_kb", behavior, all_questions=False) is True


def test_web_main_score_does_not_trigger():
    row = _df(faithfulness=0.1, answer_relevancy=0.1, context_precision=0.1, context_recall=0.1).iloc[0]
    behavior = [{
        "q_index": 0,
        "metrics": {"Task Completion": 1.0, "Step Efficiency": 1.0, "Agent Loop Detection": 1.0},
    }]
    assert should_write_evidence(row, "web", behavior, all_questions=False) is False


def test_empty_always_selected():
    row = _df(faithfulness=None, answer_relevancy=None, context_precision=None, context_recall=None).iloc[0]
    assert should_write_evidence(row, "empty", [], all_questions=False) is True


def test_write_summary_even_without_selection(tmp_path, monkeypatch):
    run = EvidenceRun(run_id="r1", run_trace_id="eval_run_r1_abcd1234", enabled=True)
    run.new_question(0)
    monkeypatch.setattr(
        "src.cli.eval_evidence_report.EVAL_EVIDENCE_DIR", str(tmp_path)
    )
    write_evidence_run(
        run,
        questions=["Q0"],
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 1.0, "sample_class": "pure_kb"}]),
        sample_classes=["pure_kb"],
        behaviour_rows=[{
            "q_index": 0,
            "metrics": {"Task Completion": 1.0, "Step Efficiency": 1.0, "Agent Loop Detection": 1.0},
        }],
        trace_ids=["t0"],
        run_id="r1",
        run_trace_id="eval_run_r1_abcd1234",
        all_questions=False,
    )
    summary = json.loads((tmp_path / "r1" / "summary.json").read_text())
    assert summary["evidence_files"] == []
    assert summary["root_cause_counts"]["total"] == {}
```

- [ ] **Step 2: Implement report module**

Create `src/cli/eval_evidence_report.py` with:

```python
"""证据选题、组装与原子写盘。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from pandas import DataFrame, Series
from loguru import logger

from src.cli.eval_evidence_attribution import attribute_question, summarize_attribution
from src.cli.eval_evidence_probe import (
    answer_mentions_fact,
    compute_testset_conflict,
    probe_reference_chunks,
    resolve_gold,
)
from src.config import EVAL_REPORT_DIR
from src.config.const import GATE_THRESHOLDS, SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_WEB
from src.core.eval_evidence import EVIDENCE_GOLD_CAP, EvidenceRun, QuestionEvidence

EVAL_EVIDENCE_DIR = str(Path(EVAL_REPORT_DIR) / "evidence")
_BEHAVIOR_KEYS = ("Task Completion", "Step Efficiency", "Agent Loop Detection")


def _is_nan(value) -> bool:
    return value is None or value != value


def should_write_evidence(
    row: Series,
    sample_class: str,
    behaviour_rows: list[dict],
    *,
    all_questions: bool,
) -> bool:
    """低分/异常选题；web 主分不触发；无主分行时只看行为。"""
    if all_questions:
        return True
    if sample_class in (SAMPLE_CLASS_EMPTY, "failed"):
        return True
    if str(row.get("error") or "") == "failed":
        return True
    behavior = {r.get("q_index"): r for r in behaviour_rows}.get(
        int(row.get("q_index", 0)), {}
    )
    if any(
        _is_nan((behavior.get("metrics") or {}).get(key))
        or float((behavior.get("metrics") or {}).get(key)) < 0.5
        for key in _BEHAVIOR_KEYS
    ):
        return True
    if sample_class == SAMPLE_CLASS_WEB:
        return False
    if not any(metric in row.index for metric in GATE_THRESHOLDS):
        return False
    for metric, threshold in GATE_THRESHOLDS.items():
        value = row.get(metric)
        if _is_nan(value) or float(value) < threshold:
            return True
    tool_reason = str(row.get("tool_correctness_reason") or "")
    tool_value = row.get("tool_correctness")
    if tool_reason in ("failed", "metric_unavailable") or tool_reason.startswith(
        "tool_correctness:"
    ):
        return True
    if not _is_nan(tool_value) and float(tool_value) < 1.0:
        return True
    return False


def _stage_sets(collector: QuestionEvidence, stage: str) -> tuple[set[str], set]:
    """取某阶段出现过的 chunk id / parent key。"""
    if stage == "candidates":
        items: list[dict] = []
        for key in ("dense", "lexical", "rrf"):
            items.extend((collector.retrieval.get(key) or {}).get("items") or [])
    elif stage == "reranked":
        items = (collector.retrieval.get("rerank_after") or {}).get("items") or []
    else:
        items = collector.retrieval.get("final_contexts") or []
    return (
        {str(item.get("chunk_id")) for item in items},
        {tuple(item.get("parent_key")) for item in items if item.get("parent_key")},
    )


def build_facts(
    collector: QuestionEvidence,
    probe: dict,
    gold: dict,
    *,
    sample_class: str,
    numeric_risk: bool,
    sample_failed: bool,
    tool_correctness_reason: str,
) -> dict:
    """把探针/gold 与各阶段候选合并成事实标记与覆盖率。"""
    tokens = probe.get("probe_tokens") or []
    tool_names = [str(call.get("name") or "") for call in collector.tool_calls]
    common = {
        "testset_conflict": {
            "numeric_conflict": None,
            "unit_missing": None,
            "period_conflict": None,
            "missing_fact_tokens": [],
        },
        "judge_failed": False,
        "metric_attempted": not sample_failed and sample_class != "empty",
        "skipped_reason": (
            "sample_class=empty"
            if sample_class == "empty"
            else "failed"
            if sample_failed
            else None
        ),
        "tool_called": tool_names,
        "call_count": len(tool_names),
        "tool_correctness_reason": tool_correctness_reason,
    }
    if sample_class == "web":
        collector.retrieval["status"] = "not_applicable"
        return {
            **probe,
            **gold,
            **common,
            "fact_tokens": tokens,
            "fact_hit_tokens": {
                "corpus": [],
                "candidates": [],
                "reranked": [],
                "final": [],
            },
            "fact_coverage": {
                "corpus": None,
                "candidates": None,
                "reranked": None,
                "final": None,
            },
            "fact_in_corpus": None,
            "fact_in_candidates": None,
            "fact_in_reranked": None,
            "fact_in_final": None,
            "missing_fact_tokens": [],
            "answer_mentions_fact": answer_mentions_fact(
                collector.answer, tokens
            ),
        }
    token_hits = probe.get("token_hits") or {}
    token_keys = probe.get("token_parent_keys") or {}
    facts: dict = {
        **common,
        "fact_tokens": tokens,
        "fact_hit_tokens": {
            "corpus": (probe.get("fact_hit_tokens") or {}).get("corpus", []),
            "candidates": [],
            "reranked": [],
            "final": [],
        },
        "fact_coverage": {
            "corpus": (probe.get("fact_coverage") or {}).get("corpus"),
            "candidates": None,
            "reranked": None,
            "final": None,
        },
        "fact_in_corpus": probe.get("fact_in_corpus"),
        "fact_in_candidates": None,
        "fact_in_reranked": None,
        "fact_in_final": None,
        "testset_conflict": compute_testset_conflict(
            str((collector.testset or {}).get("reference") or ""),
            probe,
            numeric_risk=numeric_risk,
        ),
        "answer_mentions_fact": answer_mentions_fact(collector.answer, tokens),
    }
    if gold.get("gold_chunk_source") == "testset":
        facts["fact_in_corpus"] = True
        facts["fact_coverage"]["corpus"] = 1.0
    if tokens:
        for stage, fact_key in (
            ("candidates", "fact_in_candidates"),
            ("reranked", "fact_in_reranked"),
            ("final", "fact_in_final"),
        ):
            ids, keys = _stage_sets(collector, stage)
            hit = [
                token
                for token in tokens
                if any(cid in ids for cid in token_hits.get(token, []))
                or any(key in keys for key in token_keys.get(token, []))
            ]
            facts["fact_hit_tokens"][stage] = hit
            facts["fact_coverage"][stage] = len(hit) / len(tokens)
            facts[fact_key] = bool(hit)
    facts["missing_fact_tokens"] = [
        token for token in tokens if token not in facts["fact_hit_tokens"]["final"]
    ]
    facts["judge_failed"] = any(
        bool((collector.judge.get(metric) or {}).get("metric_attempted", False))
        and (
            (collector.judge.get(metric) or {}).get("error")
            or _is_nan((collector.judge.get(metric) or {}).get("score"))
        )
        for metric in (
            "faithfulness", "answer_relevancy", "context_precision",
            "context_recall", "contextual_relevancy", "non_advice",
        )
    )
    return {**probe, **gold, **facts}


def _atomic_json(path: Path, payload: dict) -> None:
    """临时文件 + os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _behavior_for(
    collector: QuestionEvidence, behaviour_rows: list[dict], trace_ids: list[str]
) -> dict:
    row = next(
        (r for r in behaviour_rows if r.get("q_index") == collector.q_index), {}
    )
    from src.cli.eval_scoring import link_behavior_traces

    linked = link_behavior_traces([row], trace_ids)[0] if row else {}
    return {
        "metrics": row.get("metrics") or {},
        "reasons": row.get("reasons") or {},
        "trace_id": linked.get("trace_id"),
        "link_method": linked.get("link_method"),
    }


def write_evidence_run(
    run: EvidenceRun,
    *,
    questions: list[str],
    scored_df: DataFrame | None,
    sample_classes: list[str],
    behaviour_rows: list[dict],
    trace_ids: list[str],
    run_id: str,
    run_trace_id: str | None,
    all_questions: bool,
) -> None:
    """选题、探针、归因并原子写盘；异常只 warning。"""
    if not run.enabled:
        return
    started = time.monotonic()
    target_dir = Path(EVAL_EVIDENCE_DIR) / run_id
    files: list[str] = []
    low: list[int] = []
    try:
        rows = {
            int(row["q_index"]): row
            for _, row in (scored_df.iterrows() if scored_df is not None else [])
        }
        for collector in run.collectors:
            if collector is None:
                continue
            row = rows.get(collector.q_index)
            if row is None:
                row = Series({"q_index": collector.q_index})
            sample_class = sample_classes[collector.q_index]
            if not should_write_evidence(
                row, sample_class, behaviour_rows, all_questions=all_questions
            ):
                continue
            low.append(collector.q_index)
            collector.behavior = _behavior_for(collector, behaviour_rows, trace_ids)
            chunk_list = run.corpus_chunks or []
            reference = str((collector.testset or {}).get("reference") or "")
            sample_failed = str(row.get("error") or "") == "failed"
            if sample_class == SAMPLE_CLASS_WEB:
                gold: dict = {}
                probe: dict = {}
            else:
                gold = resolve_gold(collector.testset or {}, chunk_list, reference)
                probe = probe_reference_chunks(reference, chunk_list)
            collector.facts = build_facts(
                collector,
                probe,
                gold,
                sample_class=sample_class,
                numeric_risk=bool(
                    (collector.testset or {}).get("numeric_risk")
                ),
                sample_failed=sample_failed,
                tool_correctness_reason=str(
                    row.get("tool_correctness_reason") or ""
                ),
            )
            collector.attribution = attribute_question(
                collector,
                sample_class=sample_class,
                sample_failed=sample_failed,
            )
            payload = {
                "schema_version": 1,
                "q_index": collector.q_index,
                "trace": {
                    "run_trace_id": run_trace_id,
                    "sut_trace_id": collector.trace_id,
                    "judge_measures": [
                        {
                            **{k: v for k, v in measure.items() if k != "calls"},
                            "judge_calls": measure.get("calls") or [],
                        }
                        for measure in (collector.trace.get("judge_measures") or [])
                    ],
                    "behavior": {
                        "trace_id": (collector.behavior or {}).get("trace_id"),
                        "link_method": (collector.behavior or {}).get("link_method"),
                    },
                },
                "testset": collector.testset,
                "query": collector.query,
                "retrieval": collector.retrieval,
                "tool_calls": collector.tool_calls,
                "judge": collector.judge,
                "behavior": collector.behavior,
                "facts": collector.facts,
                "attribution": collector.attribution,
            }
            name = f"q_{collector.q_index}.json"
            _atomic_json(target_dir / name, payload)
            files.append(name)
        summary = {
            "schema_version": 1,
            "run_id": run_id,
            "run_trace_id": run_trace_id,
            "qa_count": len(questions),
            "low_score_questions": low,
            "evidence_files": files,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            **summarize_attribution(run.collectors),
        }
        _atomic_json(target_dir / "summary.json", summary)
    except Exception as exc:  # noqa: BLE001  # 观测旁路
        logger.warning("[cli] evidence write failed err={}", exc)
```

- [ ] **Step 3: Wire scoring snapshot into `run_evaluation` / `score_rows`**

Change signatures:

```python
def run_evaluation(..., *, evidence_run=None, ...):
def score_rows(rows, metrics, *, diagnostic_metrics=None, evidence=None, evidence_run=None):
```

In `main()`, pass `evidence_run=evidence_run` to `run_evaluation`.

Add helper near `_score_tool_correctness`:

```python
def _snapshot_judge(scored: dict, evidence) -> dict:
    """把一行评分结果与 measure 记录转成 evidence.judge。"""
    measures = {
        str(item.get("metric")): item
        for item in (evidence.measures if evidence is not None else [])
    }    names = [
        name for name in (
            "faithfulness", "answer_relevancy", "context_precision",
            "context_recall", "contextual_relevancy", "non_advice",
            "tool_correctness",
        )
        if name in scored
    ]
    skipped = scored.get("error") in ("sample_class=empty", "failed")
    out: dict[str, dict] = {}
    for name in names:
        measure = measures.get(name) or {}
        attempted = not skipped
        if name == "tool_correctness" and skipped:
            attempted = scored.get("error") == "sample_class=empty"
        out[name] = {
            "score": scored.get(name),
            "error": scored.get("error", ""),
            "reason": measure.get("reason"),
            "metric_attempted": attempted,
            "skipped_reason": scored.get("error") if skipped else None,
        }
    return out
```

In `score_rows`, just before **both** `out.append(scored)` calls (empty/failed branch and normal branch):

```python
            if evidence_run is not None:
                collector = evidence_run.collector(row["q_index"])
                if collector is not None:
                    collector.judge = _snapshot_judge(scored, evidence)
                    if evidence is not None:
                        collector.trace["judge_measures"] = copy.deepcopy(
                            evidence.measures
                        )
```

`copy` 为 `import copy`（模块顶部）。该深拷必须发生在本题评分结束、下一题 `evidence.reset()` 之前。

`collector.judge` now carries `metric_attempted` / `skipped_reason` per metric, which `build_facts` reads.

- [ ] **Step 4: Wire main**

After `masked_df` is built (or after `result`), before `if args.gate`:

```python
        if evidence_run is not None:
            from src.cli.eval_evidence_report import write_evidence_run

            write_evidence_run(
                evidence_run,
                questions=questions,
                scored_df=result.to_pandas() if result is not None else None,
                sample_classes=sample_classes,
                behaviour_rows=behaviour_rows,
                trace_ids=trace_ids,
                run_id=run_id,
                run_trace_id=run_trace_id,
                all_questions=args.evidence_all,
            )
```

This must be after `save_markdown_report` and `_write_langfuse_scores`, before `check_gate`.

- [ ] **Step 5: Add `.gitignore`**

Append:

```gitignore
data/deepeval/evidence/
```

- [ ] **Step 6: Run tests**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_eval_evidence_report.py tests/cli/test_eval_evidence_attribution.py tests/cli/test_eval_evidence_probe.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cli/eval_evidence_report.py src/cli/eval_deepeval.py .gitignore tests/cli/test_eval_evidence_report.py
git commit -m "feat(eval): write evidence files before gate"
```

---

### Task 8: Docs and acceptance

**Files:**
- Modify: `src/cli/README.md`
- Modify: `docs/agents/eval-metrics-troubleshooting.md`
- Modify: `docs/agents/api_contract.md`
- Test: `tests/cli/test_eval_evidence_integration.py`（新建）

- [ ] **Step 1: Add integration test**

Create `tests/cli/test_eval_evidence_integration.py` with a fake `EvidenceRun` collector + synthetic chunks:

```python
from src.cli.eval_evidence_attribution import attribute_question
from src.core.eval_evidence import EvidenceRun, parent_key


def test_dense_hit_but_final_miss_is_rerank_or_topk():
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    c = run.new_question(0)
    c.facts = {
        "fact_in_corpus": True,
        "fact_in_candidates": True,
        "fact_in_reranked": False,
        "fact_in_final": False,
        "fact_coverage": {
            "corpus": 1.0, "candidates": 1.0, "reranked": 0.0, "final": 0.0
        },
        "metric_attempted": True,
        "judge_failed": False,
    }
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_precision"]["root_cause"] == "rerank_or_topk"
```

- [ ] **Step 2: Update docs**

`src/cli/README.md` add:

```markdown
### 评测证据（--evidence）

- `--evidence`：只写低分/NaN/失败/工具未命中/行为低分题
- `--evidence-all`：写全部题
- 路径：`data/deepeval/evidence/<run_id>/q_<q_index>.json` + `summary.json`
- 归因：`root_cause` / `confidence` / `evidence`；`inconclusive` 是合法结果
- 边界：F v1 不做幻觉归因；多值题部分覆盖只给 inconclusive；web 不做 KB 链归因
```

`docs/agents/eval-metrics-troubleshooting.md` add a 6-step table: score → trace → corpus → candidates → rerank → final → answer/judge, and link `--evidence`.

`docs/agents/api_contract.md` register the evidence directory, schema fields, and “not in API/DB/Langfuse”.

- [ ] **Step 3: Run all evidence tests + doc gate**

Run:

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest \
  tests/core/test_eval_evidence.py \
  tests/cli/test_eval_evidence_flags.py \
  tests/cli/test_eval_evidence_probe.py \
  tests/cli/test_eval_evidence_attribution.py \
  tests/cli/test_eval_evidence_report.py \
  tests/cli/test_eval_evidence_integration.py \
  tests/rag/test_rrf_score.py tests/rag/test_retrieval_evidence.py \
  tests/rag/test_retrieve_kb_evidence.py -v
.venv/bin/python -m src.cli.check_docs
```

Expected: PASS / 0 error.

- [ ] **Step 4: Live smoke Q21**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_deepeval \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 --testset-version 6 --limit 22 --evidence
```

Then open the highest-q_index evidence for Q21 and verify:
- `fact_in_corpus=true`；
- `fact_in_final=false`；
- `retrieval.rrf` 非空；
- `attribution.context_recall.root_cause` 是 `retrieval_miss` 或 `rerank_or_topk`；
- `trace.run_trace_id` 与报告头一致。

If Q21 is not selected because its score is not low in this run, use `--evidence-all` for the smoke.

- [ ] **Step 5: Full suite**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/cli/README.md docs/agents/eval-metrics-troubleshooting.md docs/agents/api_contract.md tests/cli/test_eval_evidence_integration.py
git commit -m "docs(eval): document evidence diagnostics"
```

---

## Self-Review

**Spec coverage:**

| Requirement | Task |
|---|---|
| opt-in / never changes scoring / write before gate | Task 1、7 |
| main-agent scope / subagent isolation / web | Task 1、6、7 |
| evidence file chain + trace | Task 4、5、7 |
| retrieval identity / score_kind / rrf / dedup / topk | Task 2、3 |
| chunk probe / gold / match_scope / parent key / numeric conflict | Task 5 |
| deterministic attribution / F boundary / null/coverage | Task 6 |
| summary counts | Task 6、7 |
| local/private/atomic | Task 7 |
| graceful degradation | Task 1、3、7 |
| docs/acceptance/Q21 smoke | Task 8 |

**Placeholder scan:** 无 TBD/TODO；每个代码步骤给了实际函数、字段或测试。
**Type consistency:** `EvidenceRun.new_question`、`current_evidence`、`evidence_scope`、`QuestionEvidence` 字段、`parent_key`、`ChunkResult.rrf_score`、`write_evidence_run` 参数在各 Task 之间一致；`attribute_question` 的 behavior key 别名与 `read_behaviour_scores` 的 “Task Completion / Step Efficiency / Agent Loop Detection” 对齐。
