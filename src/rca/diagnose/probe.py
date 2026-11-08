"""检索探针：换表述再查一次，区分"表述问题"与"覆盖缺失"。

原则：变体由代码确定性生成（去年份 / 去口语与问句尾），不用模型改写——模型改写不可复现，
同一输入两次跑会得到不同结论。探针只出事实，不下根因。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.config import settings

_LEAD = ("请问", "帮我查一下", "帮我查", "麻烦", "我想知道", "想问一下", "想问")
_TAIL = ("是多少", "是什么", "怎么样", "如何", "呢", "吗", "？", "?")
_YEAR = re.compile(r"(?:19|20)\d{2}\s*年?")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class ProbeVariant:
    """一次"换表述"的检索结果。"""

    query: str  # 生成出来的变体问法
    result_count: int  # 该变体召回条数
    top_score: float  # 该变体精排 top1 分数；无结果时为 0


@dataclass(frozen=True)
class ProbeFacts:
    """探针结论：变体全量结果 + 粗判。"""

    variants: tuple[ProbeVariant, ...]

    @property
    def verdict(self) -> str:
        """rephrased（换说法就命中）/ missing（换说法也不行）/ unknown（没生成变体）。"""
        if not self.variants:
            return "unknown"
        if any(v.top_score >= settings.DIAG_SCORE_FLOOR for v in self.variants):
            return "rephrased"
        return "missing"


def build_variants(query: str) -> tuple[str, ...]:
    """生成确定性变体（去重、剔除与原问题相同者），最多 3 条。

    Args:
        query: 原始检索查询

    Returns:
        变体问法元组；无法生成时为空元组
    """
    text = _WS.sub(" ", query).strip()
    if not text:
        return ()
    no_year = _WS.sub(" ", _YEAR.sub("", text)).strip()
    trimmed = _strip_affixes(text)
    both = _strip_affixes(no_year)
    out: list[str] = []
    for candidate in (no_year, trimmed, both):
        if candidate and candidate != text and candidate not in out:
            out.append(candidate)
    return tuple(out[:3])


def _strip_affixes(text: str) -> str:
    """去掉开头的口语前缀与结尾的问句词。"""
    result = text
    for prefix in _LEAD:
        if result.startswith(prefix):
            result = result[len(prefix) :]
            break
    for suffix in _TAIL:
        if result.endswith(suffix):
            result = result[: -len(suffix)]
            break
    return result.strip()


#: 打分函数契约：(query, kb_id) -> (召回条数, 精排分数)
ScoreFn = Callable[[str, str], Awaitable[tuple[int, tuple[float, ...]]]]


class RetrievalProbe:
    """对原问题做确定性变体后重检。"""

    def __init__(self, score_fn: ScoreFn | None = None) -> None:
        """Args:
        score_fn: 打分函数（生产由 app 装配注入 vector_store + reranker）；默认自建。
        """
        self._score = score_fn or default_score_fn

    async def run(self, query: str, kb_id: str) -> ProbeFacts:
        """对变体逐个重检，返回探针事实。

        Args:
            query: 原问题的实际检索查询
            kb_id: 目标知识库（为空时直接返回空事实）

        Returns:
            ProbeFacts；无变体或 kb 为空时 variants 为空
        """
        if not kb_id:
            return ProbeFacts(())
        variants = build_variants(query)
        if not variants:
            return ProbeFacts(())
        out: list[ProbeVariant] = []
        for variant in variants:
            count, scores = await self._score(variant, kb_id)
            top = max(scores) if scores else 0.0
            out.append(ProbeVariant(query=variant, result_count=count, top_score=top))
        return ProbeFacts(tuple(out))


def make_score_fn(vector_store, reranker) -> ScoreFn:
    """用共享的向量库与精排模型构造打分函数（装配期注入，不每次新建连接）。"""

    async def score(query: str, kb_id: str) -> tuple[int, tuple[float, ...]]:
        from src.rag.retrieval import rerank_results, search

        results = await search(query, kb_id, vector_store)
        if not results:
            return 0, ()
        contexts = rerank_results(query, results, reranker)
        return len(results), tuple(float(c.score) for c in contexts)

    return score


async def default_score_fn(query: str, kb_id: str) -> tuple[int, tuple[float, ...]]:
    """默认打分函数：进程内自建向量库与精排模型（CLI / 未装配时使用）。"""
    from src.infra.db.vector_store import VectorStore
    from src.models import get_rerank

    return await make_score_fn(VectorStore(), get_rerank())(query, kb_id)
