"""Provider 兼容层数据模型与加载（design D2/D3）。"""

from __future__ import annotations

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
