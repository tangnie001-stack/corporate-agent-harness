"""统一响应处理中间件 — 数据追踪日志。

API 路由 handler 通过 response_model=ResponseModel 自行负责格式包装，
中间件不再读取或修改响应体。
异常由中间件记录日志后 re-raise，由 @app.exception_handler(Exception) 统一格式化。
健康检查和 SSE 流式响应跳过全部处理。
"""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from loguru import logger

from src.core.logging import mask_alert_webhook_path

# 跳过处理的白名单路径
_SKIP_PATHS = {"/api/health", "/api/chat/stream"}


async def response_processor_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """统一响应处理中间件。

    成功响应 → 直接透传，不修改响应体。
    异常 → 只记日志，re-raise 由 @app.exception_handler(Exception) 统一格式化。
    """
    path = request.url.path
    if path in _SKIP_PATHS:
        return await call_next(request)

    try:
        response: Response = await call_next(request)

        # @app.exception_handler 或 Auth 已返回统一格式 → 直接透传
        if response.status_code >= 400:
            return response

        # 非 GET 请求日志 — 只记路径和状态码，不记响应体
        # 路径先过 mask：告警端点把 token 放在 URL 里，凭据不得进日志
        if request.method != "GET":
            logger.info(
                "[API] {} {} | status={}",
                request.method,
                mask_alert_webhook_path(path),
                response.status_code,
            )

        return response

    except Exception as e:
        # BHMW 的 task_group 会截断 traceback，但异常对象链保留了根因
        # 这里只做日志，不处理响应 — 由 ServerErrorMiddleware 转发给
        # @app.exception_handler(Exception) 统一返回 500 JSONResponse
        logger.error(
            "[API] {} {} | 异常: type={} msg={}",
            request.method,
            mask_alert_webhook_path(path),
            type(e).__name__,
            e,
        )
        # 打印异常链根因（穿透 BHMW 截断层）
        c = e
        depth = 0
        while depth < 5:
            nxt = c.__cause__ or c.__context__
            if nxt is None:
                break
            c = nxt
            logger.error(
                "  ├─ 嵌套第{}层: type={} msg={}",
                depth + 1,
                type(c).__name__,
                c,
            )
            depth += 1
        raise
