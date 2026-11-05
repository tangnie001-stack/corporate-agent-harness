"""启动期日志工具装配（`src.main._setup_log_tools`）与 lifespan 调用顺序契约。

三条分支各自可回归：
  - 无引用（含开关关闭）：不构造 MCP 客户端、不校验白名单、不注入后端；
  - 有引用但服务不可达（失败或超时）：只记 warning，注入不可用态，工具统一返回
    「查询失败：日志服务不可用」，启动不失败；
  - 有引用且配置不全（白名单为空 / 服务端缺期望工具）：启动失败且信息可定位。

另钉住 lifespan 的开关门控与顺序：profile 校验与日志工具装配都在
`wecom_service.start()` 之前，开关关闭时两者都不被调用（T3 评审补项）。
"""

import asyncio

import pytest
from loguru import logger

import src.main as main_mod
from src.agents.presets.capability import LOG_PROFILES
from src.agents.tools import log_tools
from src.config.log_tools import LogToolTexts
from src.config.rca import RcaConfig
from src.config.wecom_bots import WeComBotConfig
from src.rca.sources.base import SourceFailedError

_LOG_PROFILE = min(LOG_PROFILES)  # 任一具备日志能力的 profile，不写死名单
_PLAIN_PROFILE = "chat"  # 基础档预设，不具日志能力
_EXPECTED_TOOLS = ["sls_latency_stats", "sls_request_detail"]
_WHITELIST = ("POST /api/v1/orders",)


class _FakeMcp:
    """可配置工具清单的 MCP 客户端替身，记录是否被探测过。"""

    def __init__(self, names: list[str]) -> None:
        """保存 tools/list 将要返回的工具名。"""
        self._names = names
        self.probed = False

    async def list_tools(self) -> list[str]:
        """返回预置工具名。"""
        self.probed = True
        return list(self._names)


class _BrokenMcp:
    """tools/list 必然失败的 MCP 客户端替身（模拟不可达）。"""

    def __init__(self, reason: str = "transport:ConnectError") -> None:
        """保存失败原因（空串用于覆盖 reason 回退类型名的分支）。"""
        self._reason = reason
        self.probed = False

    async def list_tools(self) -> list[str]:
        """抛取数层失败异常。"""
        self.probed = True
        raise SourceFailedError(self._reason)


class _HangingMcp:
    """tools/list 永不返回的 MCP 客户端替身（模拟探测卡住）。"""

    async def list_tools(self) -> list[str]:
        """挂起直到被 wait_for 取消。"""
        await asyncio.sleep(60)
        return []


def _bots(profile: str) -> list[WeComBotConfig]:
    """构造单台机器人配置（secret 占位，不参与断言）。"""
    return [WeComBotConfig(key="ops", bot_id="b1", secret="s1", profile=profile)]


def _config(whitelist: tuple[str, ...]) -> RcaConfig:
    """构造注入了指定白名单的 RCA 配置（其余字段取与默认一致的占位值）。"""
    return RcaConfig(
        enabled=False,
        alert_whitelist=(),
        grafana_base_url="",
        grafana_token="",
        collect_budget_seconds=25.0,
        source_timeout_seconds=8.0,
        rule_timeout_seconds=5.0,
        history_timeout_seconds=5.0,
        concurrency=4,
        severe_latency_ms=3000,
        cache_ttl_seconds=600.0,
        business_uris=(),
        tool_uri_whitelist=whitelist,
    )


def _reset_log_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 log_tools 的模块级状态复位为未注入（monkeypatch 用例结束自动还原）。"""
    monkeypatch.setattr(log_tools, "_backend", None)
    monkeypatch.setattr(log_tools, "_whitelist", ())


def _capture_warning(lines: list[str]) -> int:
    """挂 loguru sink 收集 warning 及以上日志的消息文本，返回 sink id（调用方负责 remove）。"""
    return logger.add(
        lambda message: lines.append(message.record["message"]), level="WARNING"
    )


@pytest.mark.asyncio
async def test_switch_off_skips_probe(monkeypatch):
    """企微通道关闭时不探测：即使配置里有日志档机器人也不连服务。"""
    _reset_log_tools(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", False)
    monkeypatch.setattr(main_mod, "load_wecom_bots", lambda: _bots(_LOG_PROFILE))

    def _make() -> _FakeMcp:
        """记录 MCP 客户端被构造。"""
        raise AssertionError("企微通道关闭时不得构造 MCP 客户端")

    monkeypatch.setattr(main_mod, "_make_mcp_source", _make)
    monkeypatch.setattr(main_mod, "load_rca_config", lambda: _config(_WHITELIST))

    await main_mod._setup_log_tools()

    assert log_tools._backend is None


@pytest.mark.asyncio
async def test_no_log_profile_skips_probe(monkeypatch):
    """有机器人但都不引用日志档：不构造后端、不校验白名单。"""
    _reset_log_tools(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(main_mod, "load_wecom_bots", lambda: _bots(_PLAIN_PROFILE))

    def _make() -> _FakeMcp:
        """记录 MCP 客户端被构造。"""
        raise AssertionError("无日志档引用时不得构造 MCP 客户端")

    def _load_config() -> RcaConfig:
        """记录白名单被读取。"""
        raise AssertionError("无日志档引用时不得读取 RCA 配置")

    monkeypatch.setattr(main_mod, "_make_mcp_source", _make)
    monkeypatch.setattr(main_mod, "load_rca_config", _load_config)

    await main_mod._setup_log_tools()

    assert log_tools._backend is None


@pytest.mark.asyncio
async def test_probe_success_configures_backend(monkeypatch):
    """探测成功：注入后端与白名单，工具可正常调用。"""
    _reset_log_tools(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(main_mod, "load_wecom_bots", lambda: _bots(_LOG_PROFILE))
    monkeypatch.setattr(main_mod, "load_rca_config", lambda: _config(_WHITELIST))
    fake = _FakeMcp(_EXPECTED_TOOLS)
    monkeypatch.setattr(main_mod, "_make_mcp_source", lambda: fake)

    await main_mod._setup_log_tools()

    assert fake.probed is True
    assert log_tools._backend is not None
    assert log_tools._whitelist == _WHITELIST


@pytest.mark.asyncio
async def test_probe_failure_degrades_without_raising(monkeypatch):
    """有引用但服务不可达：不阻断启动，注入不可用态并记 warning。"""
    _reset_log_tools(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(main_mod, "load_wecom_bots", lambda: _bots(_LOG_PROFILE))
    monkeypatch.setattr(main_mod, "load_rca_config", lambda: _config(_WHITELIST))
    broken = _BrokenMcp()
    monkeypatch.setattr(main_mod, "_make_mcp_source", lambda: broken)
    lines: list[str] = []
    sink_id = _capture_warning(lines)

    try:
        await main_mod._setup_log_tools()  # 不抛
    finally:
        logger.remove(sink_id)

    assert broken.probed is True
    assert log_tools._backend is None
    assert log_tools._whitelist == _WHITELIST
    assert any(
        line.startswith("[tool] mcp unavailable")
        and "reason=transport:ConnectError" in line
        for line in lines
    ), lines
    text = await log_tools.sls_query_latency.ainvoke(
        {"uri": _WHITELIST[0], "minutes": 15}
    )
    assert text == LogToolTexts.UNAVAILABLE


@pytest.mark.asyncio
async def test_probe_timeout_degrades(monkeypatch):
    """探测超预算：按不可用降级，reason 回退为异常类型名。"""
    _reset_log_tools(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(main_mod, "load_wecom_bots", lambda: _bots(_LOG_PROFILE))
    monkeypatch.setattr(main_mod, "load_rca_config", lambda: _config(_WHITELIST))
    monkeypatch.setattr(main_mod, "_make_mcp_source", _HangingMcp)
    monkeypatch.setattr(main_mod, "PROBE_TIMEOUT_SECONDS", 0.01)
    lines: list[str] = []
    sink_id = _capture_warning(lines)

    try:
        await main_mod._setup_log_tools()  # 不抛
    finally:
        logger.remove(sink_id)

    assert log_tools._backend is None
    assert any("reason=TimeoutError" in line for line in lines), lines
    text = await log_tools.sls_query_requests.ainvoke(
        {"uri": _WHITELIST[0], "minutes": 15}
    )
    assert text == LogToolTexts.UNAVAILABLE


@pytest.mark.asyncio
async def test_probe_failure_empty_reason_falls_back_to_type(monkeypatch):
    """失败原因为空串时，事件字段回退为异常类型名（保证 reason 始终有值）。"""
    _reset_log_tools(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(main_mod, "load_wecom_bots", lambda: _bots(_LOG_PROFILE))
    monkeypatch.setattr(main_mod, "load_rca_config", lambda: _config(_WHITELIST))
    monkeypatch.setattr(main_mod, "_make_mcp_source", lambda: _BrokenMcp(""))
    lines: list[str] = []
    sink_id = _capture_warning(lines)

    try:
        await main_mod._setup_log_tools()
    finally:
        logger.remove(sink_id)

    assert any("reason=SourceFailedError" in line for line in lines), lines


@pytest.mark.asyncio
async def test_empty_whitelist_fails_fast_when_referenced(monkeypatch):
    """有引用且白名单为空：启动失败（否则所有查询被静默拒绝）。"""
    _reset_log_tools(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(main_mod, "load_wecom_bots", lambda: _bots(_LOG_PROFILE))
    monkeypatch.setattr(main_mod, "load_rca_config", lambda: _config(()))

    def _make() -> _FakeMcp:
        """白名单为空时不得发起连接。"""
        raise AssertionError("白名单为空时不得构造 MCP 客户端")

    monkeypatch.setattr(main_mod, "_make_mcp_source", _make)

    with pytest.raises(RuntimeError) as excinfo:
        await main_mod._setup_log_tools()

    assert "RCA_TOOL_URI_WHITELIST" in str(excinfo.value)


@pytest.mark.asyncio
async def test_missing_expected_tool_fails_fast(monkeypatch):
    """探测成功但缺期望工具：启动失败并指出缺失项。"""
    _reset_log_tools(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(main_mod, "load_wecom_bots", lambda: _bots(_LOG_PROFILE))
    monkeypatch.setattr(main_mod, "load_rca_config", lambda: _config(_WHITELIST))
    monkeypatch.setattr(
        main_mod, "_make_mcp_source", lambda: _FakeMcp(["sls_latency_stats"])
    )

    with pytest.raises(RuntimeError) as excinfo:
        await main_mod._setup_log_tools()

    assert "sls_request_detail" in str(excinfo.value)
    assert log_tools._backend is None


class _FakeWeComService:
    """wecom_service 替身：只记录 start/stop 的调用顺序。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享的调用序列。"""
        self._calls = calls

    async def start(self) -> None:
        """记录启动。"""
        self._calls.append("wecom.start")

    async def stop(self) -> None:
        """记录停止。"""
        self._calls.append("wecom.stop")


async def _noop() -> None:
    """异步空操作（替换有外部副作用的启动步骤）。"""
    return


def _empty_sections() -> dict[str, int]:
    """替掉 prompt 模板校验：返回空的段字符数表。"""
    return {}


def _patch_lifespan(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], enabled: bool
) -> None:
    """把 lifespan 的启动步骤全部替换成记录器，避免触碰网络/数据库/模型。

    Args:
        monkeypatch: pytest 补丁器
        calls: 记录调用顺序的共享列表
        enabled: WECOM_BOT_ENABLED 取值
    """
    monkeypatch.setattr(main_mod.settings, "WECOM_BOT_ENABLED", enabled)
    monkeypatch.setattr(main_mod, "warm_token_encoder", lambda: None)
    monkeypatch.setattr(main_mod.validation, "validate_all", _empty_sections)
    monkeypatch.setattr(main_mod, "_clear_stale_chat_locks", _noop)
    monkeypatch.setattr(main_mod, "validate_config", lambda registry: None)
    monkeypatch.setattr(main_mod, "validate_timeout_nesting", lambda: None)
    monkeypatch.setattr(main_mod, "load_alert_config", lambda: None)
    monkeypatch.setattr(main_mod, "configure_tracing", lambda: None)
    monkeypatch.setattr(main_mod, "_ensure_model_pricing", _noop)
    monkeypatch.setattr(main_mod, "flush_tracing", lambda: None)
    monkeypatch.setattr(main_mod, "wecom_service", _FakeWeComService(calls))

    def _record_validate() -> None:
        """记录 profile 校验。"""
        calls.append("validate")

    async def _record_setup() -> None:
        """记录日志工具装配。"""
        calls.append("setup")

    monkeypatch.setattr(main_mod, "_validate_wecom_profiles", _record_validate)
    monkeypatch.setattr(main_mod, "_setup_log_tools", _record_setup)


@pytest.mark.asyncio
async def test_lifespan_skips_both_steps_when_switch_off(monkeypatch):
    """企微开关关闭：profile 校验与日志工具装配都不执行。"""
    calls: list[str] = []
    _patch_lifespan(monkeypatch, calls, enabled=False)

    async with main_mod.lifespan(main_mod.app):
        assert calls == ["wecom.start"]

    assert calls == ["wecom.start", "wecom.stop"]


@pytest.mark.asyncio
async def test_lifespan_setup_runs_before_wecom_start(monkeypatch):
    """企微开关打开：校验 → 日志工具装配 → 建立连接，顺序固定。"""
    calls: list[str] = []
    _patch_lifespan(monkeypatch, calls, enabled=True)

    async with main_mod.lifespan(main_mod.app):
        assert calls == ["validate", "setup", "wecom.start"]
