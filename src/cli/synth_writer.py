"""v6 出题：Golden → sample、元数据、原子写。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.cli.synth_contexts import ContextGroup, render_context
from src.cli.synth_gates import compute_numeric_risk, extract_context_entities
from src.config.const import EVAL_SYNTH_EXPECTED_TOOLS

_SAMPLE_KEYS = (
    "user_input",
    "reference",
    "reference_contexts",
    "expected_tools",
    "hop_type",
    "hop_basis",
    "evolutions",
    "source_file",
    "numeric_risk",
    "synth_quality",
)


def golden_to_sample(golden: Any, group: ContextGroup) -> dict:
    """把 DeepEval Golden 转成 v6 sample，并挂内部判定字段。

    Args:
        golden: Synthesizer 产出的 Golden
        group: 该题所属上下文

    Returns:
        sample dict（含 `_company` / `_entities` / `_original_user_input` 内部键）
    """
    contexts = list(golden.context or render_context(group))
    if contexts and not contexts[0].startswith("【"):
        contexts[0] = f"【{group.company} · {group.period}】\n{contexts[0]}"
    metadata = golden.additional_metadata or {}
    evolutions = list(metadata.get("evolutions") or [])
    reference = golden.expected_output or ""
    return {
        "user_input": golden.input,
        "reference": reference,
        "reference_contexts": contexts,
        "expected_tools": list(EVAL_SYNTH_EXPECTED_TOOLS),
        "hop_type": "multi_hop" if "Multi-context" in evolutions else "single_hop",
        "hop_basis": "evolution",
        "evolutions": evolutions,
        "source_file": group.source,
        "numeric_risk": compute_numeric_risk(reference),
        "synth_quality": metadata.get("synthetic_input_quality"),
        "_company": group.company,
        "_entities": set(group.aliases) | extract_context_entities(contexts),
        "_original_user_input": golden.input,
    }


def public_sample(sample: dict) -> dict:
    """去掉内部键，只保留写盘契约字段。"""
    return {key: sample[key] for key in _SAMPLE_KEYS if key in sample}


def build_metadata(
    *,
    kb_id: str,
    kb_name: str,
    version: int,
    samples: list[dict],
    stats: dict,
    params: dict,
    doc_names: list[str],
    doc_ids: list[str],
    source_chunks: int,
    covered_chunks: int,
    company_source: dict[str, str],
    hop_stats: dict[str, int],
    elapsed_seconds: float,
) -> dict:
    """构造 v6 题集元数据。

    Returns:
        可直接写入 JSON 的 metadata 字典
    """
    return {
        "kb_id": kb_id,
        "kb_name": kb_name,
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "testset_size": len(samples),
        "doc_names": doc_names,
        "doc_ids": doc_ids,
        "source_chunks": source_chunks,
        "covered_chunks": covered_chunks,
        "company_source": company_source,
        "synth_params": {
            **params,
            "elapsed_seconds": round(elapsed_seconds, 1),
        },
        "hop_stats": hop_stats,
        "gate_stats": {
            "generated": stats["generated"],
            "kept": stats["kept"],
            "language_checked": stats["language_checked"],
            "self_contained_rewritten": stats["self_contained_rewritten"],
            "failed_contexts": stats["failed_contexts"],
            "dropped": dict(stats["dropped"]),
        },
    }


def write_testset(path: Path | str, payload: dict, *, force: bool = False) -> None:
    """原子写入题集 JSON。

    Args:
        path: 目标路径
        payload: 完整题集
        force: 是否允许覆盖

    Raises:
        FileExistsError: 目标已存在且未传 force
    """
    target = Path(path)
    if target.exists() and not force:
        raise FileExistsError(f"目标已存在：{target}（如需覆盖请加 --force）")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
