"""DeepEval 原生指标的 LLM 适配器：复用 provider 层裁判。

为什么需要它：`deepeval.metrics.utils.models.initialize_model` 只接受
`str | DeepEvalBaseLLM`（`metrics/utils/models.py:170`）——**langchain 模型不被接受**；
若改用 DeepEval 自带的 `OpenAIModel`，则绕过 provider 层、并失去
`http_async_client` 上的细粒度超时与请求级 hooks。
非 native 模型路径下 DeepEval 会退回 `generate(prompt)` 并自行解析 JSON
（`models/base_model.py:151`）⇒ 本类只返回**内容文本**即可。
"""

from __future__ import annotations

from typing import Any

from deepeval.models import DeepEvalBaseLLM
from langchain_core.messages import BaseMessage


class BridgeDeepEvalLLM(DeepEvalBaseLLM):
    """把 langchain `BaseChatModel`（provider 层裁判）适配成 DeepEval LLM。

    `super().__init__()` 必须调用：基类会执行 `self.model = self.load_model()`
    并设置 `self.name`；不调用则任何读 `.name` / `.model` 的 DeepEval 代码会
    `AttributeError`（评审 Important）。
    """

    def __init__(self, chat: Any) -> None:
        self._chat = chat
        super().__init__()  # 基类据 load_model() 置 self.model / self.name

    def load_model(self) -> Any:
        return self._chat

    def get_model_name(self) -> str:
        # 显式类型检查，而非 `getattr(x, "attr", default)` 隐式兜底
        # （CLAUDE.md：类型不确定的值不做隐式兜底）——评审 Important
        name = self._chat.model_name
        return name if isinstance(name, str) else "bridge-judge"

    @staticmethod
    def _content(msg: Any) -> str:
        return str(msg.content) if isinstance(msg, BaseMessage) else str(msg)

    def generate(self, prompt: str, **kwargs: Any) -> str:
        return self._content(self._chat.invoke(prompt))

    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        return self._content(await self._chat.ainvoke(prompt))
