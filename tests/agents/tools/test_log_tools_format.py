"""日志后端格式化测试 — 裁剪 / 脱敏 / 节点别名 / 时间窗 / 空结果透传。

后端负责把 McpLogSource 的原始结构压成模型可读文本：摘要 ≤5 行、明细 ≤limit 行，
且节点地址一律以序号别名代替，不把 IPv4 与请求头带进模型上下文。
"""

from typing import cast

import pytest

from src.agents.tools.log_backend import (
    McpLogToolBackend,
    _window,
    format_detail,
    format_latency,
)
from src.config.log_tools import DETAIL_FETCH_LIMIT, MAX_LIMIT
from src.rca.sources.base import SourceEmptyError, SourceFailedError
from src.rca.sources.mcp import McpLogSource


def test_latency_summary_is_five_lines_max():
    """聚合摘要压到 5 行以内，且首屏就要给出 p95 与 5xx。"""
    data = {
        "total": 120,
        "latency_ms": {"p50": 30, "p95": 800, "max": 2100},
        "slow": {"count": 7},
        "by_node": [{"cnt": 100, "p95": 500}, {"cnt": 20}],
        "errors": {"5xx": 3},
    }
    text = format_latency(data)
    assert len(text.splitlines()) <= 5
    assert "p95 800ms" in text
    assert "5xx 3" in text
    assert "node-1: 100 条" in text


def test_latency_summary_masks_addresses():
    """真实契约字段 addr 出现在输入里也不得进入输出。"""
    data = {
        "total": 5,
        "by_node": [{"addr": "10.0.0.8:8080", "cnt": 5}],
    }
    text = format_latency(data)
    assert "10.0.0.8" not in text
    assert "node-1: 5 条" in text


def test_latency_summary_tolerates_missing_fields():
    """字段缺失（无分位 / 无节点 / 无 5xx）时只输出能确定的行，不臆造 0。"""
    text = format_latency({"total": 3})
    assert text == "样本量 3 条"


def test_latency_omits_none_values_instead_of_zero():
    """键存在但值为 None 同样视为「没采到」，不写成 0。"""
    text = format_latency(
        {"total": None, "latency_ms": {"p50": None}, "slow": {}, "errors": {}}
    )
    assert text == "无可用统计"


def test_latency_empty_input_has_no_stats():
    """整段取不到数据时给出「无可用统计」，而不是一行 0。"""
    assert format_latency({}) == "无可用统计"


def test_latency_slow_and_errors_skipped_when_count_none():
    """slow.count / errors.5xx 为 None 时不输出该片段。"""
    text = format_latency(
        {"total": 2, "slow": {"count": None}, "errors": {"5xx": None}}
    )
    assert text == "样本量 2 条"


def test_latency_node_distribution_capped():
    """分布行最多列 5 个节点，其余以「另 N 个节点」收尾（token 预算）。"""
    data = {"by_node": [{"cnt": 1} for _ in range(8)]}
    text = format_latency(data)
    assert "node-5: 1 条" in text
    assert "node-6" not in text
    assert "另 3 个节点" in text


def test_detail_rows_capped_and_masked():
    """明细最多 limit 行，末行标注总命中数；地址换成序号别名。"""
    rows = [
        {
            "ts": "2026-11-06 10:00:00",
            "upstream_addr": "10.0.0.8:8080",
            "http_user_agent": "curl/8.0",
            "rt_ms": 1200,
            "status": 502,
            "upstream_status": 502,
        }
    ] * 25
    text = format_detail(rows, limit=20)
    assert len(text.splitlines()) == 21
    assert "本次取样 25 条，仅显示最慢 20 条" in text
    assert text.splitlines()[0].startswith("2026-11-06 10:00:00 node-1 ")
    assert "10.0.0.8" not in text
    assert "node-1" in text
    assert "User-Agent" not in text
    assert "curl" not in text


def test_detail_sorted_slowest_first():
    """明细按耗时倒序，最慢的一条排在最前。"""
    rows = [
        {"ts": "t1", "rt_ms": 100, "status": 200, "upstream_status": 200},
        {"ts": "t2", "rt_ms": 1280, "status": 502, "upstream_status": 504},
    ]
    lines = format_detail(rows, limit=20).splitlines()
    assert lines[0].startswith("t2 ")
    assert "1280ms" in lines[0]
    assert "502/504" in lines[0]
    assert lines[1].startswith("t1 ")


def test_detail_without_rows_is_empty_text():
    """后端已把空结果转成异常；此处零行仅作防御，返回空串。"""
    assert format_detail([], limit=20) == ""


def test_detail_same_node_keeps_same_alias():
    """节点别名按 upstream_addr 首次出现顺序编号：同一节点跨行同一别名。"""
    rows = [
        {"ts": "t1", "upstream_addr": "10.0.0.1:80", "rt_ms": 30},
        {"ts": "t2", "upstream_addr": "10.0.0.2:80", "rt_ms": 50},
        {"ts": "t3", "upstream_addr": "10.0.0.1:80", "rt_ms": 40},
    ]
    lines = format_detail(rows, limit=20).splitlines()
    assert lines[0].startswith("t2 node-1 ")
    assert lines[1].startswith("t3 node-2 ")
    assert lines[2].startswith("t1 node-2 ")
    assert "10.0.0" not in "\n".join(lines)


def test_detail_missing_addr_uses_unknown_alias():
    """缺 upstream_addr 的行不臆造节点号，用占位别名。"""
    assert "node-?" in format_detail([{"ts": "t1", "rt_ms": 10}], limit=20)


def test_detail_missing_numbers_render_na():
    """明细数值字段缺失渲染 N/A，不写成 0。"""
    text = format_detail([{"ts": "t1"}], limit=20)
    assert "N/Ams" in text
    assert "0ms" not in text


def test_detail_limit_clamped_to_max():
    """limit 超过 MAX_LIMIT 时函数内部再 clamp，最多输出 MAX_LIMIT 行。"""
    rows = [{"ts": "t", "rt_ms": 1} for _ in range(50)]
    lines = format_detail(rows, limit=100).splitlines()
    assert len(lines) == MAX_LIMIT + 1
    assert lines[-1] == f"本次取样 50 条，仅显示最慢 {MAX_LIMIT} 条"


def test_window_is_half_open_minutes(monkeypatch):
    """minutes 换算为 [now-minutes, now) 的 epoch 半开区间。"""
    monkeypatch.setattr("src.agents.tools.log_backend.time.time", lambda: 10000.0)
    start_ts, end_ts = _window(5)
    assert end_ts == 10000
    assert start_ts == 9700


class _FakeSource:
    """记录调用参数的假 McpLogSource（只实现两个查询方法）。"""

    def __init__(self, latency_data=None, detail_rows=None, error=None):
        self.calls: list[tuple] = []
        self._latency_data = latency_data if latency_data is not None else {}
        self._detail_rows = detail_rows if detail_rows is not None else []
        self._error = error

    async def latency_stats(self, uri, start_ts, end_ts, slow_ms=None):
        self.calls.append(("latency", uri, start_ts, end_ts, slow_ms))
        if isinstance(self._error, BaseException):
            raise self._error
        return self._latency_data

    async def request_detail(self, uri, start_ts, end_ts, limit=100):
        self.calls.append(("detail", uri, start_ts, end_ts, limit))
        if isinstance(self._error, BaseException):
            raise self._error
        return self._detail_rows


def _backend(source: _FakeSource) -> McpLogToolBackend:
    """把假取数源包成后端（假实现只需两个查询方法，类型上视作 McpLogSource）。"""
    return McpLogToolBackend(cast(McpLogSource, source))


@pytest.mark.asyncio
async def test_backend_forwards_params_and_formats(monkeypatch):
    """后端把 minutes 换成时间窗、透传 slow_ms/limit，并格式化返回。"""
    monkeypatch.setattr("src.agents.tools.log_backend.time.time", lambda: 600.0)
    source = _FakeSource(
        latency_data={
            "total": 1,
            "latency_ms": {"p95": 900},
        }
    )
    backend = _backend(source)
    text = await backend.latency("/api/x", 2, 1500)
    assert "p95 900ms" in text
    assert source.calls == [("latency", "/api/x", 480, 600, 1500)]


@pytest.mark.asyncio
async def test_backend_detail_separates_fetch_and_show_limits(monkeypatch):
    """明细取数用 DETAIL_FETCH_LIMIT、展示按 limit 裁剪（两者解耦）。"""
    monkeypatch.setattr("src.agents.tools.log_backend.time.time", lambda: 600.0)
    source = _FakeSource(
        detail_rows=[
            {
                "ts": "t",
                "upstream_addr": "10.0.0.9:80",
                "rt_ms": 10,
                "status": 200,
                "upstream_status": 200,
            }
        ]
    )
    backend = _backend(source)
    text = await backend.detail("/api/x", 3, 5)
    assert "node-1" in text
    assert source.calls == [("detail", "/api/x", 420, 600, DETAIL_FETCH_LIMIT)]


@pytest.mark.asyncio
async def test_backend_does_not_swallow_empty():
    """查到但无数据：SourceEmptyError 原样抛给工具层（由工具层出空结果文案）。"""
    backend = _backend(_FakeSource(error=SourceEmptyError("no_rows")))
    with pytest.raises(SourceEmptyError):
        await backend.latency("/api/x", 5, 1000)


@pytest.mark.asyncio
async def test_backend_does_not_swallow_failure():
    """调用失败：SourceFailedError 原样抛给工具层（由工具层出失败文案）。"""
    backend = _backend(_FakeSource(error=SourceFailedError("rpc_error:-1")))
    with pytest.raises(SourceFailedError):
        await backend.detail("/api/x", 5, 20)
