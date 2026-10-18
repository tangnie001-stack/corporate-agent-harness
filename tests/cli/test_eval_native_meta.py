import json

from src.cli.eval_deepeval import build_report_meta


def test_report_meta_is_native_and_has_no_temperature_regime():
    meta = build_report_meta(
        judge_model="qwen3.8-flash",
        nan_stats={"nan_cells": 1, "denominator_cells": 76, "nan_rate": 1 / 76},
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
    )

    assert meta["caliber"] == "deep-eval-native"
    assert meta["runner_versions"] == {"deepeval": "4.2.9", "metrics": "native"}
    assert "temperature_regime" not in meta
    assert "temperature_control" not in meta
    assert "ragas" not in json.dumps(meta, ensure_ascii=False).lower()
    assert meta["model_arg_explicit"] is True


def test_report_meta_deepeval_only_has_no_nan_fields():
    meta = build_report_meta(
        judge_model="qwen3.8-flash",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
    )
    assert meta["caliber"] == "deepeval-only"
    assert meta["ragas_skipped"] is True
    assert "nan_cells" not in meta


def test_report_meta_carries_testset_version_in_both_branches():
    from src.cli.eval_deepeval import build_report_meta

    normal = build_report_meta(
        judge_model="qwen",
        nan_stats={"nan_cells": 0, "denominator_cells": 4, "nan_rate": 0.0},
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        testset_version=6,
    )
    skipped = build_report_meta(
        judge_model="qwen",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
        testset_version=6,
    )
    assert normal["testset_version"] == 6
    assert skipped["testset_version"] == 6


def test_markdown_report_renders_testset_version(tmp_path):
    from src.cli.eval_deepeval import build_report_meta, save_markdown_report

    meta = build_report_meta(
        judge_model="qwen",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
        testset_version=6,
    )
    out = tmp_path / "r.csv"
    save_markdown_report(
        None,
        questions=["Q0"],
        output_path=str(out),
        sample_classes=["pure_kb"],
        report_meta=meta,
        behaviour_rows=[{"q_index": 0, "input": "Q0", "metrics": {}}],
    )
    assert "Testset version:** 6" in (tmp_path / "r.md").read_text(encoding="utf-8")
