"""P1：出题上下文须前置【公司名 · 报告期】实体锚点（design D8）。

根因：出题 prompt 只收到 page_content + themes，而中文年报正文常自称
「本公司」、正文不出现公司全名 ⇒ 模型无实体可消解，产出不自足题面。
实体本就在分块元数据（company / report_period）里，只需前置进上下文。
"""


def test_header_uses_company_and_report_period():
    from src.cli.eval_ragas_generate import _context_header

    meta = {"company": "东软集团股份有限公司", "report_period": "2025年第一季度"}
    assert _context_header(meta) == "【东软集团股份有限公司 · 2025年第一季度】\n"


def test_header_period_falls_back_to_year_and_quarter():
    from src.cli.eval_ragas_generate import _context_header

    meta = {"company": "腾讯控股有限公司", "year": "2024", "quarter": "Q4"}
    assert _context_header(meta) == "【腾讯控股有限公司 · 2024Q4】\n"


def test_header_company_falls_back_to_source():
    from src.cli.eval_ragas_generate import _context_header

    meta = {"source": "neusoft_2025_q1.pdf", "report_period": "2025年第一季度"}
    assert _context_header(meta) == "【neusoft_2025_q1.pdf · 2025年第一季度】\n"


def test_header_partial_fields():
    from src.cli.eval_ragas_generate import _context_header

    assert _context_header({"company": "某公司"}) == "【某公司】\n"
    assert _context_header({"report_period": "2024年度"}) == "【2024年度】\n"


def test_header_empty_when_no_anchor():
    from src.cli.eval_ragas_generate import _context_header

    assert _context_header({}) == ""
    assert _context_header({"page": 1, "doc_id": "x"}) == ""


def test_header_strips_whitespace_and_handles_none():
    from src.cli.eval_ragas_generate import _context_header

    meta = {
        "company": "  东软  ",
        "report_period": None,
        "year": " 2025 ",
        "quarter": "",
    }
    assert _context_header(meta) == "【东软 · 2025】\n"


def test_page_content_is_prefixed_with_header():
    from src.cli.eval_ragas_generate import _context_header

    meta = {"company": "腾讯控股有限公司", "report_period": "2024年度"}
    page_content = _context_header(meta) + "本报告期营业收入……"
    assert page_content.startswith("【腾讯控股有限公司 · 2024年度】")
