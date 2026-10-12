# 阿里云告警接入自动分析（alibaba-alert-notify）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让阿里云 ARMS 告警经一个带鉴权的 HTTP 端点进来，跑一轮 Agent 分析，并把结论主动推送到指定企微群；失败/超时/空回答/繁忙时兜底转发原始告警。

**Architecture:** 端点极薄（只读请求头 token + 转发 body），解析/去重/编排全落 `services/`；复用站点同一条生成管线（`turn_runner.start_turn`），报警会话按 `alert` scope 独立派生；结论推送给群走通道层新增的**主动发送**出口（SDK `send_message`）。超时用**真取消**（`StreamingRunManager.cancel`）而非仅停止等待。

**Tech Stack:** FastAPI / Pydantic / LangGraph / asyncio / loguru / wecom-aibot-python-sdk==1.0.2 / pytest

**Spec:** `docs/openspec/changes/alibaba-alert-notify/`（`proposal.md` / `design.md` D1–D21 / `specs/alert-notify/spec.md` / `specs/wecom-channel/spec.md` / `tasks.md`）

## Global Constraints

- 分层：`api/` **不得**直接 import `infra/`、`config/`（必须经 `services/`）——见 `CLAUDE.md:61`。`api/` 只做路由与转发。
- 硬编码集中管理：新增常量/文案/阈值**不得**散在业务代码，统一进 `src/config/`（`CLAUDE.md:111`）。
- 日志：分层前缀 + 英文 `k=v`，值按 `docs/agents/logging-rules.md` 编码（`encode_value`）。
- docstring：Google 风格（Args/Returns/Raises），中文。
- 每个任务结束必须 `git commit`；提交用**显式路径** `git add <文件...>`（本 worktree 里 `.venv` 是 symlink，`.gitignore` 的 `.venv/` 不匹配 symlink，`git add -A` 会把它带进去）。
- 测试一律从 **worktree 根目录**跑，确保用的是本 worktree 的 `src`：
  `.venv/bin/python -m pytest <path> -v`
- 仓库根为 worktree：`/root/code/corporate_rag-alibaba-alert-notify`（下文相对路径均相对它）。
- **不引入新依赖**。

---

## File Structure

**新增**

| 文件 | 职责 |
|---|---|
| `src/config/alert.py` | 告警配置的解析与启动期校验（token / 目标群 / 去重窗 / 超时 / 固定 user_id / 兜底长度），独立于 `settings.py`（后者已近 400 行红线） |
| `src/services/alert_service.py` | 告警业务：载荷解析、会话标识派生、入口去重、编排（跑 Agent + 两段推送 + 兜底 + 超时真取消） |
| `src/api/alerts.py` | 极薄端点：读请求头 token、把 body 交给 `alert_service`、返回其状态码 |
| `tests/services/test_alert_config.py` | 配置解析/校验单测 |
| `tests/services/test_alert_parse.py` | 载荷解析 + 会话派生单测 |
| `tests/services/test_alert_dedup.py` | 去重单测 |
| `tests/services/test_alert_orchestration.py` | 编排（两段推送 / 兜底 / 超时 / TurnBusy）单测 |
| `tests/services/test_wecom_send.py` | 通道主动推送出口单测 |
| `tests/channels/test_long_connection_send.py` | 驱动 `send_message` 单测 |
| `tests/chat/test_streaming_cancel.py` | `StreamingRunManager.cancel` 单测 |
| `tests/api/test_alerts_endpoint.py` | 端点鉴权/应答单测 |
| `tests/agents/test_rag_tools_no_ask_user.py` | `make_rag_tools(include_ask_user=False)` 单测 |

**修改**

| 文件 | 改动 |
|---|---|
| `src/config/const.py` | 新增 `Channel.ALERT`、`ALERT_WEBHOOK_PATH`、`AlertTexts`（占位/兜底/query 模板） |
| `src/agents/tools/rag_tools.py` | `make_rag_tools` 增参 `include_ask_user: bool = True` |
| `src/services/agent_service.py` | 构造期编译**两套图**（含/不含 `ask_user`），`stream_chat` 按参数选图，同步 `ctx.tool_names` |
| `src/services/turn_runner.py` | `start_turn` 增参 `clarify: bool = True`，透传给 `stream_chat` |
| `src/chat/streaming.py` | `StreamingRunManager.cancel(session_id)` |
| `src/channels/wecom/long_connection.py` | 驱动新增 `async def send_message(chatid, content) -> None` |
| `src/services/wecom_service.py` | 新增 `async def send_to_session(bot_key, chatid, content) -> bool` |
| `src/api/__init__.py` | 导出 `alerts_router` |
| `src/main.py` | `include_router(alerts_router, prefix="/api")` |
| `.env.template` / `.env.example` | 新增 `ALERT_*` 键（**两个文件都要改**） |
| `docs/agents/api_contract.md` / `code-map.md` / `logging-rules.md` / `deploy-runbook.md` | 登记端点、模块、渠道值、ARMS 模板与部署步骤 |

---

## Task 1: 告警配置模块 + 渠道值与文案常量

**Files:**
- Modify: `src/config/const.py`（追加，勿动既有内容）
- Create: `src/config/alert.py`
- Test: `tests/services/test_alert_config.py`

**Interfaces:**
- Consumes: 无（本任务是最底层）
- Produces:
  - `src.config.const.Channel.ALERT: str`
  - `src.config.const.ALERT_WEBHOOK_PATH: str`
  - `src.config.const.AlertTexts.PLACEHOLDER_TEMPLATE / FALLBACK_PREFIX / QUERY_TEMPLATE`
  - `src.config.const.ALERT_USER_ID: str`（全零 UUID）
  - `src.config.alert.AlertTarget(bot_key: str, chatid: str)`
  - `src.config.alert.AlertConfig(token: str, target: AlertTarget | None, dedup_window_seconds: float, dedup_capacity: int, turn_timeout_seconds: float, fallback_max_chars: int)`
  - `src.config.alert.load_alert_config() -> AlertConfig`

- [ ] **Step 1: 写失败测试**

创建 `tests/services/test_alert_config.py`：

```python
"""告警配置解析与校验单测。"""

import json

import pytest

from src.config.alert import AlertConfig, AlertTarget, load_alert_config


def test_unset_token_yields_empty_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置 token ⇒ token 为空、目标为 None，不抛异常（不阻断启动）。"""
    monkeypatch.delenv("ALERT_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    cfg = load_alert_config()
    assert cfg.token == ""
    assert cfg.target is None


def test_valid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """齐备配置解析出目标与阈值。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.setenv("ALERT_TARGET", '{"bot_key": "dev", "chatid": "C1"}')
    monkeypatch.setenv("ALERT_DEDUP_WINDOW_SECONDS", "180")
    monkeypatch.setenv("ALERT_TURN_TIMEOUT_SECONDS", "240")
    cfg = load_alert_config()
    assert isinstance(cfg, AlertConfig)
    assert cfg.token == "s3cret"
    assert cfg.target == AlertTarget(bot_key="dev", chatid="C1")
    assert cfg.dedup_window_seconds == 180.0
    assert cfg.turn_timeout_seconds == 240.0


def test_bad_target_json_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标群 JSON 非法 ⇒ fail-fast，且错误信息不回显原值。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.setenv("ALERT_TARGET", "{not json")
    with pytest.raises(ValueError) as ei:
        load_alert_config()
    assert "ALERT_TARGET" in str(ei.value)
    assert "{not json" not in str(ei.value)


def test_target_missing_bot_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标群缺 bot_key ⇒ fail-fast。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.setenv("ALERT_TARGET", json.dumps({"chatid": "C1"}))
    with pytest.raises(ValueError, match="bot_key"):
        load_alert_config()


def test_token_set_but_target_missing_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """有 token 无目标：合法（端点收包后按"未配置目标"记 warning）。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    cfg = load_alert_config()
    assert cfg.token == "s3cret"
    assert cfg.target is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/services/test_alert_config.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.config.alert'`）

- [ ] **Step 3: 在 `src/config/const.py` 追加常量**

在文件末尾追加（不要改动既有定义）：

```python
# ── 阿里云告警接入（change: alibaba-alert-notify） ──

# 告警接收端点路径（api 层只引用本常量，不 import config 其它模块）
ALERT_WEBHOOK_PATH: Final[str] = "/alerts/webhook"

# 报警会话的固定归属：全零 UUID，与任何登录用户 uid 都不同 ⇒ 会话列表天然查不到
ALERT_USER_ID: Final[str] = "00000000-0000-0000-0000-000000000000"

# 告警专用渠道标识（另需在 docs/agents/logging-rules.md 的渠道表登记）
# 见 Channel.ALERT


class AlertTexts:
    """告警链路的用户可见文案与输入模板（集中管理，不散在业务代码）。"""

    # 受理占位（第一段推送）；{alertname} 由载荷渲染
    PLACEHOLDER_TEMPLATE: str = "收到告警「{alertname}」，正在分析…"
    # 兜底转发前缀（与正常结论区分）
    FALLBACK_PREFIX: str = "[原始告警]"
    # 喂给 Agent 的输入模板；{alertname}/{level}/{details} 由载荷渲染
    QUERY_TEMPLATE: str = (
        "收到一条云监控告警，请分析其含义、可能原因与处置建议。\n"
        "告警名称：{alertname}\n"
        "告警等级：{level}\n"
        "告警内容：\n{details}"
    )
```

再在 `Channel` 类内追加渠道值（紧跟 `FEISHU` 之后）：

```python
    ALERT: str = "alert"  # 阿里云告警自动分析轮次
```

- [ ] **Step 4: 写实现 `src/config/alert.py`**

```python
"""告警接入配置：从环境变量解析并校验（change: alibaba-alert-notify）。

独立于 settings.py（后者已接近 400 行红线），解析与校验合一，由 alert_service
在首次使用时加载（模块级缓存）。

错误信息只定位到键名，**绝不回显 ALERT_WEBHOOK_TOKEN（密钥）或 ALERT_TARGET
原值**——与 src/config/wecom_bots.py 同一纪律。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

_DEFAULT_DEDUP_WINDOW_SECONDS: float = 300.0
_DEFAULT_DEDUP_CAPACITY: int = 4096
_DEFAULT_TURN_TIMEOUT_SECONDS: float = 240.0
_DEFAULT_FALLBACK_MAX_CHARS: int = 1500


@dataclass(frozen=True)
class AlertTarget:
    """告警结论的推送目标（单目标；多群把 token 解释为路由键即可扩展）。"""

    bot_key: str  # 机器人别名（复用现有 WECOM_BOTS 里的 key）
    chatid: str  # 群会话 id（群维度，与机器人无关）


@dataclass(frozen=True)
class AlertConfig:
    """告警链路的全部可配置项。"""

    token: str  # 请求头鉴权 token；空串 = 未配置（端点恒 404，不阻断启动）
    target: AlertTarget | None  # 目标群；None = 未配置（不发送、记 warning）
    dedup_window_seconds: float  # 入口去重时间窗
    dedup_capacity: int  # 去重结构容量上限
    turn_timeout_seconds: float  # 整轮分析超时
    fallback_max_chars: int  # 兜底消息长度上限


def _positive_float(name: str, default: float) -> float:
    """读正数浮点环境变量；非法即 fail-fast。"""
    raw = os.getenv(name, "")
    if raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} 不是合法数字") from None
    if value <= 0:
        raise ValueError(f"{name} 必须为正数")
    return value


def _positive_int(name: str, default: int) -> int:
    """读正整数环境变量；非法即 fail-fast。"""
    raw = os.getenv(name, "")
    if raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} 不是合法整数") from None
    if value <= 0:
        raise ValueError(f"{name} 必须为正整数")
    return value


def _load_target() -> AlertTarget | None:
    """解析 ALERT_TARGET（单个 JSON 对象）；未配置返回 None。"""
    raw = os.getenv("ALERT_TARGET", "")
    if raw.strip() == "":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("ALERT_TARGET 不是合法 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("ALERT_TARGET 必须是 JSON 对象")  # noqa: TRY004
    bot_key = data.get("bot_key")
    chatid = data.get("chatid")
    if not isinstance(bot_key, str) or bot_key == "":
        raise ValueError("ALERT_TARGET.bot_key 为空")
    if not isinstance(chatid, str) or chatid == "":
        raise ValueError("ALERT_TARGET.chatid 为空")
    return AlertTarget(bot_key=bot_key, chatid=chatid)


def load_alert_config() -> AlertConfig:
    """解析并校验告警配置；任何非法立即抛 ValueError（fail-fast）。

    未配置 token 是**合法**状态（本地/开发环境）：token 为空、目标可为 None。
    """
    token = os.getenv("ALERT_WEBHOOK_TOKEN", "")
    return AlertConfig(
        token=token,
        target=_load_target(),
        dedup_window_seconds=_positive_float(
            "ALERT_DEDUP_WINDOW_SECONDS", _DEFAULT_DEDUP_WINDOW_SECONDS
        ),
        dedup_capacity=_positive_int("ALERT_DEDUP_CAPACITY", _DEFAULT_DEDUP_CAPACITY),
        turn_timeout_seconds=_positive_float(
            "ALERT_TURN_TIMEOUT_SECONDS", _DEFAULT_TURN_TIMEOUT_SECONDS
        ),
        fallback_max_chars=_positive_int(
            "ALERT_FALLBACK_MAX_CHARS", _DEFAULT_FALLBACK_MAX_CHARS
        ),
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/services/test_alert_config.py -v`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
git add src/config/alert.py src/config/const.py tests/services/test_alert_config.py
git commit -m "feat(alert): 新增告警配置模块与渠道值/文案常量"
```

---

## Task 2: 通道层主动推送出口

**Files:**
- Modify: `src/channels/wecom/long_connection.py`（在 `LongConnectionDriver` 内新增方法）
- Modify: `src/services/wecom_service.py`（新增 `send_to_session`）
- Test: `tests/channels/test_long_connection_send.py`、`tests/services/test_wecom_send.py`

**Interfaces:**
- Consumes: `LongConnectionDriver._client`（SDK `WSClient`）
- Produces:
  - `LongConnectionDriver.send_message(chatid: str, content: str) -> None`（失败抛 `RuntimeError`）
  - `wecom_service.send_to_session(bot_key: str, chatid: str, content: str) -> bool`（失败返回 `False`，**不抛**）

- [ ] **Step 1: 写失败测试（驱动层）**

创建 `tests/channels/test_long_connection_send.py`：

```python
"""LongConnectionDriver.send_message：主动推送（无入站帧）单测。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.channels.wecom.long_connection import LongConnectionDriver


def _driver_with_client() -> tuple[LongConnectionDriver, MagicMock]:
    """构造已注入假 client 的驱动。"""
    driver = LongConnectionDriver("botA", "secret", AsyncMock())
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"errcode": 0})
    driver._client = client
    return driver, client


def test_send_message_uses_markdown_body() -> None:
    """body 形状与实测一致：{"msgtype":"markdown","markdown":{"content":…}}。"""
    import asyncio

    driver, client = _driver_with_client()
    asyncio.run(driver.send_message("C1", "hello"))
    client.send_message.assert_awaited_once_with(
        "C1", {"msgtype": "markdown", "markdown": {"content": "hello"}}
    )


def test_send_message_without_client_raises() -> None:
    """连接未就绪（无 client）⇒ 抛 RuntimeError，不回落到任何默认会话。"""
    import asyncio

    driver = LongConnectionDriver("botA", "secret", AsyncMock())
    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(driver.send_message("C1", "hello"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/channels/test_long_connection_send.py -v`
Expected: FAIL（`AttributeError: 'LongConnectionDriver' object has no attribute 'send_message'`）

- [ ] **Step 3: 实现驱动方法**

在 `src/channels/wecom/long_connection.py` 的 `LongConnectionDriver` 内，`stop()` 之后插入：

```python
    async def send_message(self, chatid: str, content: str) -> None:
        """主动向指定会话推送一条消息（不依赖入站帧）。

        与 `reply_stream` 的区别：无入站 `req_id`，因而**没有流式更新语义**、
        也不能更新已发出的消息（多帧快照与消息更新均要求入站帧）。需要
        "先回执再结论"的调用方分两次调用。

        实测（docs/agents/wecom-sdk-facts.md E12/E12b）：可用，约 300ms ack；
        消息体为 `{"msgtype":"markdown","markdown":{"content":…}}`；不支持
        `msgtype=stream`（被拒 40008）；无效 chatid 报 93006。

        Args:
            chatid: 目标会话 id（群聊填群 chatid；单聊填用户 userid）
            content: 消息正文（markdown）

        Raises:
            RuntimeError: 连接未就绪，或 SDK 发送失败
        """
        client = self._client
        if client is None:
            raise RuntimeError("wecom driver not connected")
        await client.send_message(
            chatid, {"msgtype": "markdown", "markdown": {"content": content}}
        )
```

- [ ] **Step 4: 跑驱动层测试确认通过**

Run: `.venv/bin/python -m pytest tests/channels/test_long_connection_send.py -v`
Expected: 2 passed

- [ ] **Step 5: 写失败测试（服务层）**

创建 `tests/services/test_wecom_send.py`：

```python
"""wecom_service.send_to_session：主动推送的服务层出口单测。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.services import wecom_service


def test_send_to_session_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中驱动且发送成功 ⇒ True。"""
    driver = AsyncMock()
    monkeypatch.setitem(wecom_service._drivers, "dev", driver)
    assert asyncio.run(wecom_service.send_to_session("dev", "C1", "hi")) is True
    driver.send_message.assert_awaited_once_with("C1", "hi")


def test_send_to_session_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """机器人不存在/未启动 ⇒ False，不抛。"""
    monkeypatch.setattr(wecom_service, "_drivers", {})
    assert asyncio.run(wecom_service.send_to_session("ghost", "C1", "hi")) is False


def test_send_to_session_send_failure_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 发送失败（抛 RuntimeError）⇒ 记 warning 并返回 False。"""
    driver = AsyncMock()
    driver.send_message.side_effect = RuntimeError("errcode=93006 invalid chatid")
    monkeypatch.setitem(wecom_service._drivers, "dev", driver)
    assert asyncio.run(wecom_service.send_to_session("dev", "C1", "hi")) is False
```

- [ ] **Step 6: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/services/test_wecom_send.py -v`
Expected: FAIL（`AttributeError: module 'src.services.wecom_service' has no attribute 'send_to_session'`）

- [ ] **Step 7: 实现服务层出口**

在 `src/services/wecom_service.py` 末尾追加：

```python
async def send_to_session(bot_key: str, chatid: str, content: str) -> bool:
    """向指定会话主动推送一条消息；失败只记 warning 并返回 False。

    通道层对外只暴露"发到某个会话"这个语义，调用方不感知 SDK 细节。
    **不回落到默认会话**：目标由调用方显式给出，找不到就是失败。

    Args:
        bot_key: 机器人别名
        chatid: 目标会话 id
        content: 消息正文

    Returns:
        True 表示已发出（SDK ack 成功）；False 表示目标不存在或发送失败
    """
    driver = _drivers.get(bot_key)
    if driver is None or not isinstance(driver, LongConnectionDriver):
        logger.warning(
            "[wecom] send skipped reason=no_driver bot_key={}", encode_value(bot_key)
        )
        return False
    try:
        await driver.send_message(chatid, content)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[wecom] send failed bot_key={} chatid={} err={}",
            encode_value(bot_key),
            encode_value(chatid),
            e,
        )
        return False
    return True
```

- [ ] **Step 8: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/services/test_wecom_send.py tests/channels/test_long_connection_send.py -v`
Expected: 5 passed

- [ ] **Step 9: 提交**

```bash
git add src/channels/wecom/long_connection.py src/services/wecom_service.py tests/channels/test_long_connection_send.py tests/services/test_wecom_send.py
git commit -m "feat(wecom): 通道层新增主动推送出口（send_message 封装）"
```

---

## Task 3: `StreamingRunManager.cancel` —— 真取消的取消口子

**Files:**
- Modify: `src/chat/streaming.py`（在 `set_abort` 之后新增）
- Test: `tests/chat/test_streaming_cancel.py`

**Interfaces:**
- Consumes: `StreamingRunManager._session_tasks: dict[str, asyncio.Task]`
- Produces: `StreamingRunManager.cancel(session_id: str) -> bool`（无任务返回 `False`）

**背景:** `POST /api/sessions/cancel` 走归属校验，对 `user_id=全零 UUID` 的报警会话**无效**，故必须有进程内口子。`set_abort` 只是软信号、靠事件检查点生效，模型挂死时检查点永不到达 ⇒ 必须真 `task.cancel()`。

- [ ] **Step 1: 写失败测试**

创建 `tests/chat/test_streaming_cancel.py`：

```python
"""StreamingRunManager.cancel：硬取消单测。"""

import asyncio

import pytest

from src.chat.streaming import StreamingRunManager


def test_cancel_running_task_raises_cancelled() -> None:
    """cancel 使注册中的任务收到 CancelledError。"""

    async def _scenario() -> None:
        mgr = StreamingRunManager()

        async def _hang() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(_hang())
        mgr.register("s1", task, asyncio.Event())
        await asyncio.sleep(0)  # 让任务真正开始
        assert mgr.cancel("s1") is True
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_scenario())


def test_cancel_unknown_session_is_false() -> None:
    """无该会话任务 ⇒ False（不抛）。"""
    mgr = StreamingRunManager()
    assert mgr.cancel("nope") is False


def test_cancel_finished_task_is_false() -> None:
    """任务已结束 ⇒ False。"""

    async def _scenario() -> None:
        mgr = StreamingRunManager()

        async def _quick() -> None:
            return

        task = asyncio.create_task(_quick())
        mgr.register("s1", task, asyncio.Event())
        await task
        assert mgr.cancel("s1") is False

    asyncio.run(_scenario())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/chat/test_streaming_cancel.py -v`
Expected: FAIL（`AttributeError: 'StreamingRunManager' object has no attribute 'cancel'`）

- [ ] **Step 3: 实现**

在 `src/chat/streaming.py` 的 `set_abort` 之后插入：

```python
    def cancel(self, session_id: str) -> bool:
        """硬取消该 session 的生成任务（进程内口子）。

        与 `set_abort` 的区别：`set_abort` 只置位软信号、仅在**事件检查点**被
        消费——模型或出网挂死时检查点永不到达，生成会一直跑并占着会话锁；
        `cancel` 直接 `task.cancel()`，让生成走自身的取消路径收尾（该轮落
        `interrupted`）。`POST /api/sessions/cancel` 因归属校验对固定 user_id
        的会话无效，故告警轮用本方法。

        Args:
            session_id: 会话 ID

        Returns:
            True 表示确有活跃任务被取消；False 表示无任务或已结束
        """
        task = self._session_tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/chat/test_streaming_cancel.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add src/chat/streaming.py tests/chat/test_streaming_cancel.py
git commit -m "feat(chat): StreamingRunManager 新增 cancel（硬取消口子）"
```

---

## Task 4: 图双编译 —— 报警轮禁用澄清（`ask_user`）

**Files:**
- Modify: `src/agents/tools/rag_tools.py`（`make_rag_tools` 增参）
- Modify: `src/services/agent_service.py`（编译两套图、`stream_chat` 选图、`ctx.tool_names` 同步）
- Modify: `src/services/turn_runner.py`（`start_turn` 增参透传）
- Test: `tests/agents/test_rag_tools_no_ask_user.py`

**Interfaces:**
- Consumes: `ToolRegistry.register(..., enabled=...)`、`build_graph(..., tools=...)`
- Produces:
  - `make_rag_tools(vector_store, reranker, prompt_manager, delegate_task=None, *, include_ask_user: bool = True)`
  - `AgentService.stream_chat(kb_id, session_id, query, deep_thinking, agent="", *, clarify: bool = True)`
  - `turn_runner.start_turn(..., clarify: bool = True)`

**⚠️ 这不是"透传一个参数":** 图在 `AgentService.__init__`（`src/services/agent_service.py:868`）编译一次即固化，结果存 `self._graph` 并随 `launch_context` 传下去（`:1012`）。故必须**编译两套图**并按轮次选；`self._tool_names`（`:880`）与 `ctx.tool_names`（`:993`）也要跟着换，否则"段里写的工具面"与"实际可用工具面"不一致。

- [ ] **Step 1: 写失败测试**

创建 `tests/agents/test_rag_tools_no_ask_user.py`：

```python
"""make_rag_tools(include_ask_user=False) 不产出 ask_user 单测。"""

from src.agents.tools.rag_tools import make_rag_tools


class _FakeVS:
    """占位向量库（本用例不触发检索）。"""


def _names(**kwargs) -> set[str]:
    tools = make_rag_tools(_FakeVS(), object(), object(), **kwargs)
    return {str(t.name) for t in tools}


def test_ask_user_registered_by_default() -> None:
    """默认仍注册 ask_user（站点与企微问答不受影响）。"""
    assert "ask_user" in _names()


def test_ask_user_excluded_when_disabled() -> None:
    """include_ask_user=False ⇒ 不注册 ask_user，其余工具仍在。"""
    names = _names(include_ask_user=False)
    assert "ask_user" not in names
    assert "retrieve_kb" in names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/agents/test_rag_tools_no_ask_user.py -v`
Expected: FAIL（`TypeError: make_rag_tools() got an unexpected keyword argument 'include_ask_user'`）

- [ ] **Step 3: 给 `make_rag_tools` 加参数**

`src/agents/tools/rag_tools.py`：把签名改为

```python
def make_rag_tools(
    vector_store: VectorStore,
    reranker,
    prompt_manager,
    delegate_task: BaseTool | None = None,
    *,
    include_ask_user: bool = True,
) -> list[BaseTool]:
```

并在 docstring 的 Args 段补：

```
        include_ask_user: 是否注册 ask_user（澄清追问）。报警等**非交互轮次**
            传 False —— 无人可答，注册只会把该轮拖到澄清超时。
```

把注册行（当前 `registry.register("ask_user", ask_user, readonly=True)`）改为：

```python
    registry.register("ask_user", ask_user, readonly=True, enabled=include_ask_user)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/agents/test_rag_tools_no_ask_user.py -v`
Expected: 2 passed

- [ ] **Step 5: `AgentService` 编译两套图并按轮次选**

`src/services/agent_service.py`：

(a) 在 `__init__` 里、现有 `build_graph(...)` 建立 `self._graph` 之后，追加第二套图与工具名集合：

```python
        # 非交互轮次（如告警分析）禁用 ask_user：无人可答，注册只会拖到澄清超时。
        # 图是编译期固化的，故必须**再编译一套**并按轮次选择，而非透传参数。
        # ⚠️ 必须显式拼上 make_task_tools()：build_graph 只在 tools=None 的分支
        # 追加任务工具（src/agents/graph/workflow.py:87-91），传了 tools 就不再追加。
        # 漏掉会让报警轮丢整套任务工具（行为超出"仅禁澄清"）。
        from src.agents.tools.task_tools import make_task_tools

        no_clarify_tools = [
            *make_rag_tools(
                vector_store,
                self._reranker,
                self._prompt_manager,
                delegate_task=delegate_task_tool,
                include_ask_user=False,
            ),
            *make_task_tools(),
        ]
        self._graph_no_clarify: CompiledStateGraph = build_graph(
            vector_store,
            self._llm,
            self._reranker,
            self._prompt_manager,
            tools=no_clarify_tools,
            delegate_task=delegate_task_tool,
            tool_sink=fork_tool_pool_no_clarify,
            skill_direct_node=skill_direct_node,
        )
        self._tool_names_no_clarify: frozenset[str] = frozenset(
            str(t.name) for t in fork_tool_pool_no_clarify
        )
```

> 实现者注意：
> 1. `build_graph(..., tool_sink=...)` 需要**独立的** sink 列表（否则两套图的工具池会互相覆盖）。既有 sink 是局部变量 `fork_tool_pool: list = []`（`src/services/agent_service.py:828`，用在 `:874` 的 `tool_sink=` 与 `:881` 的工具名集合），**在它下方并列**新增 `fork_tool_pool_no_clarify: list = []`。
> 2. `agent_service.py` **当前并未导入** `make_rag_tools`（已核实 `grep` 无命中），需在 import 区新增 `from src.agents.tools.rag_tools import make_rag_tools`；`make_task_tools` 按上面代码块在函数内惰性导入（与 `workflow.py` 的既有写法一致）。

(b) 在 `stream_chat` 的签名末尾追加关键字参数（保持既有位置参数不变）：

```python
    async def stream_chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
        deep_thinking: bool = False,
        agent: str = "",
        *,
        clarify: bool = True,
    ) -> tuple[AsyncGenerator, dict]:
```

并把 `launch_context = { ... "graph": self._graph, ... }` 一行改为：

```python
            "graph": self._graph if clarify else self._graph_no_clarify,
```

把 `ctx.tool_names = self._tool_names` 一行改为：

```python
        ctx.tool_names = self._tool_names if clarify else self._tool_names_no_clarify
```

并在 `stream_chat` 的 docstring 参数段补：

```
        clarify: 是否允许澄清追问（False 用于报警等非交互轮次；切换为此编译的
            无 ask_user 图，并同步段组装用的工具名集合）
```

- [ ] **Step 6: `turn_runner.start_turn` 透传**

`src/services/turn_runner.py`：签名追加 `clarify: bool = True`（放在 `abort_signal` 之前，保持关键字-only 语义不变）：

```python
async def start_turn(
    svc: AppService,
    *,
    session_id: str,
    kb_id: str,
    query: str,
    user_id: str = "",
    deep_thinking: bool = False,
    agent: str = "",
    title: str | None = None,
    clarify: bool = True,
    abort_signal: asyncio.Event | None = None,
) -> TurnHandle:
```

docstring 补一行：

```
        clarify: 是否允许澄清追问（False 用于报警等非交互轮次，见 design D12）
```

并把内部调用改为（当前为 `svc.agent_service.stream_chat(kb_id, session_id, query, deep_thinking, agent=agent)`）：

```python
        _subscription, launch_ctx = await svc.agent_service.stream_chat(
            kb_id, session_id, query, deep_thinking, agent=agent, clarify=clarify
        )
```

- [ ] **Step 7: 跑相关测试**

Run: `.venv/bin/python -m pytest tests/agents/test_rag_tools_no_ask_user.py tests/services -k "turn or agent" -v`
Expected: 全绿（含既有 `start_turn` 相关用例——默认 `clarify=True` 行为不变）

- [ ] **Step 8: 提交**

```bash
git add src/agents/tools/rag_tools.py src/services/agent_service.py src/services/turn_runner.py tests/agents/test_rag_tools_no_ask_user.py
git commit -m "feat(agents): 支持按轮次禁用澄清（编译两套图，报警轮用无 ask_user 版）"
```

---

## Task 5: 载荷解析与会话标识派生

**Files:**
- Create: `src/services/alert_service.py`（本任务只建其中的纯函数部分）
- Test: `tests/services/test_alert_parse.py`

**Interfaces:**
- Consumes: `src.config.const.WECOM_NS`
- Produces（`src/services/alert_service.py` 模块级）：
  - `@dataclass(frozen=True) AlertEvent(alarm_id: str, alertname: str, level: str, details: str, fingerprints: tuple[str, ...])`
  - `parse_alert_payload(raw: bytes) -> AlertEvent`（非法抛 `ValueError`）
  - `derive_alert_session_id(bot_key: str, alarm_id: str) -> str`
  - `build_query(event: AlertEvent) -> str`
  - `render_fallback(event: AlertEvent, max_chars: int) -> str`
  - `render_placeholder(event: AlertEvent) -> str`

**载荷契约（design D21）:** ARMS 侧**必须**按本项目规定的自定义 body 模板推送；解析即按该模板的字段名。

- [ ] **Step 1: 写失败测试**

创建 `tests/services/test_alert_parse.py`：

```python
"""告警载荷解析、会话派生与文案渲染单测。"""

import json
import uuid

import pytest

from src.config.const import WECOM_NS, AlertTexts
from src.services.alert_service import (
    AlertEvent,
    build_query,
    derive_alert_session_id,
    parse_alert_payload,
    render_fallback,
    render_placeholder,
)

_SAMPLE = {
    "alarmId": "ALM-001",
    "level": "P2",
    "commonLabels": {"alertname": "CPU 使用率过高"},
    "alerts": [
        {"fingerprint": "fp-a", "annotations": {"message": "ecs-1 CPU 92%"}},
        {"fingerprint": "fp-b", "annotations": {"message": "ecs-2 CPU 95%"}},
    ],
}


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


def test_parse_ok() -> None:
    """正常载荷解析出全部字段，指纹逐条收集。"""
    ev = parse_alert_payload(_raw(_SAMPLE))
    assert ev.alarm_id == "ALM-001"
    assert ev.alertname == "CPU 使用率过高"
    assert ev.level == "P2"
    assert ev.fingerprints == ("fp-a", "fp-b")
    assert "ecs-1 CPU 92%" in ev.details
    assert "ecs-2 CPU 95%" in ev.details


def test_parse_bad_json() -> None:
    """非法 JSON ⇒ ValueError。"""
    with pytest.raises(ValueError, match="JSON"):
        parse_alert_payload(b"{not json")


def test_parse_missing_alarm_id() -> None:
    """缺报警标识 ⇒ ValueError（会话 key 依赖它）。"""
    payload = {k: v for k, v in _SAMPLE.items() if k != "alarmId"}
    with pytest.raises(ValueError, match="alarmId"):
        parse_alert_payload(_raw(payload))


def test_parse_no_fingerprint() -> None:
    """无任何 fingerprint ⇒ ValueError（去重依赖它）。"""
    payload = dict(_SAMPLE, alerts=[{"annotations": {"message": "x"}}])
    with pytest.raises(ValueError, match="fingerprint"):
        parse_alert_payload(_raw(payload))


def test_derive_alert_session_id_is_stable_and_isolated() -> None:
    """同 bot_key+alarmId 稳定；与群聊 scope 隔离；bot_key 参与哈希。"""
    a = derive_alert_session_id("dev", "ALM-001")
    assert a == derive_alert_session_id("dev", "ALM-001")
    assert a == str(uuid.uuid5(WECOM_NS, "wecom|dev|alert|ALM-001"))
    assert derive_alert_session_id("dev", "ALM-002") != a
    assert derive_alert_session_id("finance", "ALM-001") != a
    assert len(a) == 36


def test_build_query_uses_template() -> None:
    """query 按模板渲染，含名称/等级/正文。"""
    ev = parse_alert_payload(_raw(_SAMPLE))
    q = build_query(ev)
    assert "CPU 使用率过高" in q
    assert "P2" in q
    assert "ecs-2 CPU 95%" in q


def test_render_fallback_prefix_and_limit() -> None:
    """兜底带前缀、限长、不整包 dump。"""
    ev = parse_alert_payload(_raw(_SAMPLE))
    out = render_fallback(ev, max_chars=40)
    assert out.startswith(AlertTexts.FALLBACK_PREFIX)
    assert len(out) <= 40 + len(AlertTexts.FALLBACK_PREFIX) + 1
    assert "alarmId" not in out  # 不是原始 JSON dump


def test_render_placeholder() -> None:
    """占位文案含告警名称。"""
    ev = parse_alert_payload(_raw(_SAMPLE))
    assert "CPU 使用率过高" in render_placeholder(ev)


def test_alert_event_is_frozen() -> None:
    """AlertEvent 不可变（值对象）。"""
    ev = AlertEvent(
        alarm_id="a", alertname="n", level="P1", details="d", fingerprints=("f",)
    )
    with pytest.raises(Exception):
        ev.alarm_id = "b"  # type: ignore[misc]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/services/test_alert_parse.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.services.alert_service'`）

- [ ] **Step 3: 写实现（本任务部分）**

创建 `src/services/alert_service.py`：

```python
"""阿里云告警接入：解析、去重、编排、推送与兜底（change: alibaba-alert-notify）。

分层：`api/alerts.py` 只读请求头 token 并把 body 交给本模块；解析/去重/编排
一律落在本层（`api/` 不得直接 import `config/`、`infra/`）。

载荷契约（design D21）：ARMS 侧必须按本项目规定的自定义 body 模板推送
（默认模板不含 `alarmId`/`fingerprint`），字段名即本模块解析的字段名。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from src.config.const import WECOM_NS, AlertTexts


@dataclass(frozen=True)
class AlertEvent:
    """一次告警请求解析后的值对象。"""

    alarm_id: str  # 报警标识（派生会话 key）
    alertname: str  # 告警名称
    level: str  # 告警等级
    details: str  # 各条告警描述的合并文本
    fingerprints: tuple[str, ...]  # 逐条告警指纹（入口去重键）


def parse_alert_payload(raw: bytes) -> AlertEvent:
    """解析 ARMS 通知载荷（按约定模板的字段名）。

    Args:
        raw: 原始请求体字节

    Returns:
        AlertEvent

    Raises:
        ValueError: JSON 非法，或缺报警标识 / 缺告警名称 / 无任何指纹
    """
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("告警载荷不是合法 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("告警载荷必须是 JSON 对象")  # noqa: TRY004

    alarm_id = data.get("alarmId")
    if not isinstance(alarm_id, str) or alarm_id == "":
        raise ValueError("告警载荷缺 alarmId")

    labels = data.get("commonLabels") or {}
    alertname = labels.get("alertname") if isinstance(labels, dict) else None
    if not isinstance(alertname, str) or alertname == "":
        raise ValueError("告警载荷缺 commonLabels.alertname")

    level_raw = data.get("level")
    level = level_raw if isinstance(level_raw, str) and level_raw else "-"

    raw_alerts = data.get("alerts")
    alerts = raw_alerts if isinstance(raw_alerts, list) else []
    fingerprints: list[str] = []
    messages: list[str] = []
    for item in alerts:
        if not isinstance(item, dict):
            continue
        fp = item.get("fingerprint")
        if isinstance(fp, str) and fp:
            fingerprints.append(fp)
        annotations = item.get("annotations") or {}
        msg = annotations.get("message") if isinstance(annotations, dict) else None
        if isinstance(msg, str) and msg:
            messages.append(msg)
    if not fingerprints:
        raise ValueError("告警载荷无有效 fingerprint")

    return AlertEvent(
        alarm_id=alarm_id,
        alertname=alertname,
        level=level,
        details="\n".join(messages),
        fingerprints=tuple(fingerprints),
    )


def derive_alert_session_id(bot_key: str, alarm_id: str) -> str:
    """派生报警会话标识（`scope=alert`，与群聊/单聊问答会话隔离）。

    同一报警标识复用同一会话（历史累积）；标识每次都变时自动退化为"每次新会话"。

    Args:
        bot_key: 机器人别名
        alarm_id: 报警标识

    Returns:
        36 字符 UUIDv5 字符串
    """
    return str(uuid.uuid5(WECOM_NS, f"wecom|{bot_key}|alert|{alarm_id}"))


def build_query(event: AlertEvent) -> str:
    """按模板构造喂给 Agent 的输入文本。"""
    return AlertTexts.QUERY_TEMPLATE.format(
        alertname=event.alertname, level=event.level, details=event.details
    )


def render_placeholder(event: AlertEvent) -> str:
    """受理占位文案（第一段推送）。"""
    return AlertTexts.PLACEHOLDER_TEMPLATE.format(alertname=event.alertname)


def render_fallback(event: AlertEvent, max_chars: int) -> str:
    """兜底转发文案：带前缀、限长、不整包 dump 原始载荷。

    Args:
        event: 解析后的事件
        max_chars: 正文长度上限（前缀不计入）

    Returns:
        形如 `[原始告警] CPU 使用率过高（P2）\n<正文截断>`
    """
    body = f"{event.alertname}（{event.level}）\n{event.details}".strip()
    if len(body) > max_chars:
        body = body[:max_chars]
    return f"{AlertTexts.FALLBACK_PREFIX} {body}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/services/test_alert_parse.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/services/alert_service.py tests/services/test_alert_parse.py
git commit -m "feat(alert): 载荷解析、会话标识派生与文案渲染"
```

---

## Task 6: 入口去重（逐条指纹 + 有界时间窗）

**Files:**
- Modify: `src/services/alert_service.py`（追加去重器）
- Test: `tests/services/test_alert_dedup.py`

**Interfaces:**
- Consumes: `src.channels.wecom.bounded_map.BoundedTtlMap`（`capacity` / `ttl_seconds` / `monotonic`、`get` / `put`）
- Produces: `AlertDedup(capacity: int, ttl_seconds: float, monotonic=time.monotonic)` 与 `AlertDedup.is_duplicate(fingerprints: Sequence[str]) -> bool`

**语义（design D8）:** 仅当**全部**指纹都命中窗口时判为重复（宁可多跑一条，不漏报）；未判重时把全部指纹登记进窗口。

- [ ] **Step 1: 写失败测试**

创建 `tests/services/test_alert_dedup.py`：

```python
"""入口去重单测（逐条指纹 + 时间窗）。"""

from src.services.alert_service import AlertDedup


def test_first_time_not_duplicate() -> None:
    """首次见到的指纹不是重复。"""
    d = AlertDedup(capacity=10, ttl_seconds=300, monotonic=lambda: 0.0)
    assert d.is_duplicate(["a", "b"]) is False


def test_all_seen_is_duplicate() -> None:
    """全部指纹都在窗口内 ⇒ 重复。"""
    d = AlertDedup(capacity=10, ttl_seconds=300, monotonic=lambda: 0.0)
    d.is_duplicate(["a", "b"])
    assert d.is_duplicate(["a", "b"]) is True


def test_partial_new_is_not_duplicate() -> None:
    """存在未命中的指纹 ⇒ 不判重（宁可多跑，不漏报）。"""
    d = AlertDedup(capacity=10, ttl_seconds=300, monotonic=lambda: 0.0)
    d.is_duplicate(["a"])
    assert d.is_duplicate(["a", "b"]) is False


def test_window_expiry_allows_again() -> None:
    """窗口过期后可再次处理。"""
    now = {"t": 0.0}
    d = AlertDedup(capacity=10, ttl_seconds=300, monotonic=lambda: now["t"])
    d.is_duplicate(["a"])
    now["t"] = 301.0
    assert d.is_duplicate(["a"]) is False


def test_capacity_bounds_memory() -> None:
    """容量上限：超限淘汰最旧（有界，不无界增长）。"""
    d = AlertDedup(capacity=2, ttl_seconds=300, monotonic=lambda: 0.0)
    d.is_duplicate(["a"])
    d.is_duplicate(["b"])
    d.is_duplicate(["c"])
    assert len(d) <= 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/services/test_alert_dedup.py -v`
Expected: FAIL（`ImportError: cannot import name 'AlertDedup'`）

- [ ] **Step 3: 实现**

在 `src/services/alert_service.py` 追加（顶部 import 区补 `import time`、`from collections.abc import Callable, Sequence`、`from src.channels.wecom.bounded_map import BoundedTtlMap`）：

```python
class AlertDedup:
    """入口去重：逐条指纹 + 时间窗的有界结构。

    仅当**全部**指纹都命中窗口时判为重复（宁可多跑一条 Agent，也不漏报）；
    判为不重复时把全部指纹登记进窗口。
    """

    def __init__(
        self,
        *,
        capacity: int,
        ttl_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化。

        Args:
            capacity: 指纹条目容量上限（超出淘汰最旧）
            ttl_seconds: 指纹存活秒数（自登记起算）
            monotonic: 单调时钟（测试注入以稳定断言过期）
        """
        self._map = BoundedTtlMap(
            capacity=capacity, ttl_seconds=ttl_seconds, monotonic=monotonic
        )

    def is_duplicate(self, fingerprints: Sequence[str]) -> bool:
        """判断本批指纹是否全部在窗口内出现过。

        Args:
            fingerprints: 本条请求涉及的全部指纹（调用方保证非空）

        Returns:
            True 表示全部命中（应丢弃）；False 表示应正常处理（已登记本批）
        """
        if all(self._map.get(fp) is not None for fp in fingerprints):
            return True
        for fp in fingerprints:
            self._map.put(fp, "1")
        return False

    def __len__(self) -> int:
        """当前窗口内登记条目数（测试用）。"""
        return len(self._map)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/services/test_alert_dedup.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add src/services/alert_service.py tests/services/test_alert_dedup.py
git commit -m "feat(alert): 入口逐条指纹去重（有界时间窗）"
```

---

## Task 7: 告警接收端点（鉴权 + 立即应答）

**Files:**
- Modify: `src/services/alert_service.py`（新增 `handle_webhook`）
- Create: `src/api/alerts.py`
- Modify: `src/api/__init__.py`、`src/main.py`
- Test: `tests/api/test_alerts_endpoint.py`

**Interfaces:**
- Consumes: `load_alert_config()`、`parse_alert_payload()`、`AlertDedup`、`asyncio.create_task`
- Produces:
  - `alert_service.handle_webhook(raw_body: bytes, token_header: str | None) -> int`（返回 HTTP 状态码）
  - `src.api.alerts.router`（`POST /alerts/webhook`）

**契约（design D2/F1/F2/F5）:**
- token 从**请求头**读；不匹配或未配置 ⇒ **404**（不暴露端点存在）
- 校验 + 去重通过 ⇒ **立即 202**；分析与推送放后台任务
- **去重丢弃同样返回 202**（否则上游按失败重试）
- 载荷非法 ⇒ **400**

- [ ] **Step 1: 写失败测试**

创建 `tests/api/test_alerts_endpoint.py`：

```python
"""告警接收端点：鉴权与立即应答单测。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import alerts as alerts_api

_HEADER = "X-Alert-Token"


@pytest.fixture
def client() -> TestClient:
    """只挂告警路由的测试应用。"""
    app = FastAPI()
    app.include_router(alerts_api.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置好 token，并清空进程级去重窗口（避免用例间互相污染）。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "tok")
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    alerts_api.alert_service._reset_for_test()


def _body() -> bytes:
    return json.dumps(
        {
            "alarmId": "ALM-001",
            "level": "P2",
            "commonLabels": {"alertname": "CPU 高"},
            "alerts": [{"fingerprint": "fp-a", "annotations": {"message": "m"}}],
        }
    ).encode()


def test_token_missing_is_404(client: TestClient, configured: None) -> None:
    resp = client.post("/api/alerts/webhook", content=_body())
    assert resp.status_code == 404


def test_token_wrong_is_404(client: TestClient, configured: None) -> None:
    resp = client.post("/api/alerts/webhook", content=_body(), headers={_HEADER: "bad"})
    assert resp.status_code == 404


def test_token_correct_returns_202(client: TestClient, configured: None) -> None:
    resp = client.post("/api/alerts/webhook", content=_body(), headers={_HEADER: "tok"})
    assert resp.status_code == 202


def test_unconfigured_token_always_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALERT_WEBHOOK_TOKEN", raising=False)
    resp = client.post("/api/alerts/webhook", content=_body(), headers={_HEADER: "tok"})
    assert resp.status_code == 404


def test_bad_payload_is_400(client: TestClient, configured: None) -> None:
    resp = client.post(
        "/api/alerts/webhook", content=b"{not json", headers={_HEADER: "tok"}
    )
    assert resp.status_code == 400


def test_duplicate_still_202(client: TestClient, configured: None) -> None:
    """第二条（全部指纹命中）仍返回 202，不是错误码。"""
    h = {_HEADER: "tok"}
    assert client.post("/api/alerts/webhook", content=_body(), headers=h).status_code == 202
    assert client.post("/api/alerts/webhook", content=_body(), headers=h).status_code == 202
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/api/test_alerts_endpoint.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.api.alerts'`）

- [ ] **Step 3: 实现 `alert_service.handle_webhook`**

在 `src/services/alert_service.py` 追加（import 区补 `import asyncio`、`import hmac`、`from loguru import logger`、`from src.config.alert import AlertConfig, AlertTarget, load_alert_config`、`from src.core.logging import encode_value`）：

```python
# 进程级单例：载荷去重窗口与配置随进程生命周期（与企微桥接 handler 同款纪律）
_dedup: AlertDedup | None = None


def _get_dedup() -> AlertDedup:
    """惰性构造去重器（配置在首次调用时读取）。"""
    global _dedup
    if _dedup is None:
        cfg = load_alert_config()
        _dedup = AlertDedup(
            capacity=cfg.dedup_capacity, ttl_seconds=cfg.dedup_window_seconds
        )
    return _dedup


def _reset_for_test() -> None:
    """测试钩子：丢弃进程级去重器与配置缓存。"""
    global _dedup
    _dedup = None


def handle_webhook(raw_body: bytes, token_header: str | None) -> int:
    """处理一次告警请求，返回 HTTP 状态码。

    校验与去重通过后**立即**返回 202，把"跑 Agent → 推送"放进后台任务 ——
    上游 Webhook 有 5 秒硬超时，等待分析会把响应拖过上限并触发重试。

    Args:
        raw_body: 原始请求体
        token_header: 请求头里的 token（缺失为 None）

    Returns:
        202 已受理（含去重丢弃）；404 token 不匹配或未配置；400 载荷非法
    """
    cfg = load_alert_config()
    if cfg.token == "" or not hmac.compare_digest(token_header or "", cfg.token):
        return 404
    try:
        event = parse_alert_payload(raw_body)
    except ValueError as e:
        logger.warning("[alert] payload invalid err={}", e)
        return 400
    if _get_dedup().is_duplicate(event.fingerprints):
        logger.info(
            "[alert] duplicate dropped alarm_id={} n_fp={}",
            encode_value(event.alarm_id),
            len(event.fingerprints),
        )
        return 202
    asyncio.create_task(
        _run_analysis(cfg, event, target=cfg.target, bot_key=cfg.target.bot_key if cfg.target else "")
    )
    return 202
```

> `_run_analysis(cfg, event, *, target, bot_key)` 在 **Task 8** 实现；本任务先建一个最小可编译存根，Task 8 替换：
>
> ```python
> async def _run_analysis(cfg: AlertConfig, event: AlertEvent, *, target: AlertTarget | None, bot_key: str) -> None:
>     """占位：Task 8 实现完整编排。"""
>     return None
> ```
>
> ⚠️ 实现者：存根**必须**在 Task 8 被真正实现替换，不得留在最终提交里。

- [ ] **Step 4: 实现端点 `src/api/alerts.py`**

```python
"""告警接收路由（POST /api/alerts/webhook）。

极薄：只读请求头 token 与原始 body，其余全部委托 `alert_service`。
不声明 Pydantic 请求体 —— 避免框架校验把 400 改写成 422/500 统一信封。
"""

from fastapi import APIRouter, Request
from starlette.responses import Response

from src.config.const import ALERT_WEBHOOK_PATH
from src.services import alert_service

# 鉴权请求头名（ARMS 自定义 Header 承载；不进 URL，避免被访问日志记录）
ALERT_TOKEN_HEADER: str = "X-Alert-Token"

router = APIRouter()


@router.post(ALERT_WEBHOOK_PATH)
async def alert_webhook(request: Request) -> Response:
    """接收阿里云 ARMS 告警通知。"""
    status_code = alert_service.handle_webhook(
        await request.body(), request.headers.get(ALERT_TOKEN_HEADER)
    )
    return Response(status_code=status_code)
```

- [ ] **Step 5: 注册路由**

`src/api/__init__.py`：在 import 区加 `from src.api.alerts import router as alerts_router`，并把 `"alerts_router"` 加进 `__all__`。

`src/main.py`：在既有 `include_router` 之后加一行

```python
app.include_router(alerts_router, prefix="/api", tags=["alerts"])
```

并在 `src/main.py` 的 api import 处把 `alerts_router` 一并导入（与既有一行 import 同源，按文件里现有写法对齐）。

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/api/test_alerts_endpoint.py -v`
Expected: 6 passed

- [ ] **Step 7: 提交**

```bash
git add src/api/alerts.py src/api/__init__.py src/main.py src/services/alert_service.py tests/api/test_alerts_endpoint.py
git commit -m "feat(api): 新增告警接收端点（请求头鉴权、收包即返）"
```

---

## Task 8: 编排 —— 跑 Agent、两段推送、超时真取消、兜底

**Files:**
- Modify: `src/services/alert_service.py`（用完整实现替换 Task 7 的存根，并新增内部辅助）
- Test: `tests/services/test_alert_orchestration.py`

**Interfaces:**
- Consumes: `turn_runner.start_turn`（`clarify=False`）、`turn_runner.TurnBusy`、`streaming_manager`、`wecom_service.send_to_session`、`src.chat.process_log.serialize_process`、`current_channel` / `new_trace_id`
- Produces（模块级私有，测试可通过 `alert_service._run_analysis` 直接调用）: `_run_analysis(cfg, event, *, target, bot_key) -> None`

**行为（design D13/D14/D15/D19/D20）:**
1. 设渠道上下文 `Channel.ALERT`（在**后台协程内**、`start_turn` 之前）
2. 先推**受理占位**（在后台流中，不在响应路径）；失败只 warning
3. `start_turn(..., clarify=False, kb_id="", agent="", user_id=ALERT_USER_ID)`
4. 用 `asyncio.wait_for` 包住事件消费，超时 ⇒ `streaming_manager.cancel(session_id)` 后走兜底
5. 正常完成 ⇒ 用 `serialize_process` 取**净化正文**；`strip()` 为空 ⇒ 兜底
6. `TurnBusy` ⇒ 兜底，**不**变 5xx

- [ ] **Step 1: 写失败测试**

创建 `tests/services/test_alert_orchestration.py`：

```python
"""告警编排：两段推送 / 兜底 / 超时 / 繁忙单测。"""

import asyncio

import pytest

from src.config.alert import AlertConfig, AlertTarget
from src.services import alert_service, turn_runner
from src.services.alert_service import AlertEvent
from src.utils.sse import SSEDoneEvent, SSETokenEvent

_TARGET = AlertTarget(bot_key="dev", chatid="C1")
_EVENT = AlertEvent(
    alarm_id="ALM-001",
    alertname="CPU 高",
    level="P2",
    details="ecs-1 CPU 92%",
    fingerprints=("fp-a",),
)


def _cfg() -> AlertConfig:
    return AlertConfig(
        token="tok",
        target=_TARGET,
        dedup_window_seconds=300.0,
        dedup_capacity=16,
        turn_timeout_seconds=5.0,
        fallback_max_chars=200,
    )


class _Handle:
    """假 TurnHandle：events 为给定的异步生成器。"""

    def __init__(self, events) -> None:
        self.events = events


def _install(monkeypatch, *, events_factory, sent: list[str]) -> None:
    """替换 start_turn 与通道出口，并记录推送。"""

    async def _fake_start_turn(svc, **kwargs):
        assert kwargs["clarify"] is False, "报警轮必须禁用澄清"
        return _Handle(events_factory())

    async def _fake_send(bot_key, chatid, content):
        sent.append(content)
        return True

    async def _fake_svc():
        return object()

    monkeypatch.setattr(alert_service.turn_runner, "start_turn", _fake_start_turn)
    monkeypatch.setattr(alert_service, "_get_service", _fake_svc, raising=False)
    monkeypatch.setattr(alert_service.wecom_service, "send_to_session", _fake_send)


def test_two_stage_push_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功：先占位、后结论（净化正文），两条独立消息。"""
    sent: list[str] = []

    async def _events():
        yield SSETokenEvent(token="旁白：我先检索")
        yield SSETokenEvent(token="结论正文")
        yield SSEDoneEvent()

    _install(monkeypatch, events_factory=_events, sent=sent)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET, bot_key="dev"))
    assert len(sent) == 2
    assert "CPU 高" in sent[0]  # 占位
    assert sent[1].strip() == "结论正文"  # 净化正文（旁白剔除）


def test_fallback_on_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """结论为空 ⇒ 兜底（带前缀），不推空气泡。"""
    sent: list[str] = []

    async def _events():
        yield SSEDoneEvent()

    _install(monkeypatch, events_factory=_events, sent=sent)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET, bot_key="dev"))
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)


def test_fallback_on_turn_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """TurnBusy ⇒ 兜底（不 5xx、不静默丢失）。"""
    sent: list[str] = []

    async def _busy(svc, **kwargs):
        raise turn_runner.TurnBusy("busy")

    async def _fake_send(bot_key, chatid, content):
        sent.append(content)
        return True

    async def _fake_svc():
        return object()

    monkeypatch.setattr(alert_service.turn_runner, "start_turn", _busy)
    monkeypatch.setattr(alert_service, "_get_service", _fake_svc, raising=False)
    monkeypatch.setattr(alert_service.wecom_service, "send_to_session", _fake_send)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET, bot_key="dev"))
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)


def test_timeout_cancels_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时 ⇒ 硬取消该轮 + 兜底。"""
    sent: list[str] = []
    cancelled: list[str] = []

    async def _events():
        await asyncio.sleep(3600)
        yield SSEDoneEvent()  # pragma: no cover

    class _Mgr:
        def cancel(self, session_id: str) -> bool:
            cancelled.append(session_id)
            return True

    _install(monkeypatch, events_factory=_events, sent=sent)
    monkeypatch.setattr(alert_service, "streaming_manager", _Mgr(), raising=False)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=_TARGET, bot_key="dev"))
    assert cancelled, "超时必须调用 streaming_manager.cancel"
    assert sent[-1].startswith(alert_service.AlertTexts.FALLBACK_PREFIX)


def test_no_target_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置目标群 ⇒ 不发送并记 warning，流程不失败。"""
    sent: list[str] = []

    async def _events():
        yield SSETokenEvent(token="正文")
        yield SSEDoneEvent()

    _install(monkeypatch, events_factory=_events, sent=sent)
    asyncio.run(alert_service._run_analysis(_cfg(), _EVENT, target=None, bot_key=""))
    assert sent == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/services/test_alert_orchestration.py -v`
Expected: FAIL（存根不做任何推送 ⇒ 断言失败）

- [ ] **Step 3: 用完整实现替换存根**

把 Task 7 里 `_run_analysis` 的存根整体替换为：

```python
async def _get_service():
    """取 AppService（惰性导入，避免 services 内部循环依赖）。"""
    from src.services.app_service import get_app_service

    return await get_app_service()


async def _consume_events(events) -> tuple[list[dict], bool]:
    """消费事件流，收集 events_log 与是否正常收尾。

    Args:
        events: TurnHandle.events

    Returns:
        (events_log, abnormal) —— abnormal 表示收到 error 事件、或流在无 done
        的情况下结束
    """
    events_log: list[dict] = []
    seen_done = False
    abnormal = False
    async for ev in events:
        if isinstance(ev, SSEDoneEvent):
            seen_done = True
        if isinstance(ev, SSEErrorEvent):
            abnormal = True
        events_log.append({"type": ev.type, "payload": ev.payload_for_buffer()})
    return events_log, abnormal or not seen_done


async def _run_analysis(
    cfg: AlertConfig, event: AlertEvent, *, target: AlertTarget | None, bot_key: str
) -> None:
    """后台编排：占位 → 跑 Agent（禁澄清）→ 结论 / 兜底。

    Args:
        cfg: 告警配置
        event: 解析后的事件
        target: 目标群（None = 未配置，不发送）
        bot_key: 机器人别名
    """
    if target is None:
        logger.warning(
            "[alert] no target configured alarm_id={}", encode_value(event.alarm_id)
        )
        return

    # 占位必须在后台流里发（不在响应路径上）：等 ack 的超时常量与上游 5 秒同量级
    await _safe_send(target, render_placeholder(event))

    session_id = derive_alert_session_id(target.bot_key, event.alarm_id)
    channel_token = current_channel.set(Channel.ALERT)
    trace_token = current_trace_id.set(new_trace_id())
    try:
        try:
            svc = await _get_service()
            handle = await turn_runner.start_turn(
                svc,
                session_id=session_id,
                kb_id="",
                query=build_query(event),
                user_id=ALERT_USER_ID,
                title=f"[企微·{target.bot_key}] 报警 {event.alertname}",
                clarify=False,
            )
        except turn_runner.TurnBusy:
            logger.warning(
                "[alert] turn busy alarm_id={}", encode_value(event.alarm_id)
            )
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[alert] turn start failed alarm_id={} err={}",
                encode_value(event.alarm_id),
                e,
            )
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return

        try:
            events_log, abnormal = await asyncio.wait_for(
                _consume_events(handle.events), timeout=cfg.turn_timeout_seconds
            )
        except TimeoutError:
            logger.warning(
                "[alert] turn timeout alarm_id={} timeout={}s",
                encode_value(event.alarm_id),
                cfg.turn_timeout_seconds,
            )
            streaming_manager.cancel(session_id)
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[alert] turn consume failed alarm_id={} err={}",
                encode_value(event.alarm_id),
                e,
            )
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return

        _, purified = serialize_process(events_log)
        if abnormal or purified.strip() == "":
            logger.warning(
                "[alert] empty or abnormal answer alarm_id={} abnormal={}",
                encode_value(event.alarm_id),
                abnormal,
            )
            await _safe_send(target, render_fallback(event, cfg.fallback_max_chars))
            return
        await _safe_send(target, purified)
    finally:
        current_trace_id.reset(trace_token)
        current_channel.reset(channel_token)


async def _safe_send(target: AlertTarget, content: str) -> None:
    """推送一段文本到目标群；失败只记 warning，不中断流程。

    Args:
        target: 目标
        content: 正文
    """
    ok = await wecom_service.send_to_session(target.bot_key, target.chatid, content)
    if not ok:
        logger.warning(
            "[alert] push failed bot_key={} chatid={}",
            encode_value(target.bot_key),
            encode_value(target.chatid),
        )
```

并在 `src/services/alert_service.py` 的 import 区补齐为完整的一版（已按仓库实际路径核实；**与 Task 7 已加的行合并，勿重复**）：

```python
import asyncio
import hmac
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.channels.wecom.bounded_map import BoundedTtlMap
from src.chat.process_log import serialize_process
from src.chat.streaming import streaming_manager  # 共享单例：必须与 turn_runner 同一个
from src.config.alert import AlertConfig, AlertTarget, load_alert_config
from src.config.const import ALERT_USER_ID, WECOM_NS, AlertTexts, Channel
from src.core.logging import encode_value
from src.infra.llm.trace_context import current_channel, current_trace_id
from src.infra.llm.tracing import new_trace_id
from src.services import turn_runner, wecom_service
from src.utils.sse import SSEDoneEvent, SSEErrorEvent
```

> 路径已核实：`current_channel` / `current_trace_id` 在 `src/infra/llm/trace_context.py`（`:14`/`:17`）；`new_trace_id` 在 `src/infra/llm/tracing.py:39`；`streaming_manager` 单例在 `src/chat/streaming.py:232`（**必须 import 这个共享单例**，自己 new 一个会让 `cancel` 打不到真实生成任务）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/services/test_alert_orchestration.py -v`
Expected: 5 passed

- [ ] **Step 5: 跑告警全套回归**

Run: `.venv/bin/python -m pytest tests/services/test_alert_config.py tests/services/test_alert_parse.py tests/services/test_alert_dedup.py tests/services/test_alert_orchestration.py tests/api/test_alerts_endpoint.py -v`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add src/services/alert_service.py tests/services/test_alert_orchestration.py
git commit -m "feat(alert): 编排（两段推送、超时硬取消、兜底转发）"
```

---

## Task 9: 配置模板与文档登记

**Files:**
- Modify: `.env.template`、`.env.example`（**两个都要改**）
- Modify: `docs/agents/api_contract.md`、`docs/agents/code-map.md`、`docs/agents/logging-rules.md`、`docs/agents/deploy-runbook.md`

**Interfaces:**
- Consumes: 前 8 个任务的产物
- Produces: 可被运维照做的文档与可复制的 env 模板

- [ ] **Step 1: 同步两个 env 模板**

**两个文件都要改**（`.env.template` 是 README 让人 `cp` 的模板，`.env.example` 是键名清单）：

```
# ── 阿里云告警接入（alibaba-alert-notify） ──
# 接收端点鉴权 token（请求头 X-Alert-Token）。空 = 未启用，端点恒 404。
ALERT_WEBHOOK_TOKEN=
# 目标群（单目标 JSON）：bot_key 复用 WECOM_BOTS 里的 key；chatid 从 sessions.title 读出
ALERT_TARGET=
# 入口去重时间窗（秒）与容量上限
ALERT_DEDUP_WINDOW_SECONDS=300
ALERT_DEDUP_CAPACITY=4096
# 整轮分析超时（秒）与兜底消息长度上限
ALERT_TURN_TIMEOUT_SECONDS=240
ALERT_FALLBACK_MAX_CHARS=1500
```

- [ ] **Step 2: 登记 API 契约**

`docs/agents/api_contract.md` 新增一节（表头与行格式对齐该文件既有约定）：

- `POST /api/alerts/webhook` — 阿里云 ARMS 告警接入
- 鉴权：请求头 `X-Alert-Token`（**不放 URL**，避免被访问日志记录）；不匹配或未配置 → **404**
- 请求体：ARMS 自定义模板渲染的 JSON（字段：`alarmId` / `level` / `commonLabels.alertname` / `alerts[].fingerprint` / `alerts[].annotations.message`）
- 响应：**202**（已受理，含去重丢弃）/ 400（载荷非法）/ 404（鉴权失败）
- 约束：**必须在 5 秒内返回**（ARMS Webhook 硬超时）；分析与推送在后台任务

- [ ] **Step 3: 登记代码结构**

`docs/agents/code-map.md`：在 `src/services/` 段加 `alert_service.py`（告警解析/去重/编排），在 `src/config/` 段加 `alert.py`（告警配置），在 `src/api/` 段加 `alerts.py`。

- [ ] **Step 4: 登记渠道值**

`docs/agents/logging-rules.md` 的渠道表加一行：`alert` — 阿里云告警自动分析轮次（`Channel.ALERT`）。

- [ ] **Step 5: 登记部署步骤与 ARMS 模板**

`docs/agents/deploy-runbook.md` 增加「告警接入」小节：

1. 目标机 `.env` 写 `ALERT_WEBHOOK_TOKEN` 与 `ALERT_TARGET`（先同步 `.env`，再上新制品）
2. ARMS 侧建「通用 Webhook」通知对象：URL `https://<站点>/api/alerts/webhook`、自定义 Header `X-Alert-Token: <token>`、**自定义 body 模板为必需项**（默认模板不含 `alarmId`/`fingerprint`），模板正文即解析契约，示例：

```gotemplate
{"alarmId":"{{ .alarmId }}","level":"{{ .level }}","commonLabels":{"alertname":"{{ .commonLabels.alertname }}"},"alerts":[{{ range $i, $a := .alerts }}{{ if $i }},{{ end }}{"fingerprint":"{{ $a.fingerprint }}","annotations":{"message":"{{ $a.annotations.message }}"}}{{ end }}]}
```

3. 在通知策略里引用该通知对象；用「发送测试」抓一份真实 body 核对字段名
4. 首次取群 `chatid`：在目标群 @ 一次机器人 → 查 `sessions.title`（`[企微·dev] 群聊 <chatid>`）→ 填入 `ALERT_TARGET` → 重新发布
5. 冒烟：真实触发一次告警 → 群里**先收占位、后收结论**；再造一次失败 → 兜底带 `[原始告警]` 前缀

- [ ] **Step 6: 跑文档闸门**

Run: `.venv/bin/python -m src.cli.check_docs`
Expected: `0 error`（warn 数不高于改动前）

- [ ] **Step 7: 提交**

```bash
git add .env.template .env.example docs/agents/api_contract.md docs/agents/code-map.md docs/agents/logging-rules.md docs/agents/deploy-runbook.md
git commit -m "docs(alert): 登记端点契约、模块、渠道值与 ARMS 接入步骤"
```

---

## 验收（对照 change 的 tasks 组 9）

部署到能连企微通道的环境后：

- [ ] 真实触发一次告警 → 群里**先收到受理占位、后收到结论**
- [ ] 制造一次分析失败 → 兜底转发生效且带 `[原始告警]` 前缀
- [ ] 抓一份真实 payload 落日志（脱敏）→ 确认字段与约定模板一致、`alarmId` 稳定性
- [ ] 确认端点在上游 5 秒内返回（观察 ARMS 有无超时重试）
- [ ] 确认报警会话不出现在普通用户的会话列表
- [ ] 同一条告警二次触发（窗口外）→ 复用同一会话（历史累积）

---

## Self-Review 记录

**Spec 覆盖对照：**

| spec 需求 | 任务 |
|---|---|
| 告警接收端点以请求头 token 鉴权 | Task 7 |
| 端点立即应答，不等待分析 | Task 7（`asyncio.create_task` 后即返回 202） |
| 告警载荷解析与必填校验 | Task 5 |
| 入口按告警指纹去重 | Task 6 |
| 报警会话与问答会话隔离，且按报警标识归并 | Task 5（`derive_alert_session_id`） |
| 报警轮不接受澄清追问 | Task 4 |
| 分析轮次有超时（真取消） | Task 3 + Task 8 |
| 失败、空回答或繁忙时兜底转发 | Task 8 |
| 主动推送分两段（含净化正文） | Task 8 |
| `wecom-channel`：主动推送消息到指定会话 | Task 2 |
| 渠道上下文（`Channel.ALERT`） | Task 1 + Task 8 |
| 配置项集中在 `src/config/` | Task 1 |
| 两个 env 模板同步 | Task 9 |
| 文档登记（api_contract/code-map/logging-rules/deploy-runbook） | Task 9 |

**类型一致性检查：** `AlertConfig` / `AlertTarget` / `AlertEvent` / `AlertDedup` / `derive_alert_session_id` / `handle_webhook` / `_run_analysis` / `send_to_session` / `send_message` / `cancel` 在各任务的 Interfaces 段定义一次，后续任务按同名同签名消费。

**导入路径与变量名（已实测核实，非推测）：**
- `current_channel` / `current_trace_id` → `src/infra/llm/trace_context.py:14,17`
- `new_trace_id` → `src/infra/llm/tracing.py:39`
- `streaming_manager` 共享单例 → `src/chat/streaming.py:232`
- 既有工具池 sink 局部变量 `fork_tool_pool` → `src/services/agent_service.py:828`
- `BoundedTtlMap(capacity=, ttl_seconds=, monotonic=)` / `get` / `put` → `src/channels/wecom/bounded_map.py`（`mark_if_new` 存在，但去重用的是 `get`+`put`，因为"全部命中才丢弃"需要先判断再登记）
- `Channel` 类内以 `str` 常量定义渠道值 → `src/config/const.py:196`
- `make_rag_tools` 的 `enabled` 过滤经 `ToolRegistry.register(..., enabled=)` → `enabled_tools()`（`src/agents/tools/registry.py:71-73`）
