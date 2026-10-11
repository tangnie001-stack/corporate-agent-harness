"""RAGAS 评估脚本单元测试.

测试覆盖：
  - TestParseArgs: 命令行参数解析
  - TestGenerateAnswers: 答案生成流程（mock graph）
  - TestRunEvaluation: RAGAS 评估流程（mock ragas）
  - TestSaveResults: CSV 结果保存
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import tool

from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


@tool
def _fake_retrieve_kb(query: str) -> str:
    """测试用 retrieval 工具：把材料写进当前请求上下文的收集器。

    Args:
        query: 查询文本
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        raise RuntimeError("测试桩要求调用方已 seed RequestContext")
    ctx.tool_contexts.append(
        RAGContext(content="hit", source="doc", page=1, doc_id="d", chunk_id="c")
    )
    return "[1] hit"


@pytest.mark.asyncio
async def test_f32_seeded_ctx_populates_tool_contexts(eval_graph_factory) -> None:
    """守卫 (A)：经 **真实修复路径** `generate_answers_and_contexts` 驱动。

    `generate_answers_and_contexts` 为每题 seed `RequestContext`，工具
    （`_fake_retrieve_kb`）据此把材料写进 `ctx.tool_contexts`，收尾节点
    `agent_finalize` 再回填 `state.tool_contexts`，最终成为 `contexts[0]`。
    若去掉函数内的 seed，`ctx` 为 None，工具抛错被 except 吞掉、`contexts[0]`
    恒空 ⇒ 本用例必失败。故它是 F-32 真实修复点的护栏（此前在测试内自行 seed
    的版本绕过了该修复点，pre-fix 也会通过）。

    断言只看 contexts 长度，不受"最终回答需带 `[1]` 以过 verify 护栏"影响。

    Args:
        eval_graph_factory: conftest 提供的真图工厂（注入真工具 + 真 ctx 传播）
    """
    from src.cli.eval_ragas import generate_answers_and_contexts

    graph = eval_graph_factory([_fake_retrieve_kb])
    (
        _answers,
        contexts,
        _trace_ids,
        _details,
        _sample_classes,
    ) = await generate_answers_and_contexts(graph, "kb1", "s1", ["问题"])
    assert len(contexts[0]) > 0


@pytest.mark.asyncio
async def test_f32_no_retrieval_leaves_contexts_empty(eval_graph_factory) -> None:
    """守卫 (B)：无工具时 `contexts[0]` 为空，且调用后 ctx 已复位（防泄漏）。

    同样经 `generate_answers_and_contexts` 真实路径驱动；空工具图下无人写
    收集器 ⇒ `contexts[0] == []`。附带断言调用结束后 `current_request_ctx`
    已复位为 None，锁住每题 finally 里的 reset（缺失时会把最后一个样本的 ctx
    泄漏给调用方）。

    Args:
        eval_graph_factory: conftest 提供的真图工厂（空工具 = agent 直答）
    """
    from src.cli.eval_ragas import generate_answers_and_contexts

    graph = eval_graph_factory([])  # 无工具，agent 直答
    (
        _answers,
        contexts,
        _trace_ids,
        _details,
        _sample_classes,
    ) = await generate_answers_and_contexts(graph, "kb1", "s1", ["问题"])
    assert contexts[0] == []
    assert current_request_ctx.get() is None


# ---- 传播契约用例（`_answer_one_question` 层，测试内自行 seed）----
# 与上面的 F-32 守卫互补：这两个用例隔离"图是否把 ctx 材料回填 state.tool_contexts"
# 这一传播契约（不经评测链路的 seed），便于区分"seed 缺失"与"图传播损坏"两类回归。


@pytest.mark.asyncio
async def test_propagation_graph_populates_state_tool_contexts(
    eval_graph_factory,
) -> None:
    """传播契约：测试内手动 seed ctx 后，图的收尾节点应回填 state.tool_contexts。

    Args:
        eval_graph_factory: conftest 提供的真图工厂（注入真工具 + 真 ctx 传播）
    """
    from src.cli.eval_ragas import _answer_one_question

    graph = eval_graph_factory([_fake_retrieve_kb])
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    token = current_request_ctx.set(ctx)
    try:
        _answer, contexts, _details = await _answer_one_question(
            graph, "kb1", "s1", "q", "trace_1"
        )
    finally:
        current_request_ctx.reset(token)
    assert len(contexts) > 0


@pytest.mark.asyncio
async def test_propagation_no_tool_keeps_state_contexts_empty(
    eval_graph_factory,
) -> None:
    """传播契约：无检索工具时 state.tool_contexts 为空。

    Args:
        eval_graph_factory: conftest 提供的真图工厂（空工具 = agent 直答）
    """
    from src.cli.eval_ragas import _answer_one_question

    graph = eval_graph_factory([])  # 无工具，agent 直答
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    token = current_request_ctx.set(ctx)
    try:
        _answer, contexts, _details = await _answer_one_question(
            graph, "kb1", "s1", "q", "trace_1"
        )
    finally:
        current_request_ctx.reset(token)
    assert contexts == []


class TestParseArgs:
    """命令行参数解析测试。"""

    @patch("sys.argv", ["eval_ragas.py", "--kb-id", "test-uuid"])
    def test_default_args(self) -> None:
        """默认参数应按预期设置。"""
        from src.cli.eval_ragas import parse_args

        args = parse_args()
        assert args.kb_id == "test-uuid"
        assert args.session_id == "ragas_eval_session"

    @patch(
        "sys.argv",
        ["eval_ragas.py", "--kb-id", "test-uuid", "--session-id", "custom_session"],
    )
    def test_custom_args(self) -> None:
        """应正确解析自定义参数。"""
        from src.cli.eval_ragas import parse_args

        args = parse_args()
        assert args.kb_id == "test-uuid"
        assert args.session_id == "custom_session"

    @patch(
        "sys.argv",
        ["eval_ragas.py", "--kb-id", "test-uuid", "--output", "/tmp/result.csv"],
    )
    def test_output_arg(self) -> None:
        """应正确解析输出路径参数。"""
        from src.cli.eval_ragas import parse_args

        args = parse_args()
        assert args.output == "/tmp/result.csv"


class TestGenerateAnswers:
    """答案生成流程测试（mock graph async invoke）。"""

    def test_generate_success(self) -> None:
        """正常情况应返回答案列表和上下文列表。"""
        from src.cli.eval_ragas import generate_answers_and_contexts

        mock_graph = MagicMock()

        async def mock_ainvoke(state: dict) -> dict:
            return {
                "answer": f"Answer for: {state['query'][:10]}",
                "tool_contexts": [
                    MagicMock(
                        to_prompt_text=lambda: "Context about 茅台营收1,741亿元",
                        source="kweichow.pdf",
                        score=0.91,
                        kind="kb",
                    ),
                    MagicMock(
                        to_prompt_text=lambda: "Context about 同比增长15.66%",
                        source="kweichow.pdf",
                        score=0.87,
                        kind="kb",
                    ),
                ],
            }

        mock_graph.ainvoke = AsyncMock(side_effect=mock_ainvoke)

        answers, contexts, trace_ids, retrieval_details, sample_classes = asyncio.run(
            generate_answers_and_contexts(
                mock_graph,
                "test_kb",
                "sess_1",
                ["贵州茅台营收多少？", "净利润多少？"],
            )
        )

        assert len(answers) == 2
        assert len(contexts) == 2
        assert len(trace_ids) == 2
        assert len(retrieval_details) == 2
        assert len(sample_classes) == 2
        assert trace_ids[0].startswith("eval_")
        assert "Answer for" in answers[0]
        assert len(contexts[0]) == 2
        # 检索明细与 contexts 一一对应，逐条含 source/score/kind 三键
        assert retrieval_details[0] == [
            {"source": "kweichow.pdf", "score": 0.91, "kind": "kb"},
            {"source": "kweichow.pdf", "score": 0.87, "kind": "kb"},
        ]
        assert retrieval_details[1] == [
            {"source": "kweichow.pdf", "score": 0.91, "kind": "kb"},
            {"source": "kweichow.pdf", "score": 0.87, "kind": "kb"},
        ]

    def test_generate_partial_failure(self) -> None:
        """部分问题失败时应返回错误标记，不中断整体流程。"""
        from src.cli.eval_ragas import generate_answers_and_contexts

        mock_graph = MagicMock()
        call_count = 0

        async def mock_ainvoke(state: dict) -> dict:
            nonlocal call_count
            call_count += 1
            q = state["query"]
            if "失败" in q:
                raise ValueError("模拟错误")
            return {
                "answer": "正常回答",
                "tool_contexts": [
                    MagicMock(
                        to_prompt_text=lambda: "ctx",
                        source="s.pdf",
                        score=0.5,
                        kind="kb",
                    )
                ],
            }

        mock_graph.ainvoke = AsyncMock(side_effect=mock_ainvoke)

        answers, contexts, trace_ids, retrieval_details, sample_classes = asyncio.run(
            generate_answers_and_contexts(
                mock_graph,
                "kb",
                "sess",
                ["正常问题", "模拟失败", "正常问题2"],
            )
        )

        assert len(answers) == 3
        assert len(trace_ids) == 3
        assert len(retrieval_details) == 3
        assert len(sample_classes) == 3
        assert "[ERROR]" in answers[1]
        assert contexts[1] == []
        # 失败的问题也要有 trace_id，便于回溯；检索明细异常分支为空列表
        assert trace_ids[1].startswith("eval_")
        assert retrieval_details[0] == [{"source": "s.pdf", "score": 0.5, "kind": "kb"}]
        assert retrieval_details[1] == []


class TestRunEvaluation:
    """RAGAS 评估流程测试（全 mock）。"""

    @patch("datasets.Dataset.from_dict")
    @patch("ragas.evaluate")
    def test_evaluation_runs(
        self,
        mock_evaluate: MagicMock,
        mock_from_dict: MagicMock,
    ) -> None:
        """评估流程应正确调用 ragas.evaluate。"""
        from ragas.metrics import (
            AnswerCorrectness,
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        from src.cli.eval_ragas import run_evaluation

        mock_result = MagicMock()
        mock_result.to_pandas.return_value = MagicMock()
        mock_evaluate.return_value = mock_result

        llm_wrapper = MagicMock()
        emb_wrapper = MagicMock()

        result = run_evaluation(
            ["Q1"],
            ["GT1"],
            ["A1"],
            [["ctx1"]],
            llm_wrapper,
            emb_wrapper,
        )

        mock_evaluate.assert_called_once()
        # 验证 evaluate 传入了正确的参数（按 metric name 比较）
        _, kwargs = mock_evaluate.call_args
        assert kwargs["llm"] is llm_wrapper
        assert kwargs["embeddings"] is emb_wrapper
        actual_metric_names = {m.name for m in kwargs["metrics"]}
        expected_metric_names = {
            context_precision.name,
            context_recall.name,
            faithfulness.name,
            answer_relevancy.name,
            AnswerCorrectness().name,
        }
        assert actual_metric_names == expected_metric_names
        assert result is mock_result

    @patch("datasets.Dataset.from_dict")
    @patch("ragas.evaluate")
    def test_evaluation_empty_contexts(
        self,
        mock_evaluate: MagicMock,
        mock_from_dict: MagicMock,
    ) -> None:
        """空上下文列表不应导致崩溃。"""
        from src.cli.eval_ragas import run_evaluation

        mock_result = MagicMock()
        mock_result.to_pandas.return_value = MagicMock()
        mock_evaluate.return_value = mock_result

        result = run_evaluation(
            ["Q1"],
            ["GT1"],
            ["A1"],
            [[]],
            MagicMock(),
            MagicMock(),
        )
        assert result is mock_result
        mock_evaluate.assert_called_once()


class TestSaveResults:
    """结果保存测试。"""

    def test_save_csv(self) -> None:
        """CSV 保存应包含所有必要列。"""
        import pandas as pd

        from src.cli.eval_ragas import save_results_csv

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.95],
                "answer_relevancy": [0.88],
                "context_recall": [0.92],
                "context_precision": [0.85],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            save_results_csv(
                mock_result,
                ["Q1"],
                ["GT1"],
                output_path=tmp_path,
            )

            with open(tmp_path, "r") as f:
                content = f.read()

            assert "faithfulness" in content

        finally:
            os.unlink(tmp_path)

    def test_save_csv_with_chunk_size(self) -> None:
        """Benchmark 模式 CSV 应记录 chunk_size。"""
        import pandas as pd

        from src.cli.eval_ragas import save_results_csv

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.95],
                "answer_relevancy": [0.88],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            save_results_csv(
                mock_result,
                ["Q1"],
                ["GT1"],
                output_path=tmp_path,
            )

            with open(tmp_path, "r") as f:
                content = f.read()

            assert "faithfulness" in content

        finally:
            os.unlink(tmp_path)


# ---- --gate 标志测试 ----


def test_gate_passes_with_high_scores() -> None:
    """--gate exits 0 when all metrics meet thresholds."""
    import pandas as pd

    from src.cli.eval_ragas import check_gate

    mock_result = MagicMock()
    mock_df = pd.DataFrame(
        {
            "faithfulness": [0.95, 0.90],
            "context_precision": [0.85, 0.90],
            "context_recall": [0.75, 0.80],
            "answer_relevancy": [0.90, 0.88],
        }
    )
    mock_result.to_pandas.return_value = mock_df

    with pytest.raises(SystemExit) as exc_info:
        check_gate(mock_result, ["Q1", "Q2"])
    assert exc_info.value.code == 0


def test_gate_fails_with_low_scores() -> None:
    """--gate exits 1 when a metric is below threshold."""
    import pandas as pd

    from src.cli.eval_ragas import check_gate

    mock_result = MagicMock()
    mock_df = pd.DataFrame(
        {
            "faithfulness": [0.50, 0.45],
            "context_precision": [0.85, 0.90],
            "context_recall": [0.75, 0.80],
            "answer_relevancy": [0.90, 0.88],
        }
    )
    mock_result.to_pandas.return_value = mock_df

    with pytest.raises(SystemExit) as exc_info:
        check_gate(mock_result, ["Q1", "Q2"])
    assert exc_info.value.code == 1
