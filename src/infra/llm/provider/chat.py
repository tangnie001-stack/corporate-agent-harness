"""provider-aware 的 ChatOpenAI 子类：保留/回传第三方思考内容（design D8/D9）。

langchain-openai 只解析官方 OpenAI 字段，第三方 provider 的思考文本（字段名由
provider 决定，见 `ProviderSpec.reasoning_field`）会被丢弃或被拒。本子类：
- 流式侧把 delta 里的思考字段累积到 `AIMessageChunk.additional_kwargs`；
- 请求侧把 assistant 消息的思考文本回传给 provider。

`reasoning_field` 决定字段名，为 None 时整条链路跳过（provider 不支持思考）。

参考：langchain-ai/langchain issue #38764 的 ReasoningChatOpenAI 实现。
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.base import LanguageModelInput
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from loguru import logger


class ReasoningPreservingChat(ChatOpenAI):
    """保留 reasoning 字段的 ChatOpenAI；字段名来自 `reasoning_field`。"""

    reasoning_field: str | None = "reasoning_content"

    def _convert_chunk_to_generation_chunk(
        self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None
    ) -> ChatGenerationChunk | None:
        """转换流式 chunk，并提取思考文本到 additional_kwargs。

        Args:
            chunk: openai SDK chunk 的 dict（含 choices[0].delta）
            default_chunk_class: 默认消息块类型
            base_generation_info: 基础生成信息

        Returns:
            ChatGenerationChunk：正常解析结果；无法转换时返回 None
        """
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None or not self.reasoning_field:
            return generation_chunk
        # 兼容两种外层 chunk 结构（普通流式 / beta stream）
        choices = chunk.get("choices", [])
        if not choices and chunk.get("chunk"):
            choices = chunk["chunk"].get("choices", [])
        if not choices:
            return generation_chunk
        delta = choices[0].get("delta", {}) or {}
        reasoning = delta.get(self.reasoning_field) or delta.get("reasoning") or ""
        if reasoning and isinstance(generation_chunk.message, AIMessageChunk):
            prev = generation_chunk.message.additional_kwargs.get(
                "reasoning_content", ""
            )
            generation_chunk.message.additional_kwargs["reasoning_content"] = (
                prev + reasoning
            )
        return generation_chunk

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """构建请求 payload，并把 assistant 消息的思考文本注入其中。

        Args:
            input_: 模型输入（消息列表或 LangChain 支持的其他输入形式）
            stop: 停止词序列
            **kwargs: 透传给父类的额外请求参数

        Returns:
            dict：openai 兼容请求 payload，assistant 消息按需携带思考字段
        """
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if not self.reasoning_field:
            return payload
        payload_messages = payload.get("messages", [])
        if len(messages) != len(payload_messages):
            # 数量不一致说明两侧消息形态分歧，无法一一对应，跳过注入保证请求安全
            logger.warning(
                "reasoning injection skipped: message count mismatch ({} vs {})",
                len(messages),
                len(payload_messages),
            )
            return payload
        for msg, msg_dict in zip(messages, payload_messages):
            if msg_dict.get("role") != "assistant":
                continue
            reasoning = msg.additional_kwargs.get("reasoning_content", "")
            if reasoning and not msg_dict.get(self.reasoning_field):
                msg_dict[self.reasoning_field] = reasoning
        return payload
