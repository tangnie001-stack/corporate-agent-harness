from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

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


def test_extracts_reasoning_content_to_additional_kwargs():
    """流式 chunk 的 reasoning_content 累积到 additional_kwargs。"""
    chunk = {
        "choices": [
            {
                "delta": {"reasoning_content": "思考增量", "content": ""},
            }
        ]
    }
    gen = _llm()._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen is not None
    assert gen.message.additional_kwargs["reasoning_content"] == "思考增量"


def test_reasoning_alias_fallback():
    """OpenRouter 等用 reasoning 字段，应 fallback。"""
    chunk = {"choices": [{"delta": {"reasoning": "fallback思考", "content": ""}}]}
    gen = _llm()._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen is not None
    assert gen.message.additional_kwargs["reasoning_content"] == "fallback思考"


def test_no_reasoning_keeps_normal_content():
    """无 reasoning_content 时 content 正常透传，不影响既有行为。"""
    chunk = {"choices": [{"delta": {"content": "正文", "role": "assistant"}}]}
    gen = _llm()._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen is not None
    assert gen.message.content == "正文"
    assert "reasoning_content" not in gen.message.additional_kwargs


def test_empty_choices_returns_chunk():
    """choices 为空时返回空内容 chunk（不崩溃）。"""
    gen = _llm()._convert_chunk_to_generation_chunk(
        {"choices": []}, AIMessageChunk, None
    )
    assert gen is not None
