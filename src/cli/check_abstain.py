#!/usr/bin/env python3
"""难样本验收脚本 — abstain 率 / 幻觉率 / 目标 chunk 命中。

RAGAS 4 项指标因测试集问题与文档字面匹配而虚高，无法反映真实用户查询
（多轮省略、口语化、表述鸿沟）。本脚本用难样本集（多轮追问 + 对比 +
数据缺失场景）跑完整 LangGraph 链路，验收：
- 目标命中：有数据场景（s2/s5/s6/s3）应命中目标 chunk 且不 abstain
- 正确 abstain：无数据场景（s1）应 abstain 或如实说明，不得拿其他公司数据冒充
- 批量澄清：session 场景 classify 应一次列出多个缺失维度

用法：
    python -m src.cli.check_abstain --kb-name test123

输出每个样本的回答摘要与判定，末尾汇总 abstain 率 / 命中率 / 幻觉率。
"""

import argparse
import asyncio
import sys
import uuid

from src.config.const import SSEInteractionTexts
from src.core.logging import setup_logging
from src.infra.llm.chat_message import ChatMessage


class HardSample:
    """单条难样本。

    sid: 样本编号
    query: 当前用户问题
    history: 对话历史（ChatMessage 列表）
    target_doc_prefix: 期望命中的 doc_id 前缀（命中判定用，空串=不检查）
    target_keyword: 期望命中 chunk 需含的关键词（可为空串）
    expect: "hit"（期望命中回答）| "abstain"（期望 abstain/诚实拒绝）
    note: 样本说明
    """

    def __init__(
        self,
        sid: str,
        query: str,
        history: list[ChatMessage],
        target_doc_prefix: str,
        target_keyword: str,
        expect: str,
        note: str,
    ) -> None:
        self.sid = sid
        self.query = query
        self.history = history
        self.target_doc_prefix = target_doc_prefix
        self.target_keyword = target_keyword
        self.expect = expect
        self.note = note


SAMPLES: list[HardSample] = [
    HardSample(
        sid="s2",
        query="毛利率呢",
        history=[ChatMessage(role="user", content="腾讯2024年营收多少")],
        target_doc_prefix="38e82f97",
        target_keyword="毛利率",
        expect="hit",
        note="多轮省略补全：期望命中腾讯2024毛利率，不 abstain",
    ),
    HardSample(
        sid="s5",
        query="他们的营收呢",
        history=[ChatMessage(role="user", content="介绍一下腾讯")],
        target_doc_prefix="38e82f97",
        target_keyword="总收入",
        expect="hit",
        note="代词指代补全：期望命中腾讯营收（chunk 用'总收入'表述）",
    ),
    HardSample(
        sid="s6",
        query="2024年呢",
        history=[ChatMessage(role="user", content="2023年净利润多少")],
        target_doc_prefix="38e82f97",
        target_keyword="盈利",
        expect="hit",
        note="期间补全：期望命中腾讯净利润（chunk 用'期内盈利'表述）",
    ),
    HardSample(
        sid="s3",
        query="对比一下腾讯和东软的利润",
        history=[],
        target_doc_prefix="38e82f97",
        target_keyword="利润",
        expect="hit",
        note="对比类：期望命中腾讯侧利润数据",
    ),
    HardSample(
        sid="s1",
        query="毛利率呢",
        history=[ChatMessage(role="user", content="2025年第一季度营收多少")],
        target_doc_prefix="29d5f1bb",
        target_keyword="毛利率",
        expect="abstain",
        note="数据缺失：东软Q1无毛利率，期望 abstain 或如实说明，不得拿腾讯数据冒充",
    ),
]


def _fmt_verdict(s: HardSample, answer: str, hit: bool) -> str:
    """输出单个样本的判定。"""
    is_abstain = any(m in answer for m in SSEInteractionTexts.ABSTENTION_MARKERS)
    if s.expect == "hit":
        if hit:
            return "PASS 命中目标数据"
        return "FAIL 未命中目标"
    # expect == "abstain"
    if is_abstain:
        return "PASS 正确 abstain"
    if hit:
        return "FAIL 命中了其他数据（张冠李戴风险）"
    return "WARN 未abstain但未命中目标，需人工判断是否如实回答"


async def _run_sample(
    graph, sample: HardSample, kb_id: str, session_id: str
) -> tuple[str, bool]:
    """跑单个样本，返回 (answer, 是否命中目标)。"""
    trace_id = uuid.uuid4().hex
    final_state = await graph.ainvoke(
        {
            "kb_id": kb_id,
            "session_id": session_id,
            "query": sample.query,
            "trace_id": trace_id,
            "_history": [
                ChatMessage(role=m.role, content=m.content) for m in sample.history
            ],
        }
    )
    answer = final_state.get("answer", "") or ""
    contexts = final_state.get("tool_contexts", []) or []
    hit = False
    for ctx in contexts:
        if (
            sample.target_doc_prefix
            and ctx.doc_id.startswith(sample.target_doc_prefix)
            and (
                not sample.target_keyword
                or sample.target_keyword in (ctx.content or "")
            )
        ):
            hit = True
            break
    return answer, hit


async def _check_batch_clarification(kb_id: str) -> int:
    """独立验证批量澄清：classify 对缺参查询应一次列出多个缺失维度。"""
    from src.infra.search.query_router import QueryRouter
    from src.models import get_classify_llm

    router = QueryRouter(llm=get_classify_llm())
    result = router.route("本季度营收情况如何？", history=[], kb_entities="")
    missing = result.get("missing_entities", [])
    return len(missing)


async def main() -> None:
    """入口：构建 graph、遍历难样本、输出验收报告。"""
    parser = argparse.ArgumentParser(description="难样本 abstain/幻觉验收")
    parser.add_argument("--kb-name", default="test123", help="知识库名称")
    args = parser.parse_args()

    from src.agents.graph.workflow import build_graph
    from src.infra.db.engine import session_factory
    from src.infra.db.repos import KbRepo
    from src.infra.db.vector_store import VectorStore
    from src.infra.llm.prompt_manager import PromptManager
    from src.models import get_llm, get_rerank

    repo = KbRepo(session_factory)
    kb_id = None
    for kb in await repo.get_all_kb():
        if kb.name == args.kb_name:
            kb_id = kb.id
            break
    if not kb_id:
        print(f"error: knowledge base '{args.kb_name}' not found")
        sys.exit(1)

    vector_store = VectorStore()
    llm = get_llm()
    reranker = get_rerank()
    prompt_manager = PromptManager()
    graph = build_graph(vector_store, llm, reranker, prompt_manager)

    print("=" * 90)
    print(f"难样本验收（KB: {args.kb_name}）")
    print("=" * 90)

    n_pass = 0
    n_fail = 0
    n_abstain = 0
    for s in SAMPLES:
        answer, hit = await _run_sample(
            graph, s, kb_id, f"session_{uuid.uuid4().hex[:8]}"
        )
        verdict = _fmt_verdict(s, answer, hit)
        if verdict.startswith("PASS"):
            n_pass += 1
        elif verdict.startswith("FAIL"):
            n_fail += 1
        if any(m in answer for m in SSEInteractionTexts.ABSTENTION_MARKERS):
            n_abstain += 1
        print(f"\n[{s.sid}] {s.query!r} (期望={s.expect}) -> {verdict}")
        print(f"  note: {s.note}")
        print(f"  answer: {answer[:120].replace(chr(10), ' ')!r}")

    # 批量澄清验证
    n_missing = await _check_batch_clarification(kb_id)
    clar_ok = n_missing >= 2
    print("\n" + "=" * 90)
    print(
        f"批量澄清: classify 对'本季度营收情况如何？'列出缺失维度 {n_missing} 个 "
        f"({'PASS(≥2 一次问完)' if clar_ok else 'FAIL(<2)'})"
    )
    print(
        f"汇总: {n_pass} PASS / {n_fail} FAIL，abstain 样本 {n_abstain}/{len(SAMPLES)}"
    )
    print(
        f"结论: 有数据场景命中 + 无数据场景诚实拒绝 + 一次问完 = "
        f"{'全部达标' if n_fail == 0 and clar_ok else '存在未达标项，需人工复核 FAIL 样本'}"
    )


if __name__ == "__main__":
    setup_logging(configure_trace_id=True)
    asyncio.run(main())
