"""v6 出题：从 KB 分块构造上下文（分组 + 公司/报告期锚点）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.infra.db.vector_store.types import ChunkResult

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_COMPANY_RE = re.compile(r"[\u4e00-\u9fff]{2,12}(?:股份有限公司|有限责任公司|有限公司)")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PERIOD_RE = re.compile(r"(annual|q[1-4]|h[12])", re.IGNORECASE)
_PERIOD_SUFFIX = {
    "annual": "年年度",
    "q1": "年第一季度",
    "q2": "年第二季度",
    "q3": "年第三季度",
    "q4": "年第四季度",
    "h1": "年上半年",
    "h2": "年下半年",
}
_NARRATIVE_PREFIXES = (
    "控股股东",
    "本公司",
    "子公司",
    "母公司",
    "股东",
    "公司",
    "持有",
    "的",
)
_COMPANY_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "控股有限公司",
    "有限公司",
    "集团",
)


@dataclass(frozen=True)
class ContextGroup:
    """一个出题上下文（2 个连续分块）。"""

    chunks: list[ChunkResult]
    """按 chunk_index 升序的分块。"""
    source: str
    """文档文件名，用于 --docs 过滤与 source_file。"""
    doc_id: str
    """文档 UUID，分组键。"""
    company: str
    """中文公司全称，写入 header。"""
    period: str
    """报告期，写入 header。"""
    aliases: set[str] = field(default_factory=set)
    """公司别名（含 ASCII 形），供语言门与自足门。"""
    company_source: str = "metadata"
    """公司名来源：metadata / parent_content / content。"""


def _has_cjk(text: str) -> bool:
    """文本是否含中文。"""
    return bool(_CJK_RE.search(text))


def _strip_narrative_prefixes(name: str) -> str:
    """反复剥掉「本公司/控股股东/公司持有」等叙述前缀。

    Args:
        name: 正则命中的原始片段

    Returns:
        剥净后的公司名
    """
    changed = True
    while changed:
        changed = False
        for prefix in _NARRATIVE_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix) :]
                changed = True
                break
    return name


def derive_company(chunks_of_doc: list[ChunkResult]) -> tuple[str, str]:
    """按文档推导一次中文公司名。

    Args:
        chunks_of_doc: 同一 doc_id 的全部分块

    Returns:
        (中文全称, 来源标记)

    Raises:
        ValueError: 全部分块都推导不出中文公司名
    """
    raw = str(chunks_of_doc[0].metadata.get("company") or "").strip()
    if len(raw) >= 4 and _has_cjk(raw):
        return raw, "metadata"

    ordered = sorted(chunks_of_doc, key=lambda c: int(c.metadata.get("chunk_index", 0)))
    for field_name in ("parent_content", "content"):
        for chunk in ordered:
            text = str(chunk.metadata.get(field_name) or chunk.content or "")
            for match in _COMPANY_RE.finditer(text):
                name = _strip_narrative_prefixes(match.group(0))
                if len(name) >= 4:
                    return name, field_name
    raise ValueError(
        f"无法从 doc_id={ordered[0].metadata.get('doc_id')} 推导中文公司名"
    )


def derive_period(chunks_of_doc: list[ChunkResult]) -> str:
    """按文档推导报告期（必须含年份）。

    Args:
        chunks_of_doc: 同一 doc_id 的全部分块

    Returns:
        报告期字符串，如 `2024年年度`

    Raises:
        ValueError: 既无 report_period 也无法从 source 推出年份
    """
    first = chunks_of_doc[0]
    raw = str(first.metadata.get("report_period") or "").strip()
    if raw:
        return raw

    source = str(first.metadata.get("source") or "")
    year_match = _YEAR_RE.search(source)
    period_match = _PERIOD_RE.search(source)
    if year_match is not None:
        year = year_match.group(0)
        if period_match is not None:
            return f"{year}{_PERIOD_SUFFIX[period_match.group(1).lower()]}"
        return f"{year}年"

    report_type = str(first.metadata.get("report_type") or "").strip()
    quarter = str(first.metadata.get("quarter") or "").strip()
    if report_type or quarter:
        return f"{report_type}{quarter}"
    raise ValueError(f"无法从 {source} 推导报告期")


def company_aliases(name: str, raw_company: str) -> set[str]:
    """构造公司别名集合（全称 + 逐级去后缀 + ASCII 形）。

    Args:
        name: 中文全称
        raw_company: metadata 里的原始 company 值

    Returns:
        至少包含 `name` 的别名集合
    """
    aliases: set[str] = {name}
    current = name
    changed = True
    while changed:
        changed = False
        for suffix in _COMPANY_SUFFIXES:
            if current.endswith(suffix) and len(current) > len(suffix):
                current = current[: -len(suffix)]
                if len(current) >= 2:
                    aliases.add(current)
                changed = True
                break
    raw = raw_company.strip()
    if raw and raw.lower() != name.lower():
        aliases.add(raw)
    return aliases


async def load_chunks(kb_id: str, docs: list[str] | None) -> list[ChunkResult]:
    """读取 KB 分块；docs 非空时按 source 过滤。

    Args:
        kb_id: 知识库 UUID
        docs: 允许的 source 列表；None/空表示全部

    Returns:
        按 (doc_id, chunk_index) 升序的分块
    """
    from src.infra.db.vector_store import VectorStore

    chunks = await VectorStore().get_all_chunks(kb_id)
    if docs:
        wanted = set(docs)
        chunks = [c for c in chunks if c.metadata.get("source") in wanted]
    return sorted(
        chunks,
        key=lambda c: (
            str(c.metadata.get("doc_id") or ""),
            int(c.metadata.get("chunk_index", 0)),
        ),
    )


def build_contexts(chunks: list[ChunkResult], size: int) -> list[ContextGroup]:
    """按 doc_id 分组，每 size 块一个上下文，丢弃末尾不足 size 的块。

    Args:
        chunks: 已排序分块
        size: 每组块数

    Returns:
        上下文列表
    """
    by_doc: dict[str, list[ChunkResult]] = {}
    for chunk in chunks:
        doc_id = str(chunk.metadata.get("doc_id") or "")
        by_doc.setdefault(doc_id, []).append(chunk)

    groups: list[ContextGroup] = []
    for doc_id, items in by_doc.items():
        items.sort(key=lambda c: int(c.metadata.get("chunk_index", 0)))
        if len(items) < size:
            continue
        company, company_source = derive_company(items)
        period = derive_period(items)
        raw_company = str(items[0].metadata.get("company") or "")
        aliases = company_aliases(company, raw_company)
        source = str(items[0].metadata.get("source") or "")
        for start in range(0, len(items) - size + 1, size):
            groups.append(
                ContextGroup(
                    chunks=items[start : start + size],
                    source=source,
                    doc_id=doc_id,
                    company=company,
                    period=period,
                    aliases=aliases,
                    company_source=company_source,
                )
            )
    return groups


def render_context(group: ContextGroup) -> list[str]:
    """把组内分块渲染成带锚点头的字符串列表。

    Args:
        group: 出题上下文

    Returns:
        首块带 `【公司 · 报告期】` 头，其余原样
    """
    texts = [chunk.content for chunk in group.chunks]
    if texts:
        texts[0] = f"【{group.company} · {group.period}】\n{texts[0]}"
    return texts
