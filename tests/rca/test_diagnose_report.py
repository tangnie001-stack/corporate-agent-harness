"""报告校验：断言引用不存在即降级为未判定。"""

from src.rca.diagnose.models import DiagnosisReport, Evidence, RootCause
from src.rca.diagnose.report import validate


def _report(assertions):
    return DiagnosisReport(
        trace_id="t",
        root_cause=RootCause.NOT_CITED,
        evidence=(Evidence("E1", "trace", "retrieve_kb#1", "ok"),),
        assertions=assertions,
        next_actions=(),
        basis_rounds=0,
    )


def test_dangling_reference_degrades():
    out = validate(_report((("模型没有引用上下文", ("E9",)),)))
    assert out.root_cause is RootCause.UNDETERMINED
    assert out.assertions == ()
    assert out.evidence  # 证据保留，供人复核


def test_valid_reference_survives():
    out = validate(_report((("模型没有引用上下文", ("E1",)),)))
    assert out.root_cause is RootCause.NOT_CITED
    assert out.assertions


def test_assertion_without_any_reference_degrades():
    out = validate(_report((("模型答错了", ()),)))
    assert out.root_cause is RootCause.UNDETERMINED


def test_empty_assertions_untouched():
    """未判定报告本来就没有断言，不应被改写。"""
    rep = DiagnosisReport("t", RootCause.UNDETERMINED, (), (), ("补数据",), 0)
    assert validate(rep) is rep
