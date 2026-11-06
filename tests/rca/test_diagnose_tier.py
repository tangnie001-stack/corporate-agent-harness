"""档位与归属：与根因分开计算（D4）。"""

from src.rca.diagnose.models import DiagnosisKind, Owner, RootCause
from src.rca.diagnose.tier import assess
from src.rca.models import Tier


def test_5xx_is_dev_action():
    tier, owner = assess(DiagnosisKind.ERROR_5XX, RootCause.UNDETERMINED)
    assert tier is Tier.NEED_ACTION
    assert owner is Owner.DEV


def test_4xx_is_ops_watch():
    tier, owner = assess(DiagnosisKind.ERROR_4XX, RootCause.UNDETERMINED)
    assert tier is Tier.WATCH
    assert owner is Owner.OPS


def test_answer_with_cause_is_kb_action():
    tier, owner = assess(DiagnosisKind.ANSWER, RootCause.LOW_RELEVANCE)
    assert tier is Tier.NEED_ACTION
    assert owner is Owner.KB


def test_answer_undetermined_is_ops_watch():
    tier, owner = assess(DiagnosisKind.ANSWER, RootCause.UNDETERMINED)
    assert tier is Tier.WATCH
    assert owner is Owner.OPS
