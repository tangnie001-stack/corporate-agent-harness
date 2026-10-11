"""LLM 连通性测试 API — 直接调用 LLM 验证模型可用性和响应速度。"""

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from src.api.schema import ResponseModel
from src.models import get_llm

router = APIRouter()


class LlmTestRequest(BaseModel):
    """LLM 测试请求。"""

    model: str = ""  # 空值表示使用默认模型
    prompt: str = "你好，请回复OK"  # 测试提示词
    temperature: float = 0  # 生成温度


@router.post("/llm/test", response_model=ResponseModel)
async def llm_test(body: LlmTestRequest):
    """测试 LLM 连通性 — 发送一条简单请求验证模型可用性和响应耗时。

    模型经 provider 兼容层解析（`get_llm`）：空 `model` → `registry.models[main]`
    兜底。不再依赖 `settings.LLM_MODEL` —— 它按 design D3 是"可选覆盖"，默认空串，
    直接当模型名会以空名调用而失败。

    Args:
        model: 模型名；空值表示 registry 默认绑定
        prompt: 测试提示词
        temperature: 生成温度

    Returns:
        ResponseModel: data 包含 model, response, latency_seconds
    """
    model_override = body.model or None
    model_name = body.model or "(registry default)"
    logger.info("LLM test requested: model={} prompt={}", model_name, body.prompt[:50])

    import time

    start = time.time()
    try:
        llm = get_llm(model=model_override, temperature=body.temperature)
        model_name = llm.model_name
        result = llm.invoke(body.prompt)
        elapsed = round(time.time() - start, 2)
        logger.info("LLM test OK: model={} latency={}s", model_name, elapsed)
        return ResponseModel(
            data={
                "model": model_name,
                "response": result.content,
                "latency_seconds": elapsed,
            }
        )
    except Exception as e:  # noqa: BLE001
        elapsed = round(time.time() - start, 2)
        logger.error(
            "LLM test failed: model={} latency={}s error={}", model_name, elapsed, e
        )
        return ResponseModel(
            code="ERROR",
            message=f"LLM 调用失败: {e}",
            data={
                "model": model_name,
                "latency_seconds": elapsed,
                "error": str(e),
            },
        )
