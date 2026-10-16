"""统一事件定义层 — Event 枚举 / Signal 枚举 / ReplayEvent。

单一事实源：分层前缀允许集、信号类型、普通事件名与级别均在此定义并在 import 期校验。
EventSpec dataclass 与 EVENT_SPECS 注册表数据表在 log_event_specs.py，本模块 re-export；
src/core/logging.py 的 helper 只从本模块读取，禁止在别处硬编码前缀/事件字符串。

规范来源：docs/agents/logging-rules.md（唯一归属文档）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.core.log_event_specs import EVENT_SPECS, EventSpec

# 分层前缀允许集（logging-rules.md 前缀主表：6 处理层 + cli + app + delegate + wecom）
LOG_PREFIXES: frozenset[str] = frozenset(
    {
        "retrieval",
        "verify",
        "agent",
        "session",
        "db",
        "llm",
        "cli",
        "app",
        "delegate",
        "wecom",
        "clarify",
        "provider",
        "alert",
    }
)

# 信号行保留前缀（P1 Change 2 契约，独立于 [层] 前缀的已知例外）
SIGNAL_PREFIX = "retrieval_signal:"

_ALLOWED_LEVELS = frozenset({"info", "warning", "error"})


class Signal(str, Enum):
    """检索行为信号类型（retrieval_signal 行的 signal= 值）。"""

    RERETRIEVE = "reretrieve"
    TO_WEB = "to_web"
    ABSTAIN_AFTER_RETRIEVE = "abstain_after_retrieve"
    CITED = "cited"
    EMPTY_RESULT = "empty_result"
    INVALID_CITATION = "invalid_citation"


class Event(str, Enum):
    """普通事件枚举，值 = 事件名（log_event 的事件 key）。

    新增事件两步：1) 在此加成员；2) 在 EVENT_SPECS 登记对应 EventSpec。
    二者不一致会在本模块 import 时抛 AssertionError（import 期一致性校验）。
    事件名遵循 logging-rules.md：英文小写空格分隔、禁用函数/工具下划线名。
    """

    # [retrieval] 核心里程碑（3.1 试点存量，Task 3 转换调用点）
    SEARCH_DONE = "search done"
    HYBRID_DONE = "hybrid done"
    RERANK_SKIP = "rerank skip"
    RERANK_DONE = "rerank done"
    RERANK_FAILED = "rerank failed"
    RERANK_TIMEOUT = "rerank timeout"
    RETRIEVE_DONE = "retrieve done"
    WEB_SEARCH_DONE = "web search done"
    RETRIEVE_REPLAY = "retrieve replay"
    DEDUP_DONE = "dedup done"

    # [agent] agent 循环 / 图 / 编排事件（3.3 批迁移登记）
    ITERATION_DONE = "iteration done"
    ITERATION_LIMIT = "iteration limit"
    HISTORY_BUDGET_EXCEEDED = "history budget exceeded"
    CONTEXT_BUDGET_HIGH = "context budget high"
    MODEL_TURN = "model turn"
    GRAPH_COMPILED = "graph compiled"
    FORMAT_DONE = "format done"
    EVENT_CONVERT_FAILED = "event convert failed"
    SERVICE_READY = "service ready"
    AGENT_BIND_IGNORED = "agent bind ignored"

    # [verify] 验证管道事件（3.2 批迁移登记：态A 引用引导 / 态B 完整性+护栏）
    SKIP = "skip"
    COMPLETENESS_CHECK = "completeness check"
    WEB_CONFIRM_ASK = "web confirm ask"
    WEB_CONFIRM_RESULT = "web confirm result"
    REGEN_STOP = "regen stop"
    CITATION_GUIDE = "citation guide"

    # [session] 会话管理事件（3.4 批迁移登记：chat manager / persistence / streaming）
    REDIS_READY = "redis ready"
    REDIS_FALLBACK = "redis fallback"
    HISTORY_WRITE_FAILED = "history write failed"
    HISTORY_READ_FAILED = "history read failed"
    HISTORY_CLEAR_FAILED = "history clear failed"
    SESSION_SAVE_FAILED = "session save failed"
    MESSAGE_SAVE_FAILED = "message save failed"
    TASK_REGISTERED = "task registered"
    # [session] 预设预绑定 skill 预加载跳过（声明名查不到，不影响其余技能）
    SKILL_PRELOAD_SKIP = "skill preload skip"
    SKILL_RESOLVED = "skill resolved"
    # [session] 跨轮历史摘要成功写入（覆盖条数 / 摘要 token 量）
    SUMMARY_DONE = "summary done"
    # [session] 跨轮历史摘要降级（未采用摘要，回退纯裁剪）
    SUMMARY_FALLBACK = "summary fallback"

    # [db] 数据库层事件（3.4 批迁移登记：vector_store / file_store）
    BUCKET_CREATED = "bucket created"
    FILE_UPLOAD_FAILED = "file upload failed"
    FILE_DOWNLOAD_FAILED = "file download failed"
    FILE_DELETE_FAILED = "file delete failed"
    CHUNKS_ADDED = "chunks added"
    CHUNKS_DELETED = "chunks deleted"
    CHUNKS_READ = "chunks read"
    CHUNKS_READ_FAILED = "chunks read failed"
    COLLECTION_DELETED = "collection deleted"
    COLLECTION_DELETE_FAILED = "collection delete failed"
    SEARCH_STATS = "search stats"
    SEARCH_RESULT = "search result"

    # [llm] LLM 调用层事件（3.4 批迁移登记：prompt / LLM 内容深日志）
    PROMPT_FETCHED = "prompt fetched"
    PROMPT_FETCH_FAILED = "prompt fetch failed"
    PROMPT_FALLBACK = "prompt fallback"
    CONTENT_START = "content start"
    CONTENT_END = "content end"

    # [cli] 离线工具事件（3.5 批迁移登记：eval_ragas / eval_ragas_generate /
    # check_retrieval / compare_rewrite）
    TESTSET_LOADED = "testset loaded"
    EVALUATION_RUN = "evaluation run"
    EVALUATION_START = "evaluation start"
    EVALUATION_DONE = "evaluation done"
    EVAL_MODEL_INIT = "eval model init"
    RAG_COMPONENT_INIT = "rag component init"
    VECTOR_STORE_CHECK = "vector store check"
    VECTOR_STORE_EMPTY = "vector store empty"
    RAGAS_MODEL_MISSING = "ragas model missing"
    ANSWERS_GENERATION = "answers generation"
    QA_ANSWER_START = "question answer start"
    QA_ANSWER_DONE = "question answer done"
    QA_ANSWER_FAILED = "question answer failed"
    METRIC_MEAN = "metric mean"
    METRICS_NAN = "metrics nan"
    RESULTS_SAVED = "results saved"
    REPORT_SAVED = "report saved"
    EVAL_REPORT_SAVED = "eval report saved"
    EVAL_SAVE_FAILED = "eval save failed"
    VERTEXAI_STUB_CREATED = "vertexai stub created"
    QUESTION_PROOFREAD_FAILED = "question proofread failed"
    QUESTION_PROOFREAD_DONE = "question proofread done"
    KB_META_FAILED = "kb meta failed"
    CHUNK_LOAD_START = "chunk load start"
    CHUNKS_NOT_FOUND = "chunks not found"
    NO_CHUNK_DATA = "no chunk data"
    CHUNK_LOAD_DONE = "chunk load done"
    GENERATOR_INIT = "generator init"
    TRANSFORMS_SETUP = "transforms setup"
    KNOWLEDGE_GRAPH_FOUND = "knowledge graph found"
    KNOWLEDGE_GRAPH_BUILD = "knowledge graph build"
    KNOWLEDGE_GRAPH_SAVED = "knowledge graph saved"
    TESTSET_GENERATION_START = "testset generation start"
    TESTSET_SAVED = "testset saved"
    TESTSET_SELF_CONTAINED_DROPPED = "testset self-contained dropped"
    METRIC_RETRY = "metric retry"
    JUDGE_CALL = "judge call"
    KB_LOOKUP = "kb lookup"
    KB_NOT_FOUND = "kb not found"
    SEARCH_START = "search start"
    SEARCH_FAILED = "search failed"
    LLM_INVOKE_FAILED = "llm invoke failed"
    LLM_PARSE_FAILED = "llm parse failed"
    RERANK_ERROR = "rerank error"

    # [app] 应用边界事件（3.5 批迁移登记：main.py 生命周期 + 全局异常兜底）
    APP_STARTING = "app starting"
    APP_STOPPING = "app stopping"
    STALE_LOCKS_CLEARED = "stale locks cleared"
    STALE_LOCKS_CLEAR_FAILED = "locks clear failed"
    BIZ_ERROR = "biz error"
    HTTP_ERROR = "http error"
    VALIDATION_ERROR = "validation error"
    EXCEPTION_CHAIN = "exception chain"
    # skill 委派跳过（AgentService 装配期：skills 目录缺失/注册表为空）
    DELEGATE_SKIP = "delegate skip"
    # 能力清单读取失败降级（Task 9：/api/skills、/api/agents fail-open 空列表）
    CAPABILITY_DEGRADED = "capability degraded"
    # prompt 模板启动期校验通过（fail-fast：失败直接抛错、不记本事件）
    PROMPT_VALIDATED = "prompt validated"

    # [delegate] fork 子代理委派（delegate-hardening-observability）
    DELEGATE_START = "delegate start"
    DELEGATE_MODEL_TURN = "delegate model turn"
    DELEGATE_END = "delegate end"

    # [agent] 命令行直出跳过（skill_direct 节点：未命中 / 非 fork skill → 兜底）
    SKILL_DIRECT_SKIP = "skill direct skip"
    # [agent] 直出轮确认门（design D18）：命中 marker 向用户提问 / 未能确认按未确认处理
    FORK_CONFIRM_ASKED = "fork confirm asked"
    FORK_CONFIRM_UNCONFIRMED = "fork confirm unconfirmed"

    # [session] 本轮生效智能体与来源（turn-provenance；两者全空时不记）
    AGENT_RESOLVED = "agent resolved"
    # [llm] system prompt 组成（人设来源、条件注入、system 段数、各段字符数）
    PROMPT_ASSEMBLED = "prompt assembled"
    # [llm] system 段估算占 context window 比例超阈值（只告警不阻断）
    PROMPT_SECTION_SHARE_HIGH = "prompt section share high"
    # [llm] tiktoken 分词器不可用，token 计数降级为 len//2
    TOKEN_ENCODER_UNAVAILABLE = "token encoder unavailable"
    # [agent] 首轮组装的消息构成（system / 注入 / 历史三段条数）
    PROMPT_MESSAGES = "prompt messages"
    # [session] 技能正文成功注入（inline 命令触发 / 首轮预设预加载）
    SKILL_INJECTED = "skill injected"
    # [session] 命令形态分派结果（kind=plain 不记，避免每轮噪声）
    SKILL_DISPATCH = "skill dispatch"
    # [session] 知识库领域无对应 base 模板，回落通用领域（不阻断）
    KB_DOMAIN_FALLBACK = "kb domain fallback"
    # [session] 预设与知识库领域不一致（用户自选行为，记日志但不阻断）
    AGENT_DOMAIN_MISMATCH = "agent domain mismatch"

    # [agent] 主链路 LLM 本层有界重试留痕（timeout-and-degradation-hardening D16：
    # 首个 chunk 之前才重试，每次重试记 role/attempt/err）
    MODEL_RETRY = "model retry"


@dataclass(frozen=True)
class ReplayEvent:
    """检索重放上下文（retrieve replay 事件行字段容器）。

    query: 检索词全文（重放输入）
    query_len: 检索词字符数
    kb_id: 知识库 ID（空 = 未绑定）
    iteration: agent 迭代序号
    top_k: 工具层返回上限（精排后截断）
    hybrid: 是否启用混合检索
    rerank: 是否执行精排
    """

    query: str
    query_len: int
    kb_id: str
    iteration: int
    top_k: int
    hybrid: bool
    rerank: bool


def _validate_registry(events: set[str], specs: dict[str, EventSpec]) -> None:
    """校验 Event 成员集与 EVENT_SPECS 一致且元数据合法（import 期调用）。"""
    if events != set(specs):
        missing = events - set(specs)
        extra = set(specs) - events
        raise AssertionError(
            "Event 成员与 EVENT_SPECS 不一致 "
            f"(缺 spec: {sorted(missing)}; 多余 spec: {sorted(extra)})"
        )
    for name, spec in specs.items():
        if spec.name != name:
            raise AssertionError(f"EventSpec name 与 key 不一致: {spec.name} != {name}")
        if spec.prefix not in LOG_PREFIXES:
            raise AssertionError(
                f"事件 {name} 前缀 {spec.prefix!r} 未在 LOG_PREFIXES 登记"
            )
        if spec.level not in _ALLOWED_LEVELS:
            raise AssertionError(f"事件 {name} 级别非法: {spec.level}")


_validate_registry({e.value for e in Event}, EVENT_SPECS)
