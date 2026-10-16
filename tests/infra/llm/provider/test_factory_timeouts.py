"""构造即显式：三类 build_* 必须带超时且 SDK 重试为 0（spec timeout-policy）。"""

from __future__ import annotations

from src.infra.llm.provider import factory


def _read_of(client) -> float:
    return client.timeout.read


def test_build_chat_sets_explicit_timeouts_and_disables_sdk_retry(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    model = factory.build_chat("main")
    assert model.max_retries == 0, "SDK 自动重试必须关闭"
    assert model.http_async_client is not None, (
        "主链路是异步流式 ⇒ 必须设 http_async_client"
    )
    assert model.http_client is not None, "同步路径同样要显式超时"
    assert _read_of(model.http_async_client) > 0
    assert _read_of(model.http_client) > 0


def test_roles_can_have_different_read_timeouts(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    main = factory.build_chat("main")
    classify = factory.build_chat("classify")
    assert _read_of(main.http_async_client) != _read_of(classify.http_async_client)


def test_judge_role_is_not_touched_by_this_change(monkeypatch):
    """judge 的超时真源归评测链（D17）；本变更不得注入默认值。

    判据取 `http_async_client is None`：可见且不被将来取值变化掩盖。
    若调用方将来显式传入 client/超时，本变更不覆盖（judge 不在 ROLE_TIMEOUT_READ 表内）。
    """
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    model = factory.build_chat("judge")
    assert model.http_async_client is None


def test_build_embeddings_explicit_and_query_override(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    ingest = factory.build_embeddings()
    query = factory.build_embeddings(read_timeout=15.0)
    assert ingest.max_retries == 0
    assert ingest.http_client is not None
    assert _read_of(query.http_client) == 15.0
    assert _read_of(ingest.http_client) > _read_of(query.http_client)


def test_get_embeddings_passes_read_timeout_through(monkeypatch):
    """门面 get_embeddings 的 read_timeout 必须透传到 build_embeddings（查询档）。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    from src.models import get_embeddings

    query = get_embeddings(read_timeout=15.0)
    ingest = get_embeddings()
    assert _read_of(query.http_client) == 15.0
    assert _read_of(ingest.http_client) > _read_of(query.http_client)
