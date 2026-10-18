import sys

from src.cli.eval_deepeval import parse_args


def test_evidence_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["eval", "--kb-id", "kb", "--evidence"])
    args = parse_args()
    assert args.evidence is True
    assert args.evidence_all is False


def test_evidence_all_implies_evidence(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["eval", "--kb-id", "kb", "--evidence-all"])
    args = parse_args()
    assert args.evidence is True
    assert args.evidence_all is True
