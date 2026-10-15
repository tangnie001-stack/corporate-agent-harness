"""现代 RAGAS 工厂：temperature=0、不发 top_p、细粒度超时 + 输出预算/思考（design D6）。"""

from typing import cast

import httpx
from ragas.llms import InstructorLLM

from src.config import settings
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


def test_native_client_fine_grained_timeout_and_no_sdk_retries(monkeypatch):
    """细粒度超时 + 关闭 SDK 默认重试（避免与 instructor 叠成乘法放大）。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    client = factory.build_native_client("judge")
    t = client.timeout
    assert isinstance(t, httpx.Timeout)
    assert t.connect == settings.RAGAS_HTTP_TIMEOUT_CONNECT
    assert t.read == settings.RAGAS_HTTP_TIMEOUT_READ
    assert t.write == settings.RAGAS_HTTP_TIMEOUT_WRITE
    assert t.pool == settings.RAGAS_HTTP_TIMEOUT_POOL
    assert client.max_retries == settings.RAGAS_HTTP_MAX_RETRIES == 0
    # read 上限须显著小于原先的平铺 300s（否则空转照旧）
    assert t.read is not None and t.read < 300


def test_ragas_judge_output_budget_and_thinking(monkeypatch):
    """RAGAS 默认 max_tokens=1024 会截断长上下文；裁判默认关思考（A/B 定稿）。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    llm = cast(InstructorLLM, factory.build_ragas_judge())

    assert llm.model_args["max_tokens"] >= 8192
    assert llm.model_args["extra_body"]["enable_thinking"] is False


def test_ragas_embeddings_has_modern_interface(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(factory, "_REGISTRY", None)

    emb = factory.build_ragas_embeddings()

    assert hasattr(emb, "aembed_text")
