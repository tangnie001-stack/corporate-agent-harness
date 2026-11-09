"""断言测试进程内 tracing 被全局关停（D14）。"""

import os

from src.config import settings


def test_langfuse_env_vars_set_before_import():
    """守护 conftest 顶部的 import 期环境变量（主手段）。"""
    assert os.environ["LANGFUSE_ENABLE"] == "false"
    assert os.environ["LANGFUSE_TRACING_ENABLED"] == "false"


def test_langfuse_disabled_in_tests():
    """测试进程内 LANGFUSE_ENABLE 必须为 False。"""
    assert settings.LANGFUSE_ENABLE is False


def test_tracing_enabled_reflects_settings():
    """应用层守卫读取 settings，关闭时为 False。"""
    from src.infra.llm.tracing import tracing_enabled

    assert tracing_enabled() is False
