"""models 门面薄委托测试：main/judge 角色默认走 registry 绑定。"""

from src.models import get_judge_llm, get_llm


def test_get_judge_llm_uses_judge_role(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory

    monkeypatch.setattr(factory, "_REGISTRY", None)
    llm = get_judge_llm()
    assert llm.model_name == "kimi-k3"  # registry.models[judge]


def test_get_llm_defaults_to_registry(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory

    monkeypatch.setattr(factory, "_REGISTRY", None)
    assert get_llm().model_name == "qwen3.8-flash"  # registry.models[main]
