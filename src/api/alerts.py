"""告警接收路由（POST /api/alerts/webhook/{token}）。

极薄：只取路径里的 token 与原始 body，其余全部委托 `alert_service`。
不声明 Pydantic 请求体 —— 避免框架校验把 400 改写成 422/500 统一信封。

token 走**路径**而非请求头：上游是 Grafana 的 contact point，它不支持自定义
请求头。代价是 token 会出现在 URL 里，故两侧都做了脱敏：nginx 的 access log
用 `masked` 格式（见 deploy/nginx/nginx.conf），应用侧日志用
`src/core/logging.mask_alert_webhook_path`（见 middleware/response_processor.py）。
"""

from fastapi import APIRouter, Request
from starlette.responses import Response

from src.config.const import ALERT_WEBHOOK_PATH
from src.services import alert_service

router = APIRouter()


@router.post(ALERT_WEBHOOK_PATH)
async def alert_webhook(token: str, request: Request) -> Response:
    """接收告警通知（Grafana contact point 直连）。

    Args:
        token: URL 路径末段的鉴权 token
        request: 原始请求（只取 body）

    Returns:
        202 已受理 / 401 token 不匹配 / 400 载荷非法
    """
    return Response(
        status_code=alert_service.handle_webhook(await request.body(), token)
    )
