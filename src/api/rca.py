"""诊断报告查询端点（详情页用；需登录）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_app_service
from src.api.schema import ResponseModel
from src.config.response_codes import Code
from src.services.app_service import AppService
from src.utils.errors import BusinessError

router = APIRouter()


@router.get("/rca/diag/{diag_id}", response_model=ResponseModel)
async def get_diagnosis(
    diag_id: str,
    svc: AppService = Depends(get_app_service),
):
    """按 diag_id 取诊断报告全文。

    Args:
        diag_id: 诊断报告 id（卡片详情链接携带）
        svc: AppService 依赖

    Returns:
        ResponseModel: data 含档位、归属、根因、证据摘要、断言与建议动作

    Raises:
        BusinessError: 报告不存在（404）
    """
    data = await svc.get_diag_report(diag_id)
    if data is None:
        raise BusinessError(Code.NOT_FOUND, Code.NOT_FOUND_MSG, 404)
    return ResponseModel(data=data)
