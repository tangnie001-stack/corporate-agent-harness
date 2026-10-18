"""v6 质量门：语言、完整句、自足、去重、多跳。"""

from __future__ import annotations

from src.cli.synth_gates import (
    check_complete_sentence,
    check_language,
    compute_numeric_risk,
    extract_context_entities,
    run_gates,
    strip_context_entities,
)


def _sample(
    question: str,
    reference: str,
    *,
    hop: str = "single_hop",
    contexts: list[str] | None = None,
) -> dict:
    """构造最小 sample。"""
    return {
        "user_input": question,
        "reference": reference,
        "reference_contexts": contexts
        or ["【东软集团股份有限公司 · 2025年第一季度】\n正文"],
        "hop_type": hop,
        "evolutions": ["Multi-context"] if hop == "multi_hop" else ["Concretizing"],
        "_company": "东软集团股份有限公司",
        "_entities": {"SAP SE", "Tencent", "东软集团股份有限公司"},
        "_original_user_input": question,
    }


def test_pure_english_is_rejected():
    assert not check_language(
        "What was the revenue?", "Revenue was 18.5 billion.", set()
    )


def test_mixed_sentence_is_rejected():
    assert not check_language(
        "what did the 经审核的财务报表 show", "东软集团营业收入为18.5亿元。", set()
    )


def test_parenthetical_name_is_tolerated():
    assert check_language(
        "腾讯控股有限公司（Tencent Holdings Limited）2024年营业收入是多少？",
        "腾讯控股有限公司2024年营业收入为人民币6,603亿元。",
        {"Tencent Holdings Limited"},
    )


def test_latin_entity_adjacent_to_cjk_is_tolerated():
    assert check_language(
        "东软集团2025年第一季度报告中SAP SE的持股比例是多少？",
        "东软集团2025年第一季度报告中SAP SE持股比例为1.2%。",
        {"SAP SE"},
    )


def test_zero_letter_reference_is_rejected():
    assert not check_language("东软集团2025年营业收入？", "18.5%。", set())


def test_year_is_not_the_numeric_fact():
    assert check_complete_sentence(
        "腾讯2024年营业收入为人民币18.5亿元。",
        numeric_risk=True,
        subject_aliases={"腾讯"},
    )


def test_date_is_not_the_numeric_fact():
    assert check_complete_sentence(
        "2024年12月31日，公司实现营业收入18.5亿元。", numeric_risk=True
    )


def test_metric_without_subject_is_rejected():
    assert not check_complete_sentence("营业收入18.5亿元。", numeric_risk=True)


def test_bare_number_is_rejected_and_attributed_to_sentence_gate():
    samples = [_sample("东软集团2025年营业收入？", "1,234,567.89")]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    assert kept == []
    assert stats["dropped"]["not_complete_sentence"] == 1
    assert stats["dropped"]["language"] == 0


def test_numeric_risk_flag():
    assert compute_numeric_risk("营业收入为人民币18.5亿元。")
    assert not compute_numeric_risk("公司董事长保证财务信息真实。")


def test_duplicate_is_dropped():
    samples = [
        _sample("东软集团2025年营业收入？", "东软集团2025年营业收入为18.5亿元。"),
        _sample("东软集团2025年营业收入?", "东软集团2025年营业收入为18.5亿元。"),
    ]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    assert len(kept) == 1
    assert stats["dropped"]["duplicate"] == 1


def test_hop_ratio_is_enforced_by_hash_not_order():
    samples = [
        _sample(
            f"东软集团2025年问题{i}？",
            f"东软集团2025年营业收入为{i}亿元。",
            hop="multi_hop",
        )
        for i in range(4)
    ] + [
        _sample(f"东软集团2025年单跳{i}？", f"东软集团2025年净利润为{i}亿元。")
        for i in range(2)
    ]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    multi = [s for s in kept if s["hop_type"] == "multi_hop"]
    assert len(multi) <= 2
    assert stats["dropped"]["hop_ratio"] == 2


def test_self_contained_rewrite_is_counted():
    samples = [
        _sample(
            "2025年第一季度营业收入是多少？",
            "东软集团2025年第一季度营业收入为18.5亿元。",
        )
    ]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    assert stats["self_contained_rewritten"] == 1
    assert kept[0]["user_input"].startswith("东软集团股份有限公司")


def test_context_entities_extracted_per_word():
    entities = extract_context_entities(["报告提到 SAP SE 和 Tencent Holdings Limited"])
    assert {"SAP", "SE", "Tencent", "Holdings", "Limited"} <= entities


def test_strip_context_entities_removes_each_word():
    stripped = strip_context_entities("报告中SAP SE的持股比例", {"SAP", "SE"})
    assert "SAP" not in stripped
    assert "SE" not in stripped


def test_pure_english_sentence_rejected_even_if_entities_known():
    entities = {"SAP", "SE", "Tencent", "Holdings", "Limited"}
    assert not check_language(
        "What did Tencent Holdings Limited report?",
        "Revenue was 18.5 billion.",
        entities,
    )


def test_run_gates_reconciles_generated_and_dropped():
    samples = [
        _sample("东软集团2025年营业收入？", "东软集团2025年营业收入为18.5亿元。"),
        _sample("东软集团2025年净利润？", "1,234,567.89"),
    ]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    assert stats["generated"] - sum(stats["dropped"].values()) == len(kept)
