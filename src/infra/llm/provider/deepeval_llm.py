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
from loguru import logger

from src.config.const import DEEPEVAL_BRIDGE_PROMPT_MAX_CHARS


def _truncate_prompt(prompt: str) -> str:
    """把超长 prompt 截到上限内，保留头尾（指令在头、JSON schema 在尾）。

    超限请求会被服务端以 400 拒绝（`Range of input length should be [1, 991808]`），
    既拿不到分、又把评测拖到不可接受。截断中间的长工具输出，保留首尾两端的
    指令与 schema；此路径会记 warning 以便回溯。

    Args:
        prompt: 原 prompt

    Returns:
        原对象（未超限）或「头 + 截断标记 + 尾」的新字符串
    """
    if len(prompt) <= DEEPEVAL_BRIDGE_PROMPT_MAX_CHARS:
        return prompt
    head = int(DEEPEVAL_BRIDGE_PROMPT_MAX_CHARS * 0.6)
    tail = DEEPEVAL_BRIDGE_PROMPT_MAX_CHARS - head
    omitted = len(prompt) - head - tail
    logger.warning(
        "[provider] bridge prompt truncated len={} limit={} omitted={}",
        len(prompt),
        DEEPEVAL_BRIDGE_PROMPT_MAX_CHARS,
        omitted,
    )
    return f"{prompt[:head]}\n...[truncated {omitted} chars]...\n{prompt[-tail:]}"


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
        return self._content(self._chat.invoke(_truncate_prompt(prompt)))

    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        return self._content(await self._chat.ainvoke(_truncate_prompt(prompt)))
