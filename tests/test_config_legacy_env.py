"""旧 `RAGAS_*` 环境变量的启动期 fail-fast 测试。

`src.config.settings` 在导入时读取环境变量，因此无法在进程内用 monkeypatch
触发"导入即失败"；必须用子进程重新 import，才能观察到 RuntimeError 与
stderr 中的「旧名 → 新名」提示。
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _import_settings_with(extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    """在子进程中带指定环境变量导入 settings 模块。

    Args:
        extra_env: 追加到子进程环境的键值对（会覆盖同名变量）。

    Returns:
        子进程执行结果，含 returncode / stdout / stderr。
    """
    env = os.environ.copy()
    env.update(extra_env)

    return subprocess.run(
        [sys.executable, "-c", "import src.config.settings"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_legacy_ragas_env_fails_fast():
    """设置 RAGAS_DATA_DIR 时导入失败，并提示改名为 EVAL_DATA_DIR。"""
    proc = _import_settings_with({"RAGAS_DATA_DIR": "data/ragas"})

    assert proc.returncode != 0
    assert "RAGAS_DATA_DIR" in proc.stderr
    assert "EVAL_DATA_DIR" in proc.stderr


def test_legacy_judge_env_fails_fast():
    """设置 RAGAS_LLM_MODEL 时导入失败，并提示改名为 JUDGE_MODEL。"""
    proc = _import_settings_with({"RAGAS_LLM_MODEL": "qwen3.8-max"})

    assert proc.returncode != 0
    assert "RAGAS_LLM_MODEL" in proc.stderr
    assert "JUDGE_MODEL" in proc.stderr
