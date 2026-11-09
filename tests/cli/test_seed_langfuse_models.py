"""seed 模型定价的幂等与 pattern 行为（不发网络：client 为替身）。"""

from typing import Any

import pytest

from src.cli.seed_langfuse_models import build_model_request, seed


def test_match_pattern_is_anchored_and_matches_target():
    """锚定正则匹配目标模型（含大小写）。"""
    import re

    req = build_model_request("qwen3.8-flash", 0.000003, 0.000006)
    pattern = req["match_pattern"]
    assert re.search(pattern, "qwen3.8-flash")
    assert re.search(pattern, "QWEN3.8-FLASH")


def test_match_pattern_does_not_misfire_on_lookalikes():
    """Postgres `~` 是子串匹配：pattern 未锚定会误配形近名，故必须锚定。"""
    import re

    pattern = build_model_request("qwen3.8-flash", 0.000003, 0.000006)["match_pattern"]
    for lookalike in (
        "prefix-qwen3.8-flash",
        "qwen3.8-flash-old",
        "qwen3.8-flash-2026-07-15",
        "qwen3x8-flash",
        "qwen3-8-flash",
    ):
        assert not re.search(pattern, lookalike), lookalike


def test_match_pattern_escapes_regex_metachars():
    """模型名里的 `.` 必须被转义，不能当通配符。"""
    import re

    pattern = build_model_request("a.b", 0.0, 0.0)["match_pattern"]
    assert re.search(pattern, "a.b")
    assert not re.search(pattern, "axb")


def test_request_carries_unit_tokens():
    """unit 必须是 TOKENS，否则服务端不会按 token 算价。"""
    req = build_model_request("m", 0.000003, 0.000006)
    assert req["unit"] == "TOKENS"
    assert req["model_name"] == "m"
    assert req["input_price"] == 0.000003
    assert req["output_price"] == 0.000006


class _Models:
    def __init__(self, existing: list):
        self._existing = existing
        self.created: list = []

    def list(self, *, page=None, limit=None):
        class _Resp:
            def __init__(self, data):
                self.data = data

        return _Resp(self._existing)

    def create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs


class _Client:
    def __init__(self, existing):
        self.api: Any = type("_Api", (), {"models": _Models(existing)})()


@pytest.mark.asyncio
async def test_skips_when_price_is_zero():
    """单价为 0（未配置）时不得写入零价模型定义。"""
    client = _Client([])
    outcome = await seed(client, "m", 0.0, 0.0)
    assert outcome == "skipped"
    assert client.api.models.created == []


@pytest.mark.asyncio
async def test_skips_when_only_one_price_configured():
    """只配一侧单价（另一侧为 0）时跳过：0 是「未配置」哨兵，需两侧齐全。"""
    client = _Client([])
    assert await seed(client, "m", 0.000003, 0.0) == "skipped"
    assert client.api.models.created == []

    client2 = _Client([])
    assert await seed(client2, "m", 0.0, 0.000006) == "skipped"
    assert client2.api.models.created == []


@pytest.mark.asyncio
async def test_creates_when_absent_and_idempotent_when_present():
    """不存在则创建；同名已存在则跳过（幂等判据只看 model_name）。"""
    client = _Client([])
    assert await seed(client, "m", 0.000003, 0.000006) == "created"
    assert len(client.api.models.created) == 1

    existing = [type("_M", (), {"model_name": "m"})()]
    client2 = _Client(existing)
    assert await seed(client2, "m", 0.000003, 0.000006) == "exists"
    assert client2.api.models.created == []


@pytest.mark.asyncio
async def test_create_receives_createmodelrequest_with_camelcase_wire_keys():
    """`models.create` 必须收到 CreateModelRequest（线格式 camelCase），而非裸 snake_case dict。"""
    client = _Client([])
    assert await seed(client, "m", 0.000003, 0.000006) == "created"
    received = client.api.models.created[0]
    assert isinstance(received, dict)
    wire = received
    assert set(wire) >= {"model_name", "match_pattern", "input_price", "output_price"}
    assert wire["model_name"] == "m"
    assert wire["match_pattern"] == "(?i)^m$"
