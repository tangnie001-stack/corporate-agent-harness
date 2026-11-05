from dataclasses import fields
from typing import Annotated, get_args, get_origin

from langgraph.graph.message import add_messages

from src.agents.graph.state import AgentState
from src.rag.context import RAGContext


def test_agent_state_defaults():
    state = AgentState(query="2024年营收多少", kb_id="kb-1")
    assert state.query == "2024年营收多少"
    assert state.kb_id == "kb-1"


def test_agent_state_with_tool_contexts():
    state = AgentState(
        query="净利润多少",
        tool_contexts=[
            RAGContext(
                content="净利润100亿",
                source="财报.pdf",
                page=5,
                doc_id="doc-1",
                chunk_id="chunk-1",
                score=0.95,
            )
        ],
    )
    assert len(state.tool_contexts) == 1
    assert state.tool_contexts[0].content == "净利润100亿"


def test_agent_state_no_grader_fields():
    state = AgentState(query="test")
    assert not hasattr(state, "grader_score")
    assert not hasattr(state, "retrieval_retries")
    assert not hasattr(state, "downgraded")


def test_agent_state_trace_id():
    state = AgentState(query="test", trace_id="trace_abc123")
    assert state.trace_id == "trace_abc123"


def test_state_has_messages_with_addmessages_reducer():
    """messages 字段应存在，且带追加语义 reducer（add_messages）注解。"""
    msg_field = next(f for f in fields(AgentState) if f.name == "messages")
    assert get_origin(msg_field.type) is Annotated
    assert add_messages in get_args(msg_field.type)


def test_state_has_agent_fields():
    """材料池与 _system_messages 载体存在；循环计数三字段已删（计数内化进装配产物）。"""
    names = {f.name for f in fields(AgentState)}
    assert "tool_contexts" in names
    assert "_system_messages" in names
    assert "_ask_count" in names
    assert "_agent_iterations" not in names
    assert "_max_agent_iterations" not in names
    assert "_delegate_used" not in names


def test_classify_fields_removed():
    """classify/rewrite/retrieve/rerank 旧字段应已删除。"""
    removed = {
        "intent",
        "extracted_entities",
        "missing_entities",
        "classification_confidence",
        "skip_retrieval",
        "skip_clarify",
        "_kb_entities",
        "_kb_suggestions",
        "rewritten_query",
        "rewritten_queries",
        "retrieval_results",
        "contexts",
    }
    names = {f.name for f in fields(AgentState)}
    assert removed & names == set()


def test_initial_state_carries_profile():
    """profile 经 make_initial_state 贯穿到图状态；缺省为空串（通用人设）。"""
    assert (
        AgentState.make_initial_state("s", "", "hi", [], profile="bot-ops").profile
        == "bot-ops"
    )
    assert AgentState.make_initial_state("s", "", "hi", []).profile == ""
