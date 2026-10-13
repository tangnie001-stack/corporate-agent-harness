"""Phase 2：registry 中的 deepseek 条目 —— 角色解析正确性。

模型名以 DeepSeek 官方 ``GET /models`` 为准：``deepseek-flash``（DeepSeek-V4.1-Flash）
与 ``deepseek-v4-pro``（DeepSeek-V4-Pro-0813）。
"""

import pytest

from src.infra.llm.provider.spec import load_registry, resolve_role

REG = load_registry()


def test_main_family_follows_llm_provider():
    """main / classify / summary 跟随 LLM_PROVIDER → deepseek。"""
    env = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "k"}
    for role in ("main", "classify", "summary"):
        assert resolve_role(role, REG, env).provider.name == "deepseek"


def test_independent_roles_stay_dashscope():
    """embedding / rerank / judge 独立缺省 dashscope（不跟随 LLM_PROVIDER）。"""
    env = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "k"}
    for role in ("embedding", "rerank", "judge"):
        assert resolve_role(role, REG, env).provider.name == "dashscope"


def test_judge_can_bind_deepseek_explicitly():
    """judge 可由 RAGAS_PROVIDER 显式绑到 deepseek（registry 提供该角色模型）。"""
    env = {
        "LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "k",
        "RAGAS_PROVIDER": "deepseek",
    }
    resolved = resolve_role("judge", REG, env)
    assert resolved.provider.name == "deepseek"
    assert resolved.model == "deepseek-flash"


def test_deepseek_has_no_embedding_rerank():
    """deepseek 无 embedding / rerank 角色 ⇒ 显式绑定会 KeyError（启动校验拦截）。"""
    env = {
        "LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "k",
        "EMBEDDING_PROVIDER": "deepseek",
        "RERANK_PROVIDER": "deepseek",
    }
    with pytest.raises(KeyError):
        resolve_role("embedding", REG, env)
    with pytest.raises(KeyError):
        resolve_role("rerank", REG, env)


def test_deepseek_default_model_names():
    """deepseek 绑定的每个角色都用 deepseek-flash（不使用 pro 档）。

    judge 不跟随 ``LLM_PROVIDER``（需 ``RAGAS_PROVIDER`` 显式绑定，见上一条
    用例），故这里直接断言 registry 数据面：deepseek 下所有角色的默认模型均为
    ``deepseek-flash``；任一角色被改回 pro 都会让本用例失败。
    """
    spec = REG["deepseek"]
    assert set(spec.models) == {"main", "classify", "summary", "judge"}
    for role, model in spec.models.items():
        assert model == "deepseek-flash", f"{role} 默认模型不是 flash: {model}"

    env = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "k"}
    for role in ("main", "classify", "summary"):
        assert resolve_role(role, REG, env).model == "deepseek-flash"
