"""单条 Langfuse trace 回放：字段解析与 reference-free DeepEval 评分。

用途：从 Langfuse 取一条可疑 trace，还原 query / answer / contexts / 工具调用，
在本机跑 DeepEval 指标，输出可下钻的 JSON。默认只读 trace，不改线上分数。

边界：
- 生产 trace 没有 gold reference，因此默认只跑 reference-free 指标
  （faithfulness / answer_relevancy / contextual_relevancy）；
  传 ``--reference`` 才追加 context_precision / context_recall。
- retrieval contexts 从 ``retrieve_kb`` 工具输出文本解析，属于近似还原；
  检索阶段（dense/lexical/rerank/dedup）不在生产 trace 内。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.cli.eval_trace_analysis import analyze_trace, summarize_batch
from src.cli.eval_trace_io import (
    as_text,
    fetch_recent,
    fetch_trace,
    latency_ms,
    parse_since,
)
from src.cli.eval_trace_metrics import run_metrics
from src.config import settings

_DEFAULT_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "contextual_relevancy",
)
_REFERENCE_METRICS = ("context_precision", "context_recall")
_CITATION_RE = re.compile(r"\[(\d+)\]\s*来源:\s*(.+?)\s*\(第(\d+)页")
_BLOCK_SPLIT_RE = re.compile(r"\n(?=\[\d+\]\s*来源:)")


def _split_contexts(content: str) -> list[dict]:
    """把 retrieve_kb 的 prompt 文本拆成逐条 context。

    Args:
        content: `retrieve_kb` 返回的带引用编号文本

    Returns:
        每项含 source / page / text；source/page 解析失败时为 None
    """
    items: list[dict] = []
    seen: set[str] = set()
    for block in _BLOCK_SPLIT_RE.split(content.strip()):
        if not block.strip():
            continue
        match = _CITATION_RE.search(block)
        source = match.group(2).strip() if match else None
        page = int(match.group(3)) if match else None
        if "内容:" in block:
            text = block.split("内容:", 1)[1].strip()
        else:
            text = block.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append({"source": source, "page": page, "text": text})
    return items


def parse_trace(trace: Any) -> dict:
    """从 trace 还原回放所需字段。

    Args:
        trace: `TraceWithFullDetails`

    Returns:
        含 query / answer / contexts / tool_calls / generations 的 dict
    """
    trace_input = trace.input if isinstance(trace.input, dict) else {}
    query = str(trace_input.get("query") or "")
    answer = as_text(trace.output)

    contexts: list[dict] = []
    tool_calls: list[dict] = []
    generations: list[dict] = []
    for obs in trace.observations or []:
        name = obs.name or ""
        if name == "retrieve_kb":
            content = as_text(obs.output)
            contexts.extend(_split_contexts(content))
            tool_calls.append(
                {
                    "name": name,
                    "args": obs.input,
                    "result_chars": len(content),
                    "latency_ms": latency_ms(obs.latency),
                }
            )
            continue
        if obs.type == "SPAN" and name in {"search_web", "delegate_task"}:
            tool_calls.append(
                {
                    "name": name,
                    "args": obs.input,
                    "result_chars": len(as_text(obs.output)),
                    "latency_ms": latency_ms(obs.latency),
                }
            )
            continue
        if obs.type == "GENERATION":
            generations.append(
                {
                    "name": name,
                    "model": obs.model,
                    "input_messages": len(obs.input)
                    if isinstance(obs.input, list)
                    else None,
                    "output_type": type(obs.output).__name__,
                    "latency_ms": latency_ms(obs.latency),
                }
            )

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in contexts:
        if item["text"] in seen:
            continue
        seen.add(item["text"])
        deduped.append(item)

    return {
        "trace_id": trace.id,
        "session_id": trace.session_id,
        "timestamp": str(trace.timestamp),
        "tags": trace.tags,
        "metadata": trace.metadata,
        "query": query,
        "answer": answer,
        "contexts": deduped,
        "retrieval_trace": (trace.metadata or {}).get("retrieval_contexts") or [],
        "tool_calls": tool_calls,
        "generations": generations,
    }


def _write_report(
    parsed: dict, output_dir: str, metrics: dict | None, analysis: dict
) -> Path:
    """把解析结果、指标与归因写成 JSON。

    Args:
        parsed: `parse_trace` 产物
        output_dir: 输出目录
        metrics: 指标结果；None 表示 inspect-only
        analysis: `analyze_trace` 产物

    Returns:
        实际写入的文件路径
    """
    target = Path(output_dir) / parsed["trace_id"]
    target.mkdir(parents=True, exist_ok=True)
    payload = dict(parsed)
    if metrics is not None:
        payload["metrics"] = metrics
    payload["analysis"] = analysis
    path = target / "trace.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _summary(parsed: dict, metrics: dict | None, analysis: dict) -> dict:
    """构造 stdout 摘要，避免把上下文正文打进终端。"""
    contexts = parsed["contexts"]
    return {
        "trace_id": parsed["trace_id"],
        "session_id": parsed["session_id"],
        "query": parsed["query"],
        "answer_chars": len(parsed["answer"]),
        "answer_preview": parsed["answer"][:120],
        "contexts_count": len(contexts),
        "structured_retrieval_calls": len(parsed.get("retrieval_trace") or []),
        "context_sources": [
            {"source": item["source"], "page": item["page"], "chars": len(item["text"])}
            for item in contexts
        ],
        "tool_calls": parsed["tool_calls"],
        "generations": parsed["generations"],
        "metrics": metrics,
        "analysis": analysis,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Langfuse 线上 trace 回放、评分与确定性归因"
    )
    parser.add_argument("--trace-id", default="", help="指定单条 trace ID")
    parser.add_argument(
        "--list",
        action="store_true",
        help="只列出最近的 chat trace，不评分",
    )
    parser.add_argument(
        "--since",
        default="",
        help="起始时间（ISO 或 YYYY-MM-DD）；仅批量模式使用",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="批量模式最多处理/列出的 trace 数（默认 3）",
    )
    parser.add_argument(
        "--langfuse-host",
        default=settings.LANGFUSE_HOST,
        help="Langfuse 地址；本机调试用 http://localhost:3000",
    )
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="只解析字段与归因，不调用裁判",
    )
    parser.add_argument(
        "--metrics",
        default=",".join(_DEFAULT_METRICS),
        help="逗号分隔的指标名（默认 reference-free 三项）",
    )
    parser.add_argument(
        "--reference", default="", help="可选参考答案；提供后追加 CP/CR（仅单条）"
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(settings.EVAL_REPORT_DIR).parent / "traces"),
        help="回放 JSON 输出目录",
    )
    return parser.parse_args(argv)


def _list_recent(args: argparse.Namespace) -> None:
    """列出最近 chat trace 的摘要。"""
    traces = fetch_recent(
        args.langfuse_host, since=parse_since(args.since), limit=args.limit
    )
    for trace in traces:
        trace_input = trace.input if isinstance(trace.input, dict) else {}
        query = str(trace_input.get("query") or "")
        answer = as_text(trace.output)
        print(
            json.dumps(
                {
                    "trace_id": trace.id,
                    "timestamp": str(trace.timestamp),
                    "query": query[:160],
                    "answer_chars": len(answer),
                    "tags": trace.tags,
                },
                ensure_ascii=False,
            )
        )


def _trace_ids(args: argparse.Namespace) -> list[str]:
    """解析本次要处理的 trace ID 列表。"""
    if args.trace_id:
        return [args.trace_id]
    traces = fetch_recent(
        args.langfuse_host, since=parse_since(args.since), limit=args.limit
    )
    return [trace.id for trace in traces]


def _run_one(
    trace_id: str, args: argparse.Namespace, names: list[str], *, single: bool
) -> dict:
    """处理单条 trace，返回批量汇总条目。"""
    trace = fetch_trace(trace_id, args.langfuse_host)
    parsed = parse_trace(trace)
    parsed["reference"] = args.reference if single else ""
    metrics = None if args.inspect_only else run_metrics(parsed, names)
    analysis = analyze_trace(parsed, metrics)
    path = _write_report(parsed, args.output_dir, metrics, analysis)
    print(json.dumps(_summary(parsed, metrics, analysis), ensure_ascii=False, indent=2))
    print(f"\nreport: {path}")
    return {
        "trace_id": trace_id,
        "query": parsed["query"],
        "report": str(path),
        "metrics": metrics,
        "analysis": analysis,
    }


def main(argv: list[str] | None = None) -> None:
    """CLI 入口。"""
    args = parse_args(argv)
    if args.list:
        _list_recent(args)
        return

    ids = _trace_ids(args)
    if not ids:
        print("no chat traces found")
        return
    names = [name.strip() for name in args.metrics.split(",") if name.strip()]
    if args.reference and len(ids) == 1:
        names.extend(name for name in _REFERENCE_METRICS if name not in names)

    results: list[dict] = []
    for trace_id in ids:
        results.append(_run_one(trace_id, args, names, single=len(ids) == 1))
    if len(results) <= 1:
        return

    summary = summarize_batch(results)
    summary["generated_at"] = datetime.now(UTC).isoformat()
    summary_path = Path(args.output_dir) / (
        f"batch_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nsummary: {summary_path}")


if __name__ == "__main__":
    main()
