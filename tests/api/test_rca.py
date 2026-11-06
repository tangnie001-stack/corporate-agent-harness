"""诊断详情端点：必须登录、404 与正常返回。"""

from fastapi.testclient import TestClient

_DIAG = {
    "diag_id": "d1",
    "trace_id": "trace_x",
    "env": "local",
    "trigger": "manual",
    "kind": "answer",
    "tier": "需要处理",
    "root_cause": "low_relevance",
    "owner": "kb",
    "evidence": [
        {
            "id": "E1",
            "kind": "trace",
            "locator": "retrieve_kb#1",
            "summary": "result=30",
        }
    ],
    "assertions": [{"text": "精排分数整体偏低", "evidence_ids": ["E1"]}],
    "next_actions": "",
    "basis_rounds": 0,
    "cost_ms": 12,
    "created_at": "2026-11-06T10:00:00+00:00",
}


def test_requires_login(client: TestClient):
    """诊断报告含用户原文摘要：未登录不得读取。"""
    resp = client.get("/api/rca/diag/d1")
    assert resp.status_code == 401


def test_missing_report_is_404(auth_client: TestClient, mock_app_service):
    mock_app_service.get_diag_report.return_value = None
    resp = auth_client.get("/api/rca/diag/d1")
    assert resp.status_code == 404


def test_returns_report_detail(auth_client: TestClient, mock_app_service):
    mock_app_service.get_diag_report.return_value = _DIAG
    resp = auth_client.get("/api/rca/diag/d1")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tier"] == "需要处理"
    assert data["assertions"][0]["evidence_ids"] == ["E1"]
    mock_app_service.get_diag_report.assert_awaited_once_with("d1")
