"""单题证据组装与原子写盘。

负责把已有的 `QuestionEvidence`（检索/工具/评分快照）与 facts/归因结果
拼成 JSON 载荷并落盘；选题与流程编排在 `eval_evidence_report`。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from pandas import Series

from src.cli.eval_evidence_attribution import attribute_question
from src.cli.eval_evidence_facts import build_facts
from src.cli.eval_evidence_probe import probe_reference_chunks, resolve_gold
from src.config.const import SAMPLE_CLASS_WEB
from src.core.eval_evidence import QuestionEvidence

_JUDGE_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "contextual_relevancy",
    "non_advice",
)


def is_nan(value: Any) -> bool:
    """判断评分是否缺失（None 与 NaN 同义）。

    Args:
        value: 评分值（DataFrame 单元格，类型不固定）

    Returns:
        缺失时为 True；无法转成数值时按"非缺失"处理
    """
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def error_text(row: Series) -> str:
    """取评分行的跑图错误标记。

    Args:
        row: 评分行

    Returns:
        `error` 列文本；非字符串（含 NaN）时返回空串
    """
    value = row.get("error")
    if isinstance(value, str):
        return value
    return ""


def judge_failed(collector: QuestionEvidence) -> bool:
    """判定本题是否有实际执行却出错的裁判指标。

    Args:
        collector: 本题证据

    Returns:
        任一指标标记为已执行且 score 缺失或带 error 时为 True
    """
    for metric in _JUDGE_METRICS:
        item = collector.judge.get(metric) or {}
        if not item.get("metric_attempted", False):
            continue
        if item.get("error"):
            return True
        if is_nan(item.get("score")):
            return True
    return False


def atomic_json(path: Path, payload: dict) -> None:
    """临时文件 + `os.replace` 原子写盘。

    Args:
        path: 目标文件
        payload: 待写 JSON 结构
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def merge_trajectory_tool_calls(collector: QuestionEvidence) -> None:
    """把轨迹里 `retrieve_kb` 之外的顶层工具调用补进 `tool_calls`。

    `retrieve_kb` 已由工具函数在调用现场记录（带结果条数与延迟），
    轨迹摘要只补其它工具；轨迹取不到这些字段，一律写 null。

    Args:
        collector: 本题证据（原地追加 `tool_calls`）
    """
    calls = (collector.trajectory or {}).get("tool_calls") or []
    for call in calls:
        name = str(call.get("name") or "")
        if name == "retrieve_kb":
            continue
        collector.tool_calls.append(
            {
                "name": name,
                "args": call.get("args") or {},
                "result_count": None,
                "latency_ms": None,
                "iteration": None,
                "call_index": len(collector.tool_calls) + 1,
            }
        )


def behavior_for(
    collector: QuestionEvidence, behaviour_rows: list[dict], linked: dict
) -> dict:
    """组装本题行为分与 trace 关联信息。

    Args:
        collector: 本题证据
        behaviour_rows: `read_behaviour_scores` 产物
        linked: 已按 q_index 建索引的 `link_behavior_traces` 结果

    Returns:
        `metrics` / `reasons` / `trace_id` / `link_method`
    """
    row = next((r for r in behaviour_rows if r.get("q_index") == collector.q_index), {})
    link = linked.get(collector.q_index) or {}
    return {
        "metrics": row.get("metrics") or {},
        "reasons": row.get("reasons") or {},
        "trace_id": link.get("trace_id"),
        "link_method": link.get("link_method"),
    }


def question_payload(collector: QuestionEvidence, run_trace_id: str | None) -> dict:
    """显式组装单题证据载荷（不得用 `asdict`：含不可序列化的 Event）。

    Args:
        collector: 本题证据
        run_trace_id: 整轮 trace id

    Returns:
        可直接 JSON 序列化的字典
    """
    return {
        "schema_version": 1,
        "q_index": collector.q_index,
        "trace": {
            "run_trace_id": run_trace_id,
            "sut_trace_id": collector.trace_id,
            "judge_measures": [
                {
                    **{key: value for key, value in measure.items() if key != "calls"},
                    "judge_calls": measure.get("calls") or [],
                }
                for measure in (collector.trace.get("judge_measures") or [])
            ],
            "behavior": {
                "trace_id": (collector.behavior or {}).get("trace_id"),
                "link_method": (collector.behavior or {}).get("link_method"),
            },
        },
        "testset": collector.testset,
        "query": collector.query,
        "retrieval": collector.retrieval,
        "tool_calls": collector.tool_calls,
        "judge": collector.judge,
        "behavior": collector.behavior,
        "facts": collector.facts,
        "attribution": collector.attribution,
    }


def write_question(
    collector: QuestionEvidence,
    row: Series,
    sample_class: str,
    *,
    target_dir: Path,
    behaviour_rows: list[dict],
    linked: dict,
    chunk_list: list,
    run_trace_id: str | None,
) -> str:
    """组装并原子写单题 JSON。

    Args:
        collector: 本题证据（原地写入 behavior / facts / attribution）
        row: 本题评分行
        sample_class: 样本类别
        target_dir: 本轮输出目录
        behaviour_rows: 逐题行为分
        linked: 已按 q_index 建索引的行为行
        chunk_list: 本轮知识库全量分块
        run_trace_id: 整轮 trace id

    Returns:
        写出的文件名
    """
    collector.behavior = behavior_for(collector, behaviour_rows, linked)
    merge_trajectory_tool_calls(collector)
    reference = str((collector.testset or {}).get("reference") or "")
    sample_failed = error_text(row) == "failed"
    probe: dict = {}
    gold: dict = {}
    if sample_class != SAMPLE_CLASS_WEB:
        gold = resolve_gold(collector.testset or {}, chunk_list, reference)
        probe = probe_reference_chunks(reference, chunk_list)
    collector.facts = build_facts(
        collector,
        probe,
        gold,
        sample_class=sample_class,
        numeric_risk=bool((collector.testset or {}).get("numeric_risk")),
        sample_failed=sample_failed,
        tool_correctness_reason=str(row.get("tool_correctness_reason") or ""),
        answer=collector.answer,
        tool_calls=collector.tool_calls,
        judge_failed=judge_failed(collector),
    )
    collector.attribution = attribute_question(
        collector, sample_class=sample_class, sample_failed=sample_failed
    )
    name = f"q_{collector.q_index}.json"
    atomic_json(target_dir / name, question_payload(collector, run_trace_id))
    return name
