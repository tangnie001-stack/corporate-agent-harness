"""Langfuse 接线基础设施 —— 开关、flush 与 trace id 校验的唯一入口。

本模块只做三件事，不含任何业务语义：

1. **开关**：`settings.LANGFUSE_ENABLE` 是应用侧总开关（`tracing_enabled()`），
   各 span 创建点在最外层判断；同时把 `LANGFUSE_TRACING_ENABLED` 同步进进程环境，
   作为 SDK 侧兜底。SDK 5 没有运行时 `configure`，且 `get_client()` 是单例，
   故 env 必须在**首次取客户端之前**写入 —— 本模块在 import 期完成。
2. **flush**：SDK 默认批量上报，进程退出前不 flush 会丢最后一批缓冲事件。
3. **trace id 校验**：入站 `X-Trace-ID` / `?trace_id` 是不可信输入，且它同时是
   Langfuse 的 trace 主键。v4 走 OTLP，trace_id 必须满足 W3C Trace Context：
   **32 位小写十六进制**；SDK 对非法值只 warning 并忽略，表现为日志与 trace
   静默失联，故必须拦在写入之前。
"""

import functools
import inspect
import logging
import os
import re
import uuid
from collections.abc import Callable
from typing import Any, Final

from src.config import settings
from src.infra.llm.trace_context import current_trace_id

logger = logging.getLogger(__name__)

#: 入站 trace id 的白名单：32 位小写 hex（W3C Trace Context trace-id）。
TRACE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")

# 开关的 SDK 侧兜底：必须在首次 get_client() 之前写入，否则单例已按默认值构造。
if not settings.LANGFUSE_ENABLE:
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


def is_valid_trace_id(value: str) -> bool:
    """判断入站 trace id 是否合法。

    Args:
        value: 来自请求头 / 查询参数的原始值

    Returns:
        True 表示可原样使用；False 表示须丢弃并服务端重生成
    """
    return TRACE_ID_PATTERN.fullmatch(value) is not None


def new_trace_id() -> str:
    """生成服务端 trace id。

    Returns:
        32 位小写十六进制标识，与响应头 / 日志 / SSE / Langfuse 四处共用
    """
    return uuid.uuid4().hex


def tracing_enabled() -> bool:
    """应用侧是否产出观测（每处 span 创建点的前置判断）。"""
    return bool(settings.LANGFUSE_ENABLE)


def get_client() -> Any:
    """取 Langfuse 共享客户端（惰性单例，受 env 开关与 flush 生命周期约束）。"""
    from langfuse import get_client as _get_client

    return _get_client()


def observe_with_trace(name: str, *, as_type: str = "span") -> Callable[[Any], Any]:
    """在**指定 trace** 下开根 observation 的装饰器（替代 v2 的 observe kwarg 传 id）。

    v2 用 `@observe(langfuse_observation_id=...)` 把 trace id 塞给装饰器；v5 已移除该
    机制。这里用 `start_as_current_observation(trace_context=...)` 显式绑定，保证
    日志里的 trace_id 与 Langfuse trace id 一致。
    """

    def decorate(func: Any) -> Any:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not tracing_enabled():
                    return await func(*args, **kwargs)
                from langfuse.types import TraceContext

                trace_id = current_trace_id.get() or new_trace_id()
                with get_client().start_as_current_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name=name,
                    as_type=as_type,
                ):
                    return await func(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not tracing_enabled():
                return func(*args, **kwargs)
            from langfuse.types import TraceContext

            trace_id = current_trace_id.get() or new_trace_id()
            with get_client().start_as_current_observation(
                trace_context=TraceContext(trace_id=trace_id),
                name=name,
                as_type=as_type,
            ):
                return func(*args, **kwargs)

        return sync_wrapper

    return decorate


def configure_tracing() -> None:
    """把当前开关状态同步给 SDK 环境（幂等）。

    v5 没有 `langfuse_context.configure`：这里只能保证"关闭"这一方向在
    首次取客户端前生效。开关在**调用时**从 settings 读取，不冻结。
    """
    if not settings.LANGFUSE_ENABLE:
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


def update_current_trace(
    *,
    input: Any = None,
    output: Any = None,
    metadata: Any = None,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> None:
    """v2 `update_current_trace` 的 v5 等价实现。

    v5 没有 trace 级"随时补写"的公开接口：trace 属性在 OTLP 里由根 span 承载。
    故分两路：
    - input / metadata（含大对象，如检索快照）→ 写当前根 span 的 observation 属性；
      读取层负责把根 span metadata 合并回 trace 视图（design D12）。
    - user_id / session_id / tags → 经 `propagate_attributes` 写当前 span 的 trace 级属性
      （SDK 限制：仅字符串、单值 ≤200 字符、低基数）。

    失败只告警，绝不向业务路径抛错。
    """
    if not tracing_enabled():
        return
    try:
        client = get_client()
        updates: dict[str, Any] = {}
        if input is not None:
            updates["input"] = input
        if output is not None:
            updates["output"] = output
        if metadata is not None:
            updates["metadata"] = metadata
        if updates:
            client.update_current_span(**updates)
        if user_id or session_id or tags:
            from langfuse import propagate_attributes

            with propagate_attributes(
                user_id=user_id or None,
                session_id=session_id or None,
                tags=tags or None,
            ):
                pass
    except Exception as exc:  # noqa: BLE001  # 观测旁路
        logger.warning("[tracing] update_current_trace failed err=%s", exc)


def flush_tracing() -> None:
    """把 SDK 缓冲的事件强制上报（幂等；关闭状态下是廉价 no-op）。"""
    if not settings.LANGFUSE_ENABLE:
        return
    try:
        get_client().flush()
    except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不得影响退出路径
        logger.debug("[tracing] flush failed err=%s", exc)
