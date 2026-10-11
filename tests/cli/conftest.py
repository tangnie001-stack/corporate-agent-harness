"""RAGAS 评估 CLI 测试的共享夹具。

提供"真图"构造：真 `StateGraph` + 真工具函数 + 真 `RequestContext` 传播；
`vector_store` / `llm` / `reranker` 用 fake（`MagicMock` 与按序响应的假模型），
不发任何真实网络调用。F-32 守卫依赖真图验证工具收集的材料能经
`current_request_ctx` 回填 `state.tool_contexts`。
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph

from src.agents.graph.workflow import build_graph


class SequenceChatModel:
    """极简 fake LLM：按调用顺序消费固定 AIMessage 序列。

    `create_agent` 的模型节点经 `bind_tools(tools, tool_choice=…, **model_settings)`
    绑定后调用 `ainvoke(messages)`；故 fake 需提供这两个面，与
    `tests/agents/graph/test_graph.py` 的既有 stub 同构。
    """

    def __init__(self, responses: list[AIMessage]) -> None:
        """记录固定响应序列。

        Args:
            responses: 按调用顺序消费的 AIMessage 列表
        """
        self.responses = list(responses)
        self.tools = None

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        """绑定工具：fake 直接返回自身（接受 create_agent 传入的 tool_choice/model_settings）。

        Args:
            tools: 待绑定工具列表
            tool_choice: 工具选择策略（忽略）
            **kwargs: model_settings 等额外参数（忽略）
        """
        self.tools = tools
        return self

    def bind(self, **kwargs):
        """无工具路径的模型绑定：fake 直接返回自身。

        Args:
            **kwargs: 绑定参数（忽略）
        """
        return self

    async def ainvoke(self, messages, **kwargs):
        """按序返回一条固定响应（单条 AIMessage），忽略额外参数。

        Args:
            messages: 送入模型的消息列表（忽略）
            **kwargs: model_settings 等额外参数（忽略）
        """
        return self.responses.pop(0)


class StubPromptManager:
    """极简 PromptManager 替身：只提供 `build_prompt` 需要的取值方法。"""

    def get_user_template(self, context="", query=""):
        """返回含 query 的用户模板。

        Args:
            context: 注入的上下文（本桩忽略）
            query: 当前查询文本
        """
        return f"user template: {query}"


def build_eval_graph_for_test(tools) -> CompiledStateGraph:
    """构造评测用真图：按需注入工具，其余依赖全 fake。

    fake LLM 按序返回：先一条带 tool_calls 的 AIMessage（触发工具执行），
    再一条含 `[1]` 引用的最终回答（带引用使 verify 的 KB 溯源护栏直通，
    避免再触发一轮重生成——假模型无更多响应可消费）。工具为空时只返回最终回答。

    Args:
        tools: 注入图的工具列表（真工具函数由调用方提供；空列表 = 无工具直答）

    Returns:
        编译后的 StateGraph（真图，contextvar 正常传播）
    """
    if tools:
        final = AIMessage(content="最终回答[1]")
        first = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": tools[0].name,
                    "args": {"query": "测试查询"},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        )
        llm = SequenceChatModel([first, final])
    else:
        llm = SequenceChatModel([AIMessage(content="最终回答")])
    return build_graph(
        MagicMock(),  # vector_store
        llm,  # agent LLM（fake，按序响应）
        MagicMock(),  # reranker
        StubPromptManager(),
        tools=tools,
    )


@pytest.fixture
def eval_graph_factory():
    """返回构造评测真图的工厂函数（把 conftest 里的 helper 暴露给用例）。

    Returns:
        `build_eval_graph_for_test` 可调用对象
    """
    return build_eval_graph_for_test
