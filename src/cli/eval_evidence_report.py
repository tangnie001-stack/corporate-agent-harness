"""证据选题与流程编排。

纯旁路模块：只读取已采集的 `QuestionEvidence` 与评分行，产物落
`data/deepeval/evidence/<run_id>/`，不参与任何打分、门禁或报告。
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from loguru import logger
from pandas import DataFrame, Series

from src.cli.eval_evidence_attribution import summarize_attribution
from src.cli.eval_evidence_writer import atomic_json, error_text, is_nan, write_question
from src.cli.eval_scoring import link_behavior_traces
from src.config import EVAL_REPORT_DIR
from src.config.const import (
    GATE_THRESHOLDS,
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_WEB,
)
from src.core.eval_evidence import EvidenceRun, QuestionEvidence

EVAL_EVIDENCE_DIR = str(Path(EVAL_REPORT_DIR).parent / "evidence")
_BEHAVIOR_KEYS = ("Task Completion", "Step Efficiency", "Agent Loop Detection")


def _as_int(value: Any) -> int:
    """把 DataFrame 单元格转成题号。

    Args:
        value: `q_index` 单元格

    Returns:
        整数值；无法转换时返回 0
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    """把评分单元格转成 float。

    Args:
        value: 评分单元格

    Returns:
        浮点值；无法转换时返回 NaN（与 `is_nan` 的缺失语义一致）
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def should_write_evidence(
    row: Series,
    sample_class: str,
    behaviour_rows: list[dict],
    *,
    all_questions: bool,
) -> bool:
    """低分/异常选题；web 主分不触发；无主分行时只看行为。

    选题顺序：全量 → empty / 跑图失败 → 行为分缺失或过低 → 工具选择
    → web 短路 → 主分门禁。web 样本没有 KB 事实链，主分低不代表检索问题，
    故不因主分选中；但工具选择错误与行为异常仍应入选。

    Args:
        row: 该题评分行（无主分时只含 q_index）
        sample_class: `pure_kb` / `web` / `empty`
        behaviour_rows: `read_behaviour_scores` 产物
        all_questions: `--evidence-all` 时为 True

    Returns:
        是否需要为该题写证据
    """
    if all_questions:
        return True
    if sample_class == SAMPLE_CLASS_EMPTY:
        return True
    if error_text(row) == "failed":
        return True
    behavior = {r.get("q_index"): r for r in behaviour_rows}.get(
        _as_int(row.get("q_index")), {}
    )
    metrics = behavior.get("metrics") or {}
    for key in _BEHAVIOR_KEYS:
        value = metrics.get(key)
        if is_nan(value):
            return True
        if _as_float(value) < 0.5:
            return True
    tool_reason = row.get("tool_correctness_reason")
    if not isinstance(tool_reason, str):
        tool_reason = ""
    if tool_reason in ("failed", "metric_unavailable"):
        return True
    if tool_reason.startswith("tool_correctness:"):
        return True
    tool_value = row.get("tool_correctness")
    if not is_nan(tool_value) and _as_float(tool_value) < 1.0:
        return True
    if sample_class == SAMPLE_CLASS_WEB:
        return False
    if not any(metric in row.index for metric in GATE_THRESHOLDS):
        return False
    for metric, threshold in GATE_THRESHOLDS.items():
        # 无主分的行（--deepeval-only）只含 q_index，缺列不等于 NaN 低分
        if metric not in row.index:
            continue
        value = row.get(metric)
        if is_nan(value):
            return True
        if _as_float(value) < threshold:
            return True
    return False


def _score_rows(scored_df: DataFrame | None) -> dict[int, Series]:
    """把评分表转成按 q_index 索引的行字典。

    Args:
        scored_df: `result.to_pandas()`；`--deepeval-only` 时为 None

    Returns:
        `q_index -> 行` 的字典
    """
    if scored_df is None:
        return {}
    rows: dict[int, Series] = {}
    for _, row in scored_df.iterrows():
        rows[_as_int(row["q_index"])] = row
    return rows


def _select_collectors(
    run: EvidenceRun,
    rows: dict[int, Series],
    sample_classes: list[str],
    behaviour_rows: list[dict],
    *,
    all_questions: bool,
) -> Iterator[tuple[QuestionEvidence, Series, str]]:
    """按题序产出被选中的 `(collector, row, sample_class)`。

    Args:
        run: 本轮证据容器
        rows: 已按 q_index 索引的评分行
        sample_classes: 每题样本类别
        behaviour_rows: 逐题行为分
        all_questions: `--evidence-all` 时为 True

    Yields:
        被选中的题及其评分行与样本类别
    """
    for collector in run.collectors:
        if collector is None:
            continue
        q_index = collector.q_index
        if q_index >= len(sample_classes):
            continue
        sample_class = sample_classes[q_index]
        row = rows.get(q_index)
        if row is None:
            row = Series({"q_index": q_index})
        if not should_write_evidence(
            row, sample_class, behaviour_rows, all_questions=all_questions
        ):
            continue
        yield collector, row, sample_class


def _write_summary(
    target_dir: Path,
    *,
    run: EvidenceRun,
    questions: list[str],
    run_id: str,
    run_trace_id: str | None,
    files: list[str],
    low: list[int],
    started: float,
) -> None:
    """汇总并写 `summary.json`；失败只 warning。

    Args:
        target_dir: 本轮输出目录
        run: 本轮证据容器（用于根因汇总）
        questions: 题面列表
        run_id: 整轮 run id
        run_trace_id: 整轮 trace id
        files: 已写出的证据文件名
        low: 已写证据的题号
        started: 本轮起始 `time.monotonic()`
    """
    try:
        summary = {
            "schema_version": 1,
            "run_id": run_id,
            "run_trace_id": run_trace_id,
            "qa_count": len(questions),
            "low_score_questions": low,
            "evidence_files": files,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            **summarize_attribution(run.collectors),
        }
        atomic_json(target_dir / "summary.json", summary)
    except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响评测
        logger.warning("[cli] evidence summary write failed err={}", exc)


def write_evidence_run(
    run: EvidenceRun,
    *,
    questions: list[str],
    scored_df: DataFrame | None,
    sample_classes: list[str],
    behaviour_rows: list[dict],
    trace_ids: list[str],
    run_id: str,
    run_trace_id: str | None,
    all_questions: bool,
) -> None:
    """选题、探针、归因并原子写盘；异常只 warning。

    Args:
        run: 本轮证据容器（未开启时直接返回）
        questions: 题面列表（只用于统计题数）
        scored_df: 评分结果表；无主分时传 None
        sample_classes: 每题样本类别
        behaviour_rows: 逐题行为分
        trace_ids: 每题 trace_id
        run_id: 整轮 run id（决定输出子目录）
        run_trace_id: 整轮 trace id
        all_questions: `--evidence-all` 时为 True
    """
    if not run.enabled:
        return
    started = time.monotonic()
    target_dir = Path(EVAL_EVIDENCE_DIR) / run_id
    files: list[str] = []
    low: list[int] = []
    chunk_list = run.corpus_chunks or []
    selected: list[tuple[QuestionEvidence, Series, str]] = []
    try:
        rows = _score_rows(scored_df)
        linked = {
            row.get("q_index"): row
            for row in link_behavior_traces(behaviour_rows, trace_ids)
        }
        selected = list(
            _select_collectors(
                run, rows, sample_classes, behaviour_rows, all_questions=all_questions
            )
        )
    except Exception as exc:  # noqa: BLE001  # 选题失败只放弃本轮，仍写空 summary
        logger.warning("[cli] evidence selection failed err={}", exc)
        linked = {}

    # 单题失败只丢该题，后续题与 summary 照常产出
    for collector, row, sample_class in selected:
        try:
            name = write_question(
                collector,
                row,
                sample_class,
                target_dir=target_dir,
                behaviour_rows=behaviour_rows,
                linked=linked,
                chunk_list=chunk_list,
                run_trace_id=run_trace_id,
            )
        except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响评测
            logger.warning(
                "[cli] evidence question write failed q_index={} err={}",
                collector.q_index,
                exc,
            )
            continue
        low.append(collector.q_index)
        files.append(name)
    _write_summary(
        target_dir,
        run=run,
        questions=questions,
        run_id=run_id,
        run_trace_id=run_trace_id,
        files=files,
        low=low,
        started=started,
    )
