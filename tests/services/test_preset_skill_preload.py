"""预设预绑定 skill 首轮预加载：一次生效、不进 system prompt、缺失只 warn。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.const import SKILL_INJECTION_PREFIX
from src.core.log_events import Event
from src.services.agent_service import AgentService


class _Record:
    """最小 SkillRecord 替身（preload 只读 inline_prompt / fork_body）。"""

    def __init__(self, name: str, inline: str = "", fork: str = ""):
        self.name = name
        self.inline_prompt = inline
        self.fork_body = fork


def _service(records: dict[str, _Record]) -> AgentService:
    """构造只带预加载所需依赖的 AgentService（跳过 __init__）。"""
    svc = AgentService.__new__(AgentService)
    registry = MagicMock()
    registry.get = MagicMock(side_effect=lambda name: records.get(name))
    svc._skill_registry = registry
    return svc


def test_preload_renders_in_declared_order():
    """按 skills 声明顺序拼接（两份都是 inline 正文）。"""
    svc = _service(
        {
            "a": _Record("a", inline="方法论 A：$ARGUMENTS"),
            "b": _Record("b", inline="方法论 B"),
        }
    )
    text, names = svc._preload_skills_text(["a", "b"])
    assert text.index("方法论 A") < text.index("方法论 B")
    assert names == ["a", "b"]


def test_preload_skips_fork_skill(monkeypatch):
    """fork skill 的正文是子代理 prompt，不得注入主 agent；记 warning 并跳过。"""
    logged: list[dict] = []
    monkeypatch.setattr(
        "src.services.agent_service.core_logging.log_event",
        lambda event, **fields: logged.append({"event": event, **fields}),
    )
    svc = _service(
        {
            "a": _Record("a", inline="方法论 A"),
            "f": _Record("f", fork="子代理用的任务 prompt"),
        }
    )
    text, names = svc._preload_skills_text(["a", "f"])
    assert text == "方法论 A"
    assert names == ["a"]
    assert "子代理用的任务 prompt" not in text
    assert any(
        item.get("event") == Event.SKILL_PRELOAD_SKIP
        and item.get("reason") == "not_inline"
        for item in logged
    )


def test_preload_all_fork_returns_empty():
    """全部命中 fork skill → 什么都不注入。"""
    svc = _service({"f": _Record("f", fork="子代理 prompt")})
    text, names = svc._preload_skills_text(["f"])
    assert text == ""
    assert names == []


def test_preload_renders_without_task_text():
    """预加载不带任务文本：占位符渲染为空串（注入的是方法论）。"""
    svc = _service({"a": _Record("a", inline="方法论 A：$ARGUMENTS")})
    text, names = svc._preload_skills_text(["a"])
    assert text == "方法论 A："
    assert names == ["a"]


def test_preload_skips_unknown_skill(monkeypatch):
    """声明了但查不到的技能 → 跳过该条，其余照常，且记 warn。"""
    logged: list[dict] = []
    monkeypatch.setattr(
        "src.services.agent_service.core_logging.log_event",
        lambda event, **fields: logged.append({"event": event, **fields}),
    )
    svc = _service({"a": _Record("a", inline="方法论 A")})
    text, names = svc._preload_skills_text(["ghost", "a"])
    assert text == "方法论 A"
    assert names == ["a"]
    assert any("ghost" in str(item) for item in logged)


def test_preload_returns_text_and_resolved_names():
    """返回 (正文, 成功解析名单)：去重、保序、查不到的不入名单（design D14）。"""
    svc = _service(
        {
            "a": _Record("a", inline="方法论 A"),
            "b": _Record("b", inline="方法论 B"),
        }
    )
    text, names = svc._preload_skills_text(["a", "b", "ghost", "a"])
    assert names == ["a", "b"]
    assert "方法论 A" in text and "方法论 B" in text


def test_preload_only_on_first_round():
    """首轮返回正文、非首轮返回空（history 非空即跳过）。"""
    svc = _service({"a": _Record("a", inline="方法论 A")})
    svc._preset_registry = MagicMock()
    preset = MagicMock()
    preset.skills = ["a"]
    preset.channel = ""  # 不限渠道
    svc._preset_registry.get = MagicMock(return_value=preset)

    text, names = svc._preload_if_first_round("finance-expert", [])
    assert text == "方法论 A"
    assert names == ["a"]
    text, names = svc._preload_if_first_round("finance-expert", [object()])
    assert text == ""
    assert names == []


def test_preload_skipped_when_inline_already_injected():
    """本轮已有 inline `/xxx`（T5 已往 history 追加注入条目）→ 不预加载（条件①）。"""
    svc = _service({"a": _Record("a", inline="方法论 A")})
    svc._preset_registry = MagicMock()
    preset = MagicMock()
    preset.skills = ["a"]
    preset.channel = ""  # 不限渠道
    svc._preset_registry.get = MagicMock(return_value=preset)

    text, names = svc._preload_if_first_round("finance-expert", [object()])
    assert text == ""
    assert names == []


@pytest.mark.asyncio
async def test_stream_chat_preloads_on_first_round():
    """stream_chat 首轮命中预加载 → 走 T5b 注入通道，history 末尾为该注入条目。"""
    svc = AgentService.__new__(AgentService)
    chat_manager = AsyncMock()
    chat_manager.get_history_async.return_value = []
    chat_manager.get_session_agent_async.return_value = "finance-expert"
    chat_manager.get_summary_async.return_value = ("", 0)
    chat_manager.add_message_async = AsyncMock()
    chat_manager.save_user_async = AsyncMock()
    svc._chat_manager = chat_manager

    skill_registry = MagicMock()
    skill_registry.user_visible.return_value = []
    skill_registry.model_visible.return_value = []
    skill_registry.get.side_effect = {"a": _Record("a", inline="方法论 A")}.get
    svc._skill_registry = skill_registry

    preset_registry = MagicMock()
    preset = MagicMock()
    preset.skills = ["a"]
    preset.system_prompt = ""
    preset.channel = ""  # 不限渠道
    preset_registry.get.return_value = preset
    svc._preset_registry = preset_registry
    svc._graph = MagicMock()

    _, launch_ctx = await svc.stream_chat("kb1", "s1", "帮我分析", agent="")

    # 两次写：用户原文 + 预加载注入
    assert chat_manager.add_message_async.await_count == 2
    second = chat_manager.add_message_async.await_args_list[1]
    assert SKILL_INJECTION_PREFIX in second.args[2]
    assert launch_ctx["history"][-1].content.startswith(SKILL_INJECTION_PREFIX)
