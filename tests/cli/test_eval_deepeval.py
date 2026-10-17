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
    from src.cli.eval_deepeval import generate_answers_and_contexts

    graph = eval_graph_factory([_fake_retrieve_kb])
    (
        _answers,
        contexts,
        _trace_ids,
        _details,
        _sample_classes,
        _trajectories,
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
    from src.cli.eval_deepeval import generate_answers_and_contexts

    graph = eval_graph_factory([])  # 无工具，agent 直答
    (
        _answers,
        contexts,
        _trace_ids,
        _details,
        _sample_classes,
        _trajectories,
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
    from src.cli.eval_deepeval import _answer_one_question

    graph = eval_graph_factory([_fake_retrieve_kb])
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    token = current_request_ctx.set(ctx)
    try:
        _answer, contexts, _details, _messages = await _answer_one_question(
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
    from src.cli.eval_deepeval import _answer_one_question

    graph = eval_graph_factory([])  # 无工具，agent 直答
    ctx = RequestContext(session_id="s1", kb_id="kb1")
    token = current_request_ctx.set(ctx)
    try:
        _answer, contexts, _details, _messages = await _answer_one_question(
            graph, "kb1", "s1", "q", "trace_1"
        )
    finally:
        current_request_ctx.reset(token)
    assert contexts == []


class TestParseArgs:
    """命令行参数解析测试。"""

    @patch("sys.argv", ["eval_deepeval.py", "--kb-id", "test-uuid"])
    def test_default_args(self) -> None:
        """默认参数应按预期设置。"""
        from src.cli.eval_deepeval import parse_args

        args = parse_args()
        assert args.kb_id == "test-uuid"
        assert args.session_id == "ragas_eval_session"

    @patch(
        "sys.argv",
        ["eval_deepeval.py", "--kb-id", "test-uuid", "--session-id", "custom_session"],
    )
    def test_custom_args(self) -> None:
        """应正确解析自定义参数。"""
        from src.cli.eval_deepeval import parse_args

        args = parse_args()
        assert args.kb_id == "test-uuid"
        assert args.session_id == "custom_session"

    @patch(
        "sys.argv",
        ["eval_deepeval.py", "--kb-id", "test-uuid", "--output", "/tmp/result.csv"],
    )
    def test_output_arg(self) -> None:
        """应正确解析输出路径参数。"""
        from src.cli.eval_deepeval import parse_args

        args = parse_args()
        assert args.output == "/tmp/result.csv"


class TestGenerateAnswers:
    """答案生成流程测试（mock graph async invoke）。"""

    def test_generate_success(self) -> None:
        """正常情况应返回答案列表和上下文列表。"""
        from src.cli.eval_deepeval import generate_answers_and_contexts

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

        (
            answers,
            contexts,
            trace_ids,
            retrieval_details,
            sample_classes,
            trajectories,
        ) = asyncio.run(
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
        # mock 图未回填 messages，逐题摘要为空轨迹但仍与题目一一对应
        assert len(trajectories) == 2
        assert trajectories[0] == {"tool_calls": [], "iterations": 0}
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
        from src.cli.eval_deepeval import generate_answers_and_contexts

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

        (
            answers,
            contexts,
            trace_ids,
            retrieval_details,
            sample_classes,
            trajectories,
        ) = asyncio.run(
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
        # 失败的问题也要有 trace_id，便于回溯；检索明细与轨迹摘要异常分支为空
        assert trace_ids[1].startswith("eval_")
        assert retrieval_details[0] == [{"source": "s.pdf", "score": 0.5, "kind": "kb"}]
        assert retrieval_details[1] == []
        assert trajectories[1] == {"tool_calls": [], "iterations": 0}

    def test_generate_collects_trajectory_from_messages(self) -> None:
        """图 state 含工具调用消息时，轨迹应含工具名/入参/迭代数。

        覆盖 Task 5 遗留缺口：此前 mock 图不回填 messages，只验证了空轨迹；
        此处让 ``ainvoke`` 返回含 ``AIMessage(tool_calls=[...])`` + ``ToolMessage``
        的 messages，断言 ``summarize_trajectory`` 经真实链路产出的工具名与迭代数。
        """
        from langchain_core.messages import AIMessage, ToolMessage

        from src.cli.eval_deepeval import generate_answers_and_contexts

        mock_graph = MagicMock()

        async def mock_ainvoke(state: dict) -> dict:
            return {
                "answer": "最终回答[1]",
                "tool_contexts": [
                    MagicMock(
                        to_prompt_text=lambda: "ctx",
                        source="s.pdf",
                        score=0.5,
                        kind="kb",
                    )
                ],
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "retrieve_kb",
                                "args": {"query": "茅台营收"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    ToolMessage(content="工具输出正文", tool_call_id="call_1"),
                    AIMessage(content="最终回答[1]"),
                ],
            }

        mock_graph.ainvoke = AsyncMock(side_effect=mock_ainvoke)

        (
            _answers,
            _contexts,
            _trace_ids,
            _details,
            _sample_classes,
            trajectories,
        ) = asyncio.run(
            generate_answers_and_contexts(mock_graph, "kb", "sess", ["问题"])
        )

        assert trajectories[0]["tool_calls"][0]["name"] == "retrieve_kb"
        assert trajectories[0]["tool_calls"][0]["args"] == {"query": "茅台营收"}
        # 两条 AIMessage ⇒ 迭代 2 轮（ToolMessage 不计入）
        assert trajectories[0]["iterations"] == 2


class _FakeMetric:
    """伪造的桥指标实例：`measure` 返回固定 value，供 `run_evaluation` 单测。"""

    def __init__(self, value: float = 1.0) -> None:
        """保存固定返回值。

        Args:
            value: `measure` 返回的分数。
        """
        self._value = value

    def measure(self, test_case) -> float:
        """返回固定分数（不发起真实网络调用）。

        Args:
            test_case: `_bridge_test_case` 组装的 LLMTestCase，此处忽略。
        """
        return self._value


def _fake_metrics() -> dict:
    """构造四指标假实例表（顺序与 `eval_scoring.build_metrics` 一致）。"""
    return {
        "faithfulness": _FakeMetric(),
        "answer_relevancy": _FakeMetric(),
        "context_precision": _FakeMetric(),
        "context_recall": _FakeMetric(),
    }


class TestRunEvaluation:
    """RAGAS 评估流程测试（桥，传假 metrics）。"""

    def test_evaluation_runs(self) -> None:
        """评估流程应逐行产出四项指标分数，行序与输入一致。"""
        from src.cli.eval_deepeval import run_evaluation

        result = run_evaluation(
            ["Q1", "Q2"],
            ["GT1", "GT2"],
            ["A1", "A2"],
            [["ctx1"], ["ctx2"]],
            _fake_metrics(),
            ["trace_1", "trace_2"],
        )

        df = result.to_pandas()
        assert len(df) == 2
        assert list(df["q_index"]) == [0, 1]
        for col in (
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        ):
            assert list(df[col]) == [1.0, 1.0]
        assert list(df["error"]) == ["", ""]

    def test_evaluation_empty_contexts(self) -> None:
        """空上下文列表不应导致崩溃。"""
        from src.cli.eval_deepeval import run_evaluation

        result = run_evaluation(
            ["Q1"],
            ["GT1"],
            ["A1"],
            [[]],
            _fake_metrics(),
            ["trace_1"],
        )
        df = result.to_pandas()
        assert len(df) == 1
        assert df.loc[0, "context_recall"] == 1.0


class TestSaveResults:
    """结果保存测试。"""

    def test_save_csv(self) -> None:
        """CSV 应含掩码新列（sample_class/skipped）与轨迹列。"""
        import pandas as pd

        from src.cli.eval_deepeval import save_results_csv
        from src.config.const import SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_PURE_KB

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.95, 0.90],
                "answer_relevancy": [0.88, 0.86],
                "context_recall": [0.92, 0.88],
                "context_precision": [0.85, 0.83],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df
        trajectories = [
            {
                "tool_calls": [{"name": "retrieve_kb", "args": {"query": "x"}}],
                "iterations": 1,
            },
            {"tool_calls": [], "iterations": 0},
        ]

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            save_results_csv(
                mock_result,
                ["Q1", "Q2"],
                ["GT1", "GT2"],
                output_path=tmp_path,
                sample_classes=[SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY],
                trajectories=trajectories,
            )

            with open(tmp_path, "r") as f:
                content = f.read()

            assert "faithfulness" in content
            assert "sample_class" in content
            assert "skipped" in content
            assert "trajectory" in content
            assert "retrieve_kb" in content

        finally:
            os.unlink(tmp_path)

    def test_save_csv_with_chunk_size(self) -> None:
        """Benchmark 模式 CSV 应记录 chunk_size。"""
        import pandas as pd

        from src.cli.eval_deepeval import save_results_csv
        from src.config.const import SAMPLE_CLASS_PURE_KB

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
                sample_classes=[SAMPLE_CLASS_PURE_KB],
            )

            with open(tmp_path, "r") as f:
                content = f.read()

            assert "faithfulness" in content

        finally:
            os.unlink(tmp_path)

    def test_save_markdown_report_includes_new_columns(self) -> None:
        """MD 报告应含 sample_class、skipped、trajectory 列，且不含已退出的 answer_correctness。"""
        import pandas as pd

        from src.cli.eval_deepeval import save_markdown_report
        from src.config.const import SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_PURE_KB

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.95, 0.90],
                "answer_relevancy": [0.88, 0.86],
                "context_recall": [0.92, 0.88],
                "context_precision": [0.85, 0.83],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            csv_path = tmp.name
        md_path = csv_path.rsplit(".", 1)[0] + ".md"

        try:
            save_markdown_report(
                mock_result,
                ["Q1", "Q2"],
                csv_path,
                sample_classes=[SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY],
                trajectories=[
                    {"tool_calls": [], "iterations": 1},
                    {"tool_calls": [], "iterations": 0},
                ],
            )

            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "answer_correctness" not in content
            assert "sample_class" in content
            assert "skipped" in content
            assert "trajectory" in content

        finally:
            if os.path.exists(md_path):
                os.unlink(md_path)
            os.unlink(csv_path)

    def test_save_markdown_report_mean_line_only_whitelist_metrics(self) -> None:
        """MD 均值行只含白名单指标，额外数值列（如 chunk_size）不得出现。"""
        import pandas as pd

        from src.cli.eval_deepeval import save_markdown_report
        from src.config.const import SAMPLE_CLASS_PURE_KB

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.95],
                "answer_relevancy": [0.88],
                "context_recall": [0.92],
                "context_precision": [0.85],
                "answer_correctness": [0.91],
                "chunk_size": [512],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            csv_path = tmp.name
        md_path = csv_path.rsplit(".", 1)[0] + ".md"

        try:
            save_markdown_report(
                mock_result,
                ["Q1"],
                csv_path,
                sample_classes=[SAMPLE_CLASS_PURE_KB],
            )

            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "Main scores (pure-KB)" in content
            assert "faithfulness=0.9500" in content
            assert "chunk_size" not in content

        finally:
            if os.path.exists(md_path):
                os.unlink(md_path)
            os.unlink(csv_path)


def test_print_metric_averages_only_whitelist_metrics(capsys) -> None:
    """stdout 逐指标行只打白名单指标，额外数值列（如 chunk_size）不得出现。

    Args:
        capsys: pytest 捕获 stdout 的 fixture
    """
    import pandas as pd

    from src.cli.eval_deepeval import _print_metric_averages
    from src.cli.eval_scoring import apply_sample_masks
    from src.config.const import SAMPLE_CLASS_PURE_KB

    raw_df = pd.DataFrame(
        {
            "faithfulness": [0.95],
            "answer_relevancy": [0.88],
            "context_precision": [0.85],
            "context_recall": [0.92],
            "chunk_size": [512],
        }
    )
    df = apply_sample_masks(raw_df, [SAMPLE_CLASS_PURE_KB])

    _print_metric_averages(df)
    out = capsys.readouterr().out

    metric_lines = [ln for ln in out.splitlines() if ln.strip()]
    # 每指标一行、只剩 4 个白名单指标
    assert len(metric_lines) == 4
    assert "faithfulness: 0.9500" in out
    assert "answer_correctness" not in out
    assert "chunk_size" not in out


def test_save_markdown_report_requires_sample_classes() -> None:
    """sample_classes 为必填：缺省调用触发 TypeError（默认值语义不可再误导）。"""
    import pandas as pd

    from src.cli.eval_deepeval import save_markdown_report

    mock_result = MagicMock()
    mock_result.to_pandas.return_value = pd.DataFrame({"faithfulness": [0.9]})

    with pytest.raises(TypeError):
        save_markdown_report(mock_result, ["Q1"], "/tmp/unused.csv")  # type: ignore[call-arg]


def test_save_results_csv_requires_sample_classes() -> None:
    """sample_classes 为必填：缺省调用触发 TypeError。"""
    import pandas as pd

    from src.cli.eval_deepeval import save_results_csv

    mock_result = MagicMock()
    mock_result.to_pandas.return_value = pd.DataFrame({"faithfulness": [0.9]})

    with pytest.raises(TypeError):
        save_results_csv(
            mock_result,
            ["Q1"],
            ["GT1"],
            "/tmp/unused.csv",
        )  # type: ignore[call-arg]


# ---- --gate 标志测试 ----


def test_gate_passes_with_high_scores() -> None:
    """--gate 在全为纯 KB 且主分达标时 exits 0。"""
    import pandas as pd

    from src.cli.eval_deepeval import check_gate
    from src.cli.eval_scoring import apply_sample_masks
    from src.config.const import SAMPLE_CLASS_PURE_KB

    raw_df = pd.DataFrame(
        {
            "faithfulness": [0.95, 0.90],
            "context_precision": [0.85, 0.90],
            "context_recall": [0.75, 0.80],
            "answer_relevancy": [0.90, 0.88],
        }
    )
    # 门禁接收已掩码的 DataFrame（含 sample_class 列）
    df = apply_sample_masks(raw_df, [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_PURE_KB])

    with pytest.raises(SystemExit) as exc_info:
        check_gate(df, ["Q1", "Q2"])
    assert exc_info.value.code == 0


def test_gate_fails_with_low_scores() -> None:
    """--gate 在主分低于阈值时 exits 1。"""
    import pandas as pd

    from src.cli.eval_deepeval import check_gate
    from src.cli.eval_scoring import apply_sample_masks
    from src.config.const import SAMPLE_CLASS_PURE_KB

    raw_df = pd.DataFrame(
        {
            "faithfulness": [0.50, 0.45],
            "context_precision": [0.85, 0.90],
            "context_recall": [0.75, 0.80],
            "answer_relevancy": [0.90, 0.88],
        }
    )
    df = apply_sample_masks(raw_df, [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_PURE_KB])

    with pytest.raises(SystemExit) as exc_info:
        check_gate(df, ["Q1", "Q2"])
    assert exc_info.value.code == 1


def test_gate_fails_when_main_score_empty() -> None:
    """无纯 KB 样本时门禁必须失败（不能因全量均值而通过）。"""
    import pandas as pd

    from src.cli.eval_deepeval import check_gate

    df = pd.DataFrame(
        {
            "faithfulness": [float("nan")],
            "context_precision": [float("nan")],
            "context_recall": [float("nan")],
            "answer_relevancy": [0.9],
            "answer_correctness": [0.9],
            "sample_class": ["empty"],
            "skipped": ["faithfulness;context_precision;context_recall"],
        }
    )
    with pytest.raises(SystemExit) as e:
        check_gate(df, ["Q1"])
    assert e.value.code == 1


def test_save_eval_report_tolerates_nan_metric() -> None:
    """跳过值（pd.NA）不得致 float() 抛错被吞：insert 仍被调用且不落库失败。

    复现 Task 7 缺陷：末题三项 context 指标以 ``pd.NA``（可空语义）出现时，
    旧实现 ``float(df[col].iloc[i])`` 抛 ``TypeError`` → 整个落库流程被外层
    ``except`` 吞掉、``eval_report`` 表静默无记录。修复后改走 ``pd.isna`` 守卫，
    ``insert_eval_report`` 必被调用，且该指标在 ``detail_json`` 中保留 NaN。
    """
    from unittest.mock import patch

    import pandas as pd

    from src.cli.eval_deepeval import _save_eval_report

    mock_df = pd.DataFrame(
        {
            "faithfulness": pd.Series([0.9, pd.NA], dtype="object"),
            "answer_relevancy": pd.Series([0.8, 0.7], dtype="object"),
            "context_precision": pd.Series([0.5, pd.NA], dtype="object"),
            "context_recall": pd.Series([0.6, pd.NA], dtype="object"),
            "answer_correctness": pd.Series([0.5, 0.4], dtype="object"),
        }
    )

    captured: dict = {}

    class _FakeSvc:
        """替代真实 AppService，只记录待插入实体（不发真实网络调用）。"""

        async def insert_eval_report(self, entity) -> None:
            """记录待插入实体。

            Args:
                entity: 由 _save_eval_report 组装的落库实体
            """
            captured["entity"] = entity

    async def _fake_run_and_dispose(coro):
        """直接 await 传入协程，替代真实引擎生命周期管理。

        Args:
            coro: 待执行的落库协程

        Returns:
            协程结果
        """
        return await coro

    with (
        patch("src.services.app_service.AppService", return_value=_FakeSvc()),
        patch("src.infra.db.engine.run_and_dispose", side_effect=_fake_run_and_dispose),
    ):
        _save_eval_report(
            "kb1", mock_df, 2, "/tmp/unused.csv", None, sample_classes=["pure_kb"] * 2
        )

    assert "entity" in captured
    entity = captured["entity"]
    # 末题：跳过指标以 NaN 落库，未跳过指标为原值
    assert pd.isna(entity.detail_json[1]["faithfulness"])
    assert entity.detail_json[1]["answer_relevancy"] == 0.7
    # 首题：正常值不受影响
    assert entity.detail_json[0]["faithfulness"] == 0.9


def test_gate_fails_when_retrieval_coverage_below_min() -> None:
    """检索覆盖率低于下限时门禁失败（否则会拿空 context 指标蒙混过关）。"""
    import pandas as pd

    from src.cli.eval_deepeval import check_gate
    from src.cli.eval_scoring import apply_sample_masks
    from src.config.const import SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_PURE_KB

    raw_df = pd.DataFrame(
        {
            "faithfulness": [0.95, 0.90, 0.90],
            "context_precision": [0.85, 0.90, 0.90],
            "context_recall": [0.75, 0.80, 0.80],
            "answer_relevancy": [0.90, 0.88, 0.88],
        }
    )
    # 1 个纯 KB / 3 样本 = 0.33 < 0.5；主分本身全部达标，仍须因覆盖率失败
    df = apply_sample_masks(
        raw_df,
        [SAMPLE_CLASS_PURE_KB, SAMPLE_CLASS_EMPTY, SAMPLE_CLASS_EMPTY],
    )

    with pytest.raises(SystemExit) as exc_info:
        check_gate(df, ["Q1", "Q2", "Q3"])
    assert exc_info.value.code == 1
