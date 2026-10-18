"""证据探针：token 归一化、gold chunk 定位与 testset conflict。

纯函数模块：不调用 LLM、不访问数据库，匹配一律为归一化后的字符串/数值匹配。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from loguru import logger

from src.core.eval_evidence import EVIDENCE_GOLD_CAP, parent_key
from src.infra.db.vector_store.types import ChunkResult

# 数值 + 可选单位。千分位只吞恰好 3 位，避免把“80,348,42%”并成一个数
_NUMBER_RE = re.compile(
    r"[-−－]?(?:\d{1,3}(?:[,，]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?:万元|亿元|百分点|%|元)?"
)
# 单位表；判断“数值后 4 字符内是否有单位”时按此枚举
_UNITS = ("万元", "亿元", "百分点", "%", "元")
# 仅去掉合法千分位逗号（其后恰好 3 位数字），不粘连相邻的独立数值
_THOUSANDS_RE = re.compile(r"(?<=\d)[,，](?=\d{3}(?!\d))")
# 参考答案可能带【公司 · 报告期】头，匹配前去掉
_HEADER_RE = re.compile(r"^【[^】]*】\s*")


def normalize_text(text: str) -> str:
    """归一化文本：全角转半角、去标题头/千分位、统一负号、压缩空白。

    Args:
        text: 原文（参考答案、分块内容或回答）

    Returns:
        小写、半角、无千分位逗号、空白压缩为单空格的文本
    """
    normalized = unicodedata.normalize("NFKC", text.strip())
    normalized = _HEADER_RE.sub("", normalized)
    normalized = _THOUSANDS_RE.sub("", normalized)
    normalized = normalized.replace("−", "-").replace("－", "-")
    return re.sub(r"\s+", " ", normalized).lower()


def _match_text(text: str) -> str:
    """返回匹配用文本：在 `normalize_text` 基础上再去掉全部空白。

    Args:
        text: 原文

    Returns:
        去空白后的归一化文本，使“80,348 万元”与 token“80348万元”可命中
    """
    return normalize_text(text).replace(" ", "")


def normalize_number_token(text: str) -> str | None:
    """抽取并归一化单个数值 token（数字 + 单位）。

    Args:
        text: 可能包含数值的文本

    Returns:
        全角转半角、去千分位逗号/空白、负号统一后的 `数字[单位]`；
        没有数值时返回 None
    """
    token = unicodedata.normalize("NFKC", text).strip()
    token = token.replace("−", "-").replace("－", "-")
    match = _NUMBER_RE.search(token)
    if match is None:
        return None
    raw = _THOUSANDS_RE.sub("", match.group(0))
    return re.sub(r"\s+", "", raw)


def extract_fact_tokens(reference: str) -> list[str]:
    """抽取参考答案中的数值 token，按首次出现去重。

    Args:
        reference: 参考答案原文

    Returns:
        归一化后的数值 token 列表（数字 + 单位，去重保序）
    """
    normalized = unicodedata.normalize("NFKC", reference)
    tokens: list[str] = []
    for match in _NUMBER_RE.finditer(normalized):
        token = normalize_number_token(match.group(0))
        if token is None:
            continue
        if token in tokens:
            continue
        tokens.append(token)
    return tokens


def answer_mentions_fact(answer: str, tokens: list[str]) -> bool | None:
    """判断回答是否提到 gold 的任一数值 token。

    Args:
        answer: 被测系统回答原文
        tokens: `extract_fact_tokens` 的产物

    Returns:
        True/False；tokens 为空时返回 None（无法判定）
    """
    if not tokens:
        return None
    text = _match_text(answer)
    for token in tokens:
        if token in text:
            return True
    return False


def _chunk_detail(chunk: ChunkResult) -> dict[str, Any]:
    """构造分块的证据归属信息。

    Args:
        chunk: 检索/语料分块

    Returns:
        含 `chunk_id` / `doc_id` / `parent_key` / `source` / `page` 的字典
    """
    metadata = chunk.metadata or {}
    doc_id = str(metadata.get("doc_id") or "")
    parent_content = str(metadata.get("parent_content") or "")
    return {
        "chunk_id": chunk.id,
        "doc_id": doc_id,
        "parent_key": parent_key(doc_id, chunk.id, parent_content),
        "source": metadata.get("source"),
        "page": metadata.get("page"),
    }


def _null_probe(tokens: list[str]) -> dict[str, Any]:
    """构造“无法判定”的探针结果。

    Args:
        tokens: 已抽取的数值 token

    Returns:
        `fact_in_corpus` / `fact_coverage` 均为 None 的空结果
    """
    return {
        "gold_chunk_ids": [],
        "gold_parent_keys": [],
        "gold_chunks": [],
        "fact_in_corpus": None,
        "fact_coverage": {"corpus": None},
        "fact_hit_tokens": {"corpus": []},
        "token_hits": {},
        "token_parent_keys": {},
        "missing_fact_tokens": [],
        "probe_tokens": tokens,
        "truncated": False,
    }


def probe_reference_chunks(
    reference: str,
    chunks: list[ChunkResult],
    *,
    cap: int = EVIDENCE_GOLD_CAP,
) -> dict[str, Any]:
    """在分块 content 与 parent_content 中定位参考数值。

    Args:
        reference: 参考答案原文
        chunks: 本轮知识库全量分块（`EvidenceRun.corpus_chunks`）
        cap: gold chunk 与单 token 命中 id 的上限

    Returns:
        探针结果：`gold_chunk_ids` / `gold_parent_keys` / `gold_chunks`、
        `fact_in_corpus`、`fact_coverage`、token 命中与缺失、`truncated`；
        无 token 或语料为空时 `fact_in_corpus` 为 None
    """
    tokens = extract_fact_tokens(reference)
    if not tokens:
        return _null_probe(tokens)
    if not chunks:
        logger.warning("[cli] evidence probe corpus unavailable")
        return _null_probe(tokens)

    details = {chunk.id: _chunk_detail(chunk) for chunk in chunks}
    token_hits: dict[str, list[str]] = {token: [] for token in tokens}
    matched_ids: list[str] = []
    truncated = False
    for chunk in chunks:
        metadata = chunk.metadata or {}
        text = _match_text(f"{chunk.content}\n{metadata.get('parent_content') or ''}")
        matched_chunk = False
        for token in tokens:
            if token not in text:
                continue
            matched_chunk = True
            if len(token_hits[token]) < cap:
                token_hits[token].append(chunk.id)
            else:
                truncated = True
        if not matched_chunk:
            continue
        if len(matched_ids) < cap:
            matched_ids.append(chunk.id)
        else:
            truncated = True

    hit_tokens = [token for token in tokens if token_hits[token]]
    coverage = len(hit_tokens) / len(tokens)
    return {
        "gold_chunk_ids": matched_ids,
        "gold_parent_keys": [details[cid]["parent_key"] for cid in matched_ids],
        "gold_chunks": [details[cid] for cid in matched_ids],
        "fact_in_corpus": bool(hit_tokens),
        "fact_coverage": {"corpus": coverage},
        "fact_hit_tokens": {"corpus": hit_tokens},
        "token_hits": token_hits,
        "token_parent_keys": {
            token: [details[cid]["parent_key"] for cid in ids]
            for token, ids in token_hits.items()
        },
        "missing_fact_tokens": [token for token in tokens if not token_hits[token]],
        "probe_tokens": tokens,
        "truncated": truncated,
    }


def resolve_gold(
    sample: dict[str, Any], chunks: list[ChunkResult], reference: str
) -> dict[str, Any]:
    """解析 gold chunk：优先题集 `source_chunk_ids`，失效时降级探针。

    Args:
        sample: 测试集样本（读取 `source_chunk_ids` / `match_scope`）
        chunks: 本轮知识库全量分块
        reference: 参考答案原文（降级探针时使用）

    Returns:
        `gold_chunk_ids` / `gold_parent_keys` / `gold_chunk_source` /
        `gold_chunk_confidence`；降级时一并返回探针字段
    """
    source_ids = sample.get("source_chunk_ids") or []
    if not isinstance(source_ids, list):
        logger.warning(
            "[cli] evidence source_chunk_ids invalid type={}",
            type(source_ids).__name__,
        )
        source_ids = []
    by_id = {chunk.id: chunk for chunk in chunks}
    valid: list[ChunkResult] = []
    for raw_id in source_ids:
        cid = str(raw_id)
        chunk = by_id.get(cid)
        if chunk is None:
            logger.warning("[cli] evidence source_chunk_id missing id={}", cid)
            continue
        metadata = chunk.metadata or {}
        expected_doc = str(metadata.get("doc_id") or "")
        if cid.split(":", 1)[0] != expected_doc:
            logger.warning(
                "[cli] evidence source_chunk_id doc mismatch id={} doc={}",
                cid,
                expected_doc,
            )
            continue
        valid.append(chunk)
    if source_ids and len(valid) == len(source_ids):
        if sample.get("match_scope") == "exact":
            confidence = "high"
        else:
            confidence = "medium"
        details = [_chunk_detail(chunk) for chunk in valid]
        return {
            "gold_chunk_ids": [chunk.id for chunk in valid],
            "gold_parent_keys": [detail["parent_key"] for detail in details],
            "gold_chunks": details,
            "gold_chunk_source": "testset",
            "gold_chunk_confidence": confidence,
        }

    probe = probe_reference_chunks(reference, chunks)
    if probe["gold_chunk_ids"]:
        return {
            **probe,
            "gold_chunk_source": "probe",
            "gold_chunk_confidence": "medium",
        }
    return {
        **probe,
        "gold_chunk_source": None,
        "gold_chunk_confidence": None,
    }


def _has_unit(text: str, token: str) -> bool:
    """判断数值 token 是否带单位。

    Args:
        text: 去空白后的归一化参考文本
        token: 数值 token

    Returns:
        token 自带单位或其后 4 个字符内出现单位时为 True
    """
    for unit in _UNITS:
        if token.endswith(unit):
            return True
    start = text.find(token)
    while start >= 0:
        window = text[start + len(token) : start + len(token) + 4]
        for unit in _UNITS:
            if unit in window:
                return True
        start = text.find(token, start + 1)
    return False


def compute_testset_conflict(
    reference: str, probe: dict[str, Any], *, numeric_risk: bool = False
) -> dict[str, Any]:
    """计算参考答案与语料的 testset 冲突信号。

    Args:
        reference: 参考答案原文（检查数值后 4 字符内的单位）
        probe: `probe_reference_chunks` 的返回
        numeric_risk: 题集标注的数值风险

    Returns:
        `numeric_conflict` / `unit_missing` / `period_conflict` /
        `missing_fact_tokens`；无数值 token 或语料不可用时前两者为 None
    """
    tokens = probe.get("probe_tokens") or []
    missing = probe.get("missing_fact_tokens") or []
    fact_in_corpus = probe.get("fact_in_corpus")
    if not tokens or fact_in_corpus is None:
        return {
            "numeric_conflict": None,
            "unit_missing": None,
            "period_conflict": None,
            "missing_fact_tokens": missing,
        }

    if numeric_risk and not fact_in_corpus:
        numeric_conflict = True
    else:
        numeric_conflict = False

    text = _match_text(reference)
    unit_missing = True
    for token in tokens:
        if _has_unit(text, token):
            unit_missing = False
            break
    return {
        "numeric_conflict": numeric_conflict,
        "unit_missing": unit_missing,
        "period_conflict": None,
        "missing_fact_tokens": missing,
    }
