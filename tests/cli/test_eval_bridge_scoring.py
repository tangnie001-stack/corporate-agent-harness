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
