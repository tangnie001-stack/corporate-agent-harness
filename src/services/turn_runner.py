"""单轮生成编排入口——站点与企微通道共用（design D12）。

从 api/chat.py 的 _stream_rag_response + _run_with_finalize 下沉而来：
产出结构化 SSEEvent 流，站点把它转成 SSE 帧，通道交给投影层。
编排前置（set_chat_repo / 原子闸门 / 落库）与收尾（终态落库 / 释放 / 注销）
内聚在本入口，避免调用方各写一半。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from src.chat.process_log import serialize_process
from src.chat.streaming import (
    StreamingRunManager,
    _subscribe_events,
    streaming_manager,
)
from src.config.const import CHANNEL_DEFAULT, SSEInteractionTexts
from src.infra import retry_policy
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.infra.llm.trace_context import (
    current_channel,
    current_session_id,
    current_trace_id,
)
from src.services.agent_service import _run_generation
from src.services.app_service import AppService
from src.services.chat_lock import acquire_session_lock, release_session_lock
from src.utils.sse import SSEDoneEvent, SSEErrorEvent, SSEEvent


class TurnBusy(Exception):
    """同一会话已有进行中的生成（调用方译为用户可见结果）。"""


@dataclass
class TurnHandle:
    """一轮生成句柄。

    Attributes:
        session_id: 会话 ID（站点与通道共用）
        events: 结构化事件流（终止态由任务生命周期提供）
    """

    session_id: str
    events: AsyncIterator[SSEEvent]


def _error_copy(exc: BaseException) -> str:
    """终态 error 事件给前端的用户可见文案。

    已归类的降级 ⇒ 取取值集合表里的文案；未归类 ⇒ **空串** —— 前端据此回落
    其通用文案（与改造前逐字一致），这样既不会把内部异常文本透给用户
    （改造后前端会消费 `data.error`），也不会对可用性类错误给出"请重试"的误导。

    Args:
        exc: 收尾时捕获的异常

    Returns:
        文案字符串；未归类时为空串
    """
    reason = retry_policy.classify_failure(exc)
    if reason is None:
        return ""
    return SSEInteractionTexts.DEGRADATION_TEXTS.get(reason, "")


async def _run_with_finalize(
    svc: AppService,
    session_id: str,
    kb_id: str,
    partial_holder: dict,
    answer_builder: Callable,
    manager: StreamingRunManager,
    abort_signal: asyncio.Event,
    release_lock: Callable[[], None],
    ctx: RequestContext,
    trace_id: str = "",
    channel: str = "",
) -> None:
    """后台任务主体：跑生成，完成后按结果收尾落库，finally 释放锁并注销。

    后台任务与调用方处于不同 asyncio task，其 contextvars 是 create_task 在
    创建时复制的一份快照；本函数仍在入口显式 set
    current_request_ctx / current_trace_id / current_session_id / current_channel
    （工具与节点经 contextvar 读取 clarify_channel / tool_contexts / 日志格式段等），
    是为不依赖该快照（create_task 之后写入的值到不了任务内，中间件顺序变化
    也会静默丢失），finally 中 reset。
    trace_id 由调用方在 create_task 前从 current_trace_id.get() 捕获并显式
    传入，任务内据此 set contextvar 并写 done 终态事件，保证与请求 trace_id
    一致（单一事实来源）。session_id 直接取 ctx.session_id，供日志 patcher
    注入固定格式段。

    收尾分三支：
    - 正常结束：完整回答落 complete（MySQL）+ 写 Redis 对话历史（供下一轮
      prompt 上下文），写 done 终态事件（含 trace_id）
    - 被取消（abort 触达 task.cancel）：已产出 token 落 interrupted（仅 MySQL），
      写 done(cancelled) 终态事件，随后 re-raise 保持取消语义
    - 异常：已产出 token 落 interrupted（仅 MySQL），写 error 终态事件

    Args:
        svc: AppService 实例（save_assistant_async 落 MySQL；
            chat_manager.add_message_async 写 Redis 对话历史）
        session_id: 会话 ID
        kb_id: 知识库 ID
        partial_holder: 生产者写入的 {"text": 已产出 token, "sources": 引用来源列表}
            共享 dict，取消/出错时据此写 interrupted 部分回答，收尾落库引用来源；
            _run_generation 另挂 events_log（过程事件列表引用）并于收尾写 model_name
        answer_builder: 可调用对象，执行生成并更新 partial_holder["text"]；
            终态落库内容为 process 分拣返回的净化正文（旁白已剔除）
        manager: StreamingRunManager（终态事件写入缓冲）
        abort_signal: 请求级中止信号（由 cancel 端点置位，任务内当前不消费）
        release_lock: per-session 并发锁释放回调（幂等，任务完成时调用）
        ctx: 请求上下文（含 clarify_channel / session_id），任务入口 set 到
            current_request_ctx / current_session_id
        trace_id: 请求级 trace_id（调用方启动任务前捕获），任务入口 set 到
            current_trace_id，done 终态事件据此写入
        channel: 请求级渠道标识（调用方启动任务前从 current_channel 捕获），
            任务入口 set 到 current_channel，供日志第 5 段与 Langfuse metadata 使用；
            空串按 CHANNEL_DEFAULT（none）占位
    """
    task = asyncio.current_task()
    assert task is not None, (
        "_run_with_finalize 须由 create_task 启动（注销需任务引用）"
    )
    ctx_token = current_request_ctx.set(ctx)
    trace_token = current_trace_id.set(trace_id or None)
    session_token = current_session_id.set(ctx.session_id)
    channel_token = current_channel.set(channel or CHANNEL_DEFAULT)
    try:
        await answer_builder()
    except asyncio.CancelledError:
        # 净化正文：分拣剔除旁白后的末轮正文流，替代全量累积 token 落库，
        # 避免历史回放时旁白双重渲染；partial_holder["text"] 同步更新保持一致
        process_json, purified = serialize_process(
            partial_holder.get("events_log") or []
        )
        partial_holder["text"] = purified
        if purified:
            await svc.save_assistant_async(
                session_id,
                kb_id,
                purified,
                partial_holder.get("sources", []),
                "interrupted",
                process_json=process_json,
                model_name=partial_holder.get("model_name", ""),
            )
        manager.add_event(session_id, "done", {"cancelled": True})
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("generation failed: {}", e)
        process_json, purified = serialize_process(
            partial_holder.get("events_log") or []
        )
        partial_holder["text"] = purified
        if purified:
            await svc.save_assistant_async(
                session_id,
                kb_id,
                purified,
                partial_holder.get("sources", []),
                "interrupted",
                process_json=process_json,
                model_name=partial_holder.get("model_name", ""),
            )
        manager.add_event(session_id, "error", {"error": _error_copy(e)})
    else:
        process_json, purified = serialize_process(
            partial_holder.get("events_log") or []
        )
        partial_holder["text"] = purified
        await svc.save_assistant_async(
            session_id,
            kb_id,
            purified,
            partial_holder.get("sources", []),
            "complete",
            process_json=process_json,
            model_name=partial_holder.get("model_name", ""),
        )
        # 净化正文写 Redis 对话历史（get_history_async 供下一轮 prompt 上下文，
        # 与 MySQL 落库内容一致）；取消/异常的部分回答保持仅 MySQL，不写 Redis
        await svc.chat_manager.add_message_async(session_id, "assistant", purified)
        # 跨轮历史摘要：只在**正常完成**分支、且 assistant 已写入历史之后发起。
        # 取消/异常分支不写 Redis assistant（历史末尾会是未作答的 user 问），
        # 以其为输入会把悬空问题当成史实，故那两条路径不触发。
        # `chat_manager.redis` 为 None（无 Redis / 内存降级）时不生成摘要——
        # best-effort 守卫需要 SETNX，此时行为等同于本 change 之前。
        # 整段 best-effort：历史读取或调度构造异常一律吞掉，绝不因此跳过本回合的
        # done 终态事件（与调度模块"绝不影响用户可见流程"一致）。
        try:
            from src.services.summary_scheduler import maybe_schedule_summary

            summary_history = await svc.chat_manager.get_history_async(session_id) or []
            maybe_schedule_summary(
                svc.chat_manager, svc.chat_manager.redis, session_id, summary_history
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("summary schedule skipped: {}", exc)
        manager.add_event(session_id, "done", {"trace_id": trace_id or ""})
    finally:
        current_request_ctx.reset(ctx_token)
        current_trace_id.reset(trace_token)
        current_session_id.reset(session_token)
        current_channel.reset(channel_token)
        release_lock()
        manager.unregister_if_current(session_id, task)


async def _acquire_gate(svc: AppService, session_id: str) -> tuple[bool, object]:
    """原子闸门：进程内同步预留 + Redis SETNX 跨实例兜底。

    先做进程内同步预留（try_reserve）——判定与预留之间无 await 点，是并发
    防护的权威状态（Redis 锁 TTL 可能短于含 ask_user 的一轮生成，锁过期不
    代表生成结束）。预留成功后（预留中 is_running 已返回 True）再尝试 Redis
    锁兜底跨实例并发；Redis 不可用则跳过。

    Args:
        svc: AppService（自 chat_manager 取 Redis 客户端）
        session_id: 会话 ID

    Returns:
        (lock_held, redis)：lock_held 表示是否取得 Redis 锁；redis 为 ChatManager
        的 Redis 客户端（不可用时为 None，此时 lock_held 恒为 False）。未抛
        TurnBusy 即已预留；预留的释放在**任务尚未注册**的早退/失败路径由调用方
        显式调用 streaming_manager.release_reservation()，任务注册后由
        unregister_if_current 负责（见 _make_release_lock_cb docstring）。

    Raises:
        TurnBusy: 预留未获得（已有任务或预留），或 Redis 锁被他人持有
    """
    if not streaming_manager.try_reserve(session_id):
        raise TurnBusy("当前会话正在处理中")

    # per-session 并发锁：Redis 可用时加锁，SETNX 明确返回 False 才视为冲突
    lock_held = False
    redis = svc.chat_manager._redis
    if redis is not None:
        try:
            lock_held = await acquire_session_lock(redis, session_id)
        except Exception as e:  # noqa: BLE001
            # Redis 不可用：跳过锁，不阻塞请求（与 ChatManager 降级策略一致）
            logger.warning("Session lock skipped (Redis unavailable): {}", e)
        else:
            if not lock_held:
                streaming_manager.release_reservation(session_id)
                raise TurnBusy("当前会话正在处理中")
    return lock_held, redis


async def _persist_front(
    svc: AppService,
    session_id: str,
    title: str | None,
    kb_id: str,
    user_id: str,
    query: str,
    redis: object,
    lock_held: bool,
) -> None:
    """落库前置：session（幂等创建）+ user 消息。

    请求开始同步落 user，写入成功后才启动生成；失败时先释放预留与已取的锁
    再原样抛出（避免预留与 session 锁挂到 TTL）。

    Raises:
        Exception: 落库失败（编程错误等非吞掉路径），释放后原样抛出
    """
    try:
        if title is not None:
            session_title = title
        else:
            session_title = query[:20]
        await svc.save_session_async(session_id, session_title, kb_id, user_id)
        await svc.save_user_async(session_id, kb_id, query)
    except Exception:
        streaming_manager.release_reservation(session_id)
        if lock_held:
            await release_session_lock(redis, session_id)
        raise
    logger.info("user message persisted at request start: session_id={}", session_id)


def _make_release_lock_cb(
    redis: object, session_id: str, lock_held: bool
) -> Callable[[], None]:
    """构造幂等的同步释放回调：**仅**释放 per-session Redis 锁。

    Redis 锁释放异步调度（异常只记日志，锁有 TTL 兜底）。回调由
    _run_with_finalize.finally 与 task done_callback 双路径调用，nonlocal
    lock_held 保证 Redis 锁只释放一次。

    **不在此释放进程内预留**：任务注册（register）已把预留换成任务引用，
    任务生命周期结束由 unregister_if_current 负责清理。而本回调也是任务的
    done_callback，经 call_soon 要等下一轮事件循环才执行；其间同 session 的
    新回合可能已 try_reserve 成功，此时释放预留会清掉新回合的预留，令
    is_running 变 False、后续并发回合通过闸门并双开同一 session（R14）。
    预留释放只发生在任务尚未注册的早退/失败路径——由调用方在 release_lock_cb()
    之外显式调用 streaming_manager.release_reservation()。
    """

    async def _release_lock_async() -> None:
        """异步释放 per-session 并发锁（异常只记日志，锁有 TTL 兜底）。"""
        try:
            await release_session_lock(redis, session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Session lock release failed: {}", e)

    def release_lock_cb() -> None:
        """同步释放回调：仅调度异步释放 Redis 锁（幂等）。

        锁由后台任务持有到完成——SSE 断连不提前释放（进程内注册表 is_running
        才是并发防护的权威状态）。
        """
        nonlocal lock_held
        if not lock_held:
            return
        lock_held = False
        asyncio.create_task(_release_lock_async())

    return release_lock_cb


def _make_answer_builder(
    launch_ctx: dict,
    ctx: RequestContext,
    partial_holder: dict,
    signal: asyncio.Event,
    user_id: str,
) -> Callable[[], Awaitable[str]]:
    """构造后台任务的生成协程，捕获请求作用域的值（trace_id / user_id）。

    trace_id 与 user_id 均在请求作用域内捕获后显式传入，不依赖后台任务通过
    contextvar 继承读取：任务的上下文是 create_task 创建时的拷贝，该时点之后
    写入的值到不了任务内，依赖继承只会静默取空。
    """

    async def answer_builder() -> str:
        # 根 observation 的 id 即 trace id；任务入口已 set 过 current_trace_id，
        # 此处按调用时读取并显式传入，不依赖任务上下文的那份拷贝。
        return await _run_generation(
            launch_ctx["session_id"],
            launch_ctx["kb_id"],
            launch_ctx["query"],
            launch_ctx["history"],
            launch_ctx["deep_thinking"],
            ctx,
            streaming_manager,
            graph=launch_ctx["graph"],
            partial_holder=partial_holder,
            abort_signal=signal,
            direct_skill=launch_ctx["direct_skill"],
            user_id=user_id,
            summary=launch_ctx.get("summary", ""),
            # @observe 包装器在调用前取走 langfuse_observation_id（静态签名看不到），
            # 类型检查无法感知该 kwarg
            langfuse_observation_id=current_trace_id.get() or "",  # type: ignore[reportCallIssue]
        )

    return answer_builder


async def _error_events(error_text: str, trace_id: str) -> AsyncIterator[SSEEvent]:
    """任务未启动时的终止态事件流：error + done（不抛，保调用方 200+流）。"""
    yield SSEErrorEvent(error_text)
    yield SSEDoneEvent(trace_id=trace_id)


def _spawn_generation_task(
    svc: AppService,
    launch_ctx: dict,
    partial_holder: dict,
    answer_builder: Callable[[], Awaitable[str]],
    signal: asyncio.Event,
    release_lock_cb: Callable[[], None],
    ctx: RequestContext,
) -> None:
    """起后台生成任务，注册进事件缓冲表并挂两条 done 回调。

    两条 done 回调分别释放 per-session 锁与注销注册表（与后台任务 finally
    双路径，靠回调幂等去重）。trace_id 在 create_task 前显式捕获传入。
    """
    task = asyncio.create_task(
        _run_with_finalize(
            svc,
            launch_ctx["session_id"],
            launch_ctx["kb_id"],
            partial_holder,
            answer_builder,
            streaming_manager,
            signal,
            release_lock_cb,
            ctx,
            trace_id=current_trace_id.get() or "",
            channel=current_channel.get() or "",
        )
    )
    streaming_manager.register(launch_ctx["session_id"], task, signal)
    task.add_done_callback(lambda _t: release_lock_cb())
    task.add_done_callback(
        lambda _t: streaming_manager.unregister_if_current(
            launch_ctx["session_id"], task
        )
    )


def _spawn_from_launch_ctx(
    svc: AppService,
    launch_ctx: dict,
    abort_signal: asyncio.Event | None,
    user_id: str,
    release_lock_cb: Callable[[], None],
) -> None:
    """stream_chat 之后的建任务段：接线 abort 信号、构造 answer_builder、起后台任务。

    失败（如 launch_ctx 缺 ctx、create_task/register 抛异常）由调用方捕获并
    转为终止态句柄；本函数不吞异常。user_id 以参数喂生成（与落库同源），
    不依赖 contextvar。

    Args:
        svc: AppService
        launch_ctx: stream_chat 返回的启动上下文（含 ctx / session_id / kb_id 等）
        abort_signal: 调用方取消信号（None 时内部新建）
        user_id: 用户 ID（生成侧唯一来源）
        release_lock_cb: 幂等释放回调（仅 Redis 锁；预留由调用方在早退路径
            显式经 streaming_manager.release_reservation 释放）
    """
    partial_holder: dict = {"text": "", "sources": []}
    if abort_signal is not None:
        signal = abort_signal
    else:
        signal = asyncio.Event()
    ctx = launch_ctx["ctx"]
    # 将 cancel 端点置位的 abort_signal 接到请求上下文：ask_user 的
    # wait_with_abort_and_timeout 等待的是 ctx.abort_signal，不接线则取消
    # 唤不醒澄清等待，会干等 ASK_USER_TIMEOUT 超时
    ctx.abort_signal = signal

    answer_builder = _make_answer_builder(
        launch_ctx, ctx, partial_holder, signal, user_id
    )
    _spawn_generation_task(
        svc, launch_ctx, partial_holder, answer_builder, signal, release_lock_cb, ctx
    )


async def _launch_and_register(
    svc: AppService,
    *,
    session_id: str,
    kb_id: str,
    query: str,
    deep_thinking: bool,
    agent: str,
    user_id: str,
    abort_signal: asyncio.Event | None,
    redis: object,
    lock_held: bool,
    clarify: bool,
) -> TurnHandle:
    """取订阅与启动上下文，起后台任务并注册，返回事件流句柄。

    stream_chat 调用本身失败不抛：释放预留与锁后返回产出 error + done 的
    终止态句柄（整体抛出会让站点从 200+SSE 变 500）。stream_chat 之后的
    建任务/注册段同样有失败边界（见 _spawn_from_launch_ctx 调用处）。

    Args:
        svc: AppService
        session_id: 会话 ID
        kb_id: 知识库 ID
        query: 用户文本
        deep_thinking: 深度思考开关
        agent: 智能体预设名
        user_id: 用户 ID（生成侧唯一来源，与落库同源）
        abort_signal: 取消信号（None 时内部新建）
        redis: ChatManager 的 Redis 客户端（释放锁用）
        lock_held: 是否已取得 Redis 锁
        clarify: 是否允许澄清追问（False 用于报警等非交互轮次，透传给 stream_chat）

    Returns:
        TurnHandle：其 events 为结构化 SSEEvent 流
    """
    release_lock_cb = _make_release_lock_cb(redis, session_id, lock_held)

    try:
        _subscription, launch_ctx = await svc.agent_service.stream_chat(
            kb_id, session_id, query, deep_thinking, agent=agent, clarify=clarify
        )
    except Exception as e:  # noqa: BLE001
        # 任务未启动，无后台任务可释放，本路径直接释放预留 + 锁避免挂到 TTL；
        # 不抛出——改为 error + done 终止态
        logger.exception("Chat stream setup failed: {}", str(e))
        streaming_manager.release_reservation(session_id)
        release_lock_cb()
        return TurnHandle(
            session_id=session_id,
            events=_error_events(str(e), current_trace_id.get() or ""),
        )

    try:
        _spawn_from_launch_ctx(svc, launch_ctx, abort_signal, user_id, release_lock_cb)
    except Exception as e:  # noqa: BLE001
        # 建任务/注册段失败（任务尚未注册）：释放预留 + 锁，不抛出——改为
        # error + done 终止态
        logger.exception("Chat task spawn failed: {}", str(e))
        streaming_manager.release_reservation(session_id)
        release_lock_cb()
        return TurnHandle(
            session_id=session_id,
            events=_error_events(str(e), current_trace_id.get() or ""),
        )

    events = _subscribe_events(
        launch_ctx["session_id"], streaming_manager, max_idle=None
    )
    return TurnHandle(session_id=launch_ctx["session_id"], events=events)


async def start_turn(
    svc: AppService,
    *,
    session_id: str,
    kb_id: str,
    query: str,
    user_id: str = "",
    deep_thinking: bool = False,
    agent: str = "",
    title: str | None = None,
    clarify: bool = True,
    abort_signal: asyncio.Event | None = None,
) -> TurnHandle:
    """启动一轮生成，返回事件流句柄（站点与通道共用）。

    编排步骤（design D12）：注入 chat_repo → 原子闸门（进程内同步预留 +
    Redis 锁兜底）→ 落库前置 → 取订阅起后台任务并注册 → 返回句柄；各步细节
    见对应私有辅助函数 docstring。user_id 以参数为唯一来源：既用于落库前置，
    也经 _launch_and_register 喂生成，通道侧无需另设 contextvar。

    Args:
        svc: AppService
        session_id: 会话 ID
        kb_id: 知识库 ID（空串表示不检索）
        query: 用户文本
        user_id: 用户 ID（企微侧为派生 UUID，站点为登录用户；落库与生成同源）
        deep_thinking: 深度思考开关
        agent: 智能体预设名
        title: 会话标题（None → query[:20]；仅首次落库生效）
        clarify: 是否允许澄清追问（False 用于报警等非交互轮次，见 design D12）
        abort_signal: 取消信号（None 时内部新建）

    Returns:
        TurnHandle：其 events 为结构化 SSEEvent 流

    Raises:
        TurnBusy: 同一会话已有进行中的生成（闸门未获得）
        Exception: 落库前置失败等编排前置错误（与站点现值一致）
    """
    # 注入 chat_repo 必须先于落库前置（漏则 save_* 静默跳过）
    await svc.set_chat_repo()
    lock_held, redis = await _acquire_gate(svc, session_id)
    await _persist_front(
        svc, session_id, title, kb_id, user_id, query, redis, lock_held
    )
    return await _launch_and_register(
        svc,
        session_id=session_id,
        kb_id=kb_id,
        query=query,
        deep_thinking=deep_thinking,
        agent=agent,
        user_id=user_id,
        abort_signal=abort_signal,
        redis=redis,
        lock_held=lock_held,
        clarify=clarify,
    )
