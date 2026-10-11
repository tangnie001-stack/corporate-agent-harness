from pathlib import Path

import pytest

from src.infra.llm.provider.spec import ThinkingStyle, load_registry

REGISTRY = Path("src/infra/llm/provider/registry.yaml")


def test_load_dashscope():
    reg = load_registry(REGISTRY)
    assert "dashscope" in reg
    ds = reg["dashscope"]
    assert ds.thinking_style is ThinkingStyle.DASHSCOPE
    assert ds.reasoning_field == "reasoning_content"
    assert ds.api_key_env == "DASHSCOPE_API_KEY"
    assert ds.models["main"]  # 非空
    assert ds.models["embedding"] and ds.models["rerank"]
    assert ds.catalog[ds.models["embedding"]].embedding_dimension == 1024


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_registry(tmp_path / "nope.yaml")
