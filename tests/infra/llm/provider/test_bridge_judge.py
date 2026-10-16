from typing import ClassVar

from src.infra.llm.provider.factory import build_bridge_judge
from src.infra.llm.provider.http_observability import HookEvidence


class _FakeChat:
    model_kwargs: ClassVar[dict] = {}


def test_bridge_judge_binds_async_client_only():
    judge = build_bridge_judge(evidence=HookEvidence())

    assert judge.http_async_client is not None, "ragas 走异步：必须挂 http_async_client"
    assert judge.http_client is None, "不得只挂同步 client（hooks 与超时都会失效）"
    assert judge.max_retries == 0, "关掉 SDK 重试（防与 ragas 层重试乘法放大）"


def test_bridge_judge_does_not_pass_temperature_or_top_p(monkeypatch):
    """本函数自身不得声明采样参数（温度由 ragas 按 n 分档决定）。

    用 monkeypatch 钉住「传了什么」，不依赖 env 里 LLM_KWARGS 的取值——
    后者可能合法地注入 temperature，那是配置层的事，不属本函数的契约。
    """
    captured: dict = {}

    def fake_build_chat(role, **kwargs):
        captured["role"] = role
        captured.update(kwargs)
        return _FakeChat()

    monkeypatch.setattr("src.infra.llm.provider.factory.build_chat", fake_build_chat)

    build_bridge_judge(evidence=HookEvidence())

    assert captured["role"] == "judge"
    assert "temperature" not in captured
    assert "top_p" not in captured
    assert captured["http_async_client"] is not None
    assert "http_client" not in captured
    assert (
        captured["thinking"] is not None
    )  # 思考开关走 thinking=，不硬写 enable_thinking
