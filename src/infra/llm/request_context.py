"""Per-request 上下文 — 通过 contextvar 传递给 agent 循环内的工具与节点。

工具闭包在 AgentService 初始化时构建、跨请求共享，无法持有 per-request 对象；
graph 在同一 asyncio task 执行，contextvar 自动传播到工具与 async 节点。
因此用 ContextVar 承载单次 /chat/stream 请求的共享对象集合（RequestContext），
请求入口 set、请求结束 reset，工具/节点经 current_request_ctx.get() 读取。

注意：工具收集的上下文统一挂在 current_request_ctx.get().tool_contexts 上
（retrieve_kb 与 search_web 共享，全局递增编号），
不单独定义独立的 current_tool_contexts ContextVar —— ContextVar(default=list())
的 default 在模块加载时求值一次，所有请求会共享同一个 list（并发污染陷阱）。
"""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field

from src.rag.context import RAGContext


@dataclass
class RequestContext:
    """单次 /chat/stream 请求的共享对象集合，由请求入口创建并 set 到 current_request_ctx。"""

    session_id: str  # 会话 ID（请求/URL 参数来源），范围：整个请求生命周期，用途：路由到 registry 挂起澄清
    kb_id: str = ""  # 知识库 ID（来源：请求入口 kb_id 参数；范围：请求内只读；用途：search_web 产 to_web 信号时携带 + 判定态 B 绑定）
    kb_bound: bool = False  # 是否绑定知识库（来源：请求入口由 bool(kb_id) 派生；范围：请求内只读；用途：to_web 缺陷信号仅态 B（kb_bound=True）自主降级时产出）
    clarify_channel: asyncio.Queue = field(
        default_factory=asyncio.Queue
    )  # 澄清事件/SSE 事件通道，范围：请求内共享，用途：工具请求澄清时投递事件
    abort_signal: asyncio.Event = field(
        default_factory=asyncio.Event
    )  # 断连/取消信号，范围：请求内共享，用途：客户端断开或取消时置位以中断 agent 循环
    registry: dict = field(
        default_factory=dict
    )  # [已弃用] session_id -> asyncio.Future（挂起澄清），范围：请求内共享；POST/SSE 是独立请求，contextvar 不跨请求，请改用模块级 pending_asks（保留字段避免破坏既有引用）
    tool_contexts: list[RAGContext] = field(
        default_factory=list
    )  # retrieve_kb / search_web 共享累积上下文，范围：请求内累积，用途：按编号顺序拼装引用（编号顺序即引用顺序）
    retrieval_trace: list[dict] = field(
        default_factory=list
    )  # 每次 retrieve_kb 的结构化上下文快照（chunk_id/doc_id/entities/order），范围：请求内累积；用途：写入 Langfuse trace metadata 供单条 trace 回放
    ask_count: int = (
        0  # 澄清提问计数，范围：请求内累积，用途：限制单次请求的澄清轮数上限
    )
    web_count: int = (
        0  # search_web 调用计数，范围：请求内累积，用途：限制单轮联网搜索次数上限
    )
    retrieve_call_seq: int = 0  # 同 turn retrieve_kb 调用次数（来源：retrieve_kb 入口自增；范围：请求内累积；用途：reretrieve 换词信号判定（call_seq >= 2））
    temporal_years: list[int] = field(
        default_factory=list
    )  # 时间解析出的要求覆盖年份（来源：时间解析层；用途：验证循环完整性验收标准）
    missing_years: list[int] = field(
        default_factory=list
    )  # 知识库缺失年份（来源：时间解析比对；用途：询问用户是否联网的依据）
    web_confirmed: bool = False  # 本轮请求内已确认联网（来源：验证循环询问用户后置位；用途：本轮后续缺失年份不再询问；跨轮持久化留 P1）
    verify_ask_count: int = 0  # verify"是否联网"询问计数（来源：verify 节点自增；用途：每轮最多询问 1 次，独立于 ask_count）
    web_guided: bool = False  # 本轮 verify 指派联网检索标记（来源：verify 节点注入联网指引时置位（Task 2）；范围：请求内有效；用途：search_web 据此排除 verify 指派联网的 to_web 误报）
    temporal_parsed: bool = False  # 本轮是否已完成时间解析（来源：retrieve_kb 时间解析块置位；用途：turn 内最多解析一次，避免重复 DB+LLM 且不覆盖已置位约束）
    deep_thinking: bool = False  # 请求级深思考开关（来源：chat/stream 请求入口 deep_thinking 参数；范围：请求内只读；用途：fork 未声明 thinking 时决定 enable_thinking）
    agent: str = ""  # 会话绑定智能体名（来源：Plan 3 由 sessions.agent 写入；范围：请求内只读；用途：fork 执行者选择的第二优先级；空=未绑定→系统默认）
    persona: str = ""  # 会话智能体人设正文（来源：Plan 3 T5 由预设 system_prompt 写入；范围：请求内只读；用途：build_system_prompt 人设层；空=未选 agent）
    kb_domain: str = "general"  # 知识库领域（来源：AgentService 按 kb_id 查库并校验；范围：请求内只读；用途：base 段三选一取领域默认视角；未绑定或领域未知时回落 general）
    tool_names: frozenset[str] = (
        frozenset()
    )  # 本轮实际注册的工具名（来源：AgentService 由 build_graph 填充的工具池派生；范围：请求内只读；用途：verify 运行期指引的条件渲染判据（src/agents/graph/verify/regen_decision.py）；组装路径另由节点工厂带入同一值）
    has_skills: bool = False  # 本会话是否有可用技能（来源：Plan 3 T5 由 skill_registry.model_visible() 写入；范围：请求内只读；用途：决定人设非空时是否追加委派引导段）
    known_skill_names: set[str] = field(
        default_factory=set
    )  # 可 `user_visible()` 调用的技能名集合（来源：Plan 3 T5 写入；范围：请求内只读；用途：clean_prefix 读时清洗的历史/当前轮名单）
    agent_display_name: str = ""  # 生效智能体的展示名（来源：Plan 3 会话预设 display_name，缺失回落 name）；范围：请求内只读；用途：SSE 来源声明文案
    loaded_skills: list[str] = field(
        default_factory=list
    )  # 本轮成功加载的技能名（来源：inline 命中记录规范名 / 首轮预加载回传名单；范围：本轮；用途：SSE 技能声明一条列全；fork/unknown/none 留空）
    skill_action: str = "none"  # 本轮技能动作模式（none|inline|preload|fork|unknown；来源：stream_chat 依 parse_prefix 三态与 record.context 判定）；范围：请求内只读；用途：决定是否发技能声明与发哪条文案
    delegate_id: str = ""  # 委派 id（来源：delegate_task 生成 DelegateRun.delegate_id，executor._run_fork 写入 run.ctx 子 ctx；范围：单次委派子上下文；用途：fork_stream 读作 delegate 增量/事件与日志贯穿标识；主 ctx 恒为空串）
    fork_stop_reason: str | None = (
        None  # fork 停止原因（来源：executor / fork_stream 中断时把 DelegateStopReason 值写入 run.ctx 子 ctx；范围：单次委派子上下文；用途：executor 回写 run.stop_reason、delegate_task 终态区分 normal 与中断；None=正常完成或未执行）
    )

    def child(self) -> "RequestContext":
        """派生一个子代理上下文：共享通道与取消信号，独立引用池与计数。

        用途：fork 子代理在自己的引用池里检索与编号，不污染主 agent（design D7/D24）。
        共享项：clarify_channel / abort_signal —— 委派事件必须能回到同一条 SSE 通道，
        取消必须能即时中断子代理；二者若各持一份，事件与取消都会断链。

        Returns:
            新的 RequestContext；tool_contexts / 各计数 / temporal_* / missing_years /
            web_* 全部为默认初值（子代理独立累计）。
        """
        return RequestContext(
            session_id=self.session_id,
            kb_id=self.kb_id,
            kb_bound=self.kb_bound,
            clarify_channel=self.clarify_channel,
            abort_signal=self.abort_signal,
            deep_thinking=self.deep_thinking,
            kb_domain=self.kb_domain,
            tool_names=self.tool_names,
        )


current_request_ctx: ContextVar[RequestContext | None] = ContextVar(
    "current_request_ctx", default=None
)
"""当前请求共享对象；工具/节点经此读取 queue/abort/tool_contexts/ask_count/web_count。"""

pending_asks: dict[str, asyncio.Future] = {}
"""进程级挂起澄清注册表（session_id -> asyncio.Future）。

ask_user 工具登记挂起 Future，POST /clarify-answer 按 session_id 解析；
与 RequestContext.registry 不同，它是进程级共享的——POST 与 SSE 是独立
HTTP 请求，contextvar 不跨请求传播，per-request dict 无法被 POST 端访问。
"""
