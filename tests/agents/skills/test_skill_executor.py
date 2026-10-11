"""测试 SkillExecutor — inline 注入 / fork 子代理（零工具 astream）/ 防失控 / thinking 跟随。"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

import src.agents.skills.executor as executor_module
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import (
    DELEGATE_DEFAULT_MAX_TURNS,
    DELEGATE_VIA_DELEGATE,
    SSEInteractionTexts,
)
from src.infra.llm.request_context import RequestContext, current_request_ctx


def _record(**overrides) -> SkillRecord:
    """构造 SkillRecord，缺省 inline 形态。"""
    defaults = {
        "name": "test-skill",
        "description": "测试 skill",
        "context": SkillContext.INLINE,
        "inline_prompt": "方法论 {task}",
        "fork_body": None,
        "model": None,
        "allowed_tools": [],
        "source_path": Path("/tmp/test-skill/SKILL.md"),
    }
    defaults.update(overrides)
    return SkillRecord(**defaults)


@tool("retrieve_kb")
def _retrieve(query: str) -> str:
    """检索。"""
    return query


@tool("write_doc")
def _write_doc(text: str) -> str:
    """写文件（写类工具的代表）。"""
    return text


def _event(kind, chunk=None, output=None):
    """构造 langgraph v2 事件 dict（fake astream_events 事件源元素）。"""
    data = {}
    if chunk is not None:
        data["chunk"] = chunk
    if output is not None:
        data["output"] = output
    return {
        "event": kind,
        "name": "agent",
        "metadata": {"langgraph_node": "model"},
        "data": data,
    }


def _agen(*items):
    async def gen():
        for it in items:
            yield it

    return gen()


def _fake_sub_agent(*items):
    """astream_events 版 fake sub-agent（记录初始输入供断言）。"""
    fake = MagicMock()
    captured: dict = {}

    def _astream_events(inputs, *a, **k):
        captured["inputs"] = inputs
        return _agen(*items)

    fake.astream_events = _astream_events
    fake.captured = captured
    return fake


def _drain_channel(ctx):
    """排空 ctx.clarify_channel，按序返回全部事件。"""
    out = []
    while not ctx.clarify_channel.empty():
        out.append(ctx.clarify_channel.get_nowait())
    return out


def test_sub_agent_uses_shared_factory_with_empty_middleware(monkeypatch):
    """子角色走共用装配入口：只传模型/工具面/静态 system，middleware 集合为空。"""
    calls: dict = {}

    def _spy_build_agent(model, tools, **kwargs):
        calls.update(kwargs)
        calls["tools_count"] = len(tools)
        return object()

    monkeypatch.setattr(executor_module, "build_agent", _spy_build_agent, raising=False)
    executor = SkillExecutor(main_llm=MagicMock(), tool_provider=list)
    executor._build_sub_agent(record=None, preset=None, via=DELEGATE_VIA_DELEGATE)
    assert calls["middleware_extra"] == [], "子角色 middleware 必须为空"
    # 不传 max_turns / name：轮次上限由 fork 消费侧判定，图内不装预算
    assert "max_turns" not in calls, "子角色的轮次上限不走图内预算"
    assert "name" not in calls, "子角色不传 name"
    assert isinstance(calls["system"], str), "子角色用静态 system 串"


@pytest.mark.asyncio
async def test_inline_returns_rendered_prompt():
    """inline：返回 inline_prompt，{task} 替换为任务文本。"""
    exe = SkillExecutor(main_llm=MagicMock())
    out = await exe.execute(_record(), task="计算毛利率")
    assert out == "方法论 计算毛利率"


@pytest.mark.asyncio
async def test_inline_without_placeholder_returns_as_is():
    """inline 无 {task} 占位：原样返回正文。"""
    rec = _record(inline_prompt="固定方法论，无需任务占位")
    exe = SkillExecutor(main_llm=MagicMock())
    out = await exe.execute(rec, task="任意任务")
    assert out == "固定方法论，无需任务占位"


@pytest.mark.asyncio
async def test_inline_renders_arguments_placeholder():
    """inline：$ARGUMENTS 替换为任务文本（当前 skill 正文写法）。"""
    rec = _record(inline_prompt="任务：$ARGUMENTS")
    exe = SkillExecutor(main_llm=MagicMock())
    out = await exe.execute(rec, task="计算毛利率")
    assert out == "任务：计算毛利率"


@pytest.mark.asyncio
async def test_inline_renders_legacy_task_placeholder():
    """inline：{task} 仍替换为任务文本（向后兼容旧写法）。"""
    rec = _record(inline_prompt="方法论 {task}")
    exe = SkillExecutor(main_llm=MagicMock())
    out = await exe.execute(rec, task="计算毛利率")
    assert out == "方法论 计算毛利率"


@pytest.mark.asyncio
async def test_fork_body_renders_arguments_placeholder():
    """fork：正文（子代理 prompt）中的 $ARGUMENTS 替换为任务文本。"""
    main_llm = MagicMock()
    exe = SkillExecutor(main_llm=main_llm)
    rec = _record(
        context=SkillContext.FORK,
        fork_body="按方法论分析。\n\n任务：$ARGUMENTS",
        inline_prompt=None,
        model=None,
    )
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析结果")),
        _event("on_chat_model_end", output=AIMessage(content="分析结果")),
    )
    with patch("src.agents.skills.executor.build_agent", return_value=fake_sub):
        await exe.execute(rec, task="分析年报")

    # skill 正文改作初始 user message：$ARGUMENTS 已渲染为任务文本
    msg = fake_sub.captured["inputs"]["messages"][0]
    assert msg.content == "按方法论分析。\n\n任务：分析年报"


@pytest.mark.asyncio
async def test_fork_reuses_main_llm_when_model_empty():
    """fork 且未声明 model：复用主 agent llm 实例；经 astream_events 聚合结果。"""
    main_llm = MagicMock()
    exe = SkillExecutor(main_llm=main_llm)
    rec = _record(
        context=SkillContext.FORK,
        fork_body="你是财务建模专家",
        inline_prompt=None,
        model=None,
    )
    chunk = AIMessageChunk(content="分析结果：营收下降 20%")
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=chunk),
        _event("on_chat_model_end", output=AIMessage(content="分析结果：营收下降 20%")),
    )
    with patch(
        "src.agents.skills.executor.build_agent", return_value=fake_sub
    ) as mock_build:
        out = await exe.execute(rec, task="分析年报")

    args, _kwargs = mock_build.call_args
    assert args[1] == []  # 工具面位置参数（build_agent(model, tools, ...)）
    assert args[0] is main_llm  # 无 ctx 且未声明 model → 复用主实例
    # 正文无占位符 → 追加默认任务段后作初始 user message
    msg = fake_sub.captured["inputs"]["messages"][0]
    assert msg.content == "你是财务建模专家\n任务：分析年报"
    assert "分析结果" in out


@pytest.mark.asyncio
async def test_fork_model_override_builds_new_llm():
    """fork 且声明 model：门面 get_llm(model=record.model) 新建实例。"""
    fake_llm = MagicMock()
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="专家分析")),
        _event("on_chat_model_end", output=AIMessage(content="专家分析")),
    )

    with (
        patch(
            "src.agents.skills.executor.build_agent", return_value=fake_sub
        ) as mock_build,
        patch(
            "src.agents.skills.executor.get_llm", return_value=fake_llm
        ) as mock_get_llm,
    ):
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(
            context=SkillContext.FORK,
            fork_body="专家人格",
            inline_prompt=None,
            model="qwen3.8-max",
        )
        out = await exe.execute(rec, task="分析")
        # 无 ctx（thinking=None）且声明 model → 仅传 model，不注入思考 extra_body
        mock_get_llm.assert_called_once_with(model="qwen3.8-max")
        args, _kwargs = mock_build.call_args
        assert args[0] is fake_llm
        assert "专家分析" in out


@pytest.mark.asyncio
async def test_fork_thinking_follows_ctx_deep_thinking_true():
    """ctx.deep_thinking=True：extra_body 含 enable_thinking 与 preserve_thinking。

    形状出自 provider 层（`thinking_extra_body`），与主链路请求期同源（D8）。
    """
    fake_llm = MagicMock()
    fake_llm.model_name = "main-model"
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
        _event("on_chat_model_end", output=AIMessage(content="分析")),
    )
    ctx = RequestContext(session_id="s1")
    ctx.deep_thinking = True
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.build_agent", return_value=fake_sub),
            patch(
                "src.agents.skills.executor.get_llm", return_value=fake_llm
            ) as mock_get_llm,
        ):
            main_llm = MagicMock()
            main_llm.model_name = "main-model"
            exe = SkillExecutor(main_llm=main_llm)
            rec = _record(
                context=SkillContext.FORK,
                fork_body="人格",
                inline_prompt=None,
                model=None,
            )
            await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    kwargs = mock_get_llm.call_args.kwargs
    assert kwargs["model"] == "main-model"
    assert kwargs["extra_body"] == {
        "enable_thinking": True,
        "preserve_thinking": True,
    }


@pytest.mark.asyncio
async def test_fork_thinking_follows_ctx_deep_thinking_false():
    """ctx.deep_thinking=False：新建 llm 带 enable_thinking=False（不落模型默认思考）。"""
    fake_llm = MagicMock()
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
        _event("on_chat_model_end", output=AIMessage(content="分析")),
    )
    ctx = RequestContext(session_id="s1")  # deep_thinking 默认 False
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.build_agent", return_value=fake_sub),
            patch(
                "src.agents.skills.executor.get_llm", return_value=fake_llm
            ) as mock_get_llm,
        ):
            main_llm = MagicMock()
            main_llm.model_name = "main-model"
            exe = SkillExecutor(main_llm=main_llm)
            rec = _record(
                context=SkillContext.FORK,
                fork_body="人格",
                inline_prompt=None,
                model=None,
            )
            await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert mock_get_llm.call_args.kwargs["extra_body"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_fork_thinking_none_without_ctx_reuses_main_llm():
    """无请求上下文：复用主 agent llm（不新建；思考走模型默认）。"""
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
        _event("on_chat_model_end", output=AIMessage(content="分析")),
    )
    with (
        patch(
            "src.agents.skills.executor.build_agent", return_value=fake_sub
        ) as mock_build,
        patch("src.agents.skills.executor.get_llm") as mock_get_llm,
    ):
        main_llm = MagicMock()
        main_llm.model_name = "main-model"
        exe = SkillExecutor(main_llm=main_llm)
        rec = _record(
            context=SkillContext.FORK,
            fork_body="人格",
            inline_prompt=None,
            model=None,
        )
        await exe.execute(rec, task="分析")
    mock_get_llm.assert_not_called()
    args, _kwargs = mock_build.call_args
    assert args[0] is main_llm


@pytest.mark.asyncio
async def test_fork_total_timeout_interrupts_with_reason():
    """总时长超时（deep_thinking=False 档 240s，测试收紧）：中断并返回超时文案。"""
    import src.agents.skills.executor as exec_mod

    async def _never(*a, **k):
        await asyncio.sleep(3600)
        yield  # pragma: no cover

    fake = MagicMock()
    fake.astream_events = _never
    ctx = RequestContext(session_id="s1")  # deep_thinking=False → 走默认档
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.build_agent", return_value=fake),
            patch.object(exec_mod.settings, "DELEGATE_TOTAL_TIMEOUT_S", 0.05),
        ):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(
                context=SkillContext.FORK, fork_body="人格", inline_prompt=None
            )
            out = await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
    assert ctx.fork_stop_reason == "total"


@pytest.mark.asyncio
async def test_fork_idle_timeout_interrupts():
    """事件级空闲超时：两事件间隔超 DELEGATE_MAX_IDLE_S → reason=idle。"""
    import src.agents.skills.executor as exec_mod

    async def _slow_events(*a, **k):
        yield _event("on_chat_model_stream", chunk=AIMessageChunk(content="思考片段"))
        await asyncio.sleep(5)  # 超过被收紧的空闲阈值
        yield _event("on_chat_model_stream", chunk=AIMessageChunk(content="后续"))

    fake = MagicMock()
    fake.astream_events = _slow_events
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.build_agent", return_value=fake),
            patch.object(exec_mod.settings, "DELEGATE_MAX_IDLE_S", 0.1),
        ):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(
                context=SkillContext.FORK, fork_body="人格", inline_prompt=None
            )
            out = await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
    assert ctx.fork_stop_reason == "idle"


@pytest.mark.asyncio
async def test_fork_turn_limit_interrupts():
    """turn 上限：模型 start 事件超 DELEGATE_DEFAULT_MAX_TURNS → reason=turn。"""
    start_events = [
        _event("on_chat_model_start") for _ in range(DELEGATE_DEFAULT_MAX_TURNS + 1)
    ]
    fake_sub = _fake_sub_agent(
        *start_events,
        _event("on_chat_model_end", output=AIMessage(content="x")),
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch("src.agents.skills.executor.build_agent", return_value=fake_sub):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(
                context=SkillContext.FORK,
                fork_body="人格",
                inline_prompt=None,
            )
            out = await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
    assert ctx.fork_stop_reason == "turn"


@pytest.mark.asyncio
async def test_fork_cancel_aborts_with_cancelled():
    """请求取消：ctx.abort_signal 置位 → fork 抛 CancelledError、reason=cancelled。"""
    ctx = RequestContext(session_id="s1")
    ctx.abort_signal.set()
    token = current_request_ctx.set(ctx)
    try:
        fake_sub = _fake_sub_agent(
            _event("on_chat_model_stream", chunk=AIMessageChunk(content="a")),
            _event("on_chat_model_stream", chunk=AIMessageChunk(content="b")),
        )
        with patch("src.agents.skills.executor.build_agent", return_value=fake_sub):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(
                context=SkillContext.FORK, fork_body="人格", inline_prompt=None
            )
            with pytest.raises(asyncio.CancelledError):
                await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert ctx.fork_stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_fork_pushes_delegate_delta_events():
    """fork 增量经 ctx.clarify_channel 投 delegate delta（thinking/content）；带 delegate_id。"""
    chunk1 = AIMessageChunk(
        content="", additional_kwargs={"reasoning_content": "思考A"}
    )
    chunk2 = AIMessageChunk(content="正文B")
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=chunk1),
        _event("on_chat_model_stream", chunk=chunk2),
        _event(
            "on_chat_model_end",
            output=AIMessage(
                content="正文B",
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            ),
        ),
    )
    ctx = RequestContext(session_id="s1")
    ctx.delegate_id = "abc123"
    token = current_request_ctx.set(ctx)
    try:
        with patch("src.agents.skills.executor.build_agent", return_value=fake_sub):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(
                context=SkillContext.FORK, fork_body="人格", inline_prompt=None
            )
            out = await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    deltas = [it for it in _drain_channel(ctx) if it.get("type") == "delegate"]
    kinds = [it["kind"] for it in deltas if it["action"] == "delta"]
    assert "thinking" in kinds and "content" in kinds
    assert any(it.get("delegate_id") == "abc123" for it in deltas)
    assert "正文B" in out


@pytest.mark.asyncio
async def test_fork_result_truncated():
    """fork 结果超 DELEGATE_RESULT_LIMIT → 截断并带总字数提示。"""
    long_text = "字" * 3000
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content=long_text)),
        _event("on_chat_model_end", output=AIMessage(content=long_text)),
    )
    with patch("src.agents.skills.executor.build_agent", return_value=fake_sub):
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(context=SkillContext.FORK, fork_body="人格", inline_prompt=None)
        out = await exe.execute(rec, task="分析")
    assert "3000" in out
    assert out.startswith(SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX[:10])


# ---- fork 回调隔离回归（final review Critical） ----

_LEAK_MARKER = "子代理分析泄漏标记XYZ"
_FORK_TASK = "委派给子代理的任务"
_FORK_ANSWER = f"【子代理分析】{_LEAK_MARKER}"
_FINAL_ANSWER = "整合后的最终回答，不重复子代理原文"


class _ScenarioChatModel(BaseChatModel):
    """按输入消息特征分场景的假 LLM（真实 on_chat_model_* 事件源）。

    - 外层首轮（无 SystemMessage/ToolMessage）→ 流式输出 delegate_task 工具调用
    - fork 子代理内（含 SystemMessage prompt）→ 流式输出 _FORK_ANSWER（含泄漏标记）
    - 外层收尾（含 ToolMessage）→ 流式输出 _FINAL_ANSWER
    """

    model_name: str = "scenario-fake"

    def _pick_text(self, messages) -> str:
        """按消息特征选应答文本：fork 内带 prompt SystemMessage，收尾带 ToolMessage。"""
        if any(isinstance(m, SystemMessage) for m in messages):
            return _FORK_ANSWER
        if any(isinstance(m, ToolMessage) for m in messages):
            return _FINAL_ANSWER
        return ""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        text = self._pick_text(messages)
        if text:
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=text))]
            )
        ai_message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "delegate_task",
                    "args": {"task": _FORK_TASK, "skill": "analyst"},
                    "id": "call_delegate_1",
                    "type": "tool_call",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def _stream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> Iterator[ChatGenerationChunk]:
        text = self._pick_text(messages)
        if not text:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "delegate_task",
                            "args": json.dumps(
                                {"task": _FORK_TASK, "skill": "analyst"}
                            ),
                            "id": "call_delegate_1",
                            "index": 0,
                        }
                    ],
                )
            )
            return
        for ch in text:
            yield ChatGenerationChunk(message=AIMessageChunk(content=ch))

    @property
    def _llm_type(self) -> str:
        return "scenario-chat-model"


def _make_fork_delegate_tool(model: BaseChatModel):
    """构造走真实 fork 路径的 delegate_task 工具（SkillExecutor 不 mock create_agent）。"""

    @tool("delegate_task")
    async def delegate_task(task: str, skill: str) -> str:
        """调用 fork skill 深度分析并返回子代理文本。"""
        record = _record(
            context=SkillContext.FORK,
            fork_body="你是领域分析专家",
            inline_prompt=None,
            model=None,
        )
        executor = SkillExecutor(main_llm=model)
        return await executor.execute(record, task)

    return delegate_task


def _collect_fork_leak_events(stream_events) -> tuple[str, list[str]]:
    """从外层 astream_events 累积 full_answer 并收集带泄漏标记的 token 事件。

    与 agent_service._convert_event 同口径：仅 metadata.langgraph_node == "model"
    的 on_chat_model_stream 文本进 full_answer（对应外层 SSE token 累积）。

    Args:
        stream_events: graph.astream_events(v2) 的完整事件迭代结果

    Returns:
        (full_answer, leaked_tokens)：full_answer 为外层 agent 节点累积文本；
        leaked_tokens 为事件流中出现泄漏标记的 content 片段（空 = 无泄漏）
    """
    full_answer = ""
    leaked_tokens = []
    for event in stream_events:
        if event.get("event") != "on_chat_model_stream":
            continue
        chunk = (event.get("data") or {}).get("chunk")
        content = getattr(chunk, "content", "") or ""
        if not content:
            continue
        metadata = event.get("metadata") or {}
        if metadata.get("langgraph_node") == "model":
            full_answer += content
        if _LEAK_MARKER in content:
            leaked_tokens.append(content)
    return full_answer, leaked_tokens


@pytest.mark.asyncio
async def test_fork_subagent_events_do_not_leak_to_outer_stream():
    """fork 子代理 LLM 事件不泄漏进外层流（Critical 回归）。

    真实路径：外层图模型节点流式调模型 → ToolNode 执行 delegate_task →
    SkillExecutor._run_fork → 真实 create_agent 子代理调同一 LLM。修复前
    子代理 on_chat_model_stream 经 var_child_runnable_config 传播到外层
    astream_events，token 带 _FORK_ANSWER 内容泄漏进 SSE 并污染累积的 full_answer。

    谓词改为 "model" 后，主循环模型事件与子代理模型事件的节点名**同为 "model"**，
    子代理事件不再被节点名偶然挡住 ⇒ 本用例按 "model" 口径累积 full_answer，泄漏
    一旦发生即污染该累积，成为隔离单点机制（见 _convert_event docstring）的守护。

    注入请求上下文后，子代理原文经 ctx.clarify_channel 走 delegate 通道（可观测）
    仍不进入 full_answer：scope=delegate 只投 delta，不回灌主 token/落库内容。
    """
    model = _ScenarioChatModel()

    graph = StateGraph(MessagesState)
    graph.add_node("model", _make_streaming_agent_node(model))
    graph.add_node("tools", ToolNode([_make_fork_delegate_tool(model)]))
    graph.set_entry_point("model")
    graph.add_conditional_edges(
        "model",
        _route_to_tools_or_end,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "model")
    compiled = graph.compile()

    init_state = cast(
        MessagesState, {"messages": [{"role": "user", "content": "用户主问题"}]}
    )
    # 注入请求上下文：生产路径中 delegate_task 在请求上下文内执行，fork 增量经
    # ctx.clarify_channel 投 delegate delta（contextvar 随 graph 同 task 传播）。
    # ctx 可及 → fork 按 ctx.deep_thinking 新建 llm（get_llm）——
    # 此处 mock 模型构建边界，复用 scenario 假模型（不发起真实网络调用）
    ctx = RequestContext(session_id="s1")
    ctx.delegate_id = "dlg_scope_leak"
    token = current_request_ctx.set(ctx)
    try:
        with patch("src.agents.skills.executor.get_llm", return_value=model):
            stream_events = []
            async for event in compiled.astream_events(init_state, version="v2"):
                stream_events.append(event)
    finally:
        current_request_ctx.reset(token)

    # 隔离是**单点机制**：var_child_runnable_config.set(None) 切断子代理的回调继承
    # + 委派事件经显式喂事件走委派域。它**只能在图/路由层验证**——转换器级无法判别：
    # 只要 fork 的 on_chat_model_stream 真的到达 _convert_event，按当前设计（判据只有
    # 节点名一维 langgraph_node == "model"，与主循环同名）它就**应该**被转成主 token。
    # 故本用例（真实图 + 真实 create_agent fork）即该单点机制的守卫。
    full_answer, leaked_tokens = _collect_fork_leak_events(stream_events)
    # 1. 外层事件流不得出现携带 fork 内容的 chat_model_stream token（无泄漏）
    assert leaked_tokens == []
    # 2. 累积的 full_answer 只含主 agent 整合回答，不含子代理原文（防持久化污染）
    assert _LEAK_MARKER not in full_answer
    assert full_answer == _FINAL_ANSWER
    # 3. 子代理事件经 ctx.clarify_channel 走 delegate 通道：full_answer 仍不含过程原文
    #    （防污染不变量：scope=delegate 永不写主 token/full_answer/落库内容）
    deltas = [it for it in _drain_channel(ctx) if it.get("type") == "delegate"]
    assert deltas, "fork 子代理原文应经 delegate 通道转发，而非空通道"
    delta_text = "".join(
        it.get("delta", "") for it in deltas if it.get("action") == "delta"
    )
    assert all(it.get("delegate_id") == "dlg_scope_leak" for it in deltas)
    assert _LEAK_MARKER in delta_text  # 子代理原文经 delegate 通道以 delta 转发
    assert _LEAK_MARKER not in full_answer  # 但绝不回灌主 full_answer


def _make_streaming_agent_node(model: BaseChatModel):
    """构造流式 agent 节点：astream 聚合模型输出（复刻 repo agent_node 语义）。"""

    async def agent_node(state) -> dict:
        chunks = []
        async for chunk in model.astream(state["messages"]):
            chunks.append(chunk)
        result = chunks[0]
        for chunk in chunks[1:]:
            result = result + chunk
        return {"messages": [result]}

    return agent_node


def _route_to_tools_or_end(state) -> str:
    """条件路由：末条消息带工具调用 → tools；否则结束。"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


@pytest.mark.asyncio
async def test_fork_with_llm_lacking_model_name_does_not_crash():
    """main_llm 无 model_name 属性：构造不抛 AttributeError，_main_model_name 回落
    None，未声明 model 的 fork 仍复用该实例正常执行（B 修复回归）。"""

    class _NoModelNameLLM:
        """无 model_name 属性的极简 llm 替身。"""

    llm = _NoModelNameLLM()
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
        _event("on_chat_model_end", output=AIMessage(content="分析")),
    )

    with (
        patch(
            "src.agents.skills.executor.build_agent", return_value=fake_sub
        ) as mock_build,
        patch("src.agents.skills.executor.get_llm") as mock_get_llm,
    ):
        exe = SkillExecutor(main_llm=llm)
        rec = _record(
            context=SkillContext.FORK,
            fork_body="人格",
            inline_prompt=None,
            model=None,
        )
        out = await exe.execute(rec, task="分析")

    assert exe._main_model_name is None
    mock_get_llm.assert_not_called()
    args, _kwargs = mock_build.call_args
    assert args[0] is llm
    assert "分析" in out


def test_fork_tools_uses_readonly_map(monkeypatch):
    """未声明 allowed-tools 时继承只读面（不再零工具），且非只读工具被挡。"""
    from src.agents.tools import readonly as readonly_module

    monkeypatch.setattr(
        readonly_module, "_TOOL_READONLY", {"retrieve_kb": True, "write_doc": False}
    )
    exe = SkillExecutor(
        main_llm=MagicMock(), tool_provider=lambda: [_retrieve, _write_doc]
    )
    picked = exe._fork_tools(_record(allowed_tools=[]), None)
    assert [t.name for t in picked] == ["retrieve_kb"]


def test_fork_tools_without_provider_warns_when_tools_registered(monkeypatch):
    """工具确实注册过却拿不到 provider → 记 warning（不再静默零工具）。"""
    from src.agents.tools import readonly as readonly_module

    monkeypatch.setattr(readonly_module, "_TOOL_READONLY", {"retrieve_kb": True})
    exe = SkillExecutor(main_llm=MagicMock(), tool_provider=None)
    with pytest.warns(UserWarning, match="tool_provider"):
        assert exe._fork_tools(_record(allowed_tools=[]), None) == []


# ---- 通用委派（record=None，design D6）----


@pytest.mark.asyncio
async def test_generic_execute_none_record_uses_task_as_input():
    """execute(record=None)：不渲染任何 skill 正文，task 直接作子代理初始 user message。"""
    main_llm = MagicMock()
    exe = SkillExecutor(main_llm=main_llm)
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="通用结论")),
        _event("on_chat_model_end", output=AIMessage(content="通用结论")),
    )
    with patch(
        "src.agents.skills.executor.build_agent", return_value=fake_sub
    ) as mock_build:
        out = await exe.execute(None, task="查一下某公司近三年的营收")

    msg = fake_sub.captured["inputs"]["messages"][0]
    assert msg.content == "查一下某公司近三年的营收"
    assert "通用结论" in out
    args, _kwargs = mock_build.call_args
    assert args[0] is main_llm  # 无 ctx 且无 record.model → 复用主 llm


def test_render_fork_task_none_record_returns_task():
    """_render_fork_task(None, task)：无正文可渲染，直接返回 task 原文。"""
    exe = SkillExecutor(main_llm=MagicMock())
    assert exe._render_fork_task(None, "查营收") == "查营收"


def test_fork_tools_none_record_inherits_readonly(monkeypatch):
    """_fork_tools(None, preset)：record 缺失 → allowed=[] 不收窄，仅继承只读面。"""
    from src.agents.tools import readonly as readonly_module

    monkeypatch.setattr(
        readonly_module, "_TOOL_READONLY", {"retrieve_kb": True, "write_doc": False}
    )
    exe = SkillExecutor(
        main_llm=MagicMock(), tool_provider=lambda: [_retrieve, _write_doc]
    )
    picked = exe._fork_tools(None, None)
    assert [t.name for t in picked] == ["retrieve_kb"]


@pytest.mark.asyncio
async def test_generic_execute_none_record_skips_record_model():
    """execute(record=None) 在请求上下文可及时不碰 record.model，按主 model_name 新建 llm。"""
    fake_llm = MagicMock()
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_end", output=AIMessage(content="ok")),
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.build_agent", return_value=fake_sub),
            patch(
                "src.agents.skills.executor.get_llm", return_value=fake_llm
            ) as mock_get_llm,
        ):
            main_llm = MagicMock()
            main_llm.model_name = "main-model"
            exe = SkillExecutor(main_llm=main_llm)
            await exe.execute(None, task="分析")
    finally:
        current_request_ctx.reset(token)
    # ctx deep_thinking 默认 False → thinking=False（非 None，故新建而非复用）；
    # extra_body 形状出自 provider 层
    assert mock_get_llm.call_args.kwargs["model"] == "main-model"
    assert mock_get_llm.call_args.kwargs["extra_body"] == {"enable_thinking": False}
