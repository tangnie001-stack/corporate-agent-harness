"""run trace：格式、启动事件、报告头、meta。"""

from __future__ import annotations

import re

from loguru import logger

from src.cli.eval_deepeval import (
    begin_eval_run,
    build_report_meta,
    save_markdown_report,
)
from src.infra.llm.trace_context import EvalTraceContext, current_eval_trace


def test_begin_eval_run_emits_event_and_returns_ids():
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        run_id, run_trace_id = begin_eval_run("kb-1")
    finally:
        logger.remove(sink_id)

    assert re.fullmatch(r"\d{8}_\d{6}", run_id)
    assert re.fullmatch(rf"eval_run_{run_id}_[0-9a-f]{{8}}", run_trace_id)
    assert any(
        "evaluation run" in m and f"run_trace_id={run_trace_id}" in m for m in messages
    )


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
