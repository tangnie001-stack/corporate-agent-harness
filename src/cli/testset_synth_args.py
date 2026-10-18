"""v6 出题：命令行参数解析。"""

from __future__ import annotations

import argparse


def parse_args() -> argparse.Namespace:
    """解析出题命令行参数。

    Returns:
        已校验的参数命名空间

    Raises:
        SystemExit: 参数非法（argparse 以退出码 2 结束）
    """
    parser = argparse.ArgumentParser(description="DeepEval v6 测试集出题")
    parser.add_argument("--kb-id", required=True, help="知识库 UUID")
    parser.add_argument(
        "--version", type=int, default=None, help="版本号（省略=最大版本+1）"
    )
    parser.add_argument(
        "--docs", nargs="*", default=None, help="限定文档文件名（省略=全部）"
    )
    parser.add_argument("--kb-name", default="", help="写入元数据的知识库名")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在版本")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不调用 LLM")
    args = parser.parse_args()
    if args.version is not None and not args.docs:
        parser.error("显式 --version 时必须同时提供 --docs（保证版本可复现）")
    return args
