"""告警接收路由（POST /api/alerts/webhook）。

极薄：只读请求头 token 与原始 body，其余全部委托 `alert_service`。
不声明 Pydantic 请求体 —— 避免框架校验把 400 改写成 422/500 统一信封。
"""

from fastapi import APIRouter, Request
from starlette.responses import Response

from src.config.const import ALERT_WEBHOOK_PATH
from src.services import alert_service

# 鉴权请求头名（ARMS 自定义 Header 承载；不进 URL，避免被访问日志记录）
ALERT_TOKEN_HEADER: str = "X-Alert-Token"

router = APIRouter()


@router.post(ALERT_WEBHOOK_PATH)
async def alert_webhook(request: Request) -> Response:
    """接收阿里云 ARMS 告警通知。"""
    status_code = alert_service.handle_webhook(
        await request.body(), request.headers.get(ALERT_TOKEN_HEADER)
    )
    return Response(status_code=status_code)
