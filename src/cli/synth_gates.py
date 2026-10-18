"""v6 出题质量门（确定性，不调用 LLM）。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from src.cli.eval_self_contained import enforce_self_contained
from src.config.const import (
    EVAL_HOP_MULTI_MAX_RATIO,
    EVAL_LANG_CJK_MIN_RATIO,
    EVAL_LANG_SENTENCE_CJK_MIN_RATIO,
    EVAL_NUMERIC_METRIC_WORDS,
    EVAL_SYNTH_SUBJECT_WORDS,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_SENTENCE_RE = re.compile(r"[。？！.!?]")
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")
_MONEY_RE = re.compile(r"(?:元|万元|亿元|%|个百分点|倍)")
_NUMBER_RE = re.compile(r"\d[\d,.]*")
_DATE_NUMBER_RE = re.compile(r"(?:19|20)\d{2}(?=年)|\d{1,2}(?=[月日])")


def _ratio(text: str) -> tuple[float, int]:
    """计算 CJK 字母占全部字母的比例。

    Args:
        text: 待判定文本

    Returns:
        `(比例, 字母总数)`；0 分母时比例为 0
    """
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    total = cjk + latin
    if total == 0:
        return 0.0, 0
    return cjk / total, total


def strip_parentheticals(text: str) -> str:
    """去掉括号及其内容。"""
    return _PAREN_RE.sub("", text)


def strip_context_entities(text: str, entities: set[str]) -> str:
    """按整词、大小写不敏感剔除上下文实体与公司别名。

    Args:
        text: 原始文本
        entities: 实体集合（含 ASCII 与中文别名）

    Returns:
        剔除后的文本
    """
    result = text
    for entity in sorted(entities, key=len, reverse=True):
        if len(entity) < 2:
            continue
        pattern = rf"(?<![A-Za-z0-9]){re.escape(entity)}(?![A-Za-z0-9])"
        result = re.sub(pattern, " ", result, flags=re.IGNORECASE)
    return result


_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&.\-]{1,39}")


def extract_context_entities(contexts: list[str]) -> set[str]:
    """从上下文提取拉丁实体词（用于语言门剔除）。

    Args:
        contexts: 该样本的 `reference_contexts`

    Returns:
        长度 ≥2 的拉丁 token 集合
    """
    entities: set[str] = set()
    for text in contexts:
        for match in _LATIN_TOKEN_RE.finditer(text):
            token = match.group(0).strip(" .-")
            if len(token) >= 2:
                entities.add(token)
    return entities


def _sentences(text: str) -> list[str]:
    """按中英文句末标点切句。"""
    return [part for part in _SENTENCE_RE.split(text) if part.strip()]


def check_language(question: str, reference: str, entities: set[str]) -> bool:
    """判定题面与参考答案是否符合中文要求。

    Args:
        question: 题面
        reference: 参考答案
        entities: 该样本的上下文实体与公司别名

    Returns:
        True 表示通过
    """
    for text in (question, reference):
        for sentence in _sentences(text):
            cjk = len(_CJK_RE.findall(sentence))
            latin = len(_LATIN_RE.findall(sentence))
            if latin >= 5 and cjk == 0:
                return False

    for text in (question, reference):
        no_paren = strip_parentheticals(text)
        for sentence in _sentences(no_paren):
            cjk = len(_CJK_RE.findall(sentence))
            latin = len(_LATIN_RE.findall(sentence))
            total = cjk + latin
            if total >= 5 and cjk / total < EVAL_LANG_SENTENCE_CJK_MIN_RATIO:
                return False
        ratio, total = _ratio(strip_context_entities(no_paren, entities))
        if total == 0 or ratio < EVAL_LANG_CJK_MIN_RATIO:
            return False
    return True


def compute_numeric_risk(reference: str) -> bool:
    """判断参考答案是否含需要单位/口径判定的数值事实。"""
    return bool(_NUMBER_RE.search(reference)) and bool(_MONEY_RE.search(reference))


def _first_numeric_fact(reference: str) -> re.Match[str] | None:
    """定位第一个「后接单位/百分号/金额词」的非日期数字。"""
    for match in _NUMBER_RE.finditer(reference):
        if _DATE_NUMBER_RE.match(reference, match.start()):
            continue
        tail = reference[match.end() : match.end() + 4]
        if _MONEY_RE.match(tail):
            return match
    return None


def check_complete_sentence(
    reference: str,
    numeric_risk: bool,
    subject_aliases: set[str] | None = None,
) -> bool:
    """判定参考答案是否为自包含完整句。

    Args:
        reference: 参考答案
        numeric_risk: 是否含数值事实
        subject_aliases: 题干公司别名（数值答案的主体判定）

    Returns:
        True 表示通过
    """
    if len(reference) < 8:
        return False
    if reference[-1] not in "。！？.!?":
        return False
    if not re.search(r"[^\d\s\W_]", reference):
        return False
    if not numeric_risk:
        return True

    fact = _first_numeric_fact(reference)
    if fact is None:
        return False
    prefix = reference[: fact.start()]
    has_subject = any(word in prefix for word in EVAL_SYNTH_SUBJECT_WORDS)
    if not has_subject and subject_aliases:
        has_subject = any(alias in prefix for alias in subject_aliases)
    if not has_subject:
        return False
    return any(word in reference for word in EVAL_NUMERIC_METRIC_WORDS)


def _normalize_question(text: str) -> str:
    """题面去标点/空白后小写，用于精确去重。"""
    return re.sub(r"[\W_]+", "", text).lower()


def _hop_sort_key(sample: dict) -> str:
    """多跳截断的确定性排序键。"""
    return hashlib.sha1(sample["user_input"].encode("utf-8")).hexdigest()


def enforce_hop_ratio(samples: list[dict]) -> tuple[list[dict], int]:
    """把 multi_hop 占比截到 1/3。

    Args:
        samples: 已通过前四道门的样本

    Returns:
        `(kept, dropped_count)`
    """
    total = len(samples)
    if total == 0:
        return [], 0
    allowed = int(total * EVAL_HOP_MULTI_MAX_RATIO + 1e-9)
    multi = [s for s in samples if s.get("hop_type") == "multi_hop"]
    if len(multi) <= allowed:
        return samples, 0
    to_drop = len(multi) - allowed
    dropped_questions = {
        s["user_input"] for s in sorted(multi, key=_hop_sort_key)[:to_drop]
    }
    kept = [s for s in samples if s["user_input"] not in dropped_questions]
    return kept, to_drop


def run_gates(
    samples: list[dict], aliases_by_company: dict[str, set[str]]
) -> tuple[list[dict], dict[str, Any]]:
    """按「自足 → 完整句 → 语言 → 去重 → 多跳」执行质量门。

    Args:
        samples: `golden_to_sample` 产出的样本
        aliases_by_company: `{company_norm: aliases}`

    Returns:
        `(kept, stats)`
    """
    stats: dict[str, Any] = {
        "generated": len(samples),
        "kept": 0,
        "language_checked": 0,
        "self_contained_rewritten": 0,
        "failed_contexts": 0,
        "dropped": {
            "self_contained": 0,
            "not_complete_sentence": 0,
            "language": 0,
            "duplicate": 0,
            "hop_ratio": 0,
        },
    }

    for sample in samples:
        sample["_original_user_input"] = sample["user_input"]

    kept, dropped = enforce_self_contained(samples, aliases_by_company)
    stats["dropped"]["self_contained"] = len(dropped)
    stats["self_contained_rewritten"] = sum(
        1 for s in kept if s["user_input"] != s["_original_user_input"]
    )

    after_sentence: list[dict] = []
    for sample in kept:
        if not check_complete_sentence(
            sample["reference"],
            bool(sample.get("numeric_risk")),
            set(sample.get("_entities") or ()),
        ):
            stats["dropped"]["not_complete_sentence"] += 1
            continue
        after_sentence.append(sample)

    stats["language_checked"] = len(after_sentence)
    after_language: list[dict] = []
    for sample in after_sentence:
        entities: set[str] = set(sample.get("_entities") or ())
        if not check_language(
            sample["user_input"], sample["reference"], entities
        ) or not check_language(
            sample["_original_user_input"], sample["reference"], entities
        ):
            stats["dropped"]["language"] += 1
            continue
        after_language.append(sample)

    seen: set[str] = set()
    after_dedupe: list[dict] = []
    for sample in after_language:
        key = _normalize_question(sample["user_input"])
        if key in seen:
            stats["dropped"]["duplicate"] += 1
            continue
        seen.add(key)
        after_dedupe.append(sample)

    kept_final, hop_dropped = enforce_hop_ratio(after_dedupe)
    stats["dropped"]["hop_ratio"] = hop_dropped
    stats["kept"] = len(kept_final)
    return kept_final, stats
