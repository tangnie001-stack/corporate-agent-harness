"""卡片渲染：代码拼装、不含原文、节点脱敏。"""

from src.rca.diagnose.models import (
    DiagnosisKind,
    DiagnosisReport,
    Evidence,
    Owner,
    RootCause,
)
from src.rca.diagnose.render import render_card
from src.rca.models import Tier


def _report(**kw):
    base = {
        "trace_id": "t",
        "root_cause": RootCause.LOW_RELEVANCE,
        "evidence": (Evidence("E1", "trace", "retrieve_kb#1", "result=30"),),
        "assertions": (("精排分数整体偏低且无尖峰（top1=0.201）", ("E1",)),),
        "next_actions": (),
        "basis_rounds": 0,
        "summary": "精排分数整体偏低且无尖峰（top1=0.201）",
        "kind": DiagnosisKind.ANSWER,
        "tier": Tier.NEED_ACTION,
        "owner": Owner.KB,
    }
    base.update(kw)
    return DiagnosisReport(**base)


def test_card_has_tier_owner_and_cause():
    text = render_card(_report())
    assert "诊断 · 答错/答不准" in text
    assert "档位：需要处理" in text
    assert "归属：知识库/检索" in text
    assert "top1=0.201" in text
    assert "证据 1 条" in text


def test_card_omits_link_when_no_url():
    assert "查看详情" not in render_card(_report())


def test_card_appends_detail_link():
    text = render_card(_report(), detail_url="/diagnose.html?diag_id=abc")
    assert "[查看详情](/diagnose.html?diag_id=abc)" in text


def test_card_limits_basis_lines():
    many = tuple((f"断言{i}", ("E1",)) for i in range(5))
    text = render_card(_report(assertions=many))
    assert "断言0" in text and "断言3" not in text
    assert "另 2 条依据见详情" in text


def test_card_masks_node_address():
    text = render_card(_report(summary="上游 10.1.2.3:8080 超时"))
    assert "10.1.2.3" not in text
    assert "node-***" in text
