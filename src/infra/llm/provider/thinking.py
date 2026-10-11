"""思考参数归一化：唯一产生 provider 思考参数的地方（design D8）。"""

from __future__ import annotations

from src.infra.llm.provider.spec import ProviderSpec, ThinkingStyle


def thinking_extra_body(spec: ProviderSpec, enabled: bool) -> dict:
    """把"思考开/关"翻译为目标 provider 的完整 extra_body。

    返回的是**完整** dict（含 preserve 标志），构造期与请求期同源使用，
    避免 per-call extra_body 覆盖构造期 extra_body 时丢字段。
    """
    if spec.thinking_style is ThinkingStyle.DASHSCOPE:
        body: dict = {"enable_thinking": enabled}
        if enabled:
            body["preserve_thinking"] = True
        return body
    if spec.thinking_style is ThinkingStyle.DEEPSEEK:
        if enabled:
            return {"thinking": {"type": "enabled"}}
        return {"thinking": {"type": "disabled"}}
    raise ValueError(f"未实现 thinking_style: {spec.thinking_style}")
