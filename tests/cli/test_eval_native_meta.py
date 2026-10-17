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
