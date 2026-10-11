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


def test_embedding_dimension_mismatch_raises():
    """被绑定 embedding 模型声明维度 ≠ chunks.embedding 列维度 → 启动期 RuntimeError。

    伪造一个维度不同的 registry 条目触发（模型名不变、把 catalog 里的
    embedding_dimension 改掉），验证运行期守卫而非仅单测覆盖。
    """
    from dataclasses import replace

    spec = REG["dashscope"]
    model = spec.models["embedding"]
    bad_catalog = dict(spec.catalog)
    bad_catalog[model] = replace(spec.catalog[model], embedding_dimension=1536)
    bad_spec = replace(spec, catalog=bad_catalog)
    with pytest.raises(RuntimeError, match="需迁移列定义并重建向量"):
        validate_config(
            {"dashscope": bad_spec},
            {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "k"},
        )


def test_validate_logs_state_on():
    """校验开启时启动日志打印 state=on。"""
    from loguru import logger as _logger

    messages: list[str] = []
    sink_id = _logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        validate_config(REG, {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "k"})
    finally:
        _logger.remove(sink_id)
    assert any("[provider] validate state=on" in m for m in messages)


def test_validate_logs_state_off():
    """PROVIDER_VALIDATE=off 时跳过校验但仍打印 state=off。"""
    from loguru import logger as _logger

    messages: list[str] = []
    sink_id = _logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        validate_config(REG, {"PROVIDER_VALIDATE": "off"})
    finally:
        _logger.remove(sink_id)
    assert any("[provider] validate state=off" in m for m in messages)
