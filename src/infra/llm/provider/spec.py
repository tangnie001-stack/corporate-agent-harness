"""Provider 兼容层数据模型与加载（design D2/D3）。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml
from loguru import logger

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

# Legacy 旧键回退链（Phase 1 软迁移，design D10）：按顺序取第一个非空 env 值。
# 令牌 "<provider>" → 被绑定 provider 的键名（密钥链用 spec.api_key_env，
# 端点链用 <PROVIDER>_BASE_URL）；令牌 "<llm>" → LLM_API_KEY / LLM_BASE_URL，
# 且**仅当被绑定 provider 就是 LLM_PROVIDER 时**才生效（避免把主 LLM 的旧键误用
# 到别的 provider）。main 族的 LLM_* 走 "<llm>" 令牌；embedding / judge 的 LLM_*
# 回退沿用旧 settings 的**无条件**链（不随所选 provider 变化），故写死字面量。
# Phase 2 删除旧键时整表清空。
_LEGACY_API_KEY_CHAINS: dict[str, tuple[str, ...]] = {
    "main": ("<provider>", "<llm>"),
    "classify": ("<provider>", "<llm>"),
    "summary": ("<provider>", "<llm>"),
    "judge": ("LLM_API_KEY", "<provider>"),
    "embedding": ("EMBEDDING_API_KEY", "<provider>", "LLM_API_KEY"),
    "rerank": ("RERANK_API_KEY", "<provider>"),
}
_LEGACY_BASE_URL_CHAINS: dict[str, tuple[str, ...]] = {
    "main": ("<provider>", "<llm>"),
    "classify": ("<provider>", "<llm>"),
    "summary": ("<provider>", "<llm>"),
    "judge": ("<provider>",),
    "embedding": ("EMBEDDING_BASE_URL", "<provider>", "LLM_BASE_URL"),
    "rerank": ("<provider>",),
}
_PROVIDER_TOKEN = "<provider>"
_LLM_TOKEN = "<llm>"


def _first_env(
    chain: tuple[str, ...],
    *,
    provider_env: str,
    llm_env: str,
    llm_allowed: bool,
    env: Mapping[str, str],
) -> str:
    """按回退链顺序取第一个非空环境变量值。

    Args:
        chain: 环境变量名 / 令牌序列，见 `_LEGACY_API_KEY_CHAINS` 的说明。
        provider_env: "<provider>" 令牌展开成的键名（密钥用 spec.api_key_env，
            端点用 <PROVIDER>_BASE_URL）。
        llm_env: "<llm>" 令牌展开成的旧键名（LLM_API_KEY / LLM_BASE_URL）。
        llm_allowed: "<llm>" 令牌是否展开；False 时跳过该令牌。
        env: 环境变量映射。

    Returns:
        str：首个非空值；整链皆空时返回 ""。
    """
    for name in chain:
        if name == _PROVIDER_TOKEN:
            value = env.get(provider_env) or ""
        elif name == _LLM_TOKEN:
            if not llm_allowed:
                continue
            value = env.get(llm_env) or ""
        else:
            value = env.get(name) or ""
        if value:
            return value
    return ""


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
    / 密钥按角色的 legacy 回退链（`_LEGACY_BASE_URL_CHAINS` / `_LEGACY_API_KEY_CHAINS`）
    取第一个非空值，整链皆空时端点回落 spec.base_url、密钥回落 ""。

    Phase 1 软迁移（design D10）：旧键按角色回退，保持旧 settings 的优先级 ——
    main 族 `<PROVIDER>_BASE_URL/API_KEY` → `LLM_BASE_URL/API_KEY`（后者仅当绑定
    provider 就是 LLM_PROVIDER 时）；embedding `EMBEDDING_BASE_URL`/`EMBEDDING_API_KEY`
    → provider 键 → `LLM_BASE_URL`/`LLM_API_KEY`；rerank `RERANK_API_KEY` → provider 键；
    judge `LLM_API_KEY` → provider 键。Phase 2 删除这些旧键。

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
    provider_base_env = f"{provider_name.upper()}_BASE_URL"
    # "<llm>" 令牌仅当绑定 provider 即 LLM_PROVIDER 时展开
    llm_provider_name = env.get("LLM_PROVIDER") or _DEFAULT_PROVIDER
    llm_allowed = provider_name == llm_provider_name
    base_url = (
        _first_env(
            _LEGACY_BASE_URL_CHAINS[role],
            provider_env=provider_base_env,
            llm_env="LLM_BASE_URL",
            llm_allowed=llm_allowed,
            env=env,
        )
        or spec.base_url
    )
    api_key = _first_env(
        _LEGACY_API_KEY_CHAINS[role],
        provider_env=spec.api_key_env,
        llm_env="LLM_API_KEY",
        llm_allowed=llm_allowed,
        env=env,
    )
    return ResolvedModel(
        role=role, provider=spec, model=model, base_url=base_url, api_key=api_key
    )


def validate_config(
    registry: dict[str, ProviderSpec], env: Mapping[str, str] | None = None
) -> None:
    """启动期 fail-fast。仅在应用启动（lifespan）调用；import 不触发（design D12）。

    无论校验开或关，都在启动日志显式打印当前校验状态（`PROVIDER_VALIDATE` 的
    生效结果），运维据此确认本次启动是否走了校验。
    """
    if env is None:
        env = os.environ
    if env.get("PROVIDER_VALIDATE", "on").lower() in ("0", "off", "false"):
        mode = "off"
    else:
        mode = "on"
    logger.info("[provider] validate state={}", mode)
    if mode == "off":
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
        if role == "embedding":
            _check_embedding_dimension(resolved)


def _check_embedding_dimension(resolved: ResolvedModel) -> None:
    """被绑定 embedding 模型声明的输出维度须与向量列维度一致，否则启动失败。

    列维度是 DDL 事实（`chunk.EMBEDDING_DIMENSION`）；不一致时写入向量会失败，
    须先迁移列定义并重建。模型不在 catalog、或未声明维度（覆盖到未知模型）时
    跳过——那类情形由"成本未知"告警路径处理，不在此拦截。

    Args:
        resolved: embedding 角色的解析结果。

    Raises:
        RuntimeError: 声明维度与列维度不一致。
    """
    # 局部 import 避免 spec↔db 模型在 import 期的耦合（design D12：import 不触发校验）
    from src.infra.db.models.chunk import EMBEDDING_DIMENSION

    facts = resolved.provider.catalog.get(resolved.model)
    if facts is None or facts.embedding_dimension is None:
        return
    if facts.embedding_dimension != EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"embedding 模型 {resolved.model} 声明维度 {facts.embedding_dimension} "
            f"与向量列维度 {EMBEDDING_DIMENSION} 不一致；需迁移列定义并重建向量"
        )
