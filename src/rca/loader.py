"""rca-* skill 的加载与匹配。

与 src/agents/skills/loader.py 并存但互不影响：rca skill 只被本模块读取，
且 SKILL.md 声明 user-invocable: false / disable-model-invocation: true，
不会出现在普通问答的可用技能列表里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.rca.models import EvidenceSpec


@dataclass(frozen=True)
class RcaSkill:
    """一份可执行的判定 skill。"""

    name: str  # skill 名（目录名）
    match: str  # alertname 正则
    body: str  # 判定正文（Markdown）
    evidence: tuple[EvidenceSpec, ...]  # 证据清单


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """拆分 YAML frontmatter 与正文。"""
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("unterminated frontmatter")
    meta = yaml.safe_load(parts[1]) or {}
    if not isinstance(meta, dict):
        raise ValueError(  # noqa: TRY004  # skill 是外部内容，加载失败统一按 ValueError 跳过
            "frontmatter must be a mapping"
        )
    return meta, parts[2].strip()


def _build_evidence(items: object) -> tuple[EvidenceSpec, ...]:
    """把 frontmatter 的 evidence 列表转成 EvidenceSpec。"""
    if not isinstance(items, list) or not items:
        raise ValueError("evidence must be a non-empty list")
    specs = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(  # noqa: TRY004  # 同上：加载失败统一按 ValueError 跳过
                "evidence item must be a mapping"
            )
        specs.append(
            EvidenceSpec(
                id=str(item["id"]),
                tool=str(item["tool"]),
                params={str(k): str(v) for k, v in (item.get("params") or {}).items()},
                required=bool(item.get("required", True)),
                timeout=float(item.get("timeout", 8)),
                when=item.get("when"),
            )
        )
    return tuple(specs)


def load_skills(root: Path) -> list[RcaSkill]:
    """扫描 root 下的 skill 目录；非法 skill 跳过。"""
    skills: list[RcaSkill] = []
    if not root.exists():
        return skills
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        skill_file = directory / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            meta, body = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            match = str(meta["match"])
            re.compile(match)
            skills.append(
                RcaSkill(
                    name=str(meta.get("name", directory.name)),
                    match=match,
                    body=body,
                    evidence=_build_evidence(meta.get("evidence")),
                )
            )
        except (ValueError, KeyError, yaml.YAMLError, re.error):
            continue
    return skills


def match_skill(skills: list[RcaSkill], alertname: str) -> RcaSkill | None:
    """按 alertname 正则匹配；命中返回第一个，否则 None。"""
    for skill in skills:
        if re.search(skill.match, alertname):
            return skill
    return None
