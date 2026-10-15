"""trace_logs — 按 trace_id 拉取该次请求的完整日志（含裁判调用与模型原始响应）。

用法：
    python -m src.cli.trace_logs eval_19128e15c446          # 报告里那列 trace_id
    python -m src.cli.trace_logs trace_eeed43a3 --grep "judge call"

为什么需要它：报告只给出每题的 trace_id；要定位"为什么这题失败"，需要把该 trace 下
**所有**日志串起来看——尤其是 `judge call` 事件（含 `finish_reason` / 输出 token 数 /
**底层模型原始输出片段**）。此前这些信息只能靠人工 grep。

实现：段位无关（不依赖 `|` 分段），在 `logs/app_*.log` 与 `logs/error_*.log` 里按
trace_id **子串**过滤（与 `replay_trace` 同一套约定；trace 可跨天）。
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

DEFAULT_LOG_DIR = "logs"


def iter_trace_lines(trace_id: str, log_dir: str = DEFAULT_LOG_DIR) -> list[str]:
    """返回包含 trace_id 的日志行（按文件、行序；跨天全部读取）。

    Args:
        trace_id: 报告里的 trace_id（如 `eval_19128e15c446`）；按**子串**匹配
        log_dir: 日志目录

    Returns:
        匹配到的原始日志行列表
    """
    lines: list[str] = []
    for pattern in ("app_*.log", "error_*.log"):
        for path in sorted(glob.glob(str(Path(log_dir) / pattern))):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    lines.extend(line.rstrip("\n") for line in fh if trace_id in line)
            except OSError as exc:  # 日志文件不可读不应让工具崩
                print(f"warn: 跳过 {path}: {exc}", file=sys.stderr)
    return lines


def main() -> None:
    """按 trace_id 打印日志；`--grep` 可再按子串收窄。"""
    parser = argparse.ArgumentParser(
        description="按 trace_id 拉取日志（含 judge call 的原始模型响应）"
    )
    parser.add_argument("trace_id", help="报告里的 trace_id，如 eval_19128e15c446")
    parser.add_argument(
        "--log-dir", default=DEFAULT_LOG_DIR, help="日志目录（默认 logs）"
    )
    parser.add_argument(
        "--grep", default=None, help="只看含该子串的行（如 'judge call'）"
    )
    parser.add_argument("--count", action="store_true", help="只打印匹配行数后退出")
    args = parser.parse_args()

    lines = iter_trace_lines(args.trace_id, args.log_dir)
    if args.grep:
        lines = [line for line in lines if args.grep in line]
    if args.count:
        print(len(lines))
        return
    if not lines:
        print(f"未找到 trace_id={args.trace_id} 的日志（目录 {args.log_dir}）")
        sys.exit(1)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
