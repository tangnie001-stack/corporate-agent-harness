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
from pathlib import Path
from typing import Any

from langfuse import Langfuse

from src.config import settings

_DEFAULT_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "contextual_relevancy",
)
_REFERENCE_METRICS = ("context_precision", "context_recall")
_CITATION_RE = re.compile(r"\[(\d+)\]\s*来源:\s*(.+?)\s*\(第(\d+)页")
_BLOCK_SPLIT_RE = re.compile(r"\n(?=\[\d+\]\s*来源:)")


def _as_text(value: Any) -> str:
    """把 trace 字段归一成文本；None 返回空串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "output", "message"):
            if isinstance(value.get(key), str):
                return value[key]
    return json.dumps(value, ensure_ascii=False)


def _latency_ms(value: Any) -> int | None:
    """Langfuse 的 latency 单位是秒；统一转成毫秒。"""
    if value is None:
        return None
    return int(float(value) * 1000)


def _fetch_trace(trace_id: str, host: str) -> Any:
    """按 trace id 拉取完整 trace（含 observations）。

    Args:
        trace_id: Langfuse trace ID
        host: Langfuse 地址（本机调试通常传 http://localhost:3000）

    Returns:
        `TraceWithFullDetails`
    """
    client = Langfuse(
        host=host,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
    )
    return client.fetch_trace(trace_id).data


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
    answer = _as_text(trace.output)

    contexts: list[dict] = []
    tool_calls: list[dict] = []
    generations: list[dict] = []
    for obs in trace.observations or []:
        name = obs.name or ""
        if name == "retrieve_kb":
            content = _as_text(obs.output)
            contexts.extend(_split_contexts(content))
            tool_calls.append(
                {
                    "name": name,
                    "args": obs.input,
                    "result_chars": len(content),
                    "latency_ms": _latency_ms(obs.latency),
                }
            )
            continue
        if obs.type == "SPAN" and name in {"search_web", "delegate_task"}:
            tool_calls.append(
                {
                    "name": name,
                    "args": obs.input,
                    "result_chars": len(_as_text(obs.output)),
                    "latency_ms": _latency_ms(obs.latency),
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
                    "latency_ms": _latency_ms(obs.latency),
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


def _metric_instances(judge: Any, names: list[str]) -> dict:
    """按名称构造 DeepEval 指标。

    Args:
        judge: `BridgeDeepEvalLLM`
        names: 指标名列表

    Returns:
        指标名 -> 度量实例
    """
    # 确保 DEEPEVAL_* 环境兜底先于 deepeval import（与 eval_scoring 同序）
    # isort: off
    import src.config.settings  # noqa: F401
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        ContextualPrecisionMetric,
        ContextualRecallMetric,
        ContextualRelevancyMetric,
        FaithfulnessMetric,
    )
    # isort: on

    builders = {
        "faithfulness": lambda: FaithfulnessMetric(model=judge),
        "answer_relevancy": lambda: AnswerRelevancyMetric(model=judge),
        "contextual_relevancy": lambda: ContextualRelevancyMetric(model=judge),
        "context_precision": lambda: ContextualPrecisionMetric(model=judge),
        "context_recall": lambda: ContextualRecallMetric(model=judge),
    }
    return {name: builders[name]() for name in names if name in builders}


def _run_metrics(parsed: dict, names: list[str]) -> dict:
    """跑选中指标；单指标失败只记 error，不中断其余指标。

    Args:
        parsed: `parse_trace` 产物
        names: 指标名列表

    Returns:
        指标名 -> {score, reason, error}
    """
    from deepeval.test_case import LLMTestCase

    from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM
    from src.infra.llm.provider.factory import build_bridge_judge
    from src.infra.llm.provider.http_observability import HookEvidence

    judge = BridgeDeepEvalLLM(build_bridge_judge(evidence=HookEvidence()))
    contexts = [item["text"] for item in parsed["contexts"]]
    test_case = LLMTestCase(
        input=parsed["query"],
        actual_output=parsed["answer"],
        retrieval_context=contexts,
        expected_output=parsed.get("reference") or None,
    )
    out: dict[str, dict] = {}
    for name, metric in _metric_instances(judge, names).items():
        try:
            score = metric.measure(test_case)
            reason = getattr(metric, "reason", None)
            out[name] = {"score": score, "reason": reason, "error": None}
        except Exception as exc:  # noqa: BLE001  # 单指标失败不阻断其余指标
            out[name] = {
                "score": None,
                "reason": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return out


def _write_report(parsed: dict, output_dir: str, metrics: dict | None) -> Path:
    """把解析结果（与可选指标）写成 JSON。

    Args:
        parsed: `parse_trace` 产物
        output_dir: 输出目录
        metrics: 指标结果；None 表示 inspect-only

    Returns:
        实际写入的文件路径
    """
    target = Path(output_dir) / parsed["trace_id"]
    target.mkdir(parents=True, exist_ok=True)
    payload = dict(parsed)
    if metrics is not None:
        payload["metrics"] = metrics
    path = target / "trace.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _summary(parsed: dict, metrics: dict | None) -> dict:
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
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="单条 Langfuse trace 回放与评分")
    parser.add_argument("--trace-id", required=True, help="Langfuse trace ID")
    parser.add_argument(
        "--langfuse-host",
        default=settings.LANGFUSE_HOST,
        help="Langfuse 地址；本机调试用 http://localhost:3000",
    )
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="只解析字段，不调用裁判",
    )
    parser.add_argument(
        "--metrics",
        default=",".join(_DEFAULT_METRICS),
        help="逗号分隔的指标名（默认 reference-free 三项）",
    )
    parser.add_argument(
        "--reference", default="", help="可选参考答案；提供后追加 CP/CR"
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(settings.EVAL_REPORT_DIR).parent / "traces"),
        help="回放 JSON 输出目录",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI 入口。"""
    args = parse_args(argv)
    trace = _fetch_trace(args.trace_id, args.langfuse_host)
    parsed = parse_trace(trace)
    parsed["reference"] = args.reference

    names = [name.strip() for name in args.metrics.split(",") if name.strip()]
    if args.reference:
        names.extend(name for name in _REFERENCE_METRICS if name not in names)
    metrics = None if args.inspect_only else _run_metrics(parsed, names)
    path = _write_report(parsed, args.output_dir, metrics)
    print(json.dumps(_summary(parsed, metrics), ensure_ascii=False, indent=2))
    print(f"\nreport: {path}")


if __name__ == "__main__":
    main()
