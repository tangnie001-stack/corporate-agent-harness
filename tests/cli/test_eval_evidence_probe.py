from src.cli.eval_evidence_probe import (
    answer_mentions_fact,
    compute_testset_conflict,
    extract_fact_tokens,
    normalize_number_token,
    probe_reference_chunks,
    resolve_gold,
)
from src.core.eval_evidence import parent_key
from src.infra.db.vector_store.types import ChunkResult


def _chunk(cid: str, content: str, parent: str | None = None) -> ChunkResult:
    meta = {"doc_id": "d1", "source": "a.pdf", "page": 1}
    if parent is not None:
        meta["parent_content"] = parent
    return ChunkResult(id=cid, content=content, metadata=meta)


def test_number_normalization():
    assert normalize_number_token("-12,090,412元") == "-12090412元"
    assert normalize_number_token("８０，３４８") == "80348"
    assert normalize_number_token("330.18 %") == "330.18%"


def test_extract_fact_tokens_dedupes():
    assert extract_fact_tokens("A 80,348，B 80,348，C 42%") == ["80348", "42%"]


def test_probe_hits_content_and_parent():
    chunks = [
        _chunk("d1:0", "noise"),
        _chunk("d1:1", "非控制性权益 80,348", parent="表头：单位千元；80,348"),
    ]
    result = probe_reference_chunks("非控制性权益 80,348", chunks)
    assert result["fact_in_corpus"] is True
    assert "d1:1" in result["gold_chunk_ids"]


def test_probe_partial_coverage():
    chunks = [_chunk("d1:0", "只有 80,348")]
    result = probe_reference_chunks("80,348 与 42%", chunks)
    assert result["fact_in_corpus"] is True
    assert result["fact_coverage"]["corpus"] < 1.0
    assert result["missing_fact_tokens"] == ["42%"]


def test_probe_no_tokens_is_null():
    result = probe_reference_chunks("没有数值", [])
    assert result["fact_in_corpus"] is None
    assert result["fact_coverage"]["corpus"] is None


def test_probe_empty_corpus_is_null():
    result = probe_reference_chunks("80,348", [])
    assert result["fact_in_corpus"] is None
    assert result["gold_chunk_ids"] == []


def test_probe_cap_marks_truncated():
    chunks = [_chunk(f"d1:{index}", "80,348") for index in range(3)]
    result = probe_reference_chunks("80,348", chunks, cap=2)
    assert result["gold_chunk_ids"] == ["d1:0", "d1:1"]
    assert result["truncated"] is True

    exact = probe_reference_chunks("80,348", chunks, cap=3)
    assert exact["truncated"] is False


def test_probe_reports_parent_key_and_chunk_detail():
    chunk = _chunk("d1:1", "正文 80,348", parent="表头 80,348")
    result = probe_reference_chunks("80,348", [chunk])
    expected = parent_key("d1", "d1:1", "表头 80,348")
    assert result["gold_parent_keys"] == [expected]
    assert result["gold_chunks"] == [
        {
            "chunk_id": "d1:1",
            "doc_id": "d1",
            "parent_key": expected,
            "source": "a.pdf",
            "page": 1,
        }
    ]


def test_answer_mentions_fact_uses_shared_normalization():
    tokens = extract_fact_tokens("营业收入 80,348 万元")
    assert answer_mentions_fact("营业收入为 80,348万元", tokens) is True
    assert answer_mentions_fact("营业收入为 42 万元", tokens) is False
    assert answer_mentions_fact("没有数值", []) is None


def test_source_chunk_ids_exact_and_group():
    chunks = [_chunk("d1:0", "x"), _chunk("d1:1", "y")]
    exact = resolve_gold(
        {"source_chunk_ids": ["d1:0"], "match_scope": "exact"}, chunks, "x"
    )
    group = resolve_gold(
        {"source_chunk_ids": ["d1:0", "d1:1"], "match_scope": "group"}, chunks, "x"
    )
    assert exact["gold_chunk_confidence"] == "high"
    assert exact["gold_chunk_source"] == "testset"
    assert group["gold_chunk_confidence"] == "medium"


def test_stale_source_ids_degrade():
    chunks = [_chunk("d1:0", "x")]
    out = resolve_gold(
        {"source_chunk_ids": ["d1:9"], "match_scope": "exact"}, chunks, "x"
    )
    assert out["gold_chunk_source"] in ("probe", None)
    assert out["gold_chunk_confidence"] != "high"


def test_stale_source_ids_fall_back_to_probe():
    chunks = [_chunk("d1:0", "80,348")]
    out = resolve_gold(
        {"source_chunk_ids": ["d1:9"], "match_scope": "exact"}, chunks, "80,348"
    )
    assert out["gold_chunk_source"] == "probe"
    assert out["gold_chunk_confidence"] == "medium"
    assert out["gold_chunk_ids"] == ["d1:0"]


def test_cross_doc_source_ids_degrade():
    chunk = ChunkResult(id="d2:0", content="x", metadata={"doc_id": "d1"})
    out = resolve_gold(
        {"source_chunk_ids": ["d2:0"], "match_scope": "exact"}, [chunk], "x"
    )
    assert out["gold_chunk_source"] is None
    assert out["gold_chunk_confidence"] is None


def test_testset_conflict_numeric_risk():
    probe = {
        "probe_tokens": ["80348"],
        "fact_in_corpus": False,
        "missing_fact_tokens": ["80348"],
    }
    risky = compute_testset_conflict("80,348", probe, numeric_risk=True)
    assert risky["numeric_conflict"] is True
    assert risky["missing_fact_tokens"] == ["80348"]

    safe = compute_testset_conflict("80,348", probe)
    assert safe["numeric_conflict"] is False


def test_testset_conflict_null_without_tokens_or_corpus():
    no_tokens = compute_testset_conflict(
        "没有数值",
        {"probe_tokens": [], "fact_in_corpus": None, "missing_fact_tokens": []},
    )
    assert no_tokens["numeric_conflict"] is None
    assert no_tokens["unit_missing"] is None
    assert no_tokens["period_conflict"] is None

    no_corpus = compute_testset_conflict(
        "80,348",
        {"probe_tokens": ["80348"], "fact_in_corpus": None, "missing_fact_tokens": []},
    )
    assert no_corpus["numeric_conflict"] is None
    assert no_corpus["unit_missing"] is None


def test_unit_missing_checks_four_char_window():
    probe = {
        "probe_tokens": ["80348"],
        "fact_in_corpus": True,
        "missing_fact_tokens": [],
    }
    assert compute_testset_conflict("80,348（万元）", probe)["unit_missing"] is False
    assert compute_testset_conflict("80,348", probe)["unit_missing"] is True

    percent_probe = {
        "probe_tokens": ["42%"],
        "fact_in_corpus": True,
        "missing_fact_tokens": [],
    }
    assert compute_testset_conflict("42%", percent_probe)["unit_missing"] is False
