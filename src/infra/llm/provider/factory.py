"""三类模型工厂：handler 侧只认这三个接口（design D1）。"""

from __future__ import annotations

import json

import httpx
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


def _http_timeout(read: float) -> httpx.Timeout:
    """构造细粒度 httpx 超时（ADR-0024：不用平铺标量，否则四维同值且看不出卡在哪段）。

    Args:
        read: 「请求发出 → 字节到达」的读超时秒数（按角色取值）。

    Returns:
        httpx.Timeout：connect/write/pool 取生产链路通用值，read 取入参。
    """
    return httpx.Timeout(
        connect=settings.HTTP_TIMEOUT_CONNECT,
        read=read,
        write=settings.HTTP_TIMEOUT_WRITE,
        pool=settings.HTTP_TIMEOUT_POOL,
    )


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
    # judge 不在 ROLE_TIMEOUT_READ 表内 ⇒ 不注入默认超时/client（其真源归评测链，D17）；
    # 调用方显式传入的 http_client / http_async_client 用 setdefault 语义，不被覆盖。
    if role in settings.ROLE_TIMEOUT_READ:
        tout = _http_timeout(settings.ROLE_TIMEOUT_READ[role])
        # 同步与异步两个 client 都要设：主链路走异步流式，只设 http_client 不生效
        extra_kwargs.setdefault("http_client", httpx.Client(timeout=tout))
        extra_kwargs.setdefault("http_async_client", httpx.AsyncClient(timeout=tout))
        # spec：SDK 自动重试必须关闭（不得被 env / 调用方打开）
        extra_kwargs["max_retries"] = 0
    return ReasoningPreservingChat(
        model=model or resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        reasoning_field=resolved.provider.reasoning_field,
        **extra_kwargs,
    )


def build_embeddings(*, read_timeout: float | None = None) -> OpenAIEmbeddings:
    """构造向量化模型（角色固定 embedding）。

    Args:
        read_timeout: 覆盖 read 超时秒数；None ⇒ 用入库档（离线批量，较宽）。

    Returns:
        OpenAIEmbeddings：按 registry 的 embedding_batch_size 设置批大小，
        带显式细粒度超时且 SDK 自动重试为 0。
    """
    resolved = _resolve_model("embedding")
    facts = resolved.provider.catalog.get(resolved.model)
    chunk_size = 20
    if facts is not None and facts.embedding_batch_size:
        chunk_size = facts.embedding_batch_size
    read = settings.EMBEDDING_TIMEOUT_READ_INGEST
    if read_timeout is not None:
        read = read_timeout
    tout = _http_timeout(read)
    return OpenAIEmbeddings(
        model=resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        check_embedding_ctx_length=False,
        chunk_size=chunk_size,
        http_client=httpx.Client(timeout=tout),
        http_async_client=httpx.AsyncClient(timeout=tout),
        max_retries=0,
    )


def build_native_client(role: str, *, read_timeout: float | None = None) -> AsyncOpenAI:
    """按角色构造 native `AsyncOpenAI` 客户端（供现代 RAGAS 工厂使用）。

    现代 `llm_factory` / `embedding_factory` 的 `client` 必填且须 native 客户端；
    端点/密钥取自既有 `resolve_role`（与 `build_chat` 同源），避免 CLI 重复解析。

    **超时按细粒度区分**（design D6，2026-10-15）：`connect/read/write/pool` 各自取值，
    并经自定义 `http_client` 交给 SDK（SDK 会采用该 timeout）。此前用平铺 300s，导致
    一次挂死调用白等 300s，再叠加 SDK 默认 2 次重试 = 900s 空转。
    `max_retries=0`：**关闭 SDK 重试**（实测唯一的重试放大层：默认 2 次 ⇒ 一个逻辑调用
    实际发出 3 次 HTTP；设 0 ⇒ 只发 1 次。instructor 自身无 max_retries 参数、不叠加）。

    Args:
        role: 角色名（main/classify/summary/judge/embedding）。
        read_timeout: 覆盖 read 超时秒数；None 时取 `RAGAS_HTTP_TIMEOUT_READ`。

    Returns:
        AsyncOpenAI：指向该角色最终生效端点、带细粒度超时的异步客户端。

    Raises:
        KeyError: 角色未知或无可用模型（沿用 resolve_role 的 fail-fast）。
    """
    resolved = _resolve_model(role)
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.RAGAS_HTTP_TIMEOUT_CONNECT,
            read=read_timeout
            if read_timeout is not None
            else settings.RAGAS_HTTP_TIMEOUT_READ,
            write=settings.RAGAS_HTTP_TIMEOUT_WRITE,
            pool=settings.RAGAS_HTTP_TIMEOUT_POOL,
        )
    )
    return AsyncOpenAI(
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        http_client=http_client,
        max_retries=settings.RAGAS_HTTP_MAX_RETRIES,
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
    - **关闭思考**（`RAGAS_JUDGE_THINKING=false`，默认）—— 同题 A/B（2026-10-15）：
      开思考 + 8192 无结构失败但 21 题全量 5/21 行 `Request timed out`、耗时 1h31m；
      **关思考 + 8192 → 0 失败且快 9×**。结构化判定在 `temperature=0` 下不依赖 CoT。
      （踩坑：一度把「关思考」误判为引发 schema 形状错误，实为 4096 截断，8192 可救。）
    - client 另设**细粒度超时**（connect/read/write/pool 各自取值，见 `build_native_client`），
      超时不当会把分数变成失败。

    Returns:
        InstructorBaseRagasLLM：供 `ragas.metrics.collections` 指标使用。
    """
    from ragas.llms import llm_factory

    resolved = _resolve_model("judge")
    client = build_native_client("judge")
    return llm_factory(
        resolved.model,
        client=client,
        temperature=0,
        top_p=None,
        max_tokens=settings.RAGAS_JUDGE_MAX_TOKENS,
        # 经 provider 层唯一产地构造思考参数（完整 dict），与 build_chat 同源
        extra_body=thinking_extra_body(
            resolved.provider, settings.RAGAS_JUDGE_THINKING
        ),
    )


def build_ragas_embeddings():
    """构造现代 RAGAS embeddings（`embedding_factory`）。

    供 `answer_relevancy` / `answer_correctness` 计算语义相似度。

    Returns:
        BaseRagasEmbedding：带 `aembed_text` / `embed_text` 的现代嵌入实例。
    """
    from ragas.embeddings.base import embedding_factory

    resolved = _resolve_model("embedding")
    client = build_native_client("embedding")
    return embedding_factory("openai", model=resolved.model, client=client)


class _TimeoutReRankAdapter:
    """给 DashScope TextReRank 注入请求级超时的薄适配器。

    为什么需要：`DashScopeRerank` 的 pydantic 字段只有
    `['client','model','top_n','dashscope_api_key']` 且 `model_config['extra']='forbid'`
    ⇒ **构造器传 timeout 会直接报错**；而它唯一的调用出口是 `self.client.call(...)`，
    dashscope SDK 又**原生支持 `request_timeout`**（`api_request_factory.py:33` 会 pop 出来
    交给 HTTP 层）。故在 `client` 这一个注入点上兜住超时。

    它**根治而非绕过**：有了 `request_timeout`，挂死的调用会自行结束
    （外层 `asyncio.wait_for` 只是放弃等待，**不终止** `to_thread` 里的线程）。
    """

    def __init__(self, inner: object, timeout: float) -> None:
        self._inner = inner
        self._timeout = timeout

    def call(self, **kwargs: object) -> object:
        kwargs.setdefault("request_timeout", self._timeout)
        return self._inner.call(**kwargs)  # type: ignore[attr-defined]


def build_rerank(model: str | None = None, top_n: int | None = None) -> DashScopeRerank:
    """构造精排模型（DashScope 专有，角色固定 rerank）。

    Args:
        model: 覆盖 registry/env 解析出的模型名。
        top_n: 保留条数；None 时取 settings.TOP_K_RERANK。

    Returns:
        DashScopeRerank：DashScope TextReRank 精排器实例，其 client 为注入
        `request_timeout=RERANK_TIMEOUT_READ` 的适配器（超时下沉到客户端，D13）。
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
        client=_TimeoutReRankAdapter(
            dashscope.TextReRank, timeout=settings.RERANK_TIMEOUT_READ
        ),
    )
