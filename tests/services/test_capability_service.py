"""能力清单服务：由 registry 派生、字段投影正确、无 registry 时降级空列表。"""

from unittest.mock import MagicMock

from src.services.capability_service import CapabilityService


class _Skill:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description


class _Preset:
    def __init__(
        self, name: str, display_name: str, description: str, hidden: bool = False
    ):
        self.name = name
        self.display_name = display_name
        self.description = description
        self.hidden = hidden


def test_list_skills_projects_name_and_description():
    """只投影 name/description（不透传整对象）。"""
    registry = MagicMock()
    registry.user_visible.return_value = [_Skill("finance-qa", "财务问答")]
    svc = CapabilityService(registry, MagicMock())
    assert svc.list_skills() == [{"name": "finance-qa", "description": "财务问答"}]


def test_list_agents_projects_display_name():
    """agents 多一个 display_name（选择器显示中文名）。"""
    preset_registry = MagicMock()
    preset_registry.all.return_value = [
        _Preset("finance-expert", "财务专家", "财务分析")
    ]
    svc = CapabilityService(MagicMock(), preset_registry)
    assert svc.list_agents() == [
        {
            "name": "finance-expert",
            "display_name": "财务专家",
            "description": "财务分析",
        }
    ]


def test_list_agents_filters_hidden():
    """hidden=true 的预设（企微机器人专用）不出现在站点清单。"""
    preset_registry = MagicMock()
    preset_registry.all.return_value = [
        _Preset("chat", "通用助手", "通用问答"),
        _Preset("bot-ops", "运维机器人", "运维问答", hidden=True),
    ]
    svc = CapabilityService(MagicMock(), preset_registry)

    names = [item["name"] for item in svc.list_agents()]

    assert names == ["chat"]


def test_missing_registry_returns_empty_list():
    """registry 为 None（skills 目录缺失）→ 空列表，不抛。"""
    svc = CapabilityService(None, None)
    assert svc.list_skills() == []
    assert svc.list_agents() == []
