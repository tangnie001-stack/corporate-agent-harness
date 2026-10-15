"""models 门面薄委托测试：main/judge 角色默认走 registry 绑定。"""

from src.models import get_judge_llm, get_llm


def test_get_judge_llm_uses_judge_role(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory

    monkeypatch.setattr(factory, "_REGISTRY", None)
    llm = get_judge_llm()
    assert llm.model_name == "qwen3.8-flash"  # registry.models[judge]


def test_get_judge_llm_carries_request_timeout(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.config import settings
    from src.infra.llm.provider import factory

    monkeypatch.setattr(factory, "_REGISTRY", None)
    llm = get_judge_llm()
    # judge 客户端须带显式请求超时（思考模型无默认超时，缺省会无限挂起）
    assert llm.request_timeout == settings.RAGAS_JUDGE_TIMEOUT


def test_get_llm_defaults_to_registry(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory

    monkeypatch.setattr(factory, "_REGISTRY", None)
    assert get_llm().model_name == "qwen3.7-flash"  # registry.models[main]
