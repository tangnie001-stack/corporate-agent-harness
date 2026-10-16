# timeout-and-degradation-hardening P2（降级）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个外部依赖的失败都落到一个**已定义的降级动作**，并把"降级了"这件事同时变成**可判定的日志留痕**与**用户看得见的告知文案**（P1 已把失败变成"可判定的有限时间"，P2 处置它）。

**Architecture:** 动作留在**各调用点**（每个依赖语义不同），不抽统一降级管理器（design D1 ③ / D6）。两类出口收敛：**事件** → `src/core/log_event_specs.py` 的 `EVENT_SPECS`（唯一归属）；**用户文案** → `src/config/const.py` 的取值集合表（唯一归属）。判定（"这是什么失败"）抽成一个**无状态纯函数** `retry_policy.classify_failure`，挂在 P1 已建的重试原语模块旁边。用户可见的降级文案经**既有 `status` 事件 + 专用 stage**送达（不复用 `retrieve` / `web_search` 的 stage）。

**Tech Stack:** Python 3.12 / asyncio / LangGraph `astream_events` / pytest（`pytest.mark.asyncio`）/ loguru 事件注册表 / 原生 HTML+JS 前端（`deploy/nginx/html/chat.html`）。

**Spec:** `docs/openspec/changes/timeout-and-degradation-hardening/design.md`（D1–D17，尤其 D5/D6/D7/D9）、`.../specs/degradation-policy/spec.md`、`.../specs/hybrid-retrieval/spec.md`、`.../tasks.md` §2（范围+验收索引）、`.../proposal.md`。

## Global Constraints

以下为项目级约束，**每个任务都隐含遵守**；取值与措辞均逐字取自 spec / design。

- 降级动作 SHALL 落在**该依赖的调用点**；SHALL NOT 抽象为统一的降级管理器（D1 ③ / D6）。
- 降级事件 SHALL 定义在 `src/core/log_event_specs.py` 的 `EVENT_SPECS`；**每次降级的依赖与原因 SHALL 可判定**（可由事件名或字段承载）。
- **SHALL NOT 复用 `CAPABILITY_DEGRADED`**（其语义是 `/skills`、`/agents` 能力清单读取失败）。
- 降级 `reason` 取值与用户可见文案 **SHALL 取自 spec 取值集合表，实现 SHALL NOT 自造取值/文案**。
- 用户可见的降级文案 SHALL 经**既有 `status` 事件 + 专用 stage 常量**送达；**SHALL NOT 复用** `retrieve` / `web_search` 的 stage（会让前端触发正文/旁白重分类）。
- rerank 的降级**不新增事件、不加 `reason`** —— 原因由既有事件名 `rerank failed` / `rerank timeout` 表达（评审 I5）。
- 混合检索的"失败"（抛错/超时）SHALL 与"缺席"（成功但空结果）**可区分**；两路皆失败时 SHALL 按失败处置，**SHALL NOT 静默返回空结果**当作"没有数据"。
- **SHALL NOT 改 `search()` 的返回签名**（状态字段挂既有的 `hybrid done` 事件上）。
- `401/403` 在生产链路**一律不重试**，并合并为**单一**文案取值 `provider_unavailable`（额度/鉴权细分判据**已移出本变更**，归 `deepeval-bridge-eval`）；`429`/`5xx` 重试穷尽取 `upstream_exhausted`（该类**可**提示重试）。
- **SHALL NOT 引入进程内模型 fallback**（不自动换模型/换 provider 掩盖不可用）。
- 流一旦已开始输出（首个 chunk 之后），**SHALL NOT 重试**、也**不重放检索原文**。
- 本阶段**不做** P3 的 idle 保险丝（`generation_idle` 只登记取值，不接线）；**不新增 env 键**；**不改** `src/cli/**`（评测链地盘）。
- 本仓 **禁止三元表达式**（`CLAUDE.md`），一律 `if/elif/else`。
- 新增事件前缀须**同时**登记 `src/core/log_events.py` 的 `LOG_PREFIXES` **与** `docs/agents/logging-rules.md` 的「前缀主表」；新增"非代码可读的值域"须登记同文件的对应小节。
- 验收边界（`tasks.md` 0.2）：本 worktree 内只跑 **非 DB 单测 + `ruff` + `pyright`**；`tests/parsers`（缺 gitignored `data/`）、`tests/rag/test_temporal.py` 与 2 个 api 用例（缺 `POSTGRES_PASSWORD`）、`test_tracing`（缺 `.env`）的失败**均为环境性**；3 个 cli 失败为**改动前即存在**。
- 工作目录：`/root/code/corporate_rag-timeout-degradation`（git worktree，分支 `feat/timeout-and-degradation-hardening`）；Python 一律 `.venv/bin/python`。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `src/rag/errors.py` | **新建** | 检索层失败标记异常 `RetrievalUnavailableError`（独立模块，避免 `retry_policy` ↔ `retrieval` 成环） |
| `src/rag/retrieval.py` | 改 | `search()` 的两路失败语义 + 三态状态字段 + 单路降级留痕 |
| `src/infra/retry_policy.py` | 改 | 新增纯函数 `classify_failure`（失败 → 降级 reason 取值） |
| `src/config/const.py` | 改 | 降级 stage + reason 取值集合 + "可见 reason → 文案"表（文案唯一归属） |
| `src/core/log_events.py` | 改 | 新事件 `degraded` + 新前缀 `degradation` |
| `src/core/log_event_specs.py` | 改 | `degraded` 条目 + `hybrid done` 增 `dense_status`/`sparse_status` |
| `src/services/agent_service.py` | 改 | `_run_generation` 的失败分支：判定 → 留痕 → 经 status+stage 送达文案（+ 有原文则重放） |
| `src/services/turn_runner.py` | 改 | 终态 `error` 事件携带**安全文案**（已归类取表，未归类留空由前端回落） |
| `deploy/nginx/html/chat.html` | 改 | `error` 分支消费后端文案；`degraded` stage 单独成组呈现 |
| `docs/agents/logging-rules.md` | 改 | 登记新前缀/事件/值域（`dense_status`/`sparse_status`/`reason` 集合） |
| `tests/rag/test_retrieval.py` | 改（追加） | T1 单路失败降级 / 三态字段 / 两路皆失败 |
| `tests/config/test_degradation_vocab.py` | **新建** | T2 词表守卫 + `classify_failure` 行为 |
| `tests/services/test_agent_service.py` | 改（追加） | T3 失败分支三分支 + `error` 文案 |

---

### Task 1: 混合检索单路失败降级（三态状态字段 + `degraded` 事件）

**背景与已实测的现状**：`src/rag/retrieval.py:85` 是 `dense, sparse = await asyncio.gather(dense_coro, lexical_coro)` —— **未传 `return_exceptions`** ⇒ 稠密路（查询向量化）一抛，整个 `search` 就抛，**词法结果被连带丢弃** ⇒ "Embedding 失败 → 只走词法"**根本不成立**（design D7）。

**Files:**
- Create: `src/rag/errors.py`
- Modify: `src/rag/retrieval.py:1-31`（imports + 新增 `_path_status`）、`src/rag/retrieval.py:64-112`（`search`）
- Modify: `src/core/log_events.py:18-34`（`LOG_PREFIXES`）、`src/core/log_events.py:53-226`（`Event`）
- Modify: `src/core/log_event_specs.py`（`hybrid done` 字段 + 新增 `degraded` 条目）
- Modify: `docs/agents/logging-rules.md`（前缀主表 + 值域登记）
- Test: `tests/rag/test_retrieval.py`

**Interfaces:**
- Consumes: 无（本任务不依赖前序任务）
- Produces:
  - `src.rag.errors.RetrievalUnavailableError(RuntimeError)` —— 两路皆失败时 `search()` 抛出（T2 的 `classify_failure`、T3 的降级发射都靠它归类为 `vector_store_failed`）
  - `Event.DEGRADED = "degraded"`（前缀 `degradation`，级别 `warning`，字段 `("reason", "err")`）—— T2/T3 复用
  - `Event.HYBRID_DONE` 的字段增 `dense_status` / `sparse_status` ∈ `{"ok","empty","failed"}`

**落地规则（本任务特有的设计选择，写清以免被当成漏做）**：
- **"谁处置谁留痕"**：单路失败由本层就地降级（跳过该路）⇒ **本层记 `degraded` 事件**；两路皆失败 **不记** —— 本层的"处置"是**抛给上层**，由上层（T3）记一条，避免同一失败在两层各记一次（违背 2.9 的"唯一匹配"）。
- **dense 路失败统一记 `embedding_failed`**：dense 路的失败来源可能是"查询向量化"或"pgvector"，但**两者的降级动作相同**（跳过稠密路只走词法），且 spec 取值表把该动作的取值定为 `embedding_failed` ⇒ 本变更**不引入**区分二者所需的新判据（那会是自造细分）。代价：`degraded reason=embedding_failed` 事件不区分向量库内部故障。

- [ ] **Step 1: 写失败测试（三个场景）**

追加到 `tests/rag/test_retrieval.py` **文件末尾**。若文件顶部尚未导入 `Event`，在本步一并补 `from src.core.log_events import Event`，并补 `from src.rag.errors import RetrievalUnavailableError`。

```python
@pytest.mark.asyncio
async def test_hybrid_dense_failure_degrades_to_lexical_only(monkeypatch):
    """稠密路失败 ⇒ 仍返回词法结果；状态字段区分 failed 与 ok，并留痕 degraded。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            raise RuntimeError("embedding endpoint down")

        async def lexical_search(self, kb_id, query, k):
            return [
                ChunkResult(
                    id="b", content="B", metadata={}, lexical_score=0.9, sparse_rank=0
                )
            ]

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: logged.append((event, fields))
    )

    results = await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    # 词法结果必须被返回（修前：gather 无 return_exceptions ⇒ 整体抛错、结果丢失）
    assert [r.id for r in results] == ["b"]
    hybrid = [f for e, f in logged if e == Event.HYBRID_DONE]
    assert len(hybrid) == 1
    assert hybrid[0]["dense_status"] == "failed"
    assert hybrid[0]["sparse_status"] == "ok"
    assert hybrid[0]["dense_count"] == 0
    degraded = [f for e, f in logged if e == Event.DEGRADED]
    assert len(degraded) == 1
    assert degraded[0]["reason"] == "embedding_failed"


@pytest.mark.asyncio
async def test_hybrid_sparse_failure_still_returns_dense(monkeypatch):
    """词法路失败 ⇒ 仍返回稠密结果（对称场景，防只改了一半）。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            return [ChunkResult(id="a", content="A", metadata={}, distance=0.1)]

        async def lexical_search(self, kb_id, query, k):
            raise RuntimeError("pg fts down")

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: logged.append((event, fields))
    )

    results = await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    assert [r.id for r in results] == ["a"]
    hybrid = [f for e, f in logged if e == Event.HYBRID_DONE][0]
    assert hybrid["dense_status"] == "ok"
    assert hybrid["sparse_status"] == "failed"


@pytest.mark.asyncio
async def test_hybrid_success_but_empty_is_empty_not_failed(monkeypatch):
    """成功但空结果 ⇒ empty（缺席），不得与 failed 混淆。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            return []

        async def lexical_search(self, kb_id, query, k):
            return []

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: logged.append((event, fields))
    )

    results = await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    assert results == []
    hybrid = [f for e, f in logged if e == Event.HYBRID_DONE][0]
    assert hybrid["dense_status"] == "empty"
    assert hybrid["sparse_status"] == "empty"
    assert not [f for e, f in logged if e == Event.DEGRADED]


@pytest.mark.asyncio
async def test_hybrid_both_paths_failed_raises_retrieval_unavailable(monkeypatch):
    """两路皆失败 ⇒ 抛 RetriavalUnavailableError（按失败处置，不静默返回空）。"""

    class _Store:
        async def dense_search(self, kb_id, query, k):
            raise RuntimeError("embedding down")

        async def lexical_search(self, kb_id, query, k):
            raise RuntimeError("pg down")

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: logged.append((event, fields))
    )

    with pytest.raises(RetrievalUnavailableError):
        await retrieval.search("资产负债率", "kb1", cast(VectorStore, _Store()))

    # 本层只抛不留痕：终态那条由上层（agent_service 的降级发射）记，避免同失败两处各记一次
    assert not [f for e, f in logged if e == Event.DEGRADED]
    assert not [f for e, f in logged if e == Event.HYBRID_DONE]
```

- [ ] **Step 2: 跑测试确认全红**

Run: `.venv/bin/python -m pytest tests/rag/test_retrieval.py -q -k "hybrid_dense_failure or hybrid_sparse_failure or hybrid_success_but_empty or both_paths_failed"`
Expected: FAIL —— 4 条全挂，错误含 `ImportError: cannot import name 'RetrievalUnavailableError'`（模块未建）与 `AttributeError: 'Event' object has no attribute 'DEGRADED'`。

- [ ] **Step 3: 新建 `src/rag/errors.py`**

```python
"""检索层的失败标记异常。

归属：change `timeout-and-degradation-hardening` 的 P2（降级动作矩阵 / D6/D7）。
`search()` 两路皆失败时抛本异常，供上层归类为 `vector_store_failed`
（取值集合见 `specs/degradation-policy/spec.md`），按"知识库暂不可用"告知用户。

独立模块的理由：判定原语 `src/infra/retry_policy.py` 需要引用它，而
`retrieval.py` 已经 import `retry_policy` —— 放在 `retrieval.py` 里会成环。
"""


class RetrievalUnavailableError(RuntimeError):
    """混合检索两路皆失败 —— 向量库不可用（降级交上层处置）。"""
```

- [ ] **Step 4: `src/core/log_events.py` 登记新前缀与新事件**

`LOG_PREFIXES`（第 18-34 行）的集合里追加一行，使其成为：

```python
LOG_PREFIXES: frozenset[str] = frozenset(
    {
        "retrieval",
        "verify",
        "agent",
        "session",
        "db",
        "llm",
        "cli",
        "app",
        "delegate",
        "wecom",
        "clarify",
        "provider",
        "alert",
        "degradation",
    }
)
```

`Event` 枚举末尾（`MODEL_RETRY = "model retry"` 之后）追加：

```python
    # [degradation] 外部依赖降级留痕（timeout-and-degradation-hardening P2）：
    # reason 取值取自 specs/degradation-policy 的取值集合表，不得自造；
    # 「谁处置谁留痕」—— 同一失败不在两层各记一次。
    DEGRADED = "degraded"
```

- [ ] **Step 5: `src/core/log_event_specs.py` 登记 `degraded` 与 `hybrid done` 新字段**

`hybrid done` 条目改为：

```python
    "hybrid done": EventSpec(
        "hybrid done",
        "retrieval",
        "info",
        (
            "kb_id",
            "query_len",
            "dense_count",
            "sparse_count",
            "dense_status",
            "sparse_status",
            "result_count",
        ),
    ),
```

表末尾（`"model retry"` 条目之后）追加：

```python
    # 外部依赖降级留痕（reason 取值见 specs/degradation-policy 的取值集合表；不得自造）
    "degraded": EventSpec("degraded", "degradation", "warning", ("reason", "err")),
```

- [ ] **Step 6: 跑测试确认全绿（此时会因 `search()` 未改而只绿一半）**

Run: `.venv/bin/python -m pytest tests/rag/test_retrieval.py -q -k "hybrid_dense_failure or hybrid_sparse_failure or hybrid_success_but_empty or both_paths_failed"`
Expected: FAIL —— 只剩 `dense_status` 断言类失败（`KeyError`/`AssertionError`），且 `both_paths_failed` 仍报 `RuntimeError: pg down`（未包装成 `RetrievalUnavailableError`）。**import 与事件登记类错误应已消失**（这一步专门用来把"登记错"和"逻辑错"分开）。

- [ ] **Step 7: 改 `src/rag/retrieval.py` —— `search()` 两路失败语义 + 三态字段**

在 `_dedup_by_parent` 之后（`search` 之前）新增 `_path_status`，并补 import `from src.rag.errors import RetrievalUnavailableError`：

```python
def _path_status(failed: bool, results: list[ChunkResult]) -> str:
    """支路本次调用状态（规格三态：ok / empty / failed）。

    failed 与 empty **必须可分**：`count = 0` 分不出"该路失败"与"该路成功但没命中"，
    而两者的处置完全不同（前者是降级、后者是缺席）。

    Args:
        failed: 该路调用是否未成功（抛错 / 超时）
        results: 该路成功时返回的结果列表（failed=True 时忽略）

    Returns:
        "failed" / "empty" / "ok"
    """
    if failed:
        return "failed"
    if results:
        return "ok"
    return "empty"
```

把 `search()` 的混合分支（第 82-99 行）整体替换为：

```python
    if HYBRID_SEARCH_ENABLED:
        dense_coro = vector_store.dense_search(kb_id, query, TOP_K_RETRIEVAL)
        lexical_coro = vector_store.lexical_search(kb_id, query, TOP_K_RETRIEVAL)
        # return_exceptions=True：任一路失败不得连带丢弃另一路（design D7）。
        # 修前未传该参数 ⇒ 稠密路一抛整个 search 就抛，"退化为词法单路"根本不成立。
        dense_out, sparse_out = await asyncio.gather(
            dense_coro, lexical_coro, return_exceptions=True
        )
        dense_failed = isinstance(dense_out, BaseException)
        sparse_failed = isinstance(sparse_out, BaseException)
        if dense_failed and sparse_failed:
            # 两路皆失败 ⇒ 向量库不可用：按失败处置（抛给上层归类/告知），
            # 不得静默返回空结果当作"没有数据"。本层只抛不留痕（见计划 Task 1 落地规则）。
            assert isinstance(dense_out, BaseException)  # 供类型收窄，运行期恒真
            raise RetrievalUnavailableError(str(dense_out)) from dense_out
        if dense_failed:
            # 稠密路失败 ⇒ 跳过稠密路只走词法（降级动作在本调用点完成）。
            # 原因统一记 embedding_failed：dense 路的失败来源（查询向量化 / pgvector）
            # 与本降级动作同义，不引入区分二者所需的新判据。
            log_event(Event.DEGRADED, reason="embedding_failed", err=str(dense_out))
            dense_results: list[ChunkResult] = []
        else:
            dense_results = dense_out or []
        if sparse_failed:
            sparse_results: list[ChunkResult] = []
        else:
            sparse_results = sparse_out or []
        results = rrf_fusion(dense_results, sparse_results, k=RRF_K, top_n=RRF_TOP_N)
        # 两路各自的贡献必须可见：任一路为 0 时该字段就是 0。
        # 只记融合后的总数会让"某一路长期失效"不可发现（trace_c54ce259 的教训）。
        # dense_status / sparse_status 另把"失败"与"成功但空"分开（规格要求）。
        log_event(
            Event.HYBRID_DONE,
            kb_id=kb_id,
            query_len=len(query),
            dense_count=len(dense_results),
            sparse_count=len(sparse_results),
            dense_status=_path_status(dense_failed, dense_results),
            sparse_status=_path_status(sparse_failed, sparse_results),
            result_count=len(results),
        )
        return results
```

- [ ] **Step 8: 跑测试确认全绿 + 跑检索全量防回归**

Run: `.venv/bin/python -m pytest tests/rag/ tests/agents/tools/ -q`
Expected: PASS（`tests/rag/test_temporal.py` 的 3 例因缺 `POSTGRES_PASSWORD` 环境性失败，属预期，不算回归）。

- [ ] **Step 9: 登记文档（前缀 + 值域）**

`docs/agents/logging-rules.md` 的「前缀主表」在 `[alert]` 行后追加一行：

```markdown
| `[degradation]` | 外部依赖降级留痕（事件：`degraded`；`reason` 取值集合见 spec 取值集合表，值域登记见下） |
```

同文件「来源与 prompt 观测事件」小节的条目清单末尾（`history budget exceeded` 那组之后）追加两条：

```markdown
- `hybrid done` 增 `dense_status` / `sparse_status`（retrieval / info）——两路**本次调用**状态，
  取 ok（成功且有结果）/ empty（成功但空结果=缺席）/ failed（未成功=失败）；与 `dense_count` 配对
  使用（`count=0` 本身分不出失败与缺席）
- `degraded`（degradation / warning）——外部依赖降级留痕；`reason` 取 embedding_failed /
  vector_store_failed / llm_unavailable_with_context / llm_unavailable_no_context /
  llm_stream_interrupted / provider_unavailable / upstream_exhausted / generation_idle
  （唯一来源：`docs/openspec/specs/degradation-policy/spec.md` 的取值集合表）
```

- [ ] **Step 10: 闸门 + 提交**

Run: `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright`
Expected: 仅剩**存量**告警（`src/infra/db/engine.py` 的 `UP047` + pyright 4 处，均在 P1 之前就有）；本次改动**零新增**。

```bash
git add src/rag/errors.py src/rag/retrieval.py src/core/log_events.py src/core/log_event_specs.py tests/rag/test_retrieval.py docs/agents/logging-rules.md
git commit -m "feat(retrieval): 单路失败降级与三态状态字段（P2）" -m "- search() 的 gather 补 return_exceptions：任一路失败仍返回另一路（修前稠密路一抛会连带丢弃词法结果，D7）
- hybrid done 增 dense_status/sparse_status（ok|empty|failed）：'失败'与'缺席'可分
- 两路皆失败 ⇒ 抛 RetrievalUnavailableError（按失败处置，不静默返回空当作没有数据）
- 新事件 degraded（前缀 degradation）+ 单路失败就地留痕 reason=embedding_failed；同一失败不在两层各记（谁处置谁留痕）
- logging-rules 登记新前缀与两处值域"
```

---

### Task 2: 失败分类原语与降级词表（`classify_failure` + stage + 文案）

**背景**：P2 要求"取值不得自造"，但 P1 交付的重试原语 `src/infra/retry_policy.py` 只回答"**该不该重试**"，没有"**这是什么失败**"。本任务补上这个纯函数，并把用户可见通道（stage）与文案收敛成**唯一来源**，配一条守卫测试钉住一致性。

**Files:**
- Modify: `src/infra/retry_policy.py`（追加 `classify_failure` / `_find_status_code`）
- Modify: `src/config/const.py`（`SSEInteractionTexts` 内追加）
- Test: `tests/config/test_degradation_vocab.py`（**新建**）
- Modify: `docs/agents/logging-rules.md`（reason 值域已在 T1 Step 9 登记，本任务如发现措辞需与取值集合对齐则微调）

**Interfaces:**
- Consumes: `src.rag.errors.RetrievalUnavailableError`（T1 交付）
- Produces:
  - `src.infra.retry_policy.classify_failure(exc: BaseException) -> str | None` —— 返回取值集合表里的 `reason`，或 `None`（未归类）
  - `SSEInteractionTexts.STAGE_DEGRADED: str`（值 `"degraded"`）
  - `SSEInteractionTexts.DEGRADATION_REASONS: tuple[str, ...]`
  - `SSEInteractionTexts.DEGRADATION_TEXTS: dict[str, str]`（仅「用户可见 = 是」的取值）

- [ ] **Step 1: 写失败测试**

新建 `tests/config/test_degradation_vocab.py`：

```python
"""降级词表守卫（P2）：取值/文案唯一来源 + 失败分类原语。

守卫的意义：spec 的「降级原因与用户文案的取值集合」表是唯一来源，
实现不得自造取值；本文件把"实现里的取值 ⊆ 规格表"这件事变成可执行断言。
"""

from src.config.const import SSEInteractionTexts
from src.core.log_event_specs import EVENT_SPECS
from src.core.log_events import Event
from src.infra.retry_policy import classify_failure
from src.rag.errors import RetrievalUnavailableError


class _StatusErr(Exception):
    """带 HTTP 状态码的异常替身（openai / httpx 的失败都带 status_code）。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def test_degraded_event_registered_with_reason_field():
    """降级留痕事件在 EVENT_SPECS 登记且带 reason 字段（供日志侧判据匹配）。"""
    assert Event.DEGRADED.value in EVENT_SPECS
    assert "reason" in EVENT_SPECS[Event.DEGRADED.value].fields


def test_reason_vocabulary_covers_spec_table():
    """取值集合覆盖 spec 表（含 P3 才会消费的 generation_idle）。"""
    required = {
        "embedding_failed",
        "vector_store_failed",
        "llm_unavailable_with_context",
        "llm_unavailable_no_context",
        "llm_stream_interrupted",
        "provider_unavailable",
        "upstream_exhausted",
        "generation_idle",
    }
    assert required <= set(SSEInteractionTexts.DEGRADATION_REASONS)


def test_visible_texts_stay_inside_reason_vocabulary():
    """文案表的 key 必须都是已登记取值（防自造）。"""
    assert set(SSEInteractionTexts.DEGRADATION_TEXTS) <= set(
        SSEInteractionTexts.DEGRADATION_REASONS
    )


def test_stage_degraded_is_dedicated():
    """降级 stage 不得复用 retrieve / web_search（会触发前端重分类）。"""
    assert SSEInteractionTexts.STAGE_DEGRADED not in ("retrieve", "web_search")


def test_provider_unavailable_copy_does_not_lure_retry():
    """可用性类错误重试必然再失败 ⇒ 文案不得出现重试语义。"""
    text = SSEInteractionTexts.DEGRADATION_TEXTS["provider_unavailable"]
    assert "重试" not in text
    assert "再试" not in text


def test_classify_failure_maps_http_status_codes():
    assert classify_failure(_StatusErr(401)) == "provider_unavailable"
    assert classify_failure(_StatusErr(403)) == "provider_unavailable"
    assert classify_failure(_StatusErr(429)) == "upstream_exhausted"
    assert classify_failure(_StatusErr(500)) == "upstream_exhausted"
    assert classify_failure(_StatusErr(503)) == "upstream_exhausted"


def test_classify_failure_reads_wrapped_cause_chain():
    """异常可能被上游框架包装 ⇒ 沿 __cause__ / __context__ 链上溯状态码。"""
    wrapped = RuntimeError("langgraph wrapped")
    wrapped.__cause__ = _StatusErr(503)
    assert classify_failure(wrapped) == "upstream_exhausted"


def test_classify_failure_vector_store_marker():
    assert (
        classify_failure(RetrievalUnavailableError("pg down")) == "vector_store_failed"
    )


def test_classify_failure_unknown_returns_none():
    assert classify_failure(ValueError("bad input")) is None


def test_classify_failure_outputs_stay_inside_vocabulary():
    for exc in (
        _StatusErr(403),
        _StatusErr(429),
        RetrievalUnavailableError("x"),
    ):
        assert classify_failure(exc) in SSEInteractionTexts.DEGRADATION_REASONS
```

- [ ] **Step 2: 跑测试确认全红**

Run: `.venv/bin/python -m pytest tests/config/test_degradation_vocab.py -q`
Expected: FAIL —— `ImportError: cannot import name 'classify_failure'`（函数未建），随后其余用例因 `AttributeError`（`DEGRADATION_REASONS` 未建）失败。

- [ ] **Step 3: `src/infra/retry_policy.py` 追加分类纯函数**

在文件末尾（`acall_with_retry` 之后）追加；并在文件顶部 import 区补一行 `from src.rag.errors import RetrievalUnavailableError`（放在 `import httpx` 一行之后、`from loguru import logger` 之前的位置即可）：

```python
def _find_status_code(exc: BaseException, max_depth: int = 5) -> int | None:
    """沿异常链找 HTTP 状态码（自身 → `__cause__` / `__context__`，去重、限深）。

    上游框架（langgraph / langchain）会把 provider 异常包装后再抛，
    直接看最外层拿不到 `status_code`，故必须上溯。

    Args:
        exc: 起始异常
        max_depth: 最大上溯层数（防御异常链成环）

    Returns:
        找到的状态码；未找到返回 None
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < max_depth:
        if id(cur) in seen:
            return None
        seen.add(id(cur))
        code = getattr(cur, "status_code", None)
        if isinstance(code, int):
            return code
        cur = cur.__cause__ or cur.__context__
        depth += 1
    return None


def classify_failure(exc: BaseException) -> str | None:
    """把失败归类为**降级原因**（取值取自 spec 的取值集合表，不新造取值）。

    与 `is_retryable` 的分工：后者回答"要不要重试"，本函数回答"这是什么失败"。
    本函数**不决定动作**（动作留各调用点，design D1 ③），只给一个取值。

    判据（按 HTTP 状态码；额度耗尽与鉴权失败**合并**为单一取值，其细分判据
    已移出本变更、归评测链 `deepeval-bridge-eval`，见 design D5）：
    - 401 / 403 → `provider_unavailable`；
    - 429 / 5xx → `upstream_exhausted`（重试穷尽后可达此判定）；
    - `RetrievalUnavailableError` → `vector_store_failed`；
    - 其它 → `None`（未归类，调用方按通用失败处置）。

    Args:
        exc: 待归类的异常

    Returns:
        取值字符串（见上），或 None
    """
    if isinstance(exc, RetrievalUnavailableError):
        return "vector_store_failed"
    status = _find_status_code(exc)
    if status in (401, 403):
        return "provider_unavailable"
    if status == 429 or (status is not None and 500 <= status <= 599):
        return "upstream_exhausted"
    return None
```

- [ ] **Step 4: `src/config/const.py` 追加降级通道与词表**

在 `SSEInteractionTexts` 类内、`# ── SSE 错误事件文案 ──` 段之前追加：

```python
    # ── 降级：用户可见通道与取值集合（timeout-and-degradation-hardening P2）──
    # 降级告知专用 stage：SHALL NOT 复用 retrieve / web_search —— 前端 renderStatusTag
    # 对这两者会触发"待定正文归为旁白"的重分类（与 STAGE_TURN_* 同因）
    STAGE_DEGRADED: str = "degraded"

    # reason 取值集合 —— 唯一来源：docs/openspec/specs/degradation-policy/spec.md 的
    # 「降级原因与用户文案的取值集合」表；实现不得自造取值（含 P3 才消费的 generation_idle）
    DEGRADATION_REASONS: tuple[str, ...] = (
        "embedding_failed",
        "vector_store_failed",
        "llm_unavailable_with_context",
        "llm_unavailable_no_context",
        "llm_stream_interrupted",
        "provider_unavailable",
        "upstream_exhausted",
        "generation_idle",
    )

    # 仅「用户可见 = 是」的取值入本表（其余在实现侧不送达用户）。
    # provider_unavailable 的文案不得含"重试"语义：该类错误重试必然再失败。
    # upstream_exhausted / llm_* 属可恢复或流中断，可提示重试。
    DEGRADATION_TEXTS: ClassVar[dict[str, str]] = {
        "vector_store_failed": "知识库暂不可用",
        "llm_unavailable_with_context": "模型服务暂不可用；以下为已检索到的原文",
        "llm_unavailable_no_context": "模型服务暂不可用，请稍后再试",
        "llm_stream_interrupted": "回答未生成完整，请重试",
        "provider_unavailable": "服务暂不可用，本次无法作答",
        "upstream_exhausted": "服务繁忙，请稍后再试",
        "generation_idle": "仍在处理中，请稍候",
    }
```

（`ClassVar` 已在 `src/config/const.py` 顶部导入；若 ruff 报未使用则说明该文件实际未用它，改为普通注解 `DEGRADATION_TEXTS: dict[str, str] = {...}`。）

- [ ] **Step 5: 跑测试确认全绿**

Run: `.venv/bin/python -m pytest tests/config/test_degradation_vocab.py tests/core/ -q`
Expected: PASS（`tests/config/test_degradation_vocab.py` 全绿；`tests/core/` 无回归）。

- [ ] **Step 6: 闸门 + 提交**

Run: `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright`
Expected: 零新增（仅 P1 之前就有的存量告警）。

```bash
git add src/infra/retry_policy.py src/config/const.py tests/config/test_degradation_vocab.py
git commit -m "feat(degradation): 失败分类原语与降级词表（P2）" -m "- retry_policy.classify_failure：把失败归类为降级 reason（401/403 → provider_unavailable；429/5xx → upstream_exhausted；RetrievalUnavailableError → vector_store_failed；其它 None），沿异常链上溯状态码
- const.SSEInteractionTexts：STAGE_DEGRADED（专用 stage）+ DEGRADATION_REASONS（唯一取值集合）+ DEGRADATION_TEXTS（仅用户可见项）
- provider_unavailable 文案不含重试语义；守卫测试钉住'实现取值 ⊆ 规格表'与 stage 不复用 retrieve/web_search"
```

---

### Task 3: LLM / 向量库失败路径的降级动作（`_run_generation` + `_run_with_finalize`）

**背景与已实测的现状**：LLM 失败（首 token 前）今天走 `src/services/turn_runner.py:136` 的 `except Exception` ⇒ 发 `error` 事件（`{"error": str(e)}`）、**不发 `done`**、且已产出的 `purified` 为空时不落 assistant 行；前端 `error` 分支**硬编码** `showError('连接异常，请重试')`。⇒ 用户看到的是**通用且鼓励重试**的文案，对"额度耗尽/鉴权失败"这类**重试必然再失败**的错误是误导（design D9）。

**落点已核实（关键，避免改错地方）**：生产链路是 `turn_runner._run_with_finalize` → `agent_service._run_generation`，而 `_run_generation` **直接迭代 `graph.astream_events(...)`**（`agent_service.py:712`）⇒ 图内异常**会传播到** `_run_generation` 的循环外。另有一个 `_dual_stream`（`agent_service.py:394`）会在内部把异常吞成 `_ErrorMarker` → `SSEErrorEvent`，但它**只被自身单测调用、不在生产路径上**（`grep -rn "_dual_stream" src/` 仅命中定义与注释）⇒ 本任务**不碰**它。

**Files:**
- Modify: `src/services/agent_service.py:711-737`（`_run_generation` 的 try 段）＋ 新增两个模块级 helper + import `retry_policy`
- Modify: `src/services/turn_runner.py:152`（`error` 事件载荷）＋ 新增 `_error_copy` helper + imports
- Test: `tests/services/test_agent_service.py`（追加）

**Interfaces:**
- Consumes: `retry_policy.classify_failure`（T2）、`RetrievalUnavailableError`（T1）、`SSEInteractionTexts.STAGE_DEGRADED` / `DEGRADATION_TEXTS`（T2）、`Event.DEGRADED`（T1）
- Produces:
  - `agent_service._degradation_reason(exc, ctx, produced) -> str`
  - `agent_service._emit_degradation(manager, session_id, capture, ctx, partial_holder, exc, produced) -> None`
  - `turn_runner._error_copy(exc: BaseException) -> str`（已归类 → 表内文案；未归类 → `""`）

- [ ] **Step 1: 写失败测试**

追加到 `tests/services/test_agent_service.py` **文件末尾**（该文件已 import `agent_service`、`Event`、`RAGContext`、`RequestContext`；若缺 `RequestContext` 则在该步补 `from src.infra.llm.request_context import RequestContext`）：

```python
class _Recorder:
    """StreamingRunManager 的最小替身：只收 add_event。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def add_event(self, session_id: str, etype: str, payload: dict) -> None:
        self.events.append((etype, payload))


class _FailingGraph:
    """astream_events 立刻抛出（模拟首 token 前失败）。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def astream_events(self, state, version=None):
        raise self._exc
        yield  # pragma: no cover  # 让本方法成为异步生成器


class _TokenThenFailGraph:
    """先吐一个 token 再抛出（模拟流中途断）。"""

    def __init__(self, item: dict, exc: BaseException) -> None:
        self._item = item
        self._exc = exc

    async def astream_events(self, state, version=None):
        yield self._item
        raise self._exc


async def _run(manager, ctx, graph, partial_holder) -> None:
    from src.services.agent_service import _run_generation

    await _run_generation(
        ctx.session_id,
        ctx.kb_id,
        "营收多少",
        [],
        False,
        ctx,
        manager,
        graph=graph,
        partial_holder=partial_holder,
    )


@pytest.mark.asyncio
async def test_llm_failure_with_context_delivers_retrieved_original():
    """首 token 前失败且已有检索结果 ⇒ 送检索原文 + 告知，并更新 partial_holder。"""
    from src.config.const import SSEInteractionTexts
    from src.infra.llm.request_context import RequestContext
    from src.rag.errors import RetrievalUnavailableError  # noqa: F401  # 保持与 T1 的依赖可见

    ctx = RequestContext(session_id="s-ctx", kb_id="kb1")
    ctx.tool_contexts = [
        RAGContext(
            content="营业总收入 100 亿元",
            source="年报.pdf",
            page=3,
            doc_id="d1",
            chunk_id="c1",
            score=0.9,
        )
    ]
    manager = _Recorder()
    partial: dict = {"text": "", "sources": []}

    with pytest.raises(RuntimeError):
        await _run(manager, ctx, _FailingGraph(RuntimeError("upstream 500")), partial)

    statuses = [p for t, p in manager.events if t == "status"]
    assert statuses, "必须发出降级告知"
    assert statuses[0]["stage"] == SSEInteractionTexts.STAGE_DEGRADED
    assert (
        statuses[0]["message"]
        == SSEInteractionTexts.DEGRADATION_TEXTS["llm_unavailable_with_context"]
    )
    tokens = [p["token"] for t, p in manager.events if t == "token"]
    assert tokens, "检索原文必须作为正文送出"
    assert "营业总收入 100 亿元" in tokens[0]
    assert partial["text"] == tokens[0]
    citations = [p for t, p in manager.events if t == "citation"]
    assert citations and citations[0]["source"] == "年报.pdf"


@pytest.mark.asyncio
async def test_llm_failure_without_context_gives_notice_only():
    """首 token 前失败且无检索结果 ⇒ 仅告知，不送空正文。"""
    from src.config.const import SSEInteractionTexts
    from src.infra.llm.request_context import RequestContext

    ctx = RequestContext(session_id="s-noctx", kb_id="kb1")  # tool_contexts 为空
    manager = _Recorder()
    partial: dict = {"text": "", "sources": []}

    with pytest.raises(RuntimeError):
        await _run(manager, ctx, _FailingGraph(RuntimeError("upstream 500")), partial)

    statuses = [p for t, p in manager.events if t == "status"]
    assert statuses[0]["message"] == SSEInteractionTexts.DEGRADATION_TEXTS[
        "llm_unavailable_no_context"
    ]
    assert [t for t, _ in manager.events if t == "token"] == []
    assert partial["text"] == ""


@pytest.mark.asyncio
async def test_llm_failure_after_token_is_stream_interrupted():
    """已产出后失败 ⇒ 只告知"回答未生成完整"，不重放检索原文。"""
    from src.config.const import SSEInteractionTexts
    from src.infra.llm.request_context import RequestContext

    ctx = RequestContext(session_id="s-mid", kb_id="kb1")
    ctx.tool_contexts = [
        RAGContext(
            content="旧原文", source="x.pdf", page=1, doc_id="d", chunk_id="c", score=0.5
        )
    ]
    manager = _Recorder()
    partial: dict = {"text": "", "sources": []}

    with pytest.raises(RuntimeError):
        await _run(
            manager,
            ctx,
            _TokenThenFailGraph(_chat_model_stream_item("部分回答"), RuntimeError("boom")),
            partial,
        )

    statuses = [p for t, p in manager.events if t == "status"]
    degraded = [p for p in statuses if p["stage"] == SSEInteractionTexts.STAGE_DEGRADED]
    assert degraded, "已产出后失败也必须告知"
    assert (
        degraded[0]["message"]
        == SSEInteractionTexts.DEGRADATION_TEXTS["llm_stream_interrupted"]
    )
    # 不得重放检索原文（旧原文不该出现在任何 token 里）
    assert all("旧原文" not in p["token"] for t, p in manager.events if t == "token")


@pytest.mark.asyncio
async def test_vector_store_failure_maps_to_vector_store_copy():
    """向量库不可用 ⇒ reason=vector_store_failed、文案=知识库暂不可用。"""
    from src.config.const import SSEInteractionTexts
    from src.infra.llm.request_context import RequestContext
    from src.rag.errors import RetrievalUnavailableError

    ctx = RequestContext(session_id="s-vs", kb_id="kb1")
    manager = _Recorder()
    partial: dict = {"text": "", "sources": []}

    with pytest.raises(RetrievalUnavailableError):
        await _run(
            manager,
            ctx,
            _FailingGraph(RetrievalUnavailableError("pg down")),
            partial,
        )

    statuses = [p for t, p in manager.events if t == "status"]
    assert statuses[0]["message"] == SSEInteractionTexts.DEGRADATION_TEXTS[
        "vector_store_failed"
    ]


@pytest.mark.asyncio
async def test_error_event_carries_safe_copy_only_when_classified():
    """终态 error 的载荷：已归类给表内文案；未归类留空（前端回落通用文案，不泄露内部文本）。"""
    from src.config.const import SSEInteractionTexts
    from src.infra.llm.request_context import RequestContext
    from src.services.turn_runner import _run_with_finalize

    class _StatusErr(Exception):
        def __init__(self, code: int) -> None:
            super().__init__(f"http {code}")
            self.status_code = code

    async def _drive(exc: BaseException) -> dict:
        ctx = RequestContext(session_id="s-err")
        manager = _Recorder()
        svc = Mock()
        svc.save_assistant_async = AsyncMock()
        svc.chat_manager = Mock()
        svc.chat_manager.add_message_async = AsyncMock()

        async def builder() -> str:
            raise exc

        await _run_with_finalize(
            svc,
            "s-err",
            "",
            {"text": "", "sources": []},
            builder,
            manager,
            asyncio.Event(),
            lambda: None,
            ctx,
        )
        errors = [p for t, p in manager.events if t == "error"]
        assert errors, "异常路径必须发 error 终态"
        return errors[0]

    forbidden = await _drive(_StatusErr(403))
    assert forbidden["error"] == SSEInteractionTexts.DEGRADATION_TEXTS[
        "provider_unavailable"
    ]
    # 未归类：留空，前端回落通用文案（与改造前逐字一致，且不透出内部异常文本）
    assert (await _drive(ValueError("secret internal detail")))["error"] == ""
```

`_run_with_finalize` 需要 `asyncio.current_task()` 非空 —— 上面的 `_drive` 直接在测试协程里调用（pytest-asyncio 下已有当前任务），并且 `manager` 只需 `add_event`（`_Recorder` 已满足；`_run_with_finalize` 的 `finally` 还会调 `manager.unregister_if_current`，故给 `_Recorder` 补一个空实现）。

在 `_Recorder` 里补：

```python
    def unregister_if_current(self, session_id: str, task) -> None:
        return None
```

- [ ] **Step 2: 跑测试确认全红**

Run: `.venv/bin/python -m pytest tests/services/test_agent_service.py -q -k "degradation or failure or retrieved_original or vector_store or error_event_carries"`
Expected: FAIL —— 前 4 条断言 `status` 事件不存在（`IndexError`/`assert statuses`）；`test_error_event_carries_safe_copy_only_when_classified` 断言 `403` 的 `error` 载荷等于表内文案而实际是 `"http 403"`。

- [ ] **Step 3: `src/services/agent_service.py` 加 import 与两个 helper**

import 区（`from src.infra.llm.trace_context import ...` 一行之后）补：

```python
from src.infra import retry_policy
```

在 `_record_event`（第 380 行）之前插入两个模块级 helper：

```python
def _degradation_reason(exc: BaseException, ctx: RequestContext, produced: bool) -> str:
    """判定降级 reason（取值取自规格取值集合表，不得自造）。

    优先级：
    1. **已产出** ⇒ `llm_stream_interrupted`——流已开始输出就不能再声称"以下是
       已检索到的原文"（那部分用户已看到），也不重试（D4）；
    2. `classify_failure` 归类成功 ⇒ 用它的取值（`provider_unavailable` /
       `upstream_exhausted` / `vector_store_failed`）；
    3. 未归类 ⇒ 按本轮**是否已有检索结果**分 `llm_unavailable_with_context` /
       `llm_unavailable_no_context`。

    Args:
        exc: 触发的异常
        ctx: 请求上下文（`tool_contexts` 判定"是否已有检索结果"）
        produced: 本次是否已向用户产出过 token

    Returns:
        取值集合表内的 reason 字符串
    """
    if produced:
        return "llm_stream_interrupted"
    classified = retry_policy.classify_failure(exc)
    if classified is not None:
        return classified
    if ctx.tool_contexts:
        return "llm_unavailable_with_context"
    return "llm_unavailable_no_context"


def _emit_degradation(
    manager: StreamingRunManager,
    session_id: str,
    capture: _StreamCapture,
    ctx: RequestContext,
    partial_holder: dict | None,
    exc: BaseException,
    produced: bool,
) -> None:
    """失败时的降级发射（P2 / D6 / D9）：判定 → 留痕 → 经 status + 专用 stage 送达。

    动作在**本调用点**完成（D1 ③：不抽统一降级管理器）。三个分支见
    `_degradation_reason`；其中"有检索原文"这一支额外做两件事：
    先补发 citation（引用横条），再把原文作为 token 送进回答气泡 —— 并把
    `partial_holder["text"]` 设为该原文，使异常收尾落 `interrupted` 且**有内容**
    （spec 要求：不得落一条空内容却标记为正常完成的消息）。

    Args:
        manager: 事件缓冲（实时 SSE）
        session_id: 会话 ID
        capture: 流捕获容器（事件日志，随 process 持久化/历史回放）
        ctx: 请求上下文（`tool_contexts` 为检索原文来源）
        partial_holder: 共享 dict（有原文时更新 text/sources）
        exc: 触发的异常（供归类与留痕）
        produced: 本次是否已向用户产出过 token
    """
    reason = _degradation_reason(exc, ctx, produced)
    core_logging.log_event(Event.DEGRADED, reason=reason, err=str(exc))

    notice = SSEStatusEvent(
        stage=SSEInteractionTexts.STAGE_DEGRADED,
        message=SSEInteractionTexts.DEGRADATION_TEXTS[reason],
    )
    _record_event(capture, notice)
    manager.add_event(session_id, notice.type, notice.payload_for_buffer())

    if reason != "llm_unavailable_with_context":
        return

    for index, context in enumerate(ctx.tool_contexts, start=1):
        citation = SSECitationEvent(
            source=context.source,
            page=context.page,
            snippet=context.content[:200],
            score=context.score,
            index=index,
            kind=context.kind,
            tier=context.tier,
        )
        _record_event(capture, citation)
        manager.add_event(session_id, citation.type, citation.payload_for_buffer())
        if partial_holder is not None:
            partial_holder.setdefault("sources", []).append(
                {
                    "source": context.source,
                    "page": context.page,
                    "snippet": context.content[:200],
                    "kind": context.kind,
                    "index": index,
                    "tier": context.tier,
                }
            )

    passages = "\n\n".join(c.to_prompt_text() for c in ctx.tool_contexts)
    token = SSETokenEvent(token=passages)
    _record_event(capture, token)
    manager.add_event(session_id, token.type, token.payload_for_buffer())
    if partial_holder is not None:
        partial_holder["text"] = passages
```

- [ ] **Step 4: `src/services/agent_service.py` 在 `_run_generation` 的循环外加失败分支**

把第 711-738 行的 `try: async for ... finally:` 结构改为（**只加 `except` 分支**，循环体与 `finally` 逐字不动）：

```python
    try:
        async for item in graph.astream_events(
            initial_state, version=LangGraph.VERSION
        ):
            for event in _convert_event(item, capture):
                _record_event(capture, event)
                manager.add_event(session_id, event.type, event.payload_for_buffer())
                if isinstance(event, SSETokenEvent):
                    full_answer += event.token
                    if partial_holder is not None:
                        partial_holder["text"] = full_answer
                elif isinstance(event, SSECitationEvent) and partial_holder is not None:
                    # （既有逻辑，逐字不动）
                    partial_holder.setdefault("sources", []).append(
                        {
                            "source": event.source,
                            "page": event.page,
                            "snippet": event.snippet,
                            "kind": event.kind,
                            "index": event.index,
                            "tier": event.tier,
                        }
                    )
            tool_trace.consume(item)
            if abort_signal is not None and abort_signal.is_set():
                raise asyncio.CancelledError
    except Exception as e:  # noqa: BLE001
        # 失败降级（P2 / D6 / D9）：动作留本调用点；分类与取值来自 retry_policy + 规格表。
        # CancelledError 属 BaseException，不落此分支（取消语义由调用方收尾，不在本变更范围）。
        _emit_degradation(
            manager, session_id, capture, ctx, partial_holder, e, bool(full_answer)
        )
        raise
    finally:
        # （既有 finally 逐字不动）
        drain_task.cancel()
        watch_task.cancel()
        tool_trace.close()
        await asyncio.gather(drain_task, watch_task, return_exceptions=True)
        if partial_holder is not None:
            partial_holder["model_name"] = capture.model_used
```

- [ ] **Step 5: `src/services/turn_runner.py` 让终态 `error` 携带安全文案**

import 区补：

```python
from src.config.const import CHANNEL_DEFAULT, SSEInteractionTexts
from src.infra import retry_policy
```

（该文件现有 `from src.config.const import CHANNEL_DEFAULT` 一行，改为上面两行合并形式。）

在 `_run_with_finalize` 之前插入：

```python
def _error_copy(exc: BaseException) -> str:
    """终态 error 事件给前端的用户可见文案。

    已归类的降级 ⇒ 取取值集合表里的文案；未归类 ⇒ **空串** —— 前端据此回落
    其通用文案（与改造前逐字一致），这样既不会把内部异常文本透给用户
    （改造后前端会消费 `data.error`），也不会对可用性类错误给出"请重试"的误导。

    Args:
        exc: 收尾时捕获的异常

    Returns:
        文案字符串；未归类时为空串
    """
    reason = retry_policy.classify_failure(exc)
    if reason is None:
        return ""
    return SSEInteractionTexts.DEGRADATION_TEXTS.get(reason, "")
```

并把 `manager.add_event(session_id, "error", {"error": str(e)})` 改为：

```python
        manager.add_event(session_id, "error", {"error": _error_copy(e)})
```

- [ ] **Step 6: 跑测试确认全绿**

Run: `.venv/bin/python -m pytest tests/services/ tests/rag/ tests/config/ -q`
Expected: PASS（`tests/rag/test_temporal.py` 的 3 例与 `tests/services` 里依赖 `POSTGRES_PASSWORD` 的用例为环境性失败，属预期）。

- [ ] **Step 7: 闸门 + 提交**

Run: `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright`
Expected: 零新增。

```bash
git add src/services/agent_service.py src/services/turn_runner.py tests/services/test_agent_service.py
git commit -m "feat(degradation): LLM/向量库失败路径的降级动作与用户可见告知（P2）" -m "- _run_generation 失败分支：判定 reason（已产出→流中断；可归类→表内取值；否则按有无检索结果分 llm_unavailable_*）→ 记 degraded 事件 → 经 status + 专用 stage 送达文案
- 有检索原文时补发 citation 并把原文作为正文送出、同步 partial_holder（异常收尾落 interrupted 且有内容，不再出现空回答）
- turn_runner 终态 error 携带安全文案（未归类留空，由前端回落通用文案，避免内部异常文本外泄 + 避免对可用性类错误诱导重试）"
```

---

### Task 4: 前端消费链（`chat.html`）

**背景与已实测的现状**：`deploy/nginx/html/chat.html:2360` 的 `error` 分支**硬编码** `showError('连接异常，请重试')`、**不消费** `data.error`（`src/utils/sse.py` 的 `SSEErrorEvent` 载荷里有 `error` 字段，白传）。⇒ Task 3 后端已经给出正确文案，但没有消费方。

**Files:**
- Modify: `deploy/nginx/html/chat.html:1305-1318`（`renderStatusTag`）
- Modify: `deploy/nginx/html/chat.html:2354-2366`（`error` 分支）
- Modify: `deploy/nginx/html/chat.html` 的 `<style>` 段（新增降级标签样式）

**Interfaces:**
- Consumes: 后端 SSE `status` 事件的 `stage="degraded"`（T2 的 `STAGE_DEGRADED`）与 `error` 事件的 `error` 字段（T3 的 `_error_copy`）
- Produces: 无（叶子）

> ⚠️ 本文件是**原生 HTML/JS，无自动化测试**（既有 `deploy/nginx/html/` 无测试用例）。本任务的验证 = **逐条 grep 断言 + T5 的运行时冒烟**。这一缺口在 T5 验收里登记。

- [ ] **Step 1: 改 `error` 分支消费后端文案**

把 `chat.html` 第 2360 行：

```js
        showError('连接异常，请重试');
```

改为：

```js
        // 后端已归类时给出可区分文案（可用性类不含"重试"诱导）；
        // 未归类时 data.error 为空串 ⇒ 回落通用文案（与改造前逐字一致）
        showError(data.error || '连接异常，请重试');
```

- [ ] **Step 2: 在 `renderStatusTag` 里为降级 stage 加专用分支**

把 `renderStatusTag`（第 1305 行起）改为：

```js
function renderStatusTag(stage, message, detail) {
  closeThinkRow();
  if (stage === 'retrieve' || stage === 'web_search') {
    toolCallSeen = true;
    flushPendingAsPreamble();
    reclassifyNarrationAsPreamble();
  }
  if (!currentStatusGroup || !currentStatusGroup.isConnected) {
    currentStatusGroup = document.createElement('div');
    currentStatusGroup.className = 'pg-group';
    mountProcessElement(currentStatusGroup);
  }
  const div = buildStatusTag(stage, message, detail);
  // 降级告知（后端 reason 取值集合的可见项）：只作提示，不标记 toolCallSeen、
  // 不触发正文/旁白重分类（与 retrieve/web_search 的区别），后续可能仍有
  // 检索原文正文到达
  if (stage === 'degraded') {
    div.classList.add('status-tag-degraded');
  }
  currentStatusGroup.appendChild(div);
  scrollToBottom();
}
```

- [ ] **Step 3: 加样式**

该文件的配色统一走 `:root` 变量（第 13-31 行）。先在那里（`--shadow-lg` 一行之后）追加两个变量：

```css
    --degraded: #B45309;
    --degraded-bg: #FEF3C7;
```

再在 `.status-tag` 规则（第 542-547 行）**之后**追加：

```css
  .status-tag-degraded {
    color: var(--degraded);
    background: var(--degraded-bg);
  }
```

（`.status-tag` 的 `align-self` / `font-size` / `border-radius` / `padding` / 动画由基类提供，此处只覆盖配色 —— 与文件既有"基类管布局、修饰类管配色"的写法一致。）

- [ ] **Step 4: grep 断言（本任务唯一可自动化的验证）**

Run:
```bash
grep -n "showError(data.error || '连接异常，请重试')" deploy/nginx/html/chat.html \
 && grep -n "status-tag-degraded" deploy/nginx/html/chat.html \
 && grep -n "stage === 'degraded'" deploy/nginx/html/chat.html \
 && ! grep -n "showError('连接异常，请重试');" deploy/nginx/html/chat.html
```
Expected: 前三条各命中至少 1 行，最后一条 `!` 命中（即旧硬编码行已不存在）；整体退出码 0。

- [ ] **Step 5: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(web): 前端消费降级文案并按 degraded stage 呈现（P2）" -m "- error 分支改为消费后端 data.error（未归类留空则回落通用文案，与改造前逐字一致）
- renderStatusTag 为 degraded stage 单独加样式类，不标记 toolCallSeen、不触发正文/旁白重分类
- 该文件无自动化测试，验证方式为 grep 断言 + 验收阶段运行时冒烟（已在计划中登记缺口）"
```

---

### Task 5: P2 验收（控制方自跑，不派子代理）

**背景**：本仓约定 —— "产出是实测证据"的验收由控制方自己做（`feedback-sdd-long-verification`）。P1 的教训：结构性断言**必要但不充分**（P1 曾带着"超时配置全对但完全不生效"的 Critical 被宣告完成），所以本任务**必须**做运行时冒烟。

**Files:**
- 不改代码；产出证据 + 回填文档

- [ ] **Step 1: 静态检查 + 机检**

Run:
```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright
python docs/tmp/check-change-consistency.py docs/openspec/changes/timeout-and-degradation-hardening
openspec validate timeout-and-degradation-hardening
```
Expected: ruff/pyright **零新增**（全树仍有 P1 前就存在的 `engine.py` UP047 + pyright 4 处存量）；一致性机检 **0 命中**；`openspec validate` valid。

- [ ] **Step 2: 非 DB 全量单测**

Run:
```bash
DASHSCOPE_API_KEY=test OPENAI_API_KEY=test .venv/bin/python -m pytest -q --ignore=tests/parsers --ignore=tests/infra/db --ignore=tests/scripts
```
Expected: 与 P1 基线同量级通过（P1 基线 **1609 passed / 5 failed**，5 个失败全是已知环境性/既有）；新增用例全部通过。

- [ ] **Step 3: 逐条核 `tasks.md` §2 验收（2.6–2.10）**

| 验收项 | 判据（可当场核对） |
|---|---|
| 2.6 | 稠密路失败 → 检索返回词法结果，且 `hybrid done` 的 `dense_status=failed`（**非** `empty`）：`test_hybrid_dense_failure_degrades_to_lexical_only` |
| 2.7 | 有检索结果 → 用户收到检索原文 + 告知（`test_llm_failure_with_context_delivers_retrieved_original`）；无检索结果 → 仅告知且不落空内容 + complete（`test_llm_failure_without_context_gives_notice_only` + Step 5 冒烟断言） |
| 2.8 | `provider_unavailable` 文案不含重试语义；401/403 走同一路径（`test_provider_unavailable_copy_does_not_lure_retry` + `test_classify_failure_maps_http_status_codes`）；`upstream_exhausted` 可提示重试 |
| 2.9 | `degraded` 事件在 `EVENT_SPECS` 且带 `reason`；取值 ⊆ 规格取值集合（`test_degraded_event_registered_with_reason_field` + `test_classify_failure_outputs_stay_inside_vocabulary`） |
| 2.10 | 「用户可见 = 是」的取值都有通道：`STAGE_DEGRADED` + `renderStatusTag` 分支 + error 分支消费 `data.error`（Step 4 的 grep 断言） |

- [ ] **Step 4: 前端通道 grep 断言**

Run: Task 4 Step 4 的四条 grep。
Expected: 全过。

- [ ] **Step 5: 运行时冒烟（必须做，且按 trace_id 回查日志）**

在**同一工具调用内**起服务 → 发请求 → 停服务（本仓已知约束：`&` 起的服务活不过一次工具调用）。三档：

- **A 正常档**（默认配置，强制检索的对比型问题）→ 期望 `done=1 / error=0`，且日志有 `hybrid done ... dense_status=ok sparse_status=ok`、**无** `degraded` 事件。
- **B 首 token 前失败**（`env MAIN_TIMEOUT_READ=0.001` 起服务）→ 期望 SSE 里有 **`event: status` 且 `stage="degraded"`**、`event: error` 的 `data.error` 为表内文案（**非**空、**非**「连接异常，请重试」）；日志有 `[degradation] degraded reason=llm_unavailable_*`；**数据库里不得出现"空 content + status=complete"的 assistant 行**。
- **C rerank 超时**（`env RERANK_TIMEOUT_READ=0.001` + 强制检索的问题）→ 期望 `rerank failed attempts=1` → `rerank done scored=fallback` → 仍出答案（**P1 语义未回归**）。

取证命令（替换 `<TID>` 为该轮 SSE 里 `done`/`error` 事件带回的 trace_id）：

```bash
grep -h "<TID>" logs/app_*.log | grep -oE "(hybrid done|degraded|rerank done|rerank failed|retrieve done|model turn|format done).*" | cut -c1-150
```

逐档把**真实输出**贴进报告；任一条与期望不符即 FAIL，**停下来登记而不是绕过**。

- [ ] **Step 6: 写验收报告并登记缺口**

报告写到 `.superpowers/sdd/2026-10-16-timeout-and-degradation-p1/` 同级的 P2 台账目录（与 P1 同构）：`.superpowers/sdd/2026-10-16-timeout-and-degradation-p2/progress.md`，内容含：每档冒烟的**真实输出**、`tasks.md` 2.6–2.10 的逐条判据、以及**如实登记**：
- 前端（`chat.html`）**无自动化回归守卫**（只能靠 grep + 冒烟）；
- **`llm_unavailable_with_context` 只被单测覆盖**（冒烟里把模型置于"先检索成功、再在生成时失败"的状态不可稳定构造）；
- `embedding_failed` 的**稠密路失败**在冒烟里不可稳定构造（需使查询向量化或 pgvector 报错）。

- [ ] **Step 7: 提交文档回填**

把 §2 的验收结论与上述缺口登记写进 `tasks.md` §2 的勾选状态（**只勾切实完成的**），并提交：

```bash
git add docs/openspec/changes/timeout-and-degradation-hardening/tasks.md
git commit -m "docs(openspec): timeout-and-degradation-hardening P2 验收结论与缺口登记"
```

---

## 与后续阶段/其他 change 的边界（写清以免被当成漏做）

- **P3（idle 保险丝）不做**：`DEGRADATION_REASONS` 里的 `generation_idle` 只**登记取值**，不接线（P3 落地）。
- **`tasks.md` §4 的其余项不做**：`best-practices.md` §11 状态行、`requirements_pool.md` A-03/A-05 收尾、两份 env 模板（本阶段**不新增 env 键**）、`embedding`/`rerank` 单阶段耗时、分位回填（D11 标着"待校准"）。
  - 例外：`logging-rules.md` 的登记（`dense_status`/`sparse_status` + `degraded` 值域）**在 T1/T2 内做**（属于本阶段新增值的必要登记，先做不欠账）。
- **rerank 两行不改**：既有事件名 `rerank failed` / `rerank timeout` 已表达原因，**不新增事件、不加 `reason`**。
- **评测链（`src/cli/**`、`RAGAS_*`）不动**：403 的额度/鉴权细分判据归 `deepeval-bridge-eval`。
- **`turn-provenance-observability` 共享文件注意**（`agent_service.py` / `log_events.py` / `log_event_specs.py` / `const.py`）：动这四个文件前先看它是否在途（`tasks.md` 0.5）。

## Self-Review（写完后的自查记录）

**1. Spec 覆盖**：`degradation-policy` 的 6 条 requirement —— 每依赖降级动作（T1 检索两路 / T3 LLM+向量库）、留痕唯一口径（T1+T2 的 `degraded` 事件）、取值集合（T2）、可见且不诱导重试（T2+T3+T4）、不引入 fallback（本阶段**无任何 fallback 代码**，符合）、生成任务级兜底（**P3**，本计划显式排除）。`hybrid-retrieval` 的 ADDED 要求 —— 单路失败降级 + 三态状态字段 + 两路皆失败按失败处置（T1 全覆盖）。`tasks.md` §2 的 2.1–2.5 对应 T1/T2/T3/T4，2.6–2.10 在 T5 逐条核。

**2. 占位物扫描**：无 TBD/TODO；每个代码步骤都给了可直接落盘的实现与真实签名；无"同 Task N"式引用。

**3. 类型/命名一致性**（跨任务）：
- `RetrievalUnavailableError`：T1 建 → T2 导入分类 → T3 断言传播，名一致。
- `classify_failure`：T2 定义（`exc: BaseException -> str | None`）→ T3 两处消费（`_degradation_reason` / `_error_copy`），签名一致。
- `STAGE_DEGRADED` / `DEGRADATION_REASONS` / `DEGRADATION_TEXTS`：T2 定义 → T3 取文案、T4 用 `'degraded'` 字面量匹配（JS 无法 import Python，已注明该处为约定字符串而非导入）。
- `Event.DEGRADED` 字段 `("reason", "err")`：T1 登记 → T1/T3 调用处只传这两个键，一致。
- `_emit_degradation` / `_degradation_reason` / `_error_copy`：T3 内部定义与消费，名一致。
- `dense_status` / `sparse_status`：T1 生产（`_path_status`）→ T1 事件字段登记 → T5 冒烟 grep，一致。

**4. 已识别的残余风险（已登记，非占位）**：`serialize_process` 对"status 之后紧跟 token"的分拣行为未在本计划中验证 —— T3 Step 6 的 with-context 用例只断言 `partial_holder["text"]`，**未断言落库后的 purified 正文**；该行为由 T5 的 B 档冒烟（查库无空 content + complete）间接覆盖。
