"""Provider 兼容层数据模型与加载（design D2/D3）。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

_DEFAULT_REGISTRY = Path(__file__).with_name("registry.yaml")

ROLES: tuple[str, ...] = ("main", "classify", "summary", "judge", "embedding", "rerank")


class ThinkingStyle(str, Enum):
    """思考参数形状族（design D5–D6）。新增形状 = 加成员 + 加翻译分支 + 加数据。"""

    DASHSCOPE = "dashscope"
    DEEPSEEK = "deepseek"


@dataclass(frozen=True)
class ModelFacts:
    context_window: int | None = None
    pricing: tuple[float, float] | None = None
    tokens_per_cjk_char: float | None = None
    embedding_batch_size: int | None = None
    embedding_dimension: int | None = None


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str
    thinking_style: ThinkingStyle
    reasoning_field: str | None
    supports_tools: bool = True
    models: dict[str, str] = field(default_factory=dict)
    catalog: dict[str, ModelFacts] = field(default_factory=dict)


def load_registry(path: Path | None = None) -> dict[str, ProviderSpec]:
    """加载并解析 registry.yaml。文件缺失抛 FileNotFoundError（供启动期 fail-fast）。"""
    path = path or _DEFAULT_REGISTRY
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs: dict[str, ProviderSpec] = {}
    for name, body in (raw.get("providers") or {}).items():
        catalog = {}
        for model_name, facts in (body.get("catalog") or {}).items():
            pricing = None
            if isinstance(facts.get("pricing"), dict):
                pricing = (
                    float(facts["pricing"]["input"]),
                    float(facts["pricing"]["output"]),
                )
            catalog[model_name] = ModelFacts(
                context_window=facts.get("context_window"),
                pricing=pricing,
                tokens_per_cjk_char=facts.get("tokens_per_cjk_char"),
                embedding_batch_size=facts.get("embedding_batch_size"),
                embedding_dimension=facts.get("embedding_dimension"),
            )
        specs[name] = ProviderSpec(
            name=name,
            base_url=body["base_url"],
            api_key_env=body["api_key_env"],
            thinking_style=ThinkingStyle(body["thinking_style"]),
            reasoning_field=body.get("reasoning_field"),
            supports_tools=bool((body.get("capabilities") or {}).get("tools", True)),
            models=dict(body.get("models") or {}),
            catalog=catalog,
        )
    return specs


# 独立缺省 dashscope 的角色（不跟随 LLM_PROVIDER，design D4）
_INDEPENDENT_ROLES = ("embedding", "rerank", "judge")
_PROVIDER_ENV = {
    "main": "LLM_PROVIDER",
    "classify": "CLASSIFY_PROVIDER",
    "summary": "SUMMARY_PROVIDER",
    "judge": "RAGAS_PROVIDER",
    "embedding": "EMBEDDING_PROVIDER",
    "rerank": "RERANK_PROVIDER",
}
_MODEL_ENV = {
    "main": "LLM_MODEL",
    "classify": "CLASSIFY_MODEL",
    "summary": "SUMMARY_MODEL",
    "judge": "RAGAS_LLM_MODEL",
    "embedding": "EMBEDDING_MODEL",
    "rerank": "RERANK_MODEL",
}
_DEFAULT_PROVIDER = "dashscope"


@dataclass(frozen=True)
class ResolvedModel:
    """某角色最终生效的模型绑定（provider 规格 + 模型名 + 端点 + 密钥）。"""

    role: str
    provider: ProviderSpec
    model: str
    base_url: str
    api_key: str


def resolve_role(
    role: str,
    registry: dict[str, ProviderSpec],
    env: Mapping[str, str] | None = None,
) -> ResolvedModel:
    """按角色解析出最终生效的模型绑定（design D4）。

    覆盖顺序：provider 选择取角色 env（main→LLM_PROVIDER），缺省时独立角色用
    dashscope、其余跟随 LLM_PROVIDER；模型取角色 env 覆盖 registry 默认；端点
    取 `<PROVIDER>_BASE_URL` 覆盖 spec.base_url；密钥取 spec.api_key_env。
    未知角色 / provider / 无可用模型抛 KeyError，供启动期 fail-fast。
    """
    if env is None:
        env = os.environ
    if role not in _PROVIDER_ENV:
        raise KeyError(f"未知角色: {role}")
    # provider 选择
    provider_name = env.get(_PROVIDER_ENV[role]) or ""
    if not provider_name:
        if role in _INDEPENDENT_ROLES:
            provider_name = _DEFAULT_PROVIDER
        else:
            provider_name = env.get("LLM_PROVIDER") or _DEFAULT_PROVIDER
    if provider_name not in registry:
        raise KeyError(f"未知 provider: {provider_name}（角色 {role}）")
    spec = registry[provider_name]
    # 模型：.env 覆盖 else registry 默认
    model = env.get(_MODEL_ENV[role]) or spec.models.get(role) or ""
    if not model:
        raise KeyError(f"角色 {role} 在 provider {provider_name} 无可用模型")
    base_url = env.get(f"{provider_name.upper()}_BASE_URL") or spec.base_url
    api_key = env.get(spec.api_key_env) or ""
    return ResolvedModel(
        role=role, provider=spec, model=model, base_url=base_url, api_key=api_key
    )


def validate_config(
    registry: dict[str, ProviderSpec], env: Mapping[str, str] | None = None
) -> None:
    """启动期 fail-fast。仅在应用启动（lifespan）调用；import 不触发（design D12）。"""
    if env is None:
        env = os.environ
    if env.get("PROVIDER_VALIDATE", "on").lower() in ("0", "off", "false"):
        return
    for role in ROLES:
        try:
            resolved = resolve_role(role, registry, env)
        except KeyError as e:
            msg = str(e).strip("'\"")
            if msg.startswith("未知 provider"):
                raise RuntimeError(msg) from e
            raise RuntimeError(f"角色 {role} 解析失败: {msg}") from e
        if not resolved.api_key:
            raise RuntimeError(
                f"provider {resolved.provider.name} 缺密钥：请设置 {resolved.provider.api_key_env}"
            )
        # 被绑定 provider 的 thinking_style 必须有已实现的翻译分支：新增枚举形状
        # 却漏加 thinking_extra_body 分支时，在此 fail-fast（而非请求期才 ValueError）。
        # 局部 import 规避 spec↔thinking 的循环依赖。
        from src.infra.llm.provider.thinking import thinking_extra_body

        try:
            thinking_extra_body(resolved.provider, False)
        except ValueError as e:
            raise RuntimeError(
                f"provider {resolved.provider.name} 的 thinking_style "
                f"{resolved.provider.thinking_style} 无已实现分支: {e}"
            ) from e
