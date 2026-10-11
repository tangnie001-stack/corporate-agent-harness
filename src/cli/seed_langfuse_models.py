"""把模型定价写入 Langfuse（幂等）。

**为什么是幂等 CLI 而不是 UI 手配**：dev / prod 要各自落地同一份定价，手配不可复现；
而 Langfuse v2 的 `LANGFUSE_INIT_*` 只能播种 project / user / key，**不能播种模型定价**。

**为什么 0 要跳过**：0 是「未配置」的哨兵，输入/输出两侧都必须配置齐全。任一侧为 0
都跳过：写入一个缺价（如 output 为 0）的模型定义会把补全 token 按零价计，成本被静默
低估，且下游 `calculated_total_cost > 0` 的成本闸门仍会通过；写一个单侧为 0 的定义比
不写更糟。

**为什么 pattern 必须锚定**：服务端用 Postgres 的 `~` 做匹配，那是**子串**正则 ——
未锚定的 `(?i)qwen3.8-flash` 会命中 `prefix-qwen3.8-flash` / `qwen3.8-flash-old`
这类形近名，把单价套到别的模型上（是**误配**，不是匹配不到）。

**定价来源**：registry.yaml 各 provider 的 `catalog`（唯一数据面）。遍历其中声明的全部
模型，无 `pricing` 的模型由 `decide` 判为 skipped，不写入。

用法：
    python -m src.cli.seed_langfuse_models            # 按 registry 写入全部有价模型
    python -m src.cli.seed_langfuse_models --dry-run  # 只读后端，逐项打印将执行的动作

退出码：0 = 正常（含 skipped / exists）；1 = 失败（配置缺失或后端不可达）。
"""

import argparse
import asyncio
import re
import sys
from typing import Any

from langfuse.api import CreateModelRequest
from langfuse.decorators import langfuse_context

from src.config import LANGFUSE_ENABLE
from src.infra.llm.provider.spec import ProviderSpec, load_registry

#: Langfuse 模型定义的计价单位（配合 input_price/output_price 即「按单 token 计价」；
#: 这两个价格字段本身不含币种，本仓部署填的是人民币数值，口径见 `settings.py` 的定价注释）
UNIT_TOKENS = "TOKENS"

#: `models.list` 的翻页大小
LIST_PAGE_SIZE = 100


def build_model_request(
    model_name: str, input_price: float, output_price: float
) -> dict[str, Any]:
    """构造 Langfuse 模型定义的请求参数。

    Args:
        model_name: 模型名（取自 registry catalog）
        input_price: 输入单价（按单 token；币种口径见 `src/config/settings.py` 的定价注释）
        output_price: 输出单价（按单 token；币种口径见 `src/config/settings.py` 的定价注释）

    Returns:
        含 `model_name` / `match_pattern` / `unit` / `input_price` / `output_price`
        的字典；`match_pattern` 为锚定正则，由 `re.escape` 构造以防误配。
    """
    return {
        "model_name": model_name,
        "match_pattern": "(?i)^" + re.escape(model_name) + "$",
        "unit": UNIT_TOKENS,
        "input_price": input_price,
        "output_price": output_price,
    }


def decide(
    model_name: str, input_price: float, output_price: float, existing: set[str]
) -> str:
    """判定单条模型定义应执行的动作（纯函数，dry-run 与真跑共用判据）。

    Args:
        model_name: 模型名
        input_price: 输入单价（按单 token）
        output_price: 输出单价（按单 token）
        existing: Langfuse 侧已存在的模型名集合

    Returns:
        "skipped"（任一侧单价为 0，未配置哨兵）/ "exists"（同名已存在）/
        "created"（本次应创建）。
    """
    if input_price == 0.0 or output_price == 0.0:
        return "skipped"
    if model_name in existing:
        return "exists"
    return "created"


def _list_model_names(models: Any) -> set[str]:
    """翻页列出 Langfuse 侧已有的模型名。

    Args:
        models: `client_instance.api.models` 客户端

    Returns:
        已存在的模型名集合
    """
    names: set[str] = set()
    page = 1
    while True:
        response = models.list(page=page, limit=LIST_PAGE_SIZE)
        rows = response.data or []
        for row in rows:
            names.add(row.model_name)
        if len(rows) < LIST_PAGE_SIZE:
            return names
        page += 1


def _catalog_pricing(
    registry: dict[str, ProviderSpec],
) -> list[tuple[str, float, float]]:
    """收集 registry 各 provider catalog 声明的模型及其定价。

    无 `pricing` 的模型以 `(0.0, 0.0)` 表示 —— 0 是「未配置」哨兵，`decide` 会判为
    skipped，于是 dry-run 能如实报告「将跳过」，真跑也不写入。同名模型只取首个
    provider 的声明。

    Args:
        registry: provider 名 → ProviderSpec

    Returns:
        `[(model_name, input_price, output_price), ...]`
    """
    collected: dict[str, tuple[float, float]] = {}
    for spec in registry.values():
        for model_name, facts in spec.catalog.items():
            if facts.pricing is None:
                collected.setdefault(model_name, (0.0, 0.0))
            else:
                collected.setdefault(model_name, facts.pricing)
    return [
        (model_name, prices[0], prices[1]) for model_name, prices in collected.items()
    ]


async def seed(
    client: Any, model_name: str, input_price: float, output_price: float
) -> str:
    """幂等写入一条模型定价。

    Args:
        client: Langfuse 客户端（生产传 `langfuse_context.client_instance`）
        model_name: 模型名
        input_price: 输入单价（按单 token；币种口径见 `src/config/settings.py` 的定价注释）
        output_price: 输出单价（按单 token；币种口径见 `src/config/settings.py` 的定价注释）

    Returns:
        "skipped"（单价未配置完整）/ "exists"（同名已存在）/ "created"（本次创建）

    单价需两侧齐全：任一侧为 0（未配置哨兵）即跳过，且不查询后端。
    """
    models = client.api.models
    if input_price == 0.0 or output_price == 0.0:
        existing: set[str] = set()
    else:
        existing = _list_model_names(models)

    outcome = decide(model_name, input_price, output_price, existing)
    if outcome == "skipped":
        print(
            f"[skip] 单价未配置完整（in={input_price} out={output_price}）"
            "——不写入会低估成本的模型定义"
        )
        return outcome
    if outcome == "exists":
        print(f"[exists] 模型定义已存在，跳过：{model_name}")
        return outcome

    request = build_model_request(model_name, input_price, output_price)
    models.create(request=CreateModelRequest(**request))
    print(
        f"[created] {model_name} "
        f"pattern={request['match_pattern']} "
        f"unit={request['unit']} "
        f"in={input_price} out={output_price} (per token)"
    )
    return outcome


async def _seed_all(client: Any, targets: list[tuple[str, float, float]]) -> None:
    """逐条幂等写入 registry 声明的全部有价模型。

    Args:
        client: Langfuse 客户端
        targets: `[(model_name, input_price, output_price), ...]`
    """
    for model_name, input_price, output_price in targets:
        await seed(client, model_name, input_price, output_price)


def _dry_run(client: Any, targets: list[tuple[str, float, float]]) -> int:
    """只读后端已有的模型名，逐项如实打印 `decide` 的结论（不写入）。

    Args:
        client: Langfuse 客户端
        targets: `[(model_name, input_price, output_price), ...]`

    Returns:
        退出码 0
    """
    existing = _list_model_names(client.api.models)
    for model_name, input_price, output_price in targets:
        outcome = decide(model_name, input_price, output_price, existing)
        if outcome == "skipped":
            print(f"[dry-run] 将跳过（未配价）：{model_name}")
        elif outcome == "exists":
            print(f"[dry-run] 已存在：{model_name}")
        else:
            print(
                f"[dry-run] 将创建：{model_name} "
                f"in={input_price} out={output_price} (per token)"
            )
    return 0


def run(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Args:
        argv: 命令行参数（None 时取 sys.argv[1:]）

    Returns:
        退出码：0 正常；1 失败
    """
    parser = argparse.ArgumentParser(description="把模型定价写入 Langfuse（幂等）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读后端，逐项打印将执行的动作（不写入）",
    )
    args = parser.parse_args(argv)

    targets = _catalog_pricing(load_registry())

    if not LANGFUSE_ENABLE:
        print("[error] LANGFUSE_ENABLE=false，跳过")
        return 1

    client = langfuse_context.client_instance

    if args.dry_run:
        try:
            return _dry_run(client, targets)
        except Exception as exc:  # noqa: BLE001 - 后端不可达等统一收敛为一行
            print(f"[error] 读取失败：{exc}")
            return 1

    try:
        asyncio.run(_seed_all(client, targets))
    except Exception as exc:  # noqa: BLE001 - 后端不可达等统一收敛为一行
        print(f"[error] 写入失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
