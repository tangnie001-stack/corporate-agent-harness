"""证据事实组装：探针/gold 与各阶段候选的合并与覆盖率。

纯函数模块（唯一副作用是 web 样本写 `retrieval["status"]`），不读评分、
不访问外部系统。
"""

from __future__ import annotations

from typing import Any

from src.cli.eval_evidence_probe import (
    answer_mentions_fact,
    compute_testset_conflict,
)
from src.config.const import SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_WEB
from src.core.eval_evidence import QuestionEvidence

# 事实链阶段：检索候选（三路并集）、精排后、最终上下文
_FACT_STAGES = (
    ("candidates", "fact_in_candidates"),
    ("reranked", "fact_in_reranked"),
    ("final", "fact_in_final"),
)


def stage_sets(collector: QuestionEvidence, stage: str) -> tuple[set[str], set]:
    """取某阶段出现过的 chunk id / parent key。

    Args:
        collector: 本题证据
        stage: `candidates` / `reranked` / `final`

    Returns:
        `(chunk_id 集合, parent_key 集合)`
    """
    items: list[dict] = []
    if stage == "candidates":
        for key in ("dense", "lexical", "rrf"):
            items.extend((collector.retrieval.get(key) or {}).get("items") or [])
    elif stage == "reranked":
        items = (collector.retrieval.get("rerank_after") or {}).get("items") or []
    else:
        items = collector.retrieval.get("final_contexts") or []
    ids = {str(item.get("chunk_id")) for item in items}
    keys: set[tuple] = set()
    for item in items:
        key = item.get("parent_key")
        if isinstance(key, (list, tuple)):
            keys.add(tuple(key))
    return (ids, keys)


def _common_facts(
    *,
    sample_class: str,
    sample_failed: bool,
    tokens: list[str],
    answer: str,
    tool_calls: list[dict],
    tool_correctness_reason: str,
    judge_failed: bool,
) -> dict:
    """构造题目级公共事实：跳过语义、工具调用与回答命中。

    Args:
        sample_class: 样本类别
        sample_failed: 跑图是否失败
        tokens: 探针 token
        answer: 被测系统回答
        tool_calls: 本题工具调用（含轨迹补全）
        tool_correctness_reason: 评分行里的工具分原因
        judge_failed: 是否有裁判指标失败

    Returns:
        公共事实字典
    """
    metric_attempted = True
    if sample_failed or sample_class == SAMPLE_CLASS_EMPTY:
        metric_attempted = False
    skipped_reason = None
    if sample_failed:
        skipped_reason = "failed"
    elif sample_class == SAMPLE_CLASS_EMPTY:
        skipped_reason = "sample_class=empty"
    tool_names = [str(call.get("name") or "") for call in tool_calls]
    return {
        "testset_conflict": {
            "numeric_conflict": None,
            "unit_missing": None,
            "period_conflict": None,
            "missing_fact_tokens": [],
        },
        "judge_failed": judge_failed,
        "metric_attempted": metric_attempted,
        "skipped_reason": skipped_reason,
        "tool_called": tool_names,
        "call_count": len(tool_names),
        "tool_correctness_reason": tool_correctness_reason,
        "answer_mentions_fact": answer_mentions_fact(answer, tokens),
    }


def _null_fact_fields(tokens: list[str]) -> dict:
    """构造"无法判定"的四阶段事实字段（全 null）。

    Args:
        tokens: 探针 token

    Returns:
        `fact_tokens` / 命中表 / 覆盖率 / 四个 `fact_in_*` / 缺失 token
    """
    return {
        "fact_tokens": tokens,
        "fact_hit_tokens": {
            "corpus": [],
            "candidates": [],
            "reranked": [],
            "final": [],
        },
        "fact_coverage": {
            "corpus": None,
            "candidates": None,
            "reranked": None,
            "final": None,
        },
        "fact_in_corpus": None,
        "fact_in_candidates": None,
        "fact_in_reranked": None,
        "fact_in_final": None,
        "missing_fact_tokens": [],
    }


def _apply_stage_coverage(
    facts: dict,
    collector: QuestionEvidence,
    tokens: list[str],
    probe: dict,
) -> None:
    """逐阶段计算事实命中与覆盖率（仅 token 非空时）。

    Args:
        facts: 待填充的事实字典（原地修改）
        collector: 本题证据
        tokens: 探针 token
        probe: 探针结果（含 token 命中与 parent key 映射）
    """
    if not tokens:
        return
    token_hits = probe.get("token_hits") or {}
    token_keys = probe.get("token_parent_keys") or {}
    for stage, fact_key in _FACT_STAGES:
        ids, keys = stage_sets(collector, stage)
        hit = [
            token
            for token in tokens
            if any(cid in ids for cid in token_hits.get(token, []))
            or any(key in keys for key in token_keys.get(token, []))
        ]
        facts["fact_hit_tokens"][stage] = hit
        facts["fact_coverage"][stage] = len(hit) / len(tokens)
        facts[fact_key] = bool(hit)


def build_facts(
    collector: QuestionEvidence,
    probe: dict,
    gold: dict,
    *,
    sample_class: str,
    numeric_risk: bool,
    sample_failed: bool,
    tool_correctness_reason: str,
    answer: str,
    tool_calls: list[dict],
    judge_failed: bool,
) -> dict:
    """把探针/gold 与各阶段候选合并成事实标记与覆盖率。

    web 样本没有 KB 事实链：检索状态记 `not_applicable`，四阶段一律 null，
    不跑阶段交集。无 token 时同理写 null（不是 False）。

    Args:
        collector: 本题证据
        probe: 探针结果（web 样本为空字典）
        gold: gold 解析结果（web 样本为空字典）
        sample_class: 样本类别
        numeric_risk: 题集标注的数值风险
        sample_failed: 跑图是否失败
        tool_correctness_reason: 评分行里的工具分原因
        answer: 被测系统回答
        tool_calls: 本题工具调用（含轨迹补全）
        judge_failed: 是否有裁判指标失败

    Returns:
        完整事实字典
    """
    tokens = probe.get("probe_tokens") or []
    common = _common_facts(
        sample_class=sample_class,
        sample_failed=sample_failed,
        tokens=tokens,
        answer=answer,
        tool_calls=tool_calls,
        tool_correctness_reason=tool_correctness_reason,
        judge_failed=judge_failed,
    )
    if sample_class == SAMPLE_CLASS_WEB:
        collector.retrieval["status"] = "not_applicable"
        return {**probe, **gold, **common, **_null_fact_fields(tokens)}

    facts: dict[str, Any] = {**probe, **gold, **common, **_null_fact_fields(tokens)}
    facts["fact_hit_tokens"]["corpus"] = (probe.get("fact_hit_tokens") or {}).get(
        "corpus", []
    )
    facts["fact_coverage"]["corpus"] = (probe.get("fact_coverage") or {}).get("corpus")
    facts["fact_in_corpus"] = probe.get("fact_in_corpus")
    facts["testset_conflict"] = compute_testset_conflict(
        str((collector.testset or {}).get("reference") or ""),
        probe,
        numeric_risk=numeric_risk,
    )
    if gold.get("gold_chunk_source") == "testset":
        facts["fact_in_corpus"] = True
        facts["fact_coverage"]["corpus"] = 1.0
    _apply_stage_coverage(facts, collector, tokens, probe)
    facts["missing_fact_tokens"] = [
        token for token in tokens if token not in facts["fact_hit_tokens"]["final"]
    ]
    return facts
