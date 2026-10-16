# timeout-and-degradation-hardening P3（生成任务级 idle 兜底）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"主 agent 长时间不吐事件"这件事**被探测到并告知用户**（`reason=generation_idle`），同时**不中止生成**（档 1）—— 补上 P1/P2 之后唯一剩下的行为缺口。

**Architecture:** 把 `_silence_watchdog`（D-11 挂号待清的**临时取证埋点**：只打任务栈、不处置）**转正为业务层 idle 守卫**：判据沿用"**事件条数是否增长**"（比传输层 `read` 的字节间隔更贴用户感知 —— 模型只吐 tool_call 而不吐 token 时字节在动、用户无感），**同一机制两档阈值**（首事件前 / 首事件后）× **思考档位**（D15）。档 1 的动作 = 记一条 `degraded`（`reason=generation_idle`）+ 经**既有 `status` 事件 + 专用 stage** 送达文案；动作**留本调用点**（不抽降级管理器），且**不动控制流**。

**Tech Stack:** Python 3.12 / asyncio（`create_task` + `asyncio.sleep` + `cancel`）/ loguru 事件注册表 / pytest（`pytest.mark.asyncio`）/ `StreamingRunManager` 事件缓冲。

**Spec:** `docs/openspec/changes/timeout-and-degradation-hardening/design.md`（**D3** 层间区分 / **D12** 阈值按思考档分档 / **D15** 首 token 守卫与 idle 保险丝 = 同一机制两档阈值）、`.../specs/degradation-policy/spec.md`（「生成任务级的失控兜底」+「降级原因与用户文案的取值集合」）、`.../tasks.md` §3。

## Global Constraints

- 该保险丝作用于**生成任务**（主 agent 是否在吐事件）；**SHALL NOT** 与 SSE 订阅层的空闲收流混为一谈 —— 后者对活跃任务保持连接（`sse-subscribe` 既有语义），**本阶段不改**（`_subscribe_events(..., max_idle=None)` 必须原样保留）。
- **只做档 1**：记事件 + 用户可见文案；**不动控制流**（不取消、不降级、不改 status）。档 2（取消/降级，接 `task_registry`）登记为 follow-up（`tasks.md` 5.1）。
- 原因词 SHALL 用**自有降级事件**的取值 `reason = generation_idle`；**SHALL NOT 声称复用 `DelegateStopReason`**（其语义是「fork 结束原因」，而档 1 并不中止）。
- 文案 SHALL 取自取值集合表（`SSEInteractionTexts.DEGRADATION_TEXTS["generation_idle"]`），且为**过程**措辞，**SHALL NOT** 用"已超时"这类**终态**措辞（档 1 并不中止）。
- 用户可见降级文案 SHALL 经**既有 `status` 事件 + 专用 stage**（`SSEInteractionTexts.STAGE_DEGRADED`）送达；**SHALL NOT** 复用 `retrieve` / `web_search` 的 stage。
- **两档阈值都必须 < 传输层 `MAIN_TIMEOUT_READ`（180s）**，否则传输层先触发、该档**永不负责**（第三轮 B2/I1）⇒ 启动期 fail-fast 断言。
- 取值 SHALL 住 `src/config/settings.py`（自有键名、env 可覆盖），**SHALL NOT** 写进 `registry.yaml`（D2）。
- 新增事件字段/值域须同步 `src/core/log_event_specs.py` 的 `EVENT_SPECS` **与** `docs/agents/logging-rules.md`。
- 本仓 **禁止三元表达式**（`CLAUDE.md`）。
- **不改** `src/cli/**`、`src/chat/streaming.py`、`src/services/turn_runner.py`、`retry_policy.py`、`retrieval.py`。
- 验收边界：worktree 内跑**非 DB 单测 + `ruff` + `pyright`**；全树 5 处存量告警（`src/infra/db/engine.py:33` 的 `UP047` + pyright 4 处）**不是回归**；`tests/cli/test_generate_transforms.py` 的 1 failed 是**基线既有**（ragas API 漂移，P3 不碰 `src/cli/**`）。
- 工作目录：`/root/code/corporate_rag-tdh-p3-p4`（worktree，分支 `feat/tdh-p3-p4`）；Python 一律 `.venv/bin/python`。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `src/config/settings.py` | 改 | 四档 idle 阈值 + 轮询间隔（自有键名）；`validate_timeout_nesting()` 扩展出"idle 两档 < 传输层 read"的启动期断言 |
| `src/services/agent_service.py` | 改 | **删**临时取证埋点（`_SILENCE_WATCH_*` / `_dump_task_stacks` / `_silence_watchdog`）→ **新增** `_idle_thresholds()` + `_idle_guard()`；`_run_generation` 接线（传 `deep_thinking` / `capture`）并更新取消段 |
| `src/core/log_event_specs.py` | 改 | `degraded` 的字段元组追加 `idle_s` / `tier` |
| `docs/agents/logging-rules.md` | 改 | `degraded` 行补 `tier` 的非代码可读值域（`first` / `gap`） |
| `tests/config/test_settings.py` | 改（追加） | 四档默认值与启动期断言拦截 |
| `tests/services/test_idle_guard.py` | **新建** | 守卫本体单测（两档阈值 / 每段一条 / 缓冲清理即退出） |
| `tests/services/test_agent_service.py` | 改（追加） | 「idle 触发但**不中止生成**」端到端 + 3.5 结构性防回归 |

---

### Task 1: 四档 idle 阈值 + 启动期 fail-fast 断言

**背景**：规格把阈值写死为四值（首事件前 非思考 **60s** / 思考 **150s**；首事件后 **30s** / **60s**），并要求两档都 **< 传输层 `read` 180s**。P1 已经把"取值住 settings + 启动期 fail-fast"的范式立好了（`RERANK_*` 与 `validate_timeout_nesting()`）⇒ 本条**照抄该范式**。

**Files:**
- Modify: `src/config/settings.py`（生产链路超时段落 + `validate_timeout_nesting()`）
- Test: `tests/config/test_settings.py`（追加）

**Interfaces:**
- Consumes: 无
- Produces（T2/T3 依赖这些确切名字）：
  - `settings.MAIN_IDLE_FIRST_S: float = 60.0`
  - `settings.MAIN_IDLE_FIRST_THINKING_S: float = 150.0`
  - `settings.MAIN_IDLE_GAP_S: float = 30.0`
  - `settings.MAIN_IDLE_GAP_THINKING_S: float = 60.0`
  - `settings.MAIN_IDLE_POLL_INTERVAL_S: float = 5.0`
  - `settings.validate_timeout_nesting()` —— 现在同时校验"rerank 内层 < 外层"与"四档 idle < 传输层 read"

- [ ] **Step 1: 写失败测试**

追加到 `tests/config/test_settings.py`（该文件已导入 `pytest`，并已有 `test_rerank_timeout_nesting_*` 两条同型用例）：

```python
def test_idle_threshold_defaults_are_below_transport_read():
    """四档 idle 阈值默认值：两档都 < 传输层 read，且首事件前档不窄于事件后档。"""
    import src.config.settings as _s

    assert _s.MAIN_IDLE_FIRST_S == 60.0
    assert _s.MAIN_IDLE_FIRST_THINKING_S == 150.0
    assert _s.MAIN_IDLE_GAP_S == 30.0
    assert _s.MAIN_IDLE_GAP_THINKING_S == 60.0
    # 两档必须 < 传输层 read（否则传输层先触发、该档永不负责，D15）
    assert _s.MAIN_IDLE_FIRST_S < _s.MAIN_TIMEOUT_READ
    assert _s.MAIN_IDLE_FIRST_THINKING_S < _s.MAIN_TIMEOUT_READ
    assert _s.MAIN_IDLE_GAP_S < _s.MAIN_TIMEOUT_READ
    assert _s.MAIN_IDLE_GAP_THINKING_S < _s.MAIN_TIMEOUT_READ
    # 首事件前含排队 + prefill（开思考时更久）⇒ 不窄于事件后的间隔档
    assert _s.MAIN_IDLE_GAP_S < _s.MAIN_IDLE_FIRST_S
    assert _s.MAIN_IDLE_GAP_THINKING_S < _s.MAIN_IDLE_FIRST_THINKING_S
    _s.validate_timeout_nesting()  # 不抛即通过


def test_idle_threshold_above_transport_read_raises(monkeypatch):
    """env 把某档 idle 调到 ≥ 传输层 read ⇒ 启动期 fail-fast（该档否则永不负责）。"""
    import src.config.settings as _s

    monkeypatch.setattr(_s, "MAIN_IDLE_FIRST_S", 999.0)
    with pytest.raises(RuntimeError, match="MAIN_IDLE_FIRST_S"):
        _s.validate_timeout_nesting()
```

- [ ] **Step 2: 跑测试确认全红**

Run: `.venv/bin/python -m pytest tests/config/test_settings.py -q -k idle`
Expected: FAIL —— `AttributeError: module 'src.config.settings' has no attribute 'MAIN_IDLE_FIRST_S'`。

- [ ] **Step 3: 在 settings 里加四档 + 轮询间隔**

在 `src/config/settings.py` 的"生产链路角色级超时"段内、`RERANK_MAX_ATTEMPTS` 一行**之后**追加：

```python
# ── 业务层「生成任务静默」兜底（P3 / D15；与传输层 read 是**两个层次**）──
# 判据不同：read 看「字节间隔」，本组看「事件条数是否增长」（模型只吐 tool_call
# 而不吐 token 时字节在动、用户无感）。四档 = {首事件前 / 首事件后} × {非思考 / 思考}；
# 开思考时首个 chunk 显著更晚 ⇒ 思考档更宽（与 DELEGATE_MAX_IDLE_S 的既有做法同构）。
# ⚠ 两档都必须 **小于** MAIN_TIMEOUT_READ，否则传输层先触发、该档永不负责（启动期断言）。
MAIN_IDLE_FIRST_S: float = float(os.getenv("MAIN_IDLE_FIRST_S", "60"))
MAIN_IDLE_FIRST_THINKING_S: float = float(
    os.getenv("MAIN_IDLE_FIRST_THINKING_S", "150")
)
MAIN_IDLE_GAP_S: float = float(os.getenv("MAIN_IDLE_GAP_S", "30"))
MAIN_IDLE_GAP_THINKING_S: float = float(os.getenv("MAIN_IDLE_GAP_THINKING_S", "60"))
# 轮询间隔：最小档位是 30s，5s 轮询把"发现延迟"控制在阈值 +5s 内
MAIN_IDLE_POLL_INTERVAL_S: float = float(os.getenv("MAIN_IDLE_POLL_INTERVAL_S", "5"))
```

- [ ] **Step 4: 扩展 `validate_timeout_nesting()`**

把该函数的 docstring 首句改为"启动期 fail-fast：① rerank 内层总预算必须小于外层；② 四档 idle 阈值必须小于传输层 read。"，并在**函数末尾**（rerank 那段 `raise` 之后）追加：

```python
    for label, value in (
        ("MAIN_IDLE_FIRST_S", MAIN_IDLE_FIRST_S),
        ("MAIN_IDLE_FIRST_THINKING_S", MAIN_IDLE_FIRST_THINKING_S),
        ("MAIN_IDLE_GAP_S", MAIN_IDLE_GAP_S),
        ("MAIN_IDLE_GAP_THINKING_S", MAIN_IDLE_GAP_THINKING_S),
    ):
        if value >= MAIN_TIMEOUT_READ:
            raise RuntimeError(
                f"idle 档位 {label}({value}s) 必须小于传输层 "
                f"MAIN_TIMEOUT_READ({MAIN_TIMEOUT_READ}s)：否则传输层 read 会先触发，"
                "该档永不负责（业务层本应是对用户负责的那一层，D15）。"
                "请调小该档或调大 MAIN_TIMEOUT_READ。"
            )
```

- [ ] **Step 5: 跑测试确认全绿**

Run: `.venv/bin/python -m pytest tests/config/test_settings.py -q`
Expected: PASS（含既有 2 条 nesting 用例；`MAIN_IDLE_FIRST_S` 的 `match` 依赖错误信息里出现该名字 ⇒ 上一步的 f-string 已含 `{label}`）。

- [ ] **Step 6: 闸门 + 提交**

Run: `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright`
Expected: 仅存量（`engine.py:33` 的 `UP047` + pyright 4 处），**零新增**。

```bash
git add src/config/settings.py tests/config/test_settings.py
git commit -m "feat(config): 四档业务层 idle 阈值 + 启动期断言（P3）" -m "- MAIN_IDLE_{FIRST,GAP}[_THINKING]_S 四档 + MAIN_IDLE_POLL_INTERVAL_S；自有键名、env 可覆盖（D2）
- validate_timeout_nesting() 扩展：四档都必须 < MAIN_TIMEOUT_READ，否则传输层 read 先触发、该档永不负责（D15）
- 默认值 = 规格四值（首事件前 60/150、事件后 30/60）"
```

---

### Task 2: idle 守卫本体（两档阈值 + 每静默段一条）

**背景**：`_silence_watchdog` 现在**只打任务栈、不处置**（D-11 挂号的临时埋点）。本任务把它转正：**同一机制两档阈值**（D15），档 1 的动作 = 记 `degraded` + 送 `status`+stage 文案。

**关键实现约束（决定成败，先读懂再写）**：
1. **用 `seq` 判活跃，不用缓冲长度** —— `StreamingRunManager` 的缓冲超 `MAX_ITEMS=2000` 会**从头部裁掉 50 条**（`streaming.py:202-203`），用长度会在裁剪时误判成"有活动"。`get_events_since(session_id, last_seq)` 只看尾部、天然免疫裁剪。
2. **本层自己写的通知不算"事件恢复"** —— 否则通知一落缓冲就自我唤醒，变成每 30s 刷一条。做法：`last_seq = manager.add_event(...)`（`add_event` **返回 seq**），把自己的通知吸收掉。
3. **缓冲已被清理即退出** —— `sweep_expired()` 只在终态后清缓冲，此时守卫本就该被取消；加一句 `buffer_exists` 防御，杜绝"缓冲没了 ⇒ 永远静默 ⇒ 无限刷通知"。
4. **每静默段最多一条** —— 外部新事件到达才清 `notified`。

**Files:**
- Modify: `src/services/agent_service.py`（删临时埋点块，新增两个函数）
- Test: `tests/services/test_idle_guard.py`（**新建**）

**Interfaces:**
- Consumes: `settings.MAIN_IDLE_*`（T1）、`Event.DEGRADED`、`SSEInteractionTexts.STAGE_DEGRADED` / `DEGRADATION_TEXTS`（P2 已交付）
- Produces（T3 依赖）：
  - `agent_service._idle_thresholds(deep_thinking: bool) -> tuple[float, float]`（返回 `(首事件前, 首事件后)`）
  - `agent_service._idle_guard(manager, session_id, capture, deep_thinking) -> None`（长驻协程，由 `create_task` 启动、`cancel()` 结束）

- [ ] **Step 1: 写失败测试**

新建 `tests/services/test_idle_guard.py`：

```python
"""业务层 idle 守卫单测（P3）。

判据是"事件条数是否增长"，故替身只需实现 StreamingRunManager 的三个方法。
阈值被压到毫秒级、轮询 10ms，让事件循环里能真实走完"阈值到点 → 发通知"。
"""

import asyncio

import pytest

from src.config import settings
from src.config.const import SSEInteractionTexts
from src.core.log_events import Event
from src.services import agent_service


class _FakeBuffer:
    """StreamingRunManager 的最小替身：get_events_since / buffer_exists / add_event。"""

    def __init__(self, exists: bool = True) -> None:
        self.events: list[tuple[int, str, dict]] = []
        self.notices: list[tuple[str, dict]] = []
        self._exists = exists

    def get_events_since(self, session_id: str, after_seq: int) -> list:
        return [e for e in self.events if e[0] > after_seq]

    def buffer_exists(self, session_id: str) -> bool:
        return self._exists

    def add_event(self, session_id: str, etype: str, payload: dict) -> int:
        seq = len(self.events) + 1
        self.events.append((seq, etype, payload))
        self.notices.append((etype, payload))
        return seq


def _fast(monkeypatch, **overrides) -> None:
    """把轮询与四档阈值压到毫秒级（定时用例依赖真实 await）。"""
    for key, value in {
        "MAIN_IDLE_POLL_INTERVAL_S": 0.01,
        "MAIN_IDLE_FIRST_S": 0.05,
        "MAIN_IDLE_FIRST_THINKING_S": 0.05,
        "MAIN_IDLE_GAP_S": 0.05,
        "MAIN_IDLE_GAP_THINKING_S": 0.05,
    }.items():
        monkeypatch.setattr(settings, key, value)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)


def _spy_log(monkeypatch) -> list:
    logged: list = []
    monkeypatch.setattr(
        agent_service.core_logging,
        "log_event",
        lambda event, **fields: logged.append((event, fields)),
    )
    return logged


async def _run_briefly(coro, seconds: float) -> None:
    """跑 coro 若干秒后取消（守卫是长驻协程，用例以取消结束）。"""
    task = asyncio.create_task(coro)
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_idle_guard_notifies_once_per_silence_episode(monkeypatch):
    """静默到点：记一条 degraded(reason=generation_idle) + 送一条 status(degraded)。"""
    _fast(monkeypatch)
    logged = _spy_log(monkeypatch)
    buf = _FakeBuffer()

    await _run_briefly(agent_service._idle_guard(buf, "s1", None, False), 0.25)

    degraded = [f for e, f in logged if e == Event.DEGRADED]
    assert len(degraded) == 1, "每个静默段只记一条：本层自己的通知不算事件恢复"
    assert degraded[0]["reason"] == "generation_idle"
    assert degraded[0]["tier"] == "first"
    notices = [p for t, p in buf.notices if t == "status"]
    assert len(notices) == 1
    assert notices[0]["stage"] == SSEInteractionTexts.STAGE_DEGRADED
    assert (
        notices[0]["message"]
        == SSEInteractionTexts.DEGRADATION_TEXTS["generation_idle"]
    )


@pytest.mark.asyncio
async def test_idle_guard_re_notifies_after_activity_resumes(monkeypatch):
    """外部事件恢复后再静默 ⇒ 第二条通知（档位从 first 切到 gap）。"""
    _fast(monkeypatch)
    logged = _spy_log(monkeypatch)
    buf = _FakeBuffer()

    task = asyncio.create_task(agent_service._idle_guard(buf, "s1", None, False))
    await asyncio.sleep(0.15)  # 先触发 first 档
    buf.add_event("s1", "token", {"token": "x"})  # 外部事件恢复
    await asyncio.sleep(0.15)  # 再次静默 ⇒ 触发 gap 档
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    degraded = [f for e, f in logged if e == Event.DEGRADED]
    assert [f["tier"] for f in degraded] == ["first", "gap"]


@pytest.mark.asyncio
async def test_idle_guard_stays_silent_while_below_threshold(monkeypatch):
    """阈值未到 ⇒ 一条都不发。"""
    _fast(monkeypatch, MAIN_IDLE_FIRST_S=5.0, MAIN_IDLE_GAP_S=5.0)
    logged = _spy_log(monkeypatch)

    await _run_briefly(agent_service._idle_guard(_FakeBuffer(), "s1", None, False), 0.2)

    assert not [f for e, f in logged if e == Event.DEGRADED]


@pytest.mark.asyncio
async def test_idle_guard_exits_when_buffer_is_gone(monkeypatch):
    """缓冲已清理 ⇒ 守卫正常返回（不无限刷通知）。"""
    _fast(monkeypatch)
    logged = _spy_log(monkeypatch)

    task = asyncio.create_task(
        agent_service._idle_guard(_FakeBuffer(exists=False), "s1", None, False)
    )
    await asyncio.wait_for(task, timeout=0.5)  # 不抛即"自行退出"

    assert not [f for e, f in logged if e == Event.DEGRADED]


def test_idle_thresholds_follow_thinking_tier():
    """档位选择：思考档取 *_THINKING_S，否则取非思考档。"""
    assert agent_service._idle_thresholds(False) == (
        settings.MAIN_IDLE_FIRST_S,
        settings.MAIN_IDLE_GAP_S,
    )
    assert agent_service._idle_thresholds(True) == (
        settings.MAIN_IDLE_FIRST_THINKING_S,
        settings.MAIN_IDLE_GAP_THINKING_S,
    )
```

- [ ] **Step 2: 跑测试确认全红**

Run: `.venv/bin/python -m pytest tests/services/test_idle_guard.py -q`
Expected: FAIL —— `AttributeError: module 'src.services.agent_service' has no attribute '_idle_guard'`（新函数未建）。

- [ ] **Step 3: 删掉临时取证埋点块**

在 `src/services/agent_service.py` 里**整块删除**：

- 注释块 `# ─────────── 临时取证埋点（systematic-debugging 走 A：…）───────────`（含其 3 行说明）
- `_SILENCE_WATCH_INTERVAL_S` / `_SILENCE_WATCH_THRESHOLD_S` 两个模块常量
- `def _dump_task_stacks() -> str:` 整个函数
- `async def _silence_watchdog(...)` 整个函数

（`import time` 保留 —— 新守卫仍用 `time.monotonic()`。）

- [ ] **Step 4: 新增 `_idle_thresholds` 与 `_idle_guard`**

在原 `_silence_watchdog` 的位置（`_record_event` 之后、`@observe` 的 `_run_generation` 之前）写入：

```python
def _idle_thresholds(deep_thinking: bool) -> tuple[float, float]:
    """按思考档位取业务层静默的**两档阈值**（D15；四值见 spec / settings）。

    首事件前档更宽：该段含排队 + prefill，开思考时首个 chunk 可达数十秒，属正常。

    Args:
        deep_thinking: 本轮是否开启思考

    Returns:
        (首事件前阈值秒数, 首事件后阈值秒数)
    """
    if deep_thinking:
        return settings.MAIN_IDLE_FIRST_THINKING_S, settings.MAIN_IDLE_GAP_THINKING_S
    return settings.MAIN_IDLE_FIRST_S, settings.MAIN_IDLE_GAP_S


async def _idle_guard(
    manager: StreamingRunManager,
    session_id: str,
    capture: _StreamCapture | None,
    deep_thinking: bool,
) -> None:
    """业务层「生成任务静默」守卫（P3 / D3 / D15；**档 1：只记录、不中止**）。

    判据是**事件是否增长**（`seq` 推进），不是字节间隔 —— 模型只吐 tool_call
    而不吐 token 时字节在动、用户无感，本判据正好覆盖该情形。

    - **首事件前档**：自本守卫启动起**从未**有新事件（图事件 / 工具事件）；
    - **首事件后档**：已有事件之后再次静默。
    两档取 `_idle_thresholds()` 的对应值；两档都已由启动期断言保证 < 传输层 read。

    **档 1 的动作**：记一条 `degraded`（`reason=generation_idle`，字段 `idle_s` / `tier`）
    + 经 `status` + `STAGE_DEGRADED` 送达取值集合表里的**过程**文案；
    **不动控制流**（不取消、不降级、不改 status）。**每个静默段最多一条**：
    本层自己写入的通知不算"事件恢复"（其 `seq` 记入 `last_seq` 吸收），
    只有外部新事件才清除已通知标记。

    Args:
        manager: StreamingRunManager（读该 session 的事件缓冲；`add_event` 返回新 seq）
        session_id: 会话 ID
        capture: 流捕获容器（通知同时进事件日志，随 process 持久化与历史回放）
        deep_thinking: 本轮是否开启思考（决定取哪一档阈值）
    """
    first_threshold, gap_threshold = _idle_thresholds(deep_thinking)
    existing = manager.get_events_since(session_id, 0)
    last_seq = existing[-1][0] if existing else 0
    last_change = time.monotonic()
    first_event_seen = False
    notified = False
    interval = settings.MAIN_IDLE_POLL_INTERVAL_S
    while True:
        await asyncio.sleep(interval)
        if not manager.buffer_exists(session_id):
            # 缓冲已随终态 TTL 清理 ⇒ 无可守护，正常退出（防"永远静默 ⇒ 无限刷"）
            return
        pending = manager.get_events_since(session_id, last_seq)
        if pending:
            last_seq = pending[-1][0]
            last_change = time.monotonic()
            first_event_seen = True
            notified = False
            continue
        idle = time.monotonic() - last_change
        if first_event_seen:
            threshold = gap_threshold
            tier = "gap"
        else:
            threshold = first_threshold
            tier = "first"
        if notified or idle < threshold:
            continue
        core_logging.log_event(
            Event.DEGRADED, reason="generation_idle", idle_s=int(idle), tier=tier
        )
        notice = SSEStatusEvent(
            stage=SSEInteractionTexts.STAGE_DEGRADED,
            message=SSEInteractionTexts.DEGRADATION_TEXTS["generation_idle"],
        )
        _record_event(capture, notice)
        # add_event 返回新 seq：把自己的通知吸收掉，避免"自我唤醒"变成周期刷屏
        last_seq = manager.add_event(
            session_id, notice.type, notice.payload_for_buffer()
        )
        notified = True
```

- [ ] **Step 5: 跑测试确认全绿**

Run: `.venv/bin/python -m pytest tests/services/test_idle_guard.py tests/services/ -q`
Expected: PASS（`tests/services` 无回归；定时用例的余量为阈值 0.05s / 轮询 0.01s / 等到 0.25s，CI 本机足够宽松）。

- [ ] **Step 6: 闸门 + 提交**

Run: `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright`
Expected: 零新增。

```bash
git add src/services/agent_service.py tests/services/test_idle_guard.py
git commit -m "feat(agent): 把静默看门狗转正为 idle 守卫（档 1：只记录不中止，P3）" -m "- 删临时取证埋点（_SILENCE_WATCH_* / _dump_task_stacks / _silence_watchdog）
- 新增 _idle_thresholds（按思考档位取两档）+ _idle_guard（同一机制两档阈值，D15）
- 判据改为按 seq 判活跃（免疫缓冲头部裁剪）；本层自己的通知记入 last_seq 吸收，每个静默段只发一条
- 档 1 动作：degraded(reason=generation_idle, idle_s, tier) + status(STAGE_DEGRADED) 文案；不动控制流"
```

---

### Task 3: 接进 `_run_generation` + 不中止生成的端到端证明 + 观测登记

**Files:**
- Modify: `src/services/agent_service.py`（`_run_generation` 的创建/取消段）
- Modify: `src/core/log_event_specs.py`（`degraded` 字段元组）
- Modify: `docs/agents/logging-rules.md`（`degraded` 行的值域）
- Test: `tests/services/test_agent_service.py`（追加）

**Interfaces:**
- Consumes: `_idle_guard` / `_idle_thresholds`（T2）、`settings.MAIN_IDLE_*`（T1）
- Produces: 无（叶子；`degraded` 事件与 `status`+stage 通道均为 P2 既有契约）

- [ ] **Step 1: 写失败测试**

追加到 `tests/services/test_agent_service.py` 末尾（该文件已有 `_chat_model_stream_item`、`_Recorder`、`Event`-free 的既有夹具；`_Recorder` 若不存在则用文件里既有的 manager 替身，保持同型）：

```python
class _SlowGraph:
    """两个 token 事件之间刻意静默一段（用于触发 idle 守卫），但**不中止**。"""

    def __init__(self, gap_s: float) -> None:
        self._gap = gap_s

    async def astream_events(self, state, version=None):
        yield _chat_model_stream_item("前")
        await asyncio.sleep(self._gap)
        yield _chat_model_stream_item("后")


@pytest.mark.asyncio
async def test_idle_guard_notifies_without_aborting_generation(monkeypatch):
    """3.1/3.2/3.4：静默到点 ⇒ 送降到用户 + 记 degraded；**生成仍跑完**（档 1 不动控制流）。"""
    from src.config.const import SSEInteractionTexts
    from src.services import agent_service
    from src.services.agent_service import _run_generation

    monkeypatch.setattr(settings, "MAIN_IDLE_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(settings, "MAIN_IDLE_FIRST_S", 0.05)
    monkeypatch.setattr(settings, "MAIN_IDLE_GAP_S", 0.05)
    logged: list = []
    monkeypatch.setattr(
        agent_service.core_logging,
        "log_event",
        lambda event, **fields: logged.append((event, fields)),
    )
    ctx = RequestContext(session_id="s-idle", kb_id="")
    manager = _Recorder()
    partial: dict = {"text": "", "sources": []}

    answer = await _run_generation(
        "s-idle",
        "",
        "你好",
        [],
        False,
        ctx,
        manager,
        graph=_SlowGraph(0.25),
        partial_holder=partial,
    )

    # 不中止：两个 token 都到、函数正常返回
    tokens = [p["token"] for t, p in manager.events if t == "token"]
    assert tokens == ["前", "后"]
    assert answer == "前后"
    # 且确实发过降级告知（stage=degraded + 取值集合表文案）
    notices = [
        p
        for t, p in manager.events
        if t == "status" and p.get("stage") == SSEInteractionTexts.STAGE_DEGRADED
    ]
    assert notices, "静默超阈应送出降级告知"
    assert (
        notices[0]["message"]
        == SSEInteractionTexts.DEGRADATION_TEXTS["generation_idle"]
    )
    assert [f for e, f in logged if e == Event.DEGRADED]


def test_p3_does_not_change_sse_subscription_layer():
    """3.5 防回归（结构性）：主 POST 流与续传路的订阅仍传 max_idle=None。

    行为判据是"活跃任务长静默不断流"，而该门槛是 180s / None，单测里等不出来；
    此处钉住"调用方仍传 max_idle=None"这一事实，足以防住本阶段的回退
    （把已被推翻的 180s 空闲收流改回来）。
    """
    import inspect

    from src.services import agent_service, turn_runner

    assert "max_idle=None" in inspect.getsource(turn_runner)
    assert "max_idle=None" in inspect.getsource(agent_service)
```

**导入（已核实：该文件当前**没有**这三个 import，本步必须一并补）**：在 `tests/services/test_agent_service.py` 顶部加入

```python
from src.config import settings
from src.core.log_events import Event
from src.infra.llm.request_context import RequestContext
```

（`_Recorder` 与 `_chat_model_stream_item` 该文件**已有**（分别在第 1570 行与第 49 行附近），直接复用，不要重复定义。）

- [ ] **Step 2: 跑测试确认全红**

Run: `.venv/bin/python -m pytest tests/services/test_agent_service.py -q -k "idle_guard_notifies_without_aborting or p3_does_not_change"`
Expected: FAIL —— 第一条断言 `notices` 为空（守卫**还没接线**，`_run_generation` 仍不启动它）；第二条应已 PASS（订阅层本就没被动过，属防回归）。

- [ ] **Step 3: `_run_generation` 接线**

把创建段：

```python
    # 临时取证埋点：静默看门狗（定位完成后删除）
    watch_task = asyncio.create_task(
        _silence_watchdog(
            manager, session_id, _SILENCE_WATCH_INTERVAL_S, _SILENCE_WATCH_THRESHOLD_S
        )
    )
```

改为：

```python
    # 业务层「生成任务静默」守卫（P3 / D15）：与澄清消费并行；档 1 只记录不中止，
    # 故它不会影响下面的图事件循环（取消由本函数 finally 统一负责）
    idle_task = asyncio.create_task(
        _idle_guard(manager, session_id, capture, deep_thinking)
    )
```

并把 `finally` 里两处 `watch_task` 改名（注释同步）：

```python
        idle_task.cancel()  # 生成结束/异常/取消后停止静默守护
        # 取消 / 异常路径同样走到这里 —— 不关的 span 会永远悬空
        tool_trace.close()
        await asyncio.gather(drain_task, idle_task, return_exceptions=True)
```

- [ ] **Step 4: 事件字段与文档登记**

`src/core/log_event_specs.py` 的 `degraded` 条目改为：

```python
    "degraded": EventSpec(
        "degraded",
        "degradation",
        "warning",
        ("reason", "err", "idle_s", "tier"),
    ),
```

`docs/agents/logging-rules.md` 里 `degraded` 那条的末尾补一句（该行现由 P2 的 T1 登记）：

```markdown
  ；`idle_s` 为静默秒数（整数）；`tier` 取 first（首事件前档）/ gap（首事件后档），
  仅 `reason=generation_idle` 时出现
```

- [ ] **Step 5: 跑测试确认全绿**

Run: `.venv/bin/python -m pytest tests/services/ tests/config/ tests/core/ -q`
Expected: PASS（`tests/core/` 含 `EVENT_SPECS` 一致性校验用例 —— 字段元组改动须保持 `Event` 成员集与 spec key 集相等，本步只是改元组、不增事件）。

- [ ] **Step 6: 闸门 + 机检 + 提交**

Run:
```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright
.venv/bin/python docs/tmp/check-change-consistency.py docs/openspec/changes/timeout-and-degradation-hardening
openspec validate timeout-and-degradation-hardening
```
Expected: 静态检查零新增；一致性机检 **0 命中**；`openspec validate` **valid**。

```bash
git add src/services/agent_service.py src/core/log_event_specs.py docs/agents/logging-rules.md tests/services/test_agent_service.py
git commit -m "feat(agent): 接线 idle 守卫并证明不中止生成（P3）" -m "- _run_generation 用 idle_task 替换临时看门狗（传 capture / deep_thinking），finally 同名取消
- 端到端用例：token 间隔静默超阈 ⇒ 送出 degraded 告知且两个 token 都到、函数正常返回（档 1 不动控制流）
- 3.5 结构性防回归：主 POST 流/续传仍 max_idle=None
- degraded 事件字段补 idle_s/tier 并登记 logging-rules 值域"
```

---

### Task 4: P3 验收（控制方自跑，不派子代理）

**背景**：本仓约定"产出是实测证据的验收由控制方自己做"（`feedback-sdd-long-verification`）。P1/P2 的教训：**结构性断言必要但不充分**（P1 曾带着"超时配置全对但完全不生效"的 Critical 被宣告完成）⇒ 本任务**必须**有运行时冒烟。

**Files:** 不改代码；产出证据 + 回填 `tasks.md` §3

- [ ] **Step 1: 静态检查 + 机检 + validate**

Run:
```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright
.venv/bin/python -m src.cli.check_docs
.venv/bin/python docs/tmp/check-change-consistency.py docs/openspec/changes/timeout-and-degradation-hardening
openspec validate timeout-and-degradation-hardening
```
Expected: ruff 仅 `engine.py:33` 的 `UP047`；pyright 仅存量 4 处；`check_docs` 0 error；机检 **0 命中**；validate **valid**。

- [ ] **Step 2: 非 DB 全量单测**

Run: `DASHSCOPE_API_KEY=test OPENAI_API_KEY=test .venv/bin/python -m pytest -q --ignore=tests/parsers --ignore=tests/infra/db --ignore=tests/scripts`
Expected: 基线是 **1641 passed / 1 failed / 2 skipped**（该 1 failed 是基线既有的 ragas API 漂移：`tests/cli/test_generate_transforms.py`，P3 不碰 `src/cli/**`）⇒ 本次应 **≥1641 passed** 且**失败仍是同一条**。

- [ ] **Step 3: 逐条核 `tasks.md` §3 验收（3.4 / 3.5）**

| 验收项 | 判据（可当场核对） |
|---|---|
| 3.4（首事件前档触发 + 留痕） | `tests/services/test_idle_guard.py::test_idle_guard_notifies_once_per_silence_episode` 断言 `tier="first"` + `reason=generation_idle` + 文案取自取值集合表 |
| 3.4（中间静默触发） | 同文件 `test_idle_guard_re_notifies_after_activity_resumes` 断言 `["first", "gap"]` |
| 3.4（runtime） | Step 4 冒烟 B 档：真实服务 + 小阈值 ⇒ SSE 出现 `status{stage:"degraded"}`、日志出现 `[degradation] degraded reason=generation_idle` |
| 3.5（订阅层未回退） | `test_p3_does_not_change_sse_subscription_layer` + `grep -n "max_idle=None" src/services/turn_runner.py src/services/agent_service.py` 各命中 1 处 |

- [ ] **Step 4: 运行时冒烟（必须做，按 trace_id 回查）**

在同一工具调用内起服务 → 发请求 → 停服务（本仓已知约束：`&` 起的服务活不过一次工具调用）。两档：

- **A 正常档**（默认配置，强制检索的对比型问题）→ 期望 `done=1 / error=0`，且**无** `generation_idle` 事件（正常轮次不静默到 30s）。
- **B idle 档**（`env MAIN_IDLE_FIRST_S=3 MAIN_IDLE_GAP_S=3 MAIN_IDLE_POLL_INTERVAL_S=1` + 一个**可能触发长静默**的问题，如开启思考的宽问题）→ 期望 SSE 里出现 `event: status` 且 `data` 含 `"stage": "degraded"`、`"message": "仍在处理中，请稍候"`；日志出现 `[degradation] degraded reason=generation_idle ... tier=first|gap`；**且这一轮最终仍以 `done` 收尾**（证明档 1 不中止）。

取证命令（`<TID>` 为 SSE `done` 事件里的 trace_id）：

```bash
grep -h "<TID>" logs/app_*.log | grep -oE "(\[degradation\] degraded|hybrid done|model turn|format done|retrieve done).*" | cut -c1-160
```

**若 B 档因模型响应太快而没触发静默**：如实登记（本机不可稳定构造"长静默"），改为**补一次定向冒烟**——用 `MAIN_TIMEOUT_READ=0.001` 让首个模型调用立刻失败会走 P2 的降级路径而非 idle 路径，**不能**用作替代证据；此时把该缺口写进报告（"idle 的运行时触发仅由单测覆盖"）。

- [ ] **Step 5: 写验收报告并如实登记缺口**

报告写到 `/root/code/corporate_rag/.superpowers/sdd/2026-10-16-timeout-and-degradation-p3/t5-acceptance.md`（与 P1/P2 同构；注意本阶段的 workspace 在主工作区根下，因为 worktree 的 `.superpowers/` 不会随合并带走），含：每档冒烟的**真实输出**、3.4/3.5 的逐条判据、以及**如实登记**：
- `degrade` 档 1 **不中止生成**这一"负向承诺"的运行时证据强度（只证明了"该轮仍以 done 收尾"）；
- idle 的运行时触发是否稳定可构造（若不稳定，写明只由单测覆盖）；
- 前端对 `generation_idle` 的呈现**仍无自动化守卫**（`deploy/nginx/html/` 无测试设施，与 P2 同一缺口）。

- [ ] **Step 6: 回填 `tasks.md` §3 并提交**

把 §3 的 3.1–3.5 勾选（只勾切实完成的），并在 §3 末尾追加一段"P3 验收结论（含证据分级与缺口）"，然后：

```bash
git add docs/openspec/changes/timeout-and-degradation-hardening/tasks.md
git commit -m "docs(openspec): timeout-and-degradation-hardening P3 验收结论与证据分级"
```

---

## 与后续阶段/其他 change 的边界（写清以免被当成漏做）

- **档 2（取消/降级）不做**：接 `task_registry`、与 `request-abort` 契约交叉 ⇒ 已登记 `tasks.md` 5.1。
- **§4 不做**：4.1 单阶段耗时、4.2 分位回填（D11「待校准」）、4.3 文档同步（`best-practices.md` §11 / `requirements_pool` A-03/A-05 / 两份 env 模板）、4.4 新 ADR（**阻塞在 `deepeval-bridge-eval` 的 ADR 编号**）。⇒ **本阶段新增了 5 个 env 键**（`MAIN_IDLE_*`），§4.3 收尾时**两份模板都要补**。
- **`deploy/nginx/html/chat.html` 不改**：`degraded` stage 的呈现与样式在 P2 已落地（实时 + 回放两处），`generation_idle` 复用同一 stage ⇒ 前端无需改动。
- **`src/chat/streaming.py` 不改**：SSE 订阅层的 `max_idle` 语义是 `sse-stream-resilience` 固化的既有契约（3.5 的防回归对象）。
- **`F-37`（迭代触顶 → 空回答 + `status=complete`）不属本阶段**：那是 `agent-round-budget` 的地盘；P3 只负责"静默了要说话"，不负责"没答案怎么落库"。
- **另有一批同源的临时埋点不在本阶段范围**：`src/agents/tools/web_tools.py`（2 处）与 `src/infra/search/tavily_client.py`（4 处）的 `TIMING` 日志属"316s 取证"遗留（记忆 `debug-instrumentation-316s` 记为定位后整块删除），**不属于 §3** ⇒ 本阶段只删生成任务侧那一处（`silence_idle_s`），其余建议登记到 §5 或单开清理。

## Self-Review（写完后的自查记录）

**1. Spec/metrics 覆盖**：`degradation-policy` 的「生成任务级的失控兜底」requirement —— ① 不吐事件的 idle 保险丝（T2 守卫 + T3 接线）② 层间边界（T3 的 3.5 防回归 + 边界节）③ 按思考档位分档（T1 四档 + T2 `_idle_thresholds`）④ 原因词处理：自有事件 `reason=generation_idle`、不套 `DelegateStopReason`（T2 只记 `degraded`，全篇未出现该枚举）⑤ 档 1 只记录不中止（T2 无取消路径 + T3 端到端证明）⑥ 用户可见 = 是 ⇒ 走 `status` + `STAGE_DEGRADED`（T2）+ 文案取自取值集合表（T2 断言）⑦ 阈值 < 传输层 read（T1 启动期断言）。`tasks.md` §3 的 3.1–3.5 逐条对应 T1/T2/T3 与 T4 的取证表。

**2. 占位物扫描**：无 TBD/TODO；每个代码步骤都给了可直接落盘的实现与真实签名（`_idle_guard` / `_idle_thresholds` / settings 键名 / `EventSpec` 元组 / logging-rules 文本）；定时用例给了具体秒数与余量说明。

**3. 类型/命名一致性**（跨任务）：`_idle_thresholds(deep_thinking) -> tuple[float, float]`、`_idle_guard(manager, session_id, capture, deep_thinking)`、`idle_task`（T2 定义 → T3 接线，名字一致）；settings 五个键名在 T1 定义、T2/T3/T4 引用一致；事件字段 `idle_s` / `tier` 在 T2 生产、T3 登记，值域 `first` / `gap` 在 T2 生产、T3 文档登记，一致。

**4. 已识别的残余风险（已登记，非占位）**：① **定时用例**依赖真实 `await`（阈值 0.05s / 轮询 0.01s / 观察窗 0.25s）—— 余量足够但非确定性，若在极慢环境抖动，允许把阈值与观察窗等比放大（保持 1:5 比例）；② idle 的**运行时触发**在本机可能不可稳定构造（模型太快）⇒ T4 Step 4 已写明"如实登记 + 不得用超时路径冒充"。
