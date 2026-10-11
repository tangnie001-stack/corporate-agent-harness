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


def test_main_falls_back_to_legacy_llm_keys():
    """Phase 1 软迁移（design D10）：main 只设旧键时，端点 / 密钥回退到 LLM_*。"""
    r = resolve_role(
        "main", REG, {"LLM_BASE_URL": "http://legacy", "LLM_API_KEY": "lk"}
    )
    assert r.base_url == "http://legacy"
    assert r.api_key == "lk"


def test_provider_keys_beat_legacy_llm_keys():
    """优先级：<PROVIDER>_BASE_URL / <PROVIDER>_API_KEY 胜过 LLM_* 旧键。"""
    r = resolve_role(
        "main",
        REG,
        {
            "DASHSCOPE_BASE_URL": "http://primary",
            "LLM_BASE_URL": "http://legacy",
            "DASHSCOPE_API_KEY": "dk",
            "LLM_API_KEY": "lk",
        },
    )
    assert r.base_url == "http://primary"
    assert r.api_key == "dk"


def test_embedding_ignores_legacy_llm_keys():
    """embedding 独立缺省 dashscope，不受 LLM_BASE_URL / LLM_API_KEY 影响（design D10）。"""
    r = resolve_role(
        "embedding", REG, {"LLM_BASE_URL": "http://legacy", "LLM_API_KEY": "lk"}
    )
    assert r.base_url == REG["dashscope"].base_url
    assert r.api_key == ""


def test_rerank_ignores_legacy_llm_keys():
    """rerank 同样独立缺省 dashscope，不继承 LLM_* 旧键。"""
    r = resolve_role(
        "rerank", REG, {"LLM_BASE_URL": "http://legacy", "LLM_API_KEY": "lk"}
    )
    assert r.base_url == REG["dashscope"].base_url
    assert r.api_key == ""
