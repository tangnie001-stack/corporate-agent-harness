"""fork 子代理在独立 RequestContext 内执行：工具写入子池，主池不受影响。"""

from typing import cast

import pytest

from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.core.eval_evidence import EvidenceRun, current_evidence
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


class _FakeSubAgent:
    """替身子代理：执行期读 current_request_ctx 并写入一个检索上下文。"""

    def __init__(self):
        self.seen_ctx = None

    async def astream_events(self, inputs, config=None, version="v2"):
        ctx = current_request_ctx.get()
        self.seen_ctx = ctx
        if ctx is not None:
            ctx.tool_contexts.append(cast(RAGContext, _FakeContext("子代理材料")))
        yield {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("子代理结论")}}


class _FakeContext:
    def __init__(self, content):
        self.content = content
        self.source = "f.csv"
        self.page = 1
        self.score = 0.9
        self.kind = "kb"
        self.tier = ""

    def to_prompt_text(self):
        return self.content


class _Chunk:
    def __init__(self, text):
        self.content = text
        self.additional_kwargs = {}  # _consume_fork_events 读取 reasoning_content
        self.tool_call_chunks = []


@pytest.mark.asyncio
async def test_fork_writes_child_pool_not_parent(monkeypatch):
    """子代理的检索上下文落在子池，主池保持为空。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token = current_request_ctx.set(parent)
    fake = _FakeSubAgent()

    executor = SkillExecutor(main_llm=object())
    monkeypatch.setattr(executor, "_build_sub_agent", lambda record, preset, via: fake)
    monkeypatch.setattr(executor, "_fork_tools", lambda record, preset: [])
    record = SkillRecord(
        name="finance-analyst",
        description="d",
        context=SkillContext.FORK,
        fork_body="正文",
    )
    run = DelegateRun(delegate_id="d1", skill_name=record.name, ctx=parent.child())

    try:
        await executor.execute(record, "任务", run)
    finally:
        current_request_ctx.reset(token)

    assert fake.seen_ctx is run.ctx
    assert len(run.ctx.tool_contexts) == 1
    assert parent.tool_contexts == []


@pytest.mark.asyncio
async def test_context_var_restored_after_fork(monkeypatch):
    """执行结束后 ContextVar 复位到主 ctx（不泄漏到后续节点）。"""
    parent = RequestContext(session_id="s1")
    token = current_request_ctx.set(parent)
    executor = SkillExecutor(main_llm=object())
    monkeypatch.setattr(
        executor, "_build_sub_agent", lambda record, preset, via: _FakeSubAgent()
    )
    monkeypatch.setattr(executor, "_fork_tools", lambda record, preset: [])
    record = SkillRecord(
        name="x", description="d", context=SkillContext.FORK, fork_body="b"
    )
    run = DelegateRun(delegate_id="d1", skill_name="x", ctx=parent.child())

    try:
        await executor.execute(record, "t", run)
        assert current_request_ctx.get() is parent
    finally:
        current_request_ctx.reset(token)


class _EvidenceProbeSubAgent(_FakeSubAgent):
    """替身子代理：额外记录执行期看到的当前题证据。"""

    def __init__(self):
        super().__init__()
        self.seen_evidence = "unset"

    async def astream_events(self, inputs, config=None, version="v2"):
        self.seen_evidence = current_evidence.get()
        async for event in super().astream_events(inputs, config, version):
            yield event


@pytest.mark.asyncio
async def test_fork_clears_and_restores_current_evidence(monkeypatch):
    """fork 执行期间证据置空（子代理证据不混入当前题），结束后恢复。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token_ctx = current_request_ctx.set(parent)
    run = EvidenceRun(run_id="r1", run_trace_id=None, enabled=True)
    collector = run.new_question(0)
    token_ev = current_evidence.set(collector)
    fake = _EvidenceProbeSubAgent()
    executor = SkillExecutor(main_llm=object())
    monkeypatch.setattr(executor, "_build_sub_agent", lambda *a, **k: fake)
    monkeypatch.setattr(executor, "_fork_tools", lambda *a, **k: [])
    record = SkillRecord(
        name="x", description="d", context=SkillContext.FORK, fork_body="b"
    )
    try:
        await executor.execute(
            record,
            "t",
            DelegateRun(delegate_id="d1", skill_name="x", ctx=parent.child()),
        )
        assert fake.seen_evidence is None
        assert current_evidence.get() is collector
    finally:
        current_evidence.reset(token_ev)
        current_request_ctx.reset(token_ctx)
