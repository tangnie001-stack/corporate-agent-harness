from __future__ import annotations

import pytest

from src.core.eval_evidence import (
    EvidenceRun,
    current_evidence,
    evidence_scope,
    parent_key,
)


def test_new_question_keeps_index_and_trace():
    run = EvidenceRun(run_id="r1", run_trace_id="eval_run_r1_abcd1234", enabled=True)
    collector = run.new_question(2, "eval_t2")
    assert collector.q_index == 2
    assert collector.trace_id == "eval_t2"
    assert run.collectors[2] is collector


def test_scope_restores_previous_collector():
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    first = run.new_question(0)
    second = run.new_question(1)
    token = current_evidence.set(first)
    try:
        with evidence_scope(second):
            assert current_evidence.get() is second
        assert current_evidence.get() is first
    finally:
        current_evidence.reset(token)


def test_scope_restores_on_exception():
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    collector = run.new_question(0)
    token = current_evidence.set(None)
    try:
        with pytest.raises(RuntimeError), evidence_scope(collector):
            raise RuntimeError("boom")
        assert current_evidence.get() is None
    finally:
        current_evidence.reset(token)


def test_parent_key_falls_back_to_chunk_id():
    assert parent_key("d1", "d1:3", None) == ("d1", "d1:3")
    assert parent_key("d1", "d1:3", "") == ("d1", "d1:3")
    key = parent_key("d1", "d1:3", "parent text")
    assert key[0] == "d1" and key[1] != "d1:3"
