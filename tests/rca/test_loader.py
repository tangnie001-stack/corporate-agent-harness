from pathlib import Path

from src.rca.loader import load_skills, match_skill

SKILL = """---
name: rca-latency
match: ".*响应时间.*"
evidence:
  - id: stats
    tool: latency_stats
    required: true
    timeout: 8
    params:
      uri: "${alert.uri}"
---

判定正文
"""


def test_load_and_match(tmp_path: Path):
    path = tmp_path / "rca-latency"
    path.mkdir()
    (path / "SKILL.md").write_text(SKILL, encoding="utf-8")
    skills = load_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].evidence[0].tool == "latency_stats"
    assert match_skill(skills, "支付单createOrder响应时间上升") is not None
    assert match_skill(skills, "磁盘使用率") is None


def test_bad_skill_skipped(tmp_path: Path):
    path = tmp_path / "broken"
    path.mkdir()
    (path / "SKILL.md").write_text("---\nname: x\nmatch: '['\n---\n", encoding="utf-8")
    assert load_skills(tmp_path) == []
