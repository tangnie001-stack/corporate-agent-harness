"""知识库级 RAGAS 评估结果查询 API。"""

import json
import math

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel

from src.api.dependencies import get_app_service
from src.api.schema import ResponseModel
from src.config.const import EVAL_PASS_THRESHOLD
from src.services.app_service import AppService

router = APIRouter()


class KbEvalRequest(BaseModel):
    """知识库评估请求。"""

    kb_id: str  # 知识库 UUID


def _mean_answer_correctness(detail_json: str | None) -> float | None:
    """从逐题明细 JSON 导出 `answer_correctness` 均值。

    该指标在 `eval_report` 表无独立列，只能由 `detail_json` 逐题值求均值；
    跳过缺键、非数值与 `NaN`（掩码/失败题在明细里即为 `NaN`）。`detail_json`
    缺失、不可解析或无可取值时返回 None。

    Args:
        detail_json: 落库的逐题明细 JSON 字符串（每条含五项指标）

    Returns:
        `answer_correctness` 均值；无可取值时为 None
    """
    if not detail_json:
        return None
    try:
        entries = json.loads(detail_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(entries, list):
        return None
    values: list[float] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("answer_correctness")
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isnan(value):
            continue
        values.append(value)
    if not values:
        return None
    return sum(values) / len(values)


def _build_eval_payload(report: dict) -> dict:
    """把评估报告实体字段规整为接口响应 data。

    Args:
        report: 报告实体字段（见 `AppService.get_latest_eval_report`）

    Returns:
        响应 data，含五项指标、`overall_score` / `passed` 与 `pass_threshold`
    """
    return {
        "eval_date": report["eval_date"].isoformat()
        if hasattr(report["eval_date"], "isoformat")
        else str(report["eval_date"]),
        "faithfulness": float(report["faithfulness"])
        if report["faithfulness"]
        else None,
        "answer_relevancy": float(report["answer_relevancy"])
        if report["answer_relevancy"]
        else None,
        "context_precision": float(report["context_precision"])
        if report["context_precision"]
        else None,
        "context_recall": float(report["context_recall"])
        if report["context_recall"]
        else None,
        "answer_correctness": _mean_answer_correctness(report.get("detail_json")),
        "overall_score": float(report["overall_score"])
        if report["overall_score"]
        else None,
        "passed": report["passed"],
        "qa_count": report["qa_count"],
        "run_type": report["run_type"],
        "pass_threshold": EVAL_PASS_THRESHOLD,
    }


@router.post("/kbs/eval/latest", response_model=ResponseModel)
async def get_latest_kb_eval(
    body: KbEvalRequest,
    request: Request,
    svc: AppService = Depends(get_app_service),
):
    """获取知识库最新的 RAGAS 评估结果。

    Args:
        kb_id: 知识库 UUID

    Returns:
        ResponseModel: data 含评估详情或 None
    """
    report = await svc.get_latest_eval_report(body.kb_id)
    if report:
        logger.info(
            "KB eval report found: kb_id={} score={}",
            body.kb_id,
            report.get("overall_score"),
        )
        return ResponseModel(data=_build_eval_payload(report))
    logger.info("KB eval report not found: kb_id={}", body.kb_id)
    return ResponseModel(data=None)
