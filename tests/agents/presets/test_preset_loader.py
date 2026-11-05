"""AgentPresetLoader 解析测试（agents/<name>.md，驼峰 frontmatter）。"""

import pytest

from src.agents.presets.loader import AgentPresetLoader


def test_parse_preset_with_all_fields(tmp_path):
    """解析完整 frontmatter：display_name/description/tools/skills/maxTurns + 正文人设。"""
    (tmp_path / "finance-expert.md").write_text(
        "---\n"
        "name: finance-expert\n"
        "display_name: 财务专家\n"
        "description: 财报分析、估值与财务风险研判\n"
        "tools: retrieve_kb\n"
        "skills: finance-qa\n"
        "maxTurns: 8\n"
        "---\n"
        "你是一名资深财务分析师。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert len(presets) == 1
    preset = presets[0]
    assert preset.name == "finance-expert"
    assert preset.display_name == "财务专家"
    assert preset.tools == ["retrieve_kb"]
    assert preset.skills == ["finance-qa"]
    assert preset.max_turns == 8
    assert preset.system_prompt == "你是一名资深财务分析师。"


def test_display_name_defaults_to_name(tmp_path):
    """缺省 display_name 用 name。"""
    (tmp_path / "analyst.md").write_text(
        "---\nname: analyst\ndescription: 数据分析\n---\n你是数据分析师。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert presets[0].display_name == "analyst"


def test_description_falls_back_to_first_paragraph(tmp_path):
    """缺省 description 用正文首段。"""
    (tmp_path / "legal.md").write_text(
        "---\nname: legal\n---\n你是法务顾问，负责合同审阅。\n\n更多说明。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert presets[0].description == "你是法务顾问，负责合同审阅。"


def test_non_ascii_name_is_skipped(tmp_path):
    """非 ASCII slug 名称记 warning 并跳过。"""
    (tmp_path / "财务.md").write_text(
        "---\nname: 财务专家\n---\n正文\n", encoding="utf-8"
    )

    with pytest.warns(UserWarning, match="非法"):
        presets = AgentPresetLoader(tmp_path).load_all()

    assert presets == []


def test_missing_dir_returns_empty(tmp_path):
    """目录不存在时返回空列表，不抛异常。"""
    assert AgentPresetLoader(tmp_path / "nope").load_all() == []


def test_max_turns_bool_is_treated_as_none(tmp_path):
    """maxTurns 为布尔值时视为非法（避免 True 被当成 1），回落 None。"""
    (tmp_path / "analyst.md").write_text(
        "---\nname: analyst\ndescription: 数据分析\nmaxTurns: true\n---\n你是分析师。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert presets[0].max_turns is None


def test_illegal_file_stem_is_skipped_with_warning(tmp_path):
    """frontmatter name 合法但文件名非 ASCII slug → 记 warning 并跳过。"""
    (tmp_path / "财报.md").write_text(
        "---\nname: finance-report\ndescription: 财报\n---\n正文\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="文件名非法"):
        presets = AgentPresetLoader(tmp_path).load_all()

    assert presets == []


def test_parse_hidden_and_channel(tmp_path):
    """frontmatter 解析出 hidden 与 channel。"""
    (tmp_path / "bot-ops.md").write_text(
        "---\nname: bot-ops\ndisplay_name: 运维\ndescription: 运维值守\n"
        "hidden: true\nchannel: wecom\n---\n你是运维机器人。\n",
        encoding="utf-8",
    )

    preset = AgentPresetLoader(tmp_path).load_all()[0]

    assert preset.hidden is True
    assert preset.channel == "wecom"


def test_bot_prefix_without_declaration_is_skipped(tmp_path, recwarn):
    """bot- 前缀缺 hidden/channel：跳过该预设（不注册），其它文件不受影响。"""
    (tmp_path / "bot-ops.md").write_text(
        "---\nname: bot-ops\ndescription: 运维\n---\n正文。\n", encoding="utf-8"
    )
    (tmp_path / "analyst.md").write_text(
        "---\nname: analyst\ndescription: 分析\n---\n正文。\n", encoding="utf-8"
    )

    names = [p.name for p in AgentPresetLoader(tmp_path).load_all()]

    assert names == ["analyst"]
    assert any("bot-ops" in str(w.message) for w in recwarn)


def test_channel_rejects_unknown_value(tmp_path, recwarn):
    """channel 只允许 web / wecom / 空。"""
    (tmp_path / "analyst.md").write_text(
        "---\nname: analyst\ndescription: 分析\nchannel: feishu\n---\n正文。\n",
        encoding="utf-8",
    )

    assert AgentPresetLoader(tmp_path).load_all() == []
    assert any("feishu" in str(w.message) for w in recwarn)
