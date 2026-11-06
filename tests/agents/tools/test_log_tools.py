"""日志工具层测试 — 白名单 / 参数边界 / 超时 / 空与失败区分 / 审计日志字段。

工具层只做参数校验、白名单、超时与降级；取数格式与脱敏属后端（T8），
故此处用假后端断言"调用契约"（uri/minutes/slow_ms/limit 原样透传）。
"""

import asyncio
import contextlib

import pytest
from loguru import logger

from src.agents.tools import log_tools
from src.agents.tools.log_tools import configure, make_log_tools
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rca.sources.base import SourceEmptyError, SourceFailedError


class _FakeBackend:
    """记录调用参数的假后端；结果可设为异常以验证降级文案。"""

    def __init__(
        self,
        latency_result: str | BaseException = "latency-ok",
        detail_result: str | BaseException = "detail-ok",
    ):
        self.calls: list[tuple] = []
        self._latency_result = latency_result
        self._detail_result = detail_result

    async def latency(self, uri: str, minutes: int, slow_ms: int) -> str:
        self.calls.append(("latency", uri, minutes, slow_ms))
        if isinstance(self._latency_result, BaseException):
            raise self._latency_result
        return self._latency_result

    async def detail(self, uri: str, minutes: int, limit: int) -> str:
        self.calls.append(("detail", uri, minutes, limit))
        if isinstance(self._detail_result, BaseException):
            raise self._detail_result
        return self._detail_result


class _SlowBackend(_FakeBackend):
    """每次调用都睡一会，用于触发工具层超时。"""

    def __init__(self, delay: float = 0.2):
        super().__init__()
        self._delay = delay

    async def latency(self, uri: str, minutes: int, slow_ms: int) -> str:
        self.calls.append(("latency", uri, minutes, slow_ms))
        await asyncio.sleep(self._delay)
        return "too-late"

    async def detail(self, uri: str, minutes: int, limit: int) -> str:
        self.calls.append(("detail", uri, minutes, limit))
        await asyncio.sleep(self._delay)
        return "too-late"


@pytest.fixture(autouse=True)
def _reset_module_state():
    """每个用例前后清空进程级后端与白名单，避免用例间互相污染。"""
    configure(None, ())
    yield
    configure(None, ())


def _tools() -> dict:
    return {t.name: t for t in make_log_tools()}


@contextlib.contextmanager
def _captured_lines():
    """临时接管 loguru 输出，收集格式化后的日志行（用例结束即移除 sink）。"""
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), format="{message}")
    try:
        yield lines
    finally:
        logger.remove(sink_id)


@pytest.mark.asyncio
async def test_no_backend_returns_failure():
    """未配置后端：返回查询失败，不得返回空结果或"不在范围"。"""
    configure(None, ())
    tool = _tools()["sls_query_latency"]
    out = await tool.ainvoke({"uri": "/api/x", "minutes": 15})
    assert out.startswith("查询失败")


@pytest.mark.asyncio
async def test_uri_not_in_whitelist_lists_options_and_skips_backend():
    """白名单外：列可选接口，且不调用后端。"""
    backend = _FakeBackend()
    configure(backend, ("/api/createOrder",))
    tool = _tools()["sls_query_requests"]
    out = await tool.ainvoke({"uri": "/api/other", "minutes": 15})
    assert "/api/createOrder" in out
    assert backend.calls == []


@pytest.mark.asyncio
async def test_whitelist_empty_reports_no_configured_uri():
    """白名单为空：明确告知未配置可查询接口，不调用后端（fail-fast 属启动期）。"""
    backend = _FakeBackend()
    configure(backend, ())
    tool = _tools()["sls_query_latency"]
    out = await tool.ainvoke({"uri": "/api/x", "minutes": 15})
    assert "未配置可查询接口" in out
    assert backend.calls == []


@pytest.mark.asyncio
async def test_minutes_out_of_range_returns_param_error():
    """minutes 越界：返回参数错误，不调用后端。"""
    backend = _FakeBackend()
    configure(backend, ("/api/x",))
    tool = _tools()["sls_query_latency"]
    for bad in (0, 361):
        out = await tool.ainvoke({"uri": "/api/x", "minutes": bad})
        assert out.startswith("参数错误")
        assert "minutes" in out
    assert backend.calls == []


@pytest.mark.asyncio
async def test_limit_out_of_range_returns_param_error():
    """limit 越界：返回参数错误，不调用后端。"""
    backend = _FakeBackend()
    configure(backend, ("/api/x",))
    tool = _tools()["sls_query_requests"]
    for bad in (0, 21):
        out = await tool.ainvoke({"uri": "/api/x", "minutes": 15, "limit": bad})
        assert out.startswith("参数错误")
        assert "limit" in out
    assert backend.calls == []


@pytest.mark.asyncio
async def test_params_forwarded_to_backend():
    """工具把 minutes / slow_ms / limit 原样交给后端（时间戳换算在后端）。"""
    backend = _FakeBackend()
    configure(backend, ("/api/x",))
    tools = _tools()
    await tools["sls_query_latency"].ainvoke(
        {"uri": "/api/x", "minutes": 30, "slow_ms": 2000}
    )
    await tools["sls_query_requests"].ainvoke(
        {"uri": "/api/x", "minutes": 30, "limit": 5}
    )
    assert backend.calls == [
        ("latency", "/api/x", 30, 2000),
        ("detail", "/api/x", 30, 5),
    ]


@pytest.mark.asyncio
async def test_empty_result_is_not_failure():
    """空结果：返回"近 N 分钟无请求记录"，不得表述为查询失败。"""
    backend = _FakeBackend(latency_result=SourceEmptyError("no_rows"))
    configure(backend, ("/api/x",))
    tool = _tools()["sls_query_latency"]
    out = await tool.ainvoke({"uri": "/api/x", "minutes": 45})
    assert out == "近 45 分钟无请求记录"
    assert not out.startswith("查询失败")


@pytest.mark.asyncio
async def test_backend_failure_returns_failure_text():
    """后端失败：返回"查询失败：原因"，不抛异常给模型。"""
    backend = _FakeBackend(detail_result=SourceFailedError("rpc_error:-1"))
    configure(backend, ("/api/x",))
    tool = _tools()["sls_query_requests"]
    out = await tool.ainvoke({"uri": "/api/x", "minutes": 15})
    assert out.startswith("查询失败")
    assert "rpc_error:-1" in out


@pytest.mark.asyncio
async def test_timeout_returns_timeout_text_without_retry(monkeypatch):
    """超时：返回超时文案且只调用一次（工具内不重试）。"""
    monkeypatch.setattr(log_tools, "LOG_TOOL_TIMEOUT_SECONDS", 0.01)
    backend = _SlowBackend(delay=0.2)
    configure(backend, ("/api/x",))
    tool = _tools()["sls_query_latency"]
    out = await tool.ainvoke({"uri": "/api/x", "minutes": 15})
    assert out == "查询失败：超时"
    assert len(backend.calls) == 1


@pytest.mark.asyncio
async def test_success_log_line_fields():
    """成功调用写一条 [tool] mcp call，含 tool/uri/minutes/elapsed_ms/session。"""
    backend = _FakeBackend(latency_result="summary")
    configure(backend, ("/api/x",))
    ctx = RequestContext(session_id="sess-42")
    token = current_request_ctx.set(ctx)
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), format="{message}")
    try:
        out = await _tools()["sls_query_latency"].ainvoke(
            {"uri": "/api/x", "minutes": 15}
        )
    finally:
        logger.remove(sink_id)
        current_request_ctx.reset(token)
    assert out == "summary"
    call_lines = [line for line in lines if "[tool] mcp call" in line]
    assert len(call_lines) == 1
    line = call_lines[0]
    assert "tool=sls_query_latency" in line
    assert "uri=/api/x" in line
    assert "minutes=15" in line
    assert "elapsed_ms=" in line
    assert "rows=1" in line
    assert "session=sess-42" in line


@pytest.mark.asyncio
async def test_detail_log_line_has_rows():
    """明细调用额外记 rows=返回行数，供审计核对裁剪生效。"""
    backend = _FakeBackend(detail_result="r1\nr2\nr3")
    configure(backend, ("/api/x",))
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), format="{message}")
    try:
        await _tools()["sls_query_requests"].ainvoke({"uri": "/api/x", "minutes": 15})
    finally:
        logger.remove(sink_id)
    call_lines = [line for line in lines if "[tool] mcp call" in line]
    assert len(call_lines) == 1
    assert "tool=sls_query_requests" in call_lines[0]
    assert "rows=3" in call_lines[0]


@pytest.mark.asyncio
async def test_no_request_context_logs_empty_session():
    """无请求上下文时 session= 为空串，不抛错。"""
    backend = _FakeBackend()
    configure(backend, ("/api/x",))
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), format="{message}")
    try:
        await _tools()["sls_query_latency"].ainvoke({"uri": "/api/x", "minutes": 15})
    finally:
        logger.remove(sink_id)
    assert any('session=""' in line for line in lines if "[tool] mcp call" in line)


def test_make_log_tools_declares_readonly():
    """两个工具以 readonly=True 注册（skill 双轴推导依赖该声明）。"""
    from src.agents.tools.readonly import readonly_map

    names = {t.name for t in make_log_tools()}
    assert names == {"sls_query_latency", "sls_query_requests"}
    declared = readonly_map()
    assert declared["sls_query_latency"] is True
    assert declared["sls_query_requests"] is True


def test_rca_config_reads_tool_uri_whitelist(monkeypatch):
    """RCA_TOOL_URI_WHITELIST 解析为精确匹配白名单（兼容全角逗号）。"""
    from src.config.rca import load_rca_config, reset_for_test

    monkeypatch.setenv("RCA_TOOL_URI_WHITELIST", "/api/a, /api/b，/api/c")
    reset_for_test()
    try:
        cfg = load_rca_config()
    finally:
        reset_for_test()
    assert cfg.tool_uri_whitelist == ("/api/a", "/api/b", "/api/c")


def test_rca_config_whitelist_defaults_empty(monkeypatch):
    """未配置白名单时为空元组（日志工具据此全部拒绝，启动期再由 lifespan 判定）。"""
    from src.config.rca import load_rca_config, reset_for_test

    monkeypatch.delenv("RCA_TOOL_URI_WHITELIST", raising=False)
    reset_for_test()
    try:
        cfg = load_rca_config()
    finally:
        reset_for_test()
    assert cfg.tool_uri_whitelist == ()


def test_tool_descriptions_warn_failure_is_not_no_anomaly():
    """模型可见描述必须写明：查询失败只说明没拿到数据，不得表述为未发现异常。"""
    descriptions = {t.name: t.description for t in make_log_tools()}
    for name in ("sls_query_latency", "sls_query_requests"):
        assert "查询失败" in descriptions[name]
        assert "未发现异常" in descriptions[name]


@pytest.mark.asyncio
async def test_failure_path_logs_status_failed():
    """后端失败：返回文案的同时必须落一条 status=failed 审计日志。"""
    backend = _FakeBackend(detail_result=SourceFailedError("rpc_error:-1"))
    configure(backend, ("/api/x",))
    with _captured_lines() as lines:
        out = await _tools()["sls_query_requests"].ainvoke(
            {"uri": "/api/x", "minutes": 15}
        )
    assert out.startswith("查询失败")
    call_lines = [line for line in lines if "[tool] mcp call" in line]
    assert len(call_lines) == 1
    line = call_lines[0]
    assert "tool=sls_query_requests" in line
    assert "uri=/api/x" in line
    assert "minutes=15" in line
    assert "elapsed_ms=" in line
    assert "status=failed" in line
    assert "reason=rpc_error:-1" in line
    assert "session=" in line


@pytest.mark.asyncio
async def test_timeout_path_logs_status_failed(monkeypatch):
    """超时：最需要 elapsed_ms 的场景也要留痕，reason 固定为 timeout。"""
    monkeypatch.setattr(log_tools, "LOG_TOOL_TIMEOUT_SECONDS", 0.01)
    configure(_SlowBackend(delay=0.2), ("/api/x",))
    with _captured_lines() as lines:
        out = await _tools()["sls_query_latency"].ainvoke(
            {"uri": "/api/x", "minutes": 15}
        )
    assert out == "查询失败：超时"
    call_lines = [line for line in lines if "[tool] mcp call" in line]
    assert len(call_lines) == 1
    assert "status=failed" in call_lines[0]
    assert "reason=timeout" in call_lines[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["sls_query_latency", "sls_query_requests"])
async def test_unexpected_exception_becomes_failure_text(tool_name):
    """三类以外异常（如后端格式化 bug）也转成失败文案并落日志，不穿出工具。"""
    backend = _FakeBackend(
        latency_result=ValueError("boom"), detail_result=ValueError("boom")
    )
    configure(backend, ("/api/x",))
    with _captured_lines() as lines:
        out = await _tools()[tool_name].ainvoke({"uri": "/api/x", "minutes": 15})
    assert out.startswith("查询失败")
    assert "ValueError" in out
    call_lines = [line for line in lines if "[tool] mcp call" in line]
    assert len(call_lines) == 1
    assert "status=failed" in call_lines[0]
    assert "reason=boom" in call_lines[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["sls_query_latency", "sls_query_requests"])
async def test_backend_empty_text_maps_to_no_rows(tool_name):
    """后端返回空串：按「近 N 分钟无请求记录」处理，不得原样透传空串。"""
    backend = _FakeBackend(latency_result="", detail_result="")
    configure(backend, ("/api/x",))
    with _captured_lines() as lines:
        out = await _tools()[tool_name].ainvoke({"uri": "/api/x", "minutes": 25})
    assert out == "近 25 分钟无请求记录"
    call_lines = [line for line in lines if "[tool] mcp call" in line]
    assert len(call_lines) == 1
    assert "rows=0" in call_lines[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["sls_query_latency", "sls_query_requests"])
async def test_empty_result_logs_status_empty(tool_name):
    """空结果：审计日志记 status=empty（空 ≠ 失败），便于与真失败分流。"""
    backend = _FakeBackend(
        latency_result=SourceEmptyError("no_rows"),
        detail_result=SourceEmptyError("no_rows"),
    )
    configure(backend, ("/api/x",))
    with _captured_lines() as lines:
        out = await _tools()[tool_name].ainvoke({"uri": "/api/x", "minutes": 15})
    assert out == "近 15 分钟无请求记录"
    call_lines = [line for line in lines if "[tool] mcp call" in line]
    assert len(call_lines) == 1
    assert "status=empty" in call_lines[0]
    assert "status=failed" not in call_lines[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["sls_query_latency", "sls_query_requests"])
async def test_whitespace_only_text_counts_zero_rows(tool_name):
    """纯空白文本：先 strip 再计数，rows=0 与「无请求记录」文案保持一致。"""
    backend = _FakeBackend(latency_result="  \n\t\n ", detail_result="  \n\t\n ")
    configure(backend, ("/api/x",))
    with _captured_lines() as lines:
        out = await _tools()[tool_name].ainvoke({"uri": "/api/x", "minutes": 15})
    assert out == "近 15 分钟无请求记录"
    call_lines = [line for line in lines if "[tool] mcp call" in line]
    assert len(call_lines) == 1
    assert "rows=0" in call_lines[0]
