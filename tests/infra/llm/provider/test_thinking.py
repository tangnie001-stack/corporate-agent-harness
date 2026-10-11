from src.infra.llm.provider.spec import ProviderSpec, ThinkingStyle
from src.infra.llm.provider.thinking import thinking_extra_body


def _spec(style: ThinkingStyle) -> ProviderSpec:
    return ProviderSpec(
        name=style.value,
        base_url="http://x",
        api_key_env="K",
        thinking_style=style,
        reasoning_field="reasoning_content",
    )


def test_dashscope_on_includes_preserve_thinking():
    assert thinking_extra_body(_spec(ThinkingStyle.DASHSCOPE), True) == {
        "enable_thinking": True,
        "preserve_thinking": True,
    }


def test_dashscope_off_omits_preserve_thinking():
    assert thinking_extra_body(_spec(ThinkingStyle.DASHSCOPE), False) == {
        "enable_thinking": False,
    }


def test_deepseek_on_off():
    assert thinking_extra_body(_spec(ThinkingStyle.DEEPSEEK), True) == {
        "thinking": {"type": "enabled"},
    }
    assert thinking_extra_body(_spec(ThinkingStyle.DEEPSEEK), False) == {
        "thinking": {"type": "disabled"},
    }
