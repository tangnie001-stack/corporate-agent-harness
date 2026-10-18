"""`--deepeval-only`（别名 `--behaviour-only`）的参数与互斥语义。"""

from __future__ import annotations

import sys

import pytest

from src.cli import eval_deepeval


def _parse(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["eval_deepeval", *argv])
    return eval_deepeval.parse_args()


def test_deepeval_only_defaults_to_false(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb-x"])
    assert args.deepeval_only is False


def test_deepeval_only_flag_is_accepted(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb-x", "--deepeval-only"])
    assert args.deepeval_only is True


def test_behaviour_only_is_an_alias(monkeypatch):
    args = _parse(monkeypatch, ["--kb-id", "kb-x", "--behaviour-only"])
    assert args.deepeval_only is True


def test_deepeval_only_and_gate_are_mutually_exclusive(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["eval_deepeval", "--kb-id", "kb-x", "--deepeval-only", "--gate"]
    )
    with pytest.raises(SystemExit) as excinfo:
        eval_deepeval.parse_args()
    assert excinfo.value.code == 2
    # argparse 的未知参数同样是 exit 2；须确认是互斥校验本身给出的错误
    assert "不能同用" in capsys.readouterr().err


def test_report_meta_without_ragas_marks_skipped():
    from src.cli.eval_deepeval import build_report_meta

    meta = build_report_meta(
        judge_model="qwen-test",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
    )
    assert meta["ragas_skipped"] is True
    assert meta["caliber"] == "deepeval-only"
    assert "nan_cells" not in meta
    assert "temperature_regime" not in meta


def test_markdown_report_without_ragas_has_no_metric_rows(tmp_path):
    from src.cli.eval_deepeval import build_report_meta, save_markdown_report

    out = tmp_path / "r.csv"
    save_markdown_report(
        None,
        questions=["Q0"],
        output_path=str(out),
        sample_classes=["pure_kb"],
        report_meta=build_report_meta(
            judge_model="qwen-test",
            nan_stats=None,
            runner_versions={"deepeval": "4.2.9", "metrics": "native"},
            ragas_enabled=False,
        ),
        behaviour_rows=[
            {"q_index": 0, "input": "Q0", "metrics": {"Task Completion": 1.0}}
        ],
    )
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "Main score:** 未跑" in text
    assert "Main scores (pure-KB)" not in text
    assert "faithfulness=" not in text
    assert "Agent 行为轴" in text
    assert "工具选择：未跑（--deepeval-only）" in text


def test_deepeval_only_skips_all_ragas_side_effects(monkeypatch, tmp_path):
    """--deepeval-only 下四指标链路（构造/评分/CSV/落库）一次都不应发生."""
    from types import SimpleNamespace

    called: list[str] = []

    def _bomb(name):
        def _fn(*args, **kwargs):
            called.append(name)
            raise AssertionError(f"{name} 不应在 --deepeval-only 下被调用")

        return _fn

    # 模块级 import 的符号：直接打在 eval_deepeval 命名空间上
    monkeypatch.setattr(eval_deepeval, "build_metrics", _bomb("build_metrics"))
    monkeypatch.setattr(eval_deepeval, "run_evaluation", _bomb("run_evaluation"))
    monkeypatch.setattr(eval_deepeval, "_save_eval_report", _bomb("_save_eval_report"))
    monkeypatch.setattr(eval_deepeval, "save_results_csv", _bomb("save_results_csv"))

    # main() 函数体内的 import 必须打在源模块上
    monkeypatch.setattr(
        "src.cli.testset_io.resolve_testset_version", lambda kb_id, version: 5
    )
    monkeypatch.setattr(
        "src.cli.testset_io._load_latest_testset",
        lambda kb_id, version=None: (["q"], ["a"], [["retrieve_kb"]]),
    )
    monkeypatch.setattr(
        "src.agents.graph.workflow.build_graph", lambda *a, **k: object()
    )
    monkeypatch.setattr("src.infra.llm.prompt_manager.PromptManager", lambda: object())
    monkeypatch.setattr("src.models.get_llm", lambda: object())
    monkeypatch.setattr("src.models.get_rerank", lambda: object())
    monkeypatch.setattr(
        "src.infra.llm.provider.deepeval_llm.BridgeDeepEvalLLM",
        lambda chat: object(),
    )
    monkeypatch.setattr(
        "src.infra.llm.provider.factory.build_bridge_judge",
        lambda **kw: SimpleNamespace(model_name="fake-judge"),
    )

    class _FakeHookEvidence:
        """hooks 断言要求 events 内含 response 记录。"""

        def __init__(self) -> None:
            self.events: list[dict] = [{"phase": "response"}]

        def set_run(self, run_trace_id, judge_model=None):
            self.run_trace_id = run_trace_id
            self.judge_model = judge_model

    monkeypatch.setattr(
        "src.infra.llm.provider.http_observability.HookEvidence", _FakeHookEvidence
    )

    class _FakeVectorStore:
        async def get_all_chunks(self, kb_id: str) -> list[dict]:
            return [{"chunk_id": "c1"}]

    monkeypatch.setattr("src.infra.db.vector_store.VectorStore", _FakeVectorStore)

    async def _run_and_dispose(coro):
        return await coro

    monkeypatch.setattr("src.infra.db.engine.run_and_dispose", _run_and_dispose)

    # 其余模块级符号（行为轴链路）全部就地打桩
    monkeypatch.setattr(eval_deepeval, "configure_tracing", lambda: None)
    monkeypatch.setattr(eval_deepeval, "flush_tracing", lambda: None)
    monkeypatch.setattr(eval_deepeval, "build_behaviour_metrics", lambda llm: [])

    async def _fake_generate(*args, **kwargs):
        return (["answer"], [["ctx"]], ["trace-1"], [[]], ["pure_kb"], [{}], [])

    monkeypatch.setattr(
        eval_deepeval, "generate_answers_and_contexts_with_behaviour", _fake_generate
    )
    monkeypatch.setattr(
        eval_deepeval,
        "read_behaviour_scores",
        lambda out: [{"q_index": 0, "input": "q", "metrics": {"x": 1.0}}],
    )
    monkeypatch.setattr(
        eval_deepeval, "assert_behaviour_scores_complete", lambda *a, **k: None
    )

    out_csv = tmp_path / "r.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eval_deepeval",
            "--kb-id",
            "kb-x",
            "--testset-version",
            "5",
            "--deepeval-only",
            "--limit",
            "1",
            "--output",
            str(out_csv),
        ],
    )

    eval_deepeval.main()

    assert called == []
    out_md = tmp_path / "r.md"
    assert out_md.exists()
    assert "Main score:** 未跑" in out_md.read_text(encoding="utf-8")
    assert not out_csv.exists()
