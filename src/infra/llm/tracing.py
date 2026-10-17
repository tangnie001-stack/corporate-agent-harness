"""Langfuse 接线基础设施 —— 开关、flush 与 trace id 校验的唯一入口。

本模块只做三件事，不含任何业务语义：

1. **开关**：把 `settings.LANGFUSE_ENABLE` 翻译成 SDK 的启用状态。服务侧由
   `src/main.py` 的 lifespan 调用，CLI 侧由 `src/cli/eval_deepeval.py` 自行调用
   —— CLI 不经 lifespan，漏掉它会让 CLI 完全脱离开关控制。
2. **flush**：SDK 默认批量上报，进程退出前不 flush 会丢最后一批缓冲事件。
3. **trace id 校验**：入站 `X-Trace-ID` / `?trace_id` 是不可信输入，接线后它会
   成为 Langfuse 的 trace 主键（`client.trace(id=...)` 是 upsert）。不合法必须
   拦在写入之前 —— 客户端对 id 零校验，服务端拒绝时异常会被 SDK 吞掉只记日志，
   表现为 trace 静默消失。
"""

import re
import uuid
from typing import Final

from langfuse.decorators import langfuse_context

from src.config import settings

#: 入站 trace id 的白名单。最终字符集须与服务端实际接受范围对齐后钉死。
TRACE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


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
        形如 `trace_<uuid4>` 的标识，与响应头 / 日志 / SSE 三处共用
    """
    return f"trace_{uuid.uuid4()}"


def configure_tracing() -> None:
    """按当前开关状态配置 SDK（幂等）。

    开关在**调用时**从 `settings` 读取，不在导入时冻结 —— 否则测试无法
    monkeypatch、CLI 与服务也无法各自决定。
    """
    langfuse_context.configure(enabled=settings.LANGFUSE_ENABLE)


def flush_tracing() -> None:
    """把 SDK 缓冲的事件强制上报（幂等；关闭状态下是廉价 no-op）。"""
    langfuse_context.flush()
