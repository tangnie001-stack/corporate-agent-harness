"""make_rag_tools(include_ask_user=False) 不产出 ask_user 单测。"""

from src.agents.tools.rag_tools import make_rag_tools


class _FakeVS:
    """占位向量库（本用例不触发检索）。"""


def _names(**kwargs) -> set[str]:
    # _FakeVS 仅为占位（本用例不触发检索），真实类型是 PgVectorStore，故显式忽略
    tools = make_rag_tools(_FakeVS(), object(), object(), **kwargs)  # type: ignore[reportArgumentType]
    return {str(t.name) for t in tools}


def test_ask_user_registered_by_default() -> None:
    """默认仍注册 ask_user（站点与企微问答不受影响）。"""
    assert "ask_user" in _names()


def test_ask_user_excluded_when_disabled() -> None:
    """include_ask_user=False ⇒ 不注册 ask_user，其余工具仍在。"""
    names = _names(include_ask_user=False)
    assert "ask_user" not in names
    assert "retrieve_kb" in names
