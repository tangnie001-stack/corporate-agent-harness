# timeout-and-degradation-hardening P4（可观测与文档同步）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P1/P2/P3 落地的**超时/阈值取值**从"拍脑袋的保守值"变成"有本地实测分位支撑的值"，并把这四段（P1–P3）的结论同步进文档与两份 env 模板。

**Architecture:** 分三件：① **补单阶段耗时**（embedding / rerank 现在被并进 `retrieve done` 的合计里，无法为二者单独定值）；② **本地真实调用取分位**并回填 `design.md` D11 取值表的「待校准」项；③ **文档同步**（`best-practices.md` §11 / `requirements_pool.md` A-03·A-05 / `.env.template` + `.env.example` 两份模板 / `deploy-runbook.md` 如涉及）。

**Tech Stack:** Python 3.12 / loguru 事件注册表（`Event` + `EVENT_SPECS`，import 期一致性校验）/ pytest / 宿主直连 uvicorn + 真实 dashscope 调用（冒烟与测量）/ `awk` 取分位。

**Spec:** `docs/openspec/changes/timeout-and-degradation-hardening/design.md`（**D11 取值表**「待校准」项、**D2** 配置归属、**D17** 键名归属）、`.../tasks.md` §4。

## Global Constraints

- **本 plan 不含 §4.4（新 ADR 建档）**：它阻塞在并发 change `deepeval-bridge-eval`（其 design 明写要"追加新 ADR 取代 ADR-0024"；截至本阶段它仍是 **0/62 任务**）⇒ `docs/adr` 的最大编号仍是 `0024`，抢号会返工（`check_adr` 要求编号连续、重编号须与合并同一提交）。**留在 §4.4 未勾**。
- 单阶段耗时 SHALL **只加数值字段、不新增事件**（两处落点都已有合适的既有事件）：`search result`（前缀 `db`，`pg_store.dense_search` 内）加 `embedding_ms`；`rerank done`（前缀 `retrieval`，`retrieval.rerank_results` 内）加 `latency_ms`。
- `EVENT_SPECS` 的字段元组 SHALL 同步（它"不参与运行时校验"但属文档口径；先例：P2 的 `hybrid done` 加 `dense_status/sparse_status`、P3 的 `degraded` 加 `idle_s/tier`）。
- 数值字段**不需要**登记 `docs/agents/logging-rules.md`（该文件只登记"事件/前缀/级别与**非代码可读的值域**"，如 `scored` / `density_status` / `tier` 这类枚举；`latency_ms` 先例已存在且未登记）。
- **不新增 env 键**（本阶段无新配置）；**不改** `src/cli/**`、`src/chat/streaming.py`、`src/services/turn_runner.py`、`deploy/nginx/html/chat.html`。
- **本仓禁止三元表达式**；改动须过 `ruff check` / `ruff format --check` / `pyright`（全树 5 处存量告警不是回归）。
- **验收边界**：worktree 内跑**非 DB 单测 + ruff + pyright**（`tests/parsers` 缺 gitignored `data/`；`tests/cli/test_generate_transforms.py` 的 1 failed 是基线既有 ragas 漂移）。T1 的 embedding 用例须**写成 DB-free**（假 repo），以便在 worktree 内也能显式跑（`tests/infra/db/` 在批量跑里被 `--ignore`）。
- **一致性机检的坑（P3 踩过一次）**：改变化工件时**别引入新的 `<n>s` 秒数**（机检 C5 要求 tasks 里的 `<n>s` 必须出现在 design 唯一真源里）；要写秒数请用中文"X 秒"。
- 工作目录：`/root/code/corporate_rag-tdh-p3-p4`（worktree，分支 `feat/tdh-p3-p4`）；Python 一律 `.venv/bin/python`。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `src/infra/db/vector_store/pg_store.py` | 改 | `dense_search` 计 embedding 调用耗时 → `search result` 事件加 `embedding_ms` |
| `src/rag/retrieval.py` | 改 | `rerank_results` 计精排调用耗时 → `rerank done` 事件加 `latency_ms` |
| `src/core/log_event_specs.py` | 改 | 两个事件的字段元组同步 |
| `tests/rag/test_retrieval.py` | 改（追加） | 断言 `rerank done` 带 `latency_ms`（数值非负、可被捕获） |
| `tests/infra/db/test_pg_store_timing.py` | **新建** | DB-free：假 repo + 假 embed 断言 `search result.embedding_ms`（**放扁平目录** —— `tests/infra/db/` 无 `vector_store/` 子目录，与既有 `test_pg_vector_store_read.py` 同层） |
| `docs/openspec/changes/timeout-and-degradation-hardening/design.md` | 改 | 4.2 回填 D11 取值表（去掉「待校准」标记，写入实测分位与采样口径） |
| `docs/agents/best-practices.md` | 改 | 4.3：§11 相关行状态与现状对齐 |
| `docs/agents/requirements_pool.md` | 改 | 4.3：A-03 / A-05 收尾（状态与指向 change 的收口） |
| `.env.template` + `.env.example` | 改 | 4.3：**两份都要补** P1/P2/P3 新增的 env 键（`HTTP_TIMEOUT_*` / `MAIN_TIMEOUT_READ` / `CLASSIFY_*` / `SUMMARY_*` / `EMBEDDING_*` / `RERANK_*` / `MAIN_IDLE_*`） |
| `docs/openspec/changes/timeout-and-degradation-hardening/tasks.md` | 改 | 4.1–4.3 勾选 + 验收结论块 |

---

### Task 1: 补单阶段耗时（embedding / rerank）

**背景**：`retrieve done` 的 `latency_ms` 是**检索全链路合计**（含 embedding + dense + 词法 + 精排），无法为 `EMBEDDING_TIMEOUT_READ_QUERY`（15 秒）与 `RERANK_TIMEOUT_READ`（5 秒）单独定值 —— 这正是 D11 表里这两项标着"待校准"的原因。

**落点已核实（不要另选）**：
- embedding 的 HTTP 调用发生在 `src/infra/db/vector_store/pg_store.py:162` 的 `await asyncio.to_thread(self._embed_fn.embed_query, query)`；同函数稍后（`:167-173`）已有一条 `core_logging.log_event(Event.SEARCH_RESULT, kb_id=…, query_len=…, result_count=…, model=EMBEDDING_MODEL)` ⇒ **把耗时加在这条既有事件上**（`pg_store` 已 import `core_logging` / `Event`，但**未** import `time`）。
- 精排的调用发生在 `src/rag/retrieval.py` 的 `rerank_results` 里（`retry_policy.call_with_retry(reranker.rerank, …)`），同函数稍后已有一条 `log_event(Event.RERANK_DONE, doc_count=…, query_len=…, **_rerank_stats(…))` ⇒ **把耗时加在那条**（该文件未 import `time`）。

**Files:**
- Modify: `src/infra/db/vector_store/pg_store.py`、`src/rag/retrieval.py`、`src/core/log_event_specs.py`
- Test: `tests/rag/test_retrieval.py`（追加）、`tests/infra/db/test_pg_store_timing.py`（新建，**DB-free**）

**Interfaces:**
- Consumes: 无（本 plan 的第一个任务）
- Produces:
  - `search result` 事件字段增 `embedding_ms`（int，毫秒）
  - `rerank done` 事件字段增 `latency_ms`（int，毫秒）
  - 两者的 `EVENT_SPECS` 元组同步

- [ ] **Step 1: 写失败测试（精排，worktree 内可跑）**

追加到 `tests/rag/test_retrieval.py`（该文件已有 `_rerank_stats` 与多个 rerank 用例、并已用 `monkeypatch.setattr(retrieval, "log_event", …)` 的捕获手法）：

```python
def test_rerank_done_carries_stage_latency(monkeypatch):
    """`rerank done` 必须带本阶段耗时 `latency_ms`（D11 待校准项要它来定值）。

    计时只在精排调用**周围**，与 `retrieve done` 的全链路合计相区分。
    **注意**：`rerank_results` 在 `rerank done` **之后**还会落一条 `dedup done`
    ⇒ 捕获必须收成**列表再按事件过滤**，不能用"最后一条覆盖"的 dict
    （否则拿到的永远是 `dedup done` 的字段，断言必红）。
    """
    captured: list[tuple[Event, dict]] = []
    monkeypatch.setattr(
        retrieval, "log_event", lambda event, **fields: captured.append((event, fields))
    )
    results = [
        ChunkResult(id="a", content="A", metadata={}, distance=0.1),
        ChunkResult(id="b", content="B", metadata={}, distance=0.2),
    ]

    class _ReRanker:
        def rerank(self, docs, query):
            return [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.1},
            ]

    contexts = retrieval.rerank_results("资产负债率", results, _ReRanker())

    assert len(contexts) == 2
    rerank_done = [f for e, f in captured if e == Event.RERANK_DONE]
    assert rerank_done, "未落 rerank done 事件"
    assert "latency_ms" in rerank_done[0], "rerank done 未带 latency_ms"
    assert isinstance(rerank_done[0]["latency_ms"], int)
    assert rerank_done[0]["latency_ms"] >= 0
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/rag/test_retrieval.py -q -k stage_latency`
Expected: FAIL —— `AssertionError: rerank done 未带 latency_ms`。

- [ ] **Step 3: 实现（精排计时）**

`src/rag/retrieval.py` 顶部 import 区追加 `import time`（与既有 `import asyncio` / `import hashlib` 同组、按字母序放在 `hashlib` 之后）。然后把 `rerank_results` 的调用段改为：

```python
    docs = [r.content for r in results]
    used_fallback = False
    start = time.monotonic()
    try:
        reranked = retry_policy.call_with_retry(
            reranker.rerank,
            docs,
            query,
            attempts=RERANK_MAX_ATTEMPTS,
            initial_interval=RETRY_INITIAL_INTERVAL,
            backoff=RETRY_BACKOFF_FACTOR,
        )
    except Exception as e:  # noqa: BLE001
        log_event(
            Event.RERANK_FAILED,
            attempts=RERANK_MAX_ATTEMPTS,
            query=query,
            err=str(e),
        )
        used_fallback = True
        reranked = []
        for i, r in enumerate(results):
            if r.distance is not None:
                fallback_score = 1 - r.distance
            else:
                fallback_score = 0
            reranked.append({"index": i, "relevance_score": fallback_score})
    rerank_ms = int((time.monotonic() - start) * 1000)
```

并把底部那条事件补字段：

```python
    if contexts:
        log_event(
            Event.RERANK_DONE,
            doc_count=len(results),
            query_len=len(query),
            latency_ms=rerank_ms,
            **_rerank_stats(reranked, used_fallback),
        )
```

> ⚠️ `start` 变量名与同文件其它函数不冲突已核（`retrieval.py` 内无同名在作用域内复用）；若 ruff 报重名/未使用，按最小修正处理并登记。

- [ ] **Step 4: 跑测试确认绿**

Run: `.venv/bin/python -m pytest tests/rag/test_retrieval.py -q`
Expected: PASS（含既有 rerank 用例无回归）。

- [ ] **Step 5: 写失败测试（embedding，DB-free）**

新建 `tests/infra/db/test_pg_store_timing.py`（**扁平目录**：该目录无 `vector_store/` 子目录，与既有 `test_pg_vector_store_read.py` 同层）：

```python
"""`dense_search` 的 embedding 单阶段耗时（P4 / tasks 4.1）。

**故意 DB-free**：`PgVectorStore.__init__(chunk_repo, embed_fn)` 两个依赖都可注入
（已核实：`pg_store.py:67-86`），故用假 repo + 假 embed_fn 即可，使该用例在只跑
非 DB 单测的环境（worktree，批量跑里 `--ignore=tests/infra/db`）也能**显式**执行。
"""

import time

import pytest

from src.infra.db.vector_store import pg_store as pg_store_mod


class _FakeEmbedder:
    """假向量化器：睡一小会儿，便于断言耗时字段 ≥ 1ms。"""

    def embed_query(self, text: str) -> list[float]:
        time.sleep(0.01)
        return [0.0] * 4


class _FakeRepo:
    """假 repo：dense 检索返回空结果（本用例只关心计时字段）。"""

    async def search_dense(self, kb_id: str, vec: list[float], k: int):
        return []


@pytest.mark.asyncio
async def test_dense_search_reports_embedding_stage_latency(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(
        pg_store_mod.core_logging,
        "log_event",
        lambda event, **fields: captured.append(fields),
    )
    store = pg_store_mod.PgVectorStore(chunk_repo=_FakeRepo(), embed_fn=_FakeEmbedder())

    results = await store.dense_search("kb1", "资产负债率", 3)

    assert results == []
    assert captured, "dense_search 必须落一条 search result 事件"
    assert "embedding_ms" in captured[0], "search result 未带 embedding_ms"
    assert isinstance(captured[0]["embedding_ms"], int)
    assert captured[0]["embedding_ms"] >= 1, "假向量化器睡了 10ms，耗时字段应 ≥ 1ms"
```

> 若该用例因 `tests/infra/db/` 的批量 `--ignore` 而不会在全量跑里被执行，**这是预期的**（DB 边界）：请在本步**显式**跑它（见 Step 6）并在报告里注明"该用例不在批量跑范围内，已显式执行"。

- [ ] **Step 6: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/infra/db/test_pg_store_timing.py -q`
Expected: FAIL —— `AssertionError: search result 未带 embedding_ms`。

- [ ] **Step 7: 实现（embedding 计时）**

`src/infra/db/vector_store/pg_store.py` 顶部 import 区追加 `import time`（放在 `import asyncio` 之后）。把 `dense_search` 的取数段与事件段改为：

```python
        effective_k = min(k, MAX_QUERY_K)
        embed_start = time.monotonic()
        query_vec = await asyncio.to_thread(self._embed_fn.embed_query, query)
        embedding_ms = int((time.monotonic() - embed_start) * 1000)
        pairs = await self._repo.search_dense(kb_id, query_vec, effective_k)
        results = [
            row_to_chunk_result(row, distance=distance, dense_rank=rank)
            for rank, (row, distance) in enumerate(pairs)
        ]
        core_logging.log_event(
            Event.SEARCH_RESULT,
            kb_id=kb_id,
            query_len=len(query),
            result_count=len(results),
            embedding_ms=embedding_ms,
            model=EMBEDDING_MODEL,
        )
```

- [ ] **Step 8: 跑测试确认绿**

Run: `.venv/bin/python -m pytest tests/infra/db/test_pg_store_timing.py tests/rag/test_retrieval.py -q`
Expected: PASS。

- [ ] **Step 9: 同步 `EVENT_SPECS` 字段元组**

`src/core/log_event_specs.py`：

```python
    "rerank done": EventSpec(
        "rerank done",
        "retrieval",
        "info",
        (
            "doc_count",
            "query_len",
            "latency_ms",
            "score_max",
            "score_min",
            "score_p50",
            "scored",
        ),
    ),
```

```python
    "search result": EventSpec(
        "search result",
        "db",
        "info",
        ("kb_id", "query_len", "result_count", "embedding_ms", "model"),
    ),
```

- [ ] **Step 10: 全量回归 + 闸门 + 机检 + 提交**

Run:
```bash
DASHSCOPE_API_KEY=test OPENAI_API_KEY=test .venv/bin/python -m pytest -q --ignore=tests/parsers --ignore=tests/infra/db --ignore=tests/scripts
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright
.venv/bin/python docs/tmp/check-change-consistency.py docs/openspec/changes/timeout-and-degradation-hardening
```
Expected: 基线 **1650 passed / 1 failed（ragas 漂移）/ 2 skipped**，本次应 **≥1650 passed** 且失败仍是同一条；静态检查零新增；机检 **0 命中**。

```bash
git add src/infra/db/vector_store/pg_store.py src/rag/retrieval.py src/core/log_event_specs.py tests/rag/test_retrieval.py tests/infra/db/test_pg_store_timing.py
git commit -m "feat(observability): 补 embedding / rerank 单阶段耗时（P4 4.1）" -m "- search result 加 embedding_ms（pg_store.dense_search 包住 embed 调用计时）；rerank done 加 latency_ms（rerank_results 包住精排调用计时）
- 原先只有 retrieve done 的全链路合计，无法为 EMBEDDING_TIMEOUT_READ_QUERY / RERANK_TIMEOUT_READ 单独定值（D11 标着待校准的正是这两项）
- 只加数值字段、不新增事件；EVENT_SPECS 两个字段元组同步（数值字段无需登记 logging-rules 值域）
- 测试：rerank 用例在 tests/rag 内；embedding 用例专门写成 DB-free（假 repo/假 embed）以便 worktree 内显式跑"
```

---

### Task 2: 对照实测与 D11 回填（控制方自跑，不派子代理）

**背景**：D11 的取值表全部标着"待校准"，且 P3 的四档 idle 阈值同样来自规格的**保守值**。本任务用**本地真实调用**取分位，一次标定两组参数（超时值 + idle 阈值）。

**Files:**
- Modify: `docs/openspec/changes/timeout-and-degradation-hardening/design.md`（D11 取值表 + 「仍未定」段）
- 产出测量记录到本 plan 的 SDD workspace（`t5-acceptance.md` 或独立 `p4-measurement.md`）

**协议（必须照此执行，否则分位不可信）**

1. **起服务**（宿主直连 `.env`；同一工具调用内起停）：
   `env .venv/bin/python -m uvicorn src.main:app --port 8041 --host 127.0.0.1`
2. **样本**：**≥30 次真实请求**，覆盖三类问题（各 ≥10 次）：
   - 事实类（绑定 KB 的财务指标）⇒ 走检索；
   - 对比类（"A 和 B 分别是多少"）⇒ 走多轮检索；
   - 开放/宽问题（触发更多轮次）。
   其中**至少 10 次开 `deep_thinking=true`**（用于 `MAIN_IDLE_FIRST_THINKING_S` 与 `GAP_THINKING`）。
3. **采集**（每轮从日志取）：
   | 待标定项 | 数据来源（字段） | 取法 |
   |---|---|---|
   | `MAIN_TIMEOUT_READ` | `[agent] model turn … latency_ms` | 全样本分位（注意：该字段是**整轮**，非首字节；首字节分位另见下） |
   | 首字节/首事件延迟（支撑 `MAIN_IDLE_FIRST_*`） | 同一 trace 内**首个事件**的时间戳与该轮请求时间戳之差（日志时间戳相减） | 分位 |
   | 流间隔（支撑 `MAIN_IDLE_GAP_*`） | 同一 trace 内**相邻事件日志行**时间戳差的最大值 | 分位 |
   | `CLASSIFY` / `SUMMARY` | `[agent] model turn` 中 `model` 与主模型不同的那些轮（若无法区分，**如实登记为不可分离**，保留保守值） | 分位或标注 |
   | `EMBEDDING_TIMEOUT_READ_QUERY` | `[db] search result … embedding_ms`（**T1 新字段**） | 分位 |
   | `RERANK_TIMEOUT_READ` | `[retrieval] rerank done … latency_ms`（T1 新字段） | 分位 |
   | `RERANK_OUTER_TIMEOUT` | 同上（外层预算只需 > 内层总预算 + 余量） | 由内层推导 |
4. **取分位**：
   ```bash
   grep -h "\[retrieval\] rerank done" logs/app_*.log | grep -oE "latency_ms=[0-9]+" | cut -d= -f2 | sort -n \
     | awk '{a[NR]=$1} END{print "n="NR, "p50="a[int(NR*0.5)], "p95="a[int(NR*0.95)], "p99="a[int(NR*0.99)], "max="a[NR]}'
   ```
5. **回填规则（写进 design D11 的表述）**：取值 = **远大于 p99、明显小于现状 600 秒、且 < 传输层 300 秒**；**只朝"放宽"或"持平"方向调整**（除非实测 p99 远小于现值 ⇒ 可收紧，但须写明依据）；`main` 的**首字节**与**流间隔**分别对齐 `MAIN_IDLE_FIRST_*` / `MAIN_IDLE_GAP_*` 的**思考档位**。
6. **不做的事**：**不在生产做对照**（D11 + tasks 0.3）；**不拿本机历史日志**（stub 流量，`latency_ms=0~1`）；**不照搬 RAGAS 评测的数值**（离线批量场景）。

- [ ] **Step 1: 跑测量并把**原始分位表**写进 workspace**（含采样日期、样本数、问题分布、开思考比例）
- [ ] **Step 2: 回填 `design.md` D11**：给每一项标注"实测分位 → 取值"，去掉「待校准」；若某项**无法分离**（如 classify/summary），保留保守值并写明原因
- [ ] **Step 3: 同步 `tasks.md` 的 4.2 勾选与结论**（注意：**不要引入新的 `<n>s` 秒数**写法，用中文"X 秒"，否则被机检 C5 命中）
- [ ] **Step 4: 跑闸门 + 提交**
  ```bash
  .venv/bin/python -m src.cli.check_docs && .venv/bin/python docs/tmp/check-change-consistency.py docs/openspec/changes/timeout-and-degradation-hardening && openspec validate timeout-and-degradation-hardening
  git add docs/openspec/changes/timeout-and-degradation-hardening/design.md docs/openspec/changes/timeout-and-degradation-hardening/tasks.md
  git commit -m "docs(openspec): P4 4.2 本地实测分位回填 D11 取值表"
  ```

---

### Task 3: 文档同步（4.3，控制方自跑）

**Files:**
- Modify: `docs/agents/best-practices.md`（§11 四行状态）、`docs/agents/requirements_pool.md`（A-03 / A-05 收尾）、`.env.template` + `.env.example`（**两份**）、`docs/agents/deploy-runbook.md`（若涉及运维动作）、`.../tasks.md`（4.3 勾选）

- [ ] **Step 1: `.env.template` + `.env.example` 补键**（**两份都要改** —— 记忆 `project-root-env-templates-pair` 的教训）
  以 `src/config/settings.py` 里的 `os.getenv("<NAME>", …)` 为准逐键核对，至少覆盖：`HTTP_TIMEOUT_CONNECT/WRITE/POOL`、`MAIN_TIMEOUT_READ`、`CLASSIFY_TIMEOUT_READ`、`SUMMARY_TIMEOUT_READ`、`EMBEDDING_TIMEOUT_READ_QUERY/INGEST`、`RERANK_TIMEOUT_READ`、`RERANK_OUTER_TIMEOUT`、`RERANK_MAX_ATTEMPTS`、`MAIN_IDLE_FIRST_S`、`MAIN_IDLE_FIRST_THINKING_S`、`MAIN_IDLE_GAP_S`、`MAIN_IDLE_GAP_THINKING_S`、`MAIN_IDLE_POLL_INTERVAL_S`。
  **做法**：用 `grep -oE '^[A-Z_]+(?==)' src/config/settings.py`（或逐个 `grep -n 'os.getenv'`）拉出全量键名，与两份模板做**差集**，只补缺的（不要顺手重排模板）。
- [ ] **Step 2: `best-practices.md` §11 四行状态**：与本 change 的**现状**对齐（P1 超时/重试、P2 降级动作与文案、P3 idle 兜底、其余仍 ❌/⚠️ 的项要指向对应待办）
- [ ] **Step 3: `requirements_pool.md` A-03 / A-05 收尾**：状态收窄为"P1+P2+P3 已落地 / 余项（档 2、熔断 A-02、告警出口 H-12…）留 follow-up"，**不得**改动它们的编号或其它条目
- [ ] **Step 4: `deploy-runbook.md`**：若本 change 带来运维动作（新增 env 键 ⇒ 目标机 `.env` 需要/可选补），补一节说明；**改动前先读该文件现状**（记忆 `feedback-deploy-read-runbook`）
- [ ] **Step 5: 跑闸门 + 提交**
  ```bash
  .venv/bin/python -m src.cli.check_docs && .venv/bin/python docs/tmp/check-change-consistency.py docs/openspec/changes/timeout-and-degradation-hardening
  git add .env.template .env.example docs/agents/best-practices.md docs/agents/requirements_pool.md docs/agents/deploy-runbook.md docs/openspec/changes/timeout-and-degradation-hardening/tasks.md
  git commit -m "docs: P4 4.3 文档同步（env 模板两份 + best-practices §11 + requirements_pool A-03/A-05 + runbook）"
  ```

---

## 与后续阶段的边界（写清以免被当成漏做）

- **§4.4 新 ADR 不在本 plan**：阻塞在 `deepeval-bridge-eval`（0/62 任务）。它落地后本变更才取号建档（`check_adr` 要求编号连续、须与 `docs/adr/README.md` 索引同一提交）。
- **§5 遗留登记（5 项）不在本 plan**：档 2（取消/降级）、embedding 客户端复用、告警出口 `H-12`、`symptom_metrics` 扩容、403 额度/鉴权判别（归评测链）。
- **P3 收尾时留下的 2 条待清**（不属 §4，但如果顺手能做的就当 follow-up）：
  - `src/agents/tools/web_tools.py`（2 处）与 `src/infra/search/tavily_client.py`（4 处）的 `TIMING` 临时埋点（"316s 取证"遗留，记忆 `debug-instrumentation-316s` 记为定位后整块删除）——**建议登记 §5 或单开一次清理**。
  - 前序评审 park 的 4 条 Minor（P3 台账有裁定与判错代价）。

## Self-Review（写完后的自查记录）

**1. Spec/tasks 覆盖**：`tasks.md` §4 的 4.1 → T1；4.2 → T2；4.3 → T3；4.4 → **显式排除并写明阻塞原因**（本 plan 的 Global Constraints 第一条 + 边界节）。

**2. 占位物扫描**：T1 的两处实现给了逐字代码与逐字测试；T2 给了采集表、取分位命令、回填规则与"不做的事"清单；T3 给了逐文件动作与"先读现状"的前置。无 TBD/TODO。

**3. 类型/命名一致性**：`embedding_ms`（`search result`）与 `latency_ms`（`rerank done`）在 T1 的生产、`EVENT_SPECS`、测试三处同名；T2 的采集表用的正是这两个字段名 ✓；T2/T3 引用的 settings 键名与 P1/P3 交付的一致 ✓。

**4. 已识别的残余风险（登记，非占位）**：① `dense_search` 的计时**只覆盖 embedding 调用**（不含 pgvector 查询）—— 这是刻意的（要定的是 embedding 的 HTTP 超时），但报告里要写明；② 首字节/流间隔分位要从**日志时间戳相减**得到（不是现成字段），受同机墙钟影响（WSL 有偶发前推墙钟的记录）⇒ 报告里标注该口径的局限；③ `classify` / `summary` 的耗时可能**无法从 `model turn` 分离**（该事件不带 role 字段）⇒ 若分离不出，保留保守值并写明。
