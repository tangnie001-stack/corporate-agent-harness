"""出题后自足性门（design D8 的 P2）。

背景：RAGAS 出题链路对问题自足性零校验、无改写步；官方 `llm_context` 只是
prompt 的「题型建议」软槽位。P1（源头锚定）把【公司名 · 报告期】前置进出题
上下文后，实测题面自足率 100%，但这仍是**概率**行为。本模块给出**确定性**
的兜底门：出题后逐条校验题面是否点名其上下文里的公司，不自足者保义改写或
丢弃，使「题面自足」成为**受检保证**而非期望。

解耦：本模块纯函数、不 import ragas；KG→别名的适配在调用方完成。
"""

from __future__ import annotations

import re

# P1 前置的锚点头：【公司名 · 报告期】（多跳上下文里可能位于 `<n-hop>` 之后）
_HEADER_RE = re.compile(r"【([^】]+)】")

# 纯数字/标点串不构成人名或公司名，不作为别名（如 "2024"、"---"）
_NOISE_RE = re.compile(r"[\d\W_]+")


def header_entities(sample: dict) -> list[tuple[str, str]]:
    """从样本 `reference_contexts` 的【公司 · 报告期】头解析出实体对。

    Args:
        sample: 测试集样本 dict（含 `reference_contexts`）

    Returns:
        `(company, period)` 去重列表，按出现顺序；`period` 可为空串。
    """
    out: list[tuple[str, str]] = []
    for ctx in sample.get("reference_contexts") or []:
        for raw in _HEADER_RE.findall(ctx or ""):
            parts = [p.strip() for p in raw.split("·")]
            company = parts[0] if parts else ""
            period = parts[1] if len(parts) > 1 else ""
            if company and (company, period) not in out:
                out.append((company, period))
    return out


def _company_of(sample: dict) -> list[str]:
    """样本上下文涉及的公司（去重、保序）。"""
    companies: list[str] = []
    for company, _ in header_entities(sample):
        if company.lower() not in {c.lower() for c in companies}:
            companies.append(company)
    return companies


def doc_exclusive_aliases(docs: list[tuple[str, list[str]]]) -> dict[str, set[str]]:
    """构造「公司 → 别名集合」，用于跨语言/简写识别公司（如 tencent ↔ 腾讯）。

    别名来源是各文档的分块实体/主题词；仅保留**只出现在单一文档**、长度 ≥2、
    非纯数字标点的词——如此 `人民币`/`2024` 这类跨文档通用词会被自动排除，
    而 `腾讯`/`Tencent`/`tencent`、`东软集团`/`Neusoft Group` 会被保留。

    Args:
        docs: `(company, tokens)` 列表，每份文档一项；tokens 为该文档实体的词表

    Returns:
        `{company_norm: {alias_norm, ...}}`；每个集合至少含 company 自身。
    """
    normalized: list[tuple[str, set[str]]] = []
    counts: dict[str, int] = {}
    for company, tokens in docs:
        toks = {(t or "").strip().lower() for t in tokens}
        toks.discard("")
        normalized.append((company.strip().lower(), toks))
        for t in toks:
            counts[t] = counts.get(t, 0) + 1

    result: dict[str, set[str]] = {}
    for company, toks in normalized:
        aliases = {company} if company else set()
        for t in toks:
            if counts[t] == 1 and len(t) >= 2 and not _NOISE_RE.fullmatch(t):
                aliases.add(t)
        if aliases:
            result[company] = aliases
    return result


def _mentions(question: str, company: str, aliases: dict[str, set[str]]) -> bool:
    """题面是否点到了 company（按别名集合做包含判断）。"""
    q = (question or "").lower()
    keys = aliases.get(company.strip().lower()) or {company.strip().lower()}
    return any(k and k in q for k in keys)


def _prefix_subject(question: str, company: str, period: str) -> str:
    """保义改写：把「公司+报告期」作为主语前缀到题面（不改动其余文字）。"""
    head = f"{company}{period}".strip()
    return f"{head}，{question}" if head else question


def enforce_self_contained(
    samples: list[dict], aliases_by_company: dict[str, set[str]]
) -> tuple[list[dict], list[tuple[dict, str]]]:
    """自足性门：逐条判定、必要时保义改写，不可救者丢弃。

    规则（design D8 / 用户定：多公司无唯一实体 → 丢弃）：
      1. 题面已点名其上下文中任一公司 → 保留原样；
      2. 未点名但上下文**只涉及一家公司** → 用「公司+报告期」前缀做保义改写；改后仍不达标则丢弃；
      3. 未点名且上下文涉及**多家公司**（无唯一实体）或**无锚点** → 丢弃。

    Args:
        samples: 测试集样本 dict 列表（就地不改，返回新列表）
        aliases_by_company: `doc_exclusive_aliases` 的输出

    Returns:
        `(kept, dropped)`；dropped 为 `(sample, reason)` 列表。
    """
    kept: list[dict] = []
    dropped: list[tuple[dict, str]] = []
    for sample in samples:
        question = sample.get("user_input", "") or ""
        companies = _company_of(sample)

        if not companies:
            dropped.append((sample, "无锚点（非 P1 产物）"))
            continue

        if any(_mentions(question, c, aliases_by_company) for c in companies):
            kept.append(sample)
            continue

        if len(companies) == 1:
            company = companies[0]
            period = next((p for c, p in header_entities(sample) if c == company), "")
            rewritten = _prefix_subject(question, company, period)
            if _mentions(rewritten, company, aliases_by_company):
                new_sample = dict(sample)
                new_sample["user_input"] = rewritten
                kept.append(new_sample)
                continue

        reason = "多公司无唯一实体" if len(companies) > 1 else "保义改写后仍不自足"
        dropped.append((sample, reason))
    return kept, dropped
