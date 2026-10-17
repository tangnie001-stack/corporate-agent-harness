"""replay_trace — 按 trace 离线重放检索（本期 L1）。

用法（容器内）：docker compose exec app python -m src.cli.replay_trace --trace trace_xxx

读取全部 app_*.log（按天轮转，trace 可跨天），段位无关解析：按 trace_id 子串
过滤行、取首个 " - " 之后为 message（兼容 _LOG_FORMAT 加 session 段前后）。
输出语义：对当前 KB、当前配置重放（非历史快照）；事件行的 top_k/hybrid/rerank
为"当时值"，与本次实际执行参数并排对照并标注差异（drift 检测）。
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re

from src.config import TOP_K_RERANK, settings
from src.infra.db.vector_store import VectorStore
from src.models import get_rerank
from src.rag.context import RAGContext
from src.rag.retrieval import rerank_results, search

# k=v 切分：token 裸写 或 双引号 JSON 串（值与 helper encode_value 配套，round-trip）
_KV = re.compile(r"([A-Za-z0-9_]+)=(\"[^\"]*\"|[^ ]+)")


def parse_log_line(line: str) -> dict | None:
    """从一行日志解析 retrieve replay 字段；非 replay 行返回 None。

    段位无关：不依赖 | 分段，直接在整行找 trace 子串与 message；
    message 取首个 " - " 之后的内容，再按事件名定位 replay 行。
    """
    if " - [retrieval] retrieve replay " not in line:
        return None
    message = line.split(" - ", 1)[-1]  # 取 message（首个 " - " 后即为 message 头）
    if not message.startswith("[retrieval] retrieve replay "):
        return None
    body = message[len("[retrieval] retrieve replay ") :].rstrip()
    fields: dict = {}
    for key, raw in _KV.findall(body):
        if raw.startswith('"'):
            fields[key] = json.loads(raw)
        elif raw == "true":
            fields[key] = True
        elif raw == "false":
            fields[key] = False
        else:
            try:
                fields[key] = int(raw)
            except ValueError:
                fields[key] = raw
    return fields


def parse_trace_logs(log_dir: str, trace_id: str) -> list[dict]:
    """扫 log_dir 下全部 app_*.log，返回该 trace 的 replay 行字段列表（按文件行序）。"""
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(log_dir, "app_*.log"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if trace_id not in line:
                    continue
                fields = parse_log_line(line)
                if fields:
                    rows.append(fields)
    return rows


def _print_snippets(fields: dict, contexts: list[RAGContext]) -> None:
    """打印一次重放的命中片段与 drift 对照。

    drift：事件行记录的"当时参数"（top_k/hybrid/rerank）
    与本次实际执行（当前 settings 值）不同时标注差异——检索栈内部读模块
    常量，无法用事件参数覆盖（Q4/Q3），因此 replay 只做对照诊断，不做参数实验。
    """
    print(f"  [iteration={fields.get('iteration')}] query={fields['query']!r}")
    # 事件记录"当时值" vs 本次实际执行（检索栈内部读 settings 常量，无法在此覆盖）
    row_top_k = fields.get("top_k")
    row_hybrid = fields.get("hybrid")
    row_rerank = fields.get("rerank")
    print(f"  params: top_k={row_top_k} hybrid={row_hybrid} rerank={row_rerank}")
    current_top_k = TOP_K_RERANK
    current_hybrid = settings.HYBRID_SEARCH_ENABLED
    current_rerank = True
    for key, row_value, current_value in (
        ("top_k", row_top_k, current_top_k),
        ("hybrid", row_hybrid, current_hybrid),
        ("rerank", row_rerank, current_rerank),
    ):
        if row_value is not None and row_value != current_value:
            print(f"  drift: row {key}={row_value} → 本次 {key}={current_value}")
    for ctx in contexts[: int(fields.get("top_k", 8))]:
        snippet = (ctx.content or "").replace("\n", " ")[:80]
        print(
            f"    source={ctx.source} page={ctx.page} score={ctx.score:.3f} | {snippet}"
        )


async def _replay_all(rows: list[dict], vector_store, reranker) -> None:
    """对当前 KB/配置逐行重放检索（Q4：search/rerank 内部读模块常量）。"""
    for fields in rows:
        results = await search(fields["query"], fields["kb_id"], vector_store)
        contexts = rerank_results(fields["query"], results, reranker)
        _print_snippets(fields, contexts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="按 trace 重放检索（当前配置，drift 对照）"
    )
    parser.add_argument("--trace", required=True, help="trace_id，如 trace_xxx")
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    args = parser.parse_args(argv)

    rows = parse_trace_logs(args.log_dir, args.trace)
    if not rows:
        print(f"trace {args.trace} 无检索重放事件（纯对话/纯联网或不存在的 trace）")
        return
    print(f"trace {args.trace}: {len(rows)} 次检索重放（对当前 KB/配置，非历史快照）")

    # store/reranker 构造沿用 cli 既有先例（eval_deepeval.py 构造段）
    vector_store = VectorStore()
    reranker = get_rerank()
    asyncio.run(_replay_all(rows, vector_store, reranker))


if __name__ == "__main__":
    main()
