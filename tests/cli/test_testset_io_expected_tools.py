import pytest

from src.cli.testset_io import _normalize_expected_tools


def test_missing_labels_are_preserved_as_none():
    assert _normalize_expected_tools(None, 0) is None


def test_explicit_empty_labels_are_preserved():
    assert _normalize_expected_tools([], 1) == []


def test_non_empty_labels_are_preserved():
    assert _normalize_expected_tools(["search_web"], 2) == ["search_web"]


def test_string_label_fails_loud():
    with pytest.raises(ValueError, match="第 0 题"):
        _normalize_expected_tools("retrieve_kb", 0)


def test_non_string_element_fails_loud():
    with pytest.raises(ValueError, match="第 1 题"):
        _normalize_expected_tools([1], 1)


def test_empty_string_element_fails_loud():
    with pytest.raises(ValueError, match="第 2 题"):
        _normalize_expected_tools([""], 2)
