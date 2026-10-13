"""被拒请求的原始载荷会落日志（供与上游 ARMS 模板核对形状）。"""

import asyncio

import pytest
from loguru import logger

from src.services import alert_service
from src.services.alert_service import _REJECTED_BODY_MAX_CHARS

_VALID_BODY = (
    b'{"alarmId":"ALM-1","level":"P2","commonLabels":{"alertname":"CPU"},"alerts":'
    b'[{"fingerprint":"fp-1","annotations":{"message":"m"}}]}'
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


def test_token_mismatch_logs_body_and_not_the_supplied_token(
    token_set: None,
) -> None:
    """token 不匹配：记载荷原文，但**不记**对方传来的 token 值。"""
    lines = _capture(
        lambda: alert_service.handle_webhook(b'{"hello":"world"}', "oops-secret")
    )
    joined = "\n".join(lines)
    assert "rejected reason=token" in joined
    assert "hello" in joined
    assert "oops-secret" not in joined


def test_missing_token_is_flagged_as_absent(token_set: None) -> None:
    """未带 token 头：`token_present=false`（便于判断是"没配头"还是"头不对"）。"""
    lines = _capture(lambda: alert_service.handle_webhook(b"{}", None))
    assert any("rejected reason=token token_present=false" in line for line in lines)


def test_payload_rejected_logs_body(token_set: None) -> None:
    """token 正确但载荷解析失败：记载荷原文（上游模板形状不符时靠它定位）。"""
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
    """受理路径不产生 rejected 行（只解析失败/token 失败才记）。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "tok")
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    alert_service._reset_for_test()

    async def _run() -> int:
        return alert_service.handle_webhook(_VALID_BODY, "tok")

    lines = _capture(lambda: asyncio.run(_run()))
    assert not any("rejected" in line for line in lines)
