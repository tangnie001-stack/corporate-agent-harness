"""告警接收端点：鉴权与立即应答单测。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import alerts as alerts_api

_HEADER = "X-Alert-Token"


@pytest.fixture
def client() -> TestClient:
    """只挂告警路由的测试应用。"""
    app = FastAPI()
    app.include_router(alerts_api.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置好 token，并清空进程级去重窗口（避免用例间互相污染）。"""
    monkeypatch.setenv("ALERT_WEBHOOK_TOKEN", "tok")
    monkeypatch.delenv("ALERT_TARGET", raising=False)
    alerts_api.alert_service._reset_for_test()


def _body() -> bytes:
    return json.dumps(
        {
            "alarmId": "ALM-001",
            "level": "P2",
            "commonLabels": {"alertname": "CPU 高"},
            "alerts": [{"fingerprint": "fp-a", "annotations": {"message": "m"}}],
        }
    ).encode()


def test_token_missing_is_404(client: TestClient, configured: None) -> None:
    resp = client.post("/api/alerts/webhook", content=_body())
    assert resp.status_code == 404


def test_token_wrong_is_404(client: TestClient, configured: None) -> None:
    resp = client.post("/api/alerts/webhook", content=_body(), headers={_HEADER: "bad"})
    assert resp.status_code == 404


def test_token_correct_returns_202(client: TestClient, configured: None) -> None:
    resp = client.post("/api/alerts/webhook", content=_body(), headers={_HEADER: "tok"})
    assert resp.status_code == 202


def test_unconfigured_token_always_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALERT_WEBHOOK_TOKEN", raising=False)
    resp = client.post("/api/alerts/webhook", content=_body(), headers={_HEADER: "tok"})
    assert resp.status_code == 404


def test_bad_payload_is_400(client: TestClient, configured: None) -> None:
    resp = client.post(
        "/api/alerts/webhook", content=b"{not json", headers={_HEADER: "tok"}
    )
    assert resp.status_code == 400


def test_duplicate_still_202(client: TestClient, configured: None) -> None:
    """第二条（全部指纹命中）仍返回 202，不是错误码。"""
    h = {_HEADER: "tok"}
    assert (
        client.post("/api/alerts/webhook", content=_body(), headers=h).status_code
        == 202
    )
    assert (
        client.post("/api/alerts/webhook", content=_body(), headers=h).status_code
        == 202
    )
