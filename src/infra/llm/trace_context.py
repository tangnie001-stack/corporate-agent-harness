"""请求级上下文变量 — 通过 contextvars 实现 per-request 数据传递。

提供 current_trace_id、current_user_id、current_session_id、current_channel、
current_eval_trace 五个 ContextVar。前四个分别在 TraceID 中间件、Auth 中间件、
各渠道入口（站点 API / 企微 handler）与 chat 生成任务入口中设置，供下游模块
自动读取，无需显式传参；current_eval_trace 仅由评测 CLI 设置。

current_channel 取值域见 `src/config/const.py` 的 `Channel`；未设置时为空串，
日志 patcher 会以 `CHANNEL_DEFAULT`（`none`）占位。

current_eval_trace 承载一次评测 run 的 run id / run trace id / 裁判模型，
仅评测 CLI 设置；未设置时为 None。
"""

from contextvars import ContextVar
from dataclasses import dataclass

current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
current_channel: ContextVar[str] = ContextVar("current_channel", default="")


@dataclass(frozen=True)
class EvalTraceContext:
    """评测 run 级上下文。

    Args:
        run_id: 时间戳 run id（与报告/CSV 同源）
        run_trace_id: 整轮 trace id（`eval_run_<timestamp>_<8hex>`）
        judge_model: 裁判模型名；构造 bridge judge 后才有值
    """

    run_id: str
    run_trace_id: str
    judge_model: str | None


current_eval_trace: ContextVar[EvalTraceContext | None] = ContextVar(
    "current_eval_trace", default=None
)
