"""P2 出题后自足性门（design D8）的纯函数单测。"""

from src.cli.eval_self_contained import (
    doc_exclusive_aliases,
    enforce_self_contained,
    header_entities,
)

HDR_TENCENT = "【tencent · 2024】"
HDR_NEUSOFT = "【东软集团股份有限公司 · 2025年第一季度】"


def _aliases() -> dict[str, set[str]]:
    # tencent 与 neusoft 各自专属词；人民币/2024 跨文档故应被排除
    return doc_exclusive_aliases(
        [
            ("tencent", ["tencent", "Tencent", "腾讯", "人民币", "2024"]),
            (
                "东软集团股份有限公司",
                ["东软集团股份有限公司", "东软集团", "Neusoft Group", "人民币", "2024"],
            ),
        ]
    )


def test_header_entities_parses_and_dedups():
    sample = {
        "reference_contexts": [
            "<1-hop>\n\n" + HDR_NEUSOFT + "\n正文",
            "<2-hop>\n\n" + HDR_NEUSOFT + "\n正文",
        ]
    }
    assert header_entities(sample) == [("东软集团股份有限公司", "2025年第一季度")]


def test_header_entities_empty_when_no_header():
    assert header_entities({"reference_contexts": ["<1-hop>\n\n正文"]}) == []


def test_doc_exclusive_aliases_excludes_cross_doc_and_noise():
    al = _aliases()
    assert "腾讯" in al["tencent"]
    assert "tencent" in al["tencent"]
    assert "东软集团" in al["东软集团股份有限公司"]
    # 跨文档通用词 / 纯数字不得成为别名（否则任意题都会被误判自足）
    assert "人民币" not in al["tencent"]
    assert "2024" not in al["tencent"]


def test_kept_when_question_names_company():
    samples = [
        {
            "user_input": "东软集团2025年第一季度营业收入是多少？",
            "reference_contexts": [HDR_NEUSOFT],
        }
    ]
    kept, dropped = enforce_self_contained(samples, _aliases())
    assert len(kept) == 1 and dropped == []
    assert kept[0]["user_input"] == samples[0]["user_input"]


def test_kept_when_question_names_company_via_cross_language_alias():
    # 头是 tencent，题面用中文「腾讯」——别名应识别
    samples = [
        {"user_input": "腾讯2024年总收入是多少？", "reference_contexts": [HDR_TENCENT]}
    ]
    kept, dropped = enforce_self_contained(samples, _aliases())
    assert len(kept) == 1 and dropped == []


def test_rewrites_when_single_company_not_named():
    samples = [{"user_input": "营业收入是多少？", "reference_contexts": [HDR_NEUSOFT]}]
    kept, dropped = enforce_self_contained(samples, _aliases())
    assert dropped == []
    assert len(kept) == 1
    assert "东软集团股份有限公司" in kept[0]["user_input"]
    assert kept[0]["user_input"].endswith("营业收入是多少？")
    # 不得就地改动原样本
    assert kept[0] is not samples[0]


def test_drops_when_multiple_companies_without_naming():
    samples = [
        {
            "user_input": "净利润下降的主要原因是什么？",
            "reference_contexts": [HDR_TENCENT, HDR_NEUSOFT],
        }
    ]
    kept, dropped = enforce_self_contained(samples, _aliases())
    assert kept == []
    assert len(dropped) == 1 and "多公司" in dropped[0][1]


def test_drops_when_no_anchor():
    samples = [
        {"user_input": "本公司本期净利润是多少？", "reference_contexts": ["无头部正文"]}
    ]
    kept, dropped = enforce_self_contained(samples, _aliases())
    assert kept == []
    assert len(dropped) == 1


def test_build_company_aliases_reads_kg_nodes():
    """KG 适配层：从节点 document_metadata.company + entities/themes 汇总别名。"""
    from src.cli.eval_ragas_generate import _build_company_aliases

    class _Node:
        def __init__(self, props):
            self.properties = props

    class _KG:
        def __init__(self, nodes):
            self.nodes = nodes

    kg = _KG(
        [
            _Node(
                {
                    "document_metadata": {"company": "tencent"},
                    "entities": ["tencent", "腾讯", "人民币"],
                    "themes": ["Tencent"],
                }
            ),
            _Node(
                {
                    "document_metadata": {"company": "东软集团股份有限公司"},
                    "entities": ["东软集团", "人民币"],
                    "themes": ["东软集团股份有限公司"],
                }
            ),
            _Node({"document_metadata": {}, "entities": ["无主"], "themes": []}),
        ]
    )
    aliases = _build_company_aliases(kg)
    assert "腾讯" in aliases["tencent"]
    assert "东软集团" in aliases["东软集团股份有限公司"]
    assert "人民币" not in aliases["tencent"]
