"""`--deepeval-only` 的参数与互斥语义。"""

from __future__ import annotations

import sys

import pytest

from src.cli import eval_ragas


def _parse(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["eval_ragas", *argv])
    return eval_ragas.parse_args()


def test_deepeval_only_defaults_to_false(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb-x"])
    assert args.deepeval_only is False


def test_deepeval_only_flag_is_accepted(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb-x", "--deepeval-only"])
    assert args.deepeval_only is True


def test_deepeval_only_and_gate_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["eval_ragas", "--kb-id", "kb-x", "--deepeval-only", "--gate"]
    )
    with pytest.raises(SystemExit) as excinfo:
        eval_ragas.parse_args()
    assert excinfo.value.code == 2
