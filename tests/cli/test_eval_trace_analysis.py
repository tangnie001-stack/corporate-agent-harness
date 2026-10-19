from src.cli.eval_trace_analysis import analyze_trace, summarize_batch


def _parsed(
    *,
    query: str = "腾讯2024年营收",
    answer: str = "腾讯2024年营收为6603亿元。",
    contexts: list[dict] | None = None,
    tools: list[dict] | None = None,
    generations: list[dict] | None = None,
) -> dict:
    if contexts is None:
        contexts = [{"source": "tencent_2024_annual.pdf", "page": 1, "text": "x"}]
    return {
        "query": query,
        "answer": answer,
        "contexts": contexts,
        "tool_calls": tools or [{"name": "retrieve_kb"}],
        "generations": generations or [{"name": "agent_turn"}],
    }


def test_entity_mismatch_in_top3_is_rerank_or_topk():
    parsed = _parsed(
        contexts=[
            {"source": "tencent_2024_annual.pdf", "page": 1, "text": "a"},
            {"source": "neusoft_2024_q3.pdf", "page": 1, "text": "b"},
            {"source": "neusoft_2025_q1.pdf", "page": 1, "text": "c"},
        ]
    )

    analysis = analyze_trace(parsed, None)

    root = analysis["root_causes"]["contextual_relevancy"]
    assert root["root_cause"] == "rerank_or_topk"
    assert "top3_company_mismatch=2" in root["evidence"]


def test_all_contexts_wrong_company_is_retrieval_miss():
    parsed = _parsed(
        contexts=[
            {"source": "neusoft_2024_q3.pdf", "page": 1, "text": "b"},
            {"source": "neusoft_2025_q1.pdf", "page": 1, "text": "c"},
        ]
    )

    analysis = analyze_trace(parsed, None)

    assert (
        analysis["root_causes"]["contextual_relevancy"]["root_cause"]
        == "retrieval_miss"
    )


def test_empty_answer_is_prompt_or_generation():
    analysis = analyze_trace(_parsed(answer=""), None)

    assert (
        analysis["root_causes"]["answer_relevancy"]["root_cause"]
        == "prompt_or_generation"
    )


def test_judge_error_wins():
    analysis = analyze_trace(
        _parsed(),
        {"faithfulness": {"score": None, "error": "JSONDecodeError"}},
    )

    assert analysis["root_causes"]["faithfulness"]["root_cause"] == "judge_failure"


def test_missing_retrieve_tool_is_tool_routing():
    analysis = analyze_trace(_parsed(tools=[{"name": "search_web"}]), None)

    assert analysis["root_causes"]["tool_correctness"]["root_cause"] == "tool_routing"


def test_low_faithfulness_is_prompt_or_generation():
    analysis = analyze_trace(_parsed(), {"faithfulness": {"score": 0.2}})

    assert (
        analysis["root_causes"]["faithfulness"]["root_cause"] == "prompt_or_generation"
    )


def test_batch_summary_counts_unique_root_causes():
    first = {
        "trace_id": "t1",
        "analysis": {
            "root_causes": {
                "faithfulness": {"root_cause": "prompt_or_generation"},
                "contextual_relevancy": {"root_cause": "rerank_or_topk"},
            }
        },
    }
    second = {
        "trace_id": "t2",
        "analysis": {
            "root_causes": {
                "contextual_relevancy": {"root_cause": "rerank_or_topk"},
            }
        },
    }

    summary = summarize_batch([first, second])

    assert summary["trace_count"] == 2
    assert summary["root_cause_counts"] == {
        "prompt_or_generation": 1,
        "rerank_or_topk": 2,
    }
