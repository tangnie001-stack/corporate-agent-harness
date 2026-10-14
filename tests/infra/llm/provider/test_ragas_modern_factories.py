"""现代 RAGAS 工厂：temperature=0、不发 top_p、显式超时（design D6）。"""

from typing import cast

from ragas.llms import InstructorLLM

from src.infra.llm.provider import factory


def test_ragas_judge_sampling_and_timeout(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    # llm_factory 的签名返回抽象基类，实际实现是 InstructorLLM；cast 仅为取回具体字段
    llm = cast(InstructorLLM, factory.build_ragas_judge())

    # instructor 的 model_args 构造期落定；RAGAS 内建默认是 0.01/0.1，会被部分模型 400
    assert llm.model_args["temperature"] == 0
    assert llm.model_args.get("top_p") is None
    # client 须带显式超时（思考模型偶尔 >30s）
    assert llm.client.timeout is not None


def test_ragas_embeddings_has_modern_interface(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    emb = factory.build_ragas_embeddings()

    assert hasattr(emb, "aembed_text")
