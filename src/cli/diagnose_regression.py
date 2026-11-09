#!/usr/bin/env python3

"""诊断回归集：生成故障注入 trace → 跑诊断 → 比对根因一致率。

用法：

    LANGFUSE_HOST=http://localhost:3000 python -m src.cli.diagnose_regression
    python -m src.cli.diagnose_regression --run-only    # 复用已有回归集

产物：

    data/diagnosis/regression_set_v1.json    用例与预期根因（含生成的 trace_id）
    data/diagnosis/regression_run_<ts>.json  本轮判定与一致率

说明：注入的是 **trace 事实**（retrieval_contexts / 输出引用标记），不造假判定结果；
判定仍走与生产同一条 pipeline（语义解析 → 五类根因）。context_truncated 依赖日志侧
的预算告警标记，不在本集覆盖（由 tests/rca 的单测与告警链路负责）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import settings
from src.rca.diagnose.models import DiagnosisKind, RootCause, TraceFacts
from src.rca.diagnose.pipeline import diagnose
from src.rca.diagnose.sources import TraceSource

_DATA_DIR = Path("data/diagnosis")
_SET_PATH = _DATA_DIR / "regression_set_v1.json"
_CITED_TEXT = "回答见 [1]：东软集团 2025Q1 归母净利润为 -1209 万元。"
_PLAIN_TEXT = "东软集团 2025Q1 归母净利润为 -1209 万元。"

# 用例规格：case 名 → (预期根因, 每个用例的 trace 事实)
_CASES: dict[str, dict[str, Any]] = {
    "not_retrieved": {
        # 无快照时 trace 入口按设计给"未判定"（分不清"没检索"与"旧 trace 无字段"）；
        # 要判 not_retrieved 必须像告警链路那样直接提供 facts（calls 为空）。
        "expected": RootCause.NOT_RETRIEVED.value,
        "metadata": {"kb_id": "regression", "retrieval_contexts": []},
        "output": _PLAIN_TEXT,
        "count": 4,
        "facts_override": True,
    },
    "context_truncated": {
        "expected": RootCause.CONTEXT_TRUNCATED.value,
        "metadata": {"kb_id": "regression"},
        "output": _CITED_TEXT,
        "count": 4,
        "budget_high": True,
    },
    "low_relevance": {
        "expected": RootCause.LOW_RELEVANCE.value,
        "metadata": {"kb_id": "regression"},
        "output": _PLAIN_TEXT,
        "count": 4,
    },
    "not_cited": {
        "expected": RootCause.NOT_CITED.value,
        "metadata": {"kb_id": "regression"},
        "output": _PLAIN_TEXT,
        "count": 4,
    },
    "undetermined_fallback": {
        "expected": RootCause.UNDETERMINED.value,
        "metadata": {"kb_id": "regression"},
        "output": _CITED_TEXT,
        "count": 4,
    },
    "healthy": {
        "expected": RootCause.UNDETERMINED.value,
        "metadata": {"kb_id": "regression"},
        "output": _CITED_TEXT,
        "count": 2,
    },
}


def _retrieval_calls(case: str) -> list[dict[str, Any]]:
    """按用例造一次 retrieve_kb 的 metadata 快照。

    Args:
        case: 用例名。

    Returns:
        retrieval_contexts 列表；not_retrieved 返回空列表。
    """
    if case == "not_retrieved":
        return []
    if case == "low_relevance":
        scores = [0.20, 0.18, 0.16, 0.15]
        kind = "rerank"
    elif case == "context_truncated":
        scores = [0.82, 0.74, 0.68, 0.61, 0.55]
        kind = "rerank"
    elif case == "not_cited":
        scores = [0.82, 0.71, 0.66, 0.60]
        kind = "rerank"
    elif case == "undetermined_fallback":
        scores = [0.55, 0.40, 0.38]
        kind = "fallback"
    else:  # healthy
        scores = [0.86, 0.72, 0.64]
        kind = "rerank"
    contexts = [
        {"chunk_id": f"chunk-{i}", "score": score, "doc_id": "doc-1"}
        for i, score in enumerate(scores, 1)
    ]
    result_count = 39
    context_count = len(scores)
    if case == "context_truncated":
        result_count = 39
        context_count = 5
    return [
        {
            "call_index": 1,
            "query": "东软集团 2025Q1 归母净利润",
            "top_k": 5,
            "iteration": 1,
            "result_count": result_count,
            "context_count": context_count,
            "score_kind": kind,
            "contexts": contexts,
        }
    ]


def _create_trace(case: str, index: int) -> str:
    """在 Langfuse 建一条带指定事实的 trace。

    Args:
        case: 用例名。
        index: 该用例内的序号（只用于日志）。

    Returns:
        新 trace id（32 位 hex）。
    """
    from langfuse import get_client
    from langfuse.types import TraceContext

    client = get_client()
    trace_id = uuid.uuid4().hex
    spec = _CASES[case]
    metadata: dict[str, Any] = dict(spec["metadata"])
    metadata["retrieval_contexts"] = _retrieval_calls(case)
    metadata["regression_case"] = case
    span = client.start_observation(
        trace_context=TraceContext(trace_id=trace_id),
        name="chat_turn",
        as_type="span",
        metadata=metadata,
        output=spec["output"],
    )
    span.end()
    client.flush()
    print(f"  generated {case}#{index} trace_id={trace_id}")
    return trace_id


def _generate() -> list[dict[str, Any]]:
    """生成整份回归集并落盘。

    Returns:
        用例列表（含 trace_id）。
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    for case, spec in _CASES.items():
        for index in range(1, int(spec["count"]) + 1):
            trace_id = _create_trace(case, index)
            samples.append(
                {
                    "case": case,
                    "index": index,
                    "trace_id": trace_id,
                    "expected_root_cause": spec["expected"],
                }
            )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "langfuse_host": settings.LANGFUSE_HOST,
        "samples": samples,
    }
    _SET_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"回归集已写入 {_SET_PATH}（{len(samples)} 条）")
    return samples


def _load() -> list[dict[str, Any]]:
    """读取已有回归集。

    Returns:
        samples 列表。

    Raises:
        SystemExit: 文件不存在时提示先生成。
    """
    if not _SET_PATH.exists():
        print(f"回归集不存在：{_SET_PATH}，先不加 --run-only 运行一次")
        raise SystemExit(2)
    return json.loads(_SET_PATH.read_text(encoding="utf-8"))["samples"]


def _diagnose_with_retry(
    trace_id: str, source: TraceSource, spec: dict[str, Any], *, attempts: int = 3
) -> Any:
    """跑诊断；为空快照时重试（Langfuse 写入到可查有秒级延迟）。

    Args:
        trace_id: 目标 trace。
        source: trace 证据源。
        spec: 用例规格（决定是否注入 facts / 预算标记）。
        attempts: 最多尝试次数。

    Returns:
        DiagnosisReport；取最后一次结果。
    """
    report: Any = None
    for index in range(1, attempts + 1):
        report = diagnose(
            trace_id,
            kind=DiagnosisKind.ANSWER,
            question="",
            trace_source=source,
            log_source=None,
            history_source=None,
            facts=(
                TraceFacts(calls=(), evidence=(), cited=False)
                if spec.get("facts_override")
                else None
            ),
            has_context_budget_high=bool(spec.get("budget_high")),
        )
        transient = "无检索快照" in report.summary or "trace 不可用" in report.summary
        if not transient:
            return report
        if index < attempts:
            time.sleep(2)
    return report


def _run(samples: list[dict[str, Any]]) -> int:
    """逐条诊断并比对根因。

    Args:
        samples: 用例列表。

    Returns:
        进程退出码：一致率 < 80% 或任一用例异常时为 1。
    """
    source = TraceSource()
    results: list[dict[str, Any]] = []
    hits = 0
    for item in samples:
        trace_id = str(item["trace_id"])
        spec = _CASES.get(str(item["case"]), {})
        try:
            report = _diagnose_with_retry(trace_id, source, spec)
            actual = report.root_cause.value
            summary = report.summary
        except Exception as exc:  # noqa: BLE001  # 单条失败不中断整轮
            actual = "error"
            summary = f"{type(exc).__name__}: {exc}"
        ok = actual == item["expected_root_cause"]
        hits += int(ok)
        results.append(
            {**item, "actual_root_cause": actual, "match": ok, "summary": summary}
        )
        flag = "OK " if ok else "MISMATCH"
        print(
            f"  {flag} {item['case']}#{item['index']} expected={item['expected_root_cause']} actual={actual}"
        )

    total = len(results)
    rate = hits / total if total else 0.0
    out_path = _DATA_DIR / f"regression_run_{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    out_path.write_text(
        json.dumps(
            {
                "at": datetime.now(UTC).isoformat(),
                "agreement": rate,
                "total": total,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n根因一致率 {hits}/{total} = {rate:.1%}（门槛 80%）")
    print(f"结果已写入 {out_path}")
    return 0 if rate >= 0.8 else 1


def main(argv: list[str] | None = None) -> int:
    """入口：生成 / 复用回归集并跑诊断比对。

    Args:
        argv: 命令行参数（测试可注入）。

    Returns:
        进程退出码。
    """
    parser = argparse.ArgumentParser(description="诊断回归集（故障注入）")
    parser.add_argument(
        "--run-only", action="store_true", help="复用已有回归集，不重新生成"
    )
    args = parser.parse_args(argv)

    if args.run_only:
        samples = _load()
    else:
        samples = _generate()
    return _run(samples)


if __name__ == "__main__":
    sys.exit(main())
