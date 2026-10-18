"""测试集文件加载（评测侧唯一入口；不依赖 ragas）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import EVAL_TESTSET_DIR


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
    pattern = re.compile(rf"^testset_{re.escape(kb_id)}_v(\d+)\.json$")
    testset_dir = Path(EVAL_TESTSET_DIR)

    if version is not None:
        target_file = testset_dir / f"testset_{kb_id}_v{version}.json"
        if not target_file.exists():
            raise FileNotFoundError(
                f"测试集文件不存在: {target_file}。"
                "请用 python -m src.cli.testset_synth 生成测试集"
            )
        latest_file = target_file
    else:
        max_version = 0
        latest_file = None
        if testset_dir.exists():
            for f in testset_dir.iterdir():
                m = pattern.match(f.name)
                if m:
                    ver = int(m.group(1))
                    if ver > max_version:
                        max_version = ver
                        latest_file = f
        if latest_file is None:
            raise FileNotFoundError(
                f"No testset found for kb_id={kb_id}. "
                "请用 python -m src.cli.testset_synth 生成测试集"
            )

    data = json.loads(latest_file.read_text(encoding="utf-8"))
    return list(data["samples"])


def _load_latest_testset(
    kb_id: str, version: int | None = None
) -> tuple[list[str], list[str]]:
    """加载测试集并适配评分的 (questions, ground_truth) 形状。

    Args:
        kb_id: 知识库 UUID
        version: 指定版本号；None 表示自动取最新版本

    Returns:
        (questions, ground_truth) 元组
    """
    samples = load_testset_samples(kb_id, version)
    questions = [s["user_input"] for s in samples]
    ground_truth = [s["reference"] for s in samples]
    return questions, ground_truth
