"""测试 agent 外层节点 — 首轮消息拆分、装配产物 invoke 回写、finalize 收尾提取。

fake 装配产物（_RecordingInner）与 stub PromptManager 均为内存实现，不发真实网络调用。
"""

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.agents.graph.agent_node import (
    AgentLoopBundle,
    _split_initial_messages,
    make_agent_finalize_node,
    make_agent_loop_node,
)
from src.agents.graph.state import AgentState
from src.core.log_events import Event
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


class StubPromptManager:
    """极简 PromptManager 替身：只提供 build_prompt 需要的取值方法。"""

    def get_user_template(self, context="", query=""):
        """返回含 query 的用户模板。"""
        return f"user template: {query}"


@pytest.fixture
def fake_prompt_manager() -> StubPromptManager:
    """提供只回显 query 的 PromptManager 替身（首轮组装的最小依赖）。"""
    return StubPromptManager()


class _RecordingInner:
    """记录子图输入的假装配产物。"""

    def __init__(self, produced: list[BaseMessage]) -> None:
        """记录固定新增段。"""
        self.inputs: list[dict] = []
        self._produced = produced

    async def ainvoke(self, payload: dict) -> dict:
        """记录输入并把新增段拼在输入消息之后返回。"""
        self.inputs.append(payload)
        return {"messages": [*payload["messages"], *self._produced]}


@pytest.mark.asyncio
async def test_finalize_extracts_answer_and_contexts():
    """finalize 应提取末次消息文本为 answer，并把 tool_contexts 读入 state。"""
    ctx = RequestContext(session_id="s1")
    ctx.tool_contexts.append(
        RAGContext(
            content="x", source="a.pdf", page=1, doc_id="d1", chunk_id="d1:0", score=0.9
        )
    )
    token = current_request_ctx.set(ctx)
    try:
        state = AgentState.make_initial_state("s1", "kb1", "q", [])
        state.messages = [HumanMessage(content="q"), AIMessage(content="答案是X [1]")]
        node = make_agent_finalize_node()
        out = await node(state)
        assert out["answer"] == "答案是X [1]"
        assert len(out["tool_contexts"]) == 1
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_finalize_content_blocks():
    """content 为 list[dict(type=text)] 时按序拼接为纯文本。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    state.messages = [
        HumanMessage(content="q"),
        AIMessage(
            content=[
                {"type": "text", "text": "第一部分"},
                {"type": "text", "text": "第二部分"},
            ]
        ),
    ]
    node = make_agent_finalize_node()
    out = await node(state)
    assert out["answer"] == "第一部分第二部分"


@pytest.mark.asyncio
async def test_finalize_no_messages_returns_empty_answer():
    """messages 为空时 answer 为空字符串，不抛异常。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    node = make_agent_finalize_node()
    out = await node(state)
    assert out["answer"] == ""
    assert out["tool_contexts"] == []


def test_prompt_messages_counts_system_before_split(monkeypatch, fake_prompt_manager):
    """system_msgs 必须在拆分前算出，否则拆分后 system 段被移出列表恒为 0。"""
    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr(
        "src.agents.graph.agent_node.core_logging.log_event", fake_log_event
    )
    state = AgentState.make_initial_state(
        session_id="s", kb_id="", query="q", history=[]
    )
    system_half, rest_half = _split_initial_messages(
        state, fake_prompt_manager, frozenset()
    )
    payload = next(c for c in calls if c["event"] is Event.PROMPT_MESSAGES)
    assert payload["system_msgs"] >= 1, "拆分后 system 段被移出列表 ⇒ 计数不得为 0"
    assert all(isinstance(m, SystemMessage) for m in system_half)
    assert not any(isinstance(m, SystemMessage) for m in rest_half)


@pytest.mark.asyncio
async def test_first_turn_writes_back_whole_assembled_list(fake_prompt_manager):
    """首轮外层 messages 为空 ⇒ 回写整份（组装段 + 新增段）。"""
    inner = _RecordingInner([AIMessage(content="A1")])
    node = make_agent_loop_node(
        {
            "default": AgentLoopBundle(
                agent=inner, prompt_manager=fake_prompt_manager, tool_names=frozenset()
            )
        }
    )
    state = AgentState.make_initial_state(
        session_id="s", kb_id="", query="q", history=[]
    )
    out = await node(state)
    seed_len = len(inner.inputs[0]["messages"])
    assert seed_len > 0, "首轮必须组装出非 system 段作为子图输入"
    assert len(out["messages"]) == seed_len + 1, "首轮须回写整份组装段 + 新增段"
    assert isinstance(out["messages"][-1], AIMessage)
    assert out["_system_messages"], "system 半段必须落回外层（跨 invoke 载体）"


@pytest.mark.asyncio
async def test_regen_round_model_request_contains_query_and_history(
    fake_prompt_manager,
):
    """重生成轮的模型请求必须含原始 query 与历史（回写基准取外层条数）。"""
    inner = _RecordingInner([AIMessage(content="A2")])
    node = make_agent_loop_node(
        {
            "default": AgentLoopBundle(
                agent=inner, prompt_manager=fake_prompt_manager, tool_names=frozenset()
            )
        }
    )
    first = await node(
        AgentState.make_initial_state(
            session_id="s", kb_id="", query="原始问题", history=[]
        )
    )
    state = AgentState.make_initial_state(
        session_id="s", kb_id="", query="原始问题", history=[]
    )
    state.messages = [*first["messages"], SystemMessage(content="VERIFY-GUIDANCE")]
    state._system_messages = first["_system_messages"]
    out = await node(state)
    sent = inner.inputs[-1]["messages"]
    assert any("原始问题" in str(m.content) for m in sent), "regen 轮丢了原始问题"
    assert any(
        isinstance(m, SystemMessage) and "VERIFY-GUIDANCE" in str(m.content)
        for m in sent
    )
    assert len(out["messages"]) == 1, "regen 轮只回写新增段"


def test_make_initial_state_deep_thinking_default_false():
    """未传 deep_thinking 时默认 False。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    assert state.deep_thinking is False


def test_make_initial_state_deep_thinking_true():
    """传 deep_thinking=True 时状态字段为 True。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [], deep_thinking=True)
    assert state.deep_thinking is True
