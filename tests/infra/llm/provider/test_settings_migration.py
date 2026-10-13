"""Task 10 settings 迁移：模型默认改空串 + registry 兜底生效 + 旧键软迁移告警。

本 worktree 的 `.env` 是软链（真实定义了 `LLM_MODEL` 等），import settings 时
`load_dotenv()` 会把这些值灌进 `os.environ`。因此要断言"内置默认值"必须：
  ① 先把 6 个模型键从环境剥掉；
  ② reload 时把 `dotenv.load_dotenv` 打成 no-op（否则 reload 又从 .env 灌回 ——
     那是在测 .env，不是在测默认值）。
本手法沿用 tests/config/test_settings.py 的既定做法。
"""

import importlib
from unittest.mock import patch

from loguru import logger as _logger

from src.infra.llm.provider.spec import load_registry, resolve_role

REG = load_registry()

# 六个模型键：默认改空串后由 registry.models[role] 兜底
_MODEL_ENVS = (
    "LLM_MODEL",
    "CLASSIFY_MODEL",
    "SUMMARY_MODEL",
    "EMBEDDING_MODEL",
    "RERANK_MODEL",
    "RAGAS_LLM_MODEL",
)


def _restore_settings(monkeypatch, settings_module) -> None:
    """撤销 env patch 后 reload，让模块回到与真实 .env 一致的状态。

    不还原的话，reload 会把剥空的默认值留在模块里，污染同会话其他用例。
    """
    monkeypatch.undo()
    importlib.reload(settings_module)


def test_model_defaults_are_empty(monkeypatch):
    """无 .env 覆盖时，6 个模型常量默认均为空串（= 未配置，交给 registry 兜底）。"""
    import src.config.settings as settings_module

    for key in _MODEL_ENVS:
        monkeypatch.delenv(key, raising=False)

    with patch("dotenv.load_dotenv"):
        reloaded = importlib.reload(settings_module)
        assert reloaded.LLM_MODEL == ""
        assert reloaded.CLASSIFY_MODEL == ""
        assert reloaded.EMBEDDING_MODEL == ""
        assert reloaded.RERANK_MODEL == ""
        assert reloaded.RAGAS_LLM_MODEL == ""
        # SUMMARY_MODEL 仍"跟随 main"：main 空则 summary 也空
        assert reloaded.SUMMARY_MODEL == reloaded.LLM_MODEL == ""

    _restore_settings(monkeypatch, settings_module)


def test_registry_fallback_after_defaults_emptied():
    """settings 默认改空后，无 <ROLE>_MODEL 覆盖时 resolve_role 取 registry 默认。

    这是 Task 8 评审的跨任务指针：旧 settings 默认（qwen3.7-flash-2026-07-15 等）
    曾与 registry 默认（qwen3.8-flash 等）分叉；改空后 registry 兜底才真正生效。
    """
    resolved = resolve_role("main", REG, {"DASHSCOPE_API_KEY": "k"})
    assert resolved.model == REG["dashscope"].models["main"] == "qwen3.8-flash"

    resolved_embed = resolve_role("embedding", REG, {"DASHSCOPE_API_KEY": "k"})
    assert resolved_embed.model == REG["dashscope"].models["embedding"]


def test_legacy_base_url_key_triggers_deprecation_warning(monkeypatch):
    """设置旧键 LLM_BASE_URL 时，导入 settings 打 deprecation warning（Phase 1 软迁移）。"""
    import src.config.settings as settings_module

    messages: list[str] = []
    sink_id = _logger.add(lambda m: messages.append(m), level="WARNING")
    try:
        monkeypatch.setenv("LLM_BASE_URL", "http://legacy-proxy:4000")
        with patch("dotenv.load_dotenv"):
            importlib.reload(settings_module)
    finally:
        _logger.remove(sink_id)

    assert any("LLM_BASE_URL/LLM_API_KEY 已废弃" in str(m) for m in messages)
    # 软迁移：旧键仍被读取（Phase 1 行为等值），Phase 2 才删
    assert settings_module.LLM_BASE_URL == "http://legacy-proxy:4000"

    _restore_settings(monkeypatch, settings_module)


def test_legacy_api_key_triggers_deprecation_warning(monkeypatch):
    """设置旧键 LLM_API_KEY 时同样打 deprecation warning；且仍被读取。"""
    import src.config.settings as settings_module

    messages: list[str] = []
    sink_id = _logger.add(lambda m: messages.append(m), level="WARNING")
    try:
        monkeypatch.setenv("LLM_API_KEY", "legacy-key")
        with patch("dotenv.load_dotenv"):
            importlib.reload(settings_module)
    finally:
        _logger.remove(sink_id)

    assert any("LLM_BASE_URL/LLM_API_KEY 已废弃" in str(m) for m in messages)
    assert settings_module.LLM_API_KEY == "legacy-key"

    _restore_settings(monkeypatch, settings_module)
