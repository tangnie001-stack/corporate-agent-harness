"""reasoning_content 请求侧回传测试（design D4/D8）——provider 兼容层路径。

构造侧的 preserve_thinking 注入归 `thinking_extra_body` / `build_chat`；本文件覆盖：
- 经 provider 层构造出的模型在请求侧把 assistant 的 reasoning_content 回传；
- 主链路思考档位经 `build_chat("main", thinking=…)` 得到完整 extra_body。
"""

from langchain_core.messages import AIMessage, HumanMessage

from src.infra.llm.provider.chat import ReasoningPreservingChat


def _llm() -> ReasoningPreservingChat:
    return ReasoningPreservingChat(
        model="kimi-k3",
        api_key="test",  # type: ignore[arg-type]  # 测试用假 key，无需真实凭证
        reasoning_field="reasoning_content",
    )


def _build_main(monkeypatch, thinking: bool):
    """构造 main 角色模型；重置 registry 缓存以保证用文件默认绑定。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory

    monkeypatch.setattr(factory, "_REGISTRY", None)
    from src.infra.llm.provider.factory import build_chat

    return build_chat("main", thinking=thinking)


class TestReasoningPreserving:
    def test_assistant_reasoning_injected_into_payload(self):
        messages = [
            HumanMessage(content="计算腾讯2025年毛利率"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "retrieve_kb", "args": {"query": "q"}, "id": "t1"}
                ],
                additional_kwargs={"reasoning_content": "先检索知识库"},
            ),
        ]
        payload = _llm()._get_request_payload(messages)
        assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
        assert assistant["reasoning_content"] == "先检索知识库"
        assert assistant["tool_calls"][0]["function"]["name"] == "retrieve_kb"

    def test_no_reasoning_no_field(self):
        messages = [AIMessage(content="hi")]
        payload = _llm()._get_request_payload(messages)
        assert "reasoning_content" not in payload["messages"][0]

    def test_build_main_thinking_true_injects_preserve_thinking(self, monkeypatch):
        # 开思考：extra_body 同时含 enable_thinking 与 preserve_thinking
        extra_body = _build_main(monkeypatch, thinking=True).extra_body
        assert extra_body is not None
        assert extra_body.get("preserve_thinking") is True
        assert extra_body.get("enable_thinking") is True

    def test_build_main_thinking_false_skips_preserve_thinking(self, monkeypatch):
        # 显式关思考：请求体不含 preserve_thinking，仅 enable_thinking=False
        extra_body = _build_main(monkeypatch, thinking=False).extra_body
        assert extra_body is not None
        assert extra_body.get("preserve_thinking") is None
        assert extra_body.get("enable_thinking") is False
