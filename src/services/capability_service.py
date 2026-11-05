"""能力清单服务（design D19）——清单由 registry 派生，不引入 catalog 文件。

只做"取 + 投影"，不做缓存：registry 自身有懒重载（文件 mtime 变化自动生效），
再加一层缓存就等于要养第二份陈旧源。api 层只转发。
"""

from src.agents.presets.registry import AgentPresetRegistry
from src.agents.skills.registry import SkillRegistry


class CapabilityService:
    """对外暴露"可调用技能"与"可选智能体"两个只读清单。"""

    def __init__(
        self,
        skill_registry: SkillRegistry | None,
        preset_registry: AgentPresetRegistry | None,
    ) -> None:
        """注入两个注册表（可为 None —— skills 目录缺失时降级空列表）。"""
        self._skill_registry = skill_registry
        self._preset_registry = preset_registry

    def list_skills(self) -> list[dict]:
        """可被用户 `/xxx` 调用的技能（服务端已过滤 user-invocable:false）。"""
        if self._skill_registry is None:
            return []
        records = self._skill_registry.user_visible()
        return [{"name": r.name, "description": r.description} for r in records]

    def list_agents(self) -> list[dict]:
        """站点可选的智能体预设（含 display_name，供选择器显示中文名）。

        hidden=true 的预设（企微机器人专用）不列出：站点选择器不得选到它们。
        """
        if self._preset_registry is None:
            return []
        presets = [p for p in self._preset_registry.all() if not p.hidden]
        return [
            {
                "name": p.name,
                "display_name": p.display_name,
                "description": p.description,
            }
            for p in presets
        ]
