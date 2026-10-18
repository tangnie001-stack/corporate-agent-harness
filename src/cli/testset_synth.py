"""v6 出题：DeepEval Synthesizer 编排。"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.progress import Progress

# 先 import settings，确保 DEEPEVAL_* 环境变量已设置
from src.config import settings
from src.config.const import EVAL_SYNTH_EVOLUTION_WEIGHTS

# isort: split
from deepeval.synthesizer import Synthesizer
from deepeval.synthesizer.config import EvolutionConfig, FiltrationConfig, StylingConfig
from deepeval.synthesizer.types import Evolution

from src.cli.synth_contexts import ContextGroup, render_context
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
