"""Agent 工具集 — retrieve_kb（知识库检索）与 ask_user（澄清追问）。

工具工厂 make_rag_tools 经闭包注入共享依赖（vector_store/reranker）；
per-request 对象（tool_contexts 收集器、ask_count 计数、clarify_channel）经
current_request_ctx 读取，不进闭包。工具不能写 state：检索上下文累积到
RequestContext.tool_contexts，由后续节点读入 state；ask_user 的挂起 Future
登记进进程级 pending_asks（POST /clarify-answer 是独立请求，contextvar 不可达）。
"""

import asyncio
import time
from typing import Annotated

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from src.agents.graph.state import AgentState
from src.agents.tools.ask_tools import AskQuestion, AskUserArgs, ask_user
from src.config import RERANK_OUTER_TIMEOUT, TOP_K_RERANK, settings
from src.config.const import (
    ASK_USER_TIMEOUT,
    SSEInteractionTexts,
    resolve_source_tier,
)
from src.core import logging as core_logging
from src.core.eval_evidence import (
    EVIDENCE_CANDIDATE_CAP,
    current_evidence,
    parent_key,
)
from src.core.log_events import Event, Signal
from src.infra.db.vector_store import VectorStore
from src.infra.llm.request_context import current_request_ctx
from src.infra.search.query_router import aggregate_kb_entities
from src.rag import retrieval
from src.rag.context import RAGContext
from src.rag.retrieval import _ALL_ENTITY_KEYS
from src.rag.temporal import (
    compute_missing,
    derive_candidate_years,
    has_temporal_words,
    parse_temporal,
)

# ASK_USER_TIMEOUT / aggregate_kb_entities 为 ask_tools 的 monkeypatch 入口（测试经
# rag_tools 模块属性替换），并作为本模块对外 re-export 的一部分
__all__ = [
    "ASK_USER_TIMEOUT",
    "AskQuestion",
    "AskUserArgs",
    "RetrieveKBArgs",
    "aggregate_kb_entities",
    "ask_user",
    "make_rag_tools",
]


class RetrieveKBArgs(BaseModel):
    """retrieve_kb 工具参数（LLM 可见的入参契约）。"""

    query: str = Field(description="检索查询文本")
    top_k: int = Field(default=TOP_K_RERANK, ge=1, le=10, description="返回条数上限")


def make_rag_tools(
    vector_store: VectorStore,
    reranker,
    prompt_manager,
    delegate_task: BaseTool | None = None,
    *,
    include_ask_user: bool = True,
) -> list[BaseTool]:
    """构建工具列表：注册表管理；retrieve_kb 始终注册（KB=RAG 开关在工具内实现）。

    Args:
        vector_store: 向量存储实例（闭包注入；dense 与词法两路同源于它）
        reranker: Reranker 模型实例（闭包注入，rerank_results 使用）
        prompt_manager: 提示词管理器（闭包注入，当前工具未直接使用，保留签名）
        delegate_task: 可选 delegate_task 工具（skill 库有内容时由调用方注入并注册；
            无 skill 时传 None 不注册，主 agent 工具集保持固定三件套）
        include_ask_user: 是否注册 ask_user（澄清追问）。报警等**非交互轮次**
            传 False —— 无人可答，注册只会把该轮拖到澄清超时。

    Returns:
        工具列表：retrieve_kb（知识库检索）、ask_user（澄清追问）；开启 web 兜底时追加
        search_web；delegate_task 非空时追加 delegate_task；经 ToolRegistry.enabled_tools() 过滤启用项
    """

    @tool("retrieve_kb", args_schema=RetrieveKBArgs)
    async def retrieve_kb(
        query: str,
        top_k: int = TOP_K_RERANK,
        state: Annotated[AgentState | None, InjectedState()] = None,
    ) -> str:
        """在财务知识库检索与 query 相关的文档片段并精排，返回带引用编号的文本。

        何时调用：问题涉及公司经营数据、财务指标、报告期等事实性内容时调用；
        闲聊、一般性概念问题不需要调用。

        Args:
            query: 检索查询文本
            top_k: 返回条数上限（默认 TOP_K_RERANK，精排后按此截断）
            state: LangGraph 注入的执行状态：显式形状判定——外层自建图注入 AgentState
                （读其 kb_id，空 = 未绑定 KB 不检索）；主循环已内化进 create_agent 装配
                产物，注入的是映射（dict），读装配 seed 的 kb_id（CLI 无 ctx 入口据此
                仍取到）；两者都取不到时回退 current_request_ctx 的 kb_id

        Returns:
            带全局递增引用编号的精排上下文文本，如 "[1] 来源: xxx (第3页)\\n内容: ..."
        """
        # kb_id 取值：显式形状判定——外层自建图注入 AgentState（读其 kb_id）；
        # create_agent 承载注入映射（读装配 seed 的 kb_id，CLI 无 ctx 入口据此仍取到）。
        # 两者都取不到时回退 current_request_ctx.kb_id（子代理图场景）。
        # ctx 同时供后续检索计数 / temporal / 信号读写，此处一次读取、全程复用
        ctx = current_request_ctx.get()
        kb_id = ""
        if isinstance(state, AgentState):
            kb_id = state.kb_id
        elif isinstance(state, dict):
            kb_id = str(state.get("kb_id", ""))
        if not kb_id and ctx is not None:
            kb_id = ctx.kb_id

        # 同 turn 检索调用计数：reretrieve 换词信号判定用（retrieve_call_seq >= 2）。
        # ctx 为 None（无请求上下文）时不计数——计数只为信号服务，无 ctx 即无消费方
        if ctx is not None:
            ctx.retrieve_call_seq += 1

        # 时间结构化约束（grilling 决策）：正则粗筛命中才解析；结果写入 RequestContext。
        # turn 内最多解析一次：首次解析成功后置 temporal_parsed，后续调用跳过 DB+LLM 重复执行
        if (
            settings.TEMPORAL_PARSE_ENABLED
            and ctx is not None
            and kb_id
            and has_temporal_words(query)
            and not ctx.temporal_parsed
        ):
            candidates = await derive_candidate_years([kb_id])
            from src.models import get_classify_llm

            parsed = await parse_temporal(query, candidates, get_classify_llm())
            ctx.temporal_years = parsed["years"]
            ctx.missing_years = compute_missing(parsed["years"], candidates)
            ctx.temporal_parsed = True

        start = time.monotonic()
        # kb_id 非空 → 检索该库；kb_id 为空（未绑定 KB）→ 不检索
        # （KB=RAG 开关硬保证：即便被调也返回空，废弃 _semantic_select_kb 语义选库 = 隐式跨库）
        if kb_id:
            results = await retrieval.search(query, kb_id, vector_store)
        else:
            results = []

        # rerank 为同步 HTTP 调用：内层经 client 注入 request_timeout 使挂死调用自行结束，
        # 外层 wait_for 仅作双保险（预算须大于内层，否则外层先放弃、线程仍跑到底）；
        # 放线程池避免阻塞事件循环，超时后降级为检索原始顺序（distance 升序）避免空结果 abstain
        try:
            contexts = await asyncio.wait_for(
                asyncio.to_thread(retrieval.rerank_results, query, results, reranker),
                timeout=RERANK_OUTER_TIMEOUT,
            )
        except TimeoutError:
            core_logging.log_event(
                Event.RERANK_TIMEOUT,
                timeout_s=RERANK_OUTER_TIMEOUT,
                query=query,
            )
            # 线程池里的精排不会被 wait_for 取消：先置放弃标记，让它晚到时跳过证据回写，
            # 否则会覆盖本分支的降级快照，留下 rerank.failed=true 与
            # rerank_after.failed=false 并存的矛盾证据
            collector = current_evidence.get()
            if collector is not None:
                collector.rerank_abandoned.set()
            contexts = []
            for r in results:
                pc = r.metadata.get("parent_content")
                if pc:
                    content = pc
                else:
                    content = r.content
                contexts.append(
                    RAGContext(
                        content=content,
                        source=r.metadata.get("source", ""),
                        page=r.metadata.get("page", 0),
                        doc_id=r.metadata.get("doc_id", ""),
                        chunk_id=r.id,
                        parent_content=pc,
                        score=1 - r.distance if r.distance is not None else 0.0,
                        tier=resolve_source_tier(
                            r.metadata.get("source", ""),
                            SSEInteractionTexts.CITATION_KIND_KB,
                        ),
                        entities={
                            k: r.metadata.get(k)
                            for k in _ALL_ENTITY_KEYS
                            if r.metadata.get(k)
                        },
                    )
                )
            # 降级路径未发生精排，不存在"精排后条数"：dropped 取"检索后条数 − 去重后条数"，
            # 与 rerank_results 成功路径口径同构；无条件落盘（无丢弃时 dropped=0）
            before = len(contexts)
            contexts_before_dedup = list(contexts)
            contexts = retrieval._dedup_by_parent(contexts)
            core_logging.log_event(
                Event.DEDUP_DONE, dropped=before - len(contexts), kept=len(contexts)
            )
            contexts_deduped = list(contexts)
            # 降级路径不经过 rerank_results，top-k 截断在本分支完成；证据须在截断之后写，
            # 否则看不到 top-k 丢弃的 chunk_id
            contexts = contexts_deduped[:top_k]
            # 证据旁路：超时分支无精排结果，但事实链要读到 rerank 失败标记与各阶段快照，
            # rerank / rerank_before / rerank_after / dedup / topk 五个键都要写，不能缺段
            if collector is not None:
                deduped_ids = [c.chunk_id for c in contexts_deduped]
                final_ids = [c.chunk_id for c in contexts]
                collector.retrieval["rerank"] = {
                    "failed": True,
                    "reason": "timeout",
                    "score_kind": "fallback",
                }
                collector.retrieval["rerank_before"] = retrieval._evidence_stage(
                    results, "rrf"
                )
                collector.retrieval["dedup"] = {
                    "dropped_chunk_ids": [
                        c.chunk_id
                        for c in contexts_before_dedup
                        if c.chunk_id not in set(deduped_ids)
                    ],
                    "kept_chunk_ids": deduped_ids,
                }
                collector.retrieval["topk"] = {
                    "dropped_chunk_ids": [
                        cid for cid in deduped_ids if cid not in set(final_ids)
                    ],
                    "kept_chunk_ids": final_ids,
                    "rank_before_topk": len(contexts_deduped),
                    "rank_after_topk": len(contexts),
                }
                rerank_items = []
                for rank, chunk in enumerate(
                    contexts_before_dedup[:EVIDENCE_CANDIDATE_CAP]
                ):
                    rerank_items.append(
                        {
                            "chunk_id": chunk.chunk_id,
                            "parent_key": parent_key(
                                chunk.doc_id,
                                chunk.chunk_id,
                                chunk.parent_content,
                            ),
                            "doc_id": chunk.doc_id,
                            "source": chunk.source,
                            "page": chunk.page,
                            "rank": rank,
                            "score": chunk.score,
                            "score_kind": "fallback",
                            "distance": None,
                        }
                    )
                collector.retrieval["rerank_after"] = {
                    "items": rerank_items,
                    "count": len(rerank_items),
                    "total_count": len(contexts_before_dedup),
                    "truncated": len(contexts_before_dedup) > EVIDENCE_CANDIDATE_CAP,
                    "failed": True,
                    "reason": "timeout",
                }
        # 正常路径在此截断；超时分支已在降级段内截断（证据须在截断后写），重复截断幂等
        contexts = contexts[:top_k]

        # iteration：显式形状判定。主循环已内化进装配产物，工具只会拿到映射承载，
        # 迭代序号读装配 seed 的 _turn_count；AgentState 承载已无循环计数字段，取 0
        # （iteration 仅入日志/信号，该承载无消费主图迭代语义的字段）
        if isinstance(state, AgentState):
            iteration = 0
        elif isinstance(state, dict):
            iteration = int(state.get("_turn_count", 0))
        else:
            iteration = 0

        # 检索重放上下文（L1）：query/kb 为重放输入，其余参数为"当时值"供 drift 对照；
        # 态 A（kb_id 空）不检索、不落 replay 行
        if kb_id:
            core_logging.log_event(
                Event.RETRIEVE_REPLAY,
                query=query,
                query_len=len(query),
                kb_id=kb_id,
                iteration=iteration,
                top_k=top_k,
                hybrid=settings.HYBRID_SEARCH_ENABLED,
                rerank=True,
            )

        # 检索行为信号（态 B 专属缺陷诊断）：独立信号行，不经 log_event 拼装；
        # 态 A（kb_id 为空）未检索不产任何缺陷信号
        if kb_id and not results:
            core_logging.retrieval_signal(
                Signal.EMPTY_RESULT, query, iteration, kb_id=kb_id, result_count=0
            )
        if kb_id and ctx is not None and ctx.retrieve_call_seq >= 2:
            core_logging.retrieval_signal(
                Signal.RERETRIEVE,
                query,
                iteration,
                kb_id=kb_id,
                call_seq=ctx.retrieve_call_seq,
                result_count=len(results),
            )
        core_logging.log_event(
            Event.RETRIEVE_DONE,
            iteration=iteration,
            query=query,
            result_count=len(contexts),
            latency_ms=int((time.monotonic() - start) * 1000),
        )

        # 全局递增编号：同步块内读取偏移并追加，无 await，asyncio 单线程保证原子
        if ctx is not None:
            collector = ctx.tool_contexts
        else:
            collector = []
        offset = len(collector)
        collector.extend(contexts)
        blocks = [
            f"[{offset + i + 1}] {c.to_prompt_text()}" for i, c in enumerate(contexts)
        ]
        return "\n\n".join(blocks)

    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("retrieve_kb", retrieve_kb, readonly=True)
    registry.register("ask_user", ask_user, readonly=True, enabled=include_ask_user)
    if settings.WEB_SEARCH_ENABLED:
        from src.agents.tools.web_tools import search_web

        registry.register("search_web", search_web, readonly=True)
    if delegate_task is not None:
        registry.register("delegate_task", delegate_task, readonly=True)
    return registry.enabled_tools()
