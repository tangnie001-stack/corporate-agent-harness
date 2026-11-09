"""入站 trace id 白名单的行为契约（D3）。

关键点：校验必须发生在 `current_trace_id.set()` **之前** —— 响应头用的是中间件
内部的局部变量（`trace_id.py:43`），而 SSE done 事件重读 contextvar。若在下游
重生成，两者会取到不同的值，四方对齐当场分叉。
"""

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.trace_id import trace_id_middleware


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(trace_id_middleware)

    @app.get("/ping")
    async def ping() -> dict:
        from src.infra.llm.trace_context import current_trace_id

        return {"trace_id": current_trace_id.get()}

    return app


def test_valid_inbound_id_is_kept():
    """合法入站 id 原样使用，响应头与上下文一致。"""
    client = TestClient(_app())
    resp = client.get(
        "/ping", headers={"X-Trace-ID": "0123456789abcdef0123456789abcdef"}
    )
    assert resp.headers["X-Trace-ID"] == "0123456789abcdef0123456789abcdef"
    assert resp.json()["trace_id"] == "0123456789abcdef0123456789abcdef"


def test_illegal_inbound_id_is_replaced_and_aligned():
    """非法入站 id 被替换，且响应头与上下文取到**同一个**新值。"""
    client = TestClient(_app())
    resp = client.get("/ping", headers={"X-Trace-ID": "has space/and slash"})
    replaced = resp.headers["X-Trace-ID"]
    assert replaced != "has space/and slash"
    assert re.fullmatch(r"[0-9a-f]{32}", replaced)
    assert resp.json()["trace_id"] == replaced


def test_illegal_header_falls_back_to_valid_query_param():
    """非法请求头不短路查询参数：回落到合法 ?trace_id，且三方取同一值。"""
    client = TestClient(_app())
    resp = client.get(
        "/ping",
        headers={"X-Trace-ID": "!!!bad!!!"},
        params={"trace_id": "fedcba9876543210fedcba9876543210"},
    )
    assert resp.headers["X-Trace-ID"] == "fedcba9876543210fedcba9876543210"
    assert resp.json()["trace_id"] == "fedcba9876543210fedcba9876543210"


def test_missing_id_is_generated():
    """缺失时生成，响应头与上下文一致。"""
    client = TestClient(_app())
    resp = client.get("/ping")
    assert resp.headers["X-Trace-ID"] == resp.json()["trace_id"]


def test_illegal_id_does_not_fail_request():
    """非法 id 不导致请求失败（静默替换，不返回 400）。"""
    client = TestClient(_app())
    resp = client.get("/ping", headers={"X-Trace-ID": "!!!bad!!!"})
    assert resp.status_code == 200
