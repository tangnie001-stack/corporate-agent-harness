from src.cli.eval_evidence_attribution import (
    attribute_question,
    summarize_attribution,
)
from src.core.eval_evidence import EvidenceRun


def _collector(facts, judge=None, retrieval=None):
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    c = run.new_question(0)
    c.facts = facts
    c.judge = judge or {}
    c.retrieval = retrieval or {}
    return c


def test_q21_retrieval_miss():
    c = _collector(
        {
            "fact_in_corpus": True,
            "fact_in_candidates": False,
            "fact_in_reranked": False,
            "fact_in_final": False,
            "fact_coverage": {
                "corpus": 1.0,
                "candidates": 0.0,
                "reranked": 0.0,
                "final": 0.0,
            },
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "retrieval_miss"
    assert out["context_precision"]["root_cause"] == "retrieval_miss"
    assert out["faithfulness"]["root_cause"] == "inconclusive"
    for item in out.values():
        assert "\n" not in item["evidence"]


def test_faithfulness_does_not_use_fact_chain():
    c = _collector(
        {
            "fact_in_corpus": False,
            "fact_in_final": False,
            "fact_coverage": {"corpus": 0.0, "final": 0.0},
            "metric_attempted": True,
            "judge_failed": False,
        },
        judge={"faithfulness": {"score": 0.2, "error": ""}},
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["faithfulness"]["root_cause"] == "inconclusive"


def test_faithfulness_failed_run_is_prompt_or_generation():
    c = _collector({"metric_attempted": True, "judge_failed": False})
    out = attribute_question(c, sample_class="pure_kb", sample_failed=True)
    assert out["faithfulness"]["root_cause"] == "prompt_or_generation"


def test_null_fact_does_not_become_parsing():
    c = _collector(
        {
            "fact_in_corpus": None,
            "fact_in_candidates": None,
            "fact_in_final": None,
            "fact_coverage": {"corpus": None, "final": None},
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="web", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "inconclusive"


def test_empty_sample_is_not_judge_failure():
    c = _collector(
        {
            "metric_attempted": False,
            "skipped_reason": "sample_class=empty",
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="empty", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "inconclusive"
    assert "skipped" in out["context_recall"]["evidence"]


def test_empty_sample_tool_correctness_stays_attempted():
    c = _collector(
        {
            "metric_attempted": False,
            "skipped_reason": "sample_class=empty",
            "judge_failed": False,
        },
        judge={"tool_correctness": {"score": 1.0, "metric_attempted": True}},
    )
    out = attribute_question(c, sample_class="empty", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "inconclusive"
    assert out["context_recall"]["evidence"].startswith("skipped:")
    assert not out["tool_correctness"]["evidence"].startswith("skipped:")


def test_web_judge_failure_wins():
    c = _collector(
        {
            "metric_attempted": True,
            "judge_failed": True,
            "fact_in_corpus": None,
        },
        judge={"context_recall": {"score": float("nan"), "error": "boom"}},
    )
    out = attribute_question(c, sample_class="web", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "judge_failure"
    assert out["context_recall"]["confidence"] == "high"


def test_web_tool_fallback_is_not_routing():
    c = _collector(
        {
            "metric_attempted": True,
            "judge_failed": False,
            "tool_called": ["search_web"],
            "tool_correctness_reason": "missing retrieve_kb",
        },
        judge={
            "tool_correctness": {
                "score": 0.0,
                "error": "",
                "reason": "missing retrieve_kb",
            }
        },
    )
    out = attribute_question(c, sample_class="web", sample_failed=False)
    assert out["tool_correctness"]["root_cause"] == "inconclusive"


def test_web_ignores_non_null_fact_chain():
    c = _collector(
        {
            "fact_in_corpus": True,
            "fact_in_candidates": False,
            "fact_in_final": False,
            "fact_coverage": {"corpus": 1.0, "candidates": 0.0, "final": 0.0},
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="web", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "inconclusive"


def test_fact_not_in_corpus_is_parsing_or_chunking():
    c = _collector(
        {
            "fact_in_corpus": False,
            "fact_in_candidates": False,
            "fact_in_reranked": False,
            "fact_in_final": False,
            "fact_coverage": {
                "corpus": 0.0,
                "candidates": 0.0,
                "reranked": 0.0,
                "final": 0.0,
            },
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "parsing_or_chunking"
    assert out["contextual_relevancy"]["root_cause"] == "parsing_or_chunking"


def test_candidate_dropped_before_final_is_rerank_or_topk():
    c = _collector(
        {
            "fact_in_corpus": True,
            "fact_in_candidates": True,
            "fact_in_reranked": False,
            "fact_in_final": False,
            "fact_coverage": {
                "corpus": 1.0,
                "candidates": 1.0,
                "reranked": 0.0,
                "final": 0.0,
            },
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_precision"]["root_cause"] == "rerank_or_topk"


def test_precision_gold_in_final_but_partial_hit():
    c = _collector(
        {
            "fact_in_corpus": True,
            "fact_in_candidates": True,
            "fact_in_reranked": True,
            "fact_in_final": True,
            "fact_coverage": {
                "corpus": 1.0,
                "candidates": 1.0,
                "reranked": 1.0,
                "final": 1.0,
            },
            "metric_attempted": True,
            "judge_failed": False,
        },
        judge={"context_precision": {"score": 0.5, "error": ""}},
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_precision"]["root_cause"] == "rerank_or_topk"


def test_partial_fact_coverage_is_inconclusive():
    c = _collector(
        {
            "fact_in_corpus": True,
            "fact_in_candidates": True,
            "fact_in_reranked": None,
            "fact_in_final": None,
            "fact_coverage": {
                "corpus": 1.0,
                "candidates": 1.0,
                "reranked": 0.5,
                "final": None,
            },
            "missing_fact_tokens": ["80,348万元"],
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "inconclusive"
    assert "partial_fact_coverage" in out["context_recall"]["evidence"]
    assert "missing_fact_tokens" in out["context_recall"]["evidence"]


def test_numeric_conflict_is_testset_error():
    c = _collector(
        {
            "fact_in_corpus": False,
            "fact_in_candidates": False,
            "fact_coverage": {"corpus": 0.0, "final": 0.0},
            "testset_conflict": {"numeric_conflict": True, "period_conflict": None},
            "metric_attempted": True,
            "judge_failed": False,
        }
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_recall"]["root_cause"] == "testset_error"
    assert out["context_recall"]["confidence"] == "medium"


def test_retrieval_miss_confidence_from_gold():
    facts = {
        "fact_in_corpus": True,
        "fact_in_candidates": False,
        "fact_in_reranked": False,
        "fact_in_final": False,
        "fact_coverage": {
            "corpus": 1.0,
            "candidates": 0.0,
            "reranked": 0.0,
            "final": 0.0,
        },
        "gold_chunk_source": "testset",
        "gold_chunk_confidence": "high",
        "metric_attempted": True,
        "judge_failed": False,
    }
    out = attribute_question(
        _collector(facts), sample_class="pure_kb", sample_failed=False
    )
    assert out["context_recall"]["root_cause"] == "retrieval_miss"
    assert out["context_recall"]["confidence"] == "high"


def test_tool_routing_only_with_missing_label():
    c = _collector(
        {
            "metric_attempted": True,
            "judge_failed": False,
            "tool_called": ["search_web"],
            "tool_correctness_reason": "missing retrieve_kb",
        },
        judge={"tool_correctness": {"score": 0.0, "error": ""}},
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["tool_correctness"]["root_cause"] == "tool_routing"


def test_tool_partial_match_is_not_routing():
    c = _collector(
        {
            "metric_attempted": True,
            "judge_failed": False,
            "tool_called": ["retrieve_kb"],
            "tool_correctness_reason": "partial match",
        },
        judge={"tool_correctness": {"score": 0.5, "error": ""}},
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["tool_correctness"]["root_cause"] == "inconclusive"


def test_tool_failed_run_is_not_routing():
    c = _collector(
        {
            "metric_attempted": True,
            "judge_failed": False,
            "tool_called": [],
            "tool_correctness_reason": "failed",
        },
        judge={"tool_correctness": {"score": None, "error": ""}},
    )
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["tool_correctness"]["root_cause"] == "inconclusive"


def test_behavior_metric_aliases():
    c = _collector({"metric_attempted": True, "judge_failed": False})
    c.behavior = {
        "metrics": {
            "Task Completion": 0.2,
            "Step Efficiency": 0.2,
            "Agent Loop Detection": 0.2,
        }
    }
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["TaskCompletion"]["root_cause"] == "inconclusive"
    assert out["StepEfficiency"]["root_cause"] == "agent_orchestration"
    assert out["AgentLoopDetection"]["root_cause"] == "agent_orchestration"


def test_behavior_missing_score_is_inconclusive():
    c = _collector({"metric_attempted": True, "judge_failed": False})
    c.behavior = {"metrics": {"Task Completion": None}}
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["TaskCompletion"]["root_cause"] == "inconclusive"


def test_summary_counts_root_causes_and_skipped_separately():
    scored = _collector({"metric_attempted": True, "judge_failed": False})
    scored.attribution = {
        "context_recall": {
            "root_cause": "retrieval_miss",
            "confidence": "medium",
            "evidence": "fact_in_corpus=true, fact_in_candidates=false",
        },
        "context_precision": {
            "root_cause": "retrieval_miss",
            "confidence": "medium",
            "evidence": "fact_in_corpus=true, fact_in_candidates=false",
        },
        "faithfulness": {
            "root_cause": "inconclusive",
            "confidence": "inconclusive",
            "evidence": "no rule",
        },
    }
    skipped = _collector({"metric_attempted": False, "judge_failed": False})
    skipped.attribution = {
        "context_recall": {
            "root_cause": "inconclusive",
            "confidence": "inconclusive",
            "evidence": "skipped:sample_class=empty",
        },
        "tool_correctness": {
            "root_cause": "inconclusive",
            "confidence": "inconclusive",
            "evidence": "run failed",
        },
    }
    skipped.judge = {
        "context_recall": {"metric_attempted": False},
        "context_precision": {"metric_attempted": False},
        "tool_correctness": {"metric_attempted": False},
    }
    out = summarize_attribution([None, scored, skipped])
    assert out["root_cause_counts"]["total"] == {
        "retrieval_miss": 2,
        "inconclusive": 1,
    }
    assert out["root_cause_counts"]["by_metric"]["context_recall"] == {
        "retrieval_miss": 1
    }
    assert out["scored_metric_count"] == 3
    assert out["skipped_metric_count"] == 3
    assert out["inconclusive_count"] == 1


def test_summary_is_empty_without_attribution():
    out = summarize_attribution([None])
    assert out["root_cause_counts"] == {"by_metric": {}, "total": {}}
    assert out["scored_metric_count"] == 0
    assert out["skipped_metric_count"] == 0
    assert out["inconclusive_count"] == 0
