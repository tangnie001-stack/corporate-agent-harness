"""rca-error 技能可被 agent 侧加载，且模型可自动调用。"""

from pathlib import Path

from src.agents.skills.loader import SkillLoader

_SKILL_PATH = Path(__file__).resolve().parents[3] / "skills"


def test_rca_error_skill_loads_and_is_model_invocable():
    loader = SkillLoader(_SKILL_PATH)
    skills = {s.name: s for s in loader.load_all()}
    skill = skills["rca-error"]
    assert "diagnose_trace" in skill.description
    assert skill.user_invocable is True
    assert skill.disable_model_invocation is False
