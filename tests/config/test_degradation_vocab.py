"""降级词表守卫（P2）：取值/文案唯一来源 + 失败分类原语。

守卫的意义：spec 的「降级原因与用户文案的取值集合」表是唯一来源，
实现不得自造取值；本文件把"实现里的取值 ⊆ 规格表"这件事变成可执行断言。
"""

from src.config.const import SSEInteractionTexts
from src.core.log_event_specs import EVENT_SPECS
from src.core.log_events import Event
from src.infra.retry_policy import classify_failure
from src.rag.errors import RetrievalUnavailableError


class _StatusErr(Exception):
    """带 HTTP 状态码的异常替身（openai / httpx 的失败都带 status_code）。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def test_degraded_event_registered_with_reason_field():
    """降级留痕事件在 EVENT_SPECS 登记且带 reason 字段（供日志侧判据匹配）。"""
    assert Event.DEGRADED.value in EVENT_SPECS
    assert "reason" in EVENT_SPECS[Event.DEGRADED.value].fields


def test_reason_vocabulary_covers_spec_table():
    """取值集合覆盖 spec 表（含 P3 才会消费的 generation_idle）。"""
    required = {
        "embedding_failed",
        "vector_store_failed",
        "llm_unavailable_with_context",
        "llm_unavailable_no_context",
        "llm_stream_interrupted",
        "provider_unavailable",
        "upstream_exhausted",
        "generation_idle",
    }
    assert required <= set(SSEInteractionTexts.DEGRADATION_REASONS)


def test_visible_texts_stay_inside_reason_vocabulary():
    """文案表的 key 必须都是已登记取值（防自造）。"""
    assert set(SSEInteractionTexts.DEGRADATION_TEXTS) <= set(
        SSEInteractionTexts.DEGRADATION_REASONS
    )


def test_stage_degraded_is_dedicated():
    """降级 stage 不得复用 retrieve / web_search（会触发前端重分类）。"""
    assert SSEInteractionTexts.STAGE_DEGRADED not in ("retrieve", "web_search")


def test_provider_unavailable_copy_does_not_lure_retry():
    """可用性类错误重试必然再失败 ⇒ 文案不得出现重试语义。"""
    text = SSEInteractionTexts.DEGRADATION_TEXTS["provider_unavailable"]
    assert "重试" not in text
    assert "再试" not in text


def test_classify_failure_maps_http_status_codes():
    assert classify_failure(_StatusErr(401)) == "provider_unavailable"
    assert classify_failure(_StatusErr(403)) == "provider_unavailable"
    assert classify_failure(_StatusErr(429)) == "upstream_exhausted"
    assert classify_failure(_StatusErr(500)) == "upstream_exhausted"
    assert classify_failure(_StatusErr(503)) == "upstream_exhausted"


def test_classify_failure_reads_wrapped_cause_chain():
    """异常可能被上游框架包装 ⇒ 沿 __cause__ / __context__ 链上溯状态码。"""
    wrapped = RuntimeError("langgraph wrapped")
    wrapped.__cause__ = _StatusErr(503)
    assert classify_failure(wrapped) == "upstream_exhausted"


def test_classify_failure_vector_store_marker():
    assert (
        classify_failure(RetrievalUnavailableError("pg down")) == "vector_store_failed"
    )


def test_classify_failure_unknown_returns_none():
    assert classify_failure(ValueError("bad input")) is None


def test_classify_failure_outputs_stay_inside_vocabulary():
    for exc in (
        _StatusErr(403),
        _StatusErr(429),
        RetrievalUnavailableError("x"),
    ):
        assert classify_failure(exc) in SSEInteractionTexts.DEGRADATION_REASONS
