"""RAGAS 评估脚本 — 对 RAG 系统的检索和生成质量进行标准化评估.

本脚本使用 RAGAS 库计算四个核心指标：
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
import os
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from langfuse.decorators import langfuse_context, observe

from src.cli.eval_scoring import classify_sample, summarize_trajectory
from src.config import settings
from src.config.const import SAMPLE_CLASS_EMPTY
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
    final_state = await graph.ainvoke(
        {
            "kb_id": kb_id,
            "session_id": session_id,
            "query": query,
            "trace_id": trace_id,
            "_history": [],
        }
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


def run_evaluation(
    questions: list[str],
    ground_truth: list[str],
    answers: list[str],
    contexts: list[list[str]],
    llm_wrapper: Any,
    embeddings_wrapper: Any,
) -> Any:
    """运行 RAGAS 四指标评估.

    每个指标内部会多次调用 LLM（非简单的一次一问一答），
    因此评估耗时明显长于回答生成：
      - faithfulness: 将回答拆成陈述句后逐句判断，N+1 次 LLM
      - answer_relevancy: 从回答反向生成候选项问题，M 次 LLM
      - context_recall: 逐条判断上下文是否覆盖参考答案，P 次 LLM
      - context_precision: 逐条判断上下文是否与问题相关，Q 次 LLM
    综上，评估阶段 LLM 调用次数 = 指标内调用次数 × 指标数 × QA 对数。

    Args:
        questions: 问题列表
        ground_truth: 参考答案列表
        answers: 系统生成的回答列表
        contexts: 检索到的上下文列表
        llm_wrapper: RAGAS 用 LLM 封装器（LangchainLLMWrapper 实例）
        embeddings_wrapper: RAGAS 用 Embeddings 封装器（LangchainEmbeddingsWrapper 实例）

    Returns:
        包含评估结果的 EvaluationResult 对象（可调用 .to_pandas() 转为 DataFrame）
    """
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        AnswerCorrectness,
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )
    from ragas.run_config import RunConfig

    data = {
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truth,
    }
    dataset = Dataset.from_dict(data)

    core_logging.log_event(Event.EVALUATION_START, samples=len(questions))
    # ragas.evaluate 无类型标注（推断为 Executor），实际返回 EvaluationResult，显式标注 Any
    # 默认 RunConfig.timeout=180s，长答案的 faithfulness/answer_relevancy 会超时
    # 返回 nan（raise_exceptions=False 吞掉 TimeoutError），故放宽到 10 分钟
    result: Any = evaluate(
        dataset=dataset,
        llm=llm_wrapper,
        embeddings=embeddings_wrapper,
        metrics=[
            context_precision,
            context_recall,
            faithfulness,
            answer_relevancy,
            AnswerCorrectness(),  # 实例化；类会 TypeError。列映射 ground_truth→reference
        ],
        raise_exceptions=False,
        run_config=RunConfig(timeout=600),
    )

    df = result.to_pandas()
    core_logging.log_event(Event.EVALUATION_DONE)
    for col in df.columns:
        if col in [
            "faithfulness",
            "answer_relevancy",
            "context_recall",
            "context_precision",
        ]:
            core_logging.log_event(
                Event.METRIC_MEAN,
                metric=col,
                mean=round(float(df[col].mean()), 4),
            )

    # 显式暴露被吞掉的指标失败：ragas executor 在 raise_exceptions=False 时
    # 会把异常（如 API 额度耗尽 403 / 超时）转成 nan，并记入 "Exception raised
    # in Job[...]" 日志。这里汇总提示，避免 nan 被静默带进报告。
    metric_cols = [
        "faithfulness",
        "answer_relevancy",
        "context_recall",
        "context_precision",
    ]
    nan_counts = {
        col: int(df[col].isna().sum())
        for col in metric_cols
        if col in df.columns and df[col].isna().any()
    }
    if nan_counts:
        core_logging.log_event(Event.METRICS_NAN, counts=nan_counts)

    return result


def save_results_csv(
    result: Any,
    questions: list[str],
    ground_truth: list[str],
    output_path: str,
    trace_ids: list[str] | None = None,
) -> str:
    """将评估结果保存为 CSV 文件.

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        ground_truth: 参考答案列表
        output_path: 输出文件路径
        trace_ids: 每个问题对应的 trace_id（与 questions 一一对应），
            传入后在 CSV 中新增 trace_id 列，便于按 trace 回溯

    Returns:
        实际写入的文件路径
    """
    df = result.to_pandas()

    # 添加元信息列
    df.insert(0, "question", questions)
    df.insert(1, "ground_truth", ground_truth)
    if trace_ids is not None:
        df.insert(2, "trace_id", trace_ids)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df.to_csv(output_path, index=True, encoding="utf-8-sig")
    core_logging.log_event(Event.RESULTS_SAVED, file=output_path)
    return output_path


def save_markdown_report(
    result: Any,
    questions: list[str],
    output_path: str,
    trace_ids: list[str] | None = None,
) -> str:
    """将评估结果保存为 Markdown 摘要报告，与 CSV 同路径（.md 后缀）.

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        output_path: CSV 输出路径（用于推导 .md 路径）
        trace_ids: 每个问题对应的 trace_id（与 questions 一一对应），
            传入后在表格中新增 trace_id 列

    Returns:
        实际写入的 .md 文件路径
    """
    md_path = output_path.rsplit(".", 1)[0] + ".md"
    df = result.to_pandas()

    metric_cols = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]
    actual_metrics = [c for c in metric_cols if c in df.columns]

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
    lines.append("")

    # 表头
    header = ["Question"] + actual_metrics
    if trace_ids is not None:
        header.append("trace_id")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")

    # 每行数据
    for i in range(len(df)):
        row_vals = [f"Q{i + 1}"] + [f"{df[m].iloc[i]:.4f}" for m in actual_metrics]
        if trace_ids is not None:
            row_vals.append(trace_ids[i] if i < len(trace_ids) else "")
        lines.append("| " + " | ".join(row_vals) + " |")

    lines.append("")

    # 平均值
    avg_parts = ", ".join(f"{m}={df[m].mean():.4f}" for m in actual_metrics)
    lines.append(f"**Averages:** {avg_parts}")
    lines.append("")

    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    content = "\n".join(lines)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    core_logging.log_event(Event.REPORT_SAVED, file=md_path)
    return md_path


def check_gate(result: Any, questions: list[str]) -> None:
    """检查评估结果是否通过质量门禁阈值.

    对 GATE_THRESHOLDS 中每个指标打印 PASS/FAIL，
    任一指标不达标时列出未通过的问题并退出码为 1。

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
    """
    df = result.to_pandas()
    failing_indices: list[int] = []
    all_pass = True

    for metric, threshold in GATE_THRESHOLDS.items():
        if metric not in df.columns:
            print(f"  {metric}: N/A (metric not in evaluation results)")
            all_pass = False
            continue

        score = df[metric].mean()
        passed = score >= threshold
        status = "PASS" if passed else "FAIL"
        print(f"  {metric}: {score:.4f} {status}")

        if not passed:
            all_pass = False
            for i in range(len(df)):
                if df[metric].iloc[i] < threshold and i not in failing_indices:
                    failing_indices.append(i)

    if not all_pass:
        if failing_indices:
            print(f"\nFailing questions ({len(failing_indices)} total):")
            for idx in failing_indices:
                print(f"  Q{idx + 1}: {questions[idx][:80]}")
        sys.exit(1)

    sys.exit(0)


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
        from datasets import Dataset  # noqa: F401
        from ragas import evaluate  # noqa: F401
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (  # noqa: F401
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        from src.agents.graph.workflow import build_graph
        from src.infra.db.engine import run_and_dispose
        from src.infra.db.vector_store import VectorStore
        from src.infra.llm.prompt_manager import PromptManager
        from src.models import get_embeddings, get_judge_llm, get_llm, get_rerank

        core_logging.log_event(Event.RAG_COMPONENT_INIT)
        vector_store = VectorStore()

        # 选手/裁判分离：
        # - 选手（被测系统）= 生产模型 get_llm()（LLM_MODEL），评估结果反映线上质量
        # - 裁判（评估器）  = judge 角色绑定（RAGAS_LLM_MODEL 可覆盖），独立于选手
        #   评分，避免自我评价偏置；模型名空/None 时由 registry 兜底。
        llm = get_llm()  # 选手：生产模型（含生产 temperature）
        reranker = get_rerank()
        embeddings = get_embeddings()
        judge_llm = get_judge_llm()
        core_logging.log_event(
            Event.EVAL_MODEL_INIT,
            sut=settings.LLM_MODEL,
            judge=judge_llm.model_name,
        )
        llm_wrapper = LangchainLLMWrapper(judge_llm, bypass_n=True)
        embeddings_wrapper = LangchainEmbeddingsWrapper(embeddings)

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
            return await generate_answers_and_contexts(
                graph,
                kb_id,
                session_id,
                questions,
            )

        core_logging.log_event(Event.VECTOR_STORE_CHECK, kb_id=kb_id)
        core_logging.log_event(Event.ANSWERS_GENERATION, count=len(questions))
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
            _sample_classes,
            _trajectories,
        ) = generated
        # 生成段结束即 flush：--gate 路径会提前 sys.exit，晚 flush 等于丢数据
        flush_tracing()

        result = run_evaluation(
            questions,
            ground_truth,
            answers,
            contexts,
            llm_wrapper,
            embeddings_wrapper,
        )

        # 指标均值输出到 stdout：compare_retrieval 等 A/B 脚本以
        # 子进程方式调用本脚本，靠 stdout 解析指标（parse_metrics）；logger 只写
        # 文件不落 stdout，故此处显式 print。--gate 模式下 check_gate 已打印指标行。
        if not args.gate:
            _print_metric_averages(result)

        output_path = save_results_csv(
            result, questions, ground_truth, output_path, trace_ids
        )
        save_markdown_report(result, questions, output_path, trace_ids)

        # _save_eval_report 需要 questions 长度
        _save_eval_report(kb_id, result, len(questions), output_path, retrieval_details)

        if args.gate:
            check_gate(result, questions)

        core_logging.log_event(Event.EVALUATION_DONE)
    finally:
        # 异常退出 / --gate 的 sys.exit 路径也须 flush，否则缓冲事件丢失
        flush_tracing()


def _print_metric_averages(result: Any) -> None:
    """把四指标均值逐行打印到 stdout（供 A/B 子进程解析）。

    compare_retrieval 通过 subprocess 调用本脚本并用
    parse_metrics 解析 stdout 里的指标均值；logger 只写日志文件，因此
    均值需显式 print。行格式与 check_gate 对齐：一行一指标、
    `指标名: 数值`，便于按指标名前缀匹配取值。

    Args:
        result: RAGAS evaluate() 返回的结果对象
    """
    df = result.to_pandas()
    for col in [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]:
        if col in df.columns:
            print(f"  {col}: {df[col].mean():.4f}")


def _save_eval_report(
    kb_id: str,
    result,
    qa_count: int,
    output_path: str,
    retrieval_details: list[list[dict]] | None = None,
) -> None:
    """将 RAGAS 评估结果持久化到 eval_report 表.

    Args:
        kb_id: 知识库 UUID
        result: RAGAS evaluate() 返回的结果对象
        qa_count: QA 对数
        output_path: CSV 报告文件路径
        retrieval_details: 每 query 的检索明细（与 detail 数组按 q_index 对齐），
            传入后并入对应 detail_json 条目，供低分 query 下钻检索环节
    """
    try:
        from src.infra.db.engine import run_and_dispose
        from src.infra.db.models.eval_report import EvalReportModel as EvalReportEntity
        from src.services.app_service import AppService

        svc = AppService()

        df = result.to_pandas()
        metric_cols = [
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        ]
        detail = []
        for i in range(qa_count):
            entry = {"q_index": i, "question": ""}
            for col in metric_cols:
                if col in df.columns:
                    entry[col] = float(df[col].iloc[i])
            if retrieval_details is not None and i < len(retrieval_details):
                entry["retrieval_details"] = retrieval_details[i]
            detail.append(entry)

        avg = {}
        for col in metric_cols:
            if col in df.columns:
                avg[col] = float(df[col].mean())

        faith = avg.get("faithfulness")
        recall = avg.get("context_recall")
        precision = avg.get("context_precision")
        relevancy = avg.get("answer_relevancy")

        weights = {
            "faithfulness": 0.3,
            "context_recall": 0.3,
            "context_precision": 0.2,
            "answer_relevancy": 0.2,
        }
        weighted_sum = 0.0
        total_w = 0.0
        for k, w in weights.items():
            v = avg.get(k)
            if v is not None:
                weighted_sum += v * w
                total_w += w
        overall = weighted_sum / total_w if total_w > 0 else None

        async def _do_insert():
            entity = EvalReportEntity(
                id=str(uuid.uuid4()),
                kb_id=kb_id,
                run_type="manual",
                qa_count=qa_count,
                faithfulness=faith,
                answer_relevancy=relevancy,
                context_precision=precision,
                context_recall=recall,
                overall_score=overall,
                passed=overall >= 0.70 if overall is not None else False,
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
