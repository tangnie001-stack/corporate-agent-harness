from src.infra.db.models.chunk import EMBEDDING_DIMENSION
from src.infra.llm.provider.spec import load_registry, resolve_role


def test_registry_dimension_matches_column():
    reg = load_registry()
    r = resolve_role("embedding", reg, {"EMBEDDING_PROVIDER": "dashscope"})
    assert r.provider.catalog[r.model].embedding_dimension == EMBEDDING_DIMENSION
