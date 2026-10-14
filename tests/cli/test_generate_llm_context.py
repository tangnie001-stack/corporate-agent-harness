"""llm_context 必须传给 TestsetGenerator，常量须含公司/报告期要求。"""


def test_self_contained_hint_mentions_company_and_period():
    from src.config.const import RAGAS_SELF_CONTAINED_HINT

    assert "公司" in RAGAS_SELF_CONTAINED_HINT
    assert "报告期" in RAGAS_SELF_CONTAINED_HINT
    assert "company" in RAGAS_SELF_CONTAINED_HINT.lower()


def test_generator_receives_llm_context(monkeypatch):
    from src.config.const import RAGAS_SELF_CONTAINED_HINT

    captured = {}

    class _Gen:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("ragas.testset.synthesizers.generate.TestsetGenerator", _Gen)
    from src.cli import eval_ragas_generate

    eval_ragas_generate._make_generator(llm=object(), embeddings=object())

    assert captured["llm_context"] == RAGAS_SELF_CONTAINED_HINT
