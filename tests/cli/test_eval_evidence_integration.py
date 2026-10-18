"""证据链端到端：事实阶段 → 归因 → 落盘 JSON。"""

import json

import pandas as pd

from src.cli import eval_evidence_report as report
from src.cli.eval_evidence_attribution import attribute_question
from src.cli.eval_evidence_report import write_evidence_run
from src.core.eval_evidence import EvidenceRun, parent_key
from src.infra.db.vector_store.types import ChunkResult


def test_dense_hit_but_final_miss_is_rerank_or_topk():
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    c = run.new_question(0)
    c.facts = {
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
    c.judge = {"context_precision": {}}
    out = attribute_question(c, sample_class="pure_kb", sample_failed=False)
    assert out["context_precision"]["root_cause"] == "rerank_or_topk"


def test_written_json_attributes_dense_hit_final_miss(tmp_path, monkeypatch):
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
    run = EvidenceRun(run_id="r1", run_trace_id="eval_run_r1_abcd1234", enabled=True)
    run.corpus_chunks = [chunk]
    collector = run.new_question(0)
    collector.testset = {"reference": "2024年营收12亿元"}
    collector.judge = {"context_recall": {"score": 0.0, "metric_attempted": True}}
    # 密集路命中，精排与最终上下文都没有：事实链应停在 reranked
    collector.retrieval = {
        "dense": {"items": [{"chunk_id": "doc1:0", "parent_key": key}]},
        "rerank_after": {"items": []},
        "final_contexts": [],
    }
    monkeypatch.setattr(report, "EVAL_EVIDENCE_DIR", str(tmp_path))
    write_evidence_run(
        run,
        questions=["Q0"],
        scored_df=pd.DataFrame([{"q_index": 0, "context_recall": 0.0, "error": ""}]),
        sample_classes=["pure_kb"],
        behaviour_rows=[
            {
                "q_index": 0,
                "metrics": {
                    "Task Completion": 1.0,
                    "Step Efficiency": 1.0,
                    "Agent Loop Detection": 1.0,
                },
            }
        ],
        trace_ids=["t0"],
        run_id="r1",
        run_trace_id="eval_run_r1_abcd1234",
        all_questions=False,
    )

    payload = json.loads((tmp_path / "r1" / "q_0.json").read_text(encoding="utf-8"))
    assert payload["facts"]["fact_in_corpus"] is True
    assert payload["facts"]["fact_in_candidates"] is True
    assert payload["facts"]["fact_in_reranked"] is False
    assert payload["facts"]["fact_in_final"] is False
    assert payload["attribution"]["context_recall"]["root_cause"] == "rerank_or_topk"
