from src.infra.llm.provider.spec import load_registry, resolve_role

REG = load_registry()


def _env(**kw):
    return {k: v for k, v in kw.items()}


def test_main_follows_llm_provider():
    r = resolve_role(
        "main", REG, {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "k"}
    )
    assert r.provider.name == "dashscope"
    assert r.model == REG["dashscope"].models["main"]
    assert r.api_key == "k"


def test_embedding_independent_default():
    r = resolve_role("embedding", REG, {"LLM_PROVIDER": "deepseek"})
    assert r.provider.name == "dashscope"  # 不跟随


def test_role_model_override():
    r = resolve_role("main", REG, {"LLM_PROVIDER": "dashscope", "LLM_MODEL": "kimi-k3"})
    assert r.model == "kimi-k3"


def test_provider_base_url_override():
    r = resolve_role(
        "main", REG, {"LLM_PROVIDER": "dashscope", "DASHSCOPE_BASE_URL": "http://proxy"}
    )
    assert r.base_url == "http://proxy"
