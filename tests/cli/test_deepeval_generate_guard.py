"""`--generate` 守卫：出题入口已下线，命中即报错并以退出码 1 结束。"""

from __future__ import annotations

import sys

import pytest

from src.cli import eval_ragas


def test_generate_flag_exits_with_migration_hint(monkeypatch, capsys):
    """`--generate` 不再出题：打印前迁路径后退出 1，不得静默或抛 ImportError。

    守卫位于加载测试集之前，故只需替换 argv，无需 mock 外部依赖。
    模块名在 Task 7 改名为 eval_deepeval，行为契约不变。
    """
    monkeypatch.setattr(sys, "argv", ["eval_deepeval", "--kb-id", "kb-x", "--generate"])

    with pytest.raises(SystemExit) as excinfo:
        eval_ragas.main()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Synthesizer" in out
    assert "deepeval-synthesizer-testset" in out
