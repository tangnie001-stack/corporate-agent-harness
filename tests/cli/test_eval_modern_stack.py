"""现代栈评分内核：逐行 ascore、失败记 error、不丢行、行序保持。"""

import pandas as pd

from src.config.const import RAGAS_EVAL_CONCURRENCY


class _FakeMetric:
    def __init__(self, value=None, exc=None):
        self._value, self._exc = value, exc

    async def ascore(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        outer = self

        class _R:
            value = outer._value

        return _R()


def _metrics(faith=1.0, relev=1.0, prec=1.0, rec=1.0, corr=1.0, relev_exc=None):
    return {
        "faithfulness": _FakeMetric(value=faith),
        "answer_relevancy": _FakeMetric(value=relev, exc=relev_exc),
        "context_precision": _FakeMetric(value=prec),
        "context_recall": _FakeMetric(value=rec),
        "answer_correctness": _FakeMetric(value=corr),
    }


def _run(metrics, n=2):
    from src.cli import eval_ragas

    return eval_ragas.run_evaluation(
        [f"q{i}" for i in range(n)],
        [f"g{i}" for i in range(n)],
        [f"a{i}" for i in range(n)],
        [[f"c{i}"] for i in range(n)],
        metrics,
        [f"trace{i}" for i in range(n)],
    )


def test_run_evaluation_keeps_row_and_row_order_on_failure():
    df = _run(_metrics(relev_exc=TimeoutError("slow")), n=3).to_pandas()

    assert len(df) == 3  # 不丢行
    assert list(df["q_index"]) == [0, 1, 2]  # 行序回排（arun 按完成序 append）
    assert df.loc[0, "faithfulness"] == 1.0
    assert pd.isna(df.loc[0, "answer_relevancy"])  # 失败指标留空
    assert "TimeoutError" in df.loc[0, "error"]


def test_run_evaluation_clean_run_has_empty_error():
    df = _run(_metrics(), n=2).to_pandas()

    assert list(df["error"]) == ["", ""]
    assert df.loc[1, "context_recall"] == 1.0


def test_concurrency_constant_is_positive():
    assert RAGAS_EVAL_CONCURRENCY >= 1
