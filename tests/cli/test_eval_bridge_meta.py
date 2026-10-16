import json

from src.cli.eval_ragas import build_report_meta


def test_report_meta_records_temperature_regime_but_never_zero():
    meta = build_report_meta(
        judge_model="qwen3.8-flash",
        nan_stats={"nan_cells": 1, "denominator_cells": 76, "nan_rate": 1 / 76},
        runner_versions={"ragas": "0.3.9", "deepeval": "4.2.9"},
    )

    assert meta["caliber"] == "bridge-0.3.9"
    assert meta["temperature_regime"] == {"n1_metrics": 0.01, "answer_relevancy": 0.3}
    assert meta["model_arg_explicit"] is True
    assert meta["judge_model"] == "qwen3.8-flash"
    assert "temperature=0" not in json.dumps(meta, ensure_ascii=False)
