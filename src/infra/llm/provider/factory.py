"""三类模型工厂：handler 侧只认这三个接口（design D1）。"""

from __future__ import annotations

import json

from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from openai import AsyncOpenAI
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


def build_native_client(role: str, *, timeout: float | None = None) -> AsyncOpenAI:
    """按角色构造 native `AsyncOpenAI` 客户端（供现代 RAGAS 工厂使用）。

    现代 `llm_factory` / `embedding_factory` 的 `client` 必填且须 native 客户端；
    端点/密钥取自既有 `resolve_role`（与 `build_chat` 同源），避免 CLI 重复解析。

    Args:
        role: 角色名（main/classify/summary/judge/embedding）。
        timeout: 请求超时秒数；None 时沿用客户端默认。

    Returns:
        AsyncOpenAI：指向该角色最终生效端点的异步客户端。

    Raises:
        KeyError: 角色未知或无可用模型（沿用 resolve_role 的 fail-fast）。
    """
    resolved = _resolve_model(role)
    extra_kwargs = {}
    if timeout is not None:
        extra_kwargs["timeout"] = timeout
    return AsyncOpenAI(
        api_key=resolved.api_key, base_url=resolved.base_url, **extra_kwargs
    )


def build_ragas_judge():
    """构造现代 RAGAS 裁判 LLM（`llm_factory`）。

    采样/预算策略（design D6，2026-10-15 修订）：
    - `temperature=0` 且**不传 `top_p`** —— RAGAS 内建默认 `temperature=0.01` /
      `top_p=0.1` 会被部分模型以 400 拒绝（实测百炼 kimi-k3）；`top_p=None` 会被
      instructor 从请求里摘掉。
    - `max_tokens=RAGAS_JUDGE_MAX_TOKENS`（默认 8192）—— RAGAS 内建默认仅 **1024**，
      遇长上下文（年报表格 + 多 statements / TP-FP-FN 拆解）会被截断成
      `IncompleteOutputException`（实测 5 题中 2 题命中）。
    - **保留思考**（`enable_thinking=True`）—— 同题 A/B 实测：思考 OFF 时截断消失但
      转为 **schema 校验失败**（`InstructorRetryException`：`statements.0` 非对象 /
      缺 `statement`，RAGAS 硬编码 instructor `Mode.JSON`，长 schema 下结构遵循度下降）；
      思考 ON + 8192 预算同时覆盖「截断」与「形状」两因。
    - client 另设**显式超时**（思考模型慢，超时不足会把分数变成失败）。

    Returns:
        InstructorBaseRagasLLM：供 `ragas.metrics.collections` 指标使用。
    """
    from ragas.llms import llm_factory

    resolved = _resolve_model("judge")
    client = build_native_client("judge", timeout=settings.RAGAS_JUDGE_TIMEOUT)
    return llm_factory(
        resolved.model,
        client=client,
        temperature=0,
        top_p=None,
        max_tokens=settings.RAGAS_JUDGE_MAX_TOKENS,
        # 经 provider 层唯一产地构造思考参数（完整 dict），与 build_chat 同源
        extra_body=thinking_extra_body(resolved.provider, True),
    )


def build_ragas_embeddings():
    """构造现代 RAGAS embeddings（`embedding_factory`）。

    供 `answer_relevancy` / `answer_correctness` 计算语义相似度。

    Returns:
        BaseRagasEmbedding：带 `aembed_text` / `embed_text` 的现代嵌入实例。
    """
    from ragas.embeddings.base import embedding_factory

    resolved = _resolve_model("embedding")
    client = build_native_client("embedding", timeout=settings.RAGAS_JUDGE_TIMEOUT)
    return embedding_factory("openai", model=resolved.model, client=client)


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
