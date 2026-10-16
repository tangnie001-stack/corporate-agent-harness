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


class BridgeDeepEvalLLM(DeepEvalBaseLLM):
    """把 langchain `BaseChatModel`（provider 层裁判）适配成 DeepEval LLM。"""

    def __init__(self, chat: Any) -> None:
        self._chat = chat

    def load_model(self) -> Any:
        return self._chat

    def get_model_name(self) -> str:
        return getattr(self._chat, "model_name", "bridge-judge")

    def generate(self, prompt: str, **kwargs: Any) -> str:
        msg = self._chat.invoke(prompt)
        return getattr(msg, "content", str(msg))

    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        msg = await self._chat.ainvoke(prompt)
        return getattr(msg, "content", str(msg))
