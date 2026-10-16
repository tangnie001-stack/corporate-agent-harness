"""普通事件注册表数据 — EventSpec dataclass + EVENT_SPECS 字面量表。

纯数据模块，自包含（不 import log_events，避免环依赖）。Event 枚举与
import 期一致性校验留在 log_events.py；本表 key/name 为字面字符串，
与 Event 枚举值的一致性由 log_events 的 _validate_registry 在 import 期断言。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventSpec:
    """普通事件描述（注册表条目）。

    name: 事件名（Event 成员的值，注册表 key）
    prefix: 分层前缀（须在 LOG_PREFIXES 内）
    level: 事件标准级别（helper 按此路由 logger 级别）
    fields: 字段名登记（供 review / 文档对照，不参与运行时校验）
    """

    name: str
    prefix: str
    level: str
    fields: tuple[str, ...] = ()


EVENT_SPECS: dict[str, EventSpec] = {
    "search done": EventSpec(
        "search done", "retrieval", "info", ("kb_id", "query_len", "result_count")
    ),
    "hybrid done": EventSpec(
        "hybrid done",
        "retrieval",
        "info",
        (
            "kb_id",
            "query_len",
            "dense_count",
            "sparse_count",
            "dense_status",
            "sparse_status",
            "result_count",
        ),
    ),
    "rerank skip": EventSpec("rerank skip", "retrieval", "info", ("reason",)),
    "rerank done": EventSpec(
        "rerank done",
        "retrieval",
        "info",
        (
            "doc_count",
            "query_len",
            "latency_ms",
            "score_max",
            "score_min",
            "score_p50",
            "scored",
        ),
    ),
    "dedup done": EventSpec("dedup done", "retrieval", "info", ("dropped", "kept")),
    "rerank failed": EventSpec(
        "rerank failed", "retrieval", "warning", ("attempts", "query", "err")
    ),
    "rerank timeout": EventSpec(
        "rerank timeout", "retrieval", "warning", ("timeout_s", "query")
    ),
    "retrieve done": EventSpec(
        "retrieve done",
        "retrieval",
        "info",
        ("iteration", "query", "result_count", "latency_ms"),
    ),
    "web search done": EventSpec(
        "web search done",
        "retrieval",
        "info",
        ("query_count", "result_count", "latency_ms"),
    ),
    "retrieve replay": EventSpec(
        "retrieve replay",
        "retrieval",
        "info",
        (
            "query",
            "query_len",
            "kb_id",
            "iteration",
            "top_k",
            "hybrid",
            "rerank",
        ),
    ),
    # [agent] agent 循环 / 图 / 编排（3.3 批）
    "iteration done": EventSpec(
        "iteration done", "agent", "info", ("iteration", "msgs")
    ),
    "iteration limit": EventSpec(
        "iteration limit", "agent", "warning", ("query", "iteration")
    ),
    "history budget exceeded": EventSpec(
        "history budget exceeded", "agent", "warning", ("budget", "used", "kept")
    ),
    "context budget high": EventSpec(
        "context budget high", "agent", "warning", ("used", "limit")
    ),
    "model turn": EventSpec(
        "model turn",
        "agent",
        "info",
        (
            "model",
            "usage_in",
            "usage_out",
            "usage_estimated",
            "fallback",
            "latency_ms",
            "iteration",
            "temperature",
            "temp_source",
            "kb_bound",
        ),
    ),
    "graph compiled": EventSpec("graph compiled", "agent", "info"),
    "format done": EventSpec("format done", "agent", "info", ("citations", "reason")),
    "event convert failed": EventSpec(
        "event convert failed", "agent", "warning", ("item_type", "err")
    ),
    "service ready": EventSpec("service ready", "agent", "info"),
    "agent bind ignored": EventSpec(
        "agent bind ignored",
        "agent",
        "warning",
        ("session_id", "bound", "requested"),
    ),
    # [verify] 验证管道（3.2 批）
    "skip": EventSpec("skip", "verify", "info", ("reason",)),
    "completeness check": EventSpec(
        "completeness check",
        "verify",
        "info",
        ("kb_id", "required", "missing", "answer_len"),
    ),
    "web confirm ask": EventSpec("web confirm ask", "verify", "info", ("missing",)),
    "web confirm result": EventSpec(
        "web confirm result", "verify", "info", ("missing", "confirmed")
    ),
    "regen stop": EventSpec(
        "regen stop", "verify", "info", ("reason", "missing", "regenerations")
    ),
    "citation guide": EventSpec(
        "citation guide", "verify", "info", ("kind", "answer_len")
    ),
    # [session] 会话管理（3.4 批）
    "redis ready": EventSpec("redis ready", "session", "info"),
    "redis fallback": EventSpec("redis fallback", "session", "warning", ("err",)),
    "history write failed": EventSpec(
        "history write failed", "session", "warning", ("err",)
    ),
    "history read failed": EventSpec(
        "history read failed", "session", "warning", ("err",)
    ),
    "history clear failed": EventSpec(
        "history clear failed", "session", "warning", ("err",)
    ),
    "session save failed": EventSpec(
        "session save failed", "session", "warning", ("err",)
    ),
    "message save failed": EventSpec(
        "message save failed", "session", "warning", ("role", "err")
    ),
    "task registered": EventSpec("task registered", "session", "info"),
    "skill preload skip": EventSpec(
        "skill preload skip", "session", "warning", ("skill", "reason")
    ),
    "skill resolved": EventSpec(
        "skill resolved",
        "session",
        "info",
        ("skill", "context", "context_source", "body_chars"),
    ),
    "summary done": EventSpec("summary done", "session", "info", ("covered", "tokens")),
    "summary fallback": EventSpec(
        "summary fallback", "session", "warning", ("reason", "err")
    ),
    # [db] 向量检索 / 文件存储（3.4 批）
    "bucket created": EventSpec("bucket created", "db", "info", ("bucket",)),
    "file upload failed": EventSpec(
        "file upload failed", "db", "warning", ("key", "err")
    ),
    "file download failed": EventSpec(
        "file download failed", "db", "warning", ("key", "err")
    ),
    "file delete failed": EventSpec(
        "file delete failed", "db", "warning", ("key", "err")
    ),
    "chunks added": EventSpec(
        "chunks added", "db", "info", ("kb_id", "doc_id", "count")
    ),
    "chunks deleted": EventSpec("chunks deleted", "db", "info", ("doc_id", "count")),
    "chunks read": EventSpec(
        "chunks read", "db", "info", ("doc_id", "kb_id", "page", "total", "count")
    ),
    "chunks read failed": EventSpec(
        "chunks read failed", "db", "warning", ("doc_id", "kb_id", "err")
    ),
    "collection deleted": EventSpec("collection deleted", "db", "info", ("name",)),
    "collection delete failed": EventSpec(
        "collection delete failed", "db", "warning", ("name",)
    ),
    "search stats": EventSpec(
        "search stats",
        "db",
        "info",
        ("kb_id", "collection_name", "collection_count", "top_k"),
    ),
    "search result": EventSpec(
        "search result",
        "db",
        "info",
        ("kb_id", "query_len", "result_count", "embedding_ms", "model"),
    ),
    # [llm] prompt 拉取 / LLM 内容深日志（3.4 批）
    "prompt fetched": EventSpec("prompt fetched", "llm", "info", ("name", "version")),
    "prompt fetch failed": EventSpec(
        "prompt fetch failed", "llm", "warning", ("name", "err")
    ),
    "prompt fallback": EventSpec("prompt fallback", "llm", "info", ("name",)),
    "content start": EventSpec("content start", "llm", "info", ("model", "prompt")),
    "content end": EventSpec("content end", "llm", "info", ("model", "output")),
    # [cli] 离线工具（3.5 批：eval_ragas / eval_ragas_generate / check_retrieval / compare_rewrite）
    "testset loaded": EventSpec("testset loaded", "cli", "info", ("count",)),
    "evaluation run": EventSpec("evaluation run", "cli", "info", ("kb_id",)),
    "evaluation start": EventSpec("evaluation start", "cli", "info", ("samples",)),
    "evaluation done": EventSpec("evaluation done", "cli", "info"),
    "eval model init": EventSpec("eval model init", "cli", "info", ("sut", "judge")),
    "rag component init": EventSpec("rag component init", "cli", "info"),
    "vector store check": EventSpec("vector store check", "cli", "info", ("kb_id",)),
    "vector store empty": EventSpec("vector store empty", "cli", "error", ("kb_id",)),
    "ragas model missing": EventSpec("ragas model missing", "cli", "error"),
    "answers generation": EventSpec("answers generation", "cli", "info", ("count",)),
    "question answer start": EventSpec(
        "question answer start", "cli", "info", ("index", "query")
    ),
    "question answer done": EventSpec(
        "question answer done", "cli", "info", ("index", "answer_len", "contexts")
    ),
    "question answer failed": EventSpec(
        "question answer failed", "cli", "warning", ("index", "err")
    ),
    "metric mean": EventSpec("metric mean", "cli", "info", ("metric", "mean")),
    "metrics nan": EventSpec("metrics nan", "cli", "warning", ("counts",)),
    "results saved": EventSpec("results saved", "cli", "info", ("file",)),
    "report saved": EventSpec("report saved", "cli", "info", ("file",)),
    "eval report saved": EventSpec("eval report saved", "cli", "info", ("kb_id",)),
    "eval save failed": EventSpec("eval save failed", "cli", "warning", ("err",)),
    "vertexai stub created": EventSpec(
        "vertexai stub created", "cli", "info", ("file",)
    ),
    "question proofread failed": EventSpec(
        "question proofread failed", "cli", "warning", ("question", "err")
    ),
    "question proofread done": EventSpec(
        "question proofread done", "cli", "info", ("cleaned", "total")
    ),
    "kb meta failed": EventSpec("kb meta failed", "cli", "error", ("err",)),
    "chunk load start": EventSpec(
        "chunk load start", "cli", "info", ("kb_id", "whitelist")
    ),
    "chunks not found": EventSpec("chunks not found", "cli", "warning", ("doc_id",)),
    "no chunk data": EventSpec("no chunk data", "cli", "error"),
    "chunk load done": EventSpec("chunk load done", "cli", "info", ("docs", "chunks")),
    "generator init": EventSpec(
        "generator init", "cli", "info", ("model", "size", "chunks")
    ),
    "transforms setup": EventSpec("transforms setup", "cli", "info", ("steps",)),
    "knowledge graph found": EventSpec(
        "knowledge graph found", "cli", "info", ("file",)
    ),
    "knowledge graph build": EventSpec(
        "knowledge graph build", "cli", "info", ("chunks",)
    ),
    "knowledge graph saved": EventSpec(
        "knowledge graph saved", "cli", "info", ("file",)
    ),
    "testset generation start": EventSpec(
        "testset generation start", "cli", "info", ("size",)
    ),
    "testset saved": EventSpec(
        "testset saved", "cli", "info", ("file", "count", "version")
    ),
    "testset self-contained dropped": EventSpec(
        "testset self-contained dropped", "cli", "warning", ("kept", "dropped", "total")
    ),
    "metric retry": EventSpec(
        "metric retry", "cli", "info", ("metric", "q_index", "trace_id", "reason")
    ),
    "judge call": EventSpec(
        "judge call",
        "cli",
        "info",
        ("metric", "q_index", "trace_id", "attempt", "seconds", "ok", "note"),
    ),
    "kb lookup": EventSpec("kb lookup", "cli", "info", ("name",)),
    "kb not found": EventSpec("kb not found", "cli", "error", ("name",)),
    "search start": EventSpec("search start", "cli", "info", ("query", "top_k")),
    "search failed": EventSpec("search failed", "cli", "warning", ("query", "err")),
    "llm invoke failed": EventSpec("llm invoke failed", "cli", "warning", ("err",)),
    "llm parse failed": EventSpec("llm parse failed", "cli", "warning", ("err", "raw")),
    "rerank error": EventSpec("rerank error", "cli", "warning", ("query", "err")),
    # [app] 应用边界（3.5 批：main.py 生命周期 + 全局异常兜底）
    "app starting": EventSpec("app starting", "app", "info"),
    "app stopping": EventSpec("app stopping", "app", "info"),
    "stale locks cleared": EventSpec("stale locks cleared", "app", "info", ("count",)),
    "locks clear failed": EventSpec("locks clear failed", "app", "warning", ("err",)),
    "biz error": EventSpec("biz error", "app", "error", ("code", "message")),
    "http error": EventSpec("http error", "app", "error", ("status", "detail")),
    "validation error": EventSpec("validation error", "app", "error", ("errors",)),
    "exception chain": EventSpec(
        "exception chain", "app", "error", ("depth", "type", "msg")
    ),
    "delegate skip": EventSpec(
        "delegate skip", "app", "warning", ("reason", "skills_dir")
    ),
    "capability degraded": EventSpec(
        "capability degraded", "app", "warning", ("resource", "reason")
    ),
    # prompt 模板启动期校验通过（字段为各段字符数，容器走紧凑 JSON）
    "prompt validated": EventSpec(
        "prompt validated", "app", "info", ("section_chars",)
    ),
    # [delegate] fork 子代理委派（delegate-hardening-observability）
    "delegate start": EventSpec(
        "delegate start",
        "delegate",
        "info",
        ("delegate_id", "skill", "thinking", "task_len"),
    ),
    "delegate model turn": EventSpec(
        "delegate model turn",
        "delegate",
        "info",
        (
            "delegate_id",
            "model",
            "usage_in",
            "usage_out",
            "usage_estimated",
            "latency_ms",
        ),
    ),
    "delegate end": EventSpec(
        "delegate end",
        "delegate",
        "info",
        ("delegate_id", "ok", "reason", "elapsed_ms", "result_len"),
    ),
    # [agent] 命令行直出跳过（skill_direct 节点兜底）
    "skill direct skip": EventSpec(
        "skill direct skip", "agent", "info", ("skill", "reason")
    ),
    # [agent] 直出轮确认门（design D18）
    "fork confirm asked": EventSpec(
        "fork confirm asked", "agent", "info", ("session_id",)
    ),
    "fork confirm unconfirmed": EventSpec(
        "fork confirm unconfirmed", "agent", "warning", ("session_id", "reason")
    ),
    # [session/llm/agent] 每轮来源与 prompt 观测（turn-provenance-observability）
    "agent resolved": EventSpec(
        "agent resolved",
        "session",
        "info",
        (
            "requested",
            "bound",
            "effective",
            "source",
            "persona_applied",
        ),
    ),
    "prompt assembled": EventSpec(
        "prompt assembled",
        "llm",
        "info",
        (
            "persona_source",
            "kb_bound",
            "has_skills",
            "kb_domain",
            "tool_count",
            "system_msgs",
            "section_chars",
        ),
    ),
    # system 段估算占 context window 比例超阈值（阈值驱动，仅告警不阻断）
    "prompt section share high": EventSpec(
        "prompt section share high",
        "llm",
        "warning",
        ("est_tokens", "share", "threshold"),
    ),
    # tiktoken 分词器不可用，token 计数降级为 len//2（环境级问题，仅告警一次）
    "token encoder unavailable": EventSpec(
        "token encoder unavailable", "llm", "warning", ("err",)
    ),
    "prompt messages": EventSpec(
        "prompt messages",
        "agent",
        "info",
        ("system_msgs", "injected_msgs", "history_msgs"),
    ),
    "skill injected": EventSpec(
        "skill injected",
        "session",
        "info",
        ("skill", "mode", "chars", "source"),
    ),
    "skill dispatch": EventSpec(
        "skill dispatch",
        "session",
        "info",
        ("kind", "skill", "context", "direct_skill"),
    ),
    # 知识库领域无对应 base 模板，回落通用领域（不阻断）
    "kb domain fallback": EventSpec(
        "kb domain fallback", "session", "warning", ("kb_id", "domain")
    ),
    # 预设与知识库领域不一致（用户自选行为，记日志但不阻断）
    "agent domain mismatch": EventSpec(
        "agent domain mismatch", "session", "info", ("agent", "domain")
    ),
    # 主链路 LLM 本层有界重试（首个 chunk 之前才重试；每次重试记 role/attempt/err）
    "model retry": EventSpec(
        "model retry", "agent", "warning", ("role", "attempt", "err")
    ),
    # 外部依赖降级留痕（reason 取值见 specs/degradation-policy 的取值集合表；不得自造）
    "degraded": EventSpec(
        "degraded",
        "degradation",
        "warning",
        ("reason", "err", "idle_s", "tier"),
    ),
}
