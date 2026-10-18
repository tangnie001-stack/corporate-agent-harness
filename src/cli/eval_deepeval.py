"""DeepEval 评测脚本 — 对 RAG 系统的检索和生成质量进行标准化评估.

本脚本用 DeepEval 原生四指标打分（报告 / stdout / 门禁统一采用「主分」口径，
即仅纯 KB 样本的指标均值；含联网或无检索的样本不计入主分）：
  - faithfulness: 回答是否忠实于检索到的上下文（有无幻觉）
  - answer_relevancy: 回答与问题的相关性
  - context_recall: 检索到的上下文是否覆盖了参考答案所需的信息
  - context_precision: 检索到的上下文中有多少是真正有用的

运行方式：
  python -m src.cli.eval_deepeval --kb-id <kb-uuid>                          # 评估（最新测试集）
  python -m src.cli.eval_deepeval --kb-id <kb-uuid> --testset-version 4       # 评估（指定测试集版本）
  python -m src.cli.eval_deepeval --kb-id <kb-uuid> --gate                    # 评估并检查质量门禁
  python -m src.cli.eval_deepeval --list-kbs                                  # 列出可用知识库

出题生成入口已下线（`--generate` 保留参数、命中即报错）。
"""

import argparse
import asyncio
import json
import math
import os
import random
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
    build_diagnostic_metrics,
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
    EVAL_METRIC_COLUMNS,
    EVAL_PASS_THRESHOLD,
    EVAL_RELEVANCY_MAX_CONTEXTS,
    EVAL_RETRIEVAL_COVERAGE_MIN,
    LANGFUSE_SCORE_SUFFIX,
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


def _limit_testset(
    questions: list[str], ground_truth: list[str], limit: int | None
) -> tuple[list[str], list[str]]:
    """按冒烟轮次截取前 `limit` 题；`None` 表示不截断。

    仅用于逐级活体（1 → 5 → 21）。`limit` 超过题数时原样返回，便于同一命令
    在不同规模的题集上复用；`limit <= 0` 是调用错误，直接失败。

    Args:
        questions: 题面列表
        ground_truth: 参考答案列表（与 questions 等长同序）
        limit: 保留的题数；None 表示全量

    Returns:
        (questions, ground_truth) 的截断副本或原对象

    Raises:
        ValueError: `limit` 非正
    """
    if limit is None:
        return questions, ground_truth
    if limit <= 0:
        raise ValueError(f"--limit 必须为正整数，得到 {limit}")
    if limit >= len(questions):
        return questions, ground_truth
    return questions[:limit], ground_truth[:limit]


def parse_args() -> argparse.Namespace:
    """解析命令行参数."""
    parser = argparse.ArgumentParser(
        description="DeepEval 评测脚本 — 对 RAG 系统进行标准化评估",
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
        help="CSV 输出路径（默认: data/deepeval/reports/deepeval_eval_<timestamp>.csv）",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default="deepeval_eval_session",
        help="评估用的会话 ID（默认: deepeval_eval_session）",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="评估后检查质量门禁指标，任一不达标则退出码为 1",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="已下线（保留参数，命中即报错并指向 DeepEval Synthesizer）",
    )
    parser.add_argument(
        "--testset-version",
        type=int,
        default=None,
        help="指定测试集版本号（默认取最新版本）",
    )
    parser.add_argument(
        "--deepeval-only",
        "--behaviour-only",
        action="store_true",
        dest="deepeval_only",
        help="只跑 Agent 行为轴，跳过四指标（无主分/门禁；不写 eval_report）",
    )
    parser.add_argument(
        "--run-type",
        choices=("manual", "baseline"),
        default="manual",
        help="运行类型：baseline=新口径首轮（不判门禁），manual=常规回归",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只跑前 N 题（冒烟用；勿与 --gate 同用）",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="生成测试集的 QA 对数（出题已下线，保留参数、当前无效果）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="生成测试集用的 LLM 模型名（出题已下线，保留参数、当前无效果）",
    )
    args = parser.parse_args()
    if args.deepeval_only and args.gate:
        parser.error("--deepeval-only 与 --gate 不能同用：该模式没有主分可判")
    if args.run_type == "baseline" and args.gate:
        parser.error("--run-type baseline 与 --gate 不能同用：baseline 不判门禁")
    return args


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
    # 让裁判看到与生成模型一致的上下文（含来源/页码锚点）
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
        # `err` 只记 str(e)：超时类异常的 str 可能为空（如 TimeoutError()），
        # 单凭它无法定位；补类型与 repr 作为排障证据（字段见 EVENT_SPECS）。
        core_logging.log_event(
            Event.QA_ANSWER_FAILED,
            index=index,
            err=str(e),
            err_type=type(e).__name__,
            err_repr=repr(e)[:200],
        )
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
    """**生产死代码**：生产入口是 `generate_answers_and_contexts_with_behaviour`
    （行为轴驱动）；本函数仅被测试使用，保留以复用其"逐题 seed RequestContext +
    工具产出收集"的参考实现。新增生产调用请走 `_with_behaviour` 版本。

    对每个问题生成回答，并收集检索到的上下文.

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


def _bridge_test_case(row: dict, *, max_contexts: int | None = None):
    """把一行评测数据包成 LLMTestCase；可选截断 retrieval_context。

    Args:
        row: 含 question / answer / ground_truth / contexts 的行
        max_contexts: 仅诊断指标使用；None 表示使用全量 contexts

    Returns:
        LLMTestCase：四字段恒全部提供
    """
    from deepeval.test_case import LLMTestCase

    contexts = list(row["contexts"])
    if max_contexts is not None:
        contexts = contexts[:max_contexts]
    return LLMTestCase(
        input=row["question"],
        actual_output=row["answer"],
        expected_output=row["ground_truth"],
        retrieval_context=contexts,
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


def _retry_delay(attempt: int, evidence, event_start: int = 0) -> float:
    """计算第 attempt 次失败后的等待秒数。

    只扫描 `evidence.events[event_start:]`（本次 attempt 产生的事件），避免把
    前序指标 / 前序 attempt 留下的 429 套到本次无关失败上。命中 429 时优先采用
    响应头 `Retry-After`（仅接受有限且 >= 0 的数值，上限 30s）；否则指数退避
    + 0~0.25s jitter，上限 8s。

    Args:
        attempt: 1-based 尝试序号
        evidence: 可选证据桶（含 response 事件）；None 表示无观测
        event_start: 本次 attempt 的起始事件下标；之前的事件不参与判定

    Returns:
        等待秒数（非负）
    """
    if evidence is not None:
        for event in reversed(evidence.events[event_start:]):
            if event.get("status") != 429:
                continue
            retry_after = event.get("retry_after")
            if retry_after is None:
                continue
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(delay) or delay < 0:
                continue
            return min(delay, 30.0)
    base = min(0.5 * (2 ** (attempt - 1)), 8.0)
    return base + random.uniform(0.0, 0.25)


def _score_metric_with_retry(
    metric_name,
    metric,
    test_case,
    row,
    evidence=None,
    *,
    max_attempts: int | None = None,
) -> tuple[float, str]:
    """打分；**非掩码 NaN** 时重试至多 `settings.EVAL_METRIC_MAX_ATTEMPTS` 次。

    为何判据是 NaN 而不是异常：度量失败在本函数内统一归为 NaN（异常文本只作
    `note` 留证），重试与「缺值」因此走同一判据（design D4）。

    为何 429 由外层重试：桥的裁判 client 不再内置重试，限流（429）与其它失败
    同一判据（非掩码 NaN）进入本层重试；退避优先取响应的 `Retry-After`，
    缺失时指数退避 + jitter（见 `_retry_delay`），避免短间隔连打重试放大限流。

    为何不做"合法缺值 / 裁判失败"子分类：指标只回一个 float，HTTP 200 的
    envelope 恒为合法 JSON ⇒ 该分类不可实现（design D4，第 5 轮评审证实）。

    Args:
        metric_name: 指标名（仅用于日志与错误串）
        metric: 指标实例（需实现 `measure(test_case)`）
        test_case: 待评用例
        row: 当前行（取 q_index / trace_id 记日志）
        evidence: 可选证据桶（退避时读取 429 的 `Retry-After`）
        max_attempts: 显式重试次数；None 时取 `settings.EVAL_METRIC_MAX_ATTEMPTS`

    Returns:
        `(值, 错误说明)`；成功与"未被重试的 NaN"错误说明为 ""；
        重试耗尽时为 `"指标:原因"`。
    """
    if max_attempts is None:
        attempts = max(1, settings.EVAL_METRIC_MAX_ATTEMPTS)
    else:
        attempts = max(1, max_attempts)
    last_note = ""
    for attempt in range(1, attempts + 1):
        if evidence is None:
            event_start = 0
        else:
            event_start = len(evidence.events)
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

        if attempt >= attempts:
            break
        core_logging.log_event(
            Event.METRIC_RETRY,
            metric=metric_name,
            q_index=row.get("q_index"),
            trace_id=row.get("trace_id"),
            reason=last_note,
        )
        time.sleep(_retry_delay(attempt, evidence, event_start))
    return float("nan"), f"{metric_name}:{last_note}"


def score_rows(
    rows: list[dict], metrics: dict, *, diagnostic_metrics=None, evidence=None
) -> list[dict]:
    """**串行**逐题逐指标打分（并发=1），诊断指标走独立通道。

    为何串行：四项与诊断指标共用同一组指标实例与裁判，`measure` 期间会写
    实例自身状态（score / reason / verbose_logs）并共用证据桶 ⇒ 并发会互踩、
    表现为间歇性错分（design D7）。并发预算留给"跑图"那一侧。

    `ContextualRelevancy` 单独构造截断后的 test_case，共享 test_case 保持全量
    contexts；`empty` 样本不调用任何指标。

    Args:
        rows: 每题一行（含 q_index / trace_id / question / answer /
            ground_truth / contexts / sample_class）
        metrics: `build_metrics` 产物（顺序即评分顺序）
        diagnostic_metrics: `build_diagnostic_metrics` 产物；不进主分
        evidence: 可选证据桶（本题评分前 `mark(trace_id)`）

    Returns:
        与输入同序的行副本，追加四指标、诊断值、`tool_correctness` 与 `error`
    """
    from src.config.const import EVAL_RELEVANCY_MAX_CONTEXTS

    out: list[dict] = []
    for row in rows:
        if evidence is not None:
            evidence.reset()
            evidence.mark(row.get("trace_id"))
        scored: dict = {"q_index": row["q_index"], "error": ""}
        errors: list[str] = []

        if row.get("sample_class") == SAMPLE_CLASS_EMPTY:
            for name in metrics:
                scored[name] = float("nan")
            if diagnostic_metrics is not None:
                for name in diagnostic_metrics:
                    scored[name] = float("nan")
            scored["tool_correctness"] = None
            scored["error"] = "sample_class=empty"
            out.append(scored)
            continue

        test_case = _bridge_test_case(row)
        for name, metric in metrics.items():
            value, err = _score_metric_with_retry(
                name, metric, test_case, row, evidence
            )
            scored[name] = value
            if err:
                errors.append(err)

        if diagnostic_metrics is not None:
            relevancy = diagnostic_metrics.get("contextual_relevancy")
            if relevancy is not None:
                relevancy_case = _bridge_test_case(
                    row, max_contexts=EVAL_RELEVANCY_MAX_CONTEXTS
                )
                value, err = _score_metric_with_retry(
                    "contextual_relevancy",
                    relevancy,
                    relevancy_case,
                    row,
                    evidence,
                    max_attempts=1,
                )
                scored["contextual_relevancy"] = value
                if err:
                    errors.append(err)
            non_advice = diagnostic_metrics.get("non_advice")
            if non_advice is not None:
                value, err = _score_metric_with_retry(
                    "non_advice",
                    non_advice,
                    test_case,
                    row,
                    evidence,
                    max_attempts=1,
                )
                scored["non_advice"] = value
                if err:
                    errors.append(err)

        scored["tool_correctness"] = None
        scored["error"] = ";".join(errors)
        out.append(scored)
    return out


# `_EvalResult` 与 `_exc_chain_summary` 是当前桥路径的**活件**（非现代栈残留）：
# 前者承载产出层 `.to_pandas()` 契约，后者承载评分失败的异常链留证。
def run_evaluation(
    questions,
    ground_truth,
    answers,
    contexts,
    metrics,
    trace_ids,
    *,
    evidence=None,
    diagnostic_metrics=None,
    sample_classes=None,
) -> _EvalResult:
    """逐题打分并返回带 `.to_pandas()` 的结果（行序与输入一致）。

    本函数是评测**评分后端边界**（逃生门接入点，design D10）：`metrics` 由调用方注入，
    替代实现只需提供同签名的 `metrics`（及用例构造）即可替换，无需改这里的编排。

    Args:
        questions: 题面列表
        ground_truth: 参考答案列表
        answers: 被测系统回答列表
        contexts: 每题检索上下文列表
        metrics: 主分指标（`build_metrics` 产物）
        trace_ids: 每题 trace_id
        evidence: 可选证据桶（退避时读取 429 的 `Retry-After`）
        diagnostic_metrics: 诊断指标（`build_diagnostic_metrics` 产物）；不进主分
        sample_classes: 每题样本类别；None 表示不分类

    Returns:
        带 `.to_pandas()` 的结果对象（行序与输入一致）
    """
    rows = []
    for i, (q, a, g, c, t) in enumerate(
        zip(questions, answers, ground_truth, contexts, trace_ids, strict=True)
    ):
        sample_class = None
        if sample_classes is not None:
            sample_class = sample_classes[i]
        rows.append(
            {
                "q_index": i,
                "trace_id": t,
                "question": q,
                "answer": a,
                "ground_truth": g,
                "contexts": c,
                "sample_class": sample_class,
            }
        )
    core_logging.log_event(Event.EVALUATION_START, samples=len(rows))
    scored = score_rows(
        rows,
        metrics,
        diagnostic_metrics=diagnostic_metrics,
        evidence=evidence,
    )
    core_logging.log_event(Event.EVALUATION_DONE)
    df = pd.DataFrame(scored).sort_values("q_index").reset_index(drop=True)
    return _EvalResult(df)


def save_results_csv(
    result: _EvalResult,
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
        result: 评测结果（`_EvalResult`；仅默认四指标路径产出）
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
        df: 已掩码的结果表（可能含 sample_class / skipped / error 列）
        metric_cols: 实际存在的指标列（按固定顺序）
        trace_ids: 每题的 trace_id（None 则不展示该列）
        trajectories: 每题的轨迹摘要（None 则不展示 trajectory 列）

    Returns:
        Markdown 表格行列表
    """
    header = ["Question"] + metric_cols
    if "error" in df.columns:
        header.append("error")
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
        if "error" in df.columns:
            # 异常消息可能含 "|"，会破坏 Markdown 列，按行为轴同款口径归一化
            error_text = " ".join(str(df["error"].iloc[i]).split()).replace("|", "/")
            vals.append(error_text)
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


def _build_diagnostic_section(df: pd.DataFrame) -> list[str]:
    """渲染「诊断指标」段；诊断不进主分/门禁。

    段内含逐题表、纯 KB 诊断均值与**独立的**纯 KB 诊断 NaN 计数——
    诊断 NaN 不与四指标 NaN 合并统计，也不进入 NaN 率门禁。

    Args:
        df: 经 apply_sample_masks 处理的结果表

    Returns:
        Markdown 行列表；无诊断列时返回空列表
    """
    from src.config.const import DIAGNOSTIC_METRIC_COLUMNS

    cols = [c for c in DIAGNOSTIC_METRIC_COLUMNS if c in df.columns]
    if not cols:
        return []

    lines = ["## 诊断指标（不进主分/门禁）", ""]
    lines.append(
        f"> ContextualRelevancy 仅判前 {EVAL_RELEVANCY_MAX_CONTEXTS} 条 context"
        f"（并集顺序，非最相关 {EVAL_RELEVANCY_MAX_CONTEXTS} 条）。"
    )
    lines.append("")
    header = ["Question"] + cols
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for i in range(len(df)):
        vals = [f"Q{i + 1}"]
        for col in cols:
            value = df[col].iloc[i]
            if pd.isna(value):
                vals.append("N/A")
            else:
                vals.append(f"{value:.4f}")
        lines.append("| " + " | ".join(vals) + " |")

    pure = df[df["sample_class"] == SAMPLE_CLASS_PURE_KB]
    parts: list[str] = []
    for col in cols:
        # 显式标注标量，消除 pandas 存根的 Series/Unknown 误判
        mean: Any = pure[col].mean()
        if pd.isna(mean):
            parts.append(f"{col}=NaN")
        else:
            parts.append(f"{col}={mean:.4f}")
    lines.append("")
    lines.append(f"**Diagnostic means (pure-KB):** {', '.join(parts)}")

    nan_parts: list[str] = []
    for col in cols:
        # 显式标注，避免 pandas 存根把列访问推断为 NDArray
        series: Any = pure[col]
        nan_count = int(series.isna().sum())
        nan_parts.append(f"{col}={nan_count}/{len(series)}")
    lines.append(f"**Diagnostic NaN (pure-KB):** {', '.join(nan_parts)}")
    return lines


def _build_behaviour_section(behaviour_rows: list[dict]) -> list[str]:
    """渲染「Agent 行为轴」段：逐题 input + 各行为指标分。

    行为轴仅作诊断观测、**不进主分/门禁**；指标列按首次出现顺序收集
    （各题可能因裁判失败缺某些指标，缺失记为 N/A）。无任何指标名时
    返回空列表（不写段）。

    指标缺分时以 test_run JSON 的 `reason` 摘要标注（design R9：行为轴不走 hooks
    逐题归因，失败证据由 JSON 承载）。

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
        reasons = row.get("reasons") or {}
        for name in metric_names:
            value = (row.get("metrics") or {}).get(name)
            if isinstance(value, (int, float)):
                vals.append(f"{value:.4f}")
            else:
                note = " ".join(str(reasons.get(name) or "").split()).replace("|", "/")[
                    :60
                ]
                if note:
                    vals.append(f"N/A（{note}）")
                else:
                    vals.append("N/A")
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def save_markdown_report(
    result: _EvalResult | None,
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
        result: 评测结果对象；`None` 表示四指标未跑（--deepeval-only），
            此时只写报告头与行为轴，不写逐题指标表与主分行
        questions: 问题列表
        output_path: CSV 输出路径（用于推导 .md 路径）
        sample_classes: 每个问题的样本类别（与 questions 一一对应）；
            必填——主分均值行依赖 sample_class 列，缺省无法计算
        trace_ids: 每个问题对应的 trace_id（与 questions 一一对应），
            传入后在表格中新增 trace_id 列
        trajectories: 每个问题的轨迹诊断摘要（与 questions 一一对应），
            传入后新增 trajectory 列（JSON 字符串）
        report_meta: 报告元数据（见 build_report_meta），传入后在报告头写入
            口径版本 / 裁判 / 运行器版本 / NaN 计数
        behaviour_rows: 逐题行为分（见 eval_scoring.read_behaviour_scores），
            传入后在报告末尾追加「Agent 行为轴」段；该轴仅作诊断观测，
            **不进主分/门禁**

    Returns:
        实际写入的 .md 文件路径
    """
    md_path = output_path.rsplit(".", 1)[0] + ".md"

    cfg_topk = settings.TOP_K_RETRIEVAL
    cfg_rerank = settings.TOP_K_RERANK

    lines: list[str] = []
    lines.append("# Deepeval Evaluation Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"**Configuration:** TOP_K_RETRIEVAL={cfg_topk}, TOP_K_RERANK={cfg_rerank}"
    )
    lines.append(f"**QA Pairs:** {len(questions)}")
    if report_meta is not None:
        versions = report_meta["runner_versions"]
        if report_meta.get("ragas_skipped"):
            lines.append(
                "**Main score:** 未跑（--deepeval-only）——本报告只含 Agent 行为轴，"
                "无主分、无门禁语义"
            )
            lines.append(
                "**Runner versions:** "
                f"deepeval={versions['deepeval']}, metrics={versions['metrics']}"
            )
        else:
            lines.append(
                "**Caliber / Judge:** "
                f"caliber={report_meta['caliber']}, "
                f"judge={report_meta['judge_model']}, "
                f"model_arg_explicit={report_meta['model_arg_explicit']}"
            )
            lines.append(
                "**Runner versions:** "
                f"deepeval={versions['deepeval']}, metrics={versions['metrics']}"
            )
            lines.append(
                "**NaN cells:** "
                f"{report_meta['nan_cells']}/{report_meta['nan_denominator_cells']} "
                f"(rate={report_meta['nan_rate']})"
            )
    lines.append("")

    if result is not None:
        df = apply_sample_masks(result.to_pandas(), sample_classes)
        actual_metrics = [c for c in EVAL_METRIC_COLUMNS if c in df.columns]
        lines.extend(_build_markdown_table(df, actual_metrics, trace_ids, trajectories))
        lines.append("")

        # 均值行统一写主分（纯 KB 样本均值），与 CSV / stdout / 门禁口径一致；
        # 只取白名单指标（actual_metrics），避免额外数值列混入均值行
        means = main_score_means(df)
        avg_parts = ", ".join(
            f"{m}={means[m]:.4f}" for m in actual_metrics if m in means
        )
        lines.append(f"**Main scores (pure-KB):** {avg_parts}")
        lines.append("")
        lines.extend(_build_diagnostic_section(df))
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
    *,
    judge_model: str,
    nan_stats: dict | None,
    runner_versions: dict,
    ragas_enabled: bool = True,
) -> dict:
    """报告/台账元数据（口径版本、NaN 计数、模型显式性）。

    `ragas_enabled=False`（--deepeval-only）时四指标未跑，不产出 NaN 字段，
    报告头显式标注「主分未跑」。

    Args:
        judge_model: 裁判模型名
        nan_stats: `nan_cell_stats` 的结果；`ragas_enabled=True` 时不得为 None
        runner_versions: 版本字典，形如 `{"deepeval": "4.2.9", "metrics": "native"}`
        ragas_enabled: 是否跑四指标（False 即 --deepeval-only）

    Returns:
        报告元数据字典
    """
    if not ragas_enabled:
        return {
            "caliber": "deepeval-only",
            "judge_model": judge_model,
            "model_arg_explicit": True,
            "ragas_skipped": True,
            "runner_versions": dict(runner_versions),
        }
    if nan_stats is None:
        raise ValueError("ragas_enabled=True 时 nan_stats 不得为 None")
    return {
        "caliber": "deep-eval-native",
        "judge_model": judge_model,
        "model_arg_explicit": True,
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

    # ---- generate 模式（已下线：硬护栏，design D8 / spec「The testset generation path
    #      is guarded against accidental invocation」）----
    # 出题链已整体删除，此处显式拦下并给出前迁路径，SHALL NOT 让用户撞到裸 ImportError。
    if args.generate:
        print(
            "error: --generate 已下线。出题由 change deepeval-synthesizer-testset 提供，"
            "使用 DeepEval Synthesizer：\n"
            "  python -m src.cli.testset_synth --kb-id <uuid> --version <n> --docs <文件...>"
        )
        sys.exit(1)

    # ---- 评估模式（原有流程改造）----
    # --deepeval-only：只跑行为轴；四指标整段（度量/评分/主分表/CSV/落库/回写/门禁）跳过。
    ragas_enabled = not args.deepeval_only
    # CLI 不经 main.py 的 lifespan：开关与 flush 都要自己做（D5 / D6）。
    # configure 必须早于任何 flush（承接 Task 2 义务）：SDK 的 flush 经
    # LangfuseSingleton 惰性构造客户端，先 flush 会造出 enabled 客户端、
    # 绕过 settings.LANGFUSE_ENABLE。
    configure_tracing()
    try:
        session_id = args.session_id
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_path = (
            args.output or f"{settings.EVAL_REPORT_DIR}/deepeval_eval_{timestamp}.csv"
        )

        # 从 JSON 加载测试集
        from src.cli.testset_io import _load_latest_testset

        try:
            questions, ground_truth = _load_latest_testset(
                kb_id, version=args.testset_version
            )
        except FileNotFoundError as e:
            print(f"error: {e}")
            sys.exit(1)

        questions, ground_truth = _limit_testset(questions, ground_truth, args.limit)

        core_logging.log_event(Event.TESTSET_LOADED, count=len(questions))
        core_logging.log_event(Event.EVALUATION_RUN, kb_id=kb_id)

        # ---- 初始化 RAG 组件 ----
        from src.agents.graph.workflow import build_graph
        from src.infra.db.engine import run_and_dispose
        from src.infra.db.vector_store import VectorStore
        from src.infra.llm.prompt_manager import PromptManager
        from src.models import get_llm, get_rerank

        core_logging.log_event(Event.RAG_COMPONENT_INIT)
        vector_store = VectorStore()

        # 选手/裁判分离：
        # - 选手（被测系统）= 生产模型 get_llm()（LLM_MODEL），评估结果反映线上质量
        # - 裁判（评估器）  = judge 角色绑定（JUDGE_MODEL 可覆盖），独立于选手
        #   评分，避免自我评价偏置；模型名空/None 时由 registry 兜底。
        llm = get_llm()  # 选手：生产模型（含生产 temperature）
        reranker = get_rerank()
        # 裁判走 provider 层桥工厂（langchain chat model + http_async_client 细粒度
        # 超时 + 请求级观测）；证据桶跨题复用，每题的 mark/reset 在 score_rows 内做。
        # 四指标与行为轴共用同一个 BridgeDeepEvalLLM：原生指标要求裁判是
        # DeepEvalBaseLLM，且全程唯一实例才能让 EVAL_MODEL_INIT 日志、报告元数据
        # （judge_model）与 hooks 归因指向同一裁判。
        from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM
        from src.infra.llm.provider.factory import build_bridge_judge
        from src.infra.llm.provider.http_observability import HookEvidence

        judge_evidence = HookEvidence()
        bridge_judge = build_bridge_judge(evidence=judge_evidence)
        judge_llm = BridgeDeepEvalLLM(bridge_judge)
        metrics = None
        diagnostic_metrics = None
        if ragas_enabled:
            metrics = build_metrics(judge_llm)
            diagnostic_metrics = build_diagnostic_metrics(judge_llm)
        core_logging.log_event(
            Event.EVAL_MODEL_INIT,
            sut=settings.LLM_MODEL,
            judge=bridge_judge.model_name,
        )

        # 行为轴：复用同一 BridgeDeepEvalLLM（细粒度超时 + 请求级 hooks），
        # 行为指标按 T2 三项构造；behaviour_out 用与报告同源的 timestamp 建独立目录，
        # 分数事后从 test_run_*.json 回读（eval loop 用副本，metric.score 恒 None）。
        behaviour_metrics = build_behaviour_metrics(judge_llm)
        behaviour_out = str(Path(settings.EVAL_REPORT_DIR) / f"behaviour_{timestamp}")

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

        # 四指标产物：--deepeval-only 下不产出（均为 None），下游段按开关收敛。
        result: _EvalResult | None = None
        masked_df: pd.DataFrame | None = None
        if ragas_enabled:
            result = run_evaluation(
                questions,
                ground_truth,
                answers,
                contexts,
                metrics,
                trace_ids,
                evidence=judge_evidence,
                diagnostic_metrics=diagnostic_metrics,
                sample_classes=sample_classes,
            )

            # 统一「主分」口径：报告 / stdout / 门禁都读同一张已掩码表（纯 KB 样本）。
            masked_df = apply_sample_masks(result.to_pandas(), sample_classes)

            # 指标均值输出到 stdout：compare_retrieval 等 A/B 脚本以
            # 子进程方式调用本脚本，靠 stdout 解析指标（parse_metrics）；logger 只写
            # 文件不落 stdout，故此处显式 print。--gate 模式下 check_gate 已打印指标行。
            if not args.gate:
                _print_metric_averages(masked_df)

        # 报告/台账元数据：口径版本、NaN 计数、裁判版本与模型显式性。
        # judge_model 复用上方 EVAL_MODEL_INIT 同源的 bridge_judge.model_name；
        # --deepeval-only 下 nan_stats 恒为 None，元数据改标 ragas_skipped。
        nan_stats: dict | None = None
        if masked_df is not None:
            nan_stats = nan_cell_stats(masked_df)
        report_meta = build_report_meta(
            judge_model=bridge_judge.model_name,
            nan_stats=nan_stats,
            runner_versions={
                "deepeval": version("deepeval"),
                "metrics": "native",
            },
            ragas_enabled=ragas_enabled,
        )

        if ragas_enabled:
            assert result is not None  # 仅供类型收窄
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

        if ragas_enabled:
            # 上方同一开关已赋值，此处仅供类型收窄
            assert result is not None and masked_df is not None
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
                run_type=args.run_type,
            )

            # 观测旁路回写：排在落库之后、门禁之前（失败只记 warning，见 write_scores）
            _write_langfuse_scores(masked_df, trace_ids, behaviour_rows)

        if args.gate:
            # --deepeval-only 与 --gate 在 parse_args 层互斥，故 masked_df 必非 None
            assert masked_df is not None  # 仅供类型收窄
            check_gate(masked_df, questions)

        core_logging.log_event(Event.EVALUATION_DONE)
    finally:
        # 异常退出 / --gate 的 sys.exit 路径也须 flush，否则缓冲事件丢失
        flush_tracing()


def _print_metric_averages(df: pd.DataFrame) -> None:
    """把主分（纯 KB 样本均值）逐指标打印到 stdout（供 A/B 子进程解析）.

    compare_retrieval 通过 subprocess 调用本脚本并用 parse_metrics 解析
    stdout 里的指标均值；logger 只写日志文件，因此均值需显式 print。
    口径与 check_gate / 报告一致，均为主分；**只打 `EVAL_METRIC_COLUMNS`
    白名单内的指标**（防止额外数值列混入 stdout），每指标只打一行，行格式
    `指标名: 数值`，便于按指标名前缀匹配取值。

    Args:
        df: 经 apply_sample_masks 掩码、含 sample_class 列的结果表
    """
    means = main_score_means(df)
    for metric in EVAL_METRIC_COLUMNS:
        if metric not in means:
            continue
        print(f"  {metric}: {means[metric]:.4f}")


def _write_langfuse_scores(
    masked_df: pd.DataFrame,
    trace_ids: list[str],
    behaviour_rows: list[dict] | None = None,
) -> None:
    """把已掩码表的逐题指标分数（+ 行为轴）按 trace_id 显式回写 Langfuse（观测旁路）。

    评分在批量 evaluate() 之后才产出、逐题 @observe 已关闭，故回写必须显式定位
    trace，不能依赖「当前 trace」的隐式上下文。值取已掩码表（空材料样本的
    context 指标为 NaN=跳过语义，write_scores 内跳过不写）；列取指标白名单，
    避免额外数值列混入。回写失败（客户端获取或逐条写）由 write_scores 吞掉，
    不影响评测主流程。行数不齐是程序不变量被破坏（分数会写到别的 trace），故此处 fail-loud，不属"回写失败"的吞异常范围。

    score 名统一带口径后缀（`LANGFUSE_SCORE_SUFFIX`，design D9）：新口径与历史同名
    score 并存、SHALL NOT 覆盖；行为轴指标与四项同级、共用同一 trace_id；
    NaN 由 write_scores 跳过（SHALL NOT 写 0）。

    Args:
        masked_df: 经 apply_sample_masks 掩码、含 sample_class 列的结果表
        trace_ids: 每题的 trace_id（行序与 masked_df / behaviour_rows 对齐）
        behaviour_rows: 逐题行为分（见 read_behaviour_scores）；传入后一并回写。
            行数必须与 trace_ids 等长，否则快速失败（防分数写到别的 trace）
    """
    scores: dict[str, list] = {
        f"{col}{LANGFUSE_SCORE_SUFFIX}": list(masked_df[col])
        for col in EVAL_METRIC_COLUMNS
        if col in masked_df.columns
    }
    if behaviour_rows is not None:
        if len(behaviour_rows) != len(trace_ids):
            raise RuntimeError(
                f"行为轴行数 ({len(behaviour_rows)}) != trace_ids ({len(trace_ids)})"
            )
        names: list[str] = []
        for row in behaviour_rows:
            for name in row.get("metrics") or {}:
                if name not in names:
                    names.append(name)
        for name in names:
            scores[f"{name}{LANGFUSE_SCORE_SUFFIX}"] = [
                (row.get("metrics") or {}).get(name) for row in behaviour_rows
            ]
    write_scores(trace_ids, scores, enabled=settings.LANGFUSE_ENABLE)


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
        df: 评测结果表
        qa_count: QA 对数
        retrieval_details: 每 query 的检索明细（与 detail 按 q_index 对齐），
            传入后并入对应明细条目，供低分 query 下钻检索环节

    Returns:
        (detail, avg)：detail 为逐题条目列表（含诊断键与恒 None 的
        tool_correctness），avg 为指标名 -> 均值
    """
    from src.config.const import DIAGNOSTIC_METRIC_COLUMNS

    metric_cols = [col for col in EVAL_METRIC_COLUMNS if col in df.columns]
    diag_cols = [col for col in DIAGNOSTIC_METRIC_COLUMNS if col in df.columns]

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
        for col in diag_cols:
            v: Any = df[col].iloc[i]
            if pd.isna(v):
                entry[col] = float("nan")
            else:
                entry[col] = float(v)
        entry["tool_correctness"] = None
        if "error" in df.columns:
            entry["error"] = str(df["error"].iloc[i])
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
    run_type: str = "manual",
) -> None:
    """把评估结果持久化到 eval_report 表。

    `overall_score` 统一到**主分口径**（纯 KB 四项均值，design D11），替换原先
    「四项加权、未掩码」的旧口径；`passed` 取常量 `EVAL_PASS_THRESHOLD`。
    `run_type=baseline` 时 `passed` 强制为 `False`（新口径阈值未标定，不判红/绿）。
    传入 `sample_classes` 时先按样本类别掩码，使落库的逐题明细与各指标均值
    同为主分口径。

    Args:
        kb_id: 知识库 UUID
        df: 评测结果表（行序与 sample_classes 对齐）
        qa_count: QA 对数
        output_path: CSV 报告文件路径
        retrieval_details: 每 query 的检索明细（与 detail 数组按 q_index 对齐），
            传入后并入对应 detail_json 条目，供低分 query 下钻检索环节
        sample_classes: 每题的样本类别；传入后先掩码再算主分与明细
        run_type: 运行类型（manual=常规回归，baseline=新口径首轮）；
            下游据此决定是否展示门禁结论
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

        passed_value: bool
        if run_type == "baseline":
            passed_value = False
        else:
            passed_value = overall is not None and overall >= EVAL_PASS_THRESHOLD

        async def _do_insert():
            entity = eval_report_entity(
                id=str(uuid.uuid4()),
                kb_id=kb_id,
                run_type=run_type,
                qa_count=qa_count,
                faithfulness=means.get("faithfulness"),
                answer_relevancy=means.get("answer_relevancy"),
                context_precision=means.get("context_precision"),
                context_recall=means.get("context_recall"),
                overall_score=overall,
                passed=passed_value,
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
    from src.config import EVAL_USER_ID
    from src.services.app_service import AppService

    svc = AppService()

    # 获取所有知识库（含 doc_count）
    kbs = await svc._kb_repo.get_all_kb(EVAL_USER_ID)
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
