"""出题编排：单 context 失败隔离、进度复用、Golden 字段映射。"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from typing import cast

import pytest
from deepeval.dataset import Golden

from src.cli import testset_synth
from src.cli.synth_contexts import ContextGroup


@dataclass
class _FakeGroup:
    company: str = "东软集团股份有限公司"
    period: str = "2025年第一季度"
    source: str = "neusoft_2025_q1.pdf"
    doc_id: str = "d1"
    aliases: set[str] = field(default_factory=lambda: {"东软集团股份有限公司"})
    company_source: str = "metadata"
    chunks: list = field(default_factory=list)


class _FakeSynth:
    """一个 context 抛 ValueError，另一个正常返回 Golden。"""

    def __init__(self) -> None:
        self.calls = 0

    async def a_generate_goldens_from_contexts(self, contexts, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ValueError("bad json")
        return [
            Golden(  # type: ignore[reportCallIssue]
                input="东软集团2025年第一季度营业收入是多少？",
                expected_output="东软集团2025年第一季度营业收入为人民币18.5亿元。",
                context=contexts[0],
                additional_metadata={"evolutions": ["Concretizing"]},
            )
        ]


def test_one_context_failure_does_not_drop_others():
    contexts = cast(list[ContextGroup], [_FakeGroup(), _FakeGroup()])
    pairs, failed, last_error = asyncio.run(
        testset_synth._synthesize(contexts, _FakeSynth(), goldens_per_context=1)
    )
    assert failed == 1
    assert "bad json" in last_error
    assert len(pairs) == 1
    golden, group = pairs[0]
    assert golden.input.startswith("东软集团")
    assert group.company == "东软集团股份有限公司"


def test_all_contexts_failed_returns_zero_pairs():
    class _AllFail:
        async def a_generate_goldens_from_contexts(self, contexts, **kwargs):
            raise RuntimeError("boom")

    pairs, failed, last_error = asyncio.run(
        testset_synth._synthesize(
            cast(list[ContextGroup], [_FakeGroup()]),
            _AllFail(),
            goldens_per_context=1,
        )
    )
    assert pairs == []
    assert failed == 1
    assert "boom" in last_error


def test_synthesizer_uses_four_concurrency_and_configured_goldens(monkeypatch):
    captured: dict = {}

    class _FakeSynthesizer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeJudge:
        model_name = "fake-judge"

    monkeypatch.setattr(testset_synth, "Synthesizer", _FakeSynthesizer)
    testset_synth._build_synthesizer(_FakeJudge())
    assert captured["max_concurrent"] == 4
    assert captured["async_mode"] is True
    assert captured["styling_config"] is not None
    assert captured["evolution_config"] is not None
    assert captured["filtration_config"] is not None


def test_async_signature_has_no_send_data():
    import inspect

    from deepeval.synthesizer import Synthesizer

    params = inspect.signature(Synthesizer.a_generate_goldens_from_contexts).parameters
    assert "_send_data" not in params
    assert "_progress" in params
    assert "_pbar_id" in params


def test_explicit_version_requires_docs(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["testset_synth", "--kb-id", "kb", "--version", "6"]
    )
    with pytest.raises(SystemExit) as excinfo:
        testset_synth.parse_args()
    assert excinfo.value.code == 2
    assert "--docs" in capsys.readouterr().err


def test_docs_without_version_uses_next_version(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["testset_synth", "--kb-id", "kb", "--docs", "a.pdf"]
    )
    args = testset_synth.parse_args()
    assert args.version is None
    assert args.docs == ["a.pdf"]
