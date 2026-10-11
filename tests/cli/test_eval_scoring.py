from src.cli.eval_scoring import (
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
    classify_sample,
)
from src.rag.context import RAGContext


def _ctx(kind: str) -> RAGContext:
    return RAGContext(
        content="x", source="s", page=1, doc_id="d", chunk_id="c", kind=kind
    )


def test_classify_empty_when_no_contexts() -> None:
    assert classify_sample([]) == SAMPLE_CLASS_EMPTY


def test_classify_pure_kb() -> None:
    assert classify_sample([_ctx("kb"), _ctx("kb")]) == SAMPLE_CLASS_PURE_KB


def test_classify_web_when_any_web() -> None:
    assert classify_sample([_ctx("kb"), _ctx("web")]) == SAMPLE_CLASS_WEB
