"""证据选题、组装与原子写盘的测试。"""

import json

import pandas as pd

from src.cli import eval_evidence_report as report
from src.cli import eval_evidence_writer as writer
from src.cli.eval_evidence_report import should_write_evidence, write_evidence_run
from src.core.eval_evidence import EvidenceRun

_RUN_TRACE_ID = "eval_run_r1_abcd1234"
_GOOD_BEHAVIOUR = {
    "Task Completion": 1.0,
    "Step Efficiency": 1.0,
    "Agent Loop Detection": 1.0,
}


def _df(**scores):
    return pd.DataFrame([{"q_index": 0, **scores}])


def _behavior(metrics=None):
    return [{"q_index": 0, "metrics": metrics or dict(_GOOD_BEHAVIOUR)}]


def _run(enabled=True):
    run = EvidenceRun(run_id="r1", run_trace_id=_RUN_TRACE_ID, enabled=enabled)
    collector = run.new_question(0)
    return run, collector


def _write(tmp_path, monkeypatch, run, **overrides):
    """按统一参数调用 write_evidence_run，落盘目录指向 tmp_path。"""
    monkeypatch.setattr(report, "EVAL_EVIDENCE_DIR", str(tmp_path))
    kwargs = {
        "questions": ["Q0"],
        "scored_df": None,
        "sample_classes": ["pure_kb"],
        "behaviour_rows": _behavior(),
        "trace_ids": ["t0"],
        "run_id": "r1",
        "run_trace_id": _RUN_TRACE_ID,
        "all_questions": False,
    }
    kwargs.update(overrides)
    write_evidence_run(run, **kwargs)


def _payload(tmp_path, name="q_0.json"):
    return json.loads((tmp_path / "r1" / name).read_text(encoding="utf-8"))


def test_low_score_selection():
    row = _df(
        faithfulness=0.5,
        answer_relevancy=0.9,
        context_precision=0.9,
        context_recall=0.9,
    ).iloc[0]
    assert (
        should_write_evidence(row, "pure_kb", _behavior(), all_questions=False) is True
    )


def test_web_main_score_does_not_trigger():
    row = _df(
        faithfulness=0.1,
        answer_relevancy=0.1,
        context_precision=0.1,
        context_recall=0.1,
    ).iloc[0]
    assert should_write_evidence(row, "web", _behavior(), all_questions=False) is False


def test_web_selected_when_tool_correctness_low():
    row = _df(
        faithfulness=1.0,
        answer_relevancy=1.0,
        context_precision=1.0,
        context_recall=1.0,
        tool_correctness=0.0,
    ).iloc[0]
    assert should_write_evidence(row, "web", _behavior(), all_questions=False) is True


def test_web_main_scores_do_not_trigger_with_perfect_tool():
    row = _df(
        faithfulness=0.1,
        answer_relevancy=0.1,
        context_precision=0.1,
        context_recall=0.1,
        tool_correctness=1.0,
    ).iloc[0]
    assert should_write_evidence(row, "web", _behavior(), all_questions=False) is False


def test_empty_always_selected():
    row = _df(
        faithfulness=None,
        answer_relevancy=None,
        context_precision=None,
        context_recall=None,
    ).iloc[0]
    assert should_write_evidence(row, "empty", [], all_questions=False) is True


def test_failed_row_selected_even_with_full_scores():
    row = _df(
        faithfulness=1.0,
        answer_relevancy=1.0,
        context_precision=1.0,
        context_recall=1.0,
        error="failed",
    ).iloc[0]
    assert (
        should_write_evidence(row, "pure_kb", _behavior(), all_questions=False) is True
    )


def test_healthy_row_not_selected():
    row = _df(
        faithfulness=1.0,
        answer_relevancy=1.0,
        context_precision=1.0,
        context_recall=1.0,
        tool_correctness=1.0,
    ).iloc[0]
    assert (
        should_write_evidence(row, "pure_kb", _behavior(), all_questions=False) is False
    )


def test_write_summary_even_without_selection(tmp_path, monkeypatch):
    run, _collector = _run()
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame(
            [{"q_index": 0, "faithfulness": 1.0, "sample_class": "pure_kb"}]
        ),
    )
    summary = _payload(tmp_path, "summary.json")
    assert summary["evidence_files"] == []
    assert summary["root_cause_counts"]["total"] == {}


def test_disabled_run_writes_nothing(tmp_path, monkeypatch):
    run, _collector = _run(enabled=False)
    _write(tmp_path, monkeypatch, run, all_questions=True)
    assert not (tmp_path / "r1").exists()


def test_web_guard_nulls_fact_chain_and_skips_probe(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": "2024年营收 12 亿元"}
    collector.retrieval = {
        "final_contexts": [{"chunk_id": "c1", "parent_key": ("d1", "p1")}]
    }

    def _boom(*args, **kwargs):
        raise AssertionError("web 样本不应调用探针")

    monkeypatch.setattr(writer, "probe_reference_chunks", _boom)
    monkeypatch.setattr(writer, "resolve_gold", _boom)
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 1.0, "error": ""}]),
        sample_classes=["web"],
        all_questions=True,
    )

    payload = _payload(tmp_path)
    facts = payload["facts"]
    assert payload["retrieval"]["status"] == "not_applicable"
    assert facts["fact_coverage"] == {
        "corpus": None,
        "candidates": None,
        "reranked": None,
        "final": None,
    }
    for key in (
        "fact_in_corpus",
        "fact_in_candidates",
        "fact_in_reranked",
        "fact_in_final",
    ):
        assert facts[key] is None
    assert facts["fact_tokens"] == []
    assert facts["fact_hit_tokens"] == {
        "corpus": [],
        "candidates": [],
        "reranked": [],
        "final": [],
    }


def test_empty_tokens_write_null_markers(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": ""}
    run.corpus_chunks = []
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 0.2, "error": ""}]),
    )

    facts = _payload(tmp_path)["facts"]
    assert facts["fact_tokens"] == []
    assert facts["fact_in_corpus"] is None
    assert facts["fact_in_candidates"] is None
    assert facts["fact_in_reranked"] is None
    assert facts["fact_in_final"] is None
    assert facts["fact_coverage"] == {
        "corpus": None,
        "candidates": None,
        "reranked": None,
        "final": None,
    }
    assert facts["answer_mentions_fact"] is None


def test_failed_row_marks_metric_skipped(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": ""}
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame(
            [{"q_index": 0, "faithfulness": None, "error": "failed"}]
        ),
    )

    facts = _payload(tmp_path)["facts"]
    assert facts["metric_attempted"] is False
    assert facts["skipped_reason"] == "failed"


def test_trace_payload_keys_and_judge_calls_rename(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": ""}
    collector.trace_id = "sut_t0"
    collector.trace["judge_measures"] = [
        {
            "metric": "faithfulness",
            "reason": "ok",
            "calls": [{"call_index": 1, "status": 200}],
        }
    ]
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 0.2, "error": ""}]),
    )

    trace = _payload(tmp_path)["trace"]
    assert set(trace) == {"run_trace_id", "sut_trace_id", "judge_measures", "behavior"}
    assert trace["run_trace_id"] == _RUN_TRACE_ID
    assert trace["sut_trace_id"] == "sut_t0"
    assert trace["judge_measures"] == [
        {
            "metric": "faithfulness",
            "reason": "ok",
            "judge_calls": [{"call_index": 1, "status": 200}],
        }
    ]
    assert trace["behavior"] == {"trace_id": "t0", "link_method": "order"}


def test_trajectory_tool_calls_merged_without_duplicating_retrieve(
    tmp_path, monkeypatch
):
    run, collector = _run()
    collector.testset = {"reference": ""}
    collector.tool_calls = [
        {
            "name": "retrieve_kb",
            "args": {"query": "营收"},
            "call_index": 1,
            "result_count": 3,
        }
    ]
    collector.trajectory = {
        "tool_calls": [
            {"name": "retrieve_kb", "args": {"query": "营收"}},
            {"name": "web_search", "args": {"query": "行业增速"}},
            {"name": "calculator", "args": {"expr": "1+1"}},
        ],
        "iterations": 3,
    }
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 0.2, "error": ""}]),
    )

    calls = _payload(tmp_path)["tool_calls"]
    assert [call["name"] for call in calls] == [
        "retrieve_kb",
        "web_search",
        "calculator",
    ]
    assert [call["call_index"] for call in calls] == [1, 2, 3]
    assert calls[1]["args"] == {"query": "行业增速"}
    assert calls[1]["result_count"] is None
    assert calls[1]["latency_ms"] is None
    assert calls[1]["iteration"] is None
    facts = _payload(tmp_path)["facts"]
    assert facts["tool_called"] == ["retrieve_kb", "web_search", "calculator"]
    assert facts["call_count"] == 3


def test_deepeval_only_selects_from_behaviour(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": ""}
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=None,
        behaviour_rows=_behavior(
            {
                "Task Completion": 1.0,
                "Step Efficiency": 0.1,
                "Agent Loop Detection": 1.0,
            }
        ),
    )
    payload = _payload(tmp_path)
    assert payload["judge"] == {}
    assert payload["behavior"]["metrics"]["Step Efficiency"] == 0.1


def test_probe_failure_is_contained(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": "2024年营收 12 亿元"}
    second = run.new_question(1)
    second.testset = {"reference": ""}

    def _boom(reference, *args, **kwargs):
        if reference:
            raise RuntimeError("probe down")
        return {}

    monkeypatch.setattr(writer, "probe_reference_chunks", _boom)
    _write(
        tmp_path,
        monkeypatch,
        run,
        questions=["Q0", "Q1"],
        scored_df=None,
        sample_classes=["pure_kb", "pure_kb"],
        all_questions=True,
    )

    assert not (tmp_path / "r1" / "q_0.json").exists()
    assert (tmp_path / "r1" / "q_1.json").exists()
    summary = _payload(tmp_path, "summary.json")
    assert summary["evidence_files"] == ["q_1.json"]
    assert summary["low_score_questions"] == [1]


def test_attribution_failure_still_writes_question(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": ""}
    collector.judge = {"faithfulness": {"score": 0.2, "metric_attempted": True}}

    def _boom(*args, **kwargs):
        raise RuntimeError("attribution down")

    monkeypatch.setattr(writer, "attribute_question", _boom)
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 0.2, "error": ""}]),
    )

    attribution = _payload(tmp_path)["attribution"]
    assert attribution["faithfulness"]["evidence"] == "attribution_failed"
    assert attribution["tool_correctness"]["evidence"] == "attribution_failed"
    assert (tmp_path / "r1" / "summary.json").exists()


def test_non_finite_scores_serialized_as_null(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": ""}
    collector.judge = {
        "faithfulness": {
            "score": float("nan"),
            "error": "",
            "metric_attempted": True,
        }
    }
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 0.2, "error": ""}]),
        behaviour_rows=_behavior(
            {
                "Task Completion": float("nan"),
                "Step Efficiency": 1.0,
                "Agent Loop Detection": 1.0,
            }
        ),
    )

    raw = (tmp_path / "r1" / "q_0.json").read_text(encoding="utf-8")
    assert "NaN" not in raw
    payload = json.loads(raw)
    assert payload["judge"]["faithfulness"]["score"] is None
    assert payload["behavior"]["metrics"]["Task Completion"] is None


def test_evidence_dir_is_report_dir_sibling():
    from pathlib import Path

    from src.config import EVAL_REPORT_DIR

    assert Path(report.EVAL_EVIDENCE_DIR) == Path(EVAL_REPORT_DIR).parent / "evidence"


def test_atomic_write_leaves_no_temp_files(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": ""}
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 0.2, "error": ""}]),
    )
    names = sorted(path.name for path in (tmp_path / "r1").iterdir())
    assert names == ["q_0.json", "summary.json"]


def test_summary_reports_written_files(tmp_path, monkeypatch):
    run, collector = _run()
    collector.testset = {"reference": ""}
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 0.2, "error": ""}]),
    )
    summary = _payload(tmp_path, "summary.json")
    assert summary["evidence_files"] == ["q_0.json"]
    assert summary["low_score_questions"] == [0]
    assert summary["qa_count"] == 1


def test_snapshot_judge_marks_empty_metrics_skipped_but_keeps_tool():
    from src.cli.eval_deepeval import _snapshot_judge

    scored = {
        "q_index": 0,
        "faithfulness": None,
        "tool_correctness": 1.0,
        "error": "sample_class=empty",
    }
    out = _snapshot_judge(scored, None, None)
    assert out["faithfulness"]["metric_attempted"] is False
    assert out["faithfulness"]["skipped_reason"] == "sample_class=empty"
    assert out["tool_correctness"]["metric_attempted"] is True
    assert out["tool_correctness"]["skipped_reason"] is None
    assert out["tool_correctness"]["error"] == ""


def test_snapshot_judge_failed_run_skips_tool():
    from src.cli.eval_deepeval import _snapshot_judge

    scored = {
        "q_index": 0,
        "faithfulness": None,
        "tool_correctness": None,
        "error": "failed",
    }
    out = _snapshot_judge(scored, None, None)
    assert out["faithfulness"]["skipped_reason"] == "failed"
    assert out["tool_correctness"]["metric_attempted"] is False
    assert out["tool_correctness"]["skipped_reason"] == "failed"


def test_snapshot_judge_uses_per_metric_errors():
    from src.cli.eval_deepeval import _snapshot_judge

    scored = {
        "q_index": 0,
        "faithfulness": float("nan"),
        "context_recall": 0.9,
        "error": "faithfulness:boom",
    }
    out = _snapshot_judge(scored, None, {"faithfulness": "faithfulness:boom"})
    assert out["faithfulness"]["error"] == "faithfulness:boom"
    assert out["faithfulness"]["metric_attempted"] is True
    assert out["faithfulness"]["skipped_reason"] is None
    assert out["context_recall"]["error"] == ""
    assert out["context_recall"]["skipped_reason"] is None


def test_score_rows_passes_per_metric_errors(monkeypatch):
    from src.cli import eval_deepeval

    run, collector = _run()

    def _fake_score(name, metric, case, row, evidence):
        if name == "faithfulness":
            return float("nan"), "faithfulness:boom"
        return 0.9, ""

    monkeypatch.setattr(eval_deepeval, "_score_metric_with_retry", _fake_score)
    rows = [
        {
            "q_index": 0,
            "question": "q",
            "answer": "ok",
            "ground_truth": "gt",
            "contexts": ["c"],
            "trajectory": {},
            "expected_tools": None,
            "sample_class": "pure_kb",
            "trace_id": "t0",
        }
    ]
    eval_deepeval.score_rows(
        rows,
        {"faithfulness": object(), "context_recall": object()},
        evidence_run=run,
    )

    judge = collector.judge
    assert judge["faithfulness"]["error"] == "faithfulness:boom"
    assert judge["faithfulness"]["skipped_reason"] is None
    assert judge["context_recall"]["error"] == ""
    assert judge["context_recall"]["skipped_reason"] is None


class _FakeHookEvidence:
    """最小 HTTP 证据桶：验证 measure 深拷发生在 reset 之前。"""

    def __init__(self):
        """预置一次 measure 记录。"""
        self.measures = [
            {"metric": "faithfulness", "calls": [{"call_index": 1, "status": 200}]}
        ]

    def reset(self):
        """模拟下一题评分前的清空。"""
        self.measures.clear()


def test_capture_evidence_deep_copies_measures():
    from src.cli.eval_deepeval import _capture_evidence

    run, collector = _run()
    hook = _FakeHookEvidence()
    _capture_evidence({"q_index": 0, "faithfulness": 0.5, "error": ""}, run, hook)
    hook.reset()
    assert collector.judge["faithfulness"]["score"] == 0.5
    assert collector.trace["judge_measures"] == [
        {"metric": "faithfulness", "calls": [{"call_index": 1, "status": 200}]}
    ]


def test_capture_evidence_swallows_malformed_measure():
    from src.cli.eval_deepeval import _capture_evidence

    class _BrokenEvidence:
        """measure 记录结构损坏，模拟上游异常。"""

        def __init__(self):
            self.measures = [None]

    run, collector = _run()
    _capture_evidence(
        {"q_index": 0, "faithfulness": 0.5, "error": ""}, run, _BrokenEvidence()
    )
    assert collector.judge == {}


def test_pure_kb_fact_chain_with_real_chunks(tmp_path, monkeypatch):
    from src.core.eval_evidence import parent_key
    from src.infra.db.vector_store.types import ChunkResult

    chunk = ChunkResult(
        id="doc1:0",
        content="2024年营收12亿元，同比增长5%",
        metadata={
            "doc_id": "doc1",
            "source": "a.pdf",
            "page": 1,
            "parent_content": "2024年营收12亿元",
        },
    )
    key = parent_key("doc1", "doc1:0", "2024年营收12亿元")
    run, collector = _run()
    collector.testset = {"reference": "2024年营收12亿元"}
    run.corpus_chunks = [chunk]
    collector.retrieval = {
        "dense": {"items": [{"chunk_id": "doc1:0", "parent_key": key}]},
        "rrf": {"items": [{"chunk_id": "doc1:0", "parent_key": key}]},
        "rerank_after": {"items": [{"chunk_id": "doc1:0", "parent_key": key}]},
        "final_contexts": [{"chunk_id": "doc1:0", "parent_key": key}],
    }
    _write(
        tmp_path,
        monkeypatch,
        run,
        scored_df=pd.DataFrame([{"q_index": 0, "faithfulness": 0.2, "error": ""}]),
    )

    facts = _payload(tmp_path)["facts"]
    assert facts["gold_chunk_source"] == "probe"
    assert facts["gold_chunk_ids"] == ["doc1:0"]
    assert facts["fact_in_corpus"] is True
    assert facts["fact_in_candidates"] is True
    assert facts["fact_in_reranked"] is True
    assert facts["fact_in_final"] is True
    assert facts["fact_coverage"]["final"] == 1.0
    assert facts["missing_fact_tokens"] == []
