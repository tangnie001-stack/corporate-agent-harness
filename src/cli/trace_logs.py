"""trace_logs — 按 trace_id 拉取该次请求的完整日志（含裁判调用与框架内部请求级证据）。

用法：
    python -m src.cli.trace_logs eval_19128e15c446          # 报告里那列 trace_id
    python -m src.cli.trace_logs trace_eeed43a3 --grep "judge call"

为什么需要它：报告只给出每题的 trace_id；要定位"为什么这题失败"，需要把该 trace 下
**所有**日志串起来看。按本仓现有分工，判官相关日志有两类：
  - `judge call` / `metric retry` 事件：**外层**对桥度量 `measure` 的调用（尝试次数 /
    耗时 / 是否拿到分 / 异常链摘要 `note`），由 `src/cli/eval_ragas.py` 埋点；
  - `[provider] bridge judge http ...` 日志：**框架内部**（ragas 桥）真实 HTTP 请求级
    证据（状态码 / 响应体摘要 / `finish_reason`），由裁判 `http_async_client` 的
    event hooks 记录（design D13）。
> 说明：`note` 现为异常链摘要（`_exc_chain_summary`），**不含** `finish_reason` /
> 输出 token 数——那两样在 hooks 日志里（换桥后原 `_judge_failure_note` 已不适用）。

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
    if not lines:
        if args.count:
            print(0)
            return
        print(f"未找到 trace_id={args.trace_id} 的日志（目录 {args.log_dir}）")
        sys.exit(1)
    if args.grep:
        lines = [line for line in lines if args.grep in line]
        if not lines and not args.count:
            # trace 存在但无匹配行：与「trace 不存在」区分开，避免误导
            print(f"trace_id={args.trace_id} 有日志，但没有匹配 {args.grep!r} 的行")
            return
    if args.count:
        print(len(lines))
        return
    print("\n".join(lines))


if __name__ == "__main__":
    main()
