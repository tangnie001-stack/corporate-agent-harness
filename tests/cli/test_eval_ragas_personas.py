"""RAGAS 测试集固定人设常量的不变量守卫。

ragas 的 ``PersonaList`` 按 persona ``name`` 做**精确**字符串匹配（见
``ragas/testset/persona.py``）。因此固定人设必须满足：名字唯一、且不含括号
——否则模型可能把 role_description 并进名字（``"Chen Wei (role...)"``），
导致出题阶段抛 ``KeyError``。本文件钉住这两条不变量。
"""

from src.config.const import RAGAS_TEST_PERSONAS


def test_persona_names_are_unique() -> None:
    """人设名字必须唯一，避免 ragas 精确匹配取到错误的人设。"""
    names = [name for name, _ in RAGAS_TEST_PERSONAS]
    assert len(names) == len(set(names))


def test_persona_names_exclude_parentheses() -> None:
    """人设名字不得含中英文括号，避免模型把角色描述并进名字。"""
    for name, _ in RAGAS_TEST_PERSONAS:
        for ch in ("(", ")", "（", "）"):
            assert ch not in name


def test_persona_role_descriptions_are_non_empty() -> None:
    """每条人设都必须带非空的角色描述。"""
    for name, desc in RAGAS_TEST_PERSONAS:
        assert name.strip() != ""
        assert desc.strip() != ""
