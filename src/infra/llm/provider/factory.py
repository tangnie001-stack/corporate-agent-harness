"""三类模型工厂：handler 侧只认这三个接口（design D1）。"""

from __future__ import annotations

import json

from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from src.config import settings
from src.infra.llm.provider.chat import ReasoningPreservingChat
from src.infra.llm.provider.spec import ProviderSpec, load_registry, resolve_role
from src.infra.llm.provider.thinking import thinking_extra_body

_REGISTRY: dict[str, ProviderSpec] | None = None


def _registry() -> dict[str, ProviderSpec]:
    """进程内缓存注册表，避免每次构造模型都重读 registry.yaml。"""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_registry()
    return _REGISTRY


def _resolve_model(role: str):
    """解析角色最终生效的模型绑定（provider 规格 + 模型名 + 端点 + 密钥）。"""
    return resolve_role(role, _registry())


def _llm_kwargs() -> dict:
    """解析 settings.LLM_KWARGS（JSON）为构造 ChatOpenAI 的额外参数。"""
    return json.loads(settings.LLM_KWARGS)


def build_chat(
    role: str,
    *,
    thinking: bool | None = None,
    model: str | None = None,
    **overrides,
) -> ChatOpenAI:
    """按角色构造会话模型。

    Args:
        role: 角色名（main/classify/summary/judge）。
        thinking: 是否开启思考；None 表示不注入思考参数。
        model: 覆盖 registry/env 解析出的模型名。
        **overrides: 透传给 ChatOpenAI 的额外构造参数。

    Returns:
        ChatOpenAI：带 reasoning 保留能力的会话模型实例。
    """
    resolved = _resolve_model(role)
    extra_kwargs = _llm_kwargs()
    if thinking is not None:
        extra_kwargs["extra_body"] = thinking_extra_body(resolved.provider, thinking)
    extra_kwargs.update(overrides)
    return ReasoningPreservingChat(
        model=model or resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        reasoning_field=resolved.provider.reasoning_field,
        **extra_kwargs,
    )


def build_embeddings() -> OpenAIEmbeddings:
    """构造向量化模型（角色固定 embedding）。

    Returns:
        OpenAIEmbeddings：按 registry 的 embedding_batch_size 设置批大小。
    """
    resolved = _resolve_model("embedding")
    facts = resolved.provider.catalog.get(resolved.model)
    chunk_size = 20
    if facts is not None and facts.embedding_batch_size:
        chunk_size = facts.embedding_batch_size
    return OpenAIEmbeddings(
        model=resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        check_embedding_ctx_length=False,
        chunk_size=chunk_size,
    )


def build_rerank(model: str | None = None, top_n: int | None = None) -> DashScopeRerank:
    """构造精排模型（DashScope 专有，角色固定 rerank）。

    Args:
        model: 覆盖 registry/env 解析出的模型名。
        top_n: 保留条数；None 时取 settings.TOP_K_RERANK。

    Returns:
        DashScopeRerank：DashScope TextReRank 精排器实例。
    """
    import dashscope

    resolved = _resolve_model("rerank")
    effective_top_n = settings.TOP_K_RERANK
    if top_n is not None:
        effective_top_n = top_n
    return DashScopeRerank(
        model=model or resolved.model,
        top_n=effective_top_n,
        api_key=resolved.api_key,
        client=dashscope.TextReRank,
    )
