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


def test_report_meta_without_ragas_marks_skipped():
    from src.cli.eval_ragas import build_report_meta

    meta = build_report_meta(
        judge_model="qwen-test",
        nan_stats=None,
        runner_versions={"ragas": "0.3.9", "deepeval": "4.2.9"},
        ragas_enabled=False,
    )
    assert meta["ragas_skipped"] is True
    assert meta["caliber"] == "deepeval-only"
    assert "nan_cells" not in meta
    assert "temperature_regime" not in meta


def test_markdown_report_without_ragas_has_no_metric_rows(tmp_path):
    from src.cli.eval_ragas import build_report_meta, save_markdown_report

    out = tmp_path / "r.csv"
    save_markdown_report(
        None,
        questions=["Q0"],
        output_path=str(out),
        sample_classes=["pure_kb"],
        report_meta=build_report_meta(
            judge_model="qwen-test",
            nan_stats=None,
            runner_versions={"ragas": "0.3.9", "deepeval": "4.2.9"},
            ragas_enabled=False,
        ),
        behaviour_rows=[
            {"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}}
        ],
    )
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "RAGAS:** 未跑" in text
    assert "Main scores (pure-KB)" not in text
    assert "faithfulness=" not in text
    assert "Agent 行为轴" in text
