"""dashscope 角色绑定：main 族用 qwen3.7-flash、judge 用 qwen3.8-flash（design D10）。"""

from src.infra.llm.provider.spec import load_registry, resolve_role


def test_dashscope_roles(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    reg = load_registry()
    for role in ("main", "classify", "summary"):
        assert resolve_role(role, reg).model == "qwen3.7-flash"
    assert resolve_role("judge", reg).model == "qwen3.8-flash"


def test_qwen_flash_models_have_pricing(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    catalog = load_registry()["dashscope"].catalog
    assert catalog["qwen3.7-flash"].pricing is not None
    assert catalog["qwen3.8-flash"].pricing is not None
