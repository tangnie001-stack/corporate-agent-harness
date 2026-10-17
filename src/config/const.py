"""诊断日志标签、业务常量。"""

import re
import uuid
from enum import Enum
from typing import ClassVar, Final


class _Labels(dict):
    """标签字典，get() 无值时返回空字符串。"""

    def get(self, key, *args):
        return super().get(key, args[0] if args else "")


# ── 日志标签字典 ──
ROUTE_LABELS = _Labels(
    {
        "simple": "skip_retrieval",
        "medium": "go_to_rewrite",
        "complex": "go_to_rewrite",
    }
)

GENERATE_LABELS = _Labels(
    {
        True: "fallback_to_naive_rag",
        False: "enhanced_rag",
    }
)


# ── 实体抽取常量 ──
# 核心实体类型：文档级属性，渲染进 prompt 支撑 faithfulness 锚点
ENTITY_TYPES: tuple[str, ...] = ("company", "report_period", "sec_code")
# 核心实体渲染顺序（to_prompt_text 按此顺序渲染存在的实体）
ENTITY_RENDER_ORDER: tuple[str, ...] = ("company", "report_period", "sec_code")
# 实体渲染中文标签（to_prompt_text 渲染实体时的展示名，未知键回退为原始键）
ENTITY_LABELS: dict[str, str] = {
    "company": "公司",
    "report_period": "期间",
    "sec_code": "代码",
}
# 可选实体：LLM 兜底顺带返回，仅补充字段不渲染
ENTITY_OPTIONAL_TYPES: tuple[str, ...] = ("person", "currency", "report_type")
# 实体抽取完整三层流水线的文件类型（其余如 txt 走文件名+LLM）
ENTITY_FULL_PIPELINE_TYPES: tuple[str, ...] = ("pdf", "docx")
# search_web extract 拉取的正文上限（字符），防长网页撑爆上下文窗口
WEB_BODY_LIMIT: int = 2000


# ── agent 循环护栏常量 ──
# 来源：agentic 改造需求（2026-08-26 phase1）；用途：agent 循环的迭代/追问/历史注入/并发控制
MAX_AGENT_ITERATIONS = 5  # agent 循环最大迭代数，超限强制收尾
# 单轮内累积输入 token 的告警上限。与 MAX_AGENT_ITERATIONS 联动：迭代越多、
# 工具结果累积越大；本阶段只度量告警，不做任何压缩（压缩属摘要层）。
# 初值待标定：按 MAX_AGENT_ITERATIONS=5 轮、单轮工具结果量级推算的保守高水位
# （实测同轮曾达约 6.2 万 token），待按线上 Langfuse token 分布标定后再调。
TURN_INPUT_TOKEN_LIMIT = 65536
MAX_ASK_PER_TURN = 2  # 单 turn 内 ask_user 最大调用次数
MAX_VERIFY_ASK_PER_TURN = (
    1  # verify"是否联网"询问每轮上限（独立计数，不计入 MAX_ASK_PER_TURN）
)
# verify 修订保险丝上限：正常被决策化（看 agent 上一轮 search_web queries）提前终止，
# 仅在 agent 反复不按指引执行时兜底防死循环
MAX_VERIFY_REGENERATIONS = 2
# verify 联网指引 SystemMessage 的标记短语：注入与查重共用（避免裸字符串耦合，
# 注入文案改了而查重漏改会破坏防重复注入逻辑）
VERIFY_GUIDANCE_MARKER: str = "用户已确认联网"
# verify "一次带全" hint SystemMessage 的标记短语：完整指引已注入后 agent 上一轮
# search_web queries 仍带漏缺失年份时，独立补发一条带全提示（重申轮也须送达，
# 是"还缺哪些年 + 一次带全再查"的新信息；注入与查重共用此短语防重复堆叠）
VERIFY_HINT_MARKER: str = "一次带全以下年份"
# verify 联网引用标注指引 SystemMessage 的标记短语（无 KB 场景，2026-09-02）：
# 本轮调过 search_web 但回答未带 [n] 来源编号时注入，驱动 agent 补标注后重生成一次
VERIFY_CITATION_MARKER: str = "请为联网引用标注来源编号"
# verify KB 强制溯源指引 SystemMessage 的标记短语（态 B）：检索到 KB context 但答案无
# [n] 时注入，驱动 agent 补标后重生成一次（完整性通过后、judge 前，只引导一次）
VERIFY_KB_CITATION_MARKER: str = "请为知识库引用标注来源编号"
FORK_CONFIRM_MARKER: str = "CONFIRM_REQUIRED:"
"""子代理"需确认"信号行首标记（编排层规则检测；子代理不持有 ask_user）。"""
ASK_USER_TIMEOUT = 120  # ask_user 等待用户回答超时秒数
HISTORY_MAX_TURNS = 10  # 历史注入保留最近轮数（轮数粗筛）
# 历史注入的 token 预算（**绝对值**，与模型窗口解耦）。原「窗口 × 比例」口径已废弃：
# 比例会随窗口漂移，且窗口值本身是假设值。初值取保守量级（远小于任一模型窗口），
# 待按线上 Langfuse token 分布标定后再调。
HISTORY_TOKEN_BUDGET = 16384
# per-session 并发锁 TTL 秒：须大于 ASK_USER_TIMEOUT（ask_user 挂起等待期间锁不能提前
# 过期，否则并发兜底失效），在超时基础上留 60s 余量
SESSION_LOCK_TTL = ASK_USER_TIMEOUT + 60
# 候选年份并入的最近完整年度数（排除进行中的当年）：KB 只覆盖 2024 时"这几年"仍能
# 解析出 [2023, 2025] 等缺失年份触发联网询问
TEMPORAL_RECENT_N_YEARS = 3
# 跨轮历史摘要：触发判据落在「将被丢弃的那一段」的 token 量上（不是全量历史——
# 保留尾部覆盖全量时被丢弃段为空，以全量判据会导致每轮调用摘要模型却永不落地）。
# 经验值：0.75 × HISTORY_TOKEN_BUDGET，给摘要产出留 1/4 余量，待线上观测标定。
HISTORY_SUMMARY_TRIGGER_TOKENS = 12288
# 摘要产出的输出上限（token）。经验值来源：本地参考项目 deepseek-harness 摘要上限 8192、
# WeKnora 提示 "under 500 words"、ragflow 摘要预算 1200 字符；再受本项目
# 「摘要段 + 尾部 ≤ HISTORY_TOKEN_BUDGET」聚合约束收敛。该上限同时是
# 「拒绝截断摘要」不变量的判据前提（无上限则截断无从判定）。
SUMMARY_MAX_TOKENS = 2048
# 摘要并发锁 TTL（秒）：独立于 SESSION_LOCK_TTL——回合收尾时轮次锁即将释放，
# 两者语义与生命周期不同，不可复用同一键。取摘要调用超时 + 30s 余量。
SUMMARY_LOCK_TTL = 90
# 摘要模型调用的超时保险丝（秒）：超时按失败降级，防长尾占用锁。
SUMMARY_TIMEOUT_S = 60

# ── delegate（主从委派）护栏常量 ──
# 来源：agent-delegation-skills change（fork 超时/结果截断/inline 规模约束）+
# delegate-hardening-observability change（三层防失控：事件级空闲 / 总时长保险丝 / turn 上限）
MAX_DELEGATE_BONUS = (
    2  # delegate 轮后主 agent 迭代上限放宽轮数（整合余量，单请求总上限仍封顶）
)
DELEGATE_DEFAULT_MAX_TURNS = 5  # fork 子代理默认 turn 上限（防御）
DELEGATE_RESULT_LIMIT = 1000  # fork 结果回流主 agent 的截断阈值（字符）
# 委派路径标识（DelegateRun.via）：执行器据此决定给子代理的引用编号指示（design D16）。
# direct = /xxx 直出（**没有主 agent 补标** → 子代理须自检索并自标 [n]）；
# delegate = 主 agent 委派（[n] 由主 agent 按自身检索来源补标 → 子代理不标）。
DELEGATE_VIA_DIRECT = "direct"
DELEGATE_VIA_DELEGATE = "delegate"
INLINE_PROMPT_MAX_CHARS = 500  # inline skill 正文规模上限（字符，防上下文累积膨胀）；未声明 context 时超出即自动改用 fork，显式 inline 时仅记 warning
# skill / agent preset 名允许的字符集（ASCII slug）：/xxx 命令天然是 ASCII 惯例，
# 非 ASCII 名会让 `/财报分析` 落进"非命令形态"分支被静默当普通文本（design D15）
CAPABILITY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
DEPRECATED_SKILL_FIELDS = (
    "thinking",
    "max-iterations",
)  # 已废弃的 skill frontmatter 字段（读到时忽略并记 warning）
# skill 正文任务占位符：$ARGUMENTS 为当前写法，{task} 为旧写法（保留向后兼容）；
# 渲染时按序全部替换（见 skills.rendering.render_skill_body）
SKILL_TASK_PLACEHOLDERS = ("$ARGUMENTS", "{task}")
# fork 子代理永不可持有的工具名：
# ask_user —— D18：子代理不直接交互，需要确认时由编排层确认门代为询问；
# delegate_task —— D7：子代理不再委派，防递归与上下文爆炸。
FORK_FORBIDDEN_TOOLS = ("ask_user", "delegate_task")
# fork 子代理永不可持有的主 agent 专属工具**前缀**（design D8 第三档）：
# task_*（task_create/get/list/update/output/stop）是主 agent 的执行面（任务看板），
# 子代理是被委派的执行体，持有它会让子代理看见并改写主 agent 的任务编排。
# 与上面两项分列而不合并：三档理由不同（语义禁止 / 防递归 / 角色专属）。
# 用前缀而非逐名枚举：将来新增 task_* 工具时自动被挡，不必同步这份清单。
FORK_EXCLUSIVE_TOOL_PREFIXES = ("task_",)
SKILL_INJECTION_PREFIX: str = "[[skill-injection]]"
"""注入型隐藏消息的内容前缀标记。

用途：① `sessions/messages` 据此过滤（前端不展示）；② `agent_node._split_initial_messages`
据此把该行抽成独立 HumanMessage（模型可见）。标记必须是 ASCII 且用户不可能自然打出。
"""
# 专家分析标记短语：fork 子代理"无源分析观点"由 4.1 引导主 agent 措辞（design D9），
# kb_citation_guardrail 据此豁免（防纯分析型 fork 答案被误触发补标 regen，M7）
EXPERT_ANALYSIS_MARKER = "基于领域经验的分析"


class DelegateStopReason(str, Enum):
    """fork 结束原因统一枚举（delegate end ok/reason 与 task 注册表终态共用词表）。

    取值：normal=正常完成 / idle=流空闲超时 / total=总时长保险丝 /
    turn=轮次上限 / failed=执行异常 / cancelled=请求取消。
    契约/文档/前端文案不得另起原因词（spec delegate-execution-controls）。
    """

    NORMAL = "normal"
    IDLE = "idle"
    TOTAL = "total"
    TURN = "turn"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType:
    """任务看板条目类型（task-board change）。

    分工：plan = 主 agent 经 Task 工具创建的计划/跟踪项；
    execution = delegate fork 自动登记的执行追踪（task_id=delegate_id）。
    """

    PLAN: str = "plan"  # 主 agent 计划项
    EXECUTION: str = "execution"  # delegate 执行追踪


class TaskStatus:
    """任务条目的展示状态（前端胶囊/徽标依据；中断原因另存 reason）。

    终态映射（execution）：reason=normal → done；failed → failed；
    idle/total/turn → timeout（reason 保留具体枚举值）；cancelled → cancelled。
    """

    PENDING: str = "pending"  # 待处理（plan 建项默认）
    RUNNING: str = "running"  # 进行中（execution 运行中 / plan 手动置）
    DONE: str = "done"  # 完成
    FAILED: str = "failed"  # 失败
    TIMEOUT: str = "timeout"  # 超时中断（idle/total/turn）
    CANCELLED: str = "cancelled"  # 取消


class Channel:
    """会话来源渠道标识（日志行第 5 段与 Langfuse trace metadata.channel）。

    由各渠道入口在跑一轮之前写入 `current_channel`（见
    `src/infra/llm/trace_context.py`），供日志 patcher 与 Langfuse 落点读取；
    未设置时以 `CHANNEL_DEFAULT` 占位（不得留空段，也不得用 `-`）。
    """

    WEB: str = "web"  # 站点（HTTP API）
    WECOM: str = "wecom"  # 企业微信（长连接 / 回调）
    FEISHU: str = "feishu"  # 预留：飞书渠道
    ALERT: str = "alert"  # 阿里云告警自动分析轮次


# 渠道未设置时的日志占位（非空、不含 "|" 与 " - "，保证日志行可解析）
CHANNEL_DEFAULT: str = "none"


# execution 自动登记标题模板（task-board）：{skill} 为命中 skill 名；
# 放 const 集中管理（CLAUDE.md 硬编码集中规则），delegate_task 登记用
DELEGATE_TASK_TITLE_TMPL = "{skill} · 领域专家分析"


# ── 检索条数上限 ──
# 查询条数硬上限：源自 Chroma 时代的 100 上限，PG 本身无此限制；保留它是为了
# 不把「检索条数的能力提升」混进 dense 迁移等价性验收
MAX_QUERY_K = 100


# ── 词法检索分词 ──
# 词项长度下限：中文高频虚词与量词本身就是单字（本/及/不/年/月/了/吗），
# 文档频率接近 1，纳入检索文本会淹没精确词项的排序；该过滤同时去掉空格与
# 中文标点（长度均为 1），因此不需要另建停用词表
LEXICAL_MIN_TOKEN_LEN = 2


# ── 检索精排超时 ──
# 已迁至 `settings`（change timeout-and-degradation-hardening 的 P1）：
#   内层 read 预算 `RERANK_TIMEOUT_READ`（5s，唯一预算，attempts=1 见 `RERANK_MAX_ATTEMPTS`）、
#   外层 `wait_for` 兜底 `RERANK_OUTER_TIMEOUT`（6s，必须 > 内层）。
# 原 `RERANK_TIMEOUT = 5` 已随该变更退役（P1 收口时清理）。
# ⚠ 勿与事件枚举 `Event.RERANK_TIMEOUT`（"rerank timeout"）混淆 —— 那个仍在使用。


# ── SSE 交互事件文案与 stage 标识 ──
# 来源：agentic 改造需求（2026-08-26 phase1）；用途：SSE 事件与用户的交互说明
# （展示文案 + stage 标识）统一收敛，供 agent_service / rag_tools / clarify / nodes 引用
class SSEInteractionTexts:
    """SSE 与用户交互相关的事件文案与 stage 标识常量。

    stage 标识：SSEStatusEvent.stage 字段取值，前端按 message 展示、不依赖 stage 分支；
    事件文案：SSE 事件直接展示给用户或返回给 LLM（ask_user 工具错误）的文本。
    """

    # ── Abstention / 拒答 ──
    # 拒答语检测关键词：回答命中任一关键词时，format_node 不输出引用
    ABSTENTION_MARKERS: tuple[str, ...] = ("未在文档中找到",)

    # ── Web 搜索兜底 ──
    # web 兜底回答文案：命中此文案但带 [n] 引用时保留引用（区别于纯拒答）
    WEB_SEARCH_PHRASE: str = "该问题不在当前知识库范围内"
    # search_web 达每轮限次提示（返回给 LLM，促其基于现有信息作答）
    WEB_SEARCH_LIMIT_TEXT: str = "Error: 已达本轮联网搜索上限，请基于现有信息作答"
    # 引用来源类型：RAGContext.kind 与 citation.kind 取值
    CITATION_KIND_KB: str = "kb"
    CITATION_KIND_WEB: str = "web"
    # SSEStatusEvent.stage：search_web 工具阶段（start/end 双文案）
    STAGE_WEB_SEARCH: str = "web_search"
    WEB_SEARCH_STATUS_START: str = "正在联网搜索..."
    WEB_SEARCH_STATUS_END: str = "联网搜索完成，正在分析..."

    # ── SSEStatusEvent.stage 标识 ──
    # on_chat_model_start（agent 节点）对应 stage：模型开始思考
    STAGE_AGENT: str = "agent"

    # on_tool_start/on_tool_end（retrieve_kb）对应 stage：检索中/完成
    STAGE_RETRIEVE: str = "retrieve"

    # ── 每轮来源声明 stage（turn-provenance）──
    # 本轮生效智能体声明专用 stage：不得复用 retrieve/web_search（前端
    # renderStatusTag 对这两者会触发"待定正文归为旁白"重分类）
    STAGE_TURN_AGENT: str = "turn_agent"

    # 本轮技能动作声明专用 stage：同上约束
    STAGE_TURN_SKILL: str = "turn_skill"

    # ── Agent 状态事件文案 ──
    # on_chat_model_start（agent 节点）→ SSEStatusEvent(STAGE_AGENT)：模型开始思考
    AGENT_STATUS_THINKING: str = "正在思考..."

    # on_tool_start（retrieve_kb）→ SSEStatusEvent(STAGE_RETRIEVE)：开始检索
    AGENT_STATUS_RETRIEVING: str = "正在检索相关文档..."

    # on_tool_end（retrieve_kb）→ SSEStatusEvent(STAGE_RETRIEVE)：检索完成
    AGENT_STATUS_RETRIEVED: str = "检索完成，正在分析..."

    # ── 降级：用户可见通道与取值集合（timeout-and-degradation-hardening P2）──
    # 降级告知专用 stage：SHALL NOT 复用 retrieve / web_search —— 前端 renderStatusTag
    # 对这两者会触发"待定正文归为旁白"的重分类（与 STAGE_TURN_* 同因）
    STAGE_DEGRADED: str = "degraded"

    # reason 取值集合 —— 唯一来源：docs/openspec/specs/degradation-policy/spec.md 的
    # 「降级原因与用户文案的取值集合」表；实现不得自造取值（含 P3 才消费的 generation_idle）
    DEGRADATION_REASONS: tuple[str, ...] = (
        "embedding_failed",
        "vector_store_failed",
        "llm_unavailable_with_context",
        "llm_unavailable_no_context",
        "llm_stream_interrupted",
        "provider_unavailable",
        "upstream_exhausted",
        "generation_idle",
    )

    # 仅「用户可见 = 是」的取值入本表（其余在实现侧不送达用户）。
    # provider_unavailable 的文案不得含"重试"语义：该类错误重试必然再失败。
    # upstream_exhausted / llm_* 属可恢复或流中断，可提示重试。
    DEGRADATION_TEXTS: ClassVar[dict[str, str]] = {
        "vector_store_failed": "知识库暂不可用",
        "llm_unavailable_with_context": "模型服务暂不可用；以下为已检索到的原文",
        "llm_unavailable_no_context": "模型服务暂不可用，请稍后再试",
        "llm_stream_interrupted": "回答未生成完整，请重试",
        "provider_unavailable": "服务暂不可用，本次无法作答",
        "upstream_exhausted": "服务繁忙，请稍后再试",
        "generation_idle": "仍在处理中，请稍候",
    }

    # ── SSE 错误事件文案 ──
    # SSEErrorEvent 统一错误前缀：_dual_stream（事件源异常）与 stream_chat（外层兜底）共用
    SSE_ERROR_PREFIX: str = "暂时无法回答："

    # ── 缓冲续传（_subscribe_buffer）──
    # tail 空闲超时文案：超过 max_idle 无新事件时以 error 事件返回，提示刷新页面
    RESUME_TIMEOUT_TEXT: str = "续传超时，请刷新页面"

    # ── ask_user 澄清工具文案 ──
    # ask_user 上下文不可用文案：current_request_ctx 未设置时直接返回给 LLM
    ASK_USER_CTX_UNAVAILABLE: str = "Error: 请求上下文不可用"

    # ask_user 达本回合询问上限文案：返回给 LLM 促其基于现有信息作答
    ASK_USER_LIMIT_REACHED: str = "Error: 已达本回合询问上限，请基于现有信息作答"

    # ask_user 等待期间答案 Future 被取消文案：POST 端取消挂起澄清时返回
    ASK_USER_ANSWER_CANCELLED: str = "Error: 等待用户回答被取消"

    # ask_user 等待期间请求被取消文案：abort 信号置位（客户端断开/取消）时返回
    ASK_USER_REQUEST_CANCELLED: str = "Error: 请求已取消"

    # ask_user 等待用户回答超时文案：超过 ASK_USER_TIMEOUT（const.py 秒数）未获答案时作为工具结果
    # 给 LLM，引导其基于已有上下文给出推荐方案（而非报错）
    ASK_USER_TIMEOUT_TEXT: str = (
        "（用户因超时未填写内容）请基于已有上下文给出推荐方案。"
    )

    # /chat/clarify-answer 404 文案：POST 解析挂起澄清时查无该 session 或 Future 已结束（超时/取消）
    CLARIFY_ANSWER_NOT_FOUND_TEXT: str = "该澄清问题已超时或不存在"

    # ── delegate_task 工具返回主 agent 的文本 ──
    # 未知 skill 返回模板：{skill}=请求的 skill 名；{available}=可用 skill 列表（空列表显示"无"）
    DELEGATE_UNKNOWN_SKILL: str = "skill 不存在: {skill}，可用 skill: {available}"
    # 通用委派（省略 skill）的用户可见标签：无 skill 名可填，作看板标题与事件 skill 字段
    DELEGATE_GENERIC_LABEL: str = "通用分析"
    # 会话级委派预算触顶返回模板：{limit}=本会话委派上限次数；促模型改用自身能力作答
    DELEGATE_BUDGET_EXHAUSTED: str = (
        "本轮委派已达会话上限（{limit} 次）。请改用你自己的能力作答，不要再重试委派。"
    )
    # 未注册前缀的用户可见文案：{skill}=请求的 skill 名；{available}=可用 skill 列表
    UNKNOWN_SKILL_PREFIX: str = "技能不存在：/{skill}。可用技能：{available}"
    # 已注册但 user-invocable:false 的前缀用户可见文案：{skill}=请求的 skill 名（spec:66 只能由模型调用）
    SKILL_USER_DISABLED: str = "技能 /{skill} 只能由模型调用，无法通过 /xxx 手动触发。"
    # fork 超时文案（delegate_task 返回给 LLM，促其基于现有上下文作答）
    DELEGATE_TIMEOUT_TEXT: str = "Error: 领域专家分析超时，请基于已有检索上下文作答"
    # fork 中断/委派终态文案：中断时以 {reason} 填 DELEGATE_REASON_TEXT 的中文短词
    DELEGATE_INTERRUPT_TEXT: str = "领域专家分析中断 · {reason}"
    # DelegateStopReason → 前端可读中文短词（双通道：文字 + 颜色）
    DELEGATE_REASON_TEXT: ClassVar[dict[str, str]] = {
        "idle": "空闲超时",
        "total": "超时",
        "turn": "轮次上限",
        "failed": "失败",
        "cancelled": "已取消",
    }
    # fork 结果截断前缀模板：{total}=完整字数；{truncated}=截断后的摘要文本
    DELEGATE_TRUNCATED_PREFIX: str = (
        "子代理已产出完整分析 {total} 字，摘要如下：\n{truncated}"
    )

    # ── 命令行直出兜底文案 ──
    # /xxx 命中不到 / 非 fork skill 时返回给用户的兜底文案（直出节点 fail-open）
    SKILL_DIRECT_UNAVAILABLE: str = "该技能不可直接执行，请去掉前缀后重试。"
    # 直出节点请求上下文不可用文案（current_request_ctx 未设置；用户可见兜底，纯中文）
    SKILL_DIRECT_CTX_UNAVAILABLE: str = "请求上下文不可用"

    # ── 每轮来源声明文案（turn-provenance）──
    # 生效智能体声明：{agent}=预设展示名（display_name，缺失回落 name）
    AGENT_IN_USE_TMPL: str = "当前使用了 {agent}"
    # 本轮技能声明：{skills}=成功解析的技能名列表（顿号连接；与 UNKNOWN_SKILL_PREFIX 同标点）
    SKILLS_LOADED_TMPL: str = "成功加载 skills：{skills}"
    # fork 轮技能声明：{skill}=fork 技能名（fork 不注入上下文，故称"使用"而非"加载"）
    SKILL_IN_USE_TMPL: str = "使用技能：/{skill}（子代理执行）"

    # ── 直出轮确认门文案 ──
    # 确认被拒/超时/槽被占时拼在答案尾部的"未经确认"标注（用户可见）
    CONFIRM_UNCONFIRMED_NOTE: str = "\n\n> 注：本结论未经用户确认，仅供参考。"
    # 确认提问模板：{question}=子代理经 FORK_CONFIRM_MARKER 提出的确认问题
    CONFIRM_QUESTION_TMPL: str = "执行该技能需要你确认：{question}"


# ── Langfuse trace 保留期清理（purge_langfuse_traces CLI）──
# 来源：langfuse-trace-wiring change（D7）；对运行中观测库的不可逆删除，
# 下界/上限/武装变量集中在此，CLI 只负责护栏编排与审计输出
PURGE_DEFAULT_RETENTION_DAYS: int = 30  # 默认保留期（天）
# 保留期下界：低于它一律拒绝，防 `--retention-days 0` 删光运行库
PURGE_MIN_RETENTION_DAYS: int = 1
PURGE_MAX_DELETE_PER_RUN: int = 1000  # 单次删除上限：超过即中止且不删任何数据
# 真删所需武装变量（防 dev 配置误连 prod 库）
PURGE_ALLOW_ENV_VAR: str = "LANGFUSE_PURGE_ALLOW"


# ── 来源权威分级（source-tier-labeling change）──
# KB 内部文档固定档：resolve_source_tier 对 kind=kb 返回，不走域名解析
SOURCE_TIER_KB: int = 0
# web 未命中清单与模式时的默认中性档（非差评）
SOURCE_TIER_DEFAULT: int = 3
# 规则域 → 档位（人工维护先验；增补走候选规则审核流程，见 cookbook）。
# 匹配语义：域边界后缀（== 或 endswith("." + rule)），最长后缀优先
SOURCE_TIER_RULES: dict[str, int] = {
    # T1 官方一手
    "tencent.com": 1,
    "cninfo.com.cn": 1,
    "sse.com.cn": 1,
    "szse.cn": 1,
    # T2 权威财经媒体
    "caixin.com": 2,
    "yicai.com": 2,
    "wallstreetcn.com": 2,
    "sina.com.cn": 2,
    "finance.sina.com.cn": 2,
    "eastmoney.com": 2,
    # T4 UGC
    "zhihu.com": 4,
    "xueqiu.com": 4,
    "weibo.com": 4,
    "guba.eastmoney.com": 4,
}
# 官方/教育机构域名模式：清单未命中时命中模式升 T1
SOURCE_TIER_PATTERN_SUFFIXES: tuple[str, ...] = (".gov.cn", ".edu.cn")
# 档位 → 中文标签（权威映射唯一来源；api_contract.md 同步登记，前端照抄勿另造文案，
# 改动动线：const.py → api_contract.md → chat.html 三步走完才算改完）
SOURCE_TIER_LABELS: dict[int, str] = {
    0: "内部文档",
    1: "官方一手",
    2: "权威媒体",
    3: "一般",
    4: "UGC",
}


def _extract_domain(url: str) -> str:
    """从 url 提取归一化域名。

    去 scheme/路径/query/userinfo/端口与 www. 前缀，转小写。

    Args:
        url: 来源 url

    Returns:
        归一化后的域名字符串
    """
    host = url.strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    # 先剥 query/fragment，无路径 url（如 https://zhihu.com?a=1）也生效
    host = host.split("?", 1)[0].split("#", 1)[0]
    host = host.split("/", 1)[0]  # 剥路径
    host = host.split("@")[-1]  # 剥 userinfo
    host = host.rsplit(":", 1)[0]  # 剥端口
    host = host.removeprefix("www.")  # 剥 www. 前缀
    return host


def resolve_source_tier(url: str, kind: str) -> int:
    """来源权威确定性定档。

    KB kind 固定 T0；web 按规则域匹配（域边界后缀、最长后缀优先），
    官方/教育域名模式升 T1，未命中默认中性档 T3。同一 url 恒定输出，
    不随模型采样变化（模型判断不改写档位，design D1）。

    Args:
        url: 来源 url（KB 来源可为文件名，不参与解析）
        kind: 来源类型（SSEInteractionTexts.CITATION_KIND_KB / CITATION_KIND_WEB）

    Returns:
        档位整数（0=内部文档 1=官方一手 2=权威媒体 3=一般 4=UGC）
    """
    if kind == SSEInteractionTexts.CITATION_KIND_KB:
        return SOURCE_TIER_KB
    domain = _extract_domain(url)
    matches = [
        rule
        for rule, tier in SOURCE_TIER_RULES.items()
        if domain == rule or domain.endswith("." + rule)
    ]
    if matches:
        # 最长后缀优先：guba.eastmoney.com(T4) 覆盖 eastmoney.com(T2)
        best = max(matches, key=len)
        return SOURCE_TIER_RULES[best]
    for suffix in SOURCE_TIER_PATTERN_SUFFIXES:
        if domain.endswith(suffix):
            return 1
    return SOURCE_TIER_DEFAULT


# ── 企业微信智能机器人回调 ──
# 回调路由（router 相对路径；对外完整地址为 /api + 本值）
WECOM_CALLBACK_PATH: str = "/wecom/callback"
# 企微默认 handler 的占位回复文案：尚未接入检索时先行回执，待业务接线后替换
WECOM_REPLY_PLACEHOLDER: str = "已收到，稍后接入检索…"

# ── 企业微信智能机器人通道 ──
# 会话/用户标识派生的固定命名空间：**必须是固定字面 UUID**（不得随机/进程内生成），
# 否则 session_id 每次重启都变、企微会话与历史每重启断链（design D2）。
WECOM_NS: Final[uuid.UUID] = uuid.UUID("a3f1c2d4-5e6b-4c7d-8e9f-0a1b2c3d4e5f")
# 反馈回执事件名（回执帧字段路径为 body.event.feedback_event.{id,type}；
# 实测见 docs/agents/wecom-sdk-facts.md 的 E10）
WECOM_EVENT_FEEDBACK: Final[str] = "feedback_event"

# ── RAGAS 评测打分 ──
# 评测样本按检索来源分类（用途：主分聚合与指标跳过判据）
SAMPLE_CLASS_PURE_KB: str = "pure_kb"  # 检索材料全部来自知识库
SAMPLE_CLASS_WEB: str = "web"  # 检索材料含联网块（降级联网）
SAMPLE_CLASS_EMPTY: str = "empty"  # 未检索到任何材料
# 依赖 contexts 的指标列（无材料样本上跳过；answer_relevancy 不在此列）
CONTEXT_METRIC_COLUMNS: tuple[str, ...] = (
    "faithfulness",
    "context_precision",
    "context_recall",
)
# RAGAS 指标白名单（顺序即 MD 逐题表列序）：MD 表格列、MD 均值行与 stdout 逐指标行
# 均从此清单取；避免结果表混入额外数值列（如 chunk_size）时被静默打印/写入
RAGAS_METRIC_COLUMNS: tuple[str, ...] = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)
# 诊断指标（不进主分/门禁）：逐题写入 detail_json，NaN 单独统计。
DIAGNOSTIC_METRIC_COLUMNS: tuple[str, ...] = (
    "contextual_relevancy",
    "non_advice",
)
# ContextualRelevancy 每条 context 单独调一次裁判；截断到前 8 条以控制成本与
# gather 的失败面。报告必须标注"仅判前 8 条（并集顺序，非最相关 8 条）"。
EVAL_RELEVANCY_MAX_CONTEXTS: int = 8
# 门禁：检索覆盖率下限（低于此值判失败，杜绝"不检索即跳空 context 指标"的规避）
EVAL_RETRIEVAL_COVERAGE_MIN: float = 0.5
# NaN 率门禁：分母 = 纯 KB 样本 × 四指标（格数）；分子 = 纯 KB 格里的全部 NaN。
# 10% 为**经验初值**（依据仅换尺子前 21 题 1 例 ≈4.8%，样本薄），首轮全量后复核定稿。
EVAL_NAN_RATE_MAX: float = 0.10
# Langfuse score 名的口径后缀：新口径（DeepEval 桥 + ragas 0.3.9）与历史
# （modern-0.4.x）并存，SHALL NOT 同名覆盖（design D9）。四项与行为轴共用此后缀。
LANGFUSE_SCORE_SUFFIX: str = "@bridge-0.3.9"
# DeepEval 桥裁判的单次 prompt 字符上限。DashScope 对超长输入直接 400
# （`Range of input length should be [1, 991808]`，2026-10-17 实测）；行为轴的
# StepEfficiency 会带完整轨迹，长上下文题可超限。取报错上限的 ~40% 留余量。
# ⚠️ 与判官窗口（qwen3.8-flash 262144 tokens）的余量约 8%（400k 字符 ≈ 240k tokens）；v6 定稿时按实际轨迹长度重新推导（很可能需下调）。
DEEPEVAL_BRIDGE_PROMPT_MAX_CHARS: int = 400_000
# 评测「通过」阈值（单一来源：落库 passed 与 kb_eval API 下发同用此值）。
# 口径已由旧「四项加权、未掩码」改为「纯 KB 四项均值」，新口径系统性偏低
# 0.03~0.13（实测 n=7，区间 0.68~0.92）——此值**待标定**，暂沿用 0.70。
EVAL_PASS_THRESHOLD: float = 0.70


# ── RAGAS 测试集人设 ──
# 固定测试集生成用的人设，交给 TestsetGenerator 以跳过其内置的随机 persona 生成。
# 为什么固定：内置生成步 temperature=1.0，模型常产出重名（如 "Chen Wei" 与
# "Wei Chen"）或把角色描述并进 name（"Chen Wei (Equity research analyst ...)"）
# 的名字；而 ragas 的 PersonaList 按 name 精确匹配，任一畸变都会让出题阶段
# 抛 KeyError。名字须唯一且不含括号（角色描述由 role_description 单独承载）。
RAGAS_TEST_PERSONAS: tuple[tuple[str, str], ...] = (
    ("财报分析员", "分析上市公司定期财务报告，评估经营业绩、会计质量与投资风险。"),
    ("投资经理", "结合财务报表与行业信息进行估值判断与投资决策。"),
    ("财经记者", "采写上市公司年报与经营动态的财经报道。"),
)

# 出题自足性约束（design D8）：RAGAS 出题 prompt 对问题自足性零要求，官方唯一
# 软通道是 TestsetGenerator 的 llm_context（注入 prompt 的 "Additional Context"）。
# 中英各一句：英文对齐 RAGAS prompt 语境，中文对齐中文语料。
RAGAS_SELF_CONTAINED_HINT: str = (
    "Each question MUST be self-contained: name the company and the reporting "
    "period explicitly. Do NOT use context-dependent references such as "
    '"this reporting period" or "the company".\n'
    "每个问题必须自足：明确指出公司名称与报告期；禁止使用『本报告期』『该公司』"
    "等依赖原文的指代。"
)


# ── 阿里云告警接入（change: alibaba-alert-notify） ──

# 告警接收端点路径模板（api 层只引用本常量，不 import config 其它模块）。
# `{token}` 是 FastAPI 的路径参数：鉴权 token 走**路径**而非请求头 —— 上游是
# Grafana 的 contact point，它不支持自定义请求头。日志侧对该段做了脱敏。
ALERT_WEBHOOK_PATH: Final[str] = "/alerts/webhook/{token}"

# 报警会话的固定归属：全零 UUID，与任何登录用户 uid 都不同 ⇒ 会话列表天然查不到
ALERT_USER_ID: Final[str] = "00000000-0000-0000-0000-000000000000"

# 告警专用渠道标识（另需在 docs/agents/logging-rules.md 的渠道表登记）
# 见 Channel.ALERT


class AlertTexts:
    """告警链路的用户可见文案与输入模板（集中管理，不散在业务代码）。"""

    # 受理占位（第一段推送）；{alertname} 由载荷渲染
    PLACEHOLDER_TEMPLATE: str = "收到告警「{alertname}」，正在分析…"
    # 兜底转发前缀（与正常结论区分）
    FALLBACK_PREFIX: str = "[原始告警]"
    # 恢复通知前缀（恢复不走大模型，直接推模板消息）
    RECOVERY_PREFIX: str = "[已恢复]"
    # 喂给 Agent 的输入模板；{alertname}/{level}/{details} 由载荷渲染
    QUERY_TEMPLATE: str = (
        "收到一条云监控告警，请分析其含义、可能原因与处置建议。\n"
        "告警名称：{alertname}\n"
        "告警等级：{level}\n"
        "告警内容：\n{details}"
    )
