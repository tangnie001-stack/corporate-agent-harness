import pytest

from src.infra.llm.provider.spec import load_registry, validate_config

REG = load_registry()


def test_missing_key_raises():
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        validate_config(REG, {"LLM_PROVIDER": "dashscope"})


def test_ok_with_key():
    validate_config(REG, {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "k"})


def test_unknown_provider_raises():
    with pytest.raises(RuntimeError, match="未知 provider"):
        validate_config(REG, {"LLM_PROVIDER": "nope", "DASHSCOPE_API_KEY": "k"})


def test_unimplemented_thinking_style_raises():
    """被绑定 provider 的 thinking_style 无已实现分支 → 启动期 RuntimeError（fail-fast）。

    模拟「新增 ThinkingStyle 枚举成员却漏加 thinking_extra_body 分支」：否则该缺陷要等到
    请求期首次构造/调用才以 ValueError 暴露，本守卫把它前移到启动期。
    """
    from dataclasses import replace

    bogus = replace(REG["dashscope"], thinking_style="unimplemented")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="无已实现分支"):
        validate_config(
            {"dashscope": bogus},
            {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "k"},
        )


def test_ok_when_thinking_style_implemented():
    """有已实现分支（dashscope）时守卫放行。"""
    validate_config(REG, {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "k"})
