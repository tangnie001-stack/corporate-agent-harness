"""诊断阈值配置：默认值与可环境变量覆盖。"""

import importlib

from src.config import settings


def test_diag_defaults():
    assert settings.DIAG_SCORE_FLOOR == 0.35  # 首版未按 KB 标定
    assert settings.DIAG_SCORE_GAP == 0.05
    assert settings.DIAG_TRACE_LOOKBACK_ROUNDS == 2
    assert settings.DIAG_MAX_REPRESENTATIVE_TRACES == 3


def test_diag_thresholds_read_from_env(monkeypatch):
    monkeypatch.setenv("DIAG_SCORE_FLOOR", "0.5")
    importlib.reload(settings)
    try:
        assert settings.DIAG_SCORE_FLOOR == 0.5
    finally:
        monkeypatch.delenv("DIAG_SCORE_FLOOR", raising=False)
        importlib.reload(settings)
