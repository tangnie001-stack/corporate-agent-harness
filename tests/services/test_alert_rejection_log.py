"""被拒载荷会落日志（供与上游 Grafana contact point 核对形状）。"""

import asyncio

import pytest
from loguru import logger

from src.services import alert_service
from src.services.alert_service import _REJECTED_BODY_MAX_CHARS

_VALID_BODY = (
    b'{"status":"firing","commonLabels":{"alertname":"CPU"},'
    b'"alerts":[{"fingerprint":"fp-1","annotations":{"summary":"m"}}]}'
)


def _capture(emit) -> list[str]:
    """捕获 emit() 期间产出的 WARNING 级日志行。"""
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(str(m)), level="WARNING")
    try:
        emit()
    finally:
        logger.remove(sink_id)
    return captured


@pytest.fixture
def token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """配好 token 并清空进程级去重窗口。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "tok")
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    alert_service._reset_for_test()


def test_token_mismatch_logs_reason_without_body(token_set: None) -> None:
    """token 不匹配：只记原因，**不记录载荷**。

    未通过鉴权的调用方所发的 body 不进日志（否则等于给未授权方开了一条写
    日志的通道）；且 token 本身在任何路径下都不记。
    """
    lines = _capture(
        lambda: alert_service.handle_webhook(
            b'{"probe":"should-not-be-logged"}', "oops-secret"
        )
    )
    joined = "\n".join(lines)
    assert "rejected reason=token" in joined
    assert "should-not-be-logged" not in joined
    assert "oops-secret" not in joined


def test_payload_rejected_logs_body(token_set: None) -> None:
    """token 正确但载荷解析失败：记载荷原文（上游形状不符时靠它定位）。"""
    lines = _capture(
        lambda: alert_service.handle_webhook(
            b'{"msg_type":"text","content":"x"}', "tok"
        )
    )
    assert any("rejected reason=payload" in line for line in lines)
    assert any("msg_type" in line for line in lines)


def test_overlong_body_is_truncated(token_set: None) -> None:
    """超长载荷按上限截断，避免超长日志行。"""
    body = b"x" * (_REJECTED_BODY_MAX_CHARS + 500)
    lines = _capture(lambda: alert_service.handle_webhook(body, "tok"))
    assert any("...(truncated)" in line for line in lines)


def test_valid_but_unconfigured_target_is_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """受理路径不产生 rejected 行（只有 token 失败/解析失败才记）。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "tok")
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    alert_service._reset_for_test()

    async def _run() -> int:
        return alert_service.handle_webhook(_VALID_BODY, "tok")

    lines = _capture(lambda: asyncio.run(_run()))
    assert not any("rejected" in line for line in lines)
