"""0.4.x 现代栈工厂已删除（tasks 9.3 / design D11）——防悬空引用回流。"""


def test_modern_ragas_factories_are_gone():
    from src.infra.llm.provider import factory

    assert not hasattr(factory, "build_ragas_judge")
    assert not hasattr(factory, "build_ragas_embeddings")
    assert not hasattr(factory, "build_native_client")


def test_bridge_factories_remain():
    """桥工厂保留（评测链的裁判构造入口）。"""
    from src.infra.llm.provider import factory

    assert hasattr(factory, "build_bridge_judge")
    assert hasattr(factory, "build_embeddings")
    assert hasattr(factory, "build_chat")
