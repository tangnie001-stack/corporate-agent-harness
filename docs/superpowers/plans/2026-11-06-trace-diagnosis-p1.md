# trace-diagnosis P1（证据内核）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供一个可单测的 `diagnose(trace_id)` 内核——给定 trace，产出「根因枚举 + 证据清单 + 可校验的断言引用」，不接任何入口。

**Architecture:** 证据源（Trace / 本地日志）→ 事实模型 → 判定（纯函数）→ 报告 + 引用校验。所有判定在代码里，模型不参与本阶段。上层（bot 入口、告警定位器、落库）留到 P2/P3。

**Tech Stack:** Python 3.11+ / pytest / Langfuse 只读客户端（`src/cli/eval_trace_io.py`）/ alembic / SQLAlchemy

**Spec:** `docs/openspec/changes/trace-diagnosis/proposal.md`、`design.md`（D2/D5/D6/D9/D11）、`specs/trace-diagnosis/spec.md`

## Global Constraints

- 所有测试运行前加 `POSTGRES_HOST=localhost`（宿主侧约定）。
- 判定只用有硬证据的类别；证据不足一律 `undetermined`，不得推测。
- `score_kind != "rerank"` 时禁止进入「低相关」判定。
- 报告每条断言必须引用存在的 `evidence_id`；校验失败降级为「证据清单 + 未判定」。
- 只读：不得写任何业务数据、不得触发重放。
- 新配置一律放 `src/config/settings.py`，阈值首版标注"未标定"。

---

### Task 1: `messages` 增加 `trace_id`

**Files:**
- Modify: `src/infra/db/models/chat.py:33-57`
- Modify: `src/infra/db/repos/chat_repo.py:127-150`
- Create: `alembic/versions/0003_message_trace_id.py`
- Test: `tests/infra/test_message_trace_id.py`

**Interfaces:**
- Produces: `MessageModel.trace_id: str | None`；`chat_repo.save_message(msg)` 从 `RequestContext` 或 `msg.trace_id` 取值写入。

- [ ] **Step 1: 写失败测试**

```python
# tests/infra/test_message_trace_id.py
from src.infra.db.models.chat import MessageModel


def test_message_model_has_trace_id_column():
    assert "trace_id" in MessageModel.__table__.columns
    col = MessageModel.__table__.columns["trace_id"]
    assert col.nullable is True  # 存量行不回填
```

- [ ] **Step 2: 运行确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/infra/test_message_trace_id.py -q`
Expected: FAIL（KeyError: 'trace_id'）

- [ ] **Step 3: 最小实现**

在 `MessageModel` 的 `model_name` 之后加：

```python
    trace_id: Mapped[str | None] = mapped_column(
        String(64), comment="产生该消息的请求 trace_id；存量行不回填"
    )
```

迁移 `0003_message_trace_id.py`（照 `0002_kb_domain.py` 的写法）：

```python
def upgrade() -> None:
    op.add_column("messages", sa.Column("trace_id", sa.String(64), nullable=True))
    op.create_index("ix_messages_trace_id", "messages", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_trace_id", table_name="messages")
    op.drop_column("messages", "trace_id")
```

- [ ] **Step 4: 跑测试**

Run: `POSTGRES_HOST=localhost pytest tests/infra/test_message_trace_id.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/infra/db/models/chat.py alembic/versions/0003_message_trace_id.py tests/infra/test_message_trace_id.py
git commit -m "feat(db): messages 增加 trace_id（不迁移存量）"
```

---

### Task 2: 诊断配置项

**Files:**
- Modify: `src/config/settings.py`（`TOP_K_RERANK` 附近）
- Test: `tests/config/test_diag_settings.py`

**Interfaces:**
- Produces: `settings.DIAG_SCORE_FLOOR`、`DIAG_SCORE_GAP`、`DIAG_TRACE_LOOKBACK_ROUNDS`、`DIAG_MAX_REPRESENTATIVE_TRACES`

- [ ] **Step 1: 写失败测试**

```python
from src.config import settings


def test_diag_thresholds_have_defaults():
    assert settings.DIAG_SCORE_FLOOR == 0.35   # 首版未标定
    assert settings.DIAG_SCORE_GAP == 0.05
    assert settings.DIAG_TRACE_LOOKBACK_ROUNDS == 2
    assert settings.DIAG_MAX_REPRESENTATIVE_TRACES == 3
```

- [ ] **Step 2: 运行确认失败** → `AttributeError`

- [ ] **Step 3: 实现**

```python
# 诊断：低相关判定的分数下界（首版经验值，未按 KB 标定）
DIAG_SCORE_FLOOR: float = float(os.getenv("DIAG_SCORE_FLOOR", "0.35"))
# 诊断：top1 与 p50 的最小间距，小于此值视为"整体平庸"
DIAG_SCORE_GAP: float = float(os.getenv("DIAG_SCORE_GAP", "0.05"))
# 诊断：允许向前追溯的最大轮数（禁止链式）
DIAG_TRACE_LOOKBACK_ROUNDS: int = int(os.getenv("DIAG_TRACE_LOOKBACK_ROUNDS", "2"))
# 诊断：一次告警最多诊断的代表 trace 数
DIAG_MAX_REPRESENTATIVE_TRACES: int = int(
    os.getenv("DIAG_MAX_REPRESENTATIVE_TRACES", "3")
)
```

- [ ] **Step 4/5: 跑测试 + 提交**（同上格式）

---

### Task 3: 事实模型与根因枚举

**Files:**
- Create: `src/rca/diagnose/__init__.py`
- Create: `src/rca/diagnose/models.py`
- Test: `tests/rca/test_models.py`

**Interfaces:**
- Produces:
  - `class RootCause(str, Enum)`: `NOT_RETRIEVED / LOW_RELEVANCE / CONTEXT_TRUNCATED / NOT_CITED / KB_STALE / UNDETERMINED`
  - `@dataclass(frozen=True) RetrievalCall`: `call_index:int, query:str, top_k:int, iteration:int, result_count:int, context_count:int, score_kind:str, scores:tuple[float,...], chunk_ids:tuple[str,...]`
  - `@dataclass(frozen=True) Evidence`: `id:str, kind:str, locator:str, summary:str`
  - `@dataclass(frozen=True) DiagnosisReport`: `trace_id:str, root_cause:RootCause, evidence:tuple[Evidence,...], assertions:tuple[tuple[str, tuple[str,...]], ...], next_actions:tuple[str,...], basis_rounds:int`

- [ ] **Step 1: 写失败测试**

```python
from src.rca.diagnose.models import Evidence, RootCause


def test_root_cause_enum_is_closed():
    assert {c.value for c in RootCause} == {
        "not_retrieved", "low_relevance", "context_truncated",
        "not_cited", "kb_stale", "undetermined",
    }


def test_evidence_requires_id_and_locator():
    e = Evidence(id="E1", kind="trace", locator="retrieve_kb#1", summary="result=46 ctx=4")
    assert e.id == "E1" and e.locator.startswith("retrieve_kb")
```

- [ ] **Step 3: 实现**（dataclass + Enum，全部 frozen；字段带行内注释）

- [ ] **Step 4/5: 跑测试 + 提交**

---

### Task 4: `TraceSource` — 从 trace 还原检索事实

**Files:**
- Create: `src/rca/diagnose/sources.py`
- Test: `tests/rca/test_trace_source.py`

**Interfaces:**
- Consumes: `src/cli/eval_trace_io.fetch_trace(trace_id, host)`
- Produces: `class TraceSource: def fetch(self, trace_id: str) -> tuple[list[RetrievalCall], list[Evidence]]`；无检索快照时抛 `SourceEmptyError`（复用 `src/rca/sources/base.py`）

- [ ] **Step 1: 写失败测试**（用假 trace 对象，不连网）

```python
from types import SimpleNamespace
from src.rca.diagnose.sources import TraceSource
from src.rca.sources.base import SourceEmptyError
import pytest


def _trace(rc):
    return SimpleNamespace(
        id="trace_x", metadata={"retrieval_contexts": rc}, observations=[]
    )


def test_parses_retrieval_counts_and_scores():
    src = TraceSource(fetcher=lambda tid: _trace([
        {"call_index": 1, "query": "q", "top_k": 5, "iteration": 1,
         "result_count": 46, "context_count": 4, "score_kind": "rerank",
         "contexts": [{"chunk_id": "c1", "score": 0.76}, {"chunk_id": "c2", "score": 0.2}]}
    ]))
    calls, evidence = src.fetch("trace_x")
    assert calls[0].result_count == 46 and calls[0].context_count == 4
    assert calls[0].scores == (0.76, 0.2)
    assert evidence and evidence[0].id == "E1"


def test_missing_snapshot_raises_empty():
    src = TraceSource(fetcher=lambda tid: _trace([]))
    with pytest.raises(SourceEmptyError):
        src.fetch("trace_x")
```

- [ ] **Step 3: 实现要点**
  - 构造参数 `fetcher` 默认 `lambda tid: fetch_trace(tid, settings.LANGFUSE_HOST)`，便于测试注入。
  - 逐条 `retrieval_contexts` 转 `RetrievalCall`；`scores` 取 `contexts[*].score` 降序。
  - 每条调用产出一条 `Evidence(id=f"E{i+1}", kind="trace", locator=f"retrieve_kb#{call_index}", summary=f"result={n} ctx={m} kind={k}")`。

- [ ] **Step 4/5: 跑测试 + 提交**

---

### Task 5: `LocalLogSource` — 应用日志窗口取 trace

**Files:**
- Create: `src/rca/diagnose/logs.py`
- Test: `tests/rca/test_local_log_source.py`

**Interfaces:**
- Produces: `class LocalLogSource(log_dir: str): def errors_between(self, start: datetime, end: datetime) -> list[LogRow]`；`LogRow` 含 `ts, level, trace_id, event, raw`
- 行格式：`时间 | 级别 | trace_id(36) | session_id | channel | 位置 - 消息`

- [ ] **Step 1: 写失败测试**（临时目录造两行日志）

```python
from datetime import datetime, timedelta, UTC
from src.rca.diagnose.logs import LocalLogSource


def test_extracts_trace_id_from_error_lines(tmp_path):
    line = ("2026-11-06 03:12:41.500 | ERROR   | trace_abc                            "
            "| s1 | web | src.api.chat:h - [app] biz error code=E1\n")
    (tmp_path / "app_2026-11-06.log").write_text(line, encoding="utf-8")
    src = LocalLogSource(str(tmp_path))
    rows = src.errors_between(
        datetime(2026, 11, 6, 3, 0, tzinfo=UTC),
        datetime(2026, 11, 6, 4, 0, tzinfo=UTC),
    )
    assert len(rows) == 1 and rows[0].trace_id == "trace_abc"
    assert rows[0].event == "biz error"
```

- [ ] **Step 3: 实现要点**
  - 按日期遍历 `log_dir/app_*.log`，跳过窗口外的文件（文件名含日期）。
  - 逐行 `split("|")`，`len >= 6` 才解析；`level` 为 `ERROR`/`CRITICAL` 收集。
  - trace_id 去空白后必须匹配 `^trace_[0-9a-f-]{36}$`，否则记 `""`（保留，用于 D9）。
  - 不读 `error_*.log`（避免重复计数）。

- [ ] **Step 4/5: 跑测试 + 提交**

---

### Task 6: `judge` — 三态与四类判定

**Files:**
- Create: `src/rca/diagnose/judge.py`
- Test: `tests/rca/test_judge.py`

**Interfaces:**
- Produces: `def judge(calls: list[RetrievalCall], *, cited: bool, has_context_budget_high: bool, has_history_budget_exceeded: bool) -> tuple[RootCause, str]`（返回根因 + 人可读依据）

- [ ] **Step 1: 写失败测试（六例）**

```python
from src.rca.diagnose.judge import judge
from src.rca.diagnose.models import RetrievalCall, RootCause


def call(**kw):
    base = dict(call_index=1, query="q", top_k=5, iteration=1,
                result_count=10, context_count=5, score_kind="rerank",
                scores=(0.7, 0.6), chunk_ids=("c1",))
    base.update(kw)
    return RetrievalCall(**base)


def test_no_call_means_not_retrieved():
    assert judge([], cited=False, has_context_budget_high=False,
                 has_history_budget_exceeded=False)[0] is RootCause.NOT_RETRIEVED


def test_low_scores_with_gap_means_low_relevance():
    c = call(scores=(0.20, 0.19), result_count=30, context_count=5)
    assert judge([c], cited=False, has_context_budget_high=False,
                 has_history_budget_exceeded=False)[0] is RootCause.LOW_RELEVANCE


def test_fallback_scores_never_low_relevance():
    c = call(scores=(0.10, 0.09), score_kind="fallback")
    assert judge([c], cited=False, has_context_budget_high=False,
                 has_history_budget_exceeded=False)[0] is not RootCause.LOW_RELEVANCE


def test_truncated_context():
    c = call(result_count=46, context_count=4)
    assert judge([c], cited=True, has_context_budget_high=True,
                 has_history_budget_exceeded=False)[0] is RootCause.CONTEXT_TRUNCATED
```

- [ ] **Step 3: 实现判定顺序（必须按此序，后者依赖前者）**

1. `calls == []` → `NOT_RETRIEVED`
2. 存在 `score_kind == "rerank"` 且 `max(scores) < DIAG_SCORE_FLOOR` 且 `max - p50 < DIAG_SCORE_GAP` → `LOW_RELEVANCE`
3. `context_count < result_count` 且 `has_context_budget_high` → `CONTEXT_TRUNCATED`
4. `cited is False` 且 `max(scores) >= DIAG_SCORE_FLOOR` → `NOT_CITED`
5. 其余 → `UNDETERMINED`（依据写"证据不足：<缺什么>"）

- [ ] **Step 4/5: 跑测试 + 提交**

---

### Task 7: 报告与 `evidence_id` 校验

**Files:**
- Create: `src/rca/diagnose/report.py`
- Test: `tests/rca/test_report_validator.py`

**Interfaces:**
- Produces: `def validate(report: DiagnosisReport) -> DiagnosisReport`（校验失败返回同一 trace 的 `UNDETERMINED` 报告，断言清空）

- [ ] **Step 1: 写失败测试**

```python
from src.rca.diagnose.models import DiagnosisReport, Evidence, RootCause
from src.rca.diagnose.report import validate


def _r(assertions):
    return DiagnosisReport(
        trace_id="t", root_cause=RootCause.NOT_CITED,
        evidence=(Evidence("E1", "trace", "retrieve_kb#1", "ok"),),
        assertions=assertions, next_actions=(), basis_rounds=0,
    )


def test_dangling_reference_degrades():
    out = validate(_r((("模型没有引用上下文", ("E9",)),)))
    assert out.root_cause is RootCause.UNDETERMINED and out.assertions == ()


def test_valid_reference_survives():
    out = validate(_r((("模型没有引用上下文", ("E1",)),)))
    assert out.root_cause is RootCause.NOT_CITED
```

- [ ] **Step 3: 实现**：遍历断言引用的 id 是否都在 `{e.id}`；任一缺失 → 返回降级报告（保留 `evidence`，清空 `assertions`，`root_cause=UNDETERMINED`，`next_actions=("补齐证据后重新诊断",)`）。

- [ ] **Step 4/5: 跑测试 + 提交**

---

### Task 8: `pipeline.diagnose`

**Files:**
- Create: `src/rca/diagnose/pipeline.py`
- Test: `tests/rca/test_pipeline.py`

**Interfaces:**
- Produces: `def diagnose(trace_id: str, *, trace_source=None, log_source=None) -> DiagnosisReport`
- 行为：trace 无快照 → `UNDETERMINED`（"无检索快照"）；日志源 `failed` → `UNDETERMINED`（"日志源不可用"），**不得**输出"未落到应用层"

- [ ] **Step 1: 写失败测试（两个失败路径 + 一个正常路径）**

```python
from src.rca.diagnose.pipeline import diagnose


def test_trace_without_snapshot_is_undetermined():
    class Empty:
        def fetch(self, tid):
            from src.rca.sources.base import SourceEmptyError
            raise SourceEmptyError("no_snapshot")
    rep = diagnose("t", trace_source=Empty(), log_source=None)
    assert rep.root_cause.value == "undetermined"


def test_log_source_failure_is_not_untraced_conclusion():
    class Boom:
        def errors_between(self, a, b):
            from src.rca.sources.base import SourceFailedError
            raise SourceFailedError("unreachable")
    rep = diagnose("t", trace_source=None, log_source=Boom())
    assert rep.root_cause.value == "undetermined"
    assert "日志源不可用" in rep.assertions[0][0]
```

- [ ] **Step 3: 实现**：空源 → 跳过该段；异常按上表映射；产出 `DiagnosisReport` 后过 `validate()`。

- [ ] **Step 4/5: 跑测试 + 提交**

---

### Task 9: CLI 与手工验收

**Files:**
- Create: `src/cli/diagnose.py`
- Test: 手工（无单测）

**Interfaces:**
- 用法：`python -m src.cli.diagnose trace_xxx` → 打印根因、依据、证据清单

- [ ] **Step 1: 实现**（argparse，读 `--langfuse-host`、`--log-dir`，默认取 settings / `/data/logs`）
- [ ] **Step 2: 用真实 trace 跑一次**：`trace_afe90f74-ab71-4524-9e98-a9715eab8a6a`（已知有 4 条上下文）
- [ ] **Step 3: 提交**

```bash
git commit -m "feat(rca): trace-diagnosis P1 证据内核与 CLI"
```

---

## 自检（写完即跑）

- 规格覆盖：`spec.md` 四条要求 → Task 3-7 覆盖证据可复现、硬证据、引用校验、只读（P1 无写操作）。
- 占位符扫描：无 TBD / "类似上文"。
- 类型一致性：`RetrievalCall` 字段在 Task 3 定义，Task 4/6/8 引用同名；`Evidence.id` 全局唯一由各 Source 前缀保证（`E`）。

## P2 / P3（不在本计划内）

- P2：bot 入口（工具 + skill）、报告落库与详情页、脱敏口径对齐。
- P3：告警定位器（窗口 → 聚类 → 代表 trace）、nginx trace 头落地、灰度开关。
