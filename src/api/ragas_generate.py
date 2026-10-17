"""RAGAS 出题 API 已下线：保留路由返回 410，避免调用方撞到 404/ImportError。"""

from fastapi import APIRouter
from pydantic import BaseModel

from src.api.schema import ResponseModel
from src.config.response_codes import Code

router = APIRouter()


class RagasGenerateRequest(BaseModel):
    """兼容旧调用方的请求体（内容不再使用）。"""

    kb_name: str = ""
    size: int = 0


@router.post("/ragas/generate", response_model=ResponseModel, status_code=410)
async def ragas_generate(body: RagasGenerateRequest):
    """返回 410 与迁移指引。

    Args:
        body: 旧请求体（仅为兼容解析）

    Returns:
        ResponseModel：code=GONE，message 指向 DeepEval Synthesizer
    """
    return ResponseModel(
        code=Code.GONE,
        message=Code.GONE_MSG,
        data=None,
    )
