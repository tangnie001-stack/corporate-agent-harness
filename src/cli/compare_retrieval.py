#!/usr/bin/env python3
"""对比 TOP_K_RETRIEVAL x TOP_K_RERANK 参数组合的 RAGAS 指标。

遍历检索和重排序 top-K 值的所有组合（3x3 网格），在终端打印
并排对比表格。用于实验确定最优的检索参数。

Usage:
    python -m src.cli.compare_retrieval --kb-name rag_eval
"""

import argparse
import asyncio
import os
import re
import subprocess
import sys
from itertools import product

from src.infra.db.engine import session_factory
from src.infra.db.repos import KbRepo

# 检索 top-K 搜索网格
RETRIEVAL_VALUES = [10, 30, 50]
# 重排序 top-K 搜索网格
RERANK_VALUES = [3, 5, 8]


async def _resolve_kb_id(kb_name: str) -> str:
    """将知识库名称解析为 UUID。

    Args:
        kb_name: 知识库名称。

    Returns:
        匹配的知识库 UUID。

    Raises:
        ValueError: 未找到同名知识库。
    """
    repo = KbRepo(session_factory)
    all_kbs = await repo.get_all_kb()
    for kb in all_kbs:
        if kb.name == kb_name:
            return kb.id
    raise ValueError(f"Knowledge base '{kb_name}' not found")


def run_eval(retrieval_k: int, rerank_k: int, kb_id: str) -> dict:
    """通过环境变量覆盖 TOP_K 值后运行 eval_ragas。

    在子进程环境中设置 TOP_K_RETRIEVAL 和 TOP_K_RERANK，
    从而在不修改 .env 文件的情况下让应用读取覆盖后的值。

    Args:
        retrieval_k: TOP_K_RETRIEVAL 的值。
        rerank_k: TOP_K_RERANK 的值。
        kb_id: 待评估的知识库 UUID。

    Returns:
        包含 'retrieval_k', 'rerank_k', 'stdout',
        'stderr', 'returncode' 的字典。
    """
    env = os.environ.copy()
    env["TOP_K_RETRIEVAL"] = str(retrieval_k)
    env["TOP_K_RERANK"] = str(rerank_k)
    result = subprocess.run(
        ["python", "-m", "src.cli.eval_ragas", "--kb-id", kb_id],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return {
        "retrieval_k": retrieval_k,
        "rerank_k": rerank_k,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def parse_metrics(stdout: str) -> dict[str, float]:
    """从 eval_ragas 的标准输出中解析 RAGAS 指标均值。

    口径说明：eval_ragas 的 stdout 只打「主分」一行/指标（仅纯 KB 样本的
    指标均值，与报告 / 门禁一致），故此处解析到的即主分。

    Args:
        stdout: eval_ragas 的原始标准输出。

    Returns:
        指标名（faithfulness, answer_relevancy,
        context_precision, context_recall）到浮点值的映射，
        缺失指标不包含在内。
    """
    metrics: dict[str, float] = {}
    metric_names = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]
    for line in stdout.splitlines():
        for name in metric_names:
            if name in line.lower():
                segment = line.split(":")[-1] if ":" in line else line
                match = re.search(r"[\d.]+", segment)
                if match:
                    metrics[name] = float(match.group(0))
    return metrics


def main() -> None:
    """入口：遍历参数网格，打印结果表格。"""
    parser = argparse.ArgumentParser(description="对比检索参数组合的 RAGAS 指标")
    parser.add_argument("--kb-name", default="rag_eval", help="知识库名称")
    args = parser.parse_args()

    # 名称 → UUID（eval_ragas 只接受 --kb-id）
    try:
        kb_id = asyncio.run(_resolve_kb_id(args.kb_name))
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(1)

    # 打印表头
    print(
        f"{'RETRIEVE':>8} {'RERANK':>6} {'faithfulness':>14} {'answer_relevancy':>18} "
        f"{'context_precision':>18} {'context_recall':>14}"
    )
    print("-" * 85)

    for r_k, rp_k in product(RETRIEVAL_VALUES, RERANK_VALUES):
        res = run_eval(r_k, rp_k, kb_id)
        m = parse_metrics(res["stdout"])
        print(
            f"{r_k:>8} {rp_k:>6} {m.get('faithfulness', 'N/A'):>14} "
            f"{m.get('answer_relevancy', 'N/A'):>18} "
            f"{m.get('context_precision', 'N/A'):>18} "
            f"{m.get('context_recall', 'N/A'):>14}"
        )


if __name__ == "__main__":
    main()
