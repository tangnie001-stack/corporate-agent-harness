"""`POST /api/ragas/generate` 已下线，路由保留并返回 410。"""


def test_generate_endpoint_returns_410(auth_client):
    """出题端点为 410 桩：调用方拿到明确的迁移信号，而非 404/ImportError。"""
    response = auth_client.post(
        "/api/ragas/generate", json={"kb_name": "kb", "size": 1}
    )
    assert response.status_code == 410
    assert response.json()["code"] == "GONE"
