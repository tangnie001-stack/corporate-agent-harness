"""agent 循环的四个 middleware（仅主角色装配）。

硬约束：实例随图构造一次、跨请求共享 ⇒ 不得在实例属性上保存 per-request
状态；读图状态一律 state.get(...)（middleware 收到的是映射）。
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, BaseMessage
from langfuse.decorators import langfuse_context
from langfuse.model import ModelUsage

from src.agents.graph.agent_factory import LoopState
from src.agents.graph.message_payload import (
    _extract_text,
    _messages_payload,
    _observation_output,
)
from src.config import settings
from src.config.const import MAX_DELEGATE_BONUS, TURN_INPUT_TOKEN_LIMIT
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.provider.factory import _registry
from src.infra.llm.provider.spec import resolve_role
from src.infra.llm.provider.thinking import thinking_extra_body
from src.infra.llm.request_context import current_request_ctx
from src.infra.llm.token_count import count_messages_tokens
from src.infra.llm.token_usage import estimate_usage

logger = logging.getLogger(__name__)


class SystemMessagesMiddleware(AgentMiddleware):
    """把节点 seed 的 system 段施加到每次模型调用。

    未绑定 KB 时有两段（主 system + 未绑定提示），故不能用只支持单条的
    @dynamic_prompt；改用 override(system_message=第一条, messages=[第二条, ...])。
    """

    state_schema = LoopState

    def _apply(self, request: Any) -> Any:
        """把 state 里的 system 段施加到 request（首条进 system_message，其余前插）。"""
        system_messages = request.state.get("_system_messages", []) or []
        if not system_messages:
            return request
        first = system_messages[0]
        rest = list(system_messages[1:])
        return request.override(
            system_message=first, messages=[*rest, *request.messages]
        )

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """同步钩子（脚本/测试路径）。"""
        return handler(self._apply(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """异步钩子（生产路径）。"""
        return await handler(self._apply(request))


class ModelParamsMiddleware(AgentMiddleware):
    """施加温度分档与思考开关（承接原 agent_node 调用点的内联参数）。

    档位判据取**请求上下文**的绑定状态；ctx 缺失（如 CLI 评估入口）时回退
    图状态里的 kb_id（该键由节点 seed）。KB 档用「不带 temperature 键」表达
    "不传"，沿用模型构造温度。

    ⚠️ per-call extra_body 在 langchain-openai 中**整体覆盖**构造时的 extra_body
    （_get_request_payload 浅合并），故本模型不宜在 LLM_KWARGS 里配置其他
    extra_body 参数（会被本处覆盖丢弃）。
    """

    state_schema = LoopState

    def _settings(self, request: Any) -> dict[str, Any]:
        """算出本次调用的 model_settings（档位判据见类 docstring）。

        extra_body 经 `thinking_extra_body` 产**完整** dict（含 preserve 标志）——
        per-call extra_body 在 langchain-openai 中整体覆盖构造期 extra_body，
        只有与构造期同源（同一 `thinking_extra_body`）产出才不丢字段（D8）。
        """
        ctx = current_request_ctx.get()
        if ctx is not None:
            kb_bound = ctx.kb_bound
        else:
            kb_bound = bool(request.state.get("kb_id"))
        deep_thinking = bool(request.state.get("deep_thinking"))
        spec = resolve_role("main", _registry()).provider
        extra_body = thinking_extra_body(spec, deep_thinking)
        if kb_bound:
            return {"extra_body": extra_body}
        return {
            "extra_body": extra_body,
            "temperature": settings.NON_KB_MAIN_TEMPERATURE,
        }

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """同步钩子（脚本/测试路径）。"""
        return handler(request.override(model_settings=self._settings(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """异步钩子（生产路径）。"""
        return await handler(request.override(model_settings=self._settings(request)))


class AgentTurnBudget(AgentMiddleware):
    """回合预算（**只装配给主角色**）。

    判定放 after_model 并用 jump_to=end 结束——不注入任何消息。若放
    before_model 判下轮，会多执行一次工具、末条变 ToolMessage，答案提取就会
    把工具结果当成答案。

    委派放宽：本轮声明了 delegate_task 时**先置位**再算上限，且标志跨轮保持
    （= 今天的 `delegate_used or state._delegate_used`）。
    """

    state_schema = LoopState

    def __init__(self, limit: int, bonus: int = MAX_DELEGATE_BONUS) -> None:
        """初始化。

        Args:
            limit: 基础回合上限（模型调用次数）
            bonus: 委派放宽轮数（本轮或此前声明过 delegate_task 时叠加）
        """
        super().__init__()
        self._limit = limit  # 进程级常量
        self._bonus = bonus

    def _after_model(self, state: Any) -> dict[str, Any]:
        """自增回合计数、按有效上限判定，命中时声明 jump_to=end。

        `iteration limit` 日志与 jump 解耦：达上限即记（与该轮是否声明工具调用
        无关），jump 只在该轮仍声明工具调用时返回（正常收尾本就结束，无需 jump）。
        """
        n = state.get("_turn_count", 0) + 1
        last = state["messages"][-1]
        if isinstance(last, AIMessage):
            declared = list(last.tool_calls or [])
        else:
            declared = []
        delegate_now = any(c.get("name") == "delegate_task" for c in declared)
        delegate_used = delegate_now or bool(state.get("_delegate_used"))
        if delegate_used:
            effective_max = self._limit + self._bonus
        else:
            effective_max = self._limit
        update: dict[str, Any] = {"_turn_count": n}
        if delegate_now:
            update["_delegate_used"] = True
        if n >= effective_max:
            core_logging.log_event(
                Event.ITERATION_LIMIT, query=state.get("query", ""), iteration=n
            )
            if declared:
                update["jump_to"] = "end"
        return update

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any]:
        """同步 after_model 钩子。"""
        return self._after_model(state)

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any]:
        """异步 after_model 钩子（生产路径）。"""
        return self._after_model(state)


def _extract_ai_message(response: Any) -> AIMessage | None:
    """从 ModelResponse 拆出本轮的 AIMessage。

    Args:
        response: wrap_model_call 的 handler 返回的 ModelResponse

    Returns:
        结果里第一个 AIMessage；没有则退回首条消息；结果为空返回 None
    """
    for message in response.result:
        if isinstance(message, AIMessage):
            return message
    if response.result:
        return response.result[0]
    return None


def _sent_messages(request: Any) -> list[BaseMessage]:
    """本轮**实际送入模型**的完整消息列表：system 段在前 + `request.messages`。

    为什么需要它：`SystemMessagesMiddleware` 用 `request.override(system_message=…)`
    施加 system 段，而 LangChain 的 `ModelRequest.messages` 明确**不含** system
    （该字段单独存放）⇒ 观测与用量估算若只取 `request.messages`，system 段会整体
    缺失。system 恰是 prompt 里最大的一块（实测某轮估算口径差约 40 倍），故凡
    "记录/估算本轮送了什么"的地方都必须经本函数取列表。

    静态 system（`build_agent(system="…")`）由框架直接并入 messages，
    此时 `system_message` 为 None，本函数不重复追加。

    Args:
        request: 最内层 middleware 收到的 ModelRequest

    Returns:
        `[system_message, *request.messages]`；无 system 时即 `request.messages`
    """
    system = getattr(request, "system_message", None)
    messages = list(request.messages)
    return [system, *messages] if isinstance(system, BaseMessage) else messages


class AgentSpanMiddleware(AgentMiddleware):
    """主循环观测（模型轮次日志 + Langfuse generation span + 轮内输入预算告警）。

    必须在 middleware 列表**最内层**：这样它看到的 request 已被前序
    middleware 施加过 system 与 model_settings，温度上报才与实际生效档位一致。
    观测故障必须吞异常降级，不得影响对话。

    计数口径：`iteration = state.get("_turn_count", 0) + 1`（本次调用序号；
    AgentTurnBudget 的自增在 after_model，晚于本 middleware，直接取值会少 1）。
    """

    state_schema = LoopState

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """异步钩子（生产路径）：入口记 iteration done，出口记 model turn 与观测。"""
        iteration = request.state.get("_turn_count", 0) + 1
        msgs = (
            len(request.messages) + 1
        )  # 最内层：request.messages 已含前插的其余 system
        core_logging.log_event(Event.ITERATION_DONE, iteration=iteration, msgs=msgs)
        self._measure_turn_input(request)
        turn_start = time.monotonic()
        settings_map = request.model_settings or {}
        if "temperature" in settings_map:
            temperature = settings_map["temperature"]
            temp_source = "explicit"
        else:
            temperature = settings.LLM_TEMPERATURE
            temp_source = "default"
        response = await handler(request)
        self._record_turn(
            request,
            response,
            iteration,
            temperature,
            temp_source,
            int((time.monotonic() - turn_start) * 1000),
        )
        return response

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """同步钩子（脚本/测试路径）。"""
        iteration = request.state.get("_turn_count", 0) + 1
        msgs = len(request.messages) + 1
        core_logging.log_event(Event.ITERATION_DONE, iteration=iteration, msgs=msgs)
        self._measure_turn_input(request)
        return handler(request)

    def _measure_turn_input(self, request: Any) -> None:
        """模型调用前度量本轮输入 token；达上限记 context budget high。

        输入取 `_sent_messages`（含 system 段）——与 `_record_turn` 的用量口径同源。
        本阶段**只度量、不处置**：不截断、不改写任何消息。度量失败降级（记 warning
        后继续），绝不因观测把一次成功调用变成错误。
        """
        try:
            used = count_messages_tokens(_sent_messages(request))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[agent] context budget measure failed err=%s", exc)
            return
        if used >= TURN_INPUT_TOKEN_LIMIT:
            core_logging.log_event(
                Event.CONTEXT_BUDGET_HIGH, used=used, limit=TURN_INPUT_TOKEN_LIMIT
            )

    def _record_turn(
        self,
        request: Any,
        response: Any,
        iteration: int,
        temperature: Any,
        temp_source: str,
        latency_ms: int,
    ) -> None:
        """出口观测：发 model turn 日志并写 agent_turn generation observation。

        字段口径沿用原 agent_node 调用点：模型名 / usage（缺失走 estimate_usage
        并标 usage_estimated）/ metadata（iteration / usage_estimated /
        temperature / temp_source / kb_bound）。kb_bound 与档位判据同源
        （ctx 优先，缺失回退 state.kb_id）。
        用量估算的输入是**本轮实收消息**（含 system 段，见 `_sent_messages`）——
        system 是 prompt 里最大的一块，漏掉会让估算系统性偏小。
        """
        result = _extract_ai_message(response)
        if result is None:
            return
        # 本轮实收消息（含 system 段）：观测 input 与用量估算同源，避免两处口径分叉
        sent = _sent_messages(request)
        meta = result.usage_metadata
        if meta and (meta.get("input_tokens") or meta.get("output_tokens")):
            usage_in = int(meta.get("input_tokens") or 0)
            usage_out = int(meta.get("output_tokens") or 0)
            usage_estimated = False
        else:
            est = estimate_usage(sent, _extract_text(result))
            usage_in = est.prompt_tokens
            usage_out = est.completion_tokens
            usage_estimated = True
        resp_meta = result.response_metadata
        if isinstance(resp_meta, dict):
            model_name = resp_meta.get("model_name", "")
            if not isinstance(model_name, str):
                model_name = resp_meta.get("model", "")
        else:
            model_name = ""
        if not isinstance(model_name, str):
            model_name = ""
        ctx = current_request_ctx.get()
        if ctx is not None:
            kb_bound = ctx.kb_bound
        else:
            kb_bound = bool(request.state.get("kb_id"))
        core_logging.log_event(
            Event.MODEL_TURN,
            model=model_name,
            usage_in=usage_in,
            usage_out=usage_out,
            usage_estimated=usage_estimated,
            fallback=False,
            latency_ms=latency_ms,
            iteration=iteration,
            temperature=temperature,
            temp_source=temp_source,
            kb_bound=kb_bound,
        )
        metadata = {
            "iteration": iteration,
            "usage_estimated": usage_estimated,
            "temperature": temperature,
            "temp_source": temp_source,
            "kb_bound": kb_bound,
        }
        self._write_observation(
            sent, result, model_name, usage_in, usage_out, metadata, latency_ms
        )

    def _write_observation(
        self,
        sent: list[BaseMessage],
        result: AIMessage,
        model_name: str,
        usage_in: int,
        usage_out: int,
        metadata: dict[str, Any],
        latency_ms: int,
    ) -> None:
        """写 agent_turn generation observation（命令式 span；失败吞异常降级）。

        为什么不用 `langfuse_context.update_current_observation`：middleware 里
        `get_current_observation_id()` 指向外层 `chat_turn` span（实测），
        update_current_observation 会改写该 span 而非新建 generation 观察，
        且 generation 专属字段（model/usage）在 span 上被静默忽略 ⇒ 回退命令式。
        起止时刻由本次调用耗时反推，使 generation 时长与今天 @observe 包节点一致；
        `completion_start_time` 在 middleware 内不可观测（无 chunk 可见性），
        故不设置。

        Args:
            sent: 本轮实收消息（含 system 段，见 `_sent_messages`）—— 作为观测
                `input`，使 trace 能还原"模型到底收到了什么"
        """
        if not settings.LANGFUSE_ENABLE:
            return
        try:
            client = langfuse_context.client_instance
            now = datetime.now(UTC)
            start_time = now - timedelta(milliseconds=latency_ms)
            generation = client.generation(
                trace_id=langfuse_context.get_current_trace_id(),
                parent_observation_id=langfuse_context.get_current_observation_id(),
                name="agent_turn",
                start_time=start_time,
                model=model_name,
                input=_messages_payload(sent),
                output=_observation_output(result),
                # ModelUsage 是 TypedDict 且字段声明为 Optional（键仍算必填），
                # pyright 误判部分键构造非法；运行时 TypedDict 调用即普通 dict，SDK 接受
                usage=ModelUsage(  # type: ignore[reportCallIssue]
                    input=usage_in,
                    output=usage_out,
                    total=usage_in + usage_out,
                ),
                metadata=metadata,
            )
            generation.end(end_time=now)
        except Exception as exc:  # 观测失败不得影响对话
            # 本模块 logger 是 stdlib logging（非 loguru），故用 %s 占位符
            logger.warning(
                "[agent] agent_turn observation failed err=%s", exc, exc_info=True
            )
