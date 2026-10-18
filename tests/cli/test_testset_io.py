"""测试集版本解析单一来源。"""

from __future__ import annotations

import pytest

from src.cli import testset_io


def test_scan_versions_ignores_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    (tmp_path / "testset_kb_v5.json").write_text("{}", encoding="utf-8")
    (tmp_path / "testset_kb_v6.json").write_text("{}", encoding="utf-8")
    (tmp_path / "testset_kb_v7.json.tmp").write_text("{}", encoding="utf-8")
    assert testset_io._scan_versions("kb") == [5, 6]


def test_resolve_explicit_version_does_not_check_existence(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    assert testset_io.resolve_testset_version("kb", 6) == 6


def test_resolve_none_picks_max(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    (tmp_path / "testset_kb_v5.json").write_text("{}", encoding="utf-8")
    (tmp_path / "testset_kb_v6.json").write_text("{}", encoding="utf-8")
    assert testset_io.resolve_testset_version("kb", None) == 6


def test_resolve_none_without_files_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        testset_io.resolve_testset_version("kb", None)


def test_next_version_is_max_plus_one(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    (tmp_path / "testset_kb_v5.json").write_text("{}", encoding="utf-8")
    assert testset_io.next_testset_version("kb") == 6
