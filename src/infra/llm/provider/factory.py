"""三类模型工厂：handler 侧只认这三个接口（design D1）。"""

from __future__ import annotations

import json

import httpx
from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from src.config import settings
from src.infra.llm.provider.chat import ReasoningPreservingChat
from src.infra.llm.provider.http_observability import HookEvidence, make_logging_client
from src.infra.llm.provider.spec import ProviderSpec, load_registry, resolve_role
from src.infra.llm.provider.thinking import thinking_extra_body
from src.infra.llm.retrying_chat import RetryingReasoningChat

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
    # 生产链路角色（在 ROLE_TIMEOUT_READ 表内）才注入本阶段默认超时/client；
    # judge 等不在表内的角色不注入（其真源归评测链，D17）；
    # 调用方显式传入的 http_client / http_async_client 用 setdefault 语义，不被覆盖。
    if role in settings.ROLE_TIMEOUT_READ:
        tout = _http_timeout(settings.ROLE_TIMEOUT_READ[role])
        # 同步与异步两个 client 都要设：主链路走异步流式，只设 http_client 不生效
        extra_kwargs.setdefault("http_client", httpx.Client(timeout=tout))
        extra_kwargs.setdefault("http_async_client", httpx.AsyncClient(timeout=tout))
        # SDK 的 per-request timeout 会覆盖 httpx client 上的超时：只设 client 时
        # 我们的 read 超时是空转（实测 read=.001 时首 chunk 仍 0.49s 到达）。
        # 故必须把同一个细粒度 Timeout 对象也作为 request_timeout 传入。
        extra_kwargs.setdefault("request_timeout", tout)
        # spec：SDK 自动重试必须关闭（不得被 env / 调用方打开）
        extra_kwargs["max_retries"] = 0
    base_kwargs = {
        "model": model or resolved.model,
        "api_key": SecretStr(resolved.api_key),
        "base_url": resolved.base_url,
        "reasoning_field": resolved.provider.reasoning_field,
    }
    # 本层有界重试（D16）仅对生产链路角色启用：只有它们在返回类上收口为
    # RetryingReasoningChat，并注入本层阈值（子类不读环境）。judge 等角色**返回原
    # ReasoningPreservingChat**、不带任何 model_retry_*，逐字恢复改前行为 —— 否则
    # judge 会在「本层 3 次 × SDK 1+2 次」下放大成 9 次 HTTP（跨任务集成缺陷，D17）。
    if role not in settings.ROLE_TIMEOUT_READ:
        return ReasoningPreservingChat(**base_kwargs, **extra_kwargs)
    return RetryingReasoningChat(
        **base_kwargs,
        model_retry_attempts=settings.RETRY_MAX_ATTEMPTS,
        model_retry_initial_interval=settings.RETRY_INITIAL_INTERVAL,
        model_retry_backoff=settings.RETRY_BACKOFF_FACTOR,
        model_retry_role=role,
        **extra_kwargs,
    )


def build_bridge_judge(*, evidence: HookEvidence) -> ChatOpenAI:
    """构造 DeepEval RAGAS 桥用的裁判（langchain `BaseChatModel`）。

    与旧现代栈裁判（`InstructorBaseRagasLLM`，已删）的区别：桥要的是
    langchain chat model，故走 `build_chat`；同时把**细粒度超时**与**请求级观测**
    落在 `http_async_client` 上——ragas 的裁判调用走异步，`http_client` 只喂同步
    client（langchain `chat_models/base.py:806`）。

    Args:
        evidence: 请求级观测证据桶（挂到 hooks 上）。

    Returns:
        ChatOpenAI（`ReasoningPreservingChat`）：`http_async_client` 已置，
        `max_retries=0`，未传 `temperature` / `top_p`（温度由 ragas 分档决定）。
    """
    from src.config import settings

    timeout = httpx.Timeout(
        connect=settings.RAGAS_HTTP_TIMEOUT_CONNECT,
        read=settings.RAGAS_HTTP_TIMEOUT_READ,
        write=settings.RAGAS_HTTP_TIMEOUT_WRITE,
        pool=settings.RAGAS_HTTP_TIMEOUT_POOL,
    )
    client = make_logging_client(
        timeout=timeout,
        evidence=evidence,
    )
    return build_chat(
        "judge",
        thinking=settings.RAGAS_JUDGE_THINKING,
        max_tokens=settings.RAGAS_JUDGE_MAX_TOKENS,
        http_async_client=client,
        max_retries=settings.RAGAS_HTTP_MAX_RETRIES,
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
        # 同 build_chat：SDK per-request timeout 覆盖 client 超时，必须同时传入，
        # 否则 client 上的 read 超时是空转（实测 read=.001 时仍成功 0.42s 返回）。
        # langchain 的 pydantic 字段在 pyright 的合成签名里缺席（误报），运行期有效。
        request_timeout=tout,  # type: ignore[reportCallIssue]
        max_retries=0,
    )


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
