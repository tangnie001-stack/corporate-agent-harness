"""v6 出题：DeepEval Synthesizer 编排。"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

from rich.progress import Progress

# 先 import settings，确保 DEEPEVAL_* 环境变量已设置
from src.config import settings
from src.config.const import EVAL_SYNTH_EVOLUTION_WEIGHTS, EVAL_SYNTH_MIN_SAMPLES

# isort: split
from deepeval.synthesizer import Synthesizer
from deepeval.synthesizer.config import EvolutionConfig, FiltrationConfig, StylingConfig
from deepeval.synthesizer.types import Evolution

from src.cli.synth_contexts import (
    ContextGroup,
    build_contexts,
    load_chunks,
    render_context,
)
from src.cli.synth_gates import run_gates
from src.cli.synth_writer import (
    build_metadata,
    golden_to_sample,
    public_sample,
    write_testset,
)
from src.cli.testset_io import next_testset_version
from src.cli.testset_synth_args import parse_args
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.provider.deepeval_synth_llm import SynthDeepEvalLLM


def _evolution_weights() -> dict[Evolution, float]:
    """把字符串键的权重转成 Evolution 枚举键。

    Returns:
        `Evolution -> 权重` 字典

    Raises:
        ValueError: 权重键无法映射或和不为 1.0
    """
    by_value = {item.value: item for item in Evolution}
    result: dict[Evolution, float] = {}
    for key, weight in EVAL_SYNTH_EVOLUTION_WEIGHTS.items():
        if key not in by_value:
            raise ValueError(f"未知 evolution: {key}")
        result[by_value[key]] = weight
    total = sum(result.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"evolution 权重和必须为 1.0，当前 {total}")
    return result


def _styling_config() -> StylingConfig:
    """中文出题的硬约束。"""
    return StylingConfig(
        scenario="财务分析师阅读 A 股或港股上市公司的定期报告",
        task="基于给定的中文年报或季报上下文，提出可以用该上下文回答的问题",
        input_format=(
            "简体中文问句，一句话，必须包含公司名称与报告期，不得出现英文句子或中英拼接"
        ),
        expected_output_format=(
            "简体中文完整陈述句，包含主体、指标与数值单位（如适用），"
            "不得为裸数字，并以句号结尾"
        ),
    )


def _build_synthesizer(judge: Any) -> Synthesizer:
    """构造 Synthesizer。

    Args:
        judge: provider 层裁判（ChatOpenAI）

    Returns:
        配置好的 Synthesizer
    """
    critic = SynthDeepEvalLLM(judge)
    return Synthesizer(
        model=SynthDeepEvalLLM(judge),
        async_mode=True,
        max_concurrent=settings.EVAL_SYNTH_MAX_CONCURRENT,
        styling_config=_styling_config(),
        evolution_config=EvolutionConfig(
            num_evolutions=1, evolutions=_evolution_weights()
        ),
        filtration_config=FiltrationConfig(
            synthetic_input_quality_threshold=0.5,
            max_quality_retries=2,
            critic_model=critic,
        ),
    )


async def _synthesize(
    contexts: list[ContextGroup],
    synth: Synthesizer,
    *,
    goldens_per_context: int,
) -> tuple[list[tuple[Any, ContextGroup]], int, str]:
    """逐上下文出题，单点失败不拖垮整批。

    Args:
        contexts: 出题上下文
        synth: 已构造的 Synthesizer
        goldens_per_context: 每个 context 生成多少题

    Returns:
        `(pairs, failed_contexts, last_error)`；pairs 为 `(Golden, ContextGroup)`
    """
    semaphore = asyncio.Semaphore(settings.EVAL_SYNTH_MAX_CONCURRENT)
    progress = Progress(disable=True)

    async def _one(group: ContextGroup) -> list[tuple[Any, ContextGroup]]:
        """在并发闸门内为单个 context 出题，并把 Golden 绑回所属组。

        Args:
            group: 出题上下文

        Returns:
            `(Golden, ContextGroup)` 列表
        """
        async with semaphore:
            task_id = progress.add_task("synth", total=1)
            try:
                goldens = await synth.a_generate_goldens_from_contexts(
                    [render_context(group)],
                    include_expected_output=True,
                    max_goldens_per_context=goldens_per_context,
                    _reset_cost=False,
                    _progress=progress,
                    _pbar_id=task_id,
                )
            finally:
                progress.remove_task(task_id)
        return [(golden, group) for golden in goldens]

    results = await asyncio.gather(
        *(_one(group) for group in contexts), return_exceptions=True
    )
    pairs: list[tuple[Any, ContextGroup]] = []
    failed = 0
    last_error = ""
    for result in results:
        if isinstance(result, BaseException):
            failed += 1
            last_error = f"{type(result).__name__}: {result}"
            continue
        pairs.extend(result)
    return pairs, failed, last_error


async def _prepare_scope(
    args: argparse.Namespace,
) -> tuple[int, list[ChunkResult], list[ContextGroup], int] | None:
    """解析版本、加载并校验语料、构造出题上下文。

    Args:
        args: 已解析的命令行参数

    Returns:
        `(version, chunks, contexts, covered_chunks)`；`--dry-run` 已打印计划并
        提前结束时返回 None

    Raises:
        SystemExit: `--docs` 中有文件名未匹配到任何分块
    """
    if args.version is not None:
        version = args.version
    else:
        version = next_testset_version(args.kb_id)
    chunks = await load_chunks(args.kb_id, args.docs)
    if args.docs:
        found = {str(c.metadata.get("source")) for c in chunks}
        missing = [name for name in args.docs if name not in found]
        if missing:
            raise SystemExit(f"error: --docs 未匹配到分块: {', '.join(missing)}")

    contexts = build_contexts(chunks, settings.EVAL_SYNTH_CHUNKS_PER_CONTEXT)
    covered = sum(len(g.chunks) for g in contexts)
    if args.dry_run:
        target = (
            Path(settings.EVAL_TESTSET_DIR) / f"testset_{args.kb_id}_v{version}.json"
        )
        print(f"version={version}")
        print(f"docs={sorted({g.source for g in contexts})}")
        print(f"contexts={len(contexts)} chunks={len(chunks)} covered={covered}")
        print(
            f"calls=每 context 5-8 次，单轮约 {len(contexts) * 5}-{len(contexts) * 8}"
        )
        print(f"target={target} exists={target.exists()}")
        if target.exists():
            print("目标已存在：正式运行需 --force")
        return None
    return version, chunks, contexts, covered


async def _generate_once(
    contexts: list[ContextGroup],
    synth: Synthesizer,
    goldens_per_context: int,
) -> tuple[list[dict], dict, int, str]:
    """跑一轮出题并把 Golden 过质量门。

    Args:
        contexts: 出题上下文
        synth: 已构造的 Synthesizer
        goldens_per_context: 每个 context 生成多少题

    Returns:
        `(kept, stats, failed_contexts, last_error)`
    """
    pairs, failed, last_error = await _synthesize(
        contexts, synth, goldens_per_context=goldens_per_context
    )
    samples = [golden_to_sample(golden, group) for golden, group in pairs]
    aliases = {g.company.strip().lower(): set(g.aliases) for g in contexts}
    kept, stats = run_gates(samples, aliases)
    stats["failed_contexts"] = failed
    return kept, stats, failed, last_error


def _write_result(
    args: argparse.Namespace,
    *,
    version: int,
    kept: list[dict],
    stats: dict,
    contexts: list[ContextGroup],
    source_chunks: int,
    covered_chunks: int,
    used_goldens: int,
    elapsed_seconds: float,
) -> None:
    """构造元数据、原子写盘并打收尾日志。

    Args:
        args: 已解析的命令行参数
        version: 实际写入的版本号
        kept: 通过质量门的样本
        stats: 质量门统计
        contexts: 本轮使用的上下文
        source_chunks: 读入的分块总数
        covered_chunks: 进入上下文的块数
        used_goldens: 最终生效的每上下文出题数
        elapsed_seconds: 本轮总耗时（秒）

    Raises:
        FileExistsError: 目标版本已存在且未传 --force
    """
    target = Path(settings.EVAL_TESTSET_DIR) / f"testset_{args.kb_id}_v{version}.json"
    hop_stats = {
        "single_hop": sum(1 for s in kept if s["hop_type"] == "single_hop"),
        "multi_hop": sum(1 for s in kept if s["hop_type"] == "multi_hop"),
    }
    metadata = build_metadata(
        kb_id=args.kb_id,
        kb_name=args.kb_name,
        version=version,
        samples=kept,
        stats=stats,
        params={
            "chunks_per_context": settings.EVAL_SYNTH_CHUNKS_PER_CONTEXT,
            "goldens_per_context": used_goldens,
            "max_concurrent": settings.EVAL_SYNTH_MAX_CONCURRENT,
            "evolution_weights": dict(EVAL_SYNTH_EVOLUTION_WEIGHTS),
        },
        doc_names=sorted({g.source for g in contexts}),
        doc_ids=sorted({g.doc_id for g in contexts}),
        source_chunks=source_chunks,
        covered_chunks=covered_chunks,
        company_source={g.source: g.company_source for g in contexts},
        hop_stats=hop_stats,
        elapsed_seconds=elapsed_seconds,
    )
    payload = {"metadata": metadata, "samples": [public_sample(s) for s in kept]}
    write_testset(target, payload, force=args.force)
    core_logging.log_event(
        Event.SYNTH_GATE,
        generated=stats["generated"],
        kept=stats["kept"],
        dropped=stats["dropped"],
        failed_contexts=stats["failed_contexts"],
        language_checked=stats["language_checked"],
    )
    core_logging.log_event(Event.SYNTH_DONE, path=str(target), size=len(kept))


async def _run(args: argparse.Namespace) -> None:
    """执行出题（调用方负责 asyncio.run(run_and_dispose(...))）。

    Args:
        args: 已解析的命令行参数

    Raises:
        SystemExit: 语料不匹配、生成全失败、语言门失效或样本不足
        FileExistsError: 目标版本已存在且未传 --force
    """
    from src.infra.llm.provider.factory import build_bridge_judge
    from src.infra.llm.provider.http_observability import HookEvidence

    started = time.monotonic()
    scope = await _prepare_scope(args)
    if scope is None:
        return
    version, chunks, contexts, covered = scope

    judge = build_bridge_judge(evidence=HookEvidence())
    synth = _build_synthesizer(judge)

    core_logging.log_event(
        Event.SYNTH_START,
        kb_id=args.kb_id,
        docs=len({g.source for g in contexts}),
        contexts=len(contexts),
    )

    kept, stats, failed, last_error = await _generate_once(
        contexts, synth, settings.EVAL_SYNTH_GOLDENS_PER_CONTEXT
    )
    used_goldens = settings.EVAL_SYNTH_GOLDENS_PER_CONTEXT

    if stats["generated"] == 0:
        raise SystemExit(
            f"error: 所有上下文都失败（failed={failed}），最后错误：{last_error}"
        )
    if stats["language_checked"] == 0:
        raise SystemExit("error: 没有样本进入语言门（语言失效）")
    language_rate = stats["dropped"]["language"] / stats["language_checked"]
    if language_rate > 0.5:
        raise SystemExit(
            f"error: 语言门丢弃率 {language_rate:.0%} > 50%，出题器语言约束失效"
        )
    if len(kept) < EVAL_SYNTH_MIN_SAMPLES:
        kept, stats, failed, last_error = await _generate_once(contexts, synth, 2)
        used_goldens = 2
        if len(kept) < EVAL_SYNTH_MIN_SAMPLES:
            raise SystemExit(
                f"error: 仅 {len(kept)} 条通过质量门"
                f"（< {EVAL_SYNTH_MIN_SAMPLES}），未写文件"
            )

    if failed > 0:
        core_logging.log_event(
            Event.SYNTH_CONTEXT_FAILED,
            err=f"count={failed} last={last_error}",
        )

    _write_result(
        args,
        version=version,
        kept=kept,
        stats=stats,
        contexts=contexts,
        source_chunks=len(chunks),
        covered_chunks=covered,
        used_goldens=used_goldens,
        elapsed_seconds=time.monotonic() - started,
    )


def main() -> None:
    """出题入口。"""
    from src.infra.db.engine import run_and_dispose

    asyncio.run(run_and_dispose(_run(parse_args())))


if __name__ == "__main__":
    main()
