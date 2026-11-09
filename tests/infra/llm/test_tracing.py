"""接线基础设施单测 —— 不发网络。"""

import os
import uuid

from src.infra.llm import tracing


def test_new_trace_id_shape():
    """生成的 id 为 32 位小写十六进制（W3C Trace Context）。"""
    tid = tracing.new_trace_id()
    assert len(tid) == 32
    assert tracing.TRACE_ID_PATTERN.fullmatch(tid) is not None
    uuid.UUID(tid)  # 可解析为 16 字节


def test_valid_trace_id_accepts_32_hex():
    """32 位小写 hex 判为合法。"""
    assert tracing.is_valid_trace_id("0123456789abcdef0123456789abcdef")


def test_valid_trace_id_rejects_legacy_and_malformed():
    """旧格式（带前缀/短横线/大写）、空串、长度不符一律拒绝。"""
    assert not tracing.is_valid_trace_id("")
    assert not tracing.is_valid_trace_id("trace_1234-abcd")
    assert not tracing.is_valid_trace_id("0123456789ABCDEF0123456789ABCDEF")
    assert not tracing.is_valid_trace_id("0" * 31)
    assert not tracing.is_valid_trace_id("0" * 33)
    assert not tracing.is_valid_trace_id("g" * 32)


def test_configure_tracing_sets_sdk_env_when_disabled(monkeypatch):
    """开关关闭时把 LANGFUSE_TRACING_ENABLED 压成 false（SDK 侧兜底）。"""
    from src.config import settings

    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", False)
    tracing.configure_tracing()
    assert os.environ["LANGFUSE_TRACING_ENABLED"] == "false"
