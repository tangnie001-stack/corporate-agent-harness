"""症状指标采集：从应用日志统计三个"模型是否还在盲试"的原始指标。

口径（`design.md` D5 成功度量的「观测」层，复用现成日志、不新增埋点）：

| 指标 | 来源事件 | 口径 |
|---|---|---|
| `iteration limit` 触顶率 | `iteration limit`（agent 循环） | **按 trace 计**：出现该事件的 trace 数 / **有 agent 行为的 trace 数** |
| 每请求 `retrieve_kb` 调用次数分布 | `retrieve done`（检索工具内） | **按 trace 计**：该 trace 下 `retrieve done` 的行数 |
| `answer_len=0` 占比 | `completeness check`（verify 态 B） | **按事件计**：`answer_len=0` 的条数 / `completeness check` 条数 |

⚠ **触顶率的分母口径**：日志里没有"请求开始"的统一锚点，所以分母取"出现任一已知
agent 事件（触顶 ∪ 检索 ∪ 完整性检查）的 trace"。读作"在有 agent 行为的请求里有多少触顶"，
**不是**"占全部请求的比例"—— 纯闲聊类请求不进分母。三个指标共用这一分母以便互相参照。

⚠ 为什么检索次数不取 `retrieval_signal`：eval 链路全程不设置 `RequestContext`
（`src/cli/eval_deepeval.py` 不引用 `current_request_ctx`），`retrieve_call_seq` 恒为 0、
`empty_result` / `reretrieve` 行为信号在 eval 请求下不产生。`retrieve done` 是工具内
无条件落的事件，在两条路径下都可用。

日志行格式（`src/core/logging.py:38`）：
    time|level|trace_id|session_id|module:func:line - message
第 3 段（下标 2）是 trace_id（`trace_*` / eval 下的 `eval_*`），按它分组即"每请求"。
"""

import argparse
import glob
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

_ITERATION_LIMIT = "[agent] iteration limit "
_RETRIEVE_DONE = "[retrieval] retrieve done "
_COMPLETENESS_CHECK = "[verify] completeness check "

_ANSWER_LEN = re.compile(r"answer_len=(\d+)")


@dataclass
class SymptomStats:
    """一次采集的聚合结果（全部字段均为该批日志的原始计数，不做归一化）。"""

    traces_total: int = (
        0  # 有 agent 行为的 trace 数（触顶 ∪ 检索 ∪ 完整性检查），作触顶率分母
    )
    traces_iteration_limit: int = 0  # 出现 iteration limit 的 trace 数
    retrieve_counts: list[int] = field(
        default_factory=list
    )  # 每 trace 的 retrieve done 次数
    completeness_checks: int = 0  # completeness check 事件条数（空答率的分母）
    empty_answers: int = 0  # answer_len=0 的事件条数


def parse_line(line: str) -> tuple[str, str] | None:
    """从一行日志取 (trace_id, message)；格式不符返回 None。

    段位无关：只依赖"第 5 段里含 ` - ` 分隔 message"，不校验前四段的语义。
    """
    parts = line.split("|", 4)
    if len(parts) < 5:
        return None
    tail = parts[4]
    if " - " not in tail:
        return None
    return parts[2].strip(), tail.split(" - ", 1)[1].rstrip("\n")


def collect(log_dir: str) -> SymptomStats:
    """扫 log_dir 下全部 app_*.log，聚合三个指标。

    Args:
        log_dir: 日志目录（容器内 `/data/logs`，本机默认 `logs/`）

    Returns:
        SymptomStats；目录不存在或无匹配文件时返回全零结果（不抛异常）
    """
    iteration_traces: set[str] = set()
    seen_traces: set[str] = set()
    retrieve_counter: Counter[str] = Counter()
    checks = 0
    empties = 0

    for path in sorted(glob.glob(os.path.join(log_dir, "app_*.log"))):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parsed = parse_line(line)
                if parsed is None:
                    continue
                trace_id, message = parsed
                if message.startswith(_ITERATION_LIMIT):
                    seen_traces.add(trace_id)
                    iteration_traces.add(trace_id)
                elif message.startswith(_RETRIEVE_DONE):
                    seen_traces.add(trace_id)
                    retrieve_counter[trace_id] += 1
                elif message.startswith(_COMPLETENESS_CHECK):
                    seen_traces.add(trace_id)
                    checks += 1
                    matched = _ANSWER_LEN.search(message)
                    if matched is not None and int(matched.group(1)) == 0:
                        empties += 1

    counts = list(retrieve_counter.values())
    # 有 trace 但从未检索的请求记 0 次，否则分布会系统性偏高
    counts.extend([0] * (len(seen_traces) - len(retrieve_counter)))

    return SymptomStats(
        traces_total=len(seen_traces),
        traces_iteration_limit=len(iteration_traces),
        retrieve_counts=counts,
        completeness_checks=checks,
        empty_answers=empties,
    )


def _pct(numerator: int, denominator: int) -> str:
    """百分比，一位小数；分母为 0 时返回 n/a（不抛 ZeroDivisionError）。"""
    if denominator == 0:
        return "n/a"
    return f"{numerator / denominator * 100:.1f}%"


def format_report(stats: SymptomStats) -> str:
    """把聚合结果渲染为纯文本报告（与 T7 留档记录的表格同口径）。"""
    lines = [
        "症状指标（prompt-layering P2）",
        f"有 agent 行为的请求数（trace）：{stats.traces_total}",
        "",
        "① agent 循环触顶",
        (
            f"  iteration limit 触顶率：{_pct(stats.traces_iteration_limit, stats.traces_total)}"
            f"（{stats.traces_iteration_limit}/{stats.traces_total}）"
        ),
        "",
        "② 每请求 retrieve_kb 调用次数分布",
    ]
    if stats.retrieve_counts:
        distribution = Counter(stats.retrieve_counts)
        for calls in sorted(distribution):
            lines.append(f"  {calls}: {distribution[calls]}")
    else:
        lines.append("  （无数据）")
    lines += [
        "",
        "③ verify 空答案",
        (
            f"  answer_len=0 占比：{_pct(stats.empty_answers, stats.completeness_checks)}"
            f"（{stats.empty_answers}/{stats.completeness_checks}）"
        ),
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="从应用日志统计三个症状指标")
    parser.add_argument(
        "--log-dir",
        default=os.getenv("LOG_DIR", "logs"),
        help="日志目录（默认取 LOG_DIR 环境变量，回退 ./logs）",
    )
    parser.add_argument("--out", default=None, help="把报告写到该文件（默认只打印）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：采集并输出报告。"""
    args = parse_args(argv)
    stats = collect(args.log_dir)
    report = format_report(stats)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
