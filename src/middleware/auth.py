"""认证中间件 — 从 Cookie 中读取 token 验证身份。

优先校验 token Cookie（登录用户），
fallback 为 user_id Cookie（匿名用户，仅用于 chat/sessions）。
错误路径通过 return JSONResponse 返回（不 raise 异常，避免 BaseHTTPMiddleware 二次 raise 问题）。
"""

import uuid as uuid_mod
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from src.config.response_codes import Code
from src.infra.auth.user_auth import UserAuth
from src.infra.llm.trace_context import current_user_id
from src.infra.redis_client import get_redis_client


async def auth_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path

    # 认证端点无需鉴权
    if path.startswith("/api/auth/") or path == "/api/health":
        return await call_next(request)

    # 知识库与诊断详情端点：必须登录（诊断报告含用户原文摘要，禁止免登链接）
    if path.startswith(("/api/kbs", "/api/rca")):
        token = request.cookies.get("token")
        if not token:
            return JSONResponse(
                {
                    "code": Code.AUTH_REQUIRED,
                    "message": Code.AUTH_REQUIRED_MSG,
                    "data": None,
                },
                status_code=401,
            )
        uid = await UserAuth.get_user_id_from_token_async(get_redis_client(), token)
        if not uid:
            return JSONResponse(
                {
                    "code": Code.AUTH_TOKEN_EXPIRED,
                    "message": Code.AUTH_TOKEN_EXPIRED_MSG,
                    "data": None,
                },
                status_code=401,
            )
        request.state.user_id = uid
        current_user_id.set(uid)
        return await call_next(request)

    # Chat / Sessions：优先 token，fallback 匿名 user_id
    if path.startswith(("/api/chat/", "/api/sessions/")):
        token = request.cookies.get("token")
        if token:
            uid = await UserAuth.get_user_id_from_token_async(get_redis_client(), token)
            if uid:
                request.state.user_id = uid
                current_user_id.set(uid)
                return await call_next(request)
        uid = request.cookies.get("user_id")
        if not uid:
            uid = str(uuid_mod.uuid4())
        request.state.user_id = uid
        current_user_id.set(uid)
        resp: Response = await call_next(request)
        if not request.cookies.get("user_id"):
            resp.set_cookie("user_id", uid, max_age=31536000, path="/", samesite="lax")
        return resp

    return await call_next(request)
