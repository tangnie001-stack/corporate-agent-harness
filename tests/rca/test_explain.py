"""模型解释模块的单测：解析容错与降级。"""

import json

import pytest

from src.rca.explain import _parse_json, build_digest
from src.rca.models import EvidenceResult, EvidenceStatus, Tier, TriageVerdict


def _verdict() -> TriageVerdict:
    return TriageVerdict(Tier.WATCH, "2 条慢请求", 620, False)


def test_parse_plain_json():
    data = _parse_json('{"summary": "x", "confidence": "high"}')
    assert data["summary"] == "x"
    assert data["confidence"] == "high"


def test_parse_fenced_json():
    data = _parse_json('\u0060\u0060\u0060json\n{"summary": "y"}\n\u0060\u0060\u0060')
    assert data["summary"] == "y"


def test_parse_ignores_unknown_and_empty():
    data = _parse_json('{"summary": "  ", "hypotheses": "h", "extra": "z"}')
    assert "summary" not in data
    assert data["hypotheses"] == "h"
    assert "extra" not in data


def test_parse_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_json("not json")


def test_digest_contains_key_facts():
    results = {
        "stats": EvidenceResult(
            "stats",
            EvidenceStatus.OK,
            {
                "total": 620,
                "latency_ms": {"p50": 555, "p95": 694, "max": 1684},
                "slow": {"threshold": 1000, "count": 2},
                "errors": {"5xx": 0},
                "by_node": [{"cnt": 1}, {"cnt": 2}],
            },
            10,
        ),
        "detail": EvidenceResult("detail", EvidenceStatus.FAILED, None, 5, "timeout"),
    }
    text = build_digest(_verdict(), results)
    assert "p95=694" in text
    assert "慢请求数: 2" in text
    assert "5xx: 0" in text
    assert "未取到: detail" in text
