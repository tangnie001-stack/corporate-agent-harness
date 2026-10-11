"""启动期幂等定价 ensure（非阻塞）。

**为什么要启动期 ensure**：`registry.yaml` 各 provider 的 `catalog` 是模型定价的唯一
数据面（见 `src/cli/seed_langfuse_models.py` 模块说明），但 Langfuse v2 的
`LANGFUSE_INIT_*` 只能播种 project / user / key，**不能播种模型定价**；`python -m
src.cli.seed_langfuse_models` 又是手动步骤，漏跑会让 Langfuse 侧成本字段静默为 NULL。
本模块在进程启动点，对**被绑定角色**（main/classify/summary/judge）的模型做一次幂等
ensure，把「有价即有定义」钉在启动点上。

**为什么非阻塞**：定价缺失只影响成本可观测量，不影响问答功能。开关关闭 / Langfuse
不可达 / registry 缺角色等任何失败都降级为一行 warning，绝不阻断启动，也**不受**
`PROVIDER_VALIDATE` 影响（后者只负责密钥/配置 fail-fast，二者语义不同）。

**幂等判据复用** `seed_langfuse_models.decide`：任一侧单价为 0（未配置哨兵）判 skipped，
同名已存在判 exists，否则 created —— 与 CLI 真跑 / dry-run 共用同一判据，避免两处口径
漂移。列已有模型名复用其分页 helper `_list_model_names`。
"""

from __future__ import annotations

from typing import Any

from langfuse.api import CreateModelRequest
from loguru import logger

from src.cli.seed_langfuse_models import (
    _list_model_names,
    build_model_request,
    decide,
)
from src.config import settings
from src.infra.llm.provider.spec import ProviderSpec, resolve_role

#: 参与定价 ensure 的角色 = **被绑定角色**。embedding / rerank 不产 LLM token 成本定义，
#: 不在此列（写它们只会污染 Langfuse 模型表）。
_PRICING_ROLES: tuple[str, ...] = ("main", "classify", "summary", "judge")


def ensure_model_pricing(registry: dict[str, ProviderSpec], *, client: Any) -> None:
    """启动期对被绑定角色的模型做一次幂等定价 ensure（非阻塞）。

    Args:
        registry: provider 名 → ProviderSpec（生产传 `factory._registry()`）
        client: Langfuse 客户端（生产传 `langfuse_context.client_instance`）

    行为：`LANGFUSE_ENABLE=false` 时直接返回；否则对 main/classify/summary/judge 解析
    各自最终生效的模型，有定价即按 `decide` 幂等创建定义（同一模型被多角色绑定只创建
    一次 —— 创建后加入 `existing`），无定价记 warning 且不写入（0 是"未配置"哨兵，
    写零价定义会静默低估成本）。整体包在 try/except 内：Langfuse 不可达或 registry
    缺角色等异常都吞掉记 warning，不向上抛 —— 启动不因定价缺失而失败。
    """
    # 开关在调用时读取（与 tracing.configure_tracing 同一口径），不在导入时冻结
    if not settings.LANGFUSE_ENABLE:
        return
    try:
        models = client.api.models
        existing = _list_model_names(models)
        for role in _PRICING_ROLES:
            resolved = resolve_role(role, registry)
            facts = resolved.provider.catalog.get(resolved.model)
            if facts is None:
                price = None
            else:
                price = facts.pricing
            if not price:
                logger.warning(
                    "[provider] pricing seed no_price model={}", resolved.model
                )
                continue
            if decide(resolved.model, price[0], price[1], existing) == "created":
                request = build_model_request(resolved.model, price[0], price[1])
                models.create(request=CreateModelRequest(**request))
                existing.add(resolved.model)
    except Exception as e:  # noqa: BLE001 - 启动期降级：任何失败都只记 warning，不阻断
        logger.warning("[provider] pricing seed degraded err={}", e)
