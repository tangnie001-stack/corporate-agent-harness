from langchain_core.messages import AIMessage, HumanMessage

from src.infra.llm.provider.chat import ReasoningPreservingChat


def _llm(field: str | None = "reasoning_content") -> ReasoningPreservingChat:
    return ReasoningPreservingChat(
        model="kimi-k3",
        api_key="test",  # type: ignore[arg-type]  # 测试用假 key，无需真实凭证
        reasoning_field=field,
    )


def test_roundtrips_reasoning_field():
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", additional_kwargs={"reasoning_content": "思考"}),
    ]
    payload = _llm()._get_request_payload(msgs)
    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "思考"


def test_empty_field_skips_injection():
    msgs = [AIMessage(content="hi", additional_kwargs={"reasoning_content": "x"})]
    payload = _llm(field=None)._get_request_payload(msgs)
    assert "reasoning_content" not in payload["messages"][0]
