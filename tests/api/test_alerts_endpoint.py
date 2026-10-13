"""告警接收端点：URL 路径 token 鉴权与立即应答单测。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import alerts as alerts_api
from src.services import alert_service

_URL = "/api/alerts/webhook"


@pytest.fixture
def client() -> TestClient:
    """只挂告警路由的测试应用。"""
    app = FastAPI()
    app.include_router(alerts_api.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """配好 token，并清空进程级去重窗口（避免用例间互相污染）。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "tok")
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    alert_service._reset_for_test()


def _body(*, status: str = "firing", fingerprint: str = "fp-a") -> bytes:
    """构造一条最小可用载荷。"""
    return json.dumps(
        {
            "status": status,
            "alerts": [
                {
                    "status": status,
                    "fingerprint": fingerprint,
                    "labels": {"severity": "critical"},
                    "annotations": {"summary": "m"},
                }
            ],
            "commonLabels": {"alertname": "CPU 高"},
        }
    ).encode()


def test_token_in_path_accepted(client: TestClient, configured: None) -> None:
    """正确的路径 token ⇒ 202。"""
    assert client.post(f"{_URL}/tok", content=_body()).status_code == 202


def test_wrong_token_is_401(client: TestClient, configured: None) -> None:
    """token 不匹配 ⇒ 401（路径存在但凭据不对，与"没这个路由"可区分）。"""
    assert client.post(f"{_URL}/bad", content=_body()).status_code == 401


def test_unconfigured_token_is_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未配置 token ⇒ 一切请求 401。"""
    monkeypatch.delenv("ALERT_WEBHOOK_TOKEN", raising=False)
    alert_service._reset_for_test()
    assert client.post(f"{_URL}/tok", content=_body()).status_code == 401


def test_path_without_token_segment_is_404(
    client: TestClient, configured: None
) -> None:
    """不带 token 段的路径 ⇒ 404（路由不匹配，与 401 语义区分开）。"""
    assert client.post(_URL, content=_body()).status_code == 404


def test_bad_payload_is_400(client: TestClient, configured: None) -> None:
    """token 对但载荷非法 ⇒ 400。"""
    assert client.post(f"{_URL}/tok", content=b"{not json").status_code == 400


def test_duplicate_still_202(client: TestClient, configured: None) -> None:
    """第二条（同指纹同状态）仍 202，不是错误码。"""
    assert client.post(f"{_URL}/tok", content=_body()).status_code == 202
    assert client.post(f"{_URL}/tok", content=_body()).status_code == 202


def test_recovery_payload_accepted(client: TestClient, configured: None) -> None:
    """恢复载荷同样 202（去重键含状态，故不被触发那条吃掉）。"""
    assert client.post(f"{_URL}/tok", content=_body(status="firing")).status_code == 202
    assert (
        client.post(f"{_URL}/tok", content=_body(status="resolved")).status_code == 202
    )
