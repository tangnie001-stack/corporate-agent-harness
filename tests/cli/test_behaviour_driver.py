"""驱动形态：每题一个 CallbackHandler，且 handler 被传进 graph.ainvoke。"""

from __future__ import annotations

import pytest

from src.cli import eval_ragas


class _FakeGraph:
    def __init__(self) -> None:
        self.configs: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.configs.append(config or {})
        return {"answer": "A", "tool_contexts": [], "messages": []}


@pytest.mark.asyncio
async def test_each_question_gets_its_own_handler(monkeypatch):
    graph = _FakeGraph()

    class _H:
        pass

    # CallbackHandler 在函数体内 import ⇒ 必须打**源模块**的属性，打 eval_ragas 的
    # 模块属性会 AttributeError（那里没有这个名字）。
    monkeypatch.setattr("deepeval.integrations.langchain.CallbackHandler", _H)

    # 用假 dataset 替换 evals_iterator 的来源（只验驱动形态，不出分）
    class _FakeIterator:
        def evals_iterator(self, metrics=None, display_config=None):
            from deepeval.dataset import Golden

            for q in ("Q0", "Q1"):
                yield Golden(input=q)  # type: ignore[reportCallIssue]

    monkeypatch.setattr(
        eval_ragas, "_build_behaviour_dataset", lambda *a, **k: _FakeIterator()
    )

    await eval_ragas.generate_answers_and_contexts_with_behaviour(
        graph,
        "kb",
        "sess",
        ["Q0", "Q1"],
        ["G0", "G1"],
        behaviour_metrics=[],
        results_folder="/tmp/x",
    )

    assert len(graph.configs) == 2
    handler_0 = graph.configs[0]["callbacks"][0]
    handler_1 = graph.configs[1]["callbacks"][0]
    assert handler_0 is not handler_1, "每题必须新建独立 handler（复用会串 trace）"
