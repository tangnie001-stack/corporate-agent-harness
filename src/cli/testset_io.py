"""测试集文件加载与版本解析（评测/出题共用）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import EVAL_TESTSET_DIR


def _testset_path(kb_id: str, version: int) -> Path:
    """拼出某版本的题集路径。

    Args:
        kb_id: 知识库 UUID
        version: 版本号

    Returns:
        题集 JSON 路径
    """
    return Path(EVAL_TESTSET_DIR) / f"testset_{kb_id}_v{version}.json"


def _scan_versions(kb_id: str) -> list[int]:
    """扫描某 KB 的全部题集版本。

    Args:
        kb_id: 知识库 UUID

    Returns:
        升序版本号列表（忽略 .tmp 与其它文件）
    """
    pattern = re.compile(rf"^testset_{re.escape(kb_id)}_v(\d+)\.json$")
    directory = Path(EVAL_TESTSET_DIR)
    if not directory.exists():
        return []
    versions: list[int] = []
    for item in directory.iterdir():
        match = pattern.match(item.name)
        if match is not None:
            versions.append(int(match.group(1)))
    return sorted(versions)


def resolve_testset_version(kb_id: str, version: int | None) -> int:
    """解析评测要用的版本号。

    Args:
        kb_id: 知识库 UUID
        version: 显式版本；None 表示取最新

    Returns:
        实际版本号（显式版本不校验文件存在，由 loader 负责）

    Raises:
        FileNotFoundError: version=None 且没有任何题集
    """
    if version is not None:
        return version
    versions = _scan_versions(kb_id)
    if not versions:
        raise FileNotFoundError(
            f"No testset found for kb_id={kb_id}. "
            "请用 python -m src.cli.testset_synth 生成测试集"
        )
    return versions[-1]


def next_testset_version(kb_id: str) -> int:
    """出题用的下一个版本号（最大版本 + 1）。

    Args:
        kb_id: 知识库 UUID

    Returns:
        最大版本号加一；没有题集时从 1 开始
    """
    versions = _scan_versions(kb_id)
    if not versions:
        return 1
    return versions[-1] + 1


def load_testset_samples(kb_id: str, version: int | None = None) -> list[dict]:
    """加载指定知识库的测试集，返回完整 sample 列表。

    Args:
        kb_id: 知识库 UUID
        version: 指定版本号；None 表示自动取最新版本

    Returns:
        原始 sample 列表（含 `user_input` / `reference` / 可选的
        `expected_tools`），供评测与后续 v6 按需取字段

    Raises:
        FileNotFoundError: 没有找到该知识库的测试集文件
    """
    resolved = resolve_testset_version(kb_id, version)
    target = _testset_path(kb_id, resolved)
    if not target.exists():
        raise FileNotFoundError(
            f"测试集文件不存在: {target}。"
            "请用 python -m src.cli.testset_synth 生成测试集"
        )
    data = json.loads(target.read_text(encoding="utf-8"))
    return list(data["samples"])


def _normalize_expected_tools(raw: object, q_index: int) -> list[str] | None:
    """校验题集标签；None 表示无标签，[] 表示显式期望不调用工具。

    Args:
        raw: 原始 expected_tools 值
        q_index: 0-based 题号（仅用于报错）

    Returns:
        None 或字符串列表（保留空列表，不去重）

    Raises:
        ValueError: 值不是 None/list[str]，或含空/非字符串元素
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        # 契约要求统一用 ValueError（调用方只捕 ValueError），故不按 TRY004 改 TypeError
        raise ValueError(  # noqa: TRY004
            f"第 {q_index} 题 expected_tools 必须是 list[str] 或 None，"
            f"得到 {type(raw).__name__}"
        )
    normalized: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"第 {q_index} 题 expected_tools 含非法元素: {item!r}")
        normalized.append(item)
    return normalized


def _load_latest_testset(
    kb_id: str, version: int | None = None
) -> tuple[list[str], list[str], list[list[str] | None]]:
    """加载测试集并适配评分的 (questions, ground_truth, expected_tools) 形状。

    Args:
        kb_id: 知识库 UUID
        version: 指定版本号；None 表示自动取最新版本

    Returns:
        (questions, ground_truth, expected_tools) 三元组；expected_tools 每项
        为 None（字段缺失）、[]（显式不调工具）或非空字符串列表
    """
    samples = load_testset_samples(kb_id, version)
    questions = [s["user_input"] for s in samples]
    ground_truth = [s["reference"] for s in samples]
    expected_tools = [
        _normalize_expected_tools(s.get("expected_tools"), i)
        for i, s in enumerate(samples)
    ]
    return questions, ground_truth, expected_tools
