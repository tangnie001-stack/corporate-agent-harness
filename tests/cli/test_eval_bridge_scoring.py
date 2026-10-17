from src.cli.eval_ragas import score_rows


class _RecordingMetric:
    """记录调用顺序；返回固定分值。"""

    def __init__(self, value, calls):
        self.value = value
        self.calls = calls

    def measure(self, test_case):
        self.calls.append(test_case.input)
        self.score = self.value
        return self.value


def _rows():
    return [
        {
            "q_index": 0,
            "trace_id": "t0",
            "question": "q0",
            "answer": "a0",
            "ground_truth": "g0",
            "contexts": ["c0"],
        },
        {
            "q_index": 1,
            "trace_id": "t1",
            "question": "q1",
            "answer": "a1",
            "ground_truth": "g1",
            "contexts": ["c1"],
        },
    ]


def test_score_rows_is_serial_and_keeps_question_order():
    calls: list[str] = []
    metrics = {"faithfulness": _RecordingMetric(0.5, calls)}

    out = score_rows(_rows(), metrics)

    assert calls == ["q0", "q1"], "桥复用模块级单例 ⇒ 必须串行且按题序"
    assert [r["q_index"] for r in out] == [0, 1]
    assert [r["faithfulness"] for r in out] == [0.5, 0.5]


def test_score_rows_maps_test_case_fields_for_the_bridge():
    seen: list[dict] = []

    class _CapturingMetric:
        def measure(self, test_case):
            seen.append(
                {
                    "input": test_case.input,
                    "actual_output": test_case.actual_output,
                    "expected_output": test_case.expected_output,
                    "retrieval_context": test_case.retrieval_context,
                }
            )
            self.score = 1.0
            return 1.0

    score_rows(_rows()[:1], {"context_precision": _CapturingMetric()})

    assert seen[0] == {
        "input": "q0",
        "actual_output": "a0",
        "expected_output": "g0",
        "retrieval_context": ["c0"],
    }


def test_diagnostics_run_serially_after_main_metrics_in_question_order():
    """诊断指标排主指标之后、仍在同一题内串行执行，逐题推进。"""
    calls: list[str] = []

    class _NamedMetric:
        def __init__(self, name: str) -> None:
            self.name = name

        def measure(self, test_case) -> float:
            calls.append(f"{test_case.input}:{self.name}")
            return 0.5

    metrics = {"faithfulness": _NamedMetric("faithfulness")}
    diagnostics = {
        "contextual_relevancy": _NamedMetric("contextual_relevancy"),
        "non_advice": _NamedMetric("non_advice"),
    }

    out = score_rows(_rows(), metrics, diagnostic_metrics=diagnostics)

    assert calls == [
        "q0:faithfulness",
        "q0:contextual_relevancy",
        "q0:non_advice",
        "q1:faithfulness",
        "q1:contextual_relevancy",
        "q1:non_advice",
    ]
    assert [r["contextual_relevancy"] for r in out] == [0.5, 0.5]
    assert [r["non_advice"] for r in out] == [0.5, 0.5]
