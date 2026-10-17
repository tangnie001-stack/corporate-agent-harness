"""逃生门可替换边界的契约测试（design D10）。

边界 = ① 用例构造 `_bridge_test_case`；② 评分 `build_metrics` + `score_rows`。
本轮只**预留边界**、不实现替代实现；本测试把边界的两个可替换点钉成契约。
"""

from src.cli import eval_deepeval
from src.config.const import RAGAS_METRIC_COLUMNS


class _FakeMetric:
    """最小桥度量替身：`measure` 恒回 1.0（模拟桥的 float 出口）。"""

    def measure(self, test_case):
        return 1.0


def test_score_rows_output_shape_is_replaceable_contract():
    """评分侧边界：`score_rows` 每行含各指标名 + `"error"`，行序与输入一致。

    `metrics` 由调用方注入 ⇒ 替代实现只需提供同形入参，无需改这里的编排。
    """
    metrics = {name: _FakeMetric() for name in RAGAS_METRIC_COLUMNS}
    rows = [
        {
            "q_index": 0,
            "trace_id": "t0",
            "question": "q",
            "answer": "a",
            "ground_truth": "g",
            "contexts": ["c"],
        }
    ]

    scored = eval_deepeval.score_rows(rows, metrics)
    assert len(scored) == 1
    assert scored[0]["q_index"] == 0
    assert "error" in scored[0]
    for name in RAGAS_METRIC_COLUMNS:
        assert name in scored[0]


def test_bridge_test_case_is_the_case_construction_boundary():
    """用例构造侧边界：`_bridge_test_case` 产出桥要的四字段 LLMTestCase。"""
    tc = eval_deepeval._bridge_test_case(
        {
            "question": "Q",
            "answer": "A",
            "ground_truth": "G",
            "contexts": ["c1", "c2"],
        }
    )
    assert tc.input == "Q"
    assert tc.actual_output == "A"
    assert tc.expected_output == "G"
    assert list(tc.retrieval_context or []) == ["c1", "c2"]


def test_scoring_boundary_functions_are_module_level():
    """两个边界点须是可替换的模块级函数（替代实现据此接入）。"""
    assert callable(eval_deepeval._bridge_test_case)
    assert callable(eval_deepeval.score_rows)
    from src.cli.eval_scoring import build_metrics

    assert callable(build_metrics)
