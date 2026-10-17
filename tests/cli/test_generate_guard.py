"""`--generate` 的硬护栏：显式失败 + 前迁指引，不得撞裸 ImportError（design D8）。"""

import inspect
import sys

import pytest

from src.cli import eval_ragas


def test_generate_path_fails_with_forward_path(monkeypatch, capsys):
    """--generate 必须以明确错误信息退出，给出原因与前迁路径（DeepEval Synthesizer）。"""
    monkeypatch.setattr(sys, "argv", ["eval_ragas", "--kb-id", "kb-x", "--generate"])
    with pytest.raises(SystemExit) as excinfo:
        eval_ragas.main()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "0.4.x" in out
    assert "ragas==0.3.9" in out
    assert "Synthesizer" in out
    assert "ImportError" not in out


def test_evaluation_entry_no_longer_references_run_generate():
    """评测入口不得再引用出题生成入口 run_generate（tasks 6.1）。"""
    src = inspect.getsource(eval_ragas)
    assert "run_generate" not in src


def test_testset_loader_and_vertexai_stub_stay_referenced():
    """测试集加载器与 vertexai stub 仍被评测主流程使用，SHALL NOT 一并移除（tasks 6.1）。"""
    src = inspect.getsource(eval_ragas)
    assert "_load_latest_testset" in src
    assert "_ensure_vertexai_stub" in src
