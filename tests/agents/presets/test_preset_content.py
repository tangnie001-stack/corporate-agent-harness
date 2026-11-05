"""仓库内真实内容文件的契约测试（防内容与契约脱节）。"""

from pathlib import Path

from src.agents.presets.loader import AgentPresetLoader
from src.agents.skills.loader import SkillLoader

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_finance_expert_preset_loads():
    """agents/finance-expert.md 能被加载且带中文展示名。"""
    presets = AgentPresetLoader(REPO_ROOT / "agents").load_all()

    names = [p.name for p in presets]
    assert "finance-expert" in names
    preset = next(p for p in presets if p.name == "finance-expert")
    assert preset.display_name == "财务专家"
    assert preset.system_prompt


def test_repo_presets_all_load_with_chinese_display_names():
    """仓库内全部智能体预设均可加载（无跳过），且展示名为中文。

    智能体下拉的数据源是 GET /api/agents（读同一 agents/ 目录），故本测试同时是
    "新装预设在选择器里可见"的守卫：文件数须与解析数一致，避免静默跳过。
    """
    presets = AgentPresetLoader(REPO_ROOT / "agents").load_all()

    assert len(presets) == len(list((REPO_ROOT / "agents").glob("*.md")))
    for preset in presets:
        assert preset.system_prompt, preset.name
        assert preset.display_name, preset.name
        assert not preset.display_name.isascii(), preset.name


def test_repo_skills_all_load_and_are_named_ascii():
    """仓库内全部 skill 均通过名称校验且被解析（无跳过）。"""
    records = SkillLoader(REPO_ROOT / "skills").load_all()

    assert len(records) == len(list((REPO_ROOT / "skills").glob("*/SKILL.md")))
    for record in records:
        assert record.name.isascii()


def test_bot_presets_are_hidden_and_wecom_only():
    """仓库内的 bot- 预设必须 hidden + wecom。"""
    presets = {p.name: p for p in AgentPresetLoader(REPO_ROOT / "agents").load_all()}

    for name in ("bot-ops", "bot-dev", "bot-support", "bot-finance"):
        assert name in presets
        assert presets[name].hidden is True
        assert presets[name].channel == "wecom"
