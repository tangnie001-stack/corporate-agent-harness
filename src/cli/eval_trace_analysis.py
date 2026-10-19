"""单条线上 trace 的确定性归因（不调用 LLM）。

输入是 `eval_trace.parse_trace` 的产物与 DeepEval 指标分数；输出沿用离线证据
的 9 类 root cause，但只使用 trace 层可见的信号：
- 裁判失败 / 空回答 / 无 contexts / 工具未调用；
- query 实体与 top contexts 的 company 是否一致；
- faithfulness / contextual_relevancy 低分；
- 工具调用与模型轮次数量。

生产 trace 没有 gold，因此无法区分 `retrieval_miss` 与 `rerank_or_topk` 的
精确边界；实体错配与低 contextual_relevancy 只能给出较保守的归因。
"""

from __future__ import annotations

import math

_COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "HK:00700": ("腾讯", "tencent", "腾讯控股", "腾讯控股有限公司"),
    "CN:600718": ("东软", "neusoft", "东软集团", "东软集团股份有限公司"),
    "CN:200512": ("灿坤", "闽灿坤", "厦灿坤", "灿坤实业", "厦门灿坤实业股份有限公司"),
}
_SOURCE_HINTS: tuple[tuple[str, str], ...] = (
    ("tencent", "HK:00700"),
    ("neusoft", "CN:600718"),
    ("灿坤", "CN:200512"),
)
_ROOT_CAUSES = {
    "judge_failure",
    "testset_error",
    "parsing_or_chunking",
    "retrieval_miss",
    "rerank_or_topk",
    "prompt_or_generation",
    "tool_routing",
    "agent_orchestration",
    "inconclusive",
}


def _normalize(text: str) -> str:
    """实体匹配用的轻量归一化：小写、去空白与常见标点。"""
    lowered = text.lower()
    return "".join(ch for ch in lowered if not ch.isspace() and ch not in "_-·（）()")


def detect_company(text: str) -> str | None:
    """从文本里识别 company_id；识别不到返回 None。

    Args:
        text: query / source / 实体值

    Returns:
        canonical company_id 或 None
    """
    normalized = _normalize(text or "")
    for company_id, aliases in _COMPANY_ALIASES.items():
        if any(_normalize(alias) in normalized for alias in aliases):
            return company_id
    return None


def company_from_source(source: str | None) -> str | None:
    """从来源文件名推断公司（旧 trace 没有结构化 entities 时的兜底）。"""
    if not source:
        return None
    normalized = (source or "").lower()
    for hint, company_id in _SOURCE_HINTS:
        if hint in normalized:
            return company_id
    return detect_company(source)


def _score(metrics: dict | None, name: str) -> float | None:
    """读取指标分数；缺失/NaN 返回 None。"""
    item = (metrics or {}).get(name) or {}
    value = item.get("score")
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(score):
        return None
    return score


def _result(root: str, evidence: str, confidence: str) -> dict:
    if root not in _ROOT_CAUSES:
        root = "inconclusive"
    return {"root_cause": root, "confidence": confidence, "evidence": evidence}


def analyze_trace(parsed: dict, metrics: dict | None) -> dict:
    """按固定规则给出逐指标归因与信号。

    Args:
        parsed: `parse_trace` 产物
        metrics: `_run_metrics` 产物；None 表示 inspect-only

    Returns:
        `{"root_causes": {...}, "signals": {...}}`
    """
    query = str(parsed.get("query") or "")
    answer = str(parsed.get("answer") or "")
    contexts = parsed.get("contexts") or []
    query_company = detect_company(query)
    context_companies = [company_from_source(c.get("source")) for c in contexts]
    known = [c for c in context_companies if c]
    top = [c for c in context_companies[:3] if c]
    top_mismatch = sum(1 for c in top if query_company and c != query_company)
    all_mismatch = bool(known) and all(
        c != query_company for c in known if query_company
    )
    tool_names = [
        str(call.get("name") or "") for call in parsed.get("tool_calls") or []
    ]
    generations = parsed.get("generations") or []

    roots: dict[str, dict] = {}
    if metrics:
        for name, item in metrics.items():
            if (item or {}).get("error"):
                roots[name] = _result(
                    "judge_failure",
                    f"metric_error={str(item.get('error'))[:120]}",
                    "high",
                )

    if not answer.strip():
        roots.setdefault(
            "answer_relevancy",
            _result("prompt_or_generation", "empty_answer", "high"),
        )
    if not contexts:
        roots.setdefault(
            "contextual_relevancy",
            _result("retrieval_miss", "no_retrieval_context", "high"),
        )
    elif query_company and all_mismatch:
        roots.setdefault(
            "contextual_relevancy",
            _result(
                "retrieval_miss",
                f"all_contexts={known} query_company={query_company}",
                "high",
            ),
        )
    elif query_company and top_mismatch >= 2:
        roots.setdefault(
            "contextual_relevancy",
            _result(
                "rerank_or_topk",
                f"top3_company_mismatch={top_mismatch} query_company={query_company}",
                "medium",
            ),
        )

    faithfulness = _score(metrics, "faithfulness")
    if faithfulness is not None and faithfulness < 0.5:
        roots.setdefault(
            "faithfulness",
            _result(
                "prompt_or_generation",
                f"faithfulness={faithfulness:.3f}",
                "medium",
            ),
        )
    relevancy = _score(metrics, "contextual_relevancy")
    if relevancy is not None and relevancy < 0.5:
        roots.setdefault(
            "contextual_relevancy",
            _result(
                "rerank_or_topk",
                f"contextual_relevancy={relevancy:.3f}",
                "medium",
            ),
        )
    answer_relevancy = _score(metrics, "answer_relevancy")
    if answer_relevancy is not None and answer_relevancy < 0.5:
        roots.setdefault(
            "answer_relevancy",
            _result(
                "inconclusive",
                f"answer_relevancy={answer_relevancy:.3f}",
                "inconclusive",
            ),
        )

    if query_company and "retrieve_kb" not in tool_names:
        roots.setdefault(
            "tool_correctness",
            _result(
                "tool_routing",
                f"tools={tool_names} query_company={query_company}",
                "high",
            ),
        )
    if len(tool_names) > 6 or len(generations) > 8:
        roots.setdefault(
            "agent_orchestration",
            _result(
                "agent_orchestration",
                f"tool_calls={len(tool_names)} generations={len(generations)}",
                "medium",
            ),
        )

    signals = {
        "query_company": query_company,
        "context_companies": context_companies,
        "top3_companies": context_companies[:3],
        "top3_mismatch_count": top_mismatch,
        "known_context_count": len(known),
        "tool_names": tool_names,
        "generation_count": len(generations),
        "answer_chars": len(answer),
    }
    return {"root_causes": roots, "signals": signals}


def summarize_batch(results: list[dict]) -> dict:
    """汇总多条 trace 的 root cause 计数。

    Args:
        results: 每条 trace 的 `{"trace_id": ..., "analysis": ...}`

    Returns:
        计数与逐条摘要
    """
    counts: dict[str, int] = {}
    for item in results:
        roots = (item.get("analysis") or {}).get("root_causes") or {}
        for root in {entry.get("root_cause") for entry in roots.values()}:
            if root:
                counts[root] = counts.get(root, 0) + 1
    return {
        "trace_count": len(results),
        "root_cause_counts": counts,
        "traces": results,
    }
