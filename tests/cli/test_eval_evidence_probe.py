from src.cli.eval_evidence_probe import (
    answer_mentions_fact,
    compute_testset_conflict,
    extract_fact_tokens,
    extract_text_tokens,
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


def test_extract_fact_tokens_keeps_range_signs_out_of_negatives():
    assert extract_fact_tokens("2023-2024") == ["2023", "2024"]
    assert extract_fact_tokens("30%-50%") == ["30%", "50%"]
    assert normalize_number_token("亏损-12,090,412元") == "-12090412元"


def test_extract_text_tokens_filters_stopwords_and_length():
    tokens = extract_text_tokens(
        "公司，报告期，营业收入，同比增长，这是一个超过十二个汉字长度的文本片段需要被丢弃"
    )
    assert tokens == ["营业收入"]


def test_extract_text_tokens_accepts_ascii_window():
    tokens = extract_text_tokens("net profit, gross margin, rd")
    assert tokens == ["profit", "gross", "margin"]


def test_probe_text_only_hits_content():
    chunks = [_chunk("d1:0", "公司的主营业务为智能制造装备")]
    result = probe_reference_chunks("主营业务为智能制造装备", chunks)
    assert result["numeric_tokens"] == []
    assert result["probe_tokens"] == ["主营业务为智能制造装备"]
    assert result["fact_in_corpus"] is True
    assert result["gold_chunk_ids"] == ["d1:0"]


def test_probe_mixes_numeric_and_text_tokens():
    chunks = [_chunk("d1:0", "营业收入 80,348 万元")]
    result = probe_reference_chunks("营业收入 80,348 万元，同比增长", chunks)
    assert result["numeric_tokens"] == ["80348万元"]
    assert result["probe_tokens"] == ["80348万元", "营业收入"]
    assert result["fact_coverage"]["corpus"] == 1.0


def test_probe_coverage_counts_text_tokens():
    chunks = [_chunk("d1:0", "营业收入 80,348")]
    result = probe_reference_chunks("营业收入 80,348 42%", chunks)
    assert result["probe_tokens"] == ["80348", "42%", "营业收入"]
    assert result["fact_coverage"]["corpus"] == 2 / 3
    assert result["missing_fact_tokens"] == ["42%"]


def test_probe_hits_content_and_parent():
    chunks = [
        _chunk("d1:0", "noise"),
        _chunk("d1:1", "非控制性权益 80,348", parent="表头：单位千元；80,348"),
    ]
    result = probe_reference_chunks("非控制性权益 80,348", chunks)
    assert result["fact_in_corpus"] is True
    assert "d1:1" in result["gold_chunk_ids"]


def test_probe_table_unit_fallback():
    chunks = [_chunk("d1:0", "| 营业收入 | 80,348 |\n单位：万元")]
    result = probe_reference_chunks("80,348 万元", chunks)
    assert result["numeric_tokens"] == ["80348万元"]
    assert result["fact_in_corpus"] is True
    assert result["gold_chunk_ids"] == ["d1:0"]

    without_unit = probe_reference_chunks("80,348 万元", [_chunk("d1:1", "80,348")])
    assert without_unit["fact_in_corpus"] is False


def test_probe_partial_coverage():
    chunks = [_chunk("d1:0", "只有 80,348")]
    result = probe_reference_chunks("80,348 与 42%", chunks)
    assert result["fact_in_corpus"] is True
    assert result["fact_coverage"]["corpus"] < 1.0
    assert result["missing_fact_tokens"] == ["42%"]


def test_probe_no_tokens_is_null():
    result = probe_reference_chunks("同比，增长", [])
    assert result["fact_in_corpus"] is None
    assert result["fact_coverage"]["corpus"] is None
    assert result["token_hits"] == {}
    assert result["token_parent_keys"] == {}
    assert result["gold_chunk_ids"] is None


def test_probe_empty_corpus_is_null():
    result = probe_reference_chunks("80,348", [])
    assert result["fact_in_corpus"] is None
    assert result["gold_chunk_ids"] is None
    assert result["numeric_tokens"] == ["80348"]


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


def test_probe_parent_keys_deduped():
    chunks = [
        _chunk("d1:0", "80,348", parent="同一父块"),
        _chunk("d1:1", "80,348", parent="同一父块"),
    ]
    result = probe_reference_chunks("80,348", chunks)
    assert result["gold_chunk_ids"] == ["d1:0", "d1:1"]
    assert result["gold_parent_keys"] == [parent_key("d1", "d1:0", "同一父块")]


def test_answer_mentions_fact_uses_shared_normalization():
    tokens = extract_fact_tokens("营业收入 80,348 万元")
    assert answer_mentions_fact("营业收入为 80,348万元", tokens) is True
    assert answer_mentions_fact("营业利润为 42 万元", tokens) is False
    assert answer_mentions_fact("没有数值", []) is None


def test_answer_mentions_fact_matches_text_token():
    tokens = extract_fact_tokens("智能制造装备业务")
    assert answer_mentions_fact("公司智能制造装备业务收入增长", tokens) is True
    assert answer_mentions_fact("公司软件服务收入增长", tokens) is False


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


def test_source_chunk_ids_deduped():
    chunks = [_chunk("d1:0", "x")]
    out = resolve_gold(
        {"source_chunk_ids": ["d1:0", "d1:0"], "match_scope": "exact"}, chunks, "x"
    )
    assert out["gold_chunk_ids"] == ["d1:0"]
    assert out["gold_chunk_confidence"] == "high"


def test_resolve_gold_keeps_probe_fields_with_testset_gold():
    chunks = [_chunk("d1:0", "80,348"), _chunk("d1:1", "80,348")]
    out = resolve_gold(
        {"source_chunk_ids": ["d1:1"], "match_scope": "exact"}, chunks, "80,348"
    )
    assert out["gold_chunk_ids"] == ["d1:1"]
    assert out["gold_chunk_source"] == "testset"
    assert out["gold_chunk_confidence"] == "high"
    assert out["fact_in_corpus"] is True
    assert out["probe_tokens"] == ["80348"]
    assert out["numeric_tokens"] == ["80348"]
    assert out["missing_fact_tokens"] == []
    assert out["token_hits"]["80348"] == ["d1:0", "d1:1"]
    assert out["token_parent_keys"]["80348"] == [
        parent_key("d1", "d1:0", ""),
        parent_key("d1", "d1:1", ""),
    ]


def test_gold_chunk_ids_null_when_no_gold():
    chunks = [_chunk("d1:0", "noise")]
    out = resolve_gold({"match_scope": "exact"}, chunks, "42%")
    assert out["gold_chunk_ids"] is None
    assert out["gold_parent_keys"] is None
    assert out["gold_chunks"] is None
    assert out["gold_chunk_source"] is None
    assert out["fact_in_corpus"] is False

    no_tokens = resolve_gold({"match_scope": "exact"}, chunks, "同比，增长")
    assert no_tokens["gold_chunk_ids"] is None
    assert no_tokens["fact_in_corpus"] is None

    hit = resolve_gold({"match_scope": "exact"}, chunks, "80,348")
    assert hit["gold_chunk_ids"] is None


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
        "numeric_tokens": ["80348"],
        "fact_in_corpus": False,
        "missing_fact_tokens": ["80348"],
    }
    risky = compute_testset_conflict("80,348", probe, numeric_risk=True)
    assert risky["numeric_conflict"] is True
    assert risky["missing_fact_tokens"] == ["80348"]

    safe = compute_testset_conflict("80,348", probe)
    assert safe["numeric_conflict"] is False


def test_testset_conflict_false_when_numeric_found():
    probe = {
        "probe_tokens": ["80348", "营业收入"],
        "numeric_tokens": ["80348"],
        "fact_in_corpus": True,
        "missing_fact_tokens": [],
    }
    out = compute_testset_conflict("80,348 营业收入", probe, numeric_risk=True)
    assert out["numeric_conflict"] is False


def test_testset_conflict_ignores_text_token_hits():
    probe = {
        "probe_tokens": ["80348", "营业收入"],
        "numeric_tokens": ["80348"],
        "fact_in_corpus": True,
        "missing_fact_tokens": ["80348"],
    }
    out = compute_testset_conflict("80,348 营业收入", probe, numeric_risk=True)
    assert out["numeric_conflict"] is True


def test_testset_conflict_null_without_tokens_or_corpus():
    no_tokens = compute_testset_conflict(
        "同比，增长",
        {
            "probe_tokens": [],
            "numeric_tokens": [],
            "fact_in_corpus": None,
            "missing_fact_tokens": [],
        },
    )
    assert no_tokens["numeric_conflict"] is None
    assert no_tokens["unit_missing"] is None
    assert no_tokens["period_conflict"] is None

    no_corpus = compute_testset_conflict(
        "80,348",
        {
            "probe_tokens": ["80348"],
            "numeric_tokens": ["80348"],
            "fact_in_corpus": None,
            "missing_fact_tokens": [],
        },
    )
    assert no_corpus["numeric_conflict"] is None
    assert no_corpus["unit_missing"] is None


def test_unit_missing_checks_four_char_window():
    probe = {
        "probe_tokens": ["80348"],
        "numeric_tokens": ["80348"],
        "fact_in_corpus": True,
        "missing_fact_tokens": [],
    }
    assert compute_testset_conflict("80,348（万元）", probe)["unit_missing"] is False
    assert compute_testset_conflict("80,348", probe)["unit_missing"] is True

    percent_probe = {
        "probe_tokens": ["42%"],
        "numeric_tokens": ["42%"],
        "fact_in_corpus": True,
        "missing_fact_tokens": [],
    }
    assert compute_testset_conflict("42%", percent_probe)["unit_missing"] is False


def test_unit_missing_ignores_text_tokens():
    probe = {
        "probe_tokens": ["80348", "营业收入"],
        "numeric_tokens": [],
        "fact_in_corpus": True,
        "missing_fact_tokens": ["80348"],
    }
    out = compute_testset_conflict("营业收入 80,348", probe)
    assert out["numeric_conflict"] is None
    assert out["unit_missing"] is None
