"""run trace：格式、启动事件、报告头、meta、题级/评测级锚点。"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from loguru import logger

from src.cli import eval_deepeval
from src.cli.eval_deepeval import (
    _collect_one_question,
    begin_eval_run,
    build_report_meta,
    run_evaluation,
    save_markdown_report,
)
from src.infra.llm.trace_context import EvalTraceContext, current_eval_trace

_RUN_TRACE_ID = "eval_run_20261019_000000_abcd1234"


@contextmanager
def _captured_logs() -> Iterator[list[str]]:
    """捕获 loguru 输出，供断言事件行里的 k=v 字段。"""
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


@contextmanager
def _eval_trace_context() -> Iterator[None]:
    """在用例作用域内设置评测 run 上下文，退出时复位。"""
    token = current_eval_trace.set(
        EvalTraceContext(
            run_id="20261019_000000",
            run_trace_id=_RUN_TRACE_ID,
            judge_model="judge",
        )
    )
    try:
        yield
    finally:
        current_eval_trace.reset(token)


class _FakeGraph:
    """`ainvoke` 返回最小图状态；`error` 非空时抛出。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def ainvoke(self, state, **kwargs):
        if self.error is not None:
            raise self.error
        return {"answer": "a", "tool_contexts": [], "messages": []}


def test_begin_eval_run_emits_event_and_returns_ids():
    with _captured_logs() as messages:
        run_id, run_trace_id = begin_eval_run("kb-1")

    assert re.fullmatch(r"\d{8}_\d{6}", run_id)
    assert re.fullmatch(rf"eval_run_{run_id}_[0-9a-f]{{8}}", run_trace_id)
    lines = [m for m in messages if "evaluation run" in m]
    assert len(lines) == 1
    assert f"run_trace_id={run_trace_id}" in lines[0]
    assert "kb_id=kb-1" in lines[0]
    assert f"run_id={run_id}" in lines[0]


def test_eval_trace_context_roundtrip():
    ctx = EvalTraceContext(
        run_id="20261019_000000",
        run_trace_id="eval_run_x_abcd1234",
        judge_model="judge",
    )
    token = current_eval_trace.set(ctx)
    try:
        assert current_eval_trace.get() is ctx
    finally:
        current_eval_trace.reset(token)


def test_report_meta_carries_run_trace_in_both_branches():
    normal = build_report_meta(
        judge_model="qwen",
        nan_stats={"nan_cells": 0, "denominator_cells": 4, "nan_rate": 0.0},
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        run_trace_id="eval_run_x_abcd1234",
    )
    skipped = build_report_meta(
        judge_model="qwen",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
        run_trace_id="eval_run_x_abcd1234",
    )
    assert normal["run_trace_id"] == "eval_run_x_abcd1234"
    assert skipped["run_trace_id"] == "eval_run_x_abcd1234"


def test_markdown_report_renders_run_trace(tmp_path):
    meta = build_report_meta(
        judge_model="qwen",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
        run_trace_id="eval_run_x_abcd1234",
    )
    save_markdown_report(
        None,
        questions=["Q0"],
        output_path=str(tmp_path / "r.csv"),
        sample_classes=["pure_kb"],
        report_meta=meta,
    )
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "**Run trace:** eval_run_x_abcd1234" in text


def test_collect_one_question_logs_run_trace_on_start_and_done():
    """成功路径：START / DONE 两行都带 run trace 与题级 index。"""
    with _eval_trace_context(), _captured_logs() as messages:
        asyncio.run(
            _collect_one_question(_FakeGraph(), "kb-1", "s1", "q", "eval_t1", 3)
        )

    start_lines = [m for m in messages if "question answer start" in m]
    done_lines = [m for m in messages if "question answer done" in m]
    assert len(start_lines) == 1
    assert len(done_lines) == 1
    for line in (start_lines[0], done_lines[0]):
        assert f"run_trace_id={_RUN_TRACE_ID}" in line
        assert "index=3" in line


def test_collect_one_question_logs_run_trace_on_failure():
    """异常路径：FAILED 行同样带 run trace 与题级 index。"""
    with _eval_trace_context(), _captured_logs() as messages:
        result = asyncio.run(
            _collect_one_question(
                _FakeGraph(RuntimeError("boom")), "kb-1", "s1", "q", "eval_t1", 4
            )
        )

    failed_lines = [m for m in messages if "question answer failed" in m]
    assert len(failed_lines) == 1
    assert f"run_trace_id={_RUN_TRACE_ID}" in failed_lines[0]
    assert "index=4" in failed_lines[0]
    assert result[0] == "[ERROR] boom"


def test_run_evaluation_logs_run_trace_on_start():
    with _eval_trace_context(), _captured_logs() as messages:
        run_evaluation(
            ["Q0"],
            ["G0"],
            ["a"],
            [[]],
            {},
            ["eval_t1"],
            expected_tools=[None],
            trajectories=[{"tool_calls": [], "iterations": 0}],
            sample_classes=["pure_kb"],
        )

    start_lines = [m for m in messages if "evaluation start" in m]
    assert len(start_lines) == 1
    assert f"run_trace_id={_RUN_TRACE_ID}" in start_lines[0]
    assert "samples=1" in start_lines[0]


def test_main_logs_evaluation_run_when_testset_load_fails(monkeypatch):
    """题集加载失败（SystemExit）也要留下 run 锚点，且锚点带 kb_id。"""

    def _boom(kb_id, version):
        raise FileNotFoundError("no testset")

    monkeypatch.setattr(eval_deepeval, "configure_tracing", lambda: None)
    monkeypatch.setattr(eval_deepeval, "flush_tracing", lambda: None)
    # main 内是函数体局部 import，必须打在源模块上才生效
    monkeypatch.setattr("src.cli.testset_io.resolve_testset_version", _boom)
    monkeypatch.setattr(sys, "argv", ["eval_deepeval", "--kb-id", "kb-x"])

    with _captured_logs() as messages, pytest.raises(SystemExit):
        eval_deepeval.main()

    run_lines = [m for m in messages if "evaluation run" in m]
    assert len(run_lines) == 1
    assert "run_trace_id=eval_run_" in run_lines[0]
    assert "kb_id=kb-x" in run_lines[0]
