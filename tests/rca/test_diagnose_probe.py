"""检索探针：确定性变体与"表述问题 / 覆盖缺失"判定。"""

import pytest

from src.rca.diagnose.probe import (
    ProbeFacts,
    ProbeVariant,
    RetrievalProbe,
    build_variants,
)


def test_variants_drop_year_and_colloquialisms():
    variants = build_variants("请问腾讯2024年营业收入是多少")
    assert "腾讯营业收入" in variants
    assert all(v != "请问腾讯2024年营业收入是多少" for v in variants)
    assert len(variants) <= 3


def test_variants_unique_and_nonempty():
    assert build_variants("") == ()
    assert build_variants("营业收入") == ()


def test_verdict_rephrased_when_any_variant_scores_high():
    facts = ProbeFacts(
        (
            ProbeVariant("a", result_count=5, top_score=0.20),
            ProbeVariant("b", result_count=5, top_score=0.82),
        )
    )
    assert facts.verdict == "rephrased"


def test_verdict_missing_when_all_low():
    facts = ProbeFacts(
        (
            ProbeVariant("a", result_count=5, top_score=0.20),
            ProbeVariant("b", result_count=0, top_score=0.0),
        )
    )
    assert facts.verdict == "missing"


def test_verdict_unknown_without_variants():
    assert ProbeFacts(()).verdict == "unknown"


@pytest.mark.asyncio
async def test_probe_runs_each_variant():
    seen: list[str] = []

    async def score(query: str, kb_id: str):
        seen.append(query)
        return 3, (0.9, 0.1)

    probe = RetrievalProbe(score_fn=score)
    facts = await probe.run("请问腾讯2024年营业收入是多少", "kb1")
    assert seen
    assert len(facts.variants) == len(seen)
    assert facts.verdict == "rephrased"


@pytest.mark.asyncio
async def test_probe_skipped_without_kb():
    async def score(query: str, kb_id: str):  # pragma: no cover - 不应被调用
        raise AssertionError("should not search")

    facts = await RetrievalProbe(score_fn=score).run("q 2024", "")
    assert facts.variants == ()
