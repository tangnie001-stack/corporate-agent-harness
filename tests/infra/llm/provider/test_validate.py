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
