import sys

import pytest

from src.cli import eval_deepeval


def _parse(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["eval_deepeval", *argv])
    return eval_deepeval.parse_args()


def test_run_type_defaults_to_manual(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb"])
    assert args.run_type == "manual"
    assert args.session_id == "deepeval_eval_session"


def test_baseline_and_gate_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["eval_deepeval", "--kb-id", "kb", "--run-type", "baseline", "--gate"],
    )
    with pytest.raises(SystemExit) as excinfo:
        eval_deepeval.parse_args()
    assert excinfo.value.code == 2
