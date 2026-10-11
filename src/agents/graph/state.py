from dataclasses import dataclass, field
from typing import Annotated, Literal

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.trace_context import current_trace_id
from src.rag.context import RAGContext


@dataclass
class AgentState:
    """LangGraph 图执行状态。"""

    # ── 输入 ──
    session_id: str = ""  # 会话 ID（多轮对话用，作为 Redis key 取历史）
    kb_id: str = ""  # 知识库 ID（空字符串 = 不检索（未绑定 KB，纯对话））
    query: str = ""  # 用户原始查询文本
    trace_id: str = field(
        default_factory=lambda: current_trace_id.get() or "unknown"
    )  # 全链路追踪 ID（自动从 contextvar 读取）
    deep_thinking: bool = False  # 深度思考开关（来源：/chat/stream?deep_thinking；用途：agent LLM enable_thinking 参数）
    direct_skill: str = ""  # 本轮命令行直出的 fork skill 名（来源：AgentService 解析 /xxx 后注入初始 state，Plan 3 填值；用途：入口分派与重生成目标；空=常规轮）
    _summary: str = ""  # 跨轮历史摘要正文（来源：stream_chat 预取后经 make_initial_state 传入；范围：整轮执行；用途：seed 点作为独立历史消息注入；空=无摘要）
    # ── agent 循环 ──
    messages: Annotated[list[BaseMessage], add_messages] = field(
        default_factory=list
    )  # 模型可见消息（来源：agent 循环节点追加；范围：整轮执行；用途：LLM 上下文，add_messages 提供追加语义）
    _system_messages: list[BaseMessage] = field(
        default_factory=list
    )  # system 段（来源：agent 节点首轮组装后写入；范围：整轮执行，跨 regen invoke 持久；用途：模型参数与 system 的施加通道；**不带 reducer**，写入即替换）
    tool_contexts: list[RAGContext] = field(
        default_factory=list
    )  # 本轮材料池（来源：常规轮 agent_finalize 从主 ctx 写入 / 直出轮直出节点从子代理 ctx 写入；范围：整轮执行；用途：引用溯源，verify 引用护栏与 format 判据读此）
    verify_temporal_years: list[int] = field(
        default_factory=list
    )  # verify 判据材料：本轮要求覆盖年份（来源：常规轮 agent_finalize 从主 ctx 写入 / 直出轮直出节点从子 ctx 写入；范围：整轮执行；用途：年份完整性比对；空=不校验）
    _ask_count: int = 0  # 本 turn ask_user 调用次数（来源：ask_user 节点自增；范围：单 turn；用途：日志/兜底，实际检查走 contextvar）
    # ── 输出 ──
    answer: str = ""  # LLM 生成的完整回答
    citations: list[dict] = field(default_factory=list)  # 去重引用列表
    # ── 模型信息 ──
    model_used: str = ""  # LiteLLM 实际使用的模型名（用于识别 fallback）
    is_fallback: bool = False  # 是否触发了模型 fallback
    # ── 内部 ──
    _history: list[ChatMessage] = field(
        default_factory=list
    )  # 对话历史（注入 prompt 用）
    _token_usage: dict = field(default_factory=dict)  # token 用量统计
    _verify_regenerations: int = 0  # verify 修订保险丝计数（来源：verify 决策化兜底；范围：单轮执行；用途：防 verify→agent 无限往返，正常被决策化提前终止）
    _needs_regenerate: bool = False  # 验证循环重生成信号（来源：verify 节点置位；范围：单轮执行；用途：条件边回 agent 重生成）
    timings: dict = field(default_factory=dict)  # 各节点耗时统计

    @classmethod
    def make_initial_state(
        cls,
        session_id,
        kb_id,
        query,
        history,
        deep_thinking=False,
        direct_skill="",
        summary="",
    ):
        """创建图初始状态，只设输入字段，中间态/输出由各节点填充。

        Args:
            session_id: 会话 ID
            kb_id: 知识库 ID
            query: 用户查询文本
            history: 对话历史列表
            deep_thinking: 深度思考开关（默认 False），传给 agent LLM enable_thinking
            direct_skill: 本轮命令行直出的 fork skill 名（默认空=常规轮）
            summary: 跨轮历史摘要正文（默认空=无摘要），由 seed 点作为独立历史消息注入
        """
        return cls(
            session_id=session_id,
            kb_id=kb_id,
            query=query,
            _history=history,
            deep_thinking=deep_thinking,
            direct_skill=direct_skill,
            _summary=summary,
        )


class LangGraphNode:
    """StateGraph 节点名称与输出字段。

    每个节点一个嵌套类，NAME 为节点注册名，其余为该节点输出字段 key。
    与 AgentState 字段同源：生产侧（graph/workflow）与消费侧
    （services/agent_service）共用同一套 key，避免字段名散落成两套。
    """

    class Format:
        NAME: str = "format"  # 引用格式化
        CITATIONS: str = "citations"  # 引用列表输出字段

    class SkillDirect:
        NAME: str = "skill_direct"  # 命令行直出节点（/xxx 命中 fork skill）


class LangGraphEvent:
    """astream_events 事件类型。"""

    CHAIN_START: str = "on_chain_start"  # 节点/链开始执行
    CHAIN_END: str = "on_chain_end"  # 节点/链执行完毕
    CHAIN_STREAM: str = "on_chain_stream"  # 节点/链产出中间结果
    CHAT_MODEL_STREAM: str = "on_chat_model_stream"  # LLM 流式输出 token
    CHAT_MODEL_START: str = "on_chat_model_start"  # LLM 开始调用
    CHAT_MODEL_END: str = "on_chat_model_end"  # LLM 调用结束
    TOOL_START: str = "on_tool_start"  # 工具调用开始
    TOOL_END: str = "on_tool_end"  # 工具调用结束
    TOOL_ERROR: str = "on_tool_error"  # 工具调用异常
    RETRIEVER_START: str = "on_retriever_start"  # 检索器开始
    RETRIEVER_END: str = "on_retriever_end"  # 检索器结束
    RETRIEVER_ERROR: str = "on_retriever_error"  # 检索器异常
    PARALLEL_START: str = "on_parallel_start"  # 并行分支开始
    PARALLEL_END: str = "on_parallel_end"  # 并行分支结束


class LangGraphKey:
    """astream_events 事件 dict 字段 key。"""

    EVENT: str = "event"  # 事件类型字段
    NAME: str = "name"  # 节点名称字段
    DATA: str = "data"  # 事件数据字段
    CHUNK: str = "chunk"  # LLM 流式 chunk（在 data 内）
    OUTPUT: str = "output"  # 节点输出（在 data 内）


class LangGraph:
    """astream_events API 版本。"""

    VERSION: Literal["v1", "v2"] = "v2"  # 当前 LangGraph 稳定版本
