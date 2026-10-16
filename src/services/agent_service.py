"""Agent 服务 — LangGraph 图生命周期管理。

职责：
1. 初始化并编译 StateGraph
2. 调用 graph.astream_events() 执行
3. 将 LangGraph 事件转换为 SSE 事件（双路合并：graph 事件 + ask_user 澄清）
4. 异常边界处理和 abort 信号联动
"""

import asyncio
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

from langchain_core.messages import BaseMessage
from langchain_core.runnables.schema import StreamEvent
from langfuse.decorators import langfuse_context, observe
from langgraph.graph.state import CompiledStateGraph

from src.agents.graph.state import (
    AgentState,
    LangGraph,
    LangGraphEvent,
    LangGraphKey,
    LangGraphNode,
)
from src.agents.graph.workflow import build_graph
from src.agents.skills.models import SkillContext
from src.agents.skills.prefix import parse_prefix
from src.agents.skills.rendering import render_skill_body
from src.agents.tools.rag_tools import make_rag_tools
from src.chat.manager import ChatManager
from src.chat.streaming import (
    StreamingRunManager,
    _subscribe_events,
    streaming_manager,
)
from src.config import TOP_K_RERANK, settings
from src.config.const import (
    CHANNEL_DEFAULT,
    SKILL_INJECTION_PREFIX,
    SSEInteractionTexts,
)
from src.config.prompts import loader
from src.core import logging as core_logging
from src.core.log_events import Event, Signal
from src.infra import retry_policy
from src.infra.db.repos import KbRepo
from src.infra.db.vector_store import VectorStore
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.prompt_manager import PromptManager
from src.infra.llm.request_context import RequestContext
from src.infra.llm.tool_trace import ToolTraceCollector
from src.infra.llm.trace_context import current_channel, current_trace_id
from src.services.capability_service import CapabilityService
from src.utils.sse import (
    SSEAbstentionEvent,
    SSEAgentUsedEvent,
    SSEAskUserEvent,
    SSECitationEvent,
    SSEDelegateEvent,
    SSEErrorEvent,
    SSEEvent,
    SSEModelInfoEvent,
    SSEReasoningDeltaEvent,
    SSEStatusEvent,
    SSETokenEvent,
)


class _EndMarker:
    """事件源正常结束哨兵，标记 queue 中不再有新事件。"""


class _ErrorMarker:
    """事件源异常哨兵，携带原始异常。"""

    def __init__(self, error: Exception) -> None:
        self.error = error


@dataclass
class _StreamCapture:
    """单次流式执行的捕获结果（事件转换时收集，供 stream_chat 收尾使用）。

    捕获来源：
    - model_used：agent 节点 on_chat_model_end 的 response_metadata.model_name
    - final_answer / final_contexts：agent_finalize 节点 on_chain_end 的产物
    - events_log：两条转换路径（主循环 + clarify drain）经 _record_event 采集的
      全量 SSE 事件 [{"type", "payload"}]，供取消/收尾时落库（历史回放持久化源）
    """

    model_used: str = ""  # agent 节点 LLM 实际使用的模型名（空串 = 未捕获）
    final_answer: str | None = (
        None  # agent_finalize 产物中的最终回答（None = 未走到收尾节点）
    )
    final_contexts: list = field(
        default_factory=list
    )  # agent_finalize 产物中的检索上下文列表
    events_log: list[dict[str, Any]] = field(
        default_factory=list
    )  # 过程事件采集（历史回放持久化源，design D1）


# 合并队列元素类型：LangGraph 事件（StreamEvent）或工具经 ctx.clarify_channel
# 投递的事件 dict（ask_user/delegate），或哨兵
_QueueItem: TypeAlias = StreamEvent | dict | _EndMarker | _ErrorMarker  # noqa: UP040  # 既存代码：改写为 `type` 语句改变惰性求值语义，非本 change 范围


def _extract_model_name(output) -> str:
    """从 on_chat_model_end 的 output 提取模型名。

    Args:
        output: LLM 调用输出（AIMessage 或兼容对象）

    Returns:
        模型名；output 非 BaseMessage 或 response_metadata 无 model_name/model 时返回空字符串
    """
    if not isinstance(output, BaseMessage):
        return ""
    response_metadata = output.response_metadata
    if not isinstance(response_metadata, dict):
        return ""
    model_name = response_metadata.get("model_name")
    if isinstance(model_name, str):
        return model_name
    model = response_metadata.get("model")
    if isinstance(model, str):
        return model
    return ""


def _is_abstention(state: AgentState) -> bool:
    """abstention 判定（防御式）：命中拒答标记 且 不含 [n] 引用标记 才视为纯拒答。

    与 format_node 的拒答检测保持一致：web 兜底回答即使混入"未在文档中找到"
    措辞，只要带了引用标记就保留引用，不触发 SSEAbstentionEvent（避免
    "既发引用又发转人工"的矛盾 UX）。

    Args:
        state: agent 循环结束后的最终状态

    Returns:
        True 表示应提示转人工（answer 包含 SSEInteractionTexts.ABSTENTION_MARKERS
        任一标记 且 不含 [n] 引用标记）；闲聊/概念问答等未触发检索但正常作答的
        场景不再误判
    """
    has_abstention_marker = any(
        marker in state.answer for marker in SSEInteractionTexts.ABSTENTION_MARKERS
    )
    has_citation_marker = re.search(r"\[\d+\]", state.answer) is not None
    return has_abstention_marker and not has_citation_marker


def _tool_detail_from_input(item: StreamEvent | dict, key: str) -> str | None:
    """从 LangGraph on_tool_start 事件的 data.input 提取工具入参为可读 detail。

    Args:
        item: astream_events 事件 dict（ToolStart 事件，含 data.input 入参）
        key: 要展示的入参键（retrieve_kb→"query"；search_web→"queries"）

    Returns:
        detail 字符串（如 'query=腾讯2024年报' / 'queries=["腾讯 2023 年报", ...]'）；
        input 缺失或 key 不在 input 时返回 None
    """
    data = item.get(LangGraphKey.DATA) or {}
    tool_input = data.get("input") or {}
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        shown = [str(v)[:40] for v in value]
        return f"{key}={shown!r}"
    text = str(value)[:40]
    return f"{key}={text}"


def _convert_event(
    item: _QueueItem,
    capture: _StreamCapture | None = None,
    scope: str = "main",
) -> list[SSEEvent]:
    """把 queue 中的 item 转成 SSE 事件列表（空列表 = 无需产出）。

    scope 标识事件归属：main = 主图事件（LangGraph astream_events / 澄清通道），
    delegate = fork 子代理事件（executor/delegate_task 经 clarify_channel 投递，
    自带 type=delegate 标记）。delegate 转换不依赖 metadata.langgraph_node=="model"
    判定，杜绝子代理事件被误当主 token。scope 供后续调用方显式区分，当前实现
    下 graph 事件仅在 scope=="main" 时转换。

    queue 中混有三类 item：
    - ask_user 工具经 clarify_channel 推送的 {"type": "ask_user", "questions": [...]}
      → SSEAskUserEvent（问题卡片）
    - delegate_task fork 分支 / executor 经 clarify_channel 推送的
      {"type": "delegate", action: start|delta|end, delegate_id, skill, kind, delta,
      ok, reason} → SSEDelegateEvent（fork 过程增量，不进主 token 流/full_answer）
    - LangGraph astream_events 事件 dict（按事件类型接线，不依赖节点名映射）：
      on_chat_model_start（metadata.langgraph_node == "model"）→ SSEStatusEvent 思考中
      on_chat_model_stream（metadata.langgraph_node == "model" 且 chunk 内容非空）
      → SSETokenEvent（模型节点对 LLM 的流式 token）；chunk 带
      additional_kwargs.reasoning_content 时 → SSEReasoningDeltaEvent（思考增量）
      on_chat_model_end（metadata.langgraph_node == "model"）→ 捕获 model_used 到 capture
      on_tool_start name == "retrieve_kb" → SSEStatusEvent 检索中（detail 携带入参 query 及非默认 top_k）；
      name == "ask_user" → 不发
      on_tool_end name == "retrieve_kb" → SSEStatusEvent 检索完成
      on_chain_end name == "format" → output.citations 逐个转 SSECitationEvent
      on_chain_end name == "agent_finalize" → 捕获最终 answer/tool_contexts 到 capture
      on_chain_end name == "skill_direct" → 直出轮 answer 转 SSETokenEvent 交付，
      同时捕获 final_answer/tool_contexts 到 capture（供落库）
    其余事件忽略。

    Args:
        item: queue 中取出的原始 item（clarify_channel 推送 dict 或 LangGraph 事件 dict）
        capture: 可选的流捕获容器（model_used / 最终 state），不传则跳过捕获
        scope: 事件归属标识（main=主图；delegate=fork 子代理），graph 事件仅 main 转换

    Returns:
        list[SSEEvent]: 转换后的 SSE 事件列表；无法转换/无需产出的 item 返回空列表
    """
    if isinstance(item, dict) and item.get("type") == "ask_user":
        return [SSEAskUserEvent(questions=item.get("questions", []))]

    if isinstance(item, dict) and item.get("type") == "delegate":
        # delegate_task / executor 经 ctx.clarify_channel 投递的委派事件 dict →
        # SSEDelegateEvent（fork 过程增量 start/delta/end；不进主 token 流/full_answer，
        # 防污染不变量由"delegate 仅经本分支转 delegate 事件"保证）
        return [
            SSEDelegateEvent(
                delegate_id=item.get("delegate_id", ""),
                action=item.get("action", "delta"),
                skill=item.get("skill", ""),
                kind=item.get("kind", ""),
                delta=item.get("delta", ""),
                ok=bool(item.get("ok", True)),
                reason=item.get("reason", ""),
            )
        ]

    # 判据实际只有「节点判别键」一维：scope 形参虽在，但生产调用点均用缺省 main。
    # 主 SSE 与子代理事件的隔离由事件路由承担（var_child_runnable_config.set(None)
    # 切断回调继承 + 委派事件经显式喂事件走委派域），属**单点机制**。
    if scope != "main":
        return []

    # 哨兵类已被 _dual_stream 提前消费，此处防御性排除以收窄类型
    if not isinstance(item, dict):
        return []

    kind = item.get(LangGraphKey.EVENT, "")
    name = item.get(LangGraphKey.NAME, "")
    metadata = item.get("metadata", {}) or {}

    if kind == LangGraphEvent.CHAT_MODEL_STREAM:
        if metadata.get("langgraph_node") == "model":
            chunk = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.CHUNK)
            if chunk is not None:
                content = chunk.content
                reasoning = (chunk.additional_kwargs or {}).get("reasoning_content", "")
            else:
                content = ""
                reasoning = ""
            events = []
            if content:
                events.append(SSETokenEvent(content))
            if reasoning:
                events.append(SSEReasoningDeltaEvent(reasoning))
            return events
        return []

    if kind == LangGraphEvent.CHAT_MODEL_START:
        if metadata.get("langgraph_node") == "model":
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_AGENT,
                    SSEInteractionTexts.AGENT_STATUS_THINKING,
                )
            ]
        return []

    if kind == LangGraphEvent.CHAT_MODEL_END:
        if metadata.get("langgraph_node") == "model":
            output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT)
            model = _extract_model_name(output)
            if model and capture is not None:
                capture.model_used = model
        return []

    if kind == LangGraphEvent.TOOL_START:
        if name == "search_web":
            detail = _tool_detail_from_input(item, "queries")
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_WEB_SEARCH,
                    SSEInteractionTexts.WEB_SEARCH_STATUS_START,
                    detail=detail,
                )
            ]
        if name == "retrieve_kb":
            detail = _tool_detail_from_input(item, "query")
            tool_input = (item.get(LangGraphKey.DATA) or {}).get("input")
            if isinstance(tool_input, dict):
                top_k = tool_input.get("top_k")
                if detail is not None and top_k is not None and top_k != TOP_K_RERANK:
                    detail = f"{detail} top_k={top_k}"
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_RETRIEVE,
                    SSEInteractionTexts.AGENT_STATUS_RETRIEVING,
                    detail=detail,
                )
            ]
        # ask_user 等其他工具不发状态（composer 接管输入区）
        return []

    if kind == LangGraphEvent.TOOL_END and name == "search_web":
        return [
            SSEStatusEvent(
                SSEInteractionTexts.STAGE_WEB_SEARCH,
                SSEInteractionTexts.WEB_SEARCH_STATUS_END,
            )
        ]

    if kind == LangGraphEvent.TOOL_END and name == "retrieve_kb":
        return [
            SSEStatusEvent(
                SSEInteractionTexts.STAGE_RETRIEVE,
                SSEInteractionTexts.AGENT_STATUS_RETRIEVED,
            )
        ]

    if kind == LangGraphEvent.CHAIN_END:
        if name == LangGraphNode.Format.NAME:
            output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT) or {}
            citations = output.get(LangGraphNode.Format.CITATIONS, []) or []
            return [
                SSECitationEvent(
                    source=c.get("source", ""),
                    page=c.get("page", 0),
                    snippet=c.get("snippet", ""),
                    score=c.get("score", 0.0),
                    index=c.get("index", 0),
                    kind=c.get("kind", SSEInteractionTexts.CITATION_KIND_KB),
                    tier=c.get("tier"),
                )
                for c in citations
            ]
        if name == "agent_finalize" and capture is not None:
            output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT) or {}
            capture.final_answer = output.get("answer", "")
            capture.final_contexts = output.get("tool_contexts", [])
        if name == LangGraphNode.SkillDirect.NAME and capture is not None:
            output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT) or {}
            direct_answer = output.get("answer", "")
            capture.final_answer = direct_answer
            capture.final_contexts = output.get("tool_contexts", [])
            return [SSETokenEvent(direct_answer)]
        return []

    return []


@dataclass(frozen=True)
class AgentResolution:
    """会话智能体解析结果（design D11：来源由解析方给出，调用方不重推分支）。

    effective: 本轮生效的智能体名（空=未绑定或未注册降级）
    source: 解析来源枚举 —— bound（沿用绑定）/ new_bound（首次绑定）/
        ignored（请求与会话绑定不一致，已忽略）/ unregistered（请求名未注册，
        降级为空）/ none（请求与绑定都为空）
    """

    effective: str  # 生效值；来源：解析结果；范围：本轮请求；用途：ctx.agent/日志
    source: str  # 来源枚举；来源：解析结果；范围：本轮请求；用途：日志/单测


def _degradation_reason(exc: BaseException, ctx: RequestContext, produced: bool) -> str:
    """判定降级 reason（取值取自规格取值集合表，不得自造）。

    优先级：
    1. **已产出** ⇒ `llm_stream_interrupted`——流已开始输出就不能再声称"以下是
       已检索到的原文"（那部分用户已看到），也不重试（D4）；
    2. `classify_failure` 归类成功 ⇒ 用它的取值（`provider_unavailable` /
       `upstream_exhausted` / `vector_store_failed`）；
    3. 未归类 ⇒ 按本轮**是否已有检索结果**分 `llm_unavailable_with_context` /
       `llm_unavailable_no_context`。

    Args:
        exc: 触发的异常
        ctx: 请求上下文（`tool_contexts` 判定"是否已有检索结果"）
        produced: 本次是否已向用户产出过 token

    Returns:
        取值集合表内的 reason 字符串
    """
    if produced:
        return "llm_stream_interrupted"
    classified = retry_policy.classify_failure(exc)
    if classified is not None:
        return classified
    if ctx.tool_contexts:
        return "llm_unavailable_with_context"
    return "llm_unavailable_no_context"


def _emit_degradation(
    manager: StreamingRunManager,
    session_id: str,
    capture: _StreamCapture,
    ctx: RequestContext,
    partial_holder: dict | None,
    exc: BaseException,
    produced: bool,
) -> None:
    """失败时的降级发射（P2 / D6 / D9）：判定 → 留痕 → 经 status + 专用 stage 送达。

    动作在**本调用点**完成（D1 ③：不抽统一降级管理器）。`_degradation_reason` 判出
    **原因**（`reason`，供日志判据）；**交付产物**由文案表达 —— 两者在此**解耦**：
    「有检索结果」优先于「按 `reason` 取文案」，即只要本轮已取得检索结果，无论
    `reason` 是可用性类还是 `llm_unavailable_no_context`，用户文案一律取
    `llm_unavailable_with_context` 并附检索原文；唯一例外是流已开始输出
    （`reason = llm_stream_interrupted`，此时 SHALL NOT 重放原文）。
    「有检索原文」这一支额外做两件事：先补发 citation（引用横条），再把原文作为
    token 送进回答气泡 —— 并把 `partial_holder["text"]` 设为该原文，使异常收尾落
    `interrupted` 且**有内容**（spec 要求：不得落一条空内容却标记为正常完成的消息）。

    Args:
        manager: 事件缓冲（实时 SSE）
        session_id: 会话 ID
        capture: 流捕获容器（事件日志，随 process 持久化/历史回放）
        ctx: 请求上下文（`tool_contexts` 为检索原文来源）
        partial_holder: 共享 dict（有原文时更新 text/sources）
        exc: 触发的异常（供归类与留痕）
        produced: 本次是否已向用户产出过 token
    """
    reason = _degradation_reason(exc, ctx, produced)
    core_logging.log_event(Event.DEGRADED, reason=reason, err=str(exc))

    # 「有检索结果」优先于「按原因取文案」（spec：已有检索结果 ⇒ 用户 SHALL 收到检索原文）：
    # reason 记录**原因**（供日志判据），文案表达**交付产物** —— 两者在此解耦。
    # 例外：流已开始输出（llm_stream_interrupted）不得重放原文。
    deliver_original = reason != "llm_stream_interrupted" and bool(ctx.tool_contexts)
    if deliver_original:
        notice_text = SSEInteractionTexts.DEGRADATION_TEXTS[
            "llm_unavailable_with_context"
        ]
    else:
        notice_text = SSEInteractionTexts.DEGRADATION_TEXTS[reason]

    notice = SSEStatusEvent(
        stage=SSEInteractionTexts.STAGE_DEGRADED,
        message=notice_text,
    )
    _record_event(capture, notice)
    manager.add_event(session_id, notice.type, notice.payload_for_buffer())

    if not deliver_original:
        return

    for index, context in enumerate(ctx.tool_contexts, start=1):
        citation = SSECitationEvent(
            source=context.source,
            page=context.page,
            snippet=context.content[:200],
            score=context.score,
            index=index,
            kind=context.kind,
            tier=context.tier,
        )
        _record_event(capture, citation)
        manager.add_event(session_id, citation.type, citation.payload_for_buffer())
        if partial_holder is not None:
            partial_holder.setdefault("sources", []).append(
                {
                    "source": context.source,
                    "page": context.page,
                    "snippet": context.content[:200],
                    "kind": context.kind,
                    "index": index,
                    "tier": context.tier,
                }
            )

    passages = "\n\n".join(c.to_prompt_text() for c in ctx.tool_contexts)
    token = SSETokenEvent(token=passages)
    _record_event(capture, token)
    manager.add_event(session_id, token.type, token.payload_for_buffer())
    if partial_holder is not None:
        partial_holder["text"] = passages


def _record_event(capture: _StreamCapture | None, event: SSEEvent) -> None:
    """事件采集：追加进本次生成的私有事件日志（design D1，独立于 manager 缓冲）。

    Args:
        capture: 流捕获容器（None 时静默跳过——无消费者的采集无意义）
        event: 转换后的 SSE 事件
    """
    if capture is None:
        return
    capture.events_log.append(
        {"type": event.type, "payload": event.payload_for_buffer()}
    )


def _idle_thresholds(deep_thinking: bool) -> tuple[float, float]:
    """按思考档位取业务层静默的**两档阈值**（D15；四值见 spec / settings）。

    首事件前档更宽：该段含排队 + prefill，开思考时首个 chunk 可达数十秒，属正常。

    Args:
        deep_thinking: 本轮是否开启思考

    Returns:
        (首事件前阈值秒数, 首事件后阈值秒数)
    """
    if deep_thinking:
        return settings.MAIN_IDLE_FIRST_THINKING_S, settings.MAIN_IDLE_GAP_THINKING_S
    return settings.MAIN_IDLE_FIRST_S, settings.MAIN_IDLE_GAP_S


async def _idle_guard(
    manager: StreamingRunManager,
    session_id: str,
    capture: _StreamCapture | None,
    deep_thinking: bool,
) -> None:
    """业务层「生成任务静默」守卫（P3 / D3 / D15；**档 1：只记录、不中止**）。

    判据是**事件是否增长**（`seq` 推进），不是字节间隔 —— 模型只吐 tool_call
    而不吐 token 时字节在动、用户无感，本判据正好覆盖该情形。

    - **首事件前档**：自本守卫启动起**从未**有新事件（图事件 / 工具事件）；
    - **首事件后档**：已有事件之后再次静默。
    两档取 `_idle_thresholds()` 的对应值；两档都已由启动期断言保证 < 传输层 read。

    **档 1 的动作**：记一条 `degraded`（`reason=generation_idle`，字段 `idle_s` / `tier`）
    + 经 `status` + `STAGE_DEGRADED` 送达取值集合表里的**过程**文案；
    **不动控制流**（不取消、不降级、不改 status）。**每个静默段最多一条**：
    本层自己写入的通知不算"事件恢复"（其 `seq` 记入 `last_seq` 吸收），
    只有外部新事件才清除已通知标记。

    Args:
        manager: StreamingRunManager（读该 session 的事件缓冲；`add_event` 返回新 seq）
        session_id: 会话 ID
        capture: 流捕获容器（通知同时进事件日志，随 process 持久化与历史回放）
        deep_thinking: 本轮是否开启思考（决定取哪一档阈值）
    """
    first_threshold, gap_threshold = _idle_thresholds(deep_thinking)
    existing = manager.get_events_since(session_id, 0)
    if existing:
        last_seq = existing[-1][0]
    else:
        last_seq = 0
    last_change = time.monotonic()
    first_event_seen = False
    notified = False
    interval = settings.MAIN_IDLE_POLL_INTERVAL_S
    while True:
        await asyncio.sleep(interval)
        if not manager.buffer_exists(session_id):
            # 缓冲已随终态 TTL 清理 ⇒ 无可守护，正常退出（防"永远静默 ⇒ 无限刷"）
            return
        pending = manager.get_events_since(session_id, last_seq)
        if pending:
            last_seq = pending[-1][0]
            last_change = time.monotonic()
            first_event_seen = True
            notified = False
            continue
        idle = time.monotonic() - last_change
        if first_event_seen:
            threshold = gap_threshold
            tier = "gap"
        else:
            threshold = first_threshold
            tier = "first"
        if notified or idle < threshold:
            continue
        core_logging.log_event(
            Event.DEGRADED, reason="generation_idle", idle_s=int(idle), tier=tier
        )
        notice = SSEStatusEvent(
            stage=SSEInteractionTexts.STAGE_DEGRADED,
            message=SSEInteractionTexts.DEGRADATION_TEXTS["generation_idle"],
        )
        _record_event(capture, notice)
        # add_event 返回新 seq：把自己的通知吸收掉，避免"自我唤醒"变成周期刷屏
        last_seq = manager.add_event(
            session_id, notice.type, notice.payload_for_buffer()
        )
        notified = True


async def _dual_stream(
    event_source: AsyncIterator[StreamEvent | dict],
    queue: asyncio.Queue[_QueueItem],
    abort_signal: asyncio.Event,
    capture: _StreamCapture | None = None,
) -> AsyncGenerator[SSEEvent, None]:
    """双路合并：Task A 迭代事件源推 queue，Task B（本生成器）消费并转换产出 SSE 事件。

    queue 同时承载 graph.astream_events 事件与工具经 clarify_channel
    推送的事件 item（ask_user 澄清 / delegate 委派，应为无界 queue，避免哨兵
    put 在取消路径阻塞）。事件源异常/正常收尾统一用哨兵表达：异常 →
    _ErrorMarker 产出 SSEErrorEvent 后 break；正常结束 → _EndMarker 后 break。

    本生成器（Task B）无论正常结束还是被取消（客户端断连触发 aclose），
    finally 都会取消 Task A 并 gather 等待其退出，保证事件源不再滞留。
    abort_signal 不再由断连置位——仅 cancel 端点经 StreamingRunManager.set_abort
    置位，断连只停止消费（事件源由生产者任务自行管理）。

    Args:
        event_source: 事件源异步迭代器（graph.astream_events 返回值）
        queue: 双路事件合并队列（graph 事件 + clarify_channel 澄清 item）
        abort_signal: 请求级中止信号（由 cancel 端点置位，本函数不再写）
        capture: 可选的流捕获容器，透传给 _convert_event 收集 model_used / 最终 state

    Yields:
        SSEEvent: 事件源事件转换后的 SSE 事件（token / citation / status / error）
    """

    async def run_source():
        """Task A：迭代事件源逐个推入 queue，异常/正常收尾统一放哨兵。"""
        try:
            async for ev in event_source:
                await queue.put(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            await queue.put(_ErrorMarker(e))
        finally:
            await queue.put(_EndMarker())

    task_a = asyncio.create_task(run_source())
    try:
        while True:
            item = await queue.get()
            if isinstance(item, _EndMarker):
                break
            if isinstance(item, _ErrorMarker):
                yield SSEErrorEvent(
                    f"{SSEInteractionTexts.SSE_ERROR_PREFIX}{item.error}"
                )
                break
            for event in _convert_event(item, capture):
                yield event
    finally:
        # 断连只停止消费，不置位 abort（仅 cancel 经 manager 置位）
        task_a.cancel()
        await asyncio.gather(task_a, return_exceptions=True)


async def _drain_clarify_channel(
    ctx: RequestContext,
    manager: StreamingRunManager,
    session_id: str,
    capture: _StreamCapture,
) -> None:
    """消费 clarify_channel：ask_user 澄清 / delegate 委派事件转 SSE 事件写入缓冲。

    ask_user 工具与 verify 节点的 web_confirm 投递 {"type": "ask_user", ...}；
    delegate_task fork 分支与 executor 投递 {"type": "delegate", ...}（start/delta/end）。
    两者都进 ctx.clarify_channel（无界队列）。本任务与 _run_generation 的
    graph 事件循环并行运行，取出后经 _convert_event 转 SSEAskUserEvent /
    SSEDelegateEvent 写入 manager 缓冲，由 SSE 消费者推给前端渲染澄清卡与
    委派过程区；若缺此消费方，payload 滞留队列，前端收不到对应事件（ask_user
    等满 ASK_USER_TIMEOUT 超时）。任务随生成循环结束/异常/取消被 _run_generation
    的 finally 取消。

    Args:
        ctx: 请求上下文（clarify_channel 的单一消费方）
        manager: StreamingRunManager（事件缓冲写入）
        session_id: 会话 ID
        capture: 流捕获容器（透传 _convert_event，ask_user 不捕获字段）
    """
    while True:
        item = await ctx.clarify_channel.get()
        try:
            for event in _convert_event(item, capture):
                _record_event(capture, event)
                manager.add_event(session_id, event.type, event.payload_for_buffer())
        except Exception as exc:  # noqa: BLE001  # 单条转换失败不终止消费
            core_logging.log_event(
                Event.EVENT_CONVERT_FAILED,
                item_type=type(item).__name__,
                err=str(exc),
            )


# ─────────── 临时取证埋点（systematic-debugging 走 A：定位"长时间静默"的挂起点）───────────
# 用途：某轮生成在出网调用（web 搜索 / LLM）里长时间无任何事件推送时，打印当时所有
# asyncio 任务的栈，直接看出卡在哪个 await。定位完成后**整块删除**（含上面 import time、
# 下面的 watch_task 创建与取消）。仅诊断用，不改变任何业务逻辑。
_SILENCE_WATCH_INTERVAL_S = 10.0  # 看门狗轮询间隔（秒）
_SILENCE_WATCH_THRESHOLD_S = 60.0  # 缓冲无新事件达该秒数即打印任务栈


def _dump_task_stacks() -> str:
    """把所有 asyncio 任务的当前调用栈格式化为多行文本（临时取证用）。

    Returns:
        每个任务一行标题 + 其栈帧（文件:行号 in 函数名）；无 Python 栈时打印协程对象
    """
    lines: list[str] = []
    for task in asyncio.all_tasks():
        if task is asyncio.current_task():
            continue
        lines.append(
            f"--- task={task.get_name()} done={task.done()} cancelled={task.cancelled()}"
        )
        stack = task.get_stack()
        if not stack:
            lines.append(f"    (无 Python 栈) coro={task.get_coro()}")
            continue
        for frame in stack:
            lines.append(
                f"    {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"
            )
    return "\n".join(lines)


async def _silence_watchdog(
    manager: StreamingRunManager,
    session_id: str,
    interval: float,
    threshold: float,
) -> None:
    """临时取证：本轮缓冲长时间无新事件时，打印所有 asyncio 任务栈。

    判据用缓冲事件条数（token/status/citation 都会增长），因此出网调用期间必然静默。

    Args:
        manager: StreamingRunManager（读该 session 的事件缓冲长度）
        session_id: 会话 ID
        interval: 轮询间隔（秒）
        threshold: 静默阈值（秒），超过即打印一次并重置计时
    """
    last_len = len(manager.get_events_since(session_id, 0))
    last_change = time.monotonic()
    while True:
        await asyncio.sleep(interval)
        current_len = len(manager.get_events_since(session_id, 0))
        if current_len != last_len:
            last_len = current_len
            last_change = time.monotonic()
            continue
        idle = time.monotonic() - last_change
        if idle < threshold:
            continue
        core_logging.logger.warning(
            "[agent] TIMING silence_idle_s={} events={} session_id={}\n{}",
            int(idle),
            current_len,
            session_id,
            _dump_task_stacks(),
        )
        last_change = time.monotonic()  # 打印后重置，避免每轮轮询都刷


@observe(name="chat_turn", capture_input=False)
async def _run_generation(
    session_id: str,
    kb_id: str,
    query: str,
    history: list,
    deep_thinking: bool,
    ctx: RequestContext,
    manager: StreamingRunManager,
    graph: CompiledStateGraph | None = None,
    partial_holder: dict | None = None,
    abort_signal: asyncio.Event | None = None,
    direct_skill: str = "",
    user_id: str = "",
    summary: str = "",
) -> str:
    """后台生成任务：迭代图事件转换为带 seq 事件写入缓冲，返回完整回答。

    graph.astream_events 产出的 LangGraph 事件经 _convert_event 转成 SSE
    事件，逐个以 (seq, event.type, event.payload_for_buffer()) 写入
    manager 的 per-session 缓冲（缓冲 payload 与 to_sse 的 data: 同构）；
    token 事件同步累积 full_answer，并可选写入 partial_holder 供取消/出错
    时回读部分回答。转换期间经 _StreamCapture 捕获 model_used（agent 节点
    on_chat_model_end）与 final_answer/tool_contexts（agent_finalize 节点
    on_chain_end）；循环结束后按捕获结果补发 abstention / model_info 事件
    到缓冲（复刻旧 stream_chat 语义，供前端拒答提示与模型名展示）。
    ask_user / delegate 通道由 _drain_clarify_channel 并行消费并写入同一缓冲，
    与图事件同路推送给前端。

    Args:
        session_id: 会话 ID
        kb_id: 知识库 ID
        query: 用户查询
        history: 对话历史（不含当前 query）
        deep_thinking: 深度思考开关
        ctx: 请求上下文（含 clarify_channel / abort_signal）
        manager: StreamingRunManager（事件缓冲写入）
        graph: 图实例（测试注入用）。模块级函数无法访问 AgentService 的
            self._graph，生产侧须由调用方显式传入，None 时抛 ValueError。
        partial_holder: 可选的 {"text": str, "sources": list[dict]} 共享 dict，
            随 token 产出更新 text，随 citation 事件累积 sources
            （[{source, page, snippet, kind, index}]，历史回放重建引用用），
            供取消/出错时写 interrupted 部分回答、收尾落库引用来源；
            capture 创建后挂 events_log（events_log 的同一列表引用，取消路径
            亦持续可见），finally 写 model_name（capture.model_used）
        abort_signal: 可选的请求级中止信号（cancel 端点置位）；置位后本任务
            在循环内尽快抛 CancelledError 中断生成，交由调用方收尾落库
        direct_skill: 本轮命令行直出的 fork skill 名（默认空=常规轮）；由
            stream_chat 解析 `/xxx` 后经 launch_context 传入，写进初始 state
        user_id: 触发本轮的用户标识（请求内捕获后显式传入）。空串表示未取到，
            写入 trace 前会转成 None —— `update_current_trace` 只过滤 None、不过滤空串
        summary: 跨轮历史摘要正文（默认空=无摘要）；由 stream_chat 预取后经
            launch_context 传入，写入初始 state 供 seed 点作为独立历史消息注入

    Returns:
        完整回答（全部 token 累积结果）

    Raises:
        ValueError: graph 未传入（默认图需调用方显式注入）
        asyncio.CancelledError: abort_signal 置位时抛出（中断生成）
    """
    if graph is None:
        raise ValueError("_run_generation 需显式传 graph（默认图由调用方注入）")
    # trace 级字段：输入只写该写的（根函数入参含 ctx/manager/graph/abort_signal，
    # 自动 capture 会把内部对象序列化进 trace，故装饰器已 capture_input=False）
    # trace 级字段：只读 RequestContext，不新增取数
    if kb_id:
        kb_tag = "kb"
    else:
        kb_tag = "no_kb"
    trace_metadata: dict = {
        "agent": ctx.agent,
        "agent_display_name": ctx.agent_display_name,
        "kb_id": kb_id,
        "kb_domain": ctx.kb_domain,
        "deep_thinking": deep_thinking,
        "skill_action": ctx.skill_action,
        "loaded_skills": list(ctx.loaded_skills),
        "has_skills": ctx.has_skills,
        "channel": current_channel.get() or CHANNEL_DEFAULT,
    }
    if direct_skill:
        trace_metadata["direct_skill"] = direct_skill
    langfuse_context.update_current_trace(
        input={"query": query, "kb_id": kb_id, "deep_thinking": deep_thinking},
        session_id=session_id,
        # tags 只能加不能删（实测），故只放低基数稳定值；高基数一律进 metadata
        tags=["chat", kb_tag],
        # 空串必须转 None：SDK 的字段过滤是 `v is not None`
        user_id=user_id or None,
        metadata=trace_metadata,
    )
    initial_state = AgentState.make_initial_state(
        session_id, kb_id, query, history, deep_thinking, direct_skill, summary
    )
    capture = _StreamCapture()
    tool_trace = ToolTraceCollector(
        enabled=settings.LANGFUSE_ENABLE,
        trace_id=current_trace_id.get() or "",
    )
    full_answer = ""
    if partial_holder is not None:
        partial_holder["events_log"] = (
            capture.events_log
        )  # 共享列表引用，取消路径亦持续可见
    if abort_signal is not None and abort_signal.is_set():
        raise asyncio.CancelledError
    # 进入图事件循环前回传一次 agent_used（含本会话绑定值，前端据此即时纠正
    # 顶栏；空串=未绑定→系统默认）。须早于首个 graph 事件入缓冲
    agent_event = SSEAgentUsedEvent(agent=ctx.agent)
    manager.add_event(session_id, agent_event.type, agent_event.payload_for_buffer())
    # 每轮来源声明（design D1/D7）：复用 status，stage 用专用值；两条均写两个
    # sink（events_log 负责随 process 持久化与历史回放，manager 缓冲负责实时 SSE）。
    # 必须在创建 clarify drain 任务之前写入，否则并发 drain 会把 ask_user/delegate
    # 事件插到前面，破坏"来源声明先于澄清与委派"的顺序契约。
    if ctx.agent_display_name:
        agent_decl = SSEStatusEvent(
            stage=SSEInteractionTexts.STAGE_TURN_AGENT,
            message=SSEInteractionTexts.AGENT_IN_USE_TMPL.format(
                agent=ctx.agent_display_name
            ),
        )
        _record_event(capture, agent_decl)
        manager.add_event(session_id, agent_decl.type, agent_decl.payload_for_buffer())
    if ctx.skill_action in ("inline", "preload") and ctx.loaded_skills:
        skill_decl = SSEStatusEvent(
            stage=SSEInteractionTexts.STAGE_TURN_SKILL,
            message=SSEInteractionTexts.SKILLS_LOADED_TMPL.format(
                skills="、".join(ctx.loaded_skills)
            ),
        )
        _record_event(capture, skill_decl)
        manager.add_event(session_id, skill_decl.type, skill_decl.payload_for_buffer())
    elif ctx.skill_action == "fork" and direct_skill:
        fork_decl = SSEStatusEvent(
            stage=SSEInteractionTexts.STAGE_TURN_SKILL,
            message=SSEInteractionTexts.SKILL_IN_USE_TMPL.format(skill=direct_skill),
        )
        _record_event(capture, fork_decl)
        manager.add_event(session_id, fork_decl.type, fork_decl.payload_for_buffer())
    # 澄清通道与图事件循环并行：ask_user / web_confirm 经 clarify_channel
    # 投递的问题 payload 须转 SSE 写入缓冲，否则前端收不到澄清卡
    drain_task = asyncio.create_task(
        _drain_clarify_channel(ctx, manager, session_id, capture)
    )
    # 临时取证埋点：静默看门狗（定位完成后删除）
    watch_task = asyncio.create_task(
        _silence_watchdog(
            manager, session_id, _SILENCE_WATCH_INTERVAL_S, _SILENCE_WATCH_THRESHOLD_S
        )
    )
    try:
        async for item in graph.astream_events(
            initial_state, version=LangGraph.VERSION
        ):
            for event in _convert_event(item, capture):
                _record_event(capture, event)
                manager.add_event(session_id, event.type, event.payload_for_buffer())
                if isinstance(event, SSETokenEvent):
                    full_answer += event.token
                    if partial_holder is not None:
                        partial_holder["text"] = full_answer
                elif isinstance(event, SSECitationEvent) and partial_holder is not None:
                    # 落库保留完整引用结构（source/page/snippet/kind/index/tier），
                    # 供历史回放重建引用横条与抽屉；旧数据为 "url (第x页)" 扁平串由前端降级
                    partial_holder.setdefault("sources", []).append(
                        {
                            "source": event.source,
                            "page": event.page,
                            "snippet": event.snippet,
                            "kind": event.kind,
                            "index": event.index,
                            "tier": event.tier,
                        }
                    )
            tool_trace.consume(item)
            if abort_signal is not None and abort_signal.is_set():
                raise asyncio.CancelledError
    except Exception as e:
        # 失败降级（P2 / D6 / D9）：动作留本调用点；分类与取值来自 retry_policy + 规格表。
        # CancelledError 属 BaseException，不落此分支（取消语义由调用方收尾，不在本变更范围）。
        _emit_degradation(
            manager, session_id, capture, ctx, partial_holder, e, bool(full_answer)
        )
        raise
    finally:
        # 生成结束/异常/取消后停止澄清消费（工具等待期间图事件循环阻塞，
        # 澄清项已被并行任务即时消费，收尾时队列已空）
        drain_task.cancel()
        watch_task.cancel()  # 临时取证埋点：随生成结束一并取消
        # 取消 / 异常路径同样走到这里 —— 不关的 span 会永远悬空
        tool_trace.close()
        await asyncio.gather(drain_task, watch_task, return_exceptions=True)
        if partial_holder is not None:
            partial_holder["model_name"] = capture.model_used
    # 收尾：复刻旧 stream_chat 语义，按捕获结果补发 abstention / model_info
    # 事件（capture 在循环内经 _convert_event 填充 model_used / final_answer）
    if capture.final_answer is not None:
        final_state = AgentState(
            answer=capture.final_answer,
            tool_contexts=capture.final_contexts,
        )
        if _is_abstention(final_state):
            # abstain_after_retrieve 行为信号：绑 KB 且检索过却拒答 → 检索质量缺陷
            # （检索结果不足以支撑作答，供 P1 检索质量诊断）
            has_kb_retrieved = bool(capture.final_contexts) and bool(kb_id)
            if has_kb_retrieved:
                core_logging.retrieval_signal(
                    Signal.ABSTAIN_AFTER_RETRIEVE,
                    query,
                    0,  # iteration 非关键：capture 未存迭代数，传 0（YAGNI 不做 capture 改造）
                    kb_id=kb_id,
                    tool_context_count=len(capture.final_contexts or []),
                )
            abstention_event = SSEAbstentionEvent()
            manager.add_event(
                session_id,
                abstention_event.type,
                abstention_event.payload_for_buffer(),
            )
    if capture.model_used:
        model_info_event = SSEModelInfoEvent(
            model=capture.model_used, is_fallback=False
        )
        manager.add_event(
            session_id,
            model_info_event.type,
            model_info_event.payload_for_buffer(),
        )
    return full_answer


class AgentService:
    """图生命周期管理服务。"""

    # 可选协作方的默认契约：生产路径由 __init__ 赋值覆盖，这里声明的默认值给出
    # "无 KB 仓库 → 领域取 general"、"无工具 → 空集" 的确定语义（不用 getattr 兜底）。
    _kb_repo: KbRepo | None = None
    _tool_names: frozenset[str] = frozenset()

    def __init__(
        self,
        vector_store: VectorStore,
        chat_manager: ChatManager,
        llm=None,
        reranker=None,
        prompt_manager: PromptManager | None = None,
        kb_repo: KbRepo | None = None,
    ):
        from src.agents.presets.loader import AgentPresetLoader
        from src.agents.presets.registry import AgentPresetRegistry
        from src.agents.skills import (
            SkillExecutor,
            SkillLoader,
            SkillRegistry,
            make_delegate_task,
        )
        from src.models import get_llm, get_rerank

        self._vector_store = vector_store
        self._llm = llm or get_llm()
        self._reranker = reranker or get_rerank()
        self._chat_manager = chat_manager
        self._prompt_manager = prompt_manager or PromptManager()
        self._kb_repo = kb_repo

        # skill 委派（agent-delegation-skills）：SKILLS_DIR 环境变量覆盖，缺省项目根 skills/
        skills_dir = settings.SKILLS_DIR
        if not skills_dir:
            skills_dir = str(Path(__file__).resolve().parents[2] / "skills")
        # 智能体预设根目录：与 skills 同源推导（仓库顶层 agents/），供 fork 执行者选择
        agents_dir = Path(__file__).resolve().parents[2] / "agents"
        delegate_task_tool = None
        # fork 工具池 sink：build_graph 把当前启用工具写入本列表，SkillExecutor 的
        # 延迟 provider 读取它按 skill 白名单筛选子代理工具（打破工具集 ↔ executor
        # 循环依赖）。构造期为空，build_graph 完成后被填充
        fork_tool_pool: list = []
        # 无澄清图（非交互轮次）的独立 sink：两套图各有自己的工具池，必须分开，
        # 否则后编译的一套会覆盖前一套的池，令 fork 子代理拿到错误的可用工具集
        fork_tool_pool_no_clarify: list = []
        # skill_registry / skill_executor 提到分支外可见：直出节点（Task 8）需要二者，
        # 未命中（目录缺失/注册表为空）时置 None，直出节点退化为兜底文案
        skill_registry = None
        skill_executor = None
        # preset_registry 提升到分支外：_resolve_session_agent 绑定校验需读取，
        # skills 目录缺失/注册表为空时保持 None（绑定降级为忽略 + warning）
        preset_registry: AgentPresetRegistry | None = None
        if Path(skills_dir).exists():
            skill_registry = SkillRegistry(SkillLoader(Path(skills_dir)))
            skill_registry.reload_if_changed()  # description 在 make_delegate_task 时按当前注册表生成
            if skill_registry.names():
                preset_registry = AgentPresetRegistry(AgentPresetLoader(agents_dir))
                preset_registry.reload_if_changed()  # 同名冲突 fail-fast（配置错误）
                skill_executor = SkillExecutor(
                    self._llm,
                    tool_provider=lambda: fork_tool_pool,
                    preset_registry=preset_registry,
                )
                delegate_task_tool = make_delegate_task(skill_registry, skill_executor)
            else:
                # skills 目录存在但无任何 skill：delegate_task 不注册（描述会列空列表，注册无意义）
                core_logging.log_event(Event.DELEGATE_SKIP, reason="registry_empty")
        else:
            # skills 目录缺失（volume 未挂载/路径错）→ delegate 静默不可用需可观测
            core_logging.log_event(
                Event.DELEGATE_SKIP, reason="skills_dir_missing", skills_dir=skills_dir
            )

        self._preset_registry = preset_registry
        # skill_registry 提升到实例属性：stream_chat 解析 `/xxx` 前缀需读取
        # user_visible()/model_visible()；skills 目录缺失时保持 None（分派降级）
        self._skill_registry = skill_registry

        skill_direct_node = None
        if skill_registry is not None and skill_executor is not None:
            from src.agents.graph.skill_direct import make_skill_direct_node

            skill_direct_node = make_skill_direct_node(skill_registry, skill_executor)

        self._graph: CompiledStateGraph = build_graph(
            vector_store,
            self._llm,
            self._reranker,
            self._prompt_manager,
            delegate_task=delegate_task_tool,
            tool_sink=fork_tool_pool,
            skill_direct_node=skill_direct_node,
        )
        # 本轮实际注册的工具名（段组装与 verify 指引的条件注入判据）。
        # 取各工具的 .name（LangChain BaseTool 契约）；缺 name 是编程错误，
        # 故在装配期直接暴露，而非每请求静默跳过。
        self._tool_names: frozenset[str] = frozenset(
            str(t.name) for t in fork_tool_pool
        )
        # 非交互轮次（如告警分析）禁用 ask_user：无人可答，注册只会拖到澄清超时。
        # 图是编译期固化的，故必须**再编译一套**并按轮次选择，而非透传参数。
        # ⚠️ 必须显式拼上 make_task_tools()：build_graph 只在 tools=None 的分支
        # 追加任务工具（src/agents/graph/workflow.py:87-91），传了 tools 就不再追加。
        # 漏掉会让报警轮丢整套任务工具（行为超出"仅禁澄清"）。
        from src.agents.tools.task_tools import make_task_tools

        no_clarify_tools = [
            *make_rag_tools(
                vector_store,
                self._reranker,
                self._prompt_manager,
                delegate_task=delegate_task_tool,
                include_ask_user=False,
            ),
            *make_task_tools(),
        ]
        self._graph_no_clarify: CompiledStateGraph = build_graph(
            vector_store,
            self._llm,
            self._reranker,
            self._prompt_manager,
            tools=no_clarify_tools,
            delegate_task=delegate_task_tool,
            tool_sink=fork_tool_pool_no_clarify,
            skill_direct_node=skill_direct_node,
        )
        self._tool_names_no_clarify: frozenset[str] = frozenset(
            str(t.name) for t in fork_tool_pool_no_clarify
        )
        # 能力清单服务（Task 9）：由两个注册表派生只读清单，api 层只转发
        self.capability_service = CapabilityService(skill_registry, preset_registry)
        core_logging.log_event(Event.SERVICE_READY)

    def _preload_skills_text(self, skill_names: list[str]) -> tuple[str, list[str]]:
        """按预设声明的 skills 顺序渲染并拼接各技能正文（隐藏注入用）。

        Args:
            skill_names: 预设 frontmatter 的 skills 列表（声明顺序即渲染顺序）

        Returns:
            (用 "\\n\\n" 拼接的正文, 成功解析的技能名列表——去重、按声明顺序保序；
            全部查不到时正文为空串、名单为空列表)
            只注入 inline 正文；fork skill 被跳过（记 skill preload skip）——
            fork 正文属于子代理 prompt，注入主 agent 是语义错配。
        """
        parts: list[str] = []
        resolved: list[str] = []
        for name in skill_names:
            if self._skill_registry is None:
                break
            record = self._skill_registry.get(name)
            if record is None:
                core_logging.log_event(
                    Event.SKILL_PRELOAD_SKIP, skill=name, reason="not_found"
                )
                continue
            body = record.inline_prompt
            if not body:
                # fork skill 的正文是写给子代理的任务 prompt，注入主 agent 属语义错配
                # 且与 fork 的"正文不进主 agent 上下文"冲突；需要用 fork skill 时走委派
                core_logging.log_event(
                    Event.SKILL_PRELOAD_SKIP, skill=name, reason="not_inline"
                )
                continue
            parts.append(render_skill_body(body, ""))
            if name not in resolved:
                resolved.append(name)
        return "\n\n".join(parts), resolved

    def _preload_if_first_round(
        self, effective_agent: str, history: list
    ) -> tuple[str, list[str]]:
        """首轮预加载判定：仅在首轮、已绑定预设且该预设声明 skills 时给出待注入正文。

        Args:
            effective_agent: 本会话生效的智能体名（空=未绑定）
            history: 本轮的历史消息（若本轮已有 inline `/xxx` 注入，T5 已往其中追加条目→非空）

        Returns:
            (预加载正文, 成功解析的技能名列表)；任一条件不满足返回 ("", [])
        """
        if history:
            return "", []
        if not effective_agent:
            return "", []
        if self._preset_registry is None:
            return "", []
        preset = self._preset_registry.get(effective_agent)
        if preset is None or not preset.skills:
            return "", []
        return self._preload_skills_text(preset.skills)

    async def stream_chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
        deep_thinking: bool = False,
        agent: str = "",
        *,
        clarify: bool = True,
    ) -> tuple[AsyncGenerator[SSEEvent, None], dict]:
        """准备一轮生成的订阅生成器与启动上下文，不再启动后台任务。

        固定顺序（prompt 上下文正确性关键）：先取历史（不含当前 query）、
        再写 user 消息到 Redis，然后清空该 session 缓冲。生成任务的启动与
        assistant 收尾由 API 层负责（_run_with_finalize + _run_generation）：
        本方法只返回 (subscription_generator, launch_context)，让 API 层
        拿到启动上下文后再 create_task，避免任务生命周期与 SSE 消费耦合。

        Args:
            kb_id: 知识库 ID（空字符串表示不检索（未绑定 KB））
            session_id: 会话 ID
            query: 用户查询文本
            deep_thinking: 深度思考开关（默认 False）；为 True 时 agent LLM
                以思考模式调用（enable_thinking）
            agent: 请求体传入的智能体预设名（ASCII slug；空=未指定）。
                生效值经 _resolve_session_agent bind-once 解析后写入 ctx.agent
            clarify: 是否允许澄清追问（False 用于报警等非交互轮次；切换为此编译的
                无 ask_user 图，并同步段组装用的工具名集合）

        Returns:
            (subscription_generator, launch_context)：
            - subscription_generator：订阅事件缓冲的 SSE 消费者生成器
              （status / token / citation / ask_user / error / done 事件）
            - launch_context：启动后台任务所需的上下文 dict，键包括
              history / ctx / graph / session_id / kb_id / query / deep_thinking / agent
        """
        # 顺序约束（prompt 上下文正确性关键）：先取历史（不含当前 query），
        # 再写 Redis user
        history = await self._chat_manager.get_history_async(session_id) or []
        await self._chat_manager.add_message_async(session_id, "user", query)

        # 跨轮历史摘要：在此预取（本层持有 ChatManager），经 launch_context 传到
        # 图状态——图节点不访问存储。
        summary_text, _ = await self._chat_manager.get_summary_async(session_id)

        # 新一轮生成前清空该 session 缓冲，避免同一会话二次提问回放上一轮事件
        streaming_manager.clear_buffer(session_id)

        ctx = RequestContext(session_id=session_id)
        ctx.kb_id = kb_id
        ctx.kb_bound = bool(kb_id)
        ctx.tool_names = self._tool_names if clarify else self._tool_names_no_clarify
        # 知识库领域：base 段三选一的依据。领域非法（无对应 base 模板）时回落到保留值
        # general 并记 warning，不阻断请求 —— 写入侧已拒绝非法值（KBService._require_known_domain），
        # 此处兜的是"模板被删/改名后存量库指向了不存在的领域"这类跨版本情形。
        if kb_id and self._kb_repo is not None:
            stored_domain = await self._kb_repo.get_kb_domain(kb_id)
            if loader.has_domain(stored_domain):
                ctx.kb_domain = stored_domain
            else:
                ctx.kb_domain = "general"
                core_logging.log_event(
                    Event.KB_DOMAIN_FALLBACK, kb_id=kb_id, domain=stored_domain
                )
        ctx.deep_thinking = (
            deep_thinking  # fork thinking 跟随的请求级来源（executor 读取）
        )
        launch_context = {
            "history": history,
            "ctx": ctx,
            "graph": self._graph if clarify else self._graph_no_clarify,
            "session_id": session_id,
            "kb_id": kb_id,
            "query": query,
            "deep_thinking": deep_thinking,
            "summary": summary_text,
        }
        # bind-once：读会话已绑定值，解析本轮生效智能体（首轮绑定 / 沿用 /
        # 不一致忽略 + warning），写入 ctx.agent 供 fork 执行者选择与 agent_used 回传
        bound_raw = await self._chat_manager.get_session_agent_async(session_id)
        resolution = await self._resolve_session_agent(
            session_id, agent, bound=bound_raw
        )
        effective_agent = resolution.effective
        ctx.agent = effective_agent
        launch_context["agent"] = effective_agent
        # `/xxx` 前缀分派：只认 user_visible() 的名字（user-invocable:false 禁止
        # 用户调用）。skills 目录缺失时注册表为 None，退化为无技能可解析。
        if self._skill_registry is not None:
            known = {r.name for r in self._skill_registry.user_visible()}
            has_skills = bool(self._skill_registry.model_visible())
        else:
            known = set()
            has_skills = False
        ctx.known_skill_names = known
        ctx.has_skills = has_skills
        # persona：T4 的 build_system_prompt 人设层来源（graph 层拿不到 registry，
        # 故在服务层解析后经 RequestContext 传递）
        session_preset = None
        if self._preset_registry is not None and effective_agent:
            session_preset = self._preset_registry.get(effective_agent)
        if session_preset is not None:
            ctx.persona = session_preset.system_prompt
            ctx.agent_display_name = session_preset.display_name or session_preset.name
        else:
            ctx.persona = ""
            ctx.agent_display_name = ""
        # 预设与知识库领域不一致：属用户自选行为，**记日志但不阻断**（spec 第三 scenario）。
        # 三选一替换语义下领域方法此时不参与组装，这是明确接受的代价，不是缺陷。
        if ctx.persona and kb_id and ctx.kb_domain != "general":
            core_logging.log_event(
                Event.AGENT_DOMAIN_MISMATCH, agent=effective_agent, domain=ctx.kb_domain
            )
        # [session] 生效智能体与解析来源（design D11 #2）：请求与绑定都为空时不记，
        # 避免每轮噪声（log_event 级别由 EventSpec 固定，不能逐次降级为 debug）
        if agent or bound_raw:
            core_logging.log_event(
                Event.AGENT_RESOLVED,
                requested=agent,
                bound=bound_raw,
                effective=effective_agent,
                source=resolution.source,
                persona_applied=bool(ctx.persona),
            )

        parsed = parse_prefix(query, known, self._skill_registry)
        direct_skill = ""
        effective_query = query
        skill_action = "none"
        loaded_skills: list[str] = []
        if parsed.kind == "known":
            record = parsed.record
            if record is not None and record.context == SkillContext.INLINE:
                # inline：渲染正文持久化注入，本轮即追加进 history，主 agent 单轮
                injected_text = render_skill_body(
                    record.inline_prompt or "", parsed.task
                )
                entry = await self._inject_skill_message(
                    session_id, kb_id, injected_text
                )
                history = history + [entry]
                effective_query = parsed.task
                skill_action = "inline"
                loaded_skills = [record.name]
                # [session] 技能正文注入事实（design D11 #5）：inline 命令触发
                core_logging.log_event(
                    Event.SKILL_INJECTED,
                    skill=record.name,
                    mode="inline",
                    chars=len(injected_text),
                    source="command",
                )
            else:
                direct_skill = parsed.skill_name
                effective_query = parsed.task
                skill_action = "fork"
        elif parsed.kind == "unknown":
            # 未知前缀复用 skill_direct 的 fail-open 通道输出"不存在 + 可用列表"
            direct_skill = parsed.skill_name
            effective_query = parsed.task
            skill_action = "unknown"
        # [session] 命令形态分派结果（design D11 #6）：普通文本轮不记（每轮噪声）
        if parsed.kind != "plain":
            record_ctx = parsed.record
            context = "none"
            if record_ctx is not None:
                context = record_ctx.context
            core_logging.log_event(
                Event.SKILL_DISPATCH,
                kind=parsed.kind,
                skill=parsed.skill_name,
                context=context,
                direct_skill=direct_skill,
            )
        launch_context["direct_skill"] = direct_skill
        launch_context["history"] = history
        launch_context["query"] = effective_query
        # 预设预绑定 skill 预加载：与 `/xxx` 走同一条持久化隐藏消息通道（T5b），
        # 仅首轮（history 空）且本轮无显式 `/xxx`（direct_skill == ""）时注入一次。
        # 仅正文非空才置 preload，避免声明「成功加载 skills：」而名单为空
        if direct_skill == "":
            preload_text, preload_names = self._preload_if_first_round(
                effective_agent, history
            )
            if preload_text:
                preload_entry = await self._inject_skill_message(
                    session_id, kb_id, preload_text
                )
                history = history + [preload_entry]
                launch_context["history"] = history
                skill_action = "preload"
                loaded_skills = preload_names
                # [session] 技能正文注入事实（design D11 #5）：预设首轮预加载；
                # 多技能时 skill 取顿号连接名列表
                core_logging.log_event(
                    Event.SKILL_INJECTED,
                    skill="、".join(preload_names),
                    mode="preload",
                    chars=len(preload_text),
                    source="preset",
                )
        ctx.skill_action = skill_action
        ctx.loaded_skills = loaded_skills
        # 主 POST 订阅不按 180s 空闲收流（长静默由任务生命周期收口，含 ask_user
        # 等待、fork 长跑等合法静默）；resume 端点（sessions/events）保留空闲兜底
        return _subscribe_events(
            session_id, streaming_manager, max_idle=None
        ), launch_context

    async def _resolve_session_agent(
        self, session_id: str, requested: str, bound: str = ""
    ) -> AgentResolution:
        """解析本会话生效的智能体名（bind-once，无 400）。

        Args:
            session_id: 会话 ID
            requested: 请求体传入的智能体名（可为空）
            bound: 会话已绑定的智能体名（空=未绑定）

        Returns:
            AgentResolution：已绑定一律生效绑定值（请求不一致 → 忽略 + warning），
            来源枚举见 dataclass docstring
        """
        if bound:
            if requested and requested != bound:
                core_logging.log_event(
                    Event.AGENT_BIND_IGNORED,
                    session_id=session_id,
                    bound=bound,
                    requested=requested,
                )
                return AgentResolution(effective=bound, source="ignored")
            return AgentResolution(effective=bound, source="bound")
        if not requested:
            return AgentResolution(effective="", source="none")
        if (
            self._preset_registry is None
            or self._preset_registry.get(requested) is None
        ):
            core_logging.log_event(
                Event.AGENT_BIND_IGNORED,
                session_id=session_id,
                bound="",
                requested=requested,
            )
            return AgentResolution(effective="", source="unregistered")
        await self._chat_manager.bind_session_agent_async(session_id, requested)
        return AgentResolution(effective=requested, source="new_bound")

    async def _inject_skill_message(
        self, session_id: str, kb_id: str, text: str
    ) -> ChatMessage:
        """把 skill 正文作为一条隐藏消息写入会话上下文（Redis + DB）。

        写入的是带 SKILL_INJECTION_PREFIX 标记的 user 消息原文：前端经
        `sessions/messages` 过滤不展示，模型经 `_split_initial_messages` 抽成独立
        HumanMessage 可见且随历史持久化跨轮生效。

        Args:
            session_id: 会话 ID
            kb_id: 当前知识库 ID（落库用，可为空）
            text: 已渲染的 skill 正文

        Returns:
            新构造的 ChatMessage；调用方应把它**追加到本轮 history**
            （否则本轮 prompt 看不到，需等下一轮才从 Redis 读回）
        """
        content = f"{SKILL_INJECTION_PREFIX}\n{text}"
        await self._chat_manager.add_message_async(session_id, "user", content)
        await self._chat_manager.save_user_async(session_id, kb_id, content)
        return ChatMessage(role="user", content=content)
