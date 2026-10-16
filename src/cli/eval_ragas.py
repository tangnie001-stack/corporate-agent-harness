"""RAGAS 评估脚本 — 对 RAG 系统的检索和生成质量进行标准化评估.

本脚本使用 RAGAS 库计算四个核心指标（报告 / stdout / 门禁统一采用「主分」口径，
即仅纯 KB 样本的指标均值；含联网或无检索的样本不计入主分）：
  - faithfulness: 回答是否忠实于检索到的上下文（有无幻觉）
  - answer_relevancy: 回答与问题的相关性
  - context_recall: 检索到的上下文是否覆盖了参考答案所需的信息
  - context_precision: 检索到的上下文中有多少是真正有用的

运行方式：
  python -m src.cli.eval_ragas --kb-id <kb-uuid>                          # 评估（最新测试集）
  python -m src.cli.eval_ragas --kb-id <kb-uuid> --testset-version 4       # 评估（指定测试集版本）
  python -m src.cli.eval_ragas --kb-id <kb-uuid> --gate                    # 评估并检查质量门禁
  python -m src.cli.eval_ragas --kb-id <kb-uuid> --generate                # 生成测试集
  python -m src.cli.eval_ragas --kb-id <kb-uuid> --generate --size 30      # 生成 30 条
  python -m src.cli.eval_ragas --list-kbs                                  # 列出可用知识库
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pandas as pd
from langfuse.decorators import langfuse_context, observe

from src.cli.eval_scoring import (
    apply_sample_masks,
    assert_behaviour_scores_complete,
    build_behaviour_metrics,
    build_metrics,
    classify_sample,
    main_score_means,
    nan_cell_stats,
    read_behaviour_scores,
    retrieval_coverage,
    summarize_trajectory,
    write_scores,
)
from src.config import settings
from src.config.const import (
    EVAL_PASS_THRESHOLD,
    EVAL_RETRIEVAL_COVERAGE_MIN,
    RAGAS_EVAL_CONCURRENCY,  # noqa: F401  # 打分并发预算留给 Phase 3，暂留符号
    RAGAS_METRIC_COLUMNS,
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.core.logging import setup_logging
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.infra.llm.trace_context import current_trace_id
from src.infra.llm.tracing import configure_tracing, flush_tracing

setup_logging(configure_trace_id=True)

# Quality gate thresholds
GATE_THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.85,
    "context_precision": 0.80,
    "context_recall": 0.70,
    "answer_relevancy": 0.85,
}


def parse_args() -> argparse.Namespace:
    """解析命令行参数."""
    parser = argparse.ArgumentParser(
        description="RAGAS 评估脚本 — 对 RAG 系统进行标准化评估",
    )
    parser.add_argument(
        "--kb-id",
        type=str,
        default=None,
        help="要评估的知识库 UUID（与 --list-kbs 互斥）",
    )
    parser.add_argument(
        "--list-kbs",
        action="store_true",
        help="列出所有可用知识库后退出",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="CSV 输出路径（默认: data/ragas/reports/ragas_eval_<timestamp>.csv）",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default="ragas_eval_session",
        help="评估用的会话 ID（默认: ragas_eval_session）",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="评估后检查质量门禁指标，任一不达标则退出码为 1",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="生成测试集模式（从文档自动生成 QA 对）",
    )
    parser.add_argument(
        "--testset-version",
        type=int,
        default=None,
        help="指定测试集版本号（默认取最新版本）",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="生成测试集的 QA 对数（默认: settings.RAGAS_TEST_SIZE）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="生成测试集用的 LLM 模型名（默认: RAGAS_LLM_MODEL 或 LLM_MODEL）",
    )
    return parser.parse_args()


@observe(name="eval_question", capture_input=False)
async def _answer_one_question(
    graph: Any,
    kb_id: str,
    session_id: str,
    query: str,
    trace_id: str,
    *,
    callbacks: list | None = None,
) -> tuple[str, list[str], list[dict], list]:
    """对单个问题跑一次图，返回 (answer, contexts, retrieval_details, messages)。

    trace 级输入显式写入 —— 与 `_run_generation` 同理，根函数入参含 graph 等
    内部对象，不能靠自动捕获。

    Args:
        graph: LangGraph 编译后的图实例
        kb_id: 知识库 UUID
        session_id: 会话 ID
        query: 该问题文本
        trace_id: 该问题的 trace id（仅用于写入 trace 级 metadata）

    Returns:
        (回答文本, 渲染后的上下文列表, 结构化检索明细, 状态中的消息列表)
    """
    langfuse_context.update_current_trace(input={"query": query}, session_id=session_id)
    # 仅在显式传入 callbacks 时附加 config；否则不传（保持既有路径行为不变）
    invoke_kwargs: dict[str, Any] = {}
    if callbacks:
        invoke_kwargs["config"] = {"callbacks": callbacks}
    final_state = await graph.ainvoke(
        {
            "kb_id": kb_id,
            "session_id": session_id,
            "query": query,
            "trace_id": trace_id,
            "_history": [],
        },
        **invoke_kwargs,
    )
    answer = final_state.get("answer", "")
    # 与生产 prompt（RAGContext.to_prompt_text）保持同一渲染格式，
    # 让 RAGAS 的 NLI 看到与生成模型一致的上下文（含来源/页码锚点）
    contexts = [c.to_prompt_text() for c in final_state.get("tool_contexts", [])]
    # 结构化检索明细（detail_json 下钻用：rerank 后来源+分数+类型）
    # getattr 兜底：tool_contexts 来自图状态，约定为 RAGContext，防御性
    # 容忍异常对象，保证明细字段不缺键
    details = [
        {
            "source": getattr(c, "source", ""),
            "score": getattr(c, "score", 0.0),
            "kind": getattr(c, "kind", "kb"),
        }
        for c in final_state.get("tool_contexts", [])
    ]
    # 轨迹诊断采集用：完整消息序列（工具名/入参/迭代数由此推导）
    messages = final_state.get("messages", [])
    return answer, contexts, details, messages


async def _collect_one_question(
    graph: Any,
    kb_id: str,
    session_id: str,
    query: str,
    trace_id: str,
    index: int,
    *,
    callbacks: list | None = None,
) -> tuple[str, list[str], list[dict], str, dict]:
    """对单个问题跑图，返回 (answer, contexts, details, sample_class, trajectory)。

    每题独立 seed RequestContext（F-32）：工具据此把材料写入 ctx.tool_contexts，
    收尾节点 agent_finalize 再回填 state.tool_contexts（否则 context 指标恒空）；
    无论成败都在 finally 复位 contextvar，防止样本间泄漏。异常不向上抛，而是返回
    与成功分支同形对齐的空占位（answer 记 `[ERROR]`），由调用方逐题收集。

    Args:
        graph: LangGraph 编译后的图实例
        kb_id: 知识库 UUID
        session_id: 会话 ID
        query: 该问题文本
        trace_id: 该问题的 trace id（写入 trace 级 metadata）
        index: 该问题在整批中的 1-based 序号（仅用于日志）

    Returns:
        (回答文本, 渲染后的上下文列表, 结构化检索明细, 样本类别, 轨迹摘要)
    """
    core_logging.log_event(Event.QA_ANSWER_START, index=index, query=query)
    # F-32：镜像 turn_runner，seed 请求上下文，使 retrieve_kb 等工具收集的
    # 材料可经 agent_finalize 回填 state.tool_contexts（否则 context 指标恒空）
    req_ctx = RequestContext(session_id=session_id, kb_id=kb_id)
    ctx_token = current_request_ctx.set(req_ctx)
    try:
        answer, ctx_list, details, messages = await _answer_one_question(
            graph,
            kb_id,
            session_id,
            query,
            trace_id,
            # @observe 包装器在调用前取走 langfuse_observation_id（静态签名看不到），
            # 类型检查无法感知该 kwarg
            langfuse_observation_id=trace_id,  # type: ignore[reportCallIssue]
            callbacks=callbacks,
        )
        sample_class = classify_sample(req_ctx.tool_contexts)
        trajectory = summarize_trajectory(messages)
        core_logging.log_event(
            Event.QA_ANSWER_DONE,
            index=index,
            answer_len=len(answer),
            contexts=len(ctx_list),
        )
        return answer, ctx_list, details, sample_class, trajectory
    except Exception as e:  # noqa: BLE001
        core_logging.log_event(Event.QA_ANSWER_FAILED, index=index, err=str(e))
        return (
            f"[ERROR] {e}",
            [],
            [],
            SAMPLE_CLASS_EMPTY,
            {"tool_calls": [], "iterations": 0},
        )
    finally:
        current_request_ctx.reset(ctx_token)


async def generate_answers_and_contexts(
    graph: Any,
    kb_id: str,
    session_id: str,
    questions: list[str],
) -> tuple[
    list[str], list[list[str]], list[str], list[list[dict]], list[str], list[dict]
]:
    """对每个问题生成回答，并收集检索到的上下文.

    Args:
        graph: LangGraph 编译后的图实例
        kb_id: 知识库 UUID
        session_id: 会话 ID
        questions: 问题列表

    Returns:
        (answers, contexts, trace_ids, retrieval_details, sample_classes,
        trajectories) 元组：
        - answers: 每个问题的完整回答文本列表
        - contexts: 每个问题对应的检索上下文列表（每个元素是文档片段列表）
        - trace_ids: 每个问题对应的 trace_id 列表（与 answers/contexts 一一对应，
          可用于在日志 / Langfuse 中回溯该问题的完整链路）
        - retrieval_details: 每个问题对应的结构化检索明细列表（与 answers/contexts
          一一对应；每个元素是该 query 最终上下文（rerank 后）的
          [{source, score, kind}, ...]，供 detail_json 低分下钻）
        - sample_classes: 每个问题按检索来源分的样本类别（见 eval_scoring.classify_sample）；
          异常分支记为 SAMPLE_CLASS_EMPTY
        - trajectories: 每个问题的轨迹诊断摘要（见 eval_scoring.summarize_trajectory，
          工具名 + 入参 + 迭代数，不含工具输出正文）；异常分支记为
          {"tool_calls": [], "iterations": 0}
    """
    answers: list[str] = []
    contexts: list[list[str]] = []
    trace_ids: list[str] = []
    retrieval_details: list[list[dict]] = []
    sample_classes: list[str] = []
    trajectories: list[dict] = []

    for i, q in enumerate(questions):
        # 每个问题一个独立 trace_id，并写入 contextvar —— loguru patcher 会
        # 把它注入到该问题期间的所有日志行，Langfuse trace 也以它为 id
        trace_id = f"eval_{uuid.uuid4().hex[:12]}"
        trace_ids.append(trace_id)
        trace_token = current_trace_id.set(trace_id)
        try:
            (
                answer,
                ctx_list,
                details,
                sample_class,
                trajectory,
            ) = await _collect_one_question(
                graph, kb_id, session_id, q, trace_id, i + 1
            )
        finally:
            current_trace_id.reset(trace_token)
        answers.append(answer)
        contexts.append(ctx_list)
        retrieval_details.append(details)
        sample_classes.append(sample_class)
        trajectories.append(trajectory)

    return (
        answers,
        contexts,
        trace_ids,
        retrieval_details,
        sample_classes,
        trajectories,
    )


def _build_behaviour_dataset(questions: list[str], ground_truth: list[str]):
    """构造 DeepEval 数据集：Golden(input=题面, expected_output=参考答案)。

    `expected_output` 供 `TaskCompletion` 判定任务是否完成；缺它会让该指标失真。
    """
    from deepeval.dataset import EvaluationDataset, Golden

    return EvaluationDataset(
        goldens=[
            # pyright 误报 multimodal 缺失：deepeval 用位置参数 `Field(False)`，
            # 插件不认位置默认值（运行时确有 default=False）
            Golden(input=q, expected_output=g)  # type: ignore[reportCallIssue]
            for q, g in zip(questions, ground_truth, strict=True)
        ]
    )


async def generate_answers_and_contexts_with_behaviour(
    graph: Any,
    kb_id: str,
    session_id: str,
    questions: list[str],
    ground_truth: list[str],
    behaviour_metrics: list,
    results_folder: str,
) -> tuple:
    """跑图循环由 `evals_iterator` 驱动（design D6 定案的唯一可用形态）。

    两半缺一不可：① 循环体在 iterator 内（否则不评分）；② 分数事后从
    `test_run_*.json` 回读（eval loop 用副本，`metric.score` 恒 None）。
    **每题新建独立 `CallbackHandler`**（复用会串 trace）；**循环不得 break**
    （generator 只有耗尽才落盘 JSON）。
    """
    from deepeval.evaluate.configs import DisplayConfig
    from deepeval.integrations.langchain import CallbackHandler

    ds = _build_behaviour_dataset(questions, ground_truth)
    answers: list[str] = []
    contexts: list[list[str]] = []
    trace_ids: list[str] = []
    retrieval_details: list[list[dict]] = []
    sample_classes: list[str] = []
    trajectories: list[dict] = []
    handlers: list = []

    for i, _golden in enumerate(
        ds.evals_iterator(
            metrics=behaviour_metrics,
            display_config=DisplayConfig(
                results_folder=results_folder, inspect_after_run=False
            ),
        )
    ):
        trace_id = f"eval_{uuid.uuid4().hex[:12]}"
        trace_ids.append(trace_id)
        handler = CallbackHandler()  # 每题独立实例
        handlers.append(handler)
        trace_token = current_trace_id.set(trace_id)
        try:
            (
                answer,
                ctx_list,
                details,
                sample_class,
                trajectory,
            ) = await _collect_one_question(
                graph,
                kb_id,
                session_id,
                questions[i],
                trace_id,
                i + 1,
                callbacks=[handler],
            )
        finally:
            current_trace_id.reset(trace_token)
        answers.append(answer)
        contexts.append(ctx_list)
        retrieval_details.append(details)
        sample_classes.append(sample_class)
        trajectories.append(trajectory)

    return (
        answers,
        contexts,
        trace_ids,
        retrieval_details,
        sample_classes,
        trajectories,
        handlers,
    )


class _EvalResult:
    """`.to_pandas()` 契约（产出层零改动）。

    串行逐题打分产出的行与输入同序（按 `q_index` 排列），本类持一个已按
    `q_index` 排好的 DataFrame，并保留既有 `.to_pandas()` 调用面。
    """

    def __init__(self, df: pd.DataFrame) -> None:
        """保存已排序的结果表。

        Args:
            df: 已按 q_index 排序并重置索引的结果表
        """
        self._df = df

    def to_pandas(self) -> pd.DataFrame:
        """返回已排序的结果表（与旧 `EvaluationResult.to_pandas()` 同形）。"""
        return self._df


def _bridge_test_case(row: dict):
    """把一行评测数据包成桥要的 LLMTestCase（四字段恒全部提供）。"""
    from deepeval.test_case import LLMTestCase

    return LLMTestCase(
        input=row["question"],
        actual_output=row["answer"],
        expected_output=row["ground_truth"],
        retrieval_context=list(row["contexts"]),
    )


def _exc_chain_summary(exc: BaseException, limit: int = 140) -> str:
    """把异常链压成单行摘要：`外层类<-根因类: 根因消息`。

    只记异常类名会丢掉根因（实测一次 150s 超时被包装成无消息的 `RuntimeError`，
    报告里只剩 `RuntimeError`，无法定位）。故把 `__cause__`/`__context__` 链一并带上，
    消息取**最内层**（根因）的、压成单行并截断。
    """
    names: list[str] = []
    root = exc
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        names.append(type(cur).__name__)
        root = cur
        cur = cur.__cause__ or cur.__context__
    msg = " ".join(str(root).split())[:limit]
    chain = "<-".join(names)
    return f"{chain}: {msg}" if msg else chain


def _score_metric_with_retry(
    metric_name, metric, test_case, row, evidence=None
) -> tuple[float, str]:
    """打分；**非掩码 NaN** 时重试至多 `settings.RAGAS_METRIC_MAX_ATTEMPTS` 次。

    为何判据是 NaN 而不是异常：桥调 `ragas.evaluate(...)` 时未传
    `raise_exceptions`（默认 False），`ragas/executor.py` 对**任何** Exception 都
    `return np.nan` ⇒ 桥不抛异常，旧的 `except Exception` 判据永不触发（design D4）。

    为何 429 不重试：ragas 框架层已对 `openai.RateLimitError`（HTTP 429）重试
    ≤10 次（`ragas/run_config.py:52` + `llms/base.py:325-335`），外层再叠会 10×3。

    为何不做"合法缺值 / 裁判失败"子分类：桥只回一个 float，HTTP 200 的 envelope
    恒为合法 JSON ⇒ 该分类不可实现（design D4，第 5 轮评审证实）。

    Returns:
        `(值, 错误说明)`；成功与"未被重试的 NaN"错误说明为 ""；
        重试耗尽时为 `"指标:原因"`。
    """
    attempts = max(1, settings.RAGAS_METRIC_MAX_ATTEMPTS)
    last_note = ""
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            value = metric.measure(test_case)
            note = ""
        except Exception as exc:  # noqa: BLE001  # 异常仅作留证，判据是 NaN
            value, note = float("nan"), _exc_chain_summary(exc)
        seconds = round(time.monotonic() - started, 1)

        is_nan = value is None or (isinstance(value, float) and math.isnan(value))
        core_logging.log_event(
            Event.JUDGE_CALL,
            metric=metric_name,
            q_index=row.get("q_index"),
            trace_id=row.get("trace_id"),
            attempt=attempt,
            seconds=seconds,
            ok=not is_nan,
            note=note,
        )
        if value is not None and not is_nan:
            return float(value), ""
        last_note = note or "nan"

        if evidence is not None and evidence.non_2xx and 429 in evidence.status_codes:
            return float("nan"), ""  # 429：外层不重试（ragas 层已重试）
        if attempt >= attempts:
            break
        core_logging.log_event(
            Event.METRIC_RETRY,
            metric=metric_name,
            q_index=row.get("q_index"),
            trace_id=row.get("trace_id"),
            reason=last_note,
        )
        time.sleep(0.5 * attempt)  # 原为 await asyncio.sleep；改同步后同义
    return float("nan"), f"{metric_name}:{last_note}"


def score_rows(rows: list[dict], metrics: dict, *, evidence=None) -> list[dict]:
    """**串行**逐题逐指标打分（并发=1）。

    为何串行：桥复用 `ragas.metrics` 的**模块级指标实例**，`ragas.evaluate`
    会在执行期把 `metrics[i].llm` 置为该次裁判、结束时复位 `None`，且
    `LangchainLLMWrapper` 会改写底层 `chat_model.temperature` ⇒ 并发会互踩、
    表现为间歇性错分（design D7）。并发预算留给"跑图"那一侧。

    Args:
        rows: 每题一行（含 q_index/trace_id/question/answer/ground_truth/contexts）。
        metrics: `eval_scoring.build_metrics` 产物（顺序即评分顺序）。
        evidence: 可选证据桶（本题评分前 `mark(trace_id)`，用于 429 判定）。

    Returns:
        与输入同序的行副本，追加四指标值与 `error`（最终失败时非空）。
    """
    out: list[dict] = []
    for row in rows:
        if evidence is not None:
            evidence.reset()
            evidence.mark(row.get("trace_id"))
        scored: dict = {"q_index": row["q_index"], "error": ""}
        errors: list[str] = []
        test_case = _bridge_test_case(row)
        for name, metric in metrics.items():
            value, err = _score_metric_with_retry(
                name, metric, test_case, row, evidence
            )
            scored[name] = value
            if err:
                errors.append(err)
        scored["error"] = ";".join(errors)
        out.append(scored)
    return out


def run_evaluation(
    questions, ground_truth, answers, contexts, metrics, trace_ids, *, evidence=None
) -> Any:
    """逐题打分并返回带 `.to_pandas()` 的结果（行序与输入一致）。"""
    rows = [
        {
            "q_index": i,
            "trace_id": t,
            "question": q,
            "answer": a,
            "ground_truth": g,
            "contexts": c,
        }
        for i, (q, a, g, c, t) in enumerate(
            zip(questions, answers, ground_truth, contexts, trace_ids, strict=True)
        )
    ]
    core_logging.log_event(Event.EVALUATION_START, samples=len(rows))
    scored = score_rows(rows, metrics, evidence=evidence)
    core_logging.log_event(Event.EVALUATION_DONE)
    df = pd.DataFrame(scored).sort_values("q_index").reset_index(drop=True)
    return _EvalResult(df)


def save_results_csv(
    result: Any,
    questions: list[str],
    ground_truth: list[str],
    output_path: str,
    sample_classes: list[str],
    trace_ids: list[str] | None = None,
    trajectories: list[dict] | None = None,
) -> str:
    """将评估结果保存为 CSV 文件（含样本类别、跳过标记与轨迹列）.

    先按样本类别掩码（无材料样本的三项 context 指标置 NaN 并记 skipped），
    使落盘结果与「主分」口径一致。

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        ground_truth: 参考答案列表
        output_path: 输出文件路径
        sample_classes: 每个问题的样本类别（与 questions 一一对应）；
            必填——掩码需按类别定位样本，缺省无法写出一致口径的结果
        trace_ids: 每个问题对应的 trace_id（与 questions 一一对应），
            传入后在 CSV 中新增 trace_id 列，便于按 trace 回溯
        trajectories: 每个问题的轨迹诊断摘要（与 questions 一一对应），
            传入后新增 trajectory 列（JSON 字符串）

    Returns:
        实际写入的文件路径
    """
    df = result.to_pandas()

    df = apply_sample_masks(df, sample_classes)

    # 添加元信息列
    df.insert(0, "question", questions)
    df.insert(1, "ground_truth", ground_truth)
    if trace_ids is not None:
        df.insert(2, "trace_id", trace_ids)
    if trajectories is not None:
        df["trajectory"] = [json.dumps(t, ensure_ascii=False) for t in trajectories]

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df.to_csv(output_path, index=True, encoding="utf-8-sig")
    core_logging.log_event(Event.RESULTS_SAVED, file=output_path)
    return output_path


def _build_markdown_table(
    df: pd.DataFrame,
    metric_cols: list[str],
    trace_ids: list[str] | None,
    trajectories: list[dict] | None,
) -> list[str]:
    """渲染 MD 报告的逐题表格（表头 + 分隔行 + 每题一行）.

    Args:
        df: 已掩码的结果表（可能含 sample_class / skipped 列）
        metric_cols: 实际存在的指标列（按固定顺序）
        trace_ids: 每题的 trace_id（None 则不展示该列）
        trajectories: 每题的轨迹摘要（None 则不展示 trajectory 列）

    Returns:
        Markdown 表格行列表
    """
    header = ["Question"] + metric_cols
    if trace_ids is not None:
        header.append("trace_id")
    for col in ("sample_class", "skipped"):
        if col in df.columns:
            header.append(col)
    if trajectories is not None:
        header.append("trajectory")

    lines = ["| " + " | ".join(header) + " |"]
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for i in range(len(df)):
        vals = [f"Q{i + 1}"] + [f"{df[m].iloc[i]:.4f}" for m in metric_cols]
        if trace_ids is not None:
            if i < len(trace_ids):
                vals.append(trace_ids[i])
            else:
                vals.append("")
        for col in ("sample_class", "skipped"):
            if col in df.columns:
                vals.append(str(df[col].iloc[i]))
        if trajectories is not None:
            if i < len(trajectories):
                vals.append(json.dumps(trajectories[i], ensure_ascii=False))
            else:
                vals.append("")
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _build_behaviour_section(behaviour_rows: list[dict]) -> list[str]:
    """渲染「Agent 行为轴」段：逐题 input + 各行为指标分。

    行为轴仅作诊断观测、**不进主分/门禁**；指标列按首次出现顺序收集
    （各题可能因裁判失败缺某些指标，缺失记为 N/A）。无任何指标名时
    返回空列表（不写段）。

    Args:
        behaviour_rows: 逐题行为分（见 eval_scoring.read_behaviour_scores 返回形状）

    Returns:
        Markdown 行列表（含段标题与表格）
    """
    metric_names: list[str] = []
    for row in behaviour_rows:
        for name in row.get("metrics") or {}:
            if name not in metric_names:
                metric_names.append(name)
    if not metric_names:
        return []

    lines = ["## Agent 行为轴", ""]
    lines.append("> 行为轴仅作诊断观测，**不进主分/门禁**。")
    lines.append("")
    header = ["Question"] + metric_names
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for i, row in enumerate(behaviour_rows):
        vals = [str(row.get("input") or f"Q{i + 1}")]
        for name in metric_names:
            value = (row.get("metrics") or {}).get(name)
            if isinstance(value, (int, float)):
                vals.append(f"{value:.4f}")
            else:
                vals.append("N/A")
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def save_markdown_report(
    result: Any,
    questions: list[str],
    output_path: str,
    sample_classes: list[str],
    trace_ids: list[str] | None = None,
    trajectories: list[dict] | None = None,
    report_meta: dict | None = None,
    behaviour_rows: list[dict] | None = None,
) -> str:
    """将评估结果保存为 Markdown 摘要报告，与 CSV 同路径（.md 后缀）.

    均值行统一写「主分」（仅纯 KB 样本的指标均值），与 CSV / stdout / 门禁
    口径一致；先按样本类别掩码，报告携带 sample_class / skipped 列。

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        output_path: CSV 输出路径（用于推导 .md 路径）
        sample_classes: 每个问题的样本类别（与 questions 一一对应）；
            必填——主分均值行依赖 sample_class 列，缺省无法计算
        trace_ids: 每个问题对应的 trace_id（与 questions 一一对应），
            传入后在表格中新增 trace_id 列
        trajectories: 每个问题的轨迹诊断摘要（与 questions 一一对应），
            传入后新增 trajectory 列（JSON 字符串）
        report_meta: 报告元数据（见 build_report_meta），传入后在报告头写入
            口径版本 / 裁判 / 温度档 / 版本 / NaN 计数
        behaviour_rows: 逐题行为分（见 eval_scoring.read_behaviour_scores），
            传入后在报告末尾追加「Agent 行为轴」段；该轴仅作诊断观测，
            **不进主分/门禁**

    Returns:
        实际写入的 .md 文件路径
    """
    md_path = output_path.rsplit(".", 1)[0] + ".md"
    df = result.to_pandas()
    df = apply_sample_masks(df, sample_classes)

    actual_metrics = [c for c in RAGAS_METRIC_COLUMNS if c in df.columns]

    cfg_topk = settings.TOP_K_RETRIEVAL
    cfg_rerank = settings.TOP_K_RERANK

    lines: list[str] = []
    lines.append("# RAGAS Evaluation Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"**Configuration:** TOP_K_RETRIEVAL={cfg_topk}, TOP_K_RERANK={cfg_rerank}"
    )
    lines.append(f"**QA Pairs:** {len(questions)}")
    if report_meta is not None:
        lines.append(
            "**Caliber / Judge:** "
            f"caliber={report_meta['caliber']}, "
            f"judge={report_meta['judge_model']}, "
            f"model_arg_explicit={report_meta['model_arg_explicit']}"
        )
        regime = report_meta["temperature_regime"]
        lines.append(
            "**Temperature regime:** "
            f"n1_metrics={regime['n1_metrics']}, "
            f"answer_relevancy={regime['answer_relevancy']} "
            f"({report_meta['temperature_control']})"
        )
        versions = report_meta["runner_versions"]
        lines.append(
            "**Runner versions:** "
            f"ragas={versions['ragas']}, deepeval={versions['deepeval']}"
        )
        lines.append(
            "**NaN cells:** "
            f"{report_meta['nan_cells']}/{report_meta['nan_denominator_cells']} "
            f"(rate={report_meta['nan_rate']})"
        )
    lines.append("")

    lines.extend(_build_markdown_table(df, actual_metrics, trace_ids, trajectories))
    lines.append("")

    # 均值行统一写主分（纯 KB 样本均值），与 CSV / stdout / 门禁口径一致；
    # 只取白名单指标（actual_metrics），避免额外数值列混入均值行
    means = main_score_means(df)
    avg_parts = ", ".join(f"{m}={means[m]:.4f}" for m in actual_metrics if m in means)
    lines.append(f"**Main scores (pure-KB):** {avg_parts}")
    lines.append("")

    if behaviour_rows is not None:
        lines.extend(_build_behaviour_section(behaviour_rows))
        lines.append("")

    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    content = "\n".join(lines)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    core_logging.log_event(Event.REPORT_SAVED, file=md_path)
    return md_path


def check_gate(df: pd.DataFrame, questions: list[str]) -> None:
    """按主分（纯 KB 样本）判质量门禁；覆盖率不足或主分为空即失败.

    门禁口径统一为主分：先因「纯 KB 样本数为 0」或「检索覆盖率 <
    EVAL_RETRIEVAL_COVERAGE_MIN」判失败（杜绝"不检索即跳空 context 指标"的
    规避），再逐指标比对 GATE_THRESHOLDS。任一不达标即 exit 1。

    Args:
        df: 经 apply_sample_masks 掩码、含 sample_class 列的结果表
        questions: 问题列表（沿用既有门禁签名；主分判据不再逐题定位失败样本）
    """
    classes = list(df["sample_class"])
    cov = retrieval_coverage(classes)
    pure_kb_count = 0
    for c in classes:
        if c == SAMPLE_CLASS_PURE_KB:
            pure_kb_count += 1

    if pure_kb_count == 0:
        print("  gate FAIL: no pure-KB samples (main score undefined)")
        sys.exit(1)
    if cov < EVAL_RETRIEVAL_COVERAGE_MIN:
        print(
            f"  gate FAIL: retrieval coverage {cov:.2f} < {EVAL_RETRIEVAL_COVERAGE_MIN}"
        )
        sys.exit(1)

    stats = nan_cell_stats(df)
    rate = stats["nan_rate"]
    from src.config.const import EVAL_NAN_RATE_MAX

    print(
        f"  NaN: {stats['nan_cells']}/{stats['denominator_cells']} 格 "
        f"({rate:.2%}) 阈值 {EVAL_NAN_RATE_MAX:.0%}"
    )
    if rate > EVAL_NAN_RATE_MAX:
        print("  gate FAIL: NaN 率超阈值 —— 建议启用逃生门（见 design D10）")
        sys.exit(1)

    means = main_score_means(df)
    all_pass = True
    for metric, threshold in GATE_THRESHOLDS.items():
        if metric not in means:
            print(f"  {metric}: N/A")
            all_pass = False
            continue
        score = means[metric]
        passed = score >= threshold
        if passed:
            print(f"  {metric}: {score:.4f} PASS")
        else:
            print(f"  {metric}: {score:.4f} FAIL")
            all_pass = False

    if not all_pass:
        sys.exit(1)

    sys.exit(0)


def build_report_meta(
    *, judge_model: str, nan_stats: dict, runner_versions: dict
) -> dict:
    """报告/台账元数据（口径版本、温度档、NaN 计数、模型显式性）。

    温度**不由本项目控制**（ragas 按 n 分档：n=1 → 0.01；answer_relevancy
    n=3 → 0.3），故只记录事实，SHALL NOT 出现「temperature=0」的表述。
    """
    return {
        "caliber": "bridge-0.3.9",
        "judge_model": judge_model,
        "model_arg_explicit": True,
        "temperature_regime": {"n1_metrics": 0.01, "answer_relevancy": 0.3},
        "temperature_control": "ragas_decides_by_n",
        "nan_cells": nan_stats["nan_cells"],
        "nan_denominator_cells": nan_stats["denominator_cells"],
        "nan_rate": round(nan_stats["nan_rate"], 4),
        "runner_versions": dict(runner_versions),
    }


def main() -> None:
    """主入口 — 路由到生成模式或评估模式。"""
    args = parse_args()

    # ---- list-kbs 模式 ----
    if args.list_kbs:
        asyncio.run(_list_knowledge_bases())
        return

    # ---- 解析 kb_id ----
    if not args.kb_id:
        print("error: --kb-id 是必填参数（使用 --list-kbs 查看可用知识库）")
        sys.exit(1)
    kb_id = args.kb_id

    # ---- generate 模式 ----
    if args.generate:
        from src.cli.eval_ragas_generate import run_generate

        size = args.size or settings.RAGAS_TEST_SIZE
        model = args.model or ""
        run_generate(kb_id, size, model)
        return

    # ---- 评估模式（原有流程改造）----
    # CLI 不经 main.py 的 lifespan：开关与 flush 都要自己做（D5 / D6）。
    # configure 必须早于任何 flush（承接 Task 2 义务）：SDK 的 flush 经
    # LangfuseSingleton 惰性构造客户端，先 flush 会造出 enabled 客户端、
    # 绕过 settings.LANGFUSE_ENABLE。
    configure_tracing()
    try:
        session_id = args.session_id
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_path = (
            args.output or f"{settings.RAGAS_REPORT_DIR}/ragas_eval_{timestamp}.csv"
        )

        # 从 JSON 加载测试集
        from src.cli.eval_ragas_generate import _load_latest_testset

        try:
            questions, ground_truth = _load_latest_testset(
                kb_id, version=args.testset_version
            )
        except FileNotFoundError as e:
            print(f"error: {e}")
            sys.exit(1)

        core_logging.log_event(Event.TESTSET_LOADED, count=len(questions))
        core_logging.log_event(Event.EVALUATION_RUN, kb_id=kb_id)

        # ---- 初始化 RAG 组件 ----
        # ragas 0.4.3 + langchain-community>=0.4 兼容：先建 vertexai stub 再导入 ragas
        from src.cli.eval_ragas_generate import _ensure_vertexai_stub

        _ensure_vertexai_stub()

        from src.agents.graph.workflow import build_graph
        from src.infra.db.engine import run_and_dispose
        from src.infra.db.vector_store import VectorStore
        from src.infra.llm.prompt_manager import PromptManager
        from src.models import get_llm, get_rerank

        core_logging.log_event(Event.RAG_COMPONENT_INIT)
        vector_store = VectorStore()

        # 选手/裁判分离：
        # - 选手（被测系统）= 生产模型 get_llm()（LLM_MODEL），评估结果反映线上质量
        # - 裁判（评估器）  = judge 角色绑定（RAGAS_LLM_MODEL 可覆盖），独立于选手
        #   评分，避免自我评价偏置；模型名空/None 时由 registry 兜底。
        llm = get_llm()  # 选手：生产模型（含生产 temperature）
        reranker = get_rerank()
        # 裁判走 provider 层桥工厂（langchain chat model + http_async_client 细粒度
        # 超时 + 请求级观测），embeddings 复用既有 build_embeddings()；证据桶跨题复用，
        # 每题的 mark/reset 在 score_rows 内做。桥裁判全程唯一实例：EVAL_MODEL_INIT
        # 日志与报告元数据（judge_model）均取自它，不再构造 temperature=0 的旧裁判。
        from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM
        from src.infra.llm.provider.factory import build_bridge_judge, build_embeddings
        from src.infra.llm.provider.http_observability import HookEvidence

        judge_evidence = HookEvidence()
        bridge_judge = build_bridge_judge(evidence=judge_evidence)
        metrics = build_metrics(bridge_judge, build_embeddings())
        core_logging.log_event(
            Event.EVAL_MODEL_INIT,
            sut=settings.LLM_MODEL,
            judge=bridge_judge.model_name,
        )

        # 行为轴：BridgeDeepEvalLLM 复用 provider 层裁判（细粒度超时 + 请求级 hooks），
        # 行为指标按 T2 三项构造；behaviour_out 用与报告同源的 timestamp 建独立目录，
        # 分数事后从 test_run_*.json 回读（eval loop 用副本，metric.score 恒 None）。
        behaviour_eval_llm = BridgeDeepEvalLLM(bridge_judge)
        behaviour_metrics = build_behaviour_metrics(behaviour_eval_llm)
        behaviour_out = str(Path(settings.RAGAS_REPORT_DIR) / f"behaviour_{timestamp}")

        # 构建 LangGraph
        prompt_manager = PromptManager()
        graph = build_graph(vector_store, llm, reranker, prompt_manager)

        async def _ensure_store_and_generate():
            """空库检查与答案生成共用一个事件循环。

            引擎连接池不可跨事件循环复用，故把两次存储访问（分块读取 + 图内检索）
            放在同一次 asyncio.run 内；返回 None 表示知识库无分块。
            """
            chunks = await vector_store.get_all_chunks(kb_id)
            if not chunks:
                return None
            return await generate_answers_and_contexts_with_behaviour(
                graph,
                kb_id,
                session_id,
                questions,
                ground_truth,
                behaviour_metrics,
                behaviour_out,
            )

        core_logging.log_event(Event.VECTOR_STORE_CHECK, kb_id=kb_id)
        core_logging.log_event(Event.ANSWERS_GENERATION, count=len(questions))
        # 本文件仅生成段这处 asyncio.run 驱动 `evals_iterator`，依赖
        # nest_asyncio.apply()——DeepEval 内层 run_until_complete 在同一 loop 重入。
        # 这是进程级 monkeypatch（改全局 asyncio 行为），仅因 eval CLI 是短命进程可接受。
        generated = asyncio.run(run_and_dispose(_ensure_store_and_generate()))
        if generated is None:
            core_logging.log_event(Event.VECTOR_STORE_EMPTY, kb_id=kb_id)
            print("Knowledge base is empty")
            sys.exit(1)
        (
            answers,
            contexts,
            trace_ids,
            retrieval_details,
            sample_classes,
            trajectories,
            _handlers,
        ) = generated
        # 生成段结束即 flush：--gate 路径会提前 sys.exit，晚 flush 等于丢数据
        flush_tracing()

        # 行为分事后回读 + fail-loud（题数/分数对齐、轨迹非空、hooks 确实触发）。
        # hooks 断言必须在 run_evaluation 之前：score_rows 会逐题 reset 证据桶，
        # 晚查会因 events 被清空而误判「未触发」。
        behaviour_rows = read_behaviour_scores(behaviour_out)
        assert_behaviour_scores_complete(questions, behaviour_rows, trajectories)
        if not any(e.get("phase") == "response" for e in judge_evidence.events):
            raise RuntimeError(
                "行为轴 hooks 未触发：judge_evidence 无 response 事件"
                "（可能走了同步分支，行为分不可信）"
            )

        result = run_evaluation(
            questions,
            ground_truth,
            answers,
            contexts,
            metrics,
            trace_ids,
            evidence=judge_evidence,
        )

        # 统一「主分」口径：报告 / stdout / 门禁都读同一张已掩码表（纯 KB 样本）。
        masked_df = apply_sample_masks(result.to_pandas(), sample_classes)

        # 报告/台账元数据：口径版本、温度档、NaN 计数、裁判版本与模型显式性。
        # judge_model 复用上方 EVAL_MODEL_INIT 同源的 bridge_judge.model_name；
        # 温度由 ragas 按 n 分档、本项目不可控，故只记录事实（见 build_report_meta）。
        report_meta = build_report_meta(
            judge_model=bridge_judge.model_name,
            nan_stats=nan_cell_stats(masked_df),
            runner_versions={
                "ragas": version("ragas"),
                "deepeval": version("deepeval"),
            },
        )

        # 指标均值输出到 stdout：compare_retrieval 等 A/B 脚本以
        # 子进程方式调用本脚本，靠 stdout 解析指标（parse_metrics）；logger 只写
        # 文件不落 stdout，故此处显式 print。--gate 模式下 check_gate 已打印指标行。
        if not args.gate:
            _print_metric_averages(masked_df)

        output_path = save_results_csv(
            result,
            questions,
            ground_truth,
            output_path,
            sample_classes=sample_classes,
            trace_ids=trace_ids,
            trajectories=trajectories,
        )
        save_markdown_report(
            result,
            questions,
            output_path,
            sample_classes=sample_classes,
            trace_ids=trace_ids,
            trajectories=trajectories,
            report_meta=report_meta,
            behaviour_rows=behaviour_rows,
        )

        # 行序契约：write_scores 按 enumerate 下标把 masked_df 第 i 行写到
        # trace_ids[i]，两序列必须等长同序（均由 generate_answers_and_contexts
        # 按题序产出）。行数不一致即错配，会把分数静默写到别的 trace 上，故此处
        # 快速失败而非带病回写。用显式 raise 而非 assert：`python -O` 会剥离
        # assert，护栏在内置优化模式下失效。
        if len(masked_df) != len(trace_ids):
            raise RuntimeError(
                f"eval rows ({len(masked_df)}) != trace_ids ({len(trace_ids)})"
            )

        # 先落库：DB 写入不受第三方观测系统（Langfuse）影响，故排在回写之前
        # _save_eval_report 需要 questions 长度
        _save_eval_report(
            kb_id,
            result.to_pandas(),
            len(questions),
            output_path,
            retrieval_details,
            sample_classes,
        )

        # 观测旁路回写：排在落库之后、门禁之前（失败只记 warning，见 write_scores）
        _write_langfuse_scores(masked_df, trace_ids)

        if args.gate:
            check_gate(masked_df, questions)

        core_logging.log_event(Event.EVALUATION_DONE)
    finally:
        # 异常退出 / --gate 的 sys.exit 路径也须 flush，否则缓冲事件丢失
        flush_tracing()


def _print_metric_averages(df: pd.DataFrame) -> None:
    """把主分（纯 KB 样本均值）逐指标打印到 stdout（供 A/B 子进程解析）.

    compare_retrieval 通过 subprocess 调用本脚本并用 parse_metrics 解析
    stdout 里的指标均值；logger 只写日志文件，因此均值需显式 print。
    口径与 check_gate / 报告一致，均为主分；**只打 `RAGAS_METRIC_COLUMNS`
    白名单内的指标**（防止额外数值列混入 stdout），每指标只打一行，行格式
    `指标名: 数值`，便于按指标名前缀匹配取值。

    Args:
        df: 经 apply_sample_masks 掩码、含 sample_class 列的结果表
    """
    means = main_score_means(df)
    for metric in RAGAS_METRIC_COLUMNS:
        if metric not in means:
            continue
        print(f"  {metric}: {means[metric]:.4f}")


def _write_langfuse_scores(masked_df: pd.DataFrame, trace_ids: list[str]) -> None:
    """把已掩码表的逐题指标分数按 trace_id 显式回写 Langfuse（观测旁路）。

    评分在批量 evaluate() 之后才产出、逐题 @observe 已关闭，故回写必须显式定位
    trace，不能依赖「当前 trace」的隐式上下文。值取已掩码表（空材料样本的
    context 指标为 NaN=跳过语义，write_scores 内跳过不写）；列取指标白名单，
    避免额外数值列混入。回写失败（客户端获取或逐条写）由 write_scores 吞掉，
    不影响评测主流程。

    Args:
        masked_df: 经 apply_sample_masks 掩码、含 sample_class 列的结果表
        trace_ids: 每题的 trace_id（行序与 masked_df 对齐，已由调用方校验）
    """
    write_scores(
        trace_ids,
        {
            col: list(masked_df[col])
            for col in RAGAS_METRIC_COLUMNS
            if col in masked_df.columns
        },
        enabled=settings.LANGFUSE_ENABLE,
    )


def _build_eval_detail(
    df: pd.DataFrame,
    qa_count: int,
    retrieval_details: list[list[dict]] | None,
) -> tuple[list[dict], dict[str, float]]:
    """构建逐题明细与各指标均值（跳过值 NaN 安全；列取指标白名单）。

    跳过值（`NaN` / `pd.NA` / `None`）一律规整为 `float("nan")`：直接 `float()`
    会在 `pd.NA`/`None` 上抛 `TypeError`，被 `_save_eval_report` 外层 `except`
    吞掉 → 整份报告静默不落库。

    Args:
        df: RAGAS 结果表
        qa_count: QA 对数
        retrieval_details: 每 query 的检索明细（与 detail 按 q_index 对齐），
            传入后并入对应明细条目，供低分 query 下钻检索环节

    Returns:
        (detail, avg)：detail 为逐题条目列表，avg 为指标名 -> 均值
    """
    metric_cols = [col for col in RAGAS_METRIC_COLUMNS if col in df.columns]

    detail: list[dict] = []
    for i in range(qa_count):
        entry = {"q_index": i, "question": ""}
        for col in metric_cols:
            # pandas 存根把 .iloc[i] 推断为 Series/Unknown，显式标注标量
            v: Any = df[col].iloc[i]
            if pd.isna(v):
                entry[col] = float("nan")
            else:
                entry[col] = float(v)
        if retrieval_details is not None and i < len(retrieval_details):
            entry["retrieval_details"] = retrieval_details[i]
        detail.append(entry)

    avg: dict[str, float] = {}
    for col in metric_cols:
        # 同上：显式标注以消除 pandas 存根的 Series/Unknown 误判
        m: Any = df[col].mean()
        if pd.isna(m):
            avg[col] = float("nan")
        else:
            avg[col] = float(m)
    return detail, avg


def _eval_report_deps() -> tuple[Any, Any]:
    """DB 落库依赖（独立函数便于测试替身注入）。"""
    from src.infra.db.models.eval_report import EvalReportModel as EvalReportEntity
    from src.services.app_service import AppService

    return AppService(), EvalReportEntity


def _save_eval_report(
    kb_id: str,
    df: pd.DataFrame,
    qa_count: int,
    output_path: str,
    retrieval_details: list[list[dict]] | None = None,
    sample_classes: list[str] | None = None,
) -> None:
    """把评估结果持久化到 eval_report 表。

    `overall_score` 统一到**主分口径**（纯 KB 四项均值，design D11），替换原先
    「四项加权、未掩码」的旧口径；`passed` 取常量 `EVAL_PASS_THRESHOLD`。
    传入 `sample_classes` 时先按样本类别掩码，使落库的逐题明细与各指标均值
    同为主分口径。

    Args:
        kb_id: 知识库 UUID
        df: RAGAS 结果表（行序与 sample_classes 对齐）
        qa_count: QA 对数
        output_path: CSV 报告文件路径
        retrieval_details: 每 query 的检索明细（与 detail 数组按 q_index 对齐），
            传入后并入对应 detail_json 条目，供低分 query 下钻检索环节
        sample_classes: 每题的样本类别；传入后先掩码再算主分与明细
    """
    try:
        from src.cli import eval_scoring
        from src.infra.db.engine import run_and_dispose

        svc, eval_report_entity = _eval_report_deps()
        if sample_classes is not None:
            df = eval_scoring.apply_sample_masks(df, sample_classes)
        detail, _avg = _build_eval_detail(df, qa_count, retrieval_details)

        means = eval_scoring.main_score_means(df)
        present = [v for v in means.values() if pd.notna(v)]
        overall = float(sum(present) / len(present)) if present else None

        async def _do_insert():
            entity = eval_report_entity(
                id=str(uuid.uuid4()),
                kb_id=kb_id,
                run_type="manual",
                qa_count=qa_count,
                faithfulness=means.get("faithfulness"),
                answer_relevancy=means.get("answer_relevancy"),
                context_precision=means.get("context_precision"),
                context_recall=means.get("context_recall"),
                overall_score=overall,
                passed=overall is not None and overall >= EVAL_PASS_THRESHOLD,
                report_path=output_path,
                triggered_by=None,
                detail_json=detail,
            )
            await svc.insert_eval_report(entity)

        asyncio.run(run_and_dispose(_do_insert()))
        core_logging.log_event(Event.EVAL_REPORT_SAVED, kb_id=kb_id)
    except Exception as e:  # noqa: BLE001
        core_logging.log_event(Event.EVAL_SAVE_FAILED, err=str(e))


async def _list_knowledge_bases() -> None:
    """列出 MySQL 中所有知识库的名称和文档数."""
    from src.config import RAGAS_USER_ID
    from src.services.app_service import AppService

    svc = AppService()

    # 获取所有知识库（含 doc_count）
    kbs = await svc._kb_repo.get_all_kb(RAGAS_USER_ID)
    if not kbs:
        print("No knowledge bases found.")
        return

    print("\nAvailable knowledge bases:")
    print("-" * 40)
    for kb in kbs:
        print(f"  {kb.name:<30} ({kb.doc_count} documents)")
    print()


if __name__ == "__main__":
    main()
