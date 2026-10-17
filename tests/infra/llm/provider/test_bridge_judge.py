from typing import ClassVar

from src.config import settings
from src.infra.llm.provider.factory import build_bridge_judge
from src.infra.llm.provider.http_observability import HookEvidence


class _FakeChat:
    model_kwargs: ClassVar[dict] = {}


def test_bridge_judge_binds_both_clients_with_fine_grained_timeout():
    """异步与同步两条路都必须有细粒度超时。

    只挂 `http_async_client` 时，DeepEval 的同步 `generate()` 会回落到 SDK 默认
    `read=600s`（Phase 4 实测 5 题卡住 12+ 分钟）⇒ 两个 client 都要挂，且同一
    `httpx.Timeout` 也要作为 `request_timeout` 传入（SDK per-request 会覆盖 client 超时）。
    """
    judge = build_bridge_judge(evidence=HookEvidence())

    assert judge.http_async_client is not None, "ragas 走异步：必须挂 http_async_client"
    assert judge.http_client is not None, (
        "DeepEval 同步 generate 路径也必须挂 http_client"
    )
    assert judge.max_retries == 0, "关掉 SDK 重试（防与 ragas 层重试乘法放大）"
    assert judge.http_async_client.timeout == judge.http_client.timeout
    assert judge.request_timeout == judge.http_client.timeout
    # 超时值必须是配置层那一个（防回落到 SDK 默认 read=600s）
    assert judge.http_client.timeout.read == settings.RAGAS_HTTP_TIMEOUT_READ


def test_bridge_judge_pins_temperature_zero_and_zero_retries(monkeypatch):
    """裁判必须显式 temperature=0，且不允许 top_p 从 LLM_KWARGS 漏进来。"""
    captured: dict = {}

    def fake_build_chat(role, **kwargs):
        captured["role"] = role
        captured.update(kwargs)
        return _FakeChat()

    monkeypatch.setattr("src.infra.llm.provider.factory.build_chat", fake_build_chat)

    build_bridge_judge(evidence=HookEvidence())

    assert captured["role"] == "judge"
    assert captured["temperature"] == 0
    assert "top_p" not in captured
    assert captured["max_retries"] == 0
    assert "extra_body" not in captured
