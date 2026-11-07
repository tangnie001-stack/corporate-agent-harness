#!/usr/bin/env python3

"""诊断 CLI — 给定 trace_id 输出根因报告。

用法：

    python -m src.cli.diagnose trace_xxx --langfuse-host http://localhost:3000

前提：

  - trace 存在且含 `retrieval_contexts`（2026-10-19 之后的 trace）
  - 本地有应用日志目录（默认 /data/logs，可用 --log-dir 覆盖）
"""

from __future__ import annotations

import argparse
import sys

from src.config import settings
from src.rca.diagnose.history import HistorySource
from src.rca.diagnose.logs import LocalLogSource
from src.rca.diagnose.models import DiagnosisReport
from src.rca.diagnose.pipeline import diagnose
from src.rca.diagnose.sources import TraceSource


def _print(rep: DiagnosisReport) -> None:
    """打印报告：结论 → 依据 → 证据 → 建议。"""
    print(f"trace      : {rep.trace_id}")
    print(f"root_cause : {rep.root_cause.value}")
    print(f"结论       : {rep.summary}")
    if rep.assertions:
        print("依据       :")
        for text, refs in rep.assertions:
            print(f"  - {text}  [{' '.join(refs)}]")
    if rep.evidence:
        print("证据       :")
        for e in rep.evidence:
            print(f"  {e.id} [{e.kind}] {e.locator} — {e.summary}")
    if rep.next_actions:
        print("建议       :")
        for action in rep.next_actions:
            print(f"  - {action}")


def main(argv: list[str] | None = None) -> int:
    """入口：解析参数并打印报告。"""
    parser = argparse.ArgumentParser(description="trace 诊断")
    parser.add_argument("trace_id", help="形如 trace_<uuid>")
    parser.add_argument("--langfuse-host", default=settings.LANGFUSE_HOST)
    parser.add_argument("--log-dir", default="/data/logs")
    parser.add_argument("--no-logs", action="store_true", help="跳过日志证据")
    parser.add_argument("--no-history", action="store_true", help="跳过前序轮次追溯")
    parser.add_argument("--question", default="", help="本轮用户问题（用于追问判定）")
    args = parser.parse_args(argv)

    def _fetch(trace_id: str):
        from src.cli.eval_trace_io import fetch_trace

        return fetch_trace(trace_id, args.langfuse_host)

    report = diagnose(
        args.trace_id,
        question=args.question,
        trace_source=TraceSource(fetcher=_fetch),
        log_source=None if args.no_logs else LocalLogSource(args.log_dir),
        history_source=None if args.no_history else HistorySource(),
    )
    _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
