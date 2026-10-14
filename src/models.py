"""模型工厂门面 — 提供 LLM、Embedding、Rerank 三类模型的实例化入口。

本模块是 provider 兼容层的**薄委托**：真正的构造逻辑（角色→provider/模型/端点
解析、思考参数形状、reasoning 回传）在 `src.infra.llm.provider.factory`，本处只
负责把历史调用方的语义（温度、思考开关、max_tokens）翻译成 `build_*` 的入参，
保持既有签名向后兼容。

核心组件：
  - with_retry：通用重试装饰器，支持指数退避
  - get_embeddings / get_rerank / get_llm / get_classify_llm / get_summary_llm / get_judge_llm
"""

import functools
import time
from collections.abc import Callable
from typing import Any

from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from loguru import logger

from src.config import (
    RETRY_BACKOFF_FACTOR,
    RETRY_INITIAL_INTERVAL,
    RETRY_MAX_ATTEMPTS,
)
from src.infra.llm.provider.factory import build_chat, build_embeddings, build_rerank


def with_retry(
    func: Callable | None = None,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    initial_interval: float = RETRY_INITIAL_INTERVAL,
    backoff: float = RETRY_BACKOFF_FACTOR,
    retryable_exceptions: tuple = (Exception,),
) -> Callable:
    """通用重试装饰器 — 支持指数退避策略和精确异常类型匹配。

    用法灵活，可以不带参数或带参数使用：
        @with_retry
        def my_func(): ...

        @with_retry(max_attempts=5, initial_interval=2.0)
        def my_func(): ...

        @with_retry(retryable_exceptions=(TimeoutError, ConnectionError))
        def my_func(): ...

    Args:
        func: 被装饰的函数（由 Python 自动传入）
        max_attempts: 最大重试次数（默认 3 次）
        initial_interval: 首次重试等待时间（秒）
        backoff: 退避因子（每次等待时间乘以此值，如 2.0 表示翻倍）
        retryable_exceptions: 可重试的异常类型元组（默认所有 Exception）

    Returns:
        包装后的函数，失败时自动重试，超过次数后抛出最后一次的异常
    """
    # 当使用 @with_retry(max_attempts=5) 带参形式时，func 为 None
    # 返回一个 lambda，让 Python 再次调用 with_retry 并传入真正的 func
    if func is None:
        return lambda f: with_retry(
            f, max_attempts, initial_interval, backoff, retryable_exceptions
        )

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except retryable_exceptions as e:
                last_error = e
                if attempt < max_attempts:
                    # 指数退避：wait = initial * backoff^(attempt-1)
                    # 第 1 次重试等 1s，第 2 次等 2s，第 3 次等 4s...
                    wait = initial_interval * (backoff ** (attempt - 1))
                    logger.warning(
                        "{} failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                        func.__name__,
                        attempt,
                        max_attempts,
                        e,
                        wait,
                    )
                    time.sleep(wait)
        # 所有重试均失败，记录错误并抛出最后一次异常
        logger.exception(
            "{} failed after {} attempts: {}", func.__name__, max_attempts, last_error
        )
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{func.__name__} failed after {max_attempts} attempts")

    return wrapper


def _content_logging_callbacks() -> list | None:
    """按 LLM_LOG_CONTENT 开关返回内容记录 callback（默认 None）。

    Returns:
        LLM_LOG_CONTENT 开启时返回 [LlmContentLoggingHandler]，否则 None。
    """
    from src.config import LLM_LOG_CONTENT

    if not LLM_LOG_CONTENT:
        return None
    from src.infra.llm.llm_content_logging import LlmContentLoggingHandler

    return [LlmContentLoggingHandler()]


def get_llm(
    model: str | None = None,
    temperature: float | None = None,
    **kwargs: Any,
) -> ChatOpenAI:
    """创建主对话大语言模型实例（薄委托 build_chat("main")）。

    角色 `main` 的 provider/模型/端点由 registry 解析（`LLM_MODEL` 等 env 可覆盖）。
    思考参数不在此注入：主链路的思考档位由请求期 middleware 经 thinking_extra_body
    提供；fork 子代理经本门面 `get_llm(model=…, extra_body=…)` 新建，其 extra_body
    同样由 provider 层的 thinking_extra_body 产出，避免构造期与请求期两处各写一份。

    Args:
        model: 覆盖 registry/env 解析出的模型名；None 表示默认绑定。
        temperature: 采样温度；None 表示取 settings.LLM_TEMPERATURE。
        **kwargs: 透传给 provider 工厂的额外参数（如 extra_body）。

    Returns:
        ChatOpenAI：主对话模型实例（保留 reasoning 回传能力）。
    """
    from src.config import settings

    effective_temperature = temperature
    if effective_temperature is None:
        effective_temperature = settings.LLM_TEMPERATURE
    return build_chat(
        "main",
        model=model,
        temperature=effective_temperature,
        callbacks=_content_logging_callbacks(),
        **kwargs,
    )


def get_classify_llm() -> ChatOpenAI:
    """创建分类/路由专用 LLM 实例（小模型，关闭思考模式）。

    用于实体抽取（DocumentEntityExtractor）、相对时间词解析（parse_temporal）等
    轻量分类任务；与生成回答的大模型分离，
    避免大模型在简单分类任务上浪费推理时间。

    Returns:
        关闭思考模式的 ChatOpenAI 实例
    """
    from src.config import settings

    return build_chat(
        "classify",
        thinking=False,
        temperature=settings.CLASSIFIER_TEMPERATURE,
        callbacks=_content_logging_callbacks(),
    )


def get_summary_llm() -> ChatOpenAI:
    """创建跨轮历史摘要专用 LLM 实例（关闭思考 + 显式输出上限）。

    与 `get_classify_llm` 的区别：那个是分类器语义、模型走分类角色绑定；
    摘要走独立的 `summary` 角色绑定。必须设 `max_tokens`——否则「拒绝截断摘要」
    这一不变量失去判据（未设上限时几乎不会出现 length 截断）。

    Returns:
        关闭思考、带输出上限的 ChatOpenAI 实例
    """
    from src.config.const import SUMMARY_MAX_TOKENS

    return build_chat(
        "summary",
        thinking=False,
        temperature=0,
        max_tokens=SUMMARY_MAX_TOKENS,
        callbacks=_content_logging_callbacks(),
    )


def get_judge_llm(model: str | None = None) -> ChatOpenAI:
    """创建 RAGAS 评估裁判专用 LLM 实例（judge 角色，temperature=0）。

    裁判独立于选手（被测系统 get_llm）评分，避免自我评价偏置；`judge` 角色独立
    缺省 dashscope（见 design D4），可用 `RAGAS_PROVIDER` / `RAGAS_LLM_MODEL` 覆盖。
    显式设请求超时（settings.RAGAS_JUDGE_TIMEOUT）：judge 走思考模型且该客户端
    无默认超时，缺省会在网络 I/O 上无限挂起（出题链即因此停滞）。

    Args:
        model: 覆盖 registry/env 解析出的裁判模型名；None 表示默认绑定。

    Returns:
        ChatOpenAI：判别裁判模型实例。
    """
    from src.config import settings

    return build_chat(
        "judge",
        model=model,
        temperature=0,
        timeout=settings.RAGAS_JUDGE_TIMEOUT,
        callbacks=_content_logging_callbacks(),
    )


def get_embeddings(model: str | None = None) -> OpenAIEmbeddings:
    """创建文本向量化模型实例（薄委托 build_embeddings）。

    批次大小取自 registry 中该 embedding 模型的声明（缺省 20）。

    Args:
        model: 兼容既有调用方的占位参数；实际模型名由 embedding 角色绑定解析。

    Returns:
        OpenAIEmbeddings 实例
    """
    return build_embeddings()


def get_rerank(model: str | None = None, top_n: int | None = None) -> DashScopeRerank:
    """创建文本重排序模型实例（薄委托 build_rerank，固定走 DashScope Rerank API）。

    Rerank 不走 LiteLLM Proxy，因为 LiteLLM 不支持 DashScope 的 rerank 端点。
    API Key 与端点由 `rerank` 角色绑定解析。

    Args:
        model: 覆盖 registry/env 解析出的模型名；None 表示默认绑定。
        top_n: 重排序后保留的文档数量；None 表示取 settings.TOP_K_RERANK。

    Returns:
        DashScopeRerank 实例
    """
    return build_rerank(model=model, top_n=top_n)
