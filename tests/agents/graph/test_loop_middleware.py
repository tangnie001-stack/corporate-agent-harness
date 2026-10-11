"""agent 循环四个 middleware 的单元测试（Task 3/4/5/6）。"""

import asyncio
from typing import ClassVar

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from src.agents.graph.agent_factory import build_agent
from src.agents.graph.middleware import (
    AgentSpanMiddleware,
    AgentTurnBudget,
    ModelParamsMiddleware,
    SystemMessagesMiddleware,
)
from src.config import settings
from src.core.log_events import Event


def _capture_events(monkeypatch) -> list[dict]:
    """拦截 log_event 收集事件（日志走 loguru，caplog 抓不到）。"""
    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.core.logging.log_event", fake_log_event)
    return calls


class _RecordingModel(GenericFakeChatModel):
    """记录每次调用实收消息与 kwargs 的假模型。"""

    seen: ClassVar[list] = []
    seen_kwargs: ClassVar[list] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _RecordingModel.seen.append(list(messages))
        _RecordingModel.seen_kwargs.append(dict(kwargs))
        return super()._generate(messages, stop, run_manager, **kwargs)


def _build_recording_agent(*system_contents):
    _RecordingModel.seen = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware()],
    )
    return agent


def _sysmsgs(*contents):
    return [SystemMessage(content=c) for c in contents]


@pytest.mark.asyncio
async def test_unbound_kb_sends_two_system_messages_in_order():
    agent = _build_recording_agent()
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "_system_messages": _sysmsgs("FIRST", "SECOND-UNBOUND"),
        }
    )
    sent = _RecordingModel.seen[-1]
    assert [type(m).__name__ for m in sent[:3]] == [
        "SystemMessage",
        "SystemMessage",
        "HumanMessage",
    ]
    assert sent[0].content == "FIRST"
    assert sent[1].content == "SECOND-UNBOUND"


@pytest.mark.asyncio
async def test_bound_kb_sends_single_system_message():
    agent = _build_recording_agent()
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "_system_messages": _sysmsgs("FIRST"),
        }
    )
    sent = _RecordingModel.seen[-1]
    assert [type(m).__name__ for m in sent[:2]] == ["SystemMessage", "HumanMessage"]
    assert sent[0].content == "FIRST"


@pytest.mark.asyncio
async def test_bound_kb_omits_temperature_key():
    """绑 KB 档：不带 temperature 键（沿用模型构造温度）。"""
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    ctx = RequestContext(session_id="s", kb_id="kb-1", kb_bound=True)
    token = current_request_ctx.set(ctx)
    try:
        _RecordingModel.seen_kwargs = []
        model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
        agent = build_agent(
            model, tools=[], system="S", middleware_extra=[ModelParamsMiddleware()]
        )
        await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": "kb-1"})
        kw = _RecordingModel.seen_kwargs[-1]
        assert "temperature" not in kw
        assert kw["extra_body"] == {"enable_thinking": False}
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_unbound_kb_passes_explicit_temperature():
    _RecordingModel.seen_kwargs = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model, tools=[], system="S", middleware_extra=[ModelParamsMiddleware()]
    )
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": ""})
    kw = _RecordingModel.seen_kwargs[-1]
    assert kw["temperature"] == settings.NON_KB_MAIN_TEMPERATURE


@pytest.mark.asyncio
async def test_deep_thinking_switch_comes_from_seeded_state():
    """思考开关取 seeded 的 deep_thinking（漏 seed 会静默关闭）。

    deep_thinking=True 档的请求体是**完整** dict——经 `thinking_extra_body` 产出，
    除 enable_thinking 外还必须含 preserve_thinking（D8，见下）。
    """
    _RecordingModel.seen_kwargs = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model, tools=[], system="S", middleware_extra=[ModelParamsMiddleware()]
    )
    await agent.ainvoke(
        {"messages": [HumanMessage(content="hi")], "kb_id": "", "deep_thinking": True}
    )
    assert _RecordingModel.seen_kwargs[-1]["extra_body"] == {
        "enable_thinking": True,
        "preserve_thinking": True,
    }


def test_main_link_extra_body_includes_preserve_on_deep_thinking(monkeypatch):
    """D8 显式修复：主链路 deep_thinking 档 extra_body 必须**完整**含 preserve_thinking。

    `_settings` 产出的 `model_settings.extra_body` 在 langchain-openai 中会**整体覆盖**
    构造期 extra_body（浅合并），故必须与 `thinking_extra_body` 同源产出完整 dict；
    若退化为只含 `{"enable_thinking": …}`，请求期会丢掉构造期的 preserve_thinking。
    """
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory

    monkeypatch.setattr(factory, "_REGISTRY", None)
    from src.agents.graph.middleware import ModelParamsMiddleware

    mw = ModelParamsMiddleware()

    class _Req:
        def __init__(self) -> None:
            self.state = {"kb_id": "", "deep_thinking": True}
            self.model_settings = None

    settings_map = mw._settings(_Req())  # type: ignore[arg-type]
    assert settings_map["extra_body"] == {
        "enable_thinking": True,
        "preserve_thinking": True,
    }


@tool
def echo(x: str) -> str:
    """echo。"""
    return x


@tool
def delegate_task(skill: str) -> str:
    """委派。"""
    return "delegated"


class _LoopingModel(GenericFakeChatModel):
    """前 tool_rounds 次调用发工具调用，之后正常收尾；记录调用次数。"""

    tool_rounds: int = 0
    tool_name: str = "echo"
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _LoopingModel.calls += 1
        if _LoopingModel.calls <= _LoopingModel.tool_rounds:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": _LoopingModel.tool_name,
                        "args": {"x": "1", "skill": "s"},
                        "id": f"c{_LoopingModel.calls}",
                    }
                ],
            )
        else:
            msg = AIMessage(content="ANSWER")
        return ChatResult(generations=[ChatGeneration(message=msg)])


class _ScriptedModel(GenericFakeChatModel):
    """按轮次脚本产出工具调用；记录调用次数。

    `script` 第 i 项 = 第 i+1 轮的 `(工具名, 实参)`；`None` = 该轮正常收尾。
    用于构造「只在某一轮声明委派」这类 `_LoopingModel` 表达不了的时间线。
    """

    script: ClassVar[list] = []
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _ScriptedModel.calls += 1
        index = _ScriptedModel.calls - 1
        if index < len(_ScriptedModel.script):
            entry = _ScriptedModel.script[index]
        else:
            entry = None
        if entry is None:
            msg = AIMessage(content="ANSWER")
        else:
            name, args = entry
            msg = AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": args, "id": f"c{_ScriptedModel.calls}"}
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.mark.asyncio
async def test_hit_limit_skips_last_tool_and_keeps_tool_call_message():
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "echo"
    executed = []

    @tool("echo")
    def _echo(x: str) -> str:
        """echo。"""
        executed.append(x)
        return x

    model = _LoopingModel(messages=iter([]))
    agent = build_agent(
        model,
        tools=[_echo],
        system="S",
        max_turns=3,
        middleware_extra=[AgentTurnBudget(limit=3)],
    )
    out = await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert _LoopingModel.calls == 3  # 模型调用 = 上限次
    assert len(executed) == 2  # 工具执行 = 上限 − 1
    last = out["messages"][-1]
    assert isinstance(last, AIMessage) and last.tool_calls  # 末条含 tool_calls
    assert (last.content or "") == ""  # 允许空串


@pytest.mark.asyncio
async def test_limit_hit_on_normal_finish_still_logs(monkeypatch):
    """上限轮恰好正常收尾（无 tool_calls）也产出 iteration limit。"""
    events = _capture_events(monkeypatch)
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 1
    _LoopingModel.tool_name = "echo"
    model = _LoopingModel(messages=iter([]))
    agent = build_agent(
        model,
        tools=[echo],
        system="S",
        max_turns=2,
        middleware_extra=[AgentTurnBudget(limit=2)],
    )
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert any(c["event"] is Event.ITERATION_LIMIT for c in events)


@pytest.mark.asyncio
async def test_delegate_declared_on_limit_round_is_executed():
    """上限轮（第 5 轮）**才**声明的委派工具必须被执行，不能被判超限跳过。

    前 4 轮声明普通工具（让循环继续），第 5 轮声明 delegate_task。判别性：
    若去掉「本轮声明即置位」逻辑（`delegate_used` 只看上一轮状态），第 5 轮
    `eff=5` 会 `jump_to=end`，委派工具永不执行 ⇒ 本测试失败。
    """
    _ScriptedModel.calls = 0
    _ScriptedModel.script = [
        ("echo", {"x": "1"}),
        ("echo", {"x": "1"}),
        ("echo", {"x": "1"}),
        ("echo", {"x": "1"}),
        ("delegate_task", {"skill": "s"}),
        None,
    ]
    ran = []

    @tool("delegate_task")
    def _delegate(skill: str) -> str:
        """委派。"""
        ran.append(skill)
        return "ok"

    model = _ScriptedModel(messages=iter([]))
    agent = build_agent(model, tools=[echo, _delegate], system="S", max_turns=5)
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert ran == ["s"], "上限轮（第 5 轮）声明的委派工具必须被执行"


@pytest.mark.asyncio
async def test_delegate_bonus_does_not_fall_back_after_first_declaration():
    """第 1 轮声明委派、此后不再声明 ⇒ 放宽上限**不回落**（仍为 limit + bonus）。

    判别性：若去掉跨轮持久标志（`delegate_used` 只看本轮声明），第 2 轮
    `eff=2` 会 `jump_to=end` ⇒ 模型调用次数为 2 而非 4。
    """
    _ScriptedModel.calls = 0
    _ScriptedModel.script = [
        ("delegate_task", {"skill": "s"}),
        ("echo", {"x": "1"}),
        ("echo", {"x": "1"}),
        ("echo", {"x": "1"}),
        None,
    ]

    @tool("delegate_task")
    def _delegate(skill: str) -> str:
        """委派。"""
        return "ok"

    model = _ScriptedModel(messages=iter([]))
    agent = build_agent(model, tools=[echo, _delegate], system="S", max_turns=2)
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert _ScriptedModel.calls == 4  # 2（基础）+ 2（委派放宽余量），未回落到 2


def test_build_agent_requires_span_middleware_last():
    """Ruling U：AgentSpanMiddleware 必须在最后（最内层），否则装配期抛 ValueError。"""

    def _build(middleware_extra):
        return build_agent(
            _RecordingModel(messages=iter([AIMessage(content="ok")])),
            tools=[],
            system="S",
            max_turns=3,
            middleware_extra=middleware_extra,
        )

    # 正例：AgentSpan 在最后 → 可装配
    _build([SystemMessagesMiddleware(), ModelParamsMiddleware(), AgentSpanMiddleware()])
    # 反例：AgentSpan 不在最后 → ValueError（错误信息指明原因）
    with pytest.raises(ValueError, match="AgentSpanMiddleware"):
        _build([AgentSpanMiddleware(), ModelParamsMiddleware()])


@pytest.mark.asyncio
async def test_budget_instance_shared_across_requests_does_not_bleed():
    """同一个 middleware 实例被两个并发请求使用，计数互不污染。"""
    mw = AgentTurnBudget(limit=3)
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "echo"
    model = _LoopingModel(messages=iter([]))
    agent = build_agent(
        model, tools=[echo], system="S", max_turns=3, middleware_extra=[mw]
    )

    async def one(i):
        return await agent.ainvoke(
            {"messages": [HumanMessage(content=f"q{i}")], "query": f"q{i}"}
        )

    r1, r2 = await asyncio.gather(one(1), one(2))
    for r in (r1, r2):
        assert r["_turn_count"] == 3  # 各自独立从 0 起


@pytest.mark.asyncio
async def test_build_agent_max_turns_alone_enforces_budget():
    """Ruling O：只传 max_turns（不传预算 middleware）也必须生效。

    否则是「静默无回合上限」——评审实测的失败模式。
    """
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "echo"
    model = _LoopingModel(messages=iter([]))
    agent = build_agent(model, tools=[echo], system="S", max_turns=3)
    out = await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert _LoopingModel.calls == 3
    assert out["_turn_count"] == 3


@pytest.mark.asyncio
async def test_iteration_done_counts_full_messages_including_system(monkeypatch):
    """msgs 计的仍是含 system 段的完整条数 = len(request.messages) + 1。"""
    events = _capture_events(monkeypatch)
    _RecordingModel.seen_kwargs = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "_system_messages": _sysmsgs("S1", "S2"),
        }
    )
    done = [c for c in events if c["event"] is Event.ITERATION_DONE]
    assert done and done[0]["iteration"] == 1
    assert done[0]["msgs"] == 3  # S1(system_message) + S2 + user


@pytest.mark.asyncio
async def test_model_turn_reports_effective_temperature(monkeypatch):
    """温度上报取实际施加的档位（最内层能看到 model_settings）。"""
    events = _capture_events(monkeypatch)
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system="S",
        middleware_extra=[ModelParamsMiddleware(), AgentSpanMiddleware()],
    )
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": ""})
    turn = [c for c in events if c["event"] is Event.MODEL_TURN]
    assert turn and turn[0]["temp_source"] == "explicit"
    assert turn[0]["temperature"] == settings.NON_KB_MAIN_TEMPERATURE


@pytest.mark.asyncio
async def test_agent_turn_writes_imperative_generation(monkeypatch):
    """观测走命令式 generation span（不改写 chat_turn observation）。

    实证：middleware 里 `get_current_observation_id()` 指向外层 chat_turn span，
    `update_current_observation` 会改写它而非新建 generation 观察，故回退命令式。
    """
    recorded: list[tuple[str, dict]] = []

    class _FakeGeneration:
        def end(self, **kwargs):
            recorded.append(("end", kwargs))

    class _FakeClient:
        def generation(self, **kwargs):
            recorded.append(("generation", kwargs))
            return _FakeGeneration()

    class _FakeCtx:
        client_instance = _FakeClient()

        @staticmethod
        def get_current_trace_id():
            return "trace-x"

        @staticmethod
        def get_current_observation_id():
            return "obs-parent"

    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", True)
    monkeypatch.setattr("src.agents.graph.middleware.langfuse_context", _FakeCtx)
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model, tools=[], system="S", middleware_extra=[AgentSpanMiddleware()]
    )
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": "kb-1"})
    generation = [c for c in recorded if c[0] == "generation"]
    assert len(generation) == 1
    _, kwargs = generation[0]
    assert kwargs["name"] == "agent_turn"
    assert kwargs["trace_id"] == "trace-x"
    assert kwargs["parent_observation_id"] == "obs-parent"
    assert kwargs["usage"]["input"] >= 1


def _patch_langfuse(monkeypatch) -> list[dict]:
    """把 langfuse context 换成记录式 fake，返回收到的 generation kwargs 列表。"""
    recorded: list[dict] = []

    class _FakeGeneration:
        def end(self, **kwargs):
            pass

    class _FakeClient:
        def generation(self, **kwargs):
            recorded.append(kwargs)
            return _FakeGeneration()

    class _FakeCtx:
        client_instance = _FakeClient()

        @staticmethod
        def get_current_trace_id():
            return "trace-x"

        @staticmethod
        def get_current_observation_id():
            return "obs-parent"

    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", True)
    monkeypatch.setattr("src.agents.graph.middleware.langfuse_context", _FakeCtx)
    return recorded


@pytest.mark.asyncio
async def test_agent_turn_input_includes_system_message(monkeypatch):
    """观测 input 必须是"本轮实收集合"：system 段在前，其后才是 request.messages。

    回归守卫：`SystemMessagesMiddleware` 用 `override(system_message=…)` 施加 system，
    而 `ModelRequest.messages` 明确**不含** system（该字段单独存放）。观测若只取
    `request.messages`，system 段会从 trace 里彻底消失（已在真实 trace 上复现：
    改动前 iter1 是 `[system, user]`，改动后变成 `[user]`）。
    """
    recorded = _patch_langfuse(monkeypatch)
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "kb_id": "kb-1",
            "_system_messages": _sysmsgs("SYS-ONE", "SYS-TWO"),
        }
    )
    assert len(recorded) == 1
    payload = recorded[0]["input"]
    assert [m["role"] for m in payload] == ["system", "system", "user"]
    assert payload[0]["content"] == "SYS-ONE"
    assert payload[1]["content"] == "SYS-TWO"
    assert payload[2]["content"] == "hi"


@pytest.mark.asyncio
async def test_agent_turn_usage_estimate_includes_system(monkeypatch):
    """用量估算的输入同样含 system 段（漏掉会让每轮估算系统性偏小）。

    期望值由 `count_messages_tokens` 按**含 system 的完整消息列表**现算（与
    `estimate_usage` 同源），故与环境无关：encoder 可用走分词器、不可用走降级，
    两边一致。钉住的语义：期望按三条消息（含两条 system）算；若实现退化成只算
    `request.messages`（丢 system），期望仍是三条、实际变成一条的值，断言照样失败。
    """
    from src.infra.llm.token_count import count_messages_tokens

    recorded = _patch_langfuse(monkeypatch)
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "kb_id": "kb-1",
            "_system_messages": _sysmsgs("SYS-ONE", "SYS-TWO"),
        }
    )
    expected = count_messages_tokens(
        [
            SystemMessage(content="SYS-ONE"),
            SystemMessage(content="SYS-TWO"),
            HumanMessage(content="hi"),
        ]
    )
    assert recorded[0]["usage"]["input"] == expected


@pytest.mark.asyncio
async def test_turn_input_budget_logs_when_over_limit(monkeypatch):
    """轮内输入 token 达上限 → 记 context budget high（含 used/limit）。"""
    events = _capture_events(monkeypatch)
    monkeypatch.setattr(
        "src.agents.graph.middleware.TURN_INPUT_TOKEN_LIMIT", 1, raising=True
    )
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "kb_id": "kb-1",
            "_system_messages": _sysmsgs("SYS-ONE", "SYS-TWO"),
        }
    )
    budget_events = [e for e in events if e["event"] == Event.CONTEXT_BUDGET_HIGH]
    assert len(budget_events) == 1
    assert budget_events[0]["limit"] == 1
    assert budget_events[0]["used"] > 1


@pytest.mark.asyncio
async def test_turn_input_budget_silent_when_under_limit(monkeypatch):
    """未达上限时不记事件（避免日志噪音）。"""
    events = _capture_events(monkeypatch)
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": "kb-1"})
    assert [e for e in events if e["event"] == Event.CONTEXT_BUDGET_HIGH] == []


@pytest.mark.asyncio
async def test_turn_input_measure_failure_degrades(monkeypatch):
    """度量抛异常时降级：请求照常完成，不产生该事件。"""
    events = _capture_events(monkeypatch)

    def _boom(_messages):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.agents.graph.middleware.count_messages_tokens", _boom)
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="hi")], "kb_id": "kb-1"}
    )
    assert result["messages"][-1].content == "ok"
    assert [e for e in events if e["event"] == Event.CONTEXT_BUDGET_HIGH] == []
