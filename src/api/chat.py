"""流式聊天 SSE 端点 — 支持分阶段状态推送。

端点只做参数提取、闸门结果译码与 SSE 帧化；单轮生成的编排
（落库前置 / 原子闸门 / 后台任务 / 收尾）已下沉到 services，见 design D12。
"""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from src.api.dependencies import get_app_service
from src.api.model.request import ChatStreamRequest
from src.config.const import Channel
from src.infra.llm.trace_context import current_channel
from src.services import turn_runner
from src.services.app_service import AppService
from src.utils.sse import SSEDoneEvent, SSEErrorEvent, to_sse

router = APIRouter()


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: ChatStreamRequest,
    svc: AppService = Depends(get_app_service),
):
    """流式 RAG 问答端点 — 返回 SSE 事件流（编排已下沉 services，见 D12）。

    Args:
        body: 流式问答请求体（含 session_id / kb_id / query / deep_thinking）
        svc: AppService 实例（通过 FastAPI Depends 注入）
        request: FastAPI 请求对象（从中提取 user_id 用于会话归属）

    Returns:
        StreamingResponse: SSE 流式响应，包含
        status / token / citation / error / done 事件

    Raises:
        HTTPException 422: 参数校验失败（FastAPI 自动处理）
        HTTPException 409: 同一会话已有进行中的请求（进程内注册表或 Redis 并发锁冲突）
    """
    session_id = body.session_id
    kb_id = body.kb_id
    query = body.query
    deep_thinking = body.deep_thinking
    agent = body.agent
    user_id = getattr(request.state, "user_id", "") if request else ""
    trace_id = getattr(request.state, "trace_id", "") if request else ""
    # 渠道标识：HTTP 入口恒为站点（web），供日志第 5 段与 Langfuse metadata 读取。
    # 在 start_turn 之前设置，使落库前置 / 闸门等同请求域日志也带渠道。
    current_channel.set(Channel.WEB)

    try:
        handle = await turn_runner.start_turn(
            svc,
            session_id=session_id,
            kb_id=kb_id,
            query=query,
            user_id=user_id,
            deep_thinking=deep_thinking,
            agent=agent,
        )
    except turn_runner.TurnBusy:
        raise HTTPException(409, "当前会话正在处理中")

    async def _frames() -> AsyncGenerator[str, None]:
        """把结构化事件流帧化为 SSE；订阅迭代异常时补终止态 error+done。

        订阅迭代可能因畸形载荷等在 from_payload 处抛异常，此处兜底为
        error + done 终止帧，避免连接中断且无终态（保持站点外部行为不变）。
        """
        try:
            async for event in handle.events:
                yield to_sse(event)
        except Exception as e:  # noqa: BLE001
            logger.exception("Chat stream unhandled error: {}", str(e))
            # 兜底 error 帧的文案走唯一策略 safe_error_copy：未归类 ⇒ 空串，
            # 前端回落通用文案；不得直接透出原始异常文本（前端消费 data.error）
            yield to_sse(SSEErrorEvent(turn_runner.safe_error_copy(e)))
            yield to_sse(SSEDoneEvent(trace_id=trace_id))

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证 SSE 实时推送
        },
    )
