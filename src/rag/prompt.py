"""Prompt 构建 — 将上下文、历史和问题组装为 LLM 消息列表。

组装职责边界（spec <prompt-composition>）：
- 段顺序、逐条条件注入、base 三选一都住在本模块；YAML 模板只装正文。
- 判据留代码、不由模板声明（"能改文案、不能改挂载"），逐条对照表见
  docs/agents/prompt-ownership.md §3。
"""

from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.config import settings
from src.config.prompts import loader
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.prompt_manager import PromptManager, _with_current_date
from src.infra.llm.provider.factory import _registry
from src.infra.llm.provider.spec import resolve_role
from src.rag.context import RAGContext

# 段组装顺序（spec「五段按固定顺序组装」）
SECTION_ORDER: tuple[str, ...] = (
    "base",
    "runtime_contract",
    "sources",
    "tools",
    "output",
)

# 通用 base 的领域保留值（spec「general 是保留值，且它必须有对应模板」）
GENERAL_DOMAIN: str = "general"


@dataclass(frozen=True)
class AssemblyContext:
    """段组装的判据输入 —— 本轮实际可用能力。

    Attributes:
        persona: 会话智能体预设正文；来源：RequestContext.persona；空串 = 未选预设
        kb_bound: 是否绑定知识库；来源：state.kb_id 非空
        has_skills: 是否有可用技能；来源：RequestContext.has_skills
        tool_names: 本轮实际注册的工具名；来源：build_graph 的 rag_tools 列表
        kb_domain: 知识库领域；来源：RequestContext.kb_domain；缺省 general
    """

    persona: str
    kb_bound: bool
    has_skills: bool
    tool_names: frozenset[str]
    kb_domain: str


RuleFn = Callable[[AssemblyContext], bool]


def _always(_ctx: AssemblyContext) -> bool:
    """无条件规则：与本轮能力无关。"""
    return True


def _kb_retrieval_ladder(ctx: AssemblyContext) -> bool:
    """检索阶梯：`retrieve_kb` 已注册 AND 适用域成立（已绑定知识库）。"""
    if not ctx.kb_bound:
        return False
    return "retrieve_kb" in ctx.tool_names


def _kb_web_rules(ctx: AssemblyContext) -> bool:
    """联网系列：`search_web` 已注册 AND 适用域成立（已绑定知识库）。"""
    if not ctx.kb_bound:
        return False
    return "search_web" in ctx.tool_names


def _ask_user_available(ctx: AssemblyContext) -> bool:
    """澄清时机：只看 `ask_user` 是否注册（无适用域）。"""
    return "ask_user" in ctx.tool_names


def _delegate_available(ctx: AssemblyContext) -> bool:
    """委派系列：只看 `delegate_task` 是否注册（无适用域）。"""
    return "delegate_task" in ctx.tool_names


# 段 → ((模板 id, 判据), ...)；判据留代码、不由 YAML 声明（spec「判据的位置」）。
# 增删条目必须同步 docs/agents/prompt-ownership.md §3 的逐条判据表（该表另含判据
# 在 `_build_unbound_message`、不在本表的 `sources-kb-unbound(-web)` 两条）。
_SECTION_RULES: dict[str, tuple[tuple[str, RuleFn], ...]] = {
    "runtime_contract": (("runtime-contract", _always),),
    "sources": (
        ("sources-general", _always),
        ("sources-kb-ladder", _kb_retrieval_ladder),
        ("sources-kb-web-rules", _kb_web_rules),
    ),
    "tools": (
        ("tools-execution", _always),
        ("tools-ask-user", _ask_user_available),
        ("tools-delegate", _delegate_available),
    ),
    "output": (
        ("output-presentation", _always),
        ("output-citation", _always),
        ("output-delegate-citation", _delegate_available),
    ),
}


def format_context(contexts: list[RAGContext]) -> str:
    """将检索上下文格式化为参考文档字符串。"""
    blocks = []
    for i, ctx in enumerate(contexts):
        blocks.append(f"[{i + 1}] {ctx.to_prompt_text()}")
    return "\n\n".join(blocks)


def _resolve_base(ctx: AssemblyContext) -> tuple[str, str]:
    """按三选一（替换）解析 base 段正文。

    Args:
        ctx: 组装判据输入

    Returns:
        (base 正文, persona_source)；persona_source 取 preset / domain / general
    """
    if ctx.persona:
        return ctx.persona.strip("\n"), "preset"
    if loader.has_domain(ctx.kb_domain):
        return loader.get_domain_base(ctx.kb_domain).strip("\n"), "domain"
    return loader.get_domain_base(GENERAL_DOMAIN).strip("\n"), "general"


def _render_section(section: str, ctx: AssemblyContext) -> str:
    """按判据表逐条取正文，拼成一段。

    条目间以空行分隔（保留模板作者刻意的分块意图）。

    Args:
        section: 段名（SECTION_ORDER 中除 base 外的段）
        ctx: 组装判据输入

    Returns:
        该段正文；无条目命中时为空串（调用方丢弃，不输出空标题）
    """
    parts: list[str] = []
    for template_id, predicate in _SECTION_RULES[section]:
        if not predicate(ctx):
            continue
        parts.append(loader.get_content(template_id).strip("\n"))
    return "\n\n".join(parts)


def _render_all_sections(ctx: AssemblyContext) -> tuple[dict[str, str], str]:
    """按 SECTION_ORDER 逐段渲染。

    base 只解析一次：先取三选一结果，再按段序填入，避免重复解析。

    Args:
        ctx: 组装判据输入

    Returns:
        (段名 → 段正文, persona_source)；段键序 = 组装顺序，
        persona_source 取 preset / domain / general
    """
    base_text, persona_source = _resolve_base(ctx)
    sections: dict[str, str] = {}
    for section in SECTION_ORDER:
        if section == "base":
            sections[section] = base_text
            continue
        sections[section] = _render_section(section, ctx)
    return sections, persona_source


def _resolve_tokens_per_cjk_char() -> float:
    """经层取主模型的中文 token 换算系数。

    来源：registry 为主模型声明的 tokens_per_cjk_char（design D3）。该值属
    「跨模型借用」经验系数、非本模型分词器实测，仅作量级估算。

    Returns:
        registry 声明值；主模型未声明时回退 settings.PROMPT_TOKENS_PER_CJK_CHAR
    """
    resolved = resolve_role("main", _registry())
    facts = resolved.provider.catalog.get(resolved.model)
    if facts is None or not facts.tokens_per_cjk_char:
        return settings.PROMPT_TOKENS_PER_CJK_CHAR
    return facts.tokens_per_cjk_char


def _warn_if_section_share_high(section_chars: dict[str, int]) -> None:
    """system 段估算占 context window 比例超阈值时记 warning（**不阻断**）。

    换算系数经层取（registry 声明，回退 settings 缺省）、阈值为推断值，
    仅作量级参考，不构成契约。

    Args:
        section_chars: 各段字符数（正整数）
    """
    # window 为环境变量可配；配成非正值时直接放弃估算，绝不让观测路径中断组装
    if settings.MODEL_CONTEXT_WINDOW_TOKENS <= 0:
        return
    total = sum(section_chars.values())
    est_tokens = int(total * _resolve_tokens_per_cjk_char())
    share = est_tokens / settings.MODEL_CONTEXT_WINDOW_TOKENS
    if share > settings.PROMPT_CONTEXT_SHARE_WARN:
        core_logging.log_event(
            Event.PROMPT_SECTION_SHARE_HIGH,
            est_tokens=est_tokens,
            share=round(share, 4),
            threshold=settings.PROMPT_CONTEXT_SHARE_WARN,
        )


def _build_unbound_message(ctx: AssemblyContext) -> str:
    """组装态 A 的第二条 system 消息（核心句 + 条件联网句）。

    Args:
        ctx: 组装判据输入（读 tool_names 决定是否追加联网句）

    Returns:
        未绑定提示正文
    """
    text = loader.get_content("sources-kb-unbound").strip("\n")
    if "search_web" in ctx.tool_names:
        text += "\n" + loader.get_content("sources-kb-unbound-web").strip("\n")
    return text


def build_system_prompt(
    persona: str,
    kb_bound: bool,
    has_skills: bool,
    tool_names: frozenset[str] | None = None,
    kb_domain: str = GENERAL_DOMAIN,
) -> list[SystemMessage]:
    """组装 system 消息（人设层 base + 环境约束层四段）。

    组装顺序固定为 base → runtime_contract → sources → tools → output；
    空段丢弃。未绑定知识库时追加第二条 system 消息（未绑定提示）。

    Args:
        persona: 会话智能体预设正文；空串 = 未选预设（改用知识库领域 base）
        kb_bound: 是否绑定知识库
        has_skills: 是否有可用技能（保留形参：决策判据已改由工具集承担，
            见 prompt-ownership.md §3；本值仍进组装日志）
        tool_names: 本轮实际注册的工具名；None 视为空集（无工具）
        kb_domain: 知识库领域；缺省 general

    Returns:
        system 消息列表（未绑定 KB 时为两条）
    """
    if tool_names is None:
        tool_names = frozenset()
    ctx = AssemblyContext(
        persona=persona,
        kb_bound=kb_bound,
        has_skills=has_skills,
        tool_names=tool_names,
        kb_domain=kb_domain,
    )
    sections, persona_source = _render_all_sections(ctx)
    body = "\n\n".join(text for text in sections.values() if text)
    messages: list[SystemMessage] = [SystemMessage(content=_with_current_date(body))]
    if not kb_bound:
        messages.append(SystemMessage(content=_build_unbound_message(ctx)))
    # system prompt 组成事实：人设来源 + 条件注入命中 + system 段数 + 各段字符数
    # （容器值，由日志层编码为紧凑 JSON；键序 = 组装顺序，空段不出现）
    section_chars = {name: len(text) for name, text in sections.items() if text}
    core_logging.log_event(
        Event.PROMPT_ASSEMBLED,
        persona_source=persona_source,
        kb_bound=kb_bound,
        has_skills=has_skills,
        kb_domain=ctx.kb_domain,
        tool_count=len(tool_names),
        system_msgs=len(messages),
        section_chars=section_chars,
    )
    _warn_if_section_share_high(section_chars)
    return messages


def build_prompt(
    query: str,
    context: str,
    history: list[ChatMessage],
    prompt_manager: PromptManager,
    kb_bound: bool = True,
    persona: str = "",
    has_skills: bool = False,
    tool_names: frozenset[str] | None = None,
    kb_domain: str = GENERAL_DOMAIN,
) -> list:
    """构建含系统指令和对话历史的完整 prompt。

    追加形参见 build_system_prompt；prompt_manager 只用于渲染用户消息模板。
    """
    messages: list[BaseMessage] = []
    messages.extend(
        build_system_prompt(
            persona,
            kb_bound,
            has_skills,
            tool_names=tool_names,
            kb_domain=kb_domain,
        )
    )
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
    user_content = prompt_manager.get_user_template(context=context, query=query)
    messages.append(HumanMessage(content=user_content))
    return messages


def build_simple_prompt(
    query: str,
    history: list[ChatMessage],
    prompt_manager: PromptManager,
    tool_names: frozenset[str] | None = None,
    kb_domain: str = GENERAL_DOMAIN,
) -> list:
    """构建无检索上下文的简洁 prompt。"""
    messages: list[BaseMessage] = []
    messages.extend(
        build_system_prompt("", True, False, tool_names=tool_names, kb_domain=kb_domain)
    )
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
    messages.append(HumanMessage(content=query))
    return messages
