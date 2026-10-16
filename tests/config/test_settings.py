"""Tests for configuration settings."""

import os
from importlib import reload
from unittest.mock import patch

import pytest

import src.config.settings as config


def test_langfuse_secret_key_defaults_to_empty():
    """LANGFUSE_SECRET_KEY 内置默认值为空串（code default，不假装有值）。"""
    assert hasattr(config, "LANGFUSE_SECRET_KEY")
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("LANGFUSE_SECRET_KEY", None)
        reloaded = reload(_s)
        assert reloaded.LANGFUSE_SECRET_KEY == ""


def test_langfuse_public_key_defaults_to_empty():
    """LANGFUSE_PUBLIC_KEY 内置默认值为空串（code default，不假装有值）。"""
    assert hasattr(config, "LANGFUSE_PUBLIC_KEY")
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
        reloaded = reload(_s)
        assert reloaded.LANGFUSE_PUBLIC_KEY == ""


def test_langfuse_host_default():
    """LANGFUSE_HOST 内置默认值指向 compose 中真实存在的服务名 langfuse-web。"""
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("LANGFUSE_HOST", None)
        reloaded = reload(_s)
        assert reloaded.LANGFUSE_HOST == "http://langfuse-web:3000"


def test_langfuse_enable_default_true():
    """LANGFUSE_ENABLE defaults to True."""
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("LANGFUSE_ENABLE", None)
        reloaded = reload(_s)
        assert reloaded.LANGFUSE_ENABLE is True


def test_langfuse_host_override_from_env():
    """LANGFUSE_HOST can be overridden via environment variable."""
    import src.config.settings

    with patch.dict(
        os.environ, {"LANGFUSE_HOST": "http://localhost:3000"}, clear=False
    ):
        reloaded = reload(src.config.settings)
        assert reloaded.LANGFUSE_HOST == "http://localhost:3000"


def test_entity_llm_fallback_default_auto():
    """ENTITY_LLM_FALLBACK defaults to 'auto' (三态开关默认值)."""
    from src.config.settings import ENTITY_LLM_FALLBACK

    assert ENTITY_LLM_FALLBACK in ("off", "on", "auto")
    assert ENTITY_LLM_FALLBACK == "auto"


def test_entity_text_prefix_len_default():
    """ENTITY_TEXT_PREFIX_LEN defaults to 600（正文前缀字符数）."""
    from src.config.settings import ENTITY_TEXT_PREFIX_LEN

    assert ENTITY_TEXT_PREFIX_LEN == 600


def test_margin_constants_split():
    """HEADER_MARGIN/FOOTER_MARGIN 替换 HEADER_FOOTER_MARGIN（默认 45/80）。"""
    from src.config import FOOTER_MARGIN, HEADER_MARGIN

    assert HEADER_MARGIN == 45
    assert FOOTER_MARGIN == 80
    with pytest.raises(ImportError):
        from src.config import (
            HEADER_FOOTER_MARGIN,  # noqa: F401  # pyright: ignore[reportAttributeAccessIssue]
        )


def test_pdf_heading_subprocess_constants():
    """pm 标题树子进程超时与并发上限默认值。"""
    from src.config import (
        MAX_CONCURRENT_HEADING_SUBPROCESS,
        PDF_HEADING_SUBPROCESS_TIMEOUT,
    )

    assert PDF_HEADING_SUBPROCESS_TIMEOUT == 180
    assert MAX_CONCURRENT_HEADING_SUBPROCESS == 2


def test_non_kb_temperature_default():
    """NON_KB_MAIN_TEMPERATURE 默认 0.6（.env 污染环境也可稳定断言）。"""
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("NON_KB_MAIN_TEMPERATURE", None)
        reloaded = reload(_s)
        assert reloaded.NON_KB_MAIN_TEMPERATURE == 0.6


def test_langfuse_defaults_do_not_point_to_nonexistent_host():
    """内置 HOST 默认值不得指向仓库内不存在的服务名（D19）。

    reload 前必须把 `dotenv.load_dotenv` 打成 no-op：`src.config.settings`
    在**导入时**调用它，而本 worktree 的 `.env`（软链）定义了 `LANGFUSE_HOST`，
    否则 reload 会重新灌回 .env 的值 —— 那是在测 .env，不是在测内置默认值。
    """
    import importlib

    import src.config.settings as settings_mod

    with patch("dotenv.load_dotenv"), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LANGFUSE_HOST", None)
        reloaded = importlib.reload(settings_mod)
        assert "langfuse:3000" not in reloaded.LANGFUSE_HOST
        assert reloaded.LANGFUSE_HOST == "http://langfuse-web:3000"


def test_langfuse_key_defaults_are_empty():
    """内置 key 默认值为空串 —— 没配就明确不可用，不假装有值（D19）。

    同上的 `.env` reload 陷阱：必须把 `dotenv.load_dotenv` 打成 no-op，
    否则 reload 会从软链 .env 重新灌回真实的 SECRET/PUBLIC key。
    """
    import importlib

    import src.config.settings as settings_mod

    with patch("dotenv.load_dotenv"), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LANGFUSE_SECRET_KEY", None)
        os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
        reloaded = importlib.reload(settings_mod)
        assert reloaded.LANGFUSE_SECRET_KEY == ""
        assert reloaded.LANGFUSE_PUBLIC_KEY == ""


def test_model_price_defaults_to_zero_meaning_unconfigured():
    """单价默认必须是 0.0 —— 0 是「未配置」的哨兵值（seed CLI 据此跳过）。

    同上的 `.env` reload 陷阱：必须把 `dotenv.load_dotenv` 打成 no-op 并清掉
    两个键，否则 reload 会从软链 .env 灌回真实单价 —— 那是在测 .env，
    不是在测内置默认值。
    """
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MODEL_INPUT_PRICE_PER_TOKEN", None)
        os.environ.pop("MODEL_OUTPUT_PRICE_PER_TOKEN", None)
        reloaded = reload(_s)
        assert reloaded.MODEL_INPUT_PRICE_PER_TOKEN == 0.0
        assert reloaded.MODEL_OUTPUT_PRICE_PER_TOKEN == 0.0


def test_rerank_timeout_nesting_defaults_pass():
    """默认取值（1 × 5s < 6s）满足嵌套约束，启动期断言放行。"""
    import src.config.settings as _s

    _s.validate_timeout_nesting()  # 不抛即通过


def test_rerank_timeout_nesting_violation_raises(monkeypatch):
    """env 把 attempts 调大 ⇒ 内层总预算 ≥ 外层 ⇒ 启动期 fail-fast（D13）。

    默认 attempts=1 使内层总预算（5s）成为真实上界；一旦被 env 调成 3，
    复合超时（≈18s）会原样回归，而外层 6s 会先放弃、底层线程仍跑到底。
    """
    import src.config.settings as _s

    monkeypatch.setattr(_s, "RERANK_MAX_ATTEMPTS", 3)
    with pytest.raises(RuntimeError, match="RERANK_OUTER_TIMEOUT"):
        _s.validate_timeout_nesting()


def test_model_price_is_env_driven(monkeypatch):
    """单价可由环境变量覆盖，且为浮点。

    两次 reload 都必须在隔离块内：否则退出时 `.env` 会把真实单价写回，
    污染同会话其他用例。
    """
    import importlib

    monkeypatch.setenv("MODEL_INPUT_PRICE_PER_TOKEN", "0.000003")
    monkeypatch.setenv("MODEL_OUTPUT_PRICE_PER_TOKEN", "0.000006")

    from src.config import settings as settings_module

    with patch("dotenv.load_dotenv"), patch.dict(os.environ, {}, clear=False):
        reloaded = importlib.reload(settings_module)
        assert reloaded.MODEL_INPUT_PRICE_PER_TOKEN == 0.000003
        assert reloaded.MODEL_OUTPUT_PRICE_PER_TOKEN == 0.000006

        monkeypatch.delenv("MODEL_INPUT_PRICE_PER_TOKEN")
        monkeypatch.delenv("MODEL_OUTPUT_PRICE_PER_TOKEN")
        importlib.reload(settings_module)


def test_idle_threshold_defaults_are_below_transport_read():
    """四档 idle 阈值默认值：两档都 < 传输层 read，且首事件前档不窄于事件后档。"""
    import src.config.settings as _s

    assert _s.MAIN_IDLE_FIRST_S == 60.0
    assert _s.MAIN_IDLE_FIRST_THINKING_S == 150.0
    assert _s.MAIN_IDLE_GAP_S == 30.0
    assert _s.MAIN_IDLE_GAP_THINKING_S == 60.0
    # 两档必须 < 传输层 read（否则传输层先触发、该档永不负责，D15）
    assert _s.MAIN_IDLE_FIRST_S < _s.MAIN_TIMEOUT_READ
    assert _s.MAIN_IDLE_FIRST_THINKING_S < _s.MAIN_TIMEOUT_READ
    assert _s.MAIN_IDLE_GAP_S < _s.MAIN_TIMEOUT_READ
    assert _s.MAIN_IDLE_GAP_THINKING_S < _s.MAIN_TIMEOUT_READ
    # 首事件前含排队 + prefill（开思考时更久）⇒ 不窄于事件后的间隔档
    assert _s.MAIN_IDLE_GAP_S < _s.MAIN_IDLE_FIRST_S
    assert _s.MAIN_IDLE_GAP_THINKING_S < _s.MAIN_IDLE_FIRST_THINKING_S
    _s.validate_timeout_nesting()  # 不抛即通过


def test_idle_threshold_above_transport_read_raises(monkeypatch):
    """env 把某档 idle 调到 ≥ 传输层 read ⇒ 启动期 fail-fast（该档否则永不负责）。"""
    import src.config.settings as _s

    monkeypatch.setattr(_s, "MAIN_IDLE_FIRST_S", 999.0)
    with pytest.raises(RuntimeError, match="MAIN_IDLE_FIRST_S"):
        _s.validate_timeout_nesting()
