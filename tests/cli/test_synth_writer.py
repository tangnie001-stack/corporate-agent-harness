"""v6 落盘：字段契约、原子写、覆盖保护、对账。"""

from __future__ import annotations

import json

import pytest
from deepeval.dataset import Golden

from src.cli.synth_contexts import ContextGroup
from src.cli.synth_writer import build_metadata, golden_to_sample, write_testset
from src.infra.db.vector_store.types import ChunkResult


def _group() -> ContextGroup:
    """构造单组上下文。"""
    chunk = ChunkResult(
        id="d1:0",
        content="东软集团2025年第一季度营业收入为18.5亿元。",
        metadata={"doc_id": "d1", "source": "a.pdf", "chunk_index": 0},
    )
    return ContextGroup(
        chunks=[chunk],
        source="a.pdf",
        doc_id="d1",
        company="东软集团股份有限公司",
        period="2025年第一季度",
        aliases={"东软集团股份有限公司", "东软"},
    )


def test_golden_to_sample_has_all_contract_fields():
    golden = Golden(  # type: ignore[reportCallIssue]
        input="东软集团2025年第一季度营业收入是多少？",
        expected_output="东软集团2025年第一季度营业收入为18.5亿元。",
        context=["正文"],
        additional_metadata={
            "evolutions": ["Multi-context"],
            "synthetic_input_quality": 0.9,
        },
    )
    sample = golden_to_sample(golden, _group())
    for key in (
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
    ):
        assert key in sample
    assert sample["expected_tools"] == ["retrieve_kb"]
    assert sample["hop_type"] == "multi_hop"
    assert sample["hop_basis"] == "evolution"
    assert sample["numeric_risk"] is True
    assert sample["synth_quality"] == 0.9
    assert sample["reference_contexts"][0].startswith("【")


def test_retrieval_context_none_does_not_break_conversion():
    golden = Golden(  # type: ignore[reportCallIssue]
        input="东软集团2025年第一季度营业收入是多少？",
        expected_output="东软集团2025年第一季度营业收入为18.5亿元。",
        context=["正文"],
    )
    assert golden.retrieval_context is None
    sample = golden_to_sample(golden, _group())
    assert sample["reference_contexts"]


def test_write_is_atomic_and_creates_parent(tmp_path):
    path = tmp_path / "testset_d1_v6.json"
    write_testset(path, {"samples": []})
    assert json.loads(path.read_text(encoding="utf-8")) == {"samples": []}
    assert not path.with_suffix(".json.tmp").exists()


def test_existing_file_requires_force(tmp_path):
    path = tmp_path / "testset_d1_v6.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_testset(path, {"samples": []})
    write_testset(path, {"samples": [1]}, force=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"samples": [1]}


def test_metadata_reconciles(tmp_path):
    meta = build_metadata(
        kb_id="kb",
        kb_name="test",
        version=6,
        samples=[{"user_input": "a"}, {"user_input": "b"}],
        stats={
            "generated": 3,
            "kept": 2,
            "language_checked": 2,
            "self_contained_rewritten": 1,
            "failed_contexts": 1,
            "dropped": {
                "self_contained": 0,
                "not_complete_sentence": 1,
                "language": 0,
                "duplicate": 0,
                "hop_ratio": 0,
            },
        },
        params={"goldens_per_context": 1},
        doc_names=["a.pdf"],
        doc_ids=["d1"],
        source_chunks=2,
        covered_chunks=2,
        company_source={"a.pdf": "metadata"},
        hop_stats={"single_hop": 2, "multi_hop": 0},
        elapsed_seconds=1.5,
    )
    assert meta["testset_size"] == 2
    assert (
        meta["gate_stats"]["generated"] - sum(meta["gate_stats"]["dropped"].values())
        == meta["testset_size"]
    )
