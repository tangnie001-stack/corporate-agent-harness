"""评测打分辅助：样本分类、主分聚合、跳过掩码、检索覆盖率、分数回写/回读。

与 eval_ragas.py 的分工：本模块放评测辅助逻辑与可单测的纯函数；
含外部 I/O 的有两个：`write_scores`（把每题分数回写 Langfuse）与
`read_behaviour_scores`（从 `test_run_*.json` 回读逐题行为分）；
CLI 编排（跑图、写报告文件）留在 eval_ragas.py。

**可替换评分边界（逃生门接入点，design D10）**：评测的「用例构造」与「评分」两步
各自落在具名函数上——用例构造 = `eval_ragas._bridge_test_case`；评分 = 本模块的
`build_metrics`（构造桥的四项指标）+ `eval_ragas.score_rows`（串行逐题打分）。
桥为**默认实现**；逃生门替代实现（DeepEval 采样 + 项目侧直调 ragas 0.3 线评分）
应在**这两个点**接入、保持同签名。本轮只预留边界、不实现替代实现。
"""

from pathlib import Path

import pandas as pd
from langchain_core.messages import AIMessage
from loguru import logger

from src.config.const import (
    CONTEXT_METRIC_COLUMNS,
    RAGAS_METRIC_COLUMNS,
    SAMPLE_CLASS_EMPTY,
    SAMPLE_CLASS_PURE_KB,
    SAMPLE_CLASS_WEB,
    SSEInteractionTexts,
)
from src.rag.context import RAGContext


def build_metrics(judge, embeddings) -> dict:
    """构造 DeepEval RAGAS 桥的**经典四项**（design D2）。

    只用单项、不用聚合：聚合 `RagasMetric` 在源码里是 5 项算术平均（含
    `ContextualEntitiesRecall`），与官方文档写四项不一致 ⇒ 口径不可控。

    Args:
        judge: langchain `BaseChatModel`（桥要求）
        embeddings: langchain Embeddings（`answer_relevancy` 唯一需要）

    Returns:
        指标名 -> 桥度量实例；**顺序即串行评分顺序**
    """
    # 确保 DEEPEVAL_* 兜底先于 deepeval 被 import（顺序即语义，故禁用 isort 重排）
    # isort: off
    import src.config.settings  # noqa: F401
    from deepeval.metrics.ragas import (
        RAGASAnswerRelevancyMetric,
        RAGASContextualPrecisionMetric,
        RAGASContextualRecallMetric,
        RAGASFaithfulnessMetric,
    )
    # isort: on

    if embeddings is None:
        raise AssertionError("answer_relevancy 需要显式 embeddings（不得为 None）")

    return {
        "faithfulness": RAGASFaithfulnessMetric(model=judge),
        "answer_relevancy": RAGASAnswerRelevancyMetric(
            model=judge, embeddings=embeddings
        ),
        "context_precision": RAGASContextualPrecisionMetric(model=judge),
        "context_recall": RAGASContextualRecallMetric(model=judge),
    }


def classify_sample(contexts: list[RAGContext]) -> str:
    """按检索来源把单个样本分为纯 KB / 含 web / 无检索 三类。

    Args:
        contexts: 该样本经工具收集的检索材料

    Returns:
        SAMPLE_CLASS_EMPTY / SAMPLE_CLASS_PURE_KB / SAMPLE_CLASS_WEB
    """
    if not contexts:
        return SAMPLE_CLASS_EMPTY
    for c in contexts:
        if c.kind == SSEInteractionTexts.CITATION_KIND_WEB:
            return SAMPLE_CLASS_WEB
    return SAMPLE_CLASS_PURE_KB


def apply_sample_masks(df: pd.DataFrame, sample_classes: list[str]) -> pd.DataFrame:
    """按样本类别掩码：无材料样本的三项 context 指标置 NaN 并记 skipped。

    置空用 NaN（而非 None/pd.NA），以免下游 float() 抛错致落库静默失败。
    返回值重建为从 0 起的 RangeIndex：掩码用 `.loc[i, col]` 定位行，
    若传入表带非默认索引（如按 trace_id 建索引）会 KeyError。

    Args:
        df: ragas 结果 DataFrame（行序与 sample_classes 对齐）
        sample_classes: 每题的样本类别

    Returns:
        增加 sample_class / skipped 两列的副本（索引重置为 RangeIndex）
    """
    out = df.copy().reset_index(drop=True)
    out["sample_class"] = sample_classes
    skipped_col: list[str] = []
    for i in range(len(out)):
        marks: list[str] = []
        if sample_classes[i] == SAMPLE_CLASS_EMPTY:
            for col in CONTEXT_METRIC_COLUMNS:
                if col in out.columns:
                    out.loc[i, col] = float("nan")
                    marks.append(col)
        if marks:
            skipped_col.append(";".join(marks))
        else:
            skipped_col.append("")
    out["skipped"] = skipped_col
    return out


def nan_cell_stats(df: pd.DataFrame) -> dict:
    """NaN 率（单位是**格**，不是题）：分母 = 纯 KB 样本 × 四指标。

    分子 = 纯 KB 格里的**全部** NaN（合法缺值与裁判失败不可区分，一并计入）。
    掩码产生的 NaN 只出现在 `empty` 样本 ⇒ 天然落在分母之外，无需排除。

    Returns:
        {"nan_cells": int, "denominator_cells": int, "nan_rate": float}
    """
    pure = df[df["sample_class"] == SAMPLE_CLASS_PURE_KB]
    cols = [c for c in RAGAS_METRIC_COLUMNS if c in df.columns]
    denom = len(pure) * len(cols)
    nan_cells = int(pure.loc[:, cols].isna().to_numpy().sum()) if denom else 0
    return {
        "nan_cells": nan_cells,
        "denominator_cells": denom,
        "nan_rate": (nan_cells / denom) if denom else 0.0,
    }


def main_score_means(df: pd.DataFrame) -> dict[str, float]:
    """计算主分：仅纯 KB 样本行的各指标均值。

    指标集合在函数内收窄到 `RAGAS_METRIC_COLUMNS` 白名单（不依赖调用方过滤），
    以免结果表混入额外数值列（如 chunk_size）时被静默计入主分。
    均值口径为"纯 KB 且非 NaN"（`Series.mean()` 默认 `skipna=True`，即本口径；
    **不得**改为 `skipna=False`）。

    Args:
        df: 经 apply_sample_masks 处理、含 sample_class 列的结果

    Returns:
        指标名 -> 均值；无纯 KB 样本时对应值为 nan
    """
    mask = df["sample_class"] == SAMPLE_CLASS_PURE_KB
    result: dict[str, float] = {}
    for col in RAGAS_METRIC_COLUMNS:
        if col not in df.columns:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            result[col] = float(df.loc[mask, col].mean())
    return result


def retrieval_coverage(sample_classes: list[str]) -> float:
    """有检索样本（非 empty）占比。

    Args:
        sample_classes: 每题的样本类别

    Returns:
        覆盖率；空列表返回 0.0
    """
    if not sample_classes:
        return 0.0
    hit = sum(1 for c in sample_classes if c != SAMPLE_CLASS_EMPTY)
    return hit / len(sample_classes)


def summarize_trajectory(messages: list) -> dict:
    """生成轨迹诊断摘要：工具名 + 入参 + 迭代数；**不含工具输出正文**。

    **仅作报告诊断摘要，SHALL NOT 作为行为指标的数据源**（tasks 5.8）：
    行为分的唯一来源是 `test_run_*.json` 的 `testCases[].metricsData`（经
    `read_behaviour_scores` 回读），本函数的"工具名 + 迭代数"只是给人看的
    缩略轨迹，不是 DeepEval 行为指标（`StepEfficiency` / `AgentLoopDetection`）
    所消费的轨迹对象。

    Args:
        messages: LangGraph 状态中的消息列表

    Returns:
        {"tool_calls": [{"name": str, "args": dict}, ...], "iterations": int}；
        iterations 为 AIMessage 条数（即 Agent 迭代轮数）
    """
    calls: list[dict] = []
    iterations = 0
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        iterations += 1
        for tc in m.tool_calls:
            calls.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
    return {"tool_calls": calls, "iterations": iterations}


def write_scores(
    trace_ids: list[str], scores: dict[str, list[float]], enabled: bool
) -> None:
    """把每题各指标分数按 trace_id 显式回写 Langfuse。

    评分在批量 evaluate() 之后才产出，逐题 @observe 已关闭，
    故不能用 score_current_trace（无当前 trace 且吞异常）。客户端实例统一取
    `langfuse_context.client_instance`（**不得** new Langfuse()：那会绕过
    LANGFUSE_ENABLE 开关与关停 flush）。客户端获取失败或逐条回写失败都只记
    warning、不向上抛：回写属观测旁路，绝不阻断评测主流程（DB 落库与门禁）。

    Args:
        trace_ids: 每题 trace_id（与 scores 各列表等长）
        scores: 指标名 -> 每题的分数列表；值为 None 或 NaN（掩码的跳过语义）
            时该题该指标不写出
        enabled: LANGFUSE_ENABLE 取值；False 时整体短路、不触碰客户端
    """
    if not enabled:
        return
    from langfuse.decorators import langfuse_context

    try:
        client = langfuse_context.client_instance
    except Exception as exc:  # noqa: BLE001  # 观测旁路，失败不影响评测主流程
        logger.warning(
            "[cli] langfuse client unavailable, scores not written err={}", exc
        )
        return
    for name, values in scores.items():
        for i, trace_id in enumerate(trace_ids):
            if i >= len(values):
                continue
            value = values[i]
            if value is None:
                continue
            if pd.isna(value):
                continue
            try:
                client.score(trace_id=trace_id, name=name, value=float(value))
            except Exception as exc:  # noqa: BLE001  # 回写失败不影响评测主流程
                logger.warning(
                    "[cli] langfuse score write failed trace_id={} metric={} err={}",
                    trace_id,
                    name,
                    exc,
                )


def build_behaviour_metrics(judge):
    """构造 DeepEval 原生行为指标（design D6；本阶段定稿为 3 项）。

    顺序即评分与报告顺序。三点实测依据（docs/tmp/deepeval-poc/RESULTS.md）：
    - `TaskCompletionMetric(task=None)`：**逐题**从 test case 取任务
      （deepeval `metrics/task_completion/task_completion.py:121`），故不必每题新建实例；
    - `StepEfficiencyMetric`：按轨迹算步骤效率，需裁判；
    - `AgentLoopDetectionMetric`：**纯 trace 分析、不调 LLM**（构造器无 model 参数）。

    `threshold=0.5` 只影响 JSON 里的 `success` 字段；行为轴**不进主分/门禁**。
    """
    from deepeval.metrics import (
        AgentLoopDetectionMetric,
        StepEfficiencyMetric,
        TaskCompletionMetric,
    )

    return [
        TaskCompletionMetric(model=judge, threshold=0.5),
        StepEfficiencyMetric(model=judge, threshold=0.5),
        AgentLoopDetectionMetric(threshold=0.5),
    ]


def read_behaviour_scores(results_folder: str) -> list[dict]:
    """从 `<results_folder>/test_run_*.json` 回读**逐题**行为分。

    为什么必须回读文件：DeepEval 的 eval loop 用 `copy_metrics()` 的副本打分
    ⇒ 调用方持有的 `metric.score` 恒为 `None`（见 design D6）。
    逐题分在 `testCases[j].metricsData[k]`（含 `name`/`score`/`success`）；
    `metricsScores` 是**按指标聚合**，不作逐题分。

    Args:
        results_folder: `DisplayConfig(results_folder=…)` 指定的目录。

    Returns:
        按题序的 `[{"q_index", "input", "metrics": {name: score},
        "reasons": {name: reason}}, …]`。`reasons` 是行为轴的逐题失败证据承载点
        （design R9：行为轴判官调用不经 hooks 逐题归因，失败证据取此处）。

    Raises:
        RuntimeError: 目录下没有 `test_run_*.json`（生成器未耗尽 ⇒ 行为分不可信）。
    """
    import glob
    import json

    files = sorted(glob.glob(str(Path(results_folder) / "test_run_*.json")))
    if not files:
        raise RuntimeError(
            f"{results_folder} 下没有 test_run_*.json："
            "evals_iterator 未耗尽（或循环提前结束），行为分不可信"
        )
    raw = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    rows: list[dict] = []
    for idx, tc in enumerate(raw.get("testCases") or []):
        metrics = {
            md.get("name"): md.get("score") for md in (tc.get("metricsData") or [])
        }
        reasons = {
            md.get("name"): md.get("reason") for md in (tc.get("metricsData") or [])
        }
        rows.append(
            {
                "q_index": idx,
                "input": tc.get("input", ""),
                "metrics": metrics,
                "reasons": reasons,
            }
        )
    return rows


def assert_behaviour_scores_complete(
    questions: list[str], rows: list[dict], trajectories: list[dict]
) -> None:
    """行为轴 fail-loud：题数对齐 + 每题至少一个非 None 分 + 轨迹非空。

    为什么必须 fail-loud：`AgentLoopDetection` 在**无 trace 时源码直接置 0.0**
    （静默假分数）；若 JSON 缺失或循环提前结束，回读会得到空表 ⇒ 必须炸掉而不是
    把"没测到"当成"行为良好"。
    """
    if len(rows) != len(questions):
        raise RuntimeError(
            f"行为分题数不齐：questions={len(questions)} rows={len(rows)}（JSON 缺失或循环未耗尽）"
        )
    if len(trajectories) != len(questions):
        # 缺这条守卫时，trajectories 偏短会让下面的循环体一次都不跑 ⇒ 静默通过，
        # 正是本函数要防的"没测到当成没异常"（评审 Important）
        raise RuntimeError(
            f"轨迹数与题数不齐：questions={len(questions)} trajectories={len(trajectories)}"
        )
    for r in rows:
        if not any(v is not None for v in (r.get("metrics") or {}).values()):
            raise RuntimeError(
                f"第 {r.get('q_index')} 题没有任何行为分（行为分不可信）"
            )
    for i, t in enumerate(trajectories):
        if not (t or {}).get("tool_calls") and not (t or {}).get("iterations"):
            raise RuntimeError(
                f"第 {i} 题轨迹为空（行为指标无 trace 时源码直接置 0.0）"
            )
