"""工具调用 → Langfuse span 采集器（事件流驱动，请求内私有）。

消费 `_run_generation` 里既有的 `graph.astream_events(...)` 事件流：以节点级
`on_chain_start/end(name=="tools")` 开合该轮的父 span，再把 `on_tool_start/end/error`
按 `run_id` 配对成子 span。

委派域（scope=delegate）：每次委派一个实例，先 open_delegate_span 再 consume 子代理
事件流；委派父 span 名为 `delegate`（无前缀，标注 delegate_id / skill），`"delegate:"`
前缀只加在该域的轮次 span 与工具 span 名上，便于在 trace 上区分归属。

三条不变量（改动时不得破坏）：
1. **不按工具名分支、`data.output` 原样透传** —— 对工具实现无感，MCP 工具经统一入口
   进 ToolNode 即自动覆盖；
2. span 一律用 `Langfuse.span(trace_id=...)` 建，**不调用 `client.trace()`** ——
   后者会无条件覆盖 trace 的 `timestamp`；
3. `close()` 必须被调用（挂 `finally`）—— 取消 / 异常路径不关的 span 会永远悬空。
"""

import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import ToolMessage
from langfuse.types import TraceContext

from src.infra.llm.tracing import get_client as _lf_get_client

logger = logging.getLogger(__name__)

# 事件名与 `src/agents/graph/state.py::LangGraphEvent` 同值。此处刻意写字面量而不从
# agents 层 import：infra 不应反向依赖 agents（同值字符串是稳定契约）。
_EV_CHAIN_START = "on_chain_start"
_EV_CHAIN_END = "on_chain_end"
_EV_TOOL_START = "on_tool_start"
_EV_TOOL_END = "on_tool_end"
_EV_TOOL_ERROR = "on_tool_error"

# 只认该节点下的事件（与 `_convert_event` 的既有口径一致）。
# **不得**改用 `checkpoint_ns` 判空：实测 on_tool_* 的 checkpoint_ns 是
# `tools:<uuid>`（非空），照此过滤会丢掉全部工具事件。
_TOOLS_NODE = "tools"

_DELEGATE_SPAN = "delegate"  # 委派父 span 名（与 scope=delegate 对应）


def _now() -> datetime:
    """当前 UTC 时间（span 的起止时刻）。"""
    return datetime.now(UTC)


class ToolTraceCollector:
    """把工具事件转成 Langfuse span 的请求内私有采集器。

    每次请求 new 一个、绝不共享（跨事件累积状态 + 并发隔离）。

    `client` 为 None 时惰性取 `langfuse_context.client_instance`（**不得**
    `new Langfuse()`：那会绕过开关与关停 flush）。构造参数见 `__init__`。
    """

    def __init__(
        self,
        enabled: bool,
        trace_id: str,
        client: Any = None,
        scope: str = "main",
        parent_span: Any = None,
        name_prefix: str = "",
    ) -> None:
        """初始化采集器。

        Args:
            enabled: 是否产出（取自 settings.LANGFUSE_ENABLE）
            trace_id: 本轮 trace id
            client: Langfuse 客户端；None 时惰性取单例
            scope: 该采集器所属域（main=主图；delegate=fork 子代理），记录后供调用方 /
                排查辨识；当前实现不据它分支，委派域由 open_delegate_span 与
                name_prefix 落地
            parent_span: 委派父 span（scope=delegate 时由 open_delegate_span 产出并回填）
            name_prefix: span 名前缀（委派域传 "delegate:"，便于在 trace 上区分归属）
        """
        self._enabled = enabled and bool(trace_id)
        self._trace_id = trace_id
        self._client = client
        self._scope = scope
        self._parent_span = parent_span
        self._name_prefix = name_prefix
        self._open: dict[str, Any] = {}  # run_id -> 尚未结束的工具 span
        self._round: Any = None  # 当前 tools 父 span
        self._delegate_span: Any = None  # 委派父 span（仅 scope=delegate）
        self._tools_used: set[str] = set()  # 本轮实际调用过的工具名（去重）

    # ---------- 对外 ----------

    @property
    def tools_used(self) -> list[str]:
        """本轮实际调用过的工具名（去重、稳定排序）。

        它写入 trace 元数据而非 span，故观测关闭（enabled=False）时仍记录。
        """
        return sorted(name for name in self._tools_used if name)

    def open_delegate_span(self, delegate_id: str, skill: str) -> Any | None:
        """开委派父 span（标注 delegate_id / skill），供该次委派的工具 span 挂靠。

        Args:
            delegate_id: 本次委派短 id
            skill: 被调用的 skill 名（通用委派传占位名）

        Returns:
            委派父 span；未启用或建 span 失败时返回 None（观测失败不得影响对话）
        """
        if not self._enabled:
            return None
        try:
            self._delegate_span = self._get_client().start_observation(
                trace_context=TraceContext(trace_id=self._trace_id),
                name=_DELEGATE_SPAN,
                as_type="span",
                metadata={"delegate_id": delegate_id, "skill": skill},
            )
        except Exception:
            logger.warning("[tool_trace] open delegate span failed", exc_info=True)
            return None
        self._parent_span = self._delegate_span
        return self._delegate_span

    def consume(self, item: Any) -> None:
        """事件循环每项调一次。

        工具名无条件记录（进 trace 元数据，与开关、节点无关）；span 相关处理
        只对 `langgraph_node == "tools"` 的事件生效，其余事件直接返回。

        Args:
            item: `astream_events` 的单项（dict）
        """
        if not isinstance(item, dict):
            return
        # 工具名先于开关与节点过滤记录：它只进 trace 元数据，不依赖 span 是否产出。
        if item.get("event") == _EV_TOOL_START:
            name = str(item.get("name", ""))
            if name:
                self._tools_used.add(name)
        if not self._enabled:
            return
        metadata = item.get("metadata") or {}
        if metadata.get("langgraph_node") != _TOOLS_NODE:
            return
        kind = item.get("event", "")
        if kind == _EV_CHAIN_START:
            if item.get("name") == _TOOLS_NODE:
                self._open_round()
        elif kind == _EV_CHAIN_END:
            if item.get("name") == _TOOLS_NODE:
                self._close_round()
        elif kind == _EV_TOOL_START:
            self._on_tool_start(item)
        elif kind == _EV_TOOL_END:
            self._on_tool_end(item)
        elif kind == _EV_TOOL_ERROR:
            self._on_tool_error(item)

    def close(self) -> None:
        """收尾兜底：关闭所有未结束的 span（取消 / 异常路径必经）。"""
        for span in list(self._open.values()):
            self._end_span(span)
        self._open.clear()
        self._close_round()
        if self._delegate_span is not None:
            span, self._delegate_span = self._delegate_span, None
            self._parent_span = None
            self._end_span(span)

    # ---------- 扩展点（MCP 接入时在此加逻辑，本期不实现） ----------

    def _normalize_input(self, item: dict) -> Any:
        """事件 → span 入参。

        Args:
            item: 工具事件

        Returns:
            事件的 `data.input`（LLM 可见的干净实参，不含 InjectedState）
        """
        return (item.get("data") or {}).get("input")

    def _normalize_output(self, item: dict) -> Any:
        """事件 → span 输出。

        `on_tool_end` 的 `data.output` 在工具路径下是 `ToolMessage` 对象 —— 必须显式
        取字段后再写，整对象交给序列化器会落成不可读的东西。

        Args:
            item: 工具事件

        Returns:
            字符串原样返回；`ToolMessage` 拆成
            `{tool_call_id, name, content}`；其他类型原样返回
        """
        output = (item.get("data") or {}).get("output")
        if output is None:
            return None
        if not isinstance(output, ToolMessage):
            return output
        name = output.name if output.name is not None else ""
        return {
            "tool_call_id": output.tool_call_id,
            "name": name,
            "content": output.content,
        }

    # ---------- 内部 ----------

    def _get_client(self) -> Any:
        """取 Langfuse 客户端单例（与 configure / flush 同源）。"""
        if self._client is None:
            self._client = _lf_get_client()
        return self._client

    def _open_round(self) -> None:
        """开该轮的 tools 父 span（同一轮只开一次）。"""
        if self._round is not None:
            return
        parent_id = None
        if self._parent_span is not None:
            parent_id = self._parent_span.id
        try:
            self._round = self._get_client().start_observation(
                trace_context=(
                    TraceContext(trace_id=self._trace_id, parent_span_id=parent_id)
                    if parent_id
                    else TraceContext(trace_id=self._trace_id)
                ),
                name=self._name_prefix + _TOOLS_NODE,
                as_type="span",
            )
        except Exception:  # 观测失败不得影响对话
            logger.warning("[tool_trace] open round span failed", exc_info=True)

    def _close_round(self) -> None:
        """关该轮父 span。"""
        span, self._round = self._round, None
        if span is not None:
            self._end_span(span)

    def _on_tool_start(self, item: dict) -> None:
        """开一条工具 span，按 run_id 记账。"""
        if self._round is not None:
            parent_id = self._round.id
        else:
            parent_id = None
        try:
            span = self._get_client().start_observation(
                trace_context=(
                    TraceContext(trace_id=self._trace_id, parent_span_id=parent_id)
                    if parent_id
                    else TraceContext(trace_id=self._trace_id)
                ),
                name=self._name_prefix + str(item.get("name", "")),
                as_type="tool",
                input=self._normalize_input(item),
            )
        except Exception:
            logger.warning("[tool_trace] open tool span failed", exc_info=True)
            return
        self._open[str(item.get("run_id", ""))] = span

    def _on_tool_end(self, item: dict) -> None:
        """按 run_id 取回 span 并写入返回值。"""
        span = self._open.pop(str(item.get("run_id", "")), None)
        if span is None:
            return
        self._end_span(span, output=self._normalize_output(item))

    def _on_tool_error(self, item: dict) -> None:
        """工具抛错：标记 ERROR 并写入错误信息（此路径没有 on_tool_end）。"""
        span = self._open.pop(str(item.get("run_id", "")), None)
        if span is None:
            return
        data = item.get("data") or {}
        self._end_span(
            span,
            output=data.get("input"),
            level="ERROR",
            status_message=str(data.get("error", "")),
        )

    def _end_span(
        self,
        span: Any,
        *,
        output: Any = None,
        level: str = "DEFAULT",
        status_message: str = "",
    ) -> None:
        """关一条 span；异常吞掉只记 warning。"""
        try:
            span.update(output=output, level=level, status_message=status_message)
            span.end()
        except Exception:
            logger.warning("[tool_trace] end span failed", exc_info=True)
