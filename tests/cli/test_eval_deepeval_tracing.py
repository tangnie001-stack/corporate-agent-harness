"""CLI 侧接线契约（D6 / D2 的 Q6 决定）。"""

import inspect

from src.cli import eval_deepeval


def test_single_question_helper_is_observed():
    """每个问题必须由被 @observe 装饰的独立函数承载（每问一条 trace）。"""
    assert hasattr(eval_deepeval, "_answer_one_question")
    src = inspect.getsource(eval_deepeval._answer_one_question)
    assert "eval_question" in src


def test_cli_configures_and_flushes_tracing():
    """CLI 不经 lifespan，必须自己 configure 与 flush。"""
    src = inspect.getsource(eval_deepeval)
    assert "configure_tracing()" in src
    assert "flush_tracing()" in src
