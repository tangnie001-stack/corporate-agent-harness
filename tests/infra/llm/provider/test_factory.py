from langchain_openai import ChatOpenAI

from src.infra.llm.provider.factory import build_chat


def test_build_main_no_thinking_has_no_thinking_key(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    llm = build_chat("main")
    assert isinstance(llm, ChatOpenAI)
    assert "enable_thinking" not in (llm.extra_body or {})


def test_build_summary_thinking_false(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    llm = build_chat("summary", thinking=False)
    assert llm.extra_body == {"enable_thinking": False}
