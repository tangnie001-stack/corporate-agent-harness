"""确定性归因：事实阶段 + 指标专属规则。

纯函数模块：只读取证据字段，不调用 LLM、不改写任何评分。
"""

from __future__ import annotations

from src.core.eval_evidence import QuestionEvidence

_METRICS: tuple[str, ...] = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "contextual_relevancy",
    "non_advice",
    "tool_correctness",
)
_BEHAVIOR_ALIASES: dict[str, str] = {
    "Task Completion": "TaskCompletion",
    "Step Efficiency": "StepEfficiency",
    "Agent Loop Detection": "AgentLoopDetection",
}
# 事实链顺序：键名、coverage 阶段名、该阶段丢失时的根因
_FACT_STAGES: tuple[tuple[str, str, str], ...] = (
    ("fact_in_corpus", "corpus", "parsing_or_chunking"),
    ("fact_in_candidates", "candidates", "retrieval_miss"),
    ("fact_in_reranked", "reranked", "rerank_or_topk"),
    ("fact_in_final", "final", "rerank_or_topk"),
)
_INCONCLUSIVE = "inconclusive"


def _one_line(text: str) -> str:
    """把证据文本压成单行。

    Args:
        text: 待输出的证据文本

    Returns:
        以单空格分隔、不含换行的字符串
    """
    return " ".join(str(text).split())


def _result(root: str, evidence: str, confidence: str) -> dict:
    """构造单个指标的归因结果。

    Args:
        root: 根因枚举值
        evidence: 触发规则的证据（自动压成单行）
        confidence: high / medium / low / inconclusive

    Returns:
        含 `root_cause` / `confidence` / `evidence` 的字典
    """
    return {
        "root_cause": root,
        "confidence": confidence,
        "evidence": _one_line(evidence),
    }


def _fact_stage(facts: dict) -> tuple[str, str] | None:
    """按 corpus → candidates → reranked → final 定位首个丢失阶段。

    值为 None 时必须先短路为 `inconclusive`，不得进入 `is False` 的
    否定分支；全部为 True 时返回 None。

    Args:
        facts: `QuestionEvidence.facts`

    Returns:
        `(root_cause, evidence)` 或 None
    """
    evidence: list[str] = []
    for key, _stage, root in _FACT_STAGES:
        value = facts.get(key)
        if value is None:
            evidence.append(f"{key}=null")
            return (_INCONCLUSIVE, ", ".join(evidence))
        if value is False:
            evidence.append(f"{key}=false")
            return (root, ", ".join(evidence))
        evidence.append(f"{key}=true")
    return None


def _partial_stage(facts: dict) -> str | None:
    """返回首个覆盖率在 (0, 1) 之间的阶段名。

    Args:
        facts: `QuestionEvidence.facts`

    Returns:
        阶段名；没有部分命中阶段时返回 None
    """
    coverage = facts.get("fact_coverage") or {}
    for stage in ("corpus", "candidates", "reranked", "final"):
        value = coverage.get(stage)
        if value is None:
            continue
        if 0 < value < 1.0:
            return stage
    return None


def _fact_confidence(root: str, facts: dict) -> str:
    """计算事实阶段根因的置信度。

    Args:
        root: 事实阶段根因
        facts: `QuestionEvidence.facts`

    Returns:
        gold 来自题集且精确匹配时为 high，其余为 medium
    """
    if root == "retrieval_miss" and facts.get("gold_chunk_confidence") == "high":
        return "high"
    return "medium"


def _skipped_reason(facts: dict, sample_class: str) -> str:
    """生成跳过原因。

    Args:
        facts: `QuestionEvidence.facts`
        sample_class: 样本类别

    Returns:
        `skipped:` 之后的原因文本
    """
    reason = facts.get("skipped_reason")
    if not reason:
        return f"sample_class={sample_class}"
    return str(reason)


def _metric_attempted(facts: dict, item: dict) -> bool:
    """判定指标是否实际执行。

    逐指标标记优先于题目级标记：empty 样本的 `tool_correctness` 仍会执行，
    不能被题目级的 `metric_attempted=false` 误判为跳过。

    Args:
        facts: `QuestionEvidence.facts`
        item: 该指标的 judge 条目

    Returns:
        明确标记为 False 时返回 False，其余情况返回 True
    """
    value = item.get("metric_attempted")
    if value is None:
        value = facts.get("metric_attempted")
    return value is not False


def attribute_question(
    collector: QuestionEvidence, *, sample_class: str, sample_failed: bool
) -> dict[str, dict]:
    """按指标输出确定性归因。

    规则顺序：跳过 → judge_failed → 指标专属规则 → 事实链。

    Args:
        collector: 本题证据
        sample_class: `pure_kb` / `web` / `empty`
        sample_failed: 跑图是否失败

    Returns:
        以指标名为键、`{root_cause, confidence, evidence}` 为值的字典
    """
    facts = collector.facts or {}
    judge = collector.judge or {}
    out: dict[str, dict] = {}

    for metric in _METRICS:
        item = judge.get(metric) or {}
        if not _metric_attempted(facts, item):
            out[metric] = _result(
                _INCONCLUSIVE,
                f"skipped:{_skipped_reason(facts, sample_class)}",
                _INCONCLUSIVE,
            )
            continue
        if facts.get("judge_failed") is True:
            out[metric] = _result("judge_failure", "judge_failed=true", "high")
            continue
        if metric == "faithfulness":
            if sample_failed:
                out[metric] = _result("prompt_or_generation", "run failed", "low")
            elif item.get("score") is None:
                out[metric] = _result(_INCONCLUSIVE, "no score", _INCONCLUSIVE)
            else:
                out[metric] = _result(
                    _INCONCLUSIVE,
                    "faithfulness v1 has no claim-level evidence",
                    _INCONCLUSIVE,
                )
            continue
        if metric == "answer_relevancy":
            if sample_failed:
                out[metric] = _result("prompt_or_generation", "run failed", "low")
            else:
                out[metric] = _result(
                    _INCONCLUSIVE, "no answer-level evidence", _INCONCLUSIVE
                )
            continue
        if metric == "tool_correctness":
            reason = facts.get("tool_correctness_reason")
            if not isinstance(reason, str):
                reason = ""
            if reason == "failed":
                out[metric] = _result(_INCONCLUSIVE, "run failed", _INCONCLUSIVE)
            elif sample_class == "web" and reason:
                out[metric] = _result(_INCONCLUSIVE, "web fallback", _INCONCLUSIVE)
            elif reason.startswith("missing "):
                out[metric] = _result(
                    "tool_routing", f"missing expected tool: {reason}", "high"
                )
            else:
                out[metric] = _result(_INCONCLUSIVE, "no routing signal", _INCONCLUSIVE)
            continue
        if metric == "non_advice":
            score = item.get("score")
            if score is not None and float(score) < 1.0:
                out[metric] = _result(
                    "prompt_or_generation", "advice detected", "medium"
                )
            else:
                out[metric] = _result(_INCONCLUSIVE, "no violation", _INCONCLUSIVE)
            continue
        # web 样本没有 KB 事实链，裁判成功时只可能 inconclusive；
        # 即使上游 facts 误填了非 null 值，也不能断言检索层根因。
        if sample_class == "web":
            out[metric] = _result(
                _INCONCLUSIVE, "web sample has no kb fact chain", _INCONCLUSIVE
            )
            continue

        conflict = facts.get("testset_conflict") or {}
        if conflict.get("numeric_conflict") is True:
            out[metric] = _result("testset_error", "numeric_conflict=true", "medium")
            continue
        partial_stage = _partial_stage(facts)
        if partial_stage is not None:
            missing = facts.get("missing_fact_tokens")
            out[metric] = _result(
                _INCONCLUSIVE,
                (
                    f"partial_fact_coverage stage={partial_stage} "
                    f"missing_fact_tokens={missing}"
                ),
                _INCONCLUSIVE,
            )
            continue
        if metric == "context_precision":
            score = item.get("score")
            if (
                facts.get("fact_in_final") is True
                and score is not None
                and float(score) < 1.0
            ):
                out[metric] = _result(
                    "rerank_or_topk", "gold in final but precision<1", "medium"
                )
                continue
        stage = _fact_stage(facts)
        if stage is not None:
            root, evidence = stage
            out[metric] = _result(root, evidence, _fact_confidence(root, facts))
            continue
        out[metric] = _result(_INCONCLUSIVE, "no rule", _INCONCLUSIVE)

    behavior_metrics = (collector.behavior or {}).get("metrics") or {}
    for raw_name, score in behavior_metrics.items():
        if not isinstance(raw_name, str):
            continue
        name = raw_name
        key = _BEHAVIOR_ALIASES.get(name, name)
        if sample_failed:
            out[key] = _result("prompt_or_generation", "run failed", "low")
        elif score is None:
            out[key] = _result(
                _INCONCLUSIVE, f"no behavior score for {name}", _INCONCLUSIVE
            )
        elif key in ("StepEfficiency", "AgentLoopDetection") and score < 0.5:
            out[key] = _result("agent_orchestration", f"score={score}", "medium")
        elif key == "TaskCompletion" and score < 0.5:
            out[key] = _result(_INCONCLUSIVE, f"score={score}", _INCONCLUSIVE)
    return out


def summarize_attribution(collectors: list[QuestionEvidence | None]) -> dict:
    """汇总本轮根因分布与指标计数。

    根因分布只统计实际执行的指标；跳过指标单独计数，且不计入
    `inconclusive_count`。

    Args:
        collectors: 本轮全部题的 collector（可含 None）

    Returns:
        `root_cause_counts`（`by_metric` / `total`）、`scored_metric_count`、
        `skipped_metric_count`、`inconclusive_count`
    """
    scored = 0
    skipped = 0
    inconclusive = 0
    by_metric: dict[str, dict[str, int]] = {}
    total: dict[str, int] = {}

    for collector in collectors:
        if collector is None:
            continue
        attribution = collector.attribution or {}
        judge = collector.judge or {}
        for metric, item in attribution.items():
            if not isinstance(item, dict):
                continue
            judge_item = judge.get(metric)
            if (
                isinstance(judge_item, dict)
                and judge_item.get("metric_attempted") is False
            ):
                skipped += 1
                continue
            evidence = item.get("evidence")
            if isinstance(evidence, str) and evidence.startswith("skipped:"):
                skipped += 1
                continue
            root = item.get("root_cause")
            if not isinstance(root, str) or not root:
                continue
            scored += 1
            metric_counts = by_metric.setdefault(metric, {})
            metric_counts[root] = metric_counts.get(root, 0) + 1
            total[root] = total.get(root, 0) + 1
            if root == _INCONCLUSIVE:
                inconclusive += 1
        # 未写归因的题（未被证据选中）仍可能整体跳过；只按逐指标标记补计，
        # 避免把已写入的归因重复计数。
        for metric, item in judge.items():
            if metric in attribution:
                continue
            if isinstance(item, dict) and item.get("metric_attempted") is False:
                skipped += 1

    return {
        "root_cause_counts": {"by_metric": by_metric, "total": total},
        "scored_metric_count": scored,
        "skipped_metric_count": skipped,
        "inconclusive_count": inconclusive,
    }
